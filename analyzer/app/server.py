"""Serveur local de l'application : l'interface, l'API et les pages d'analyse.

N'écoute que sur 127.0.0.1 et refuse les requêtes dont l'en-tête Host (ou Origin)
ne correspond pas : une page web tierce ne peut ni lire tes mains ni en importer.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from ..theory import postflop
from .library import Library, UnknownPlayer
from .solves import NeedSession
from .studies import build_studies_page

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "explorer.js": "text/javascript; charset=utf-8", "explorer.css": "text/css; charset=utf-8"}
MAX_PATH = 40  # étapes d'un chemin dans l'arbre (bien plus qu'un coup réel)
MAX_BODY = 200 * 1024 * 1024  # 200 Mo d'historiques par import
MAX_SMALL_BODY = 64 * 1024


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, library: Library):
        super().__init__(address, Handler)
        self.library = library

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server: AppServer
    server_version = "Analyzer"

    def log_message(self, fmt, *args):  # console silencieuse
        pass

    # --- réponses ---------------------------------------------------------------
    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def _html(self, html: str, status: int = 200) -> None:
        self._send(status, "text/html; charset=utf-8", html.encode())

    def _error(self, status: int, message: str) -> None:
        if self.path.startswith("/api/"):
            self._json({"error": message}, status)
        else:
            self._html(f'<!doctype html><meta charset="utf-8"><p style="font-family:system-ui;padding:24px">{message}</p>',
                       status)

    def _trusted(self) -> bool:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin.removeprefix("http://") in self.server.allowed_hosts

    def _small_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_SMALL_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def _parts(self) -> list[str]:
        return [unquote(p) for p in urlsplit(self.path).path.split("/") if p]

    # --- GET --------------------------------------------------------------------
    def do_GET(self):
        if not self._trusted():
            return self._error(403, "Accès refusé.")
        parts = self._parts()
        library = self.server.library
        try:
            if not parts:
                return self._html((STATIC / "index.html").read_text(encoding="utf-8"))
            if parts[0] == "static" and len(parts) == 2 and parts[1] in STATIC_FILES:
                return self._send(200, STATIC_FILES[parts[1]], (STATIC / parts[1]).read_bytes())
            if parts == ["api", "state"]:
                return self._json(library.summary())
            if parts == ["api", "solveur"]:
                return self._json(postflop.status())
            if len(parts) == 3 and parts[:2] == ["api", "resoudre"]:
                job = library.solves.get(parts[2])
                return self._json(job) if job else self._error(404, "Résolution inconnue.")
            if len(parts) == 2 and parts[0] == "moi":
                return self._html(library.self_page(parts[1]))
            if len(parts) == 3 and parts[0] == "p":
                return self._html(library.player_page(parts[1], parts[2]))
            if parts == ["etudes"]:
                return self._html(build_studies_page(embed=True))
            if len(parts) == 2 and parts[0] == "explorateur" and parts[1] in library.by_id:
                page = (STATIC / "explorer.html").read_text(encoding="utf-8")
                return self._html(page.replace("__HAND__", html.escape(parts[1], quote=True)))
        except (UnknownPlayer, KeyError):
            return self._error(404, "Page introuvable.")
        except Exception:  # noqa: BLE001 — une erreur d'analyse ne doit pas tuer le serveur
            traceback.print_exc()
            return self._error(500, "Erreur pendant l'analyse (détails dans le terminal).")
        return self._error(404, "Page introuvable.")

    # --- POST -------------------------------------------------------------------
    def do_POST(self):
        if not self._trusted():
            return self._error(403, "Accès refusé.")
        parts = self._parts()
        library = self.server.library
        if parts == ["api", "reload"]:
            library.reload()
            return self._json(library.summary())
        if parts == ["api", "resoudre"]:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.solve(payload["hand"], bool(payload.get("start")),
                                                bool(payload.get("force"))))
            except UnknownPlayer:
                return self._error(404, "Main introuvable.")
        if parts == ["api", "etudes", "supprimer"]:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("key"), str):
                return self._error(400, "Requête invalide.")
            return self._json({"ok": postflop.delete_study(payload["key"])})
        if parts == ["api", "explorateur", "etat"]:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.explorer_state(payload["hand"]))
            except (UnknownPlayer, KeyError):
                return self._error(404, "Main introuvable.")
        if parts == ["api", "explorateur", "noeud"]:
            payload = self._small_json()
            path = payload.get("path") if isinstance(payload, dict) else None
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str) or not valid_path(path):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.explorer_node(payload["hand"], path))
            except UnknownPlayer:
                return self._error(404, "Main introuvable.")
            except postflop.Unsupported as exc:
                return self._error(404, str(exc))
            except NeedSession:
                return self._json({"error": "Branche hors de la ligne jouée : relance la résolution pour l'explorer.",
                                   "state": "session"}, 409)
            except postflop.SolverError as exc:
                return self._json({"error": str(exc), "state": "session"}, 409)
        if len(parts) == 4 and parts[:2] == ["api", "resoudre"] and parts[3] == "arreter":
            job = library.solves.cancel(parts[2])
            return self._json(job) if job else self._error(404, "Résolution inconnue.")
        if parts == ["api", "import"]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._error(413, "Import trop volumineux (200 Mo maximum).")
            try:
                payload = json.loads(self.rfile.read(length))
                files = payload["files"]
                if not isinstance(files, list):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._error(400, "Requête d'import invalide.")
            return self._json(library.import_files([f for f in files if isinstance(f, dict)]))
        return self._error(404, "Page introuvable.")


def valid_path(path) -> bool:
    """Chemin dans l'arbre : [{"type": "action", "index": 0}, {"type": "card", "card": "Ah"}, ...]."""
    if not isinstance(path, list) or len(path) > MAX_PATH:
        return False
    for step in path:
        if not isinstance(step, dict):
            return False
        if step.get("type") == "action":
            if set(step) != {"type", "index"} or type(step["index"]) is not int or not 0 <= step["index"] < 20:
                return False
        elif step.get("type") == "card":
            if set(step) != {"type", "card"} or not isinstance(step["card"], str) \
                    or not re.fullmatch(r"[2-9TJQKA][cdhs]", step["card"]):
                return False
        else:
            return False
    return True


def start(library: Library, port: int = 8765, tries: int = 10) -> AppServer:
    """Démarre sur le premier port libre à partir de `port`."""
    last_error: Optional[OSError] = None
    for candidate in range(port, port + tries):
        try:
            return AppServer(("127.0.0.1", candidate), library)
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"Aucun port libre entre {port} et {port + tries - 1} : {last_error}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analyzer app", description="Lance l'application Analyzer HU.")
    parser.add_argument("-d", "--dossier", default="hands", help="dossier des historiques (défaut : hands/)")
    parser.add_argument("--hero", help="ton pseudo (détecté automatiquement via le tag Hero)")
    parser.add_argument("--port", type=int, default=8765, help="port local (défaut : 8765)")
    parser.add_argument("--sans-navigateur", action="store_true", help="n'ouvre pas le navigateur")
    args = parser.parse_args(argv)

    print("Chargement des mains…", flush=True)
    library = Library(args.dossier, args.hero)
    server = start(library, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"{len(library.hands)} mains, toi : {library.hero or 'inconnu'}")
    print(f"Analyzer est ouvert sur {url}  (Ctrl+C pour arrêter)", flush=True)
    if not args.sans_navigateur:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        library.solves.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
