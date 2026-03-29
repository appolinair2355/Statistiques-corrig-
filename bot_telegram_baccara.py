#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram Baccara - Système de redirection de données
Récupère les données de l'API 1xBet, les transforme et les redistribue
vers plus de 20 canaux Telegram simultanément.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)
from web_server import start_web_server, set_bot

from utils_new import get_latest_results, update_history

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class ConfigManager:
    """Gestionnaire de configuration basé sur config.json."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Fichier de configuration {self.config_path} non trouvé!")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Erreur de parsing JSON: {e}")
            raise

    def get(self, section: str, key: str = None, default=None):
        if key is None:
            return self.config.get(section, default)
        return self.config.get(section, {}).get(key, default)

    def update(self, section: str, key: str, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self._save_config()

    def _save_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)


class BaccaraBot:
    """Bot de redirection Baccara — collecte, transforme et diffuse vers 20+ canaux."""

    def __init__(self, config_path: str = "config.json"):
        self.config = ConfigManager(config_path)

        # Configuration Telegram
        self.token = self.config.get('telegram', 'bot_token')
        self.admin_id = self.config.get('telegram', 'admin_id')
        self.main_channel = self.config.get('telegram', 'main_channel')
        self.redirect_channels: List[int] = self.config.get('telegram', 'redirect_channels', [])
        self.notify_on_error = self.config.get('telegram', 'notification_on_error', True)

        # Configuration App
        self.language = self.config.get('app', 'language', 'FR')
        self.check_interval = self.config.get('app', 'check_interval_seconds', 15)

        # Configuration API
        self.api_url = self.config.get('api', 'url')
        self.api_params = self.config.get('api', 'params', {})
        self.api_timeout = self.config.get('api', 'timeout', 30)

        # État interne
        self.history: Dict = {}
        self.is_running = True
        self.last_check: Optional[datetime] = None
        self.last_api_game: Optional[Dict] = None
        self.last_results: List[Dict] = []
        self.seen_game_nums: set = set()   # jeux entièrement traités (terminés, message envoyé/édité)
        self.pending_games: Dict = {}      # jeux en cours déjà envoyés → {gnum: [(channel_id, msg_id)...]}

        # Publicité automatique
        self.pub_message = ""
        self.pub_enabled = False
        self.pub_interval_minutes = 30
        self.pub_job = None

        # Pub basée sur le nombre de jeux redirigés
        self.pub_every_n_games = 0      # 0 = désactivé
        self.pub_games_counter = 0      # compteur de jeux redirigés depuis la dernière pub

        # Emoji personnalisable pour les jeux en cours
        self.pending_emoji = self.config.get('app', 'pending_emoji', '⏰')

        # Emoji personnalisable pour le séparateur d'égalité (Tie)
        self.tie_emoji = self.config.get('app', 'tie_emoji', '🔰')

        logger.info(f"Bot initialisé — Canal principal: {self.main_channel}, Canaux: {len(self.redirect_channels)}, Admin: {self.admin_id}")

    # ─────────────────────────────────────────────
    # UTILITAIRES
    # ─────────────────────────────────────────────

    def _is_admin(self, user_id: int) -> bool:
        return user_id == self.admin_id

    def _admin_only_text(self) -> str:
        return "⚠️ Seul l'administrateur peut utiliser cette commande."

    async def _notify_admin(self, context: ContextTypes.DEFAULT_TYPE, message: str):
        try:
            await context.bot.send_message(
                chat_id=self.admin_id,
                text=f"⚠️ *Alerte Admin*\n\n{message}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Échec notification admin: {e}")

    def _all_channels(self) -> List[int]:
        """Retourne tous les canaux uniques (principal + redirections)."""
        seen = set()
        result = []
        for ch in [self.main_channel] + self.redirect_channels:
            if ch and ch not in seen:
                seen.add(ch)
                result.append(ch)
        return result

    # ─────────────────────────────────────────────
    # FORMATAGE DES CARTES
    # ─────────────────────────────────────────────

    def _fmt_rank(self, r) -> str:
        rank_labels = {0: '10', 1: 'A', 10: '10', 11: 'J', 12: 'Q', 13: 'K', 14: 'A'}
        if isinstance(r, int):
            return rank_labels.get(r, str(r))
        return str(r)

    def _fmt_cards_inline(self, cards: List[Dict]) -> str:
        """Formate les cartes collées : ex. 8♦️2♣️J♦️"""
        parts = []
        for c in cards:
            r = self._fmt_rank(c.get('R', '?'))
            s = c.get('S', '?')
            parts.append(f"{r}{s}")
        return ''.join(parts)

    def _format_cards(self, cards: List[Dict]) -> str:
        """Formate les cartes espacées : ex. ♠️2  ♦️7  ♥️K"""
        if not cards:
            return '—'
        rank_labels = {1: 'A', 11: 'J', 12: 'Q', 13: 'K', 14: 'A'}
        parts = []
        for c in cards:
            suit = c.get('S', '?')
            rank = c.get('R', '?')
            if isinstance(rank, int):
                label = rank_labels.get(rank, str(rank % 10) if rank >= 10 else str(rank))
            else:
                label = str(rank)
            parts.append(f"{suit}{label}")
        return '  '.join(parts)

    def _calc_baccara_score(self, cards: List[Dict]) -> int:
        """Calcule le score baccara d'une main (mod 10).
        Valeurs : As=1, 2-9=valeur nominale, 10/J/Q/K=0.
        Rangs API connus : 0=10, 1=As, 2-9=valeur, 10=10, 11=J, 12=Q, 13=K, 14=As.
        """
        int_rank_values = {
            0: 0,   # 10
            1: 1,   # As
            2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9,
            10: 0,  # 10
            11: 0,  # J
            12: 0,  # Q
            13: 0,  # K
            14: 1,  # As (représenté comme 14 dans l'API)
        }
        str_rank_values = {
            '10': 0, 'J': 0, 'Q': 0, 'K': 0, 'A': 1,
            '2': 2, '3': 3, '4': 4, '5': 5,
            '6': 6, '7': 7, '8': 8, '9': 9,
        }
        total = 0
        for card in cards:
            r = card.get('R', None)
            if isinstance(r, int):
                total += int_rank_values.get(r, 0)
            else:
                total += str_rank_values.get(str(r), 0)
        return total % 10

    def _format_redirect_game_line(self, game_number: int, game_data: Dict) -> str:
        """
        Formate un jeu terminé dans le format de redirection.
        Ex: #N634. ✅2(K♣️J♥️2♠️) - 1(Q♦️Q♥️A♠️) #T3
            #N633. 6(K♠️2♦️4♦️) 🔰 6(10♥️K♣️6♣️) #T12 🔵#R 🟣#X
        Tags :
          🔵#R = partie 2/2 (joueur et banquier ont exactement 2 cartes)
          🟣#X = égalité (Tie)
        """
        if not game_data.get('is_finished', False):
            return ''

        p_cards = game_data.get('player_cards', [])
        b_cards = game_data.get('banker_cards', [])
        p_cards_str = self._fmt_cards_inline(p_cards)
        b_cards_str = self._fmt_cards_inline(b_cards)

        score = game_data.get('score', {}) or {}
        p_score = score.get('S1', '')
        b_score = score.get('S2', '')

        if p_score == '' or p_score is None:
            p_score = self._calc_baccara_score(p_cards)
        if b_score == '' or b_score is None:
            b_score = self._calc_baccara_score(b_cards)

        winner = game_data.get('winner')

        # Détection de l'égalité par comparaison de scores si winner non fourni
        if winner not in ('Player', 'Banker', 'Tie'):
            try:
                if int(p_score) == int(b_score):
                    winner = 'Tie'
            except (TypeError, ValueError):
                pass

        if winner == 'Tie':
            sep = self.tie_emoji
            p_prefix = ''
            b_prefix = ''
        else:
            sep = '-'
            p_prefix = '✅' if winner == 'Player' else ''
            b_prefix = '✅' if winner == 'Banker' else ''

        try:
            total = int(p_score) + int(b_score)
            total_str = f"#T{total}"
        except (TypeError, ValueError):
            total_str = "#T?"

        tags = []
        if len(p_cards) == 2 and len(b_cards) == 2:
            tags.append('🔵#R')
        if winner == 'Tie':
            tags.append('🟣#X')

        line = (
            f"#N{game_number}. "
            f"{p_prefix}{p_score}({p_cards_str}) "
            f"{sep} "
            f"{b_prefix}{b_score}({b_cards_str}) "
            f"{total_str}"
        )
        if tags:
            line += ' ' + ' '.join(tags)
        return line

    def _format_game_line(self, game_number: int, game_data: Dict) -> str:
        """Formate un jeu en ligne compacte (pour /parties)."""
        is_finished = game_data.get('is_finished', False)
        p_cards = game_data.get('player_cards', [])
        b_cards = game_data.get('banker_cards', [])
        p_cards_str = self._fmt_cards_inline(p_cards)
        b_cards_str = self._fmt_cards_inline(b_cards)
        score = game_data.get('score', {}) or {}
        p_score = score.get('S1', '')
        b_score = score.get('S2', '')

        if not is_finished:
            p_count = len(p_cards)
            b_count = len(b_cards)
            if p_count > b_count:
                p_marker, b_marker = '', '▶️'
            else:
                p_marker, b_marker = '▶️', ''
            p_part = f"{p_marker}{p_score}({p_cards_str})" if p_cards_str else f"{p_marker}(—)"
            b_part = f"{b_marker}{b_score}({b_cards_str})" if b_cards_str else f"{b_marker}(—)"
            return f"{self.pending_emoji}#N{game_number}. {p_part} - {b_part}"

        winner = game_data.get('winner')
        if winner == 'Tie':
            sep = '🔰'
            p_prefix = b_prefix = ''
        else:
            sep = '-'
            p_prefix = '✅' if winner == 'Player' else ''
            b_prefix = '✅' if winner == 'Banker' else ''

        try:
            total = int(p_score) + int(b_score)
            total_str = f"#T{total}"
        except (TypeError, ValueError):
            total_str = "#T?"

        return (
            f"#N{game_number}. "
            f"{p_prefix}{p_score}({p_cards_str}) "
            f"{sep} "
            f"{b_prefix}{b_score}({b_cards_str}) "
            f"{total_str}"
        )

    def _format_single_game(self, game_number: int, game_data: Dict, title: str = "") -> str:
        """Formate les infos complètes d'un jeu."""
        player_cards = game_data.get('player_cards', [])
        banker_cards = game_data.get('banker_cards', [])
        winner = game_data.get('winner')
        score = game_data.get('score', {})
        is_finished = game_data.get('is_finished', False)

        p_score = score.get('S1', '?') if score else '?'
        b_score = score.get('S2', '?') if score else '?'

        if winner == 'Player':
            winner_str = "👤 Joueur gagne"
        elif winner == 'Banker':
            winner_str = "🏦 Banquier gagne"
        elif winner == 'Tie':
            winner_str = "🤝 Égalité"
        else:
            winner_str = "⏳ En cours"

        etat = "✅ Terminé" if is_finished else "⏳ En cours"
        header = f"*{title}* " if title else ""
        return (
            f"{header}🎴 *Jeu #{game_number}*\n"
            f"├ 👤 Joueur  : `{self._format_cards(player_cards)}`\n"
            f"├ 🏦 Banquier: `{self._format_cards(banker_cards)}`\n"
            f"├ 🏆 Gagnant : {winner_str}\n"
            f"├ 📊 Score   : `{p_score} - {b_score}`\n"
            f"└ {etat}"
        )

    def _format_game_full(self, game_number: int, game_data: Dict) -> str:
        """
        Formate un jeu (terminé ou en cours) dans le format de redirection.

        Terminé  : #N650. 1(3♥️8♠️Q♠️) - ✅4(Q♦️4♦️) #T5
        Tie      : #N648. 3(Q♦️2♠️) 🔰 3(3♥️K♥️) #T6 #X
        En cours : ⏰#N650. ▶️1(3♥️8♠️) - 4(Q♦️4♦️)   (joueur n'a pas fini)
                   ⏰#N648. 3(Q♦️2♠️A♦️) - ▶️3(3♥️K♥️) (banquier n'a pas fini)

        Tags (jeux terminés seulement) :
          #R = distribution directe (2 cartes chacun)
          #X = égalité
        """
        is_finished = game_data.get('is_finished', False)
        p_cards = game_data.get('player_cards', [])
        b_cards = game_data.get('banker_cards', [])
        p_cards_str = self._fmt_cards_inline(p_cards)
        b_cards_str = self._fmt_cards_inline(b_cards)

        score = game_data.get('score', {}) or {}
        p_score = score.get('S1', None)
        b_score = score.get('S2', None)

        if p_score is None or p_score == '':
            p_score = self._calc_baccara_score(p_cards) if p_cards else 0
        if b_score is None or b_score == '':
            b_score = self._calc_baccara_score(b_cards) if b_cards else 0

        winner = game_data.get('winner')

        if is_finished:
            # Détection de l'égalité par comparaison de scores si winner non fourni
            if winner not in ('Player', 'Banker', 'Tie'):
                try:
                    if int(p_score) == int(b_score):
                        winner = 'Tie'
                except (TypeError, ValueError):
                    pass

            if winner == 'Tie':
                sep = self.tie_emoji
                p_prefix = ''
                b_prefix = ''
            else:
                sep = '-'
                p_prefix = '✅' if winner == 'Player' else ''
                b_prefix = '✅' if winner == 'Banker' else ''

            try:
                total = int(p_score) + int(b_score)
                total_str = f"#T{total}"
            except (TypeError, ValueError):
                total_str = "#T?"

            p_part = f"{p_prefix}{p_score}({p_cards_str})" if p_cards_str else f"{p_prefix}{p_score}(—)"
            b_part = f"{b_prefix}{b_score}({b_cards_str})" if b_cards_str else f"{b_prefix}{b_score}(—)"

            line = f"#N{game_number}. {p_part} {sep} {b_part} {total_str}"

            tags = []
            if len(p_cards) == 2 and len(b_cards) == 2:
                tags.append('#R')
            if winner == 'Tie':
                tags.append('#X')
            if tags:
                line += ' ' + ' '.join(tags)
            return line

        else:
            # En cours : ▶️ sur le côté qui n'a pas encore terminé
            p_count = len(p_cards)
            b_count = len(b_cards)
            if p_count > b_count:
                p_marker, b_marker = '', '▶️'
            else:
                p_marker, b_marker = '▶️', ''

            p_part = f"{p_marker}{p_score}({p_cards_str})" if p_cards_str else f"{p_marker}(—)"
            b_part = f"{b_marker}{b_score}({b_cards_str})" if b_cards_str else f"{b_marker}(—)"

            return f"{self.pending_emoji}#N{game_number}. {p_part} - {b_part}"

    async def _send_single_game_to_channels(self, context, text: str):
        """Envoie UN jeu terminé vers tous les canaux (pas de tracking)."""
        for channel_id in self.redirect_channels:
            if not channel_id:
                continue
            try:
                await context.bot.send_message(chat_id=channel_id, text=text)
            except Exception as e:
                logger.error(f"[Redirect] Échec canal {channel_id}: {e}")

    async def _send_and_track_game(self, context, text: str, gnum: int):
        """Envoie un jeu EN COURS et stocke les IDs + le texte pour suivi."""
        entries = []
        for channel_id in self.redirect_channels:
            if not channel_id:
                continue
            try:
                sent = await context.bot.send_message(chat_id=channel_id, text=text)
                entries.append((channel_id, sent.message_id))
            except Exception as e:
                logger.error(f"[Redirect] Échec canal {channel_id}: {e}")
        if entries:
            self.pending_games[gnum] = {"entries": entries, "last_text": text}
            logger.info(f"[Pending] #N{gnum} → {len(entries)} message(s) suivis")

    async def _edit_game_messages(self, context, gnum: int, new_text: str):
        """Édite les messages d'un jeu pending si le texte a changé."""
        pending = self.pending_games.get(gnum)
        if not pending:
            return
        if pending["last_text"] == new_text:
            return  # Aucun changement, pas d'édition inutile
        for (channel_id, message_id) in pending["entries"]:
            try:
                await context.bot.edit_message_text(
                    chat_id=channel_id,
                    message_id=message_id,
                    text=new_text
                )
            except Exception as e:
                logger.error(f"[Edit] Échec canal {channel_id} msg {message_id}: {e}")
        pending["last_text"] = new_text
        logger.info(f"[Edit] #N{gnum} mis à jour → {len(pending['entries'])} message(s)")

    # ─────────────────────────────────────────────
    # FORMATAGE & ENVOI DE LA PUBLICITÉ
    # ─────────────────────────────────────────────

    def _format_pub_message(self) -> str:
        """
        Transforme le texte brut de l'admin en un message publicitaire
        visuellement attractif.
        """
        raw = self.pub_message.strip()
        border = "━" * 28
        return (
            f"╔{border}╗\n"
            f"║  📣  *ANNONCE OFFICIELLE*  📣  ║\n"
            f"╚{border}╝\n\n"
            f"{raw}\n\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"🎰 *Bot Baccara* | Légiste Carte Enseigne 🤴💰"
        )

    async def _dispatch_pub(self, context: ContextTypes.DEFAULT_TYPE):
        """Envoie la publication vers tous les canaux (principal + redirections)."""
        if not self.pub_message:
            return
        text = self._format_pub_message()
        for ch in self._all_channels():
            try:
                await context.bot.send_message(chat_id=ch, text=text, parse_mode='Markdown')
                logger.info(f"[Pub] Envoyé → canal {ch}")
            except Exception as e:
                logger.error(f"[Pub] Échec canal {ch}: {e}")

    async def _check_pub_by_game_count(self, context: ContextTypes.DEFAULT_TYPE, nb_new_games: int):
        """
        Incrémente le compteur de jeux redirigés et déclenche la pub
        si le seuil défini est atteint.
        """
        if self.pub_every_n_games <= 0 or not self.pub_message:
            return

        self.pub_games_counter += nb_new_games
        logger.info(f"[Pub/msg] Compteur: {self.pub_games_counter}/{self.pub_every_n_games}")

        if self.pub_games_counter >= self.pub_every_n_games:
            self.pub_games_counter = 0
            logger.info(f"[Pub/msg] Seuil atteint → envoi de la publication")
            await self._dispatch_pub(context)

    # ─────────────────────────────────────────────
    # REDIRECTION VERS LES CANAUX (supprimé — remplacé par _send_single_game_to_channels)
    # ─────────────────────────────────────────────

    # ─────────────────────────────────────────────
    # BOUCLE PRINCIPALE
    # ─────────────────────────────────────────────

    async def collect_and_redirect(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Boucle principale :
        1. Édite les messages des jeux EN COURS qui viennent de se TERMINER
        2. Envoie les nouveaux jeux avec cartes (⏰ si en cours, résultat final si terminé)
        3. Ignore les jeux "Prématch" sans aucune carte (on réessaie au prochain poll)
        """
        try:
            self.last_check = datetime.now()

            results = get_latest_results()
            if not results:
                logger.warning("Aucune donnée reçue de l'API")
                return

            self.last_api_game = max(results, key=lambda r: r['game_number'])
            self.last_results = results
            self.history = update_history(results, self.history)

            current_map = {r['game_number']: r for r in results}

            # ── Premier démarrage : mémoriser uniquement les jeux TERMINÉS sans les renvoyer ──
            # Les jeux en cours ou prématch sont laissés pour être traités normalement
            if not self.seen_game_nums and not self.pending_games:
                finished_at_start = {r['game_number'] for r in results if r.get('is_finished', False)}
                self.seen_game_nums = finished_at_start
                logger.info(
                    f"[Init] {len(finished_at_start)} jeux terminés mémorisés "
                    f"({len(results) - len(finished_at_start)} en cours seront traités au prochain poll)."
                )
                return

            nb_sent = 0

            # ── ÉTAPE 1 : Mettre à jour les jeux pending à chaque poll ──
            for gnum in list(self.pending_games.keys()):
                game_data = current_map.get(gnum)

                if game_data is None:
                    # Jeu disparu de l'API → abandon
                    logger.warning(f"[Pending] #N{gnum} disparu de l'API, abandon.")
                    del self.pending_games[gnum]
                    self.seen_game_nums.add(gnum)
                    continue

                new_text = self._format_game_full(gnum, game_data)
                if new_text:
                    # Édite si le texte a changé (nouvelles cartes ou état terminé)
                    await self._edit_game_messages(context, gnum, new_text)

                if game_data.get('is_finished', False):
                    # Jeu terminé → sortir du pending
                    del self.pending_games[gnum]
                    self.seen_game_nums.add(gnum)
                    nb_sent += 1

            # ── ÉTAPE 2 : Nouveaux jeux (pas dans seen, pas dans pending) ──
            all_handled = self.seen_game_nums | set(self.pending_games.keys())
            new_nums = sorted(n for n in current_map if n not in all_handled)

            for gnum in new_nums:
                game_data = current_map[gnum]
                p_cards = game_data.get('player_cards', [])
                b_cards = game_data.get('banker_cards', [])
                is_finished = game_data.get('is_finished', False)
                has_cards = len(p_cards) > 0 or len(b_cards) > 0

                if not has_cards and not is_finished:
                    # Prématch sans cartes → ignorer ce poll, réessayer au suivant
                    continue

                msg = self._format_game_full(gnum, game_data)
                if not msg:
                    continue

                if is_finished:
                    # Jeu déjà terminé → envoyer directement, pas de tracking
                    await self._send_single_game_to_channels(context, msg)
                    self.seen_game_nums.add(gnum)
                    nb_sent += 1
                else:
                    # Jeu en cours avec cartes → envoyer ⏰ et suivre l'ID pour édition future
                    await self._send_and_track_game(context, msg, gnum)

            if nb_sent > 0:
                await self._check_pub_by_game_count(context, nb_sent)

        except Exception as e:
            logger.error(f"Erreur dans collect_and_redirect: {e}")
            if self.notify_on_error:
                await self._notify_admin(context, f"Erreur collecte: {e}")

    # ─────────────────────────────────────────────
    # COMMANDES TELEGRAM
    # ─────────────────────────────────────────────

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /start — Menu principal."""
        user = update.effective_user
        is_admin = self._is_admin(user.id)

        keyboard = [
            [InlineKeyboardButton("📊 Status", callback_data='status'),
             InlineKeyboardButton("⚙️ Configuration", callback_data='config')],
            [InlineKeyboardButton("📡 Canaux", callback_data='channels')]
        ]

        commandes_base = (
            "📋 *COMMANDES DISPONIBLES*\n\n"
            "👤 *Général*\n"
            "`/start` — Menu principal\n"
            "`/status` — État du bot\n"
            "`/jeu` — Dernier jeu terminé (détaillé)\n"
            "`/derniers` — 5 derniers jeux API (format redirection)\n"
            "`/parties` — Jeux récents en cours\n"
        )
        commandes_admin = (
            "\n🔐 *Admin seulement*\n"
            "`/config` — Configuration\n"
            "`/redirect [add|remove|list] [ID]` — Gérer les canaux\n"
            "`/setemoji <emoji>` — Changer l'emoji des jeux en cours\n"
            "`/settie <emoji>` — Changer l'emoji séparateur du match nul\n"
            "`/setpub <texte>` — Définir le message de pub (aperçu auto)\n"
            "`/startpub min <N>` — Pub toutes les N minutes\n"
            "`/startpub msg <N>` — Pub toutes les N parties redirigées\n"
            "`/stoppub [min|msg]` — Arrêter la pub (un ou tous les modes)\n"
        ) if is_admin else ""

        await update.message.reply_text(
            f"🎰 *Bot Baccara — Redirecteur de données*\n\n"
            f"Bienvenue {user.first_name}!\n"
            f"Ce bot collecte les données de l'API 1xBet et les redistribue\n"
            f"en temps réel vers tous vos canaux Telegram.\n\n"
            f"📡 Canal principal: `{self.main_channel}`\n"
            f"🔀 Canaux actifs: `{len(self.redirect_channels)}`\n"
            f"⏱ Intervalle: `{self.check_interval}s`\n\n"
            f"{commandes_base}{commandes_admin}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /status — État du bot."""
        await update.message.reply_text(self._build_status_text(), parse_mode='Markdown')

    def _build_status_text(self) -> str:
        collecte = "🟢 Active" if self.is_running else "🔴 Arrêtée"
        last_check = self.last_check.strftime('%H:%M:%S') if self.last_check else 'Jamais'
        last_game = f"#{self.last_api_game['game_number']}" if self.last_api_game else 'En attente...'
        total_channels = len(self._all_channels())
        last_sent = max(self.seen_game_nums) if self.seen_game_nums else 0
        return (
            f"📊 *État du Bot*\n\n"
            f"📡 Collecte de données: {collecte}\n"
            f"🕐 Dernière vérification: `{last_check}`\n"
            f"🔢 Dernier jeu API: `{last_game}`\n"
            f"🎮 Jeux en mémoire: `{len(self.history)}`\n"
            f"🔀 Dernier jeu envoyé: `#{last_sent}`\n"
            f"📨 Jeux envoyés (session): `{len(self.seen_game_nums)}`\n\n"
            f"📡 Canal principal: `{self.main_channel}`\n"
            f"🔀 Canaux de redirection: `{len(self.redirect_channels)}`\n"
            f"🌐 Total canaux actifs: `{total_channels}`\n"
            f"👤 Admin: `{self.admin_id}`"
        )

    async def config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /config — Configuration (admin)."""
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return
        text, markup = self._build_config_message()
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')

    def _build_config_message(self):
        keyboard = [
            [InlineKeyboardButton("🌍 Langue", callback_data='cfg_language'),
             InlineKeyboardButton("⏱ Intervalle", callback_data='cfg_interval')],
            [InlineKeyboardButton("📡 Canaux", callback_data='channels')]
        ]
        text = (
            f"⚙️ *Configuration actuelle*\n\n"
            f"🌍 Langue: `{self.language}`\n"
            f"⏱ Intervalle: `{self.check_interval}s`\n"
            f"📡 Canal principal: `{self.main_channel}`\n"
            f"🔀 Canaux de redirection: `{len(self.redirect_channels)}`\n"
            f"🌐 Total canaux: `{len(self._all_channels())}`"
        )
        return text, InlineKeyboardMarkup(keyboard)

    async def redirect_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /redirect list              — liste les canaux
        /redirect add -1001234567   — ajoute un canal
        /redirect remove -1001234   — retire un canal
        /redirect -1001234567       — raccourci pour add
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        args = context.args or []

        if not args or args[0].lower() == 'list':
            if self.redirect_channels:
                ch_list = '\n'.join(f"• `{c}`" for c in self.redirect_channels)
                text = (
                    f"📡 *Canaux de redirection ({len(self.redirect_channels)}):*\n"
                    f"{ch_list}\n\n"
                    f"🌐 Total canaux (avec principal): `{len(self._all_channels())}`"
                )
            else:
                text = (
                    "📡 Aucun canal de redirection configuré.\n\n"
                    "*Commandes:*\n"
                    "`/redirect add -1001234567890` — ajouter\n"
                    "`/redirect remove -1001234567890` — retirer\n"
                    "`/redirect list` — lister"
                )
            await update.message.reply_text(text, parse_mode='Markdown')
            return

        if args[0] not in ('add', 'remove') and len(args) == 1:
            args = ['add'] + args

        action = args[0].lower()

        if len(args) < 2:
            await update.message.reply_text(
                "❌ ID manquant.\nExemple: `/redirect add -1001234567890`",
                parse_mode='Markdown'
            )
            return

        try:
            channel_id = int(args[1])
        except ValueError:
            await update.message.reply_text(
                "❌ ID invalide. L'ID doit être un entier.\nExemple: `-1001234567890`",
                parse_mode='Markdown'
            )
            return

        if action == 'add':
            if channel_id in self.redirect_channels:
                await update.message.reply_text(
                    f"⚠️ Canal `{channel_id}` déjà dans la liste.", parse_mode='Markdown'
                )
            else:
                self.redirect_channels.append(channel_id)
                self.config.update('telegram', 'redirect_channels', self.redirect_channels)
                await update.message.reply_text(
                    f"✅ Canal `{channel_id}` ajouté.\n"
                    f"Total canaux de redirection: `{len(self.redirect_channels)}`\n"
                    f"Total canaux actifs: `{len(self._all_channels())}`",
                    parse_mode='Markdown'
                )
        elif action == 'remove':
            if channel_id in self.redirect_channels:
                self.redirect_channels.remove(channel_id)
                self.config.update('telegram', 'redirect_channels', self.redirect_channels)
                await update.message.reply_text(
                    f"✅ Canal `{channel_id}` retiré.\n"
                    f"Total restant: `{len(self.redirect_channels)}`",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    f"⚠️ Canal `{channel_id}` non trouvé dans la liste.", parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                "❌ Action inconnue. Utilisez `add`, `remove` ou `list`.", parse_mode='Markdown'
            )

    async def jeu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /jeu — Dernier jeu terminé."""
        finished_games = {k: v for k, v in self.history.items() if v.get('is_finished')}

        if not finished_games:
            if self.last_api_game is None:
                await update.message.reply_text(
                    "⏳ *Aucune donnée disponible pour l'instant.*\n"
                    "Le bot collecte les résultats, réessaie dans 30 secondes.",
                    parse_mode='Markdown'
                )
                return
            g = self.last_api_game
            await update.message.reply_text(
                f"⏳ *Jeu en attente*\n\n"
                f"🔢 Numéro : `#{g.get('game_number', '?')}`\n"
                f"📡 Statut : Prématch / En cours\n\n"
                f"_Aucun jeu terminé en mémoire pour l'instant._",
                parse_mode='Markdown'
            )
            return

        last_num = max(finished_games.keys())
        last_game = finished_games[last_num]

        text = "🎰 *DERNIER JEU BACCARA*\n\n"
        text += self._format_single_game(last_num, last_game)

        previous = sorted([k for k in finished_games if k != last_num], reverse=True)[:4]
        if previous:
            text += "\n\n```\n─────────────────```\n"
            text += "📋 *Jeux précédents :*\n\n"
            for num in previous:
                g = finished_games[num]
                winner = g.get('winner', '')
                icon = "👤" if winner == 'Player' else "🏦" if winner == 'Banker' else "🤝"
                p_cards = self._format_cards(g.get('player_cards', []))
                b_cards = self._format_cards(g.get('banker_cards', []))
                text += f"*Jeu #{num}* {icon}\n  👤 `{p_cards}`  🏦 `{b_cards}`\n\n"

        text += f"\n_🎮 Total en mémoire : {len(finished_games)} jeux_"
        await update.message.reply_text(text, parse_mode='Markdown')

    async def dernier_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.jeu_command(update, context)

    async def parties_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /parties — Jeux récents en format compact."""
        await update.message.reply_text("⏳ Récupération des données...", parse_mode='Markdown')

        results = get_latest_results()
        lines = ["🎰 *Parties Baccara en cours / récentes*\n"]

        if results:
            results_sorted = sorted(results, key=lambda r: r['game_number'])
            for r in results_sorted:
                lines.append(self._format_game_line(r['game_number'], r))
        else:
            lines.append("⚠️ Aucune donnée disponible depuis l'API.")

        finished = {k: v for k, v in self.history.items() if v.get('is_finished')}
        if finished:
            recent_nums = sorted(finished.keys(), reverse=True)[:8]
            api_nums = {r['game_number'] for r in results} if results else set()
            extra = [n for n in sorted(recent_nums) if n not in api_nums]
            if extra:
                lines.append("\n📋 *Historique récent:*")
                for num in extra:
                    lines.append(self._format_game_line(num, finished[num]))

        text = '\n'.join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n_...tronqué_"

        try:
            await update.message.reply_text(text, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(text)

    # ─────────────────────────────────────────────
    # COMMANDE : 5 DERNIERS JEUX API
    # ─────────────────────────────────────────────

    async def derniers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /derniers — Affiche les 5 derniers jeux terminés récupérés de l'API
        dans le format de redirection (Légiste Carte Enseigne).
        """
        finished = {k: v for k, v in self.history.items() if v.get('is_finished')}

        if not finished:
            await update.message.reply_text(
                "⏳ *Aucun jeu terminé en mémoire.*\n"
                "Le bot collecte encore les premières données, réessaie dans 30 secondes.",
                parse_mode='Markdown'
            )
            return

        recent_nums = sorted(finished.keys(), reverse=True)[:5]

        lines = []
        for num in sorted(recent_nums):
            line = self._format_redirect_game_line(num, finished[num])
            if line:
                lines.append(line)

        now_str = datetime.now().strftime('%H:%M:%S')
        header = (
            f"🕐 *{now_str}*  |  5 derniers jeux API\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        body = '\n\n'.join(lines) if lines else "_Aucune donnée disponible_"
        footer = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎰 Légiste Carte Enseigne 🤴💰"
        )

        await update.message.reply_text(header + body + footer, parse_mode='Markdown')

    # ─────────────────────────────────────────────
    # PUBLICITÉ
    # ─────────────────────────────────────────────

    async def setpub_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /setpub <message> — Définit le message publicitaire.
        Affiche un aperçu formaté du message.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        if not context.args:
            if self.pub_message:
                preview = self._format_pub_message()
                await update.message.reply_text(
                    f"📢 *Message publicitaire actuel (aperçu):*\n\n{preview}\n\n"
                    f"_Pour modifier: `/setpub Nouveau message ici`_",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    "📢 *Aucun message publicitaire défini.*\n\n"
                    "Pour définir:\n`/setpub Votre texte ici`\n\n"
                    "Ensuite pour programmer l'envoi:\n"
                    "`/startpub min 30` — toutes les 30 minutes\n"
                    "`/startpub msg 20` — toutes les 20 parties redirigées",
                    parse_mode='Markdown'
                )
            return

        self.pub_message = ' '.join(context.args)
        preview = self._format_pub_message()
        await update.message.reply_text(
            f"✅ *Message publicitaire enregistré!*\n\n"
            f"*Aperçu:*\n\n{preview}\n\n"
            f"_Programmez l'envoi avec:_\n"
            f"`/startpub min 30` — toutes les 30 minutes\n"
            f"`/startpub msg 20` — toutes les 20 parties redirigées",
            parse_mode='Markdown'
        )

    async def startpub_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /startpub min <N>  — envoie la pub toutes les N minutes
        /startpub msg <N>  — envoie la pub après chaque N jeux redirigés
        /startpub <N>      — raccourci pour 'min N' (compatibilité)
        Les deux modes peuvent être actifs simultanément.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        if not self.pub_message:
            await update.message.reply_text(
                "❌ Aucun message publicitaire défini.\n"
                "Utilisez d'abord: `/setpub Votre message ici`",
                parse_mode='Markdown'
            )
            return

        args = context.args or []

        if not args:
            await update.message.reply_text(
                "❌ *Usage:*\n"
                "`/startpub min 30` — toutes les 30 minutes\n"
                "`/startpub msg 20` — toutes les 20 parties redirigées\n"
                "`/startpub 30`     — raccourci pour 30 minutes",
                parse_mode='Markdown'
            )
            return

        # Détecter le mode
        if args[0].lower() == 'min':
            mode = 'min'
            val_str = args[1] if len(args) > 1 else ''
        elif args[0].lower() == 'msg':
            mode = 'msg'
            val_str = args[1] if len(args) > 1 else ''
        else:
            # Raccourci : /startpub 30 → mode min
            mode = 'min'
            val_str = args[0]

        try:
            val = int(val_str)
            if val < 1:
                raise ValueError
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ Valeur invalide.\n"
                "`/startpub min 30` — minutes\n"
                "`/startpub msg 20` — nombre de parties",
                parse_mode='Markdown'
            )
            return

        all_ch = self._all_channels()

        if mode == 'min':
            self.pub_interval_minutes = val
            self.pub_enabled = True

            if self.pub_job:
                self.pub_job.schedule_removal()
                self.pub_job = None

            self.pub_job = context.job_queue.run_repeating(
                self._send_pub_job,
                interval=val * 60,
                first=val * 60,
                name='pub_automatique'
            )
            await update.message.reply_text(
                f"📢 *Pub par intervalle de temps activée!*\n\n"
                f"⏱ Toutes les: `{val} minutes`\n"
                f"📡 Canaux: `{len(all_ch)}`\n\n"
                f"*Aperçu du message:*\n\n{self._format_pub_message()}",
                parse_mode='Markdown'
            )
            logger.info(f"[Pub/min] Démarrage toutes les {val} min → {len(all_ch)} canaux")

        else:  # mode == 'msg'
            self.pub_every_n_games = val
            self.pub_games_counter = 0
            await update.message.reply_text(
                f"📢 *Pub par nombre de parties activée!*\n\n"
                f"🎮 Toutes les: `{val} parties redirigées`\n"
                f"📡 Canaux: `{len(all_ch)}`\n"
                f"🔢 Compteur remis à zéro\n\n"
                f"*Aperçu du message:*\n\n{self._format_pub_message()}",
                parse_mode='Markdown'
            )
            logger.info(f"[Pub/msg] Démarrage toutes les {val} parties → {len(all_ch)} canaux")

    async def stoppub_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /stoppub        — arrête tous les modes de pub
        /stoppub min    — arrête uniquement le mode minuteur
        /stoppub msg    — arrête uniquement le mode compteur de parties
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        args = context.args or []
        mode = args[0].lower() if args else 'all'

        stopped = []

        if mode in ('all', 'min'):
            if self.pub_job:
                self.pub_job.schedule_removal()
                self.pub_job = None
            self.pub_enabled = False
            stopped.append("⏱ Mode minuteur")

        if mode in ('all', 'msg'):
            self.pub_every_n_games = 0
            self.pub_games_counter = 0
            stopped.append("🎮 Mode compteur de parties")

        if stopped:
            await update.message.reply_text(
                f"🛑 *Publication arrêtée:*\n" + '\n'.join(f"• {s}" for s in stopped),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ Mode inconnu. Utilisez `/stoppub`, `/stoppub min` ou `/stoppub msg`",
                parse_mode='Markdown'
            )
        logger.info(f"[Pub] Arrêt: {stopped}")

    async def _send_pub_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Job répété (mode minuteur) — envoie la pub via _dispatch_pub."""
        if not self.pub_enabled or not self.pub_message:
            return
        await self._dispatch_pub(context)

    # ─────────────────────────────────────────────
    # COMMANDE : CHANGER L'EMOJI DES JEUX EN COURS
    # ─────────────────────────────────────────────

    async def setemoji_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /setemoji <emoji>  — Change l'emoji affiché devant les jeux en cours (défaut ⏰).
        /setemoji reset    — Remet l'emoji par défaut (⏰).
        /setemoji          — Affiche l'emoji actuel.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        args = context.args or []

        if not args:
            await update.message.reply_text(
                f"⚙️ *Emoji actuel pour les jeux en cours:* `{self.pending_emoji}`\n\n"
                f"Pour changer: `/setemoji 🔴`\n"
                f"Pour réinitialiser: `/setemoji reset`",
                parse_mode='Markdown'
            )
            return

        new_emoji = args[0]

        if new_emoji.lower() == 'reset':
            new_emoji = '⏰'

        self.pending_emoji = new_emoji
        self.config.update('app', 'pending_emoji', new_emoji)

        await update.message.reply_text(
            f"✅ *Emoji mis à jour!*\n\n"
            f"Nouvel emoji: `{new_emoji}`\n"
            f"Exemple d'affichage: `{new_emoji}#N650. ▶️1(3♥️8♠️) - 4(Q♦️4♦️)`\n\n"
            f"_Ce changement s'applique immédiatement aux prochains jeux en cours._",
            parse_mode='Markdown'
        )
        logger.info(f"[Config] Emoji jeux en cours changé → {new_emoji}")

    # ─────────────────────────────────────────────
    # COMMANDE : CHANGER L'EMOJI DU SÉPARATEUR TIE
    # ─────────────────────────────────────────────

    async def settie_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /settie <emoji>  — Change l'emoji séparateur affiché pour les matchs nuls (défaut 🔰).
        /settie reset    — Remet l'emoji par défaut (🔰).
        /settie          — Affiche l'emoji actuel.
        """
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(self._admin_only_text())
            return

        args = context.args or []

        if not args:
            await update.message.reply_text(
                f"⚙️ *Emoji actuel pour les matchs nuls (Tie):* `{self.tie_emoji}`\n\n"
                f"Pour changer: `/settie 🏳️`\n"
                f"Pour réinitialiser: `/settie reset`",
                parse_mode='Markdown'
            )
            return

        new_emoji = args[0]

        if new_emoji.lower() == 'reset':
            new_emoji = '🔰'

        self.tie_emoji = new_emoji
        self.config.update('app', 'tie_emoji', new_emoji)

        await update.message.reply_text(
            f"✅ *Emoji de match nul mis à jour!*\n\n"
            f"Nouvel emoji: `{new_emoji}`\n"
            f"Exemple d'affichage: `#N1130. 6(4♦️9♣️3♣️) {new_emoji} 6(2♠️J♦️4♥️) #T12 🟣#X`\n\n"
            f"_Ce changement s'applique immédiatement aux prochains jeux._",
            parse_mode='Markdown'
        )
        logger.info(f"[Config] Emoji tie changé → {new_emoji}")

    # ─────────────────────────────────────────────
    # CALLBACKS INLINE (BOUTONS)
    # ─────────────────────────────────────────────

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user = query.from_user

        if query.data == 'status':
            keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data='menu')]]
            await query.edit_message_text(
                self._build_status_text(),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        elif query.data == 'config':
            if not self._is_admin(user.id):
                await query.edit_message_text(self._admin_only_text())
                return
            text, markup = self._build_config_message()
            await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')

        elif query.data == 'channels':
            keyboard = [[InlineKeyboardButton("🔙 Retour", callback_data='menu')]]
            all_ch = self._all_channels()
            if all_ch:
                ch_list = '\n'.join(f"• `{c}`" for c in all_ch)
                text = (
                    f"📡 *Tous les canaux actifs ({len(all_ch)}):*\n\n"
                    f"{ch_list}\n\n"
                    f"_Utilisez `/redirect add ID` pour ajouter un canal._"
                )
            else:
                text = "📡 Aucun canal configuré."
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

        elif query.data == 'menu':
            keyboard = [
                [InlineKeyboardButton("📊 Status", callback_data='status'),
                 InlineKeyboardButton("⚙️ Configuration", callback_data='config')],
                [InlineKeyboardButton("📡 Canaux", callback_data='channels')]
            ]
            await query.edit_message_text(
                "🎰 *Bot Baccara — Redirecteur* — Menu principal",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        elif query.data == 'cfg_language':
            if not self._is_admin(user.id):
                await query.edit_message_text(self._admin_only_text())
                return
            langs = ['FR', 'EN', 'ES', 'DE', 'RU', 'AR']
            keyboard = [
                [InlineKeyboardButton(
                    f"{'✅ ' if lg == self.language else ''}{lg}",
                    callback_data=f'set_lang_{lg}'
                ) for lg in langs],
                [InlineKeyboardButton("🔙 Retour", callback_data='config')]
            ]
            await query.edit_message_text(
                f"🌍 *Choisir la langue*\nLangue actuelle: `{self.language}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        elif query.data.startswith('set_lang_'):
            if not self._is_admin(user.id):
                await query.edit_message_text(self._admin_only_text())
                return
            new_lang = query.data.replace('set_lang_', '')
            self.language = new_lang
            self.config.update('app', 'language', new_lang)
            text, markup = self._build_config_message()
            await query.edit_message_text(
                f"✅ Langue changée en `{new_lang}`\n\n" + text,
                reply_markup=markup,
                parse_mode='Markdown'
            )

        elif query.data == 'cfg_interval':
            if not self._is_admin(user.id):
                await query.edit_message_text(self._admin_only_text())
                return
            options = [10, 15, 30, 60]
            keyboard = [
                [InlineKeyboardButton(
                    f"{'✅ ' if iv == self.check_interval else ''}{iv}s",
                    callback_data=f'set_interval_{iv}'
                ) for iv in options],
                [InlineKeyboardButton("🔙 Retour", callback_data='config')]
            ]
            await query.edit_message_text(
                f"⏱ *Choisir l'intervalle de collecte*\nActuel: `{self.check_interval}s`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        elif query.data.startswith('set_interval_'):
            if not self._is_admin(user.id):
                await query.edit_message_text(self._admin_only_text())
                return
            new_interval = int(query.data.replace('set_interval_', ''))
            self.check_interval = new_interval
            self.config.update('app', 'check_interval_seconds', new_interval)
            text, markup = self._build_config_message()
            await query.edit_message_text(
                f"✅ Intervalle changé à `{new_interval}s`\n\n" + text,
                reply_markup=markup,
                parse_mode='Markdown'
            )

    # ─────────────────────────────────────────────
    # DÉMARRAGE
    # ─────────────────────────────────────────────

    def run(self):
        """Démarre le bot."""
        if not self.token:
            logger.error("Token Telegram non configuré!")
            return

        web_port = int(os.environ.get("PORT", 10000))
        set_bot(self)
        start_web_server(port=web_port)

        application = Application.builder().token(self.token).build()

        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("config", self.config_command))
        application.add_handler(CommandHandler("redirect", self.redirect_command))
        application.add_handler(CommandHandler("jeu", self.jeu_command))
        application.add_handler(CommandHandler("dernier", self.dernier_command))
        application.add_handler(CommandHandler("derniers", self.derniers_command))
        application.add_handler(CommandHandler("parties", self.parties_command))
        application.add_handler(CommandHandler("setemoji", self.setemoji_command))
        application.add_handler(CommandHandler("settie", self.settie_command))
        application.add_handler(CommandHandler("setpub", self.setpub_command))
        application.add_handler(CommandHandler("startpub", self.startpub_command))
        application.add_handler(CommandHandler("stoppub", self.stoppub_command))

        application.add_handler(CallbackQueryHandler(self.button_callback))

        application.job_queue.run_repeating(
            self.collect_and_redirect,
            interval=self.check_interval,
            first=10,
            name='baccara_redirecteur'
        )

        logger.info(f"Bot démarré — Redirection vers {len(self.redirect_channels)} canaux, intervalle {self.check_interval}s")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    bot = BaccaraBot()
    bot.run()
