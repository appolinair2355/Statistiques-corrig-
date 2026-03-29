# Bot Telegram Baccara - Système de Prédiction

## Vue d'ensemble

Ce bot analyse les patterns du jeu de Baccara sur 1xBet et envoie des prédictions basées sur l'analyse des intervalles entre apparitions des enseignes.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  API 1xBet  │────▶│  utils_new  │────▶│  Historique │────▶│  Analyseur  │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                    │
                                                                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Canal     │◀────│     Bot     │◀────│  Prédiction │◀────│  Intervalles│
│  Telegram   │     │  Telegram   │     │   Générée   │     │   Calculés  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

## Configuration

Le fichier `config.json` contient tous les paramètres:

```json
{
    "telegram": {
        "bot_token": "VOTRE_TOKEN",
        "admin_id": 1190237801,
        "main_channel": -1003798444695,
        "redirect_channels": []
    },
    "app": {
        "language": "FR",
        "check_interval_seconds": 30,
        "verification_attempts": 3
    }
}
```

### Paramètres importants

| Paramètre | Description | Valeur actuelle |
|-----------|-------------|-----------------|
| `main_channel` | Canal où envoyer les prédictions | `-1003798444695` |
| `admin_id` | ID Telegram de l'admin | `1190237801` |
| `check_interval_seconds` | Fréquence de vérification | `30` secondes |
| `verification_attempts` | Tentatives de vérification (0-10) | `3` |
| `language` | Langue du bot | `FR` |

## Installation

1. **Cloner/installer les dépendances:**
```bash
pip install -r requirements.txt
```

2. **Configurer le bot:**
   - Éditer `config.json` avec vos paramètres
   - Ou utiliser les valeurs déjà configurées

3. **Lancer le bot:**
```bash
python bot_telegram_baccara.py
```

## Commandes disponibles

| Commande | Description | Qui peut utiliser |
|----------|-------------|-------------------|
| `/start` | Menu principal avec boutons | Tous |
| `/stats` | Statistiques du jour | Tous |
| `/status` | État du bot | Tous |
| `/config` | Modifier configuration | Admin uniquement |
| `/upload` | Uploader fichier costumes | Tous |

## Fonctionnement

### 1. Récupération des données
- Le bot interroge l'API 1xBet toutes les 30 secondes
- Récupère les jeux Baccara terminés
- Stocke dans l'historique local

### 2. Analyse
- Extrait les symboles des cartes du joueur
- Calcule les intervalles entre apparitions de chaque enseigne
- Détermine quel enseigne a le pattern le plus régulier
- Score basé sur: régularité > fréquence

### 3. Prédiction
- Génère un cycle de 3 prédictions sur la même enseigne
- Calcule le jeu cible: dernier_jeu + intervalle_moyen
- Envoie vers le canal Telegram configuré

### 4. Vérification
- Surveille les résultats
- Vérifie si l'enseigne apparaît au jeu N, N+1 ou N+2
- Met à jour les statistiques (win/loss)

## Structure des fichiers

```
.
├── bot_telegram_baccara.py      # Bot principal (28KB)
├── strategies_intervalles.py    # Analyseur d'intervalles (11KB)
├── strategies.py                # Gestionnaire de stratégies
├── utils_new.py                 # Récupération API 1xBet
├── config.json                  # Configuration (vos IDs)
├── daily_stats.json             # Statistiques journalières
├── requirements.txt             # Dépendances
└── README.md                    # Ce fichier
```

## Sécurité

⚠️ **NE JAMAIS COMMITTER `config.json`!** Il contient des tokens sensibles.

Le fichier `.gitignore` est configuré pour exclure:
- `config.json`
- `daily_stats.json`
- Dossiers `logs/` et `uploads/`

## Support

En cas de problème, le bot notifie automatiquement l'admin (ID: 1190237801).

## Multi-langue

Le bot supporte 6 langues:
- 🇫🇷 FR - Français
- 🇬🇧 EN - English
- 🇪🇸 ES - Español
- 🇩🇪 DE - Deutsch
- 🇷🇺 RU - Русский
- 🇸🇦 AR - العربية
