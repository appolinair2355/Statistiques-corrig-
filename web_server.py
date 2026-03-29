#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur HTTP minimal pour Render.com (port 10000).
Render exige qu'un service web écoute sur un port.
Ce module démarre un serveur léger en arrière-plan.
"""

import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

_bot_ref = None


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            body = self._build_status().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _build_status(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if _bot_ref is not None:
            running = "OUI" if _bot_ref.is_running else "NON"
            last_check = (
                _bot_ref.last_check.strftime("%H:%M:%S")
                if _bot_ref.last_check
                else "jamais"
            )
            history_size = len(_bot_ref.history)
            last_game = (
                f"#{_bot_ref.last_api_game['game_number']}"
                if _bot_ref.last_api_game
                else "N/A"
            )
        else:
            running = "N/A"
            last_check = "N/A"
            history_size = 0
            last_game = "N/A"

        return (
            f"Bot Baccara - OK\n"
            f"Heure serveur   : {now}\n"
            f"Bot actif       : {running}\n"
            f"Dernière verif  : {last_check}\n"
            f"Jeux en mémoire : {history_size}\n"
            f"Dernier jeu API : {last_game}\n"
        )


def start_web_server(port: int = 10000):
    """Démarre le serveur HTTP dans un thread daemon."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)

    def _run():
        server.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[WebServer] Serveur de santé démarré sur le port {port}")
    return server
