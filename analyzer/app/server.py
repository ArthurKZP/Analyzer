"""Serveur local de l'application : l'interface, l'API et les pages d'analyse.

N'écoute que sur 127.0.0.1 et refuse les requêtes dont l'en-tête Host (ou Origin)
ne correspond pas : une page web tierce ne peut ni lire tes mains ni en importer.
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import date
import re
import sys
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from .. import NAME, aliases, db, stakes
from ..db import hands as db_hands
from ..errors import NotFound
from ..theory import postflop, preflop_tree, ring_tree, stake_charts, studyspots
from . import trainer
from .library import Library, UnknownPlayer
from .solves import NeedSession
from .studies import build_studies_page

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "explorer.js": "text/javascript; charset=utf-8", "explorer.css": "text/css; charset=utf-8",
                "trainer.js": "text/javascript; charset=utf-8", "trainer.css": "text/css; charset=utf-8",
                "replayer.js": "text/javascript; charset=utf-8", "replayer.css": "text/css; charset=utf-8",
                "coach.js": "text/javascript; charset=utf-8", "coach.css": "text/css; charset=utf-8",
                "rangekit.js": "text/javascript; charset=utf-8"}
MAX_PATH = 40  # étapes d'un chemin dans l'arbre (bien plus qu'un coup réel)
MAX_BODY = 200 * 1024 * 1024  # 200 Mo d'historiques par import (un envoi)
MAX_FILE = 1024 ** 3  # un historique (ou une archive) envoyé seul, en octets : 1 Go
MAX_SMALL_BODY = 64 * 1024
MAX_CHARTS_BODY = 4 * 1024 * 1024  # des charts préflop importés (Paramètres › Ranges préflop)
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
STUDENT_PAGES = ("leaks", "preflop", "solveur", "spots", "bilan", "stats", "bluffs", "tables", "mains", "suivi")
REFINE_SIDES = {"attaque": "attack", "defense": "defense"}  # affiner un niveau du plan : le côté


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
    server_version = NAME

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

    def _download(self, content: str | bytes, filename: str, ctype: str = "text/html; charset=utf-8") -> None:
        """Un fichier à enregistrer (rapport à envoyer : page, PDF, présentation)."""
        body = content.encode() if isinstance(content, str) else content
        self.send_response(200)
        self.send_header("Content-Type", ctype)
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

    def _small_json(self, limit: int = MAX_SMALL_BODY):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def _parts(self) -> list[str]:
        return [unquote(p) for p in urlsplit(self.path).path.split("/") if p]

    def _format(self) -> Optional[str]:
        """Le format demandé (« ?format=ring ») : le Leakfinding a un rapport pour le heads-up, un pour les tables à
        plusieurs (« 6-max », « 3-max »… y mènent aussi)."""
        return parse_qs(urlsplit(self.path).query).get("format", [None])[0]

    def _query(self) -> dict[str, str]:
        """Les paramètres de l'adresse (le premier de chaque nom)."""
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _ranges_set(self) -> str:
        """Le jeu de ranges demandé (« ?jeu=hautes » ou « ?ranges=hautes ») : « petites » par défaut."""
        query = parse_qs(urlsplit(self.path).query)
        found = (query.get("jeu") or query.get("ranges") or ["petites"])[0]
        return found if found in stake_charts.SETS else "petites"

    def _study_set(self) -> str:
        """Les ranges des études demandées (« ?ranges=hautes »), sinon celles de la référence (Paramètres › Ranges
        préflop)."""
        if "ranges" in parse_qs(urlsplit(self.path).query):
            return self._ranges_set()
        return stake_charts.reference_set()

    def _stakes(self) -> Optional[str]:
        """Le niveau de limites demandé (« ?limites=micro ») pour l'Étude du field (stakes.BANDS), ou None."""
        band = parse_qs(urlsplit(self.path).query).get("limites", [None])[0]
        return band if band in stakes.BANDS else None

    @staticmethod
    def _report_name(table_format: Optional[str], who: str = "", ext: str = "html") -> str:
        suffix = "-tables-a-plusieurs" if table_format and table_format != "HU" else ""
        return f"leakfinding{'-' + who if who else ''}{suffix}.{ext}"

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
            if parts == ["api", "fichiers"]:  # tes historiques importés
                return self._json(library.files_view())
            if parts == ["api", "pseudos"]:  # les pseudos de tes historiques (leurs mains, celles supprimées)
                return self._json(library.pseudos_view())
            if len(parts) == 3 and parts[:2] == ["api", "import"]:  # l'avancement d'un import (barre)
                job = library.import_status(parts[2])
                return self._json(job) if job else self._error(404, "Import inconnu.")
            if parts == ["api", "alias"]:  # les pseudos regroupés sous un alias
                return self._json(aliases.groups())
            if parts == ["api", "periode"]:  # la période d'analyse
                return self._json(library.period_view())
            if parts == ["api", "solveur"]:
                return self._json(postflop.status())
            if len(parts) == 3 and parts[:2] == ["api", "resoudre"]:
                job = library.solves.get(parts[2])
                return self._json(job) if job else self._error(404, "Résolution inconnue.")
            if parts == ["moi", "rapport"]:
                return self._download(library.leaks_page(standalone=True, table_format=self._format()),
                                      self._report_name(self._format()))
            if parts == ["moi", "rapport.pdf"]:
                return self._download(library.report_pdf(self._format()), self._report_name(self._format(), ext="pdf"),
                                      "application/pdf")
            if parts == ["moi", "presentation.pptx"]:
                return self._download(library.report_pptx(self._format()), self._report_name(self._format(), ext="pptx"),
                                      PPTX)
            if parts == ["moi", "leaks"]:
                return self._html(library.leaks_page(table_format=self._format()))
            if len(parts) == 2 and parts[0] == "moi":
                return self._html(library.self_page(parts[1], table_format=self._format(), filters=self._query()))
            if len(parts) == 2 and parts[0] == "field":  # Étude du field
                return self._html(library.field_page(parts[1], self._format(), self._stakes()))
            if len(parts) == 3 and parts[0] == "field" and parts[1] == "joueur":  # la fiche d'un adversaire
                return self._html(library.field_player(parts[2], self._format(), self._stakes()))
            if len(parts) == 3 and parts[0] == "field" and parts[1] == "groupe":  # un groupe de récréatifs
                return self._html(library.field_group(parts[2], self._format(), self._stakes()))
            if parts == ["parametres", "general"]:  # Paramètres
                return self._html(library.settings_page())
            if parts == ["parametres", "joueurs"]:  # … les types des adversaires et les alias
                return self._html(library.field_page("joueurs"))
            if parts == ["parametres", "ranges"]:  # … tes ranges préflop par défaut, ligne par ligne
                return self._html(library.ranges_settings_page())
            if parts == ["api", "ranges", "defaut"]:  # (« ?jeu=hautes » : les ranges des hautes limites)
                return self._json(library.default_ranges_view(self._ranges_set()))
            if parts == ["api", "ranges", "limites"]:  # … tes charts par niveau de limites
                return self._json(library.stake_charts_view())
            if parts == ["api", "leaks"]:
                return self._json(library.leaks_state(table_format=self._format()))
            if parts == ["api", "eleves"]:
                return self._json(library.students_summary())
            if parts == ["api", "references"]:  # tes joueurs de référence, et les pseudos qui peuvent le devenir
                return self._json(library.references_view())
            if len(parts) == 3 and parts[0] == "reference":  # une page d'un joueur de référence
                return self._html(library.reference_page(parts[1], parts[2], self._format(), self._query()))
            if len(parts) == 3 and parts[0] == "eleve":
                student = library.student(parts[1])
                if parts[2] == "rapport":
                    return self._download(student.leaks_page(standalone=True, table_format=self._format()),
                                          self._report_name(self._format(), parts[1]))
                if parts[2] == "rapport.pdf":
                    return self._download(student.report_pdf(self._format()),
                                          self._report_name(self._format(), parts[1], "pdf"), "application/pdf")
                if parts[2] == "presentation.pptx":
                    return self._download(student.report_pptx(self._format()),
                                          self._report_name(self._format(), parts[1], "pptx"), PPTX)
                if parts[2] not in STUDENT_PAGES:
                    raise NotFound(parts[2])
                if parts[2] == "leaks":
                    return self._html(student.leaks_page(table_format=self._format()))
                return self._html(student.self_page(parts[2], table_format=self._format(), filters=self._query()))
            if len(parts) == 5 and parts[:2] == ["api", "eleves"] and parts[3] == "import":
                job = library.student(parts[2]).import_status(parts[4])
                return self._json(job) if job else self._error(404, "Import inconnu.")
            if len(parts) == 4 and parts[:2] == ["api", "eleves"] and parts[3] in ("leaks", "revue", "fichiers", "periode",
                                                                                   "pseudos"):
                student = library.student(parts[2])
                if parts[3] == "fichiers":
                    return self._json(student.files_view())
                if parts[3] == "pseudos":
                    return self._json(student.pseudos_view())
                if parts[3] == "periode":
                    return self._json(student.period_view())
                return self._json(student.leaks_state(table_format=self._format()) if parts[3] == "leaks"
                                  else student.review_state(table_format=self._format()))
            if len(parts) == 5 and parts[:2] == ["api", "eleves"] and parts[3:] == ["revue", "panneau"]:
                return self._html(library.student(parts[2]).solver_panel(self._format()))
            if parts[:2] == ["api", "coups"] and len(parts) in (2, 3) or \
                    parts[:2] == ["api", "eleves"] and len(parts) in (4, 5) and parts[3] == "coups":
                owner = library if parts[1] == "coups" else library.student(parts[2])
                if len(parts) in (3, 5):  # une main, pour la rejouer
                    return self._json(owner.hand_fiche(parts[-1]))
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                return self._json(owner.spots_search(query))
            if len(parts) == 2 and parts[0] == "rejouer":  # le replayer d'une main jouée (?api= : son espace)
                return self._html((STATIC / "replayer.html").read_text(encoding="utf-8"))
            if parts[:2] == ["api", "rejouer"] and len(parts) == 3 or (
                    len(parts) == 5 and parts[:2] in (["api", "eleves"], ["api", "references"])
                    and parts[3] == "rejouer"):
                owner = (library if len(parts) == 3 else library.student(parts[2]) if parts[1] == "eleves"
                         else library.reference(parts[2]))
                return self._json(owner.replay_record(parts[-1]))
            if parts == ["api", "mains"] or (len(parts) == 4 and parts[:2] in (["api", "eleves"], ["api", "references"])
                                             and parts[3] == "mains"):
                owner = (library if len(parts) == 2 else library.student(parts[2]) if parts[1] == "eleves"
                         else library.reference(parts[2]))
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                return self._json(owner.hands_detail(query))
            if len(parts) == 3 and parts[0] == "p":
                return self._html(library.player_page(parts[1], parts[2]))
            if parts == ["etudes"]:
                return self._html(build_studies_page(embed=True, rset=self._study_set(),
                                                     compaction=library.compactions.view()))
            if parts == ["etudes", "plan"]:  # (« ?limites=micro » : le field de ce niveau)
                return self._html(library.plan_page(self._stakes()))
            if parts == ["api", "plan"]:
                return self._json(library.plan_state())
            if parts == ["api", "plan", "affiner"]:
                return self._json(library.plan_refine_state())
            if parts == ["api", "coach"]:
                return self._json(library.coach.status())
            if len(parts) == 3 and parts[:2] == ["api", "coach"]:
                view = library.coach.view(parts[2])
                return self._json(view) if view else self._error(404, "Conversation inconnue.")
            if len(parts) == 2 and parts[0] == "etudes":
                return self._html(build_studies_page(embed=True, section=parts[1], rset=self._study_set(),
                                                     compaction=library.compactions.view()))
            if parts == ["entraineur"]:
                return self._html((STATIC / "trainer.html").read_text(encoding="utf-8"))
            if parts == ["entraineur", "bluffcatch"]:  # payer ou coucher, sur tes mains jouées
                return self._html(library.catch_trainer_page())
            if parts == ["api", "entraineur"]:
                return self._json(trainer.overview())
            if parts == ["api", "sauvegarde"]:
                return self._json(library.backups.view())
            if parts == ["api", "revue"]:  # (« ?format=HU », « ring » : un format ; sans : tous)
                villain = parse_qs(urlsplit(self.path).query).get("adversaire", [None])[0]
                return self._json(library.review_state(villain, table_format=self._format()))
            if parts == ["api", "revue", "panneau"]:  # le panneau « Face au solveur » de l'onglet d'un format
                return self._html(library.solver_panel(self._format()))
            if parts[:2] == ["api", "spots"] and len(parts) == 3 and parts[2] in library.SPOT_GROUPS:
                # toutes les séries heads-up, ou 6-max (« ?ranges=hautes » : avec les ranges des hautes limites)
                return self._json(library.group_spot_sets(parts[2], rset=self._study_set()))
            if len(parts) == 3 and parts[:2] == ["api", "spots"]:
                return self._json(library.spot_set(parts[2], rset=self._study_set()))
            if len(parts) == 2 and parts[0] == "explorateur" and (parts[1] == "preflop" or library.find_hand(parts[1])
                                                                   or studyspots.is_ident(parts[1])):
                page = (STATIC / "explorer.html").read_text(encoding="utf-8")
                return self._html(page.replace("__HAND__", html.escape(parts[1], quote=True)))
        except NotFound:  # (une KeyError pendant un calcul est une erreur d'analyse, plus bas)
            return self._error(404, "Page introuvable.")
        except ConnectionError:
            self.close_connection = True
            return None
        except Exception:  # noqa: BLE001 — une erreur d'analyse ne doit pas tuer le serveur
            print(f"Erreur sur la page {unquote(self.path)} :", file=sys.stderr)
            traceback.print_exc()
            return self._error(500, "Erreur pendant l'analyse (détails dans le terminal).")
        return self._error(404, "Page introuvable.")

    def _file_action(self, owner: Library, action: str):
        """Retirer ou rétablir un historique importé (le tien, ou celui d'un élève)."""
        payload = self._small_json()
        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(file_id, int) or isinstance(file_id, bool):
            return self._error(400, "Requête invalide.")
        try:
            return self._json(owner.remove_file(file_id) if action == "retirer" else owner.restore_file(file_id))
        except KeyError:
            return self._error(404, "Historique inconnu.")

    def _pseudo_action(self, owner: Library, action: str):
        """Supprimer les mains d'un ou plusieurs pseudos ({"names": […]}), ou en rétablir un ({"name": …}) : les
        tiens, ou ceux d'un élève."""
        payload = self._small_json()
        if not isinstance(payload, dict):
            return self._error(400, "Requête invalide.")
        try:
            if action == "supprimer":
                return self._json(owner.remove_pseudos(payload.get("names")))
            return self._json(owner.restore_pseudo(payload.get("name")))
        except ValueError as exc:
            return self._error(400, str(exc))
        except KeyError:
            return self._error(404, "Pseudo inconnu : ses mains ne sont pas supprimées.")

    def _tree_action(self, library: Library, action: Optional[str], payload: dict):
        """L'onglet Arbre de l'explorateur : l'état (avec le nœud au bout de « path »), tes tailles, le retour à l'arbre
        d'origine, un verrou posé ou retiré."""
        hand, path = payload["hand"], payload.get("path")
        try:
            if action is None:
                return self._json(library.tree_state(hand, path if valid_path(path) else None))
            if action == "tailles" and isinstance(payload.get("plan"), dict):
                return self._json(library.set_tree_sizes(hand, payload["plan"]))
            if action == "origine":
                return self._json(library.reset_tree(hand))
            if action == "verrou" and valid_path(path) and isinstance(payload.get("edits"), list):
                return self._json(library.add_lock(hand, path, payload["edits"], payload.get("labels")))
            if action == "deverrouiller":
                index = payload.get("index")
                if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
                    return self._error(400, "Requête invalide.")
                return self._json(library.remove_lock(hand, index))
            return self._error(400, "Requête invalide.")
        except (UnknownPlayer, KeyError):
            return self._error(404, "Main introuvable.")
        except (ValueError, postflop.Unsupported) as exc:
            return self._error(400, str(exc))
        except postflop.SolverError as exc:
            return self._error(409, str(exc))

    def _default_ranges(self, library: Library, action: Optional[str], payload):
        """Une ligne de tes ranges par défaut (sans action), l'enregistrer, l'effacer ; l'option des spots d'étude."""
        if not isinstance(payload, dict):
            return self._error(400, "Requête invalide.")
        if action == "spots":
            if not isinstance(payload.get("for_spots"), bool):
                return self._error(400, "Requête invalide.")
            return self._json(library.set_ranges_for_spots(payload["for_spots"]))
        if action == "choix":  # tes ranges par défaut : selon la limite de la main, petites ou hautes limites
            try:
                return self._json(library.set_ranges_choice(payload.get("choix")))
            except ValueError as exc:
                return self._error(400, str(exc))
        if action == "reference":  # la référence des études et de l'explorateur préflop : petites ou hautes limites
            try:
                return self._json(library.set_ranges_reference(payload.get("reference")))
            except ValueError as exc:
                return self._error(400, str(exc))
        context = payload.get("context")
        rset = payload.get("jeu", "petites")
        if not isinstance(context, str) or len(context) > 200 or rset not in stake_charts.SETS:
            return self._error(400, "Requête invalide.")
        try:
            if action is None:
                return self._json(library.default_range_line(context, rset))
            if action == "enregistrer":
                return self._json(library.save_default_ranges(context, payload.get("ranges"), rset))
            if action == "effacer":
                return self._json(library.clear_default_ranges(context, rset))
        except NotFound:
            return self._error(404, "Ligne sans range de référence.")
        except ValueError as exc:
            return self._error(400, str(exc))
        return self._error(400, "Requête invalide.")

    def _period(self, owner: Library):
        """Choisir la période d'analyse (la tienne, ou celle d'un élève) : {"kind": "all" | "last" (n) | "days" (days)
        | "month" | "quarter" | "year" | "range" (from, to)}."""
        try:
            return self._json(owner.set_period(self._small_json()))
        except ValueError as exc:
            return self._error(400, str(exc))

    def _ring_preflop(self, payload: dict):
        """Le nœud de l'arbre préflop 6-max (ring_tree) au bout de la ligne, ou de celle qui mène au flop d'une
        famille 6-max (le début du déroulé d'un spot d'étude)."""
        line = payload.get("line")
        family = payload.get("family")
        if isinstance(family, str):
            if not studyspots.is_ring(family):
                return self._error(400, "Requête invalide.")
            line = ring_tree.line_for_family(family)
        rset = payload.get("set")
        if not (isinstance(line, list) and len(line) <= 16 and all(a in ring_tree.ACTIONS for a in line)
                and rset in (None, *stake_charts.SETS)):
            return self._error(400, "Requête invalide.")
        try:
            return self._json(ring_tree.node(line, ranges_set=rset))
        except ring_tree.NoCharts as exc:
            return self._error(404, str(exc))
        except ValueError:
            return self._error(404, "Ligne préflop hors des charts.")

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
        if parts == ["api", "coups"] or (len(parts) == 4 and parts[:2] == ["api", "eleves"] and parts[3] == "coups"):
            # tes mains face au solveur : la sélection du visualiseur (ses filtres) ou des mains (ids)
            payload = self._small_json()
            if not (isinstance(payload, dict) and payload.get("action") in ("etat", "resoudre", "arreter")
                    and isinstance(payload.get("query", {}), dict)):
                return self._error(400, "Requête invalide.")
            ids, limit = payload.get("ids"), payload.get("limit")
            if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
                return self._error(400, "Requête invalide.")
            query = {str(k): str(v) for k, v in payload.get("query", {}).items()}
            try:
                owner = library if len(parts) == 2 else library.student(parts[2])
            except UnknownPlayer:
                return self._error(404, "Élève inconnu.")
            return self._json(owner.spots_solve(query, payload["action"],
                                                limit if isinstance(limit, int) and limit > 0 else None, ids))
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
            if payload.get("table") == ring_tree.TABLE:  # un coup à plusieurs : l'arbre des charts 6-max
                return self._ring_preflop(payload)
            line = payload.get("line")
            if payload.get("family") in preflop_tree.FAMILY_LINES:
                line = list(preflop_tree.FAMILY_LINES[payload["family"]])
            rset = payload.get("set")  # le jeu de ranges (petites ou hautes limites) ; par défaut, la référence
            if not (isinstance(line, list) and len(line) <= 6 and all(a in preflop_tree.ACTIONS for a in line)
                    and rset in (None, *stake_charts.SETS)):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(preflop_tree.node(line, ranges_set=rset))
            except ValueError:
                return self._error(404, "Ligne préflop hors de la solution.")
        if parts == ["api", "explorateur", "flop"]:
            payload = self._small_json()
            board = payload.get("board") if isinstance(payload, dict) else None
            if not (isinstance(payload, dict) and isinstance(payload.get("family"), str)
                    and studyspots.known_family(payload["family"]) and trainer.valid_cards(board, 3)
                    and payload.get("set") in (None, *stake_charts.SETS)):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(studyspots.flop_options(payload["family"], board, payload.get("set")))
            except postflop.Unsupported as exc:  # spot 6-max sans tes charts
                return self._error(400, str(exc))
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
        if parts[:3] == ["api", "explorateur", "construction"] and len(parts) <= 4:  # ton arbre, avant la résolution
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str) or (
                    len(parts) == 4 and parts[3] != "enregistrer"):
                return self._error(400, "Requête invalide.")
            try:
                if len(parts) == 3:
                    return self._json(library.tree_builder(payload["hand"]))
                return self._json(library.save_tree_builder(payload["hand"], payload.get("choices")))
            except NotFound:
                return self._error(404, "Main introuvable.")
            except (ValueError, postflop.Unsupported) as exc:
                return self._error(400, str(exc))
        if parts[:3] == ["api", "explorateur", "arbre"] and len(parts) <= 4:  # tes tailles et tes verrous
            payload = self._small_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hand"), str):
                return self._error(400, "Requête invalide.")
            return self._tree_action(library, parts[3] if len(parts) == 4 else None, payload)
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
        if parts[:3] == ["api", "ranges", "defaut"] and len(parts) <= 4:  # Paramètres › Ranges préflop
            return self._default_ranges(library, parts[3] if len(parts) == 4 else None, self._small_json())
        if parts[:3] == ["api", "ranges", "limites"] and parts[3:] in ([], ["retirer"]):  # charts par limites
            payload = self._small_json(MAX_CHARTS_BODY)
            fmt = payload.get("format") if isinstance(payload, dict) else None
            band = payload.get("limites") if isinstance(payload, dict) else None
            charts = payload.get("charts") if isinstance(payload, dict) else None
            text = payload.get("texte") if isinstance(payload, dict) else None
            if not (isinstance(fmt, str) and isinstance(band, str)
                    and (parts[3:] or isinstance(charts, dict) or isinstance(text, str))):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.delete_stake_charts(fmt, band) if parts[3:] else
                                  library.save_stake_charts(fmt, band, charts if isinstance(charts, dict) else None,
                                                            text if isinstance(text, str) else None))
            except ValueError as exc:
                return self._error(400, str(exc))
        if parts == ["api", "ranges", "hand2note"]:  # charts 6-max de Hand2Note Guide, sur ta machine
            try:
                return self._json(library.load_hand2note())
            except (OSError, ValueError) as exc:
                return self._error(502, f"Téléchargement impossible : {exc}")
        if parts == ["api", "plan", "preparer"]:
            return self._json(library.plan_state(start=True))
        if parts == ["api", "plan", "arreter"]:
            return self._json(library.plan_cancel())
        if parts[:3] == ["api", "plan", "affiner"] and len(parts) <= 4 and parts[3:] in ([], ["arreter"]):
            payload = self._small_json()  # affiner un niveau du plan au solveur, ou l'arrêter
            if not (isinstance(payload, dict) and isinstance(payload.get("famille"), str)
                    and isinstance(payload.get("niveau"), str) and payload.get("cote", "attaque") in REFINE_SIDES
                    and payload.get("mode", "flop") in ("flop", "turn")):
                return self._error(400, "Requête invalide.")
            args = (payload["famille"], payload["niveau"], REFINE_SIDES[payload.get("cote", "attaque")],
                    payload.get("mode", "flop"))
            if parts[3:]:
                return self._json(library.plan_refine_cancel(*args))
            try:
                return self._json(library.plan_refine(*args))
            except ValueError as exc:
                return self._error(400, str(exc))
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
            choice = self._solve_choice(payload)
            if (villain is not None and not isinstance(villain, str)) or choice is None:
                return self._error(400, "Requête invalide.")
            try:
                if parts[2] == "arreter":
                    return self._json(library.review_cancel(villain, choice["format"]))
                return self._json(library.review_state(villain, start=True, order=choice["order"] or "pots",
                                                       limit=choice["limit"], table_format=choice["format"]))
            except UnknownPlayer:
                return self._error(404, "Adversaire inconnu.")
        if (parts[:2] == ["api", "spots"] and len(parts) == 4 and parts[2] in library.SPOT_GROUPS
                and parts[3] in ("resoudre", "arreter")):
            group, rset = parts[2], self._study_set()
            return self._json(library.group_spot_cancel(group, rset) if parts[3] == "arreter"
                              else library.group_spot_sets(group, start=True, rset=rset))
        if len(parts) == 4 and parts[:2] == ["api", "spots"] and parts[3] in ("resoudre", "arreter"):
            if not studyspots.is_series(parts[2]):
                return self._error(404, "Série inconnue.")
            if parts[3] == "arreter":
                return self._json(library.spot_cancel(parts[2], self._study_set()))
            return self._json(library.spot_set(parts[2], start=True, rset=self._study_set()))
        if len(parts) == 4 and parts[:2] == ["api", "resoudre"] and parts[3] == "arreter":
            job = library.solves.cancel(parts[2])
            return self._json(job) if job else self._error(404, "Résolution inconnue.")
        if parts == ["api", "import"]:
            files = self._import_files()
            return files if files is None else self._json(library.import_files(files))
        if parts == ["api", "import", "lancer"]:  # en arrière-plan : son avancement, GET /api/import/<id>
            files = self._import_files()
            return files if files is None else self._json(library.start_import(files))
        if len(parts) >= 3 and parts[:2] == ["api", "import"]:  # un import envoyé en plusieurs fois
            return self._import_step(library, parts[2:])
        if len(parts) == 3 and parts[:2] == ["api", "fichiers"] and parts[2] in ("retirer", "retablir"):
            return self._file_action(library, parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "pseudos"] and parts[2] in ("supprimer", "retablir"):
            return self._pseudo_action(library, parts[2])
        if parts == ["api", "periode"]:
            return self._period(library)
        if parts == ["api", "parametres"]:  # tes paramètres (pseudo, formats, coach, seuil du field)
            try:
                return self._json(library.set_settings(self._small_json()))
            except ValueError as exc:
                return self._error(400, str(exc))
        if parts in (["api", "alias"], ["api", "alias", "defaire"]):
            payload = self._small_json()
            alias = payload.get("alias") if isinstance(payload, dict) else None
            pseudos = payload.get("pseudos") if isinstance(payload, dict) else None
            if not isinstance(alias, str) or (len(parts) == 2 and not (isinstance(pseudos, list) and len(pseudos) <= 50)):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.set_alias(alias, pseudos) if len(parts) == 2 else library.remove_alias(alias))
            except ValueError as exc:
                return self._error(400, str(exc))
        if parts == ["api", "suivi"] or len(parts) == 4 and parts[:2] == ["api", "eleves"] and parts[3] == "suivi":
            follow = self._follow_choice(self._small_json())
            if follow is None:
                return self._error(400, "Requête invalide.")
            try:
                owner = library if parts == ["api", "suivi"] else library.student(parts[2])
                return self._json(owner.follow_leak(follow["leak"], follow["since"], follow["title"], self._format()))
            except UnknownPlayer:
                return self._error(404, "Élève ou format de table inconnu.")
        if len(parts) == 3 and parts[:2] == ["api", "leaks"] and parts[2] in ("lancer", "arreter"):
            choice = self._solve_choice(self._small_json())
            if choice is None:
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.leaks_cancel(self._format()) if parts[2] == "arreter"
                                  else library.leaks_state(start=True, table_format=self._format(),
                                                           order=choice["order"] or "leaks", limit=choice["limit"],
                                                           leak=choice["leak"]))
            except UnknownPlayer:
                return self._error(404, "Format de table inconnu.")
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
        if parts == ["api", "references"]:  # un nouveau joueur de référence : son nom et ses pseudos
            payload = self._small_json()
            if not isinstance(payload, dict):
                return self._error(400, "Requête invalide.")
            try:
                return self._json(library.create_reference(payload.get("name"), payload.get("pseudos")))
            except ValueError as exc:
                return self._error(400, str(exc))
        if len(parts) == 4 and parts[:2] == ["api", "references"] and parts[3] == "retirer":
            try:
                return self._json(library.remove_reference(parts[2]))
            except UnknownPlayer:
                return self._error(404, "Joueur de référence inconnu.")
        if len(parts) >= 4 and parts[:2] == ["api", "eleves"]:
            try:
                student = library.student(parts[2])
            except UnknownPlayer:
                return self._error(404, "Élève inconnu.")
            if parts[3:] == ["import"]:
                files = self._import_files()
                return files if files is None else self._json(student.import_files(files))
            if parts[3:] == ["import", "lancer"]:
                files = self._import_files()
                return files if files is None else self._json(student.start_import(files))
            if len(parts) >= 5 and parts[3] == "import":
                return self._import_step(student, parts[4:])
            if len(parts) == 5 and parts[3] == "fichiers" and parts[4] in ("retirer", "retablir"):
                return self._file_action(student, parts[4])
            if len(parts) == 5 and parts[3] == "pseudos" and parts[4] in ("supprimer", "retablir"):
                return self._pseudo_action(student, parts[4])
            if parts[3:] == ["formats"]:  # ce qu'il joue : heads-up et/ou tables à plusieurs (null : tout)
                payload = self._small_json()
                try:
                    return self._json(student.set_formats(payload.get("formats") if isinstance(payload, dict)
                                                          else []))
                except ValueError as exc:
                    return self._error(400, str(exc))
            if parts[3:] == ["pseudos", "choisir"]:  # ses pseudos, parmi ceux de ses historiques
                payload = self._small_json()
                try:
                    return self._json(student.set_chosen_pseudos(payload.get("names") if isinstance(payload, dict)
                                                                 else None))
                except ValueError as exc:
                    return self._error(400, str(exc))
            if parts[3:] == ["periode"]:
                return self._period(student)
            if len(parts) == 5 and parts[3] in ("leaks", "revue") and parts[4] in ("lancer", "arreter"):
                choice = self._solve_choice(self._small_json())
                if choice is None:
                    return self._error(400, "Requête invalide.")
                if parts[3] == "revue":
                    return self._json(student.review_cancel(table_format=choice["format"]) if parts[4] == "arreter"
                                      else student.review_state(start=True, order=choice["order"] or "pots",
                                                                limit=choice["limit"], table_format=choice["format"]))
                try:
                    return self._json(student.leaks_cancel(self._format()) if parts[4] == "arreter"
                                      else student.leaks_state(start=True, table_format=self._format(),
                                                               order=choice["order"] or "leaks",
                                                               limit=choice["limit"], leak=choice["leak"]))
                except UnknownPlayer:
                    return self._error(404, "Format de table inconnu.")
        return self._error(404, "Page introuvable.")

    @staticmethod
    def _follow_choice(payload) -> Optional[dict]:
        """Un leak à suivre, d'après la requête : {"leak", "since" (date ou None : ne plus le suivre), "title"}, ou None
        si elle est invalide."""
        if not isinstance(payload, dict):
            return None
        leak, since, title = payload.get("leak"), payload.get("depuis"), payload.get("titre") or ""
        if not (isinstance(leak, str) and 0 < len(leak) <= 200) or not isinstance(title, str):
            return None
        if since is not None:
            try:
                since = date.fromisoformat(since) if isinstance(since, str) else None
            except ValueError:
                since = None
            if since is None:
                return None
        return {"leak": leak, "since": since, "title": title[:200]}

    @staticmethod
    def _solve_choice(payload) -> Optional[dict]:
        """Les mains à passer au solveur, d'après la requête : {"order", "limit", "leak", "format"} (None : par défaut),
        ou None si la requête est invalide. « ordre » : leaks, pertes, lignes ou pots ; « limite » : un nombre de mains
        (0 : toutes) ; « leak » : la clé d'un leak ; « format » : HU, ring ou all (toutes tes mains)."""
        payload = payload if isinstance(payload, dict) else {}
        order, limit, leak = payload.get("ordre"), payload.get("limite"), payload.get("leak")
        table_format = payload.get("format")
        if order is not None and order not in ("leaks", "pertes", "lignes", "pots"):
            return None
        if table_format is not None and table_format not in ("HU", "ring", "all"):
            return None
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 5000):
            return None
        if leak is not None and not (isinstance(leak, str) and len(leak) <= 200):
            return None
        return {"order": order, "limit": limit or None, "leak": leak, "format": table_format}

    def _import_step(self, owner, rest: list[str]):
        """Un import envoyé en plusieurs fois (le tien, ou celui d'un élève) : « ouvrir », puis « <id>/lot » (des
        historiques en JSON, comme /lancer) ou « <id>/fichier?nom=… » (un historique ou une archive zip, ses octets
        tels quels), et « <id>/terminer » (les analyses se mettent à jour ; l'avancement : GET …/import/<id>)."""
        try:
            if rest == ["ouvrir"]:
                self._small_json()  # (le corps, vide, est lu)
                return self._json(owner.open_import())
            if len(rest) == 2 and rest[1] == "lot":
                files = self._import_files()
                return files if files is None else self._json(owner.import_part(rest[0], files))
            if len(rest) == 2 and rest[1] == "fichier":
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_FILE:
                    return self._error(413, "Historique trop volumineux (1 Go maximum par fichier).")
                name = (parse_qs(urlsplit(self.path).query).get("nom") or ["fichier"])[0]
                data = self.rfile.read(length)
                return self._json(owner.import_part(rest[0], [{"name": name, "data": data}]))
            if len(rest) == 2 and rest[1] == "terminer":
                self._small_json()
                return self._json(owner.close_import(rest[0]))
        except NotFound:
            return self._error(404, "Import inconnu ou déjà fini.")
        except Exception as exc:  # noqa: BLE001 — un envoi qui échoue : la page le dit, l'import reste ouvert
            traceback.print_exc()
            return self._error(500, f"Import impossible : {exc}")
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
    parser = argparse.ArgumentParser(prog="python -m analyzer app", description=f"Lance l'application {NAME}.")
    parser.add_argument("-d", "--dossier", default="hands", help="dossier des historiques (défaut : hands/)")
    parser.add_argument("--hero", help="ton pseudo (détecté automatiquement via le tag Hero)")
    parser.add_argument("--port", type=int, default=8765, help="port local (défaut : 8765)")
    parser.add_argument("--sans-navigateur", action="store_true", help="n'ouvre pas le navigateur")
    args = parser.parse_args(argv)

    print("Chargement des mains…", flush=True)
    outdated = db_hands.outdated(db.current())
    if outdated:  # le code de lecture a changé : une fois, les historiques gardés sont relus
        print(f"Lecture améliorée : {outdated} mains relues une fois depuis tes historiques (quelques minutes pour une "
              "grosse base)…", flush=True)
    library = Library(args.dossier, args.hero, warm_up=True)  # les pages se calculent en arrière-plan
    server = start(library, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"{len(library.hands) + len(library.ring)} mains, toi : {library.hero or 'inconnu'}")
    print(f"{NAME} est ouvert sur {url}  (Ctrl+C pour arrêter)", flush=True)
    if not args.sans_navigateur:
        webbrowser.open(url)
    library.compactions.start()  # les études pas encore compressées, en arrière-plan
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        library.solves.shutdown()
        library.backups.shutdown()
        library.compactions.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
