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
from urllib.parse import parse_qs, unquote, urlsplit

from ..theory import postflop, preflop_tree, studyspots
from . import trainer
from .library import Library, UnknownPlayer
from .solves import NeedSession
from .studies import build_studies_page

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "explorer.js": "text/javascript; charset=utf-8", "explorer.css": "text/css; charset=utf-8",
                "trainer.js": "text/javascript; charset=utf-8", "trainer.css": "text/css; charset=utf-8",
                "coach.js": "text/javascript; charset=utf-8", "coach.css": "text/css; charset=utf-8"}
MAX_PATH = 40  # étapes d'un chemin dans l'arbre (bien plus qu'un coup réel)
MAX_BODY = 200 * 1024 * 1024  # 200 Mo d'historiques par import
MAX_SMALL_BODY = 64 * 1024
STUDENT_PAGES = ("leaks", "preflop", "solveur", "spots", "bilan", "bluffs", "tables", "mains")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, library: Library):
        super().__init__(address, Handler)
        self.library = library

    def handle_error(self, request, client_address) -> None:
        # Le navigateur qui abandonne une requête (rechargement, changement d'onglet) n'est pas une erreur.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)

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
        try:
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:  # le navigateur est parti avant la réponse (page rechargée, onglet changé)
            self.close_connection = True

    def _json(self, data, status: int = 200) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def _html(self, html: str, status: int = 200) -> None:
        self._send(status, "text/html; charset=utf-8", html.encode())

    def _download(self, html_text: str, filename: str) -> None:
        """Une page à enregistrer (rapport à envoyer)."""
        body = html_text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            self.close_connection = True

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
            if parts == ["moi", "rapport"]:
                return self._download(library.leaks_page(standalone=True), "leakfinding.html")
            if len(parts) == 2 and parts[0] == "moi":
                return self._html(library.self_page(parts[1]))
            if parts == ["api", "leaks"]:
                return self._json(library.leaks_state())
            if parts == ["api", "eleves"]:
                return self._json(library.students_summary())
            if len(parts) == 3 and parts[0] == "eleve":
                student = library.student(parts[1])
                if parts[2] == "rapport":
                    return self._download(student.leaks_page(standalone=True), f"leakfinding-{parts[1]}.html")
                if parts[2] not in STUDENT_PAGES:
                    raise KeyError(parts[2])
                return self._html(student.self_page(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "eleves"] and parts[3] in ("leaks", "revue"):
                student = library.student(parts[2])
                return self._json(student.leaks_state() if parts[3] == "leaks" else student.review_state())
            if parts[:2] == ["api", "coups"] and len(parts) in (2, 3) or \
                    parts[:2] == ["api", "eleves"] and len(parts) in (4, 5) and parts[3] == "coups":
                owner = library if parts[1] == "coups" else library.student(parts[2])
                if len(parts) in (3, 5):  # une main, pour la rejouer
                    return self._json(owner.hand_fiche(parts[-1]))
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                return self._json(owner.spots_search(query))
            if parts == ["api", "mains"] or (len(parts) == 4 and parts[:2] == ["api", "eleves"] and parts[3] == "mains"):
                owner = library if len(parts) == 2 else library.student(parts[2])
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                return self._json(owner.hands_detail(query))
            if len(parts) == 3 and parts[0] == "p":
                return self._html(library.player_page(parts[1], parts[2]))
            if parts == ["etudes"]:
                return self._html(build_studies_page(embed=True))
            if parts == ["etudes", "plan"]:
                return self._html(library.plan_page())
            if parts == ["api", "plan"]:
                return self._json(library.plan_state())
            if parts == ["api", "coach"]:
                return self._json(library.coach.status())
            if len(parts) == 3 and parts[:2] == ["api", "coach"]:
                view = library.coach.view(parts[2])
                return self._json(view) if view else self._error(404, "Conversation inconnue.")
            if len(parts) == 2 and parts[0] == "etudes":
                return self._html(build_studies_page(embed=True, section=parts[1]))
            if parts == ["entraineur"]:
                return self._html((STATIC / "trainer.html").read_text(encoding="utf-8"))
            if parts == ["api", "entraineur"]:
                return self._json(trainer.overview())
            if parts == ["api", "sauvegarde"]:
                return self._json(library.backups.view())
            if parts == ["api", "revue"]:
                villain = parse_qs(urlsplit(self.path).query).get("adversaire", [None])[0]
                return self._json(library.review_state(villain))
            if parts == ["api", "spots", "6max"]:  # toutes les séries 6-max
                return self._json(library.ring_spot_sets())
            if len(parts) == 3 and parts[:2] == ["api", "spots"]:
                return self._json(library.spot_set(parts[2]))
            if len(parts) == 2 and parts[0] == "explorateur" and (parts[1] == "preflop" or library.find_hand(parts[1])
                                                                   or studyspots.is_ident(parts[1])):
                page = (STATIC / "explorer.html").read_text(encoding="utf-8")
                return self._html(page.replace("__HAND__", html.escape(parts[1], quote=True)))
        except (UnknownPlayer, KeyError):
            return self._error(404, "Page introuvable.")
        except ConnectionError:
            self.close_connection = True
            return None
        except Exception:  # noqa: BLE001 — une erreur d'analyse ne doit pas tuer le serveur
            print(f"Erreur sur la page {unquote(self.path)} :", file=sys.stderr)
            traceback.print_exc()
            return self._error(500, "Erreur pendant l'analyse (détails dans le terminal).")
        return self._error(404, "Page introuvable.")

    # --- POST -------------------------------------------------------------------
    def do_POST(self):
        if not self._trusted():
            return self._error(403, "Accès refusé.")
        parts = self._parts()
        library = self.server.library
        if parts == ["api", "joueurs"]:
            payload = self._small_json()
            if not (isinstance(payload, dict) and isinstance(payload.get("name"), str)
                    and payload.get("kind") in (None, "reg", "rec")):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.set_kind(payload["name"], payload.get("kind")))
            except UnknownPlayer:
                return self._error(404, "Adversaire inconnu.")
        if parts == ["api", "reload"]:
            library.reload()
            return self._json(library.summary())
        if parts == ["api", "resoudre"]:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.solve(payload["hand"], bool(payload.get("start")),
                                                bool(payload.get("force")), bool(payload.get("fresh"))))
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
        if parts == ["api", "explorateur", "preflop"]:
            payload = self._small_json()
            if not isinstance(payload, dict):
                return self._error(400, "Requête invalide.")
            line = payload.get("line")
            if payload.get("family") in preflop_tree.FAMILY_LINES:
                line = list(preflop_tree.FAMILY_LINES[payload["family"]])
            if not (isinstance(line, list) and len(line) <= 6 and all(a in preflop_tree.ACTIONS for a in line)):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(preflop_tree.node(line))
            except ValueError:
                return self._error(404, "Ligne préflop hors de la solution.")
        if parts == ["api", "explorateur", "flop"]:
            payload = self._small_json()
            board = payload.get("board") if isinstance(payload, dict) else None
            if not (isinstance(payload, dict) and payload.get("family") in studyspots.FAMILIES
                    and trainer.valid_cards(board, 3)):
                return self._error(400, "Requête invalide.")
            return self._json(studyspots.flop_options(payload["family"], board))
        if parts == ["api", "estimation"]:  # durée d'une résolution selon la précision
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.estimate(payload["hand"]))
            except (UnknownPlayer, KeyError):
                return self._error(404, "Main introuvable.")
            except (ValueError, postflop.SolverError) as exc:  # coup non couvert, solveur absent
                return self._error(400, str(exc))
        if parts == ["api", "precision"]:  # précision visée des prochaines résolutions
            payload = self._small_json()
            try:
                return self._json({"precision": postflop.set_precision(float(payload["precision"]))})
            except (TypeError, KeyError, ValueError):
                return self._error(400, "Précision inconnue.")
        if parts == ["api", "explorateur", "tailles"]:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.choose_hand_sizes(payload["hand"]))
            except (UnknownPlayer, KeyError):
                return self._error(404, "Main introuvable.")
            except ValueError as exc:  # pot non couvert (Unsupported compris)
                return self._error(400, str(exc))
        if parts[:3] == ["api", "explorateur", "ranges"] and len(parts) <= 4:
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            action = parts[3] if len(parts) == 4 else None
            try:
                if action is None:
                    return self._json(library.ranges_state(payload["hand"]))
                if action == "enregistrer" and isinstance(payload.get("ranges"), dict):
                    return self._json(library.save_ranges(payload["hand"], payload.get("scope"), payload["ranges"]))
                if action == "effacer":
                    return self._json(library.clear_ranges(payload["hand"], payload.get("scope")))
                return self._error(400, "Requête invalide.")
            except (UnknownPlayer, KeyError):
                return self._error(404, "Main introuvable.")
            except (ValueError, postflop.Unsupported) as exc:
                return self._error(400, str(exc))
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
        if parts == ["api", "entraineur", "mains"]:
            payload = self._small_json()
            board = payload.get("board") if isinstance(payload, dict) else None
            holes = payload.get("holes") if isinstance(payload, dict) else None
            if not (trainer.valid_cards(board) and 3 <= len(board) <= 5 and isinstance(holes, list) and len(holes) == 2
                    and all(trainer.valid_cards(h, 2) for h in holes)
                    and trainer.valid_cards(board + holes[0] + holes[1])):
                return self._error(400, "Requête invalide.")
            return self._json(trainer.hands_info(board, holes))
        if parts == ["api", "entraineur", "resultat"]:
            payload = self._small_json()
            if not isinstance(payload, dict):
                return self._error(400, "Requête invalide.")
            return self._json(trainer.record(payload.get("entries")))
        if parts == ["api", "entraineur", "effacer"]:
            return self._json(trainer.clear())
        if parts[:2] == ["api", "coach"] and parts[2:] in (["message"], ["texte"]):
            payload = self._small_json()
            if not isinstance(payload, dict):
                return self._error(400, "Requête invalide.")
            text, conv, context = payload.get("text"), payload.get("conversation"), payload.get("context")
            if not (isinstance(text, str) and 0 < len(text.strip()) <= 4000 and (conv is None or isinstance(conv, str))):
                return self._error(400, "Requête invalide.")
            if context is not None and not (isinstance(context, dict) and isinstance(context.get("spot"), str)
                                            and valid_path(context.get("path") or [])
                                            and (context.get("main") is None or isinstance(context.get("main"), str))):
                return self._error(400, "Requête invalide.")
            if parts[2] == "texte":  # à coller dans Claude (coach branché par MCP sur l'abonnement)
                return self._json({"text": library.coach.prompt(text.strip(), context)})
            return self._json(library.coach.ask(conv, text.strip(), context))
        if parts == ["api", "ranges", "hand2note"]:  # charts 6-max de Hand2Note Guide, sur ta machine
            try:
                return self._json(library.load_hand2note())
            except (OSError, ValueError) as exc:
                return self._error(502, f"Téléchargement impossible : {exc}")
        if parts == ["api", "plan", "preparer"]:
            return self._json(library.plan_state(start=True))
        if parts == ["api", "plan", "arreter"]:
            return self._json(library.plan_cancel())
        if parts == ["api", "sauvegarde", "reglages"]:
            payload = self._small_json()
            if not (isinstance(payload, dict) and isinstance(payload.get("dest"), str) and len(payload["dest"]) <= 500
                    and isinstance(payload.get("studies"), bool) and isinstance(payload.get("auto"), bool)):
                return self._error(400, "Requête invalide.")
            return self._json(library.backups.configure(payload["dest"], payload["studies"], payload["auto"]))
        if parts == ["api", "sauvegarde", "lancer"]:
            return self._json(library.backups.start("backup"))
        if parts == ["api", "sauvegarde", "restaurer"]:
            return self._json(library.backups.start("restore"))
        if len(parts) == 3 and parts[:2] == ["api", "revue"] and parts[2] in ("lancer", "arreter"):
            payload = self._small_json()
            villain = payload.get("adversaire") if isinstance(payload, dict) else None
            if villain is not None and not isinstance(villain, str):
                return self._error(400, "Requête invalide.")
            try:
                if parts[2] == "arreter":
                    return self._json(library.review_cancel(villain))
                return self._json(library.review_state(villain, start=True))
            except UnknownPlayer:
                return self._error(404, "Adversaire inconnu.")
        if parts[:3] == ["api", "spots", "6max"] and len(parts) == 4 and parts[3] in ("resoudre", "arreter"):
            return self._json(library.ring_spot_cancel() if parts[3] == "arreter" else library.ring_spot_sets(start=True))
        if len(parts) == 4 and parts[:2] == ["api", "spots"] and parts[3] in ("resoudre", "arreter"):
            if not studyspots.known_family(parts[2]):
                return self._error(404, "Série inconnue.")
            if parts[3] == "arreter":
                return self._json(library.spot_cancel(parts[2]))
            return self._json(library.spot_set(parts[2], start=True))
        if len(parts) == 4 and parts[:2] == ["api", "resoudre"] and parts[3] == "arreter":
            job = library.solves.cancel(parts[2])
            return self._json(job) if job else self._error(404, "Résolution inconnue.")
        if parts == ["api", "import"]:
            files = self._import_files()
            return files if files is None else self._json(library.import_files(files))
        if len(parts) == 3 and parts[:2] == ["api", "leaks"] and parts[2] in ("lancer", "arreter"):
            return self._json(library.leaks_cancel() if parts[2] == "arreter" else library.leaks_state(start=True))
        if parts == ["api", "eleves"]:
            payload = self._small_json()
            name = payload.get("name") if isinstance(payload, dict) else None
            pseudo = payload.get("pseudo") if isinstance(payload, dict) else None
            if not (isinstance(name, str) and 0 < len(name.strip()) <= 60
                    and (pseudo is None or (isinstance(pseudo, str) and len(pseudo) <= 60))):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.create_student(name, pseudo))
            except ValueError as exc:
                return self._error(400, str(exc))
        if len(parts) >= 4 and parts[:2] == ["api", "eleves"]:
            try:
                student = library.student(parts[2])
            except UnknownPlayer:
                return self._error(404, "Élève inconnu.")
            if parts[3:] == ["import"]:
                files = self._import_files()
                return files if files is None else self._json(student.import_files(files))
            if len(parts) == 5 and parts[3] == "leaks" and parts[4] in ("lancer", "arreter"):
                return self._json(student.leaks_cancel() if parts[4] == "arreter" else student.leaks_state(start=True))
            if len(parts) == 5 and parts[3] == "revue" and parts[4] in ("lancer", "arreter"):
                return self._json(student.review_cancel() if parts[4] == "arreter" else student.review_state(start=True))
        return self._error(404, "Page introuvable.")

    def _import_files(self) -> Optional[list]:
        """Les fichiers d'un import (ou None, la réponse d'erreur déjà envoyée)."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._error(413, "Import trop volumineux (200 Mo maximum).")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
            files = payload["files"]
            if not isinstance(files, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            self._error(400, "Requête d'import invalide.")
            return None
        return [f for f in files if isinstance(f, dict)]


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
        library.backups.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
