"""Bibliothèque de mains de l'application : chargement du dossier, import, analyses en cache."""
from __future__ import annotations

import base64
import binascii
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

from .. import bluffs, leaks, players, ring, students
from ..cli import detect_hero, slugify, unify_hero
from ..lines import villain_lines
from ..models import CALL, RAISE, Hand
from ..parsers import load_hands, parse_text, read_zip
from ..report import build_plan_page, build_report
from ..selfreport import build_self_report, opponent_results
from ..stats import analyze
from ..theory import coach, custom_ranges, handclass, postflop, review, ring_ranges, studyspots
from ..theory.page import build_preflop_page
from ..viewer import build_viewer
from .bluffs_page import build_bluffs_page
from .leaks_page import build_leaks_page
from .ring_page import build_ring_page
from .plan_page import build_coach_page
from .review_page import build_review_page
from .backups import Backups
from .coach_chat import Coach
from .solves import SolveQueue

PLAYER_PAGES = ("plan", "preflop", "rapport", "spots", "solveur", "bluffs")
SELF_PAGES = ("bilan", "preflop", "spots", "solveur", "bluffs", "leaks", "tables")
MAX_IMPORT_FILES = 5000  # fichiers (ou archives zip) par import


class UnknownPlayer(KeyError):
    pass


def _excluded_note(excluded: dict) -> str:
    if not excluded["hands"]:
        return ""
    return (f"{excluded['hands']} mains contre des récréatifs ({', '.join(excluded['players'])}) sont exclues : "
            "contre eux, l'exploitation prime sur la théorie.")


def _note(text: str) -> str:
    return f'<p class="note">{escape(text)} Le type de chaque adversaire se règle en haut de sa fiche.</p>'


def _kind_note(kind: dict) -> str:
    if kind["kind"] != "rec":
        return ""
    return ("Joueur classé récréatif : tes décisions contre lui ne sont pas comparées à la théorie ; "
            "ses fréquences ci-dessous servent à l'exploiter.")



def _formats(hands: list[Hand]) -> dict[str, int]:
    """Mains par format de table (HU, 3-max, 6-max…), dans cet ordre."""
    counts: dict[str, int] = {}
    for h in sorted(hands, key=lambda h: h.size):
        counts[h.table_format] = counts.get(h.table_format, 0) + 1
    return counts

class Library:
    """Les mains d'un dossier et les pages d'analyse, calculées à la demande puis gardées en cache."""

    def __init__(self, folder: Path | str, hero: Optional[str] = None, solves: Optional[SolveQueue] = None,
                 backups: Optional[Backups] = None, api: str = "/api", pages: str = "/moi"):
        """solves, backups : ceux de la bibliothèque principale, partagés par celles des élèves (une résolution à
        la fois) ; api, pages : préfixes des adresses de ses pages et de leurs actions."""
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hero_override = hero
        self.version = 0
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._cache: dict[tuple, object] = {}
        self.hands: list[Hand] = []
        self.ring: list[Hand] = []
        self.by_id: dict[str, Hand] = {}
        self.hero: Optional[str] = None
        self.known_ids: set[str] = set()
        self.api, self.pages = api, pages
        self.display_name: Optional[str] = None  # nom de l'élève (rapport)
        self._students: dict[str, Library] = {}
        self.solves = solves or SolveQueue()
        self.backups = backups or Backups()
        self.coach = Coach(self)
        if solves is None:
            self.solves.on_done.append(lambda job: self.backups.schedule())
        self.reload()

    # --- chargement -------------------------------------------------------------
    def reload(self) -> None:
        hands = load_hands([self.folder])
        hero = self.hero_override or detect_hero(hands)
        unify_hero(hands, hero)
        heads_up = [h for h in hands if hero and hero in h.seats and len(h.seats) == 2 and h.button and h.bb]
        ring = [h for h in hands if hero and hero in h.seats and len(h.seats) > 2 and h.button and h.bb]
        with self._lock:
            self.known_ids = {f"{h.site}:{h.hand_id}" for h in hands}
            self.hands = heads_up
            self.ring = ring  # tes mains aux tables à plusieurs (3-max, 6-max)
            self.by_id = {h.hand_id: h for h in heads_up + ring}  # le solveur résout aussi leurs pots à deux
            self.hero = hero
            self.version += 1
            self._cache.clear()
            self._key_locks.clear()

    def opponents(self) -> list[dict]:
        return self._cached(("opponents",), lambda: opponent_results(self.hands, self.hero) if self.hero else [])

    def hands_against(self, name: str) -> list[Hand]:
        match = [h for h in self.hands if name in h.seats]
        if not match:
            raise UnknownPlayer(name)
        return match

    # --- type des adversaires (régulier / récréatif) ---------------------------------
    def all_stats(self) -> dict:
        return self._cached(("stats",), lambda: analyze(self.hands))

    def kinds(self) -> dict[str, dict]:
        """Type retenu pour chaque adversaire (choix enregistré, sinon suggestion d'après ses stats)."""
        return players.classify([o["name"] for o in self.opponents()], self.all_stats())

    def _kinds_key(self) -> tuple:
        return tuple(sorted((name, info["kind"]) for name, info in self.kinds().items()))

    def regular_hands(self) -> tuple[list[Hand], dict]:
        """Mains contre les réguliers (celles qu'on compare à la théorie) et ce qui est laissé de côté."""
        recs = {name for name, info in self.kinds().items() if info["kind"] == "rec"}
        hands = [h for h in self.hands if h.opponent_of(self.hero) not in recs]
        return hands, {"hands": len(self.hands) - len(hands), "players": sorted(recs)}

    def set_kind(self, player: str, kind: Optional[str]) -> dict:
        if not any(h for h in self.hands if player in h.seats) and not any(
                player in {o["name"] for o in self.student(s["id"]).opponents()} for s in students.all_students()):
            raise UnknownPlayer(player)  # ni ton adversaire, ni celui d'un élève
        players.set_kind(player, kind)
        return self.summary()

    def summary(self) -> dict:
        opponents = self.opponents()
        kinds = self.kinds()
        return {
            "hero": self.hero,
            "folder": str(self.folder),
            "hands": len(self.hands),
            "first": self.hands[0].date.strftime("%d/%m/%Y") if self.hands else None,
            "last": self.hands[-1].date.strftime("%d/%m/%Y") if self.hands else None,
            "net_bb": round(sum(o["net_bb"] for o in opponents), 1),
            "opponents": [
                {"name": o["name"], "hands": o["hands"], "net_bb": round(o["net_bb"], 1),
                 "bb100": round(o["bb100"], 1), "last": o["last"].strftime("%d/%m/%Y"), **kinds[o["name"]]}
                for o in opponents
            ],
        }

    # --- pages ------------------------------------------------------------------
    def _cached(self, key: tuple, build):
        """Calcule une seule fois par version, même si deux requêtes arrivent en même temps."""
        full_key = (self.version,) + key
        if full_key in self._cache:
            return self._cache[full_key]
        with self._lock:
            lock = self._key_locks.setdefault(full_key, threading.Lock())
        with lock:
            if full_key not in self._cache:
                self._cache[full_key] = build()
            return self._cache[full_key]

    def _analysis(self, player: str):
        def build():
            hands = self.hands_against(player)
            return hands, analyze(hands), villain_lines(hands, player, self.hero)
        return self._cached(("analysis", player), build)

    def player_page(self, player: str, page: str) -> str:
        if page not in PLAYER_PAGES:
            raise KeyError(page)
        hands = self.hands_against(player)  # 404 si le joueur est inconnu
        kind = self.kinds()[player]
        if page == "solveur":  # change au fil des analyses : jamais en cache
            return build_review_page(hands, self.hero, player, kind=kind)

        def build():
            hands, stats, lines = self._analysis(player)
            if page == "plan":
                return build_plan_page(hands, stats, self.hero, player, embed=True, lines=lines)
            if page == "preflop":
                return build_preflop_page(hands, self.hero, player, stats, embed=True, spots_href="spots",
                                          compare_hero=kind["kind"] != "rec", note=_kind_note(kind))
            if page == "rapport":
                return build_report(hands, stats, self.hero, player, spots_href="spots", embed=True, lines=lines)
            if page == "bluffs":
                return build_bluffs_page(bluffs.analyze(hands, [player], self.hero), f"de {player}",
                                         note=_note(f"{player} : {players.describe(kind)}."))
            return build_viewer(hands, self.hero, player, embed=True, solver=True)
        return self._cached(("player", player, page) + ((kind["kind"],) if page == "preflop" else ()), build)

    def self_page(self, page: str) -> str:
        if page not in SELF_PAGES:
            raise KeyError(page)
        if page == "tables":  # tables à 3 joueurs et plus : pas besoin de mains heads-up
            return self._cached(("self", "tables"), lambda: build_ring_page(
                ring.analyze(self.ring, self.hero or ""), self.hero or "", spots=self.ring_spots(),
                ranges=ring_ranges.available()))
        if not self.hands:
            raise UnknownPlayer("moi")
        regular, excluded = self.regular_hands()
        if page == "solveur":
            return build_review_page(regular, self.hero, excluded=excluded, api=f"{self.api}/revue")
        if page == "leaks":  # change au fil des analyses : le rapport a son propre cache
            return self.leaks_page()

        def build():
            if page == "bilan":
                return build_self_report(self.hands, self.all_stats(), self.hero, embed=True, spots_href="",
                                         kinds=self.kinds())
            if page == "preflop":
                return build_preflop_page(regular, self.hero, embed=True, spots_href="spots",
                                          note=_excluded_note(excluded))
            if page == "bluffs":
                return self._population_bluffs()
            return build_viewer(self.hands, self.hero, None, embed=True, solver=True)
        kinds = self._kinds_key() if page in ("bilan", "preflop", "bluffs") else ()
        return self._cached(("self", page) + kinds, build)

    def _population_bluffs(self) -> str:
        """Les bluffs des réguliers, ensemble puis un par un."""
        regs = [n for n, info in self.kinds().items() if info["kind"] == "reg"]
        recs = sorted(n for n, info in self.kinds().items() if info["kind"] == "rec")
        rows = []
        for name in regs:
            report = bluffs.analyze(self.hands_against(name), [name], self.hero)
            top = next((p for p in report.patterns if p.confidence == "solide"), None)
            rows.append({"name": name, "hands": report.hands, "shown": len(report.shown),
                         "river": sum(1 for s in report.shown if s.street == "river" and s.bluff),
                         "top": top.title if top else None})
        note = ("Les réguliers ensemble : " + ", ".join(regs) + "." if regs else "Aucun adversaire classé régulier.")
        if recs:
            note += f" Les récréatifs ({', '.join(recs)}) ont chacun leur page, dans leur fiche."
        return build_bluffs_page(bluffs.analyze(self.hands, regs, self.hero), "des réguliers", note=_note(note),
                                 players=sorted(rows, key=lambda r: -r["hands"]))

    # --- élèves -----------------------------------------------------------------------
    def student(self, ident: str) -> "Library":
        """La bibliothèque d'un élève (chargée au premier accès), qui partage la file du solveur."""
        meta = students.get(ident)
        if meta is None:
            raise UnknownPlayer(ident)
        with self._lock:
            lib = self._students.get(ident)
        if lib is None:
            lib = Library(students.folder(ident), meta.get("pseudo"), solves=self.solves, backups=self.backups,
                          api=f"/api/eleves/{ident}", pages=f"/eleve/{ident}")
            lib.display_name = meta["name"]
            with self._lock:
                lib = self._students.setdefault(ident, lib)
        return lib

    def students_summary(self) -> list[dict]:
        out = []
        for meta in students.all_students():
            lib = self.student(meta["id"])
            out.append(dict(lib.summary(), id=meta["id"], name=meta["name"], pseudo=meta.get("pseudo")))
        return out

    def create_student(self, name: str, pseudo: Optional[str] = None) -> dict:
        meta = students.create(name, pseudo)
        return dict(self.student(meta["id"]).summary(), id=meta["id"], name=meta["name"], pseudo=meta.get("pseudo"))

    def load_hand2note(self) -> dict:
        """Télécharge les charts 6-max de Hand2Note Guide (usage personnel) : les pots à deux au flop s'ouvrent au
        solveur."""
        ring_ranges.install_hand2note(log=lambda message: None)
        with self._lock:
            self._cache.clear()
        return {"ranges": ring_ranges.available()}

    def ring_spots(self) -> list[dict]:
        """Tes coups des tables à plusieurs où il ne reste que deux joueurs au flop, les plus gros pots d'abord :
        de quoi les ouvrir au solveur (ou pourquoi ils ne se résolvent pas encore)."""
        rows = []
        for h in self.ring:
            pair = postflop.flop_pair(h)
            if not pair or self.hero not in pair:
                continue
            villain = pair[1] if pair[0] == self.hero else pair[0]
            try:
                status, pot_type = None, postflop.build_spot(h, self.hero).pot_type
            except postflop.Unsupported as exc:
                status, pot_type = str(exc), None
            pre = [a for a in h.actions if a.street == "preflop" and a.kind in (RAISE, CALL)]
            rows.append({"id": h.hand_id, "date": h.date, "format": h.table_format, "hero": h.position(self.hero),
                         "villain": h.position(villain), "line": ring_ranges.describe([(h.position(a.player), a.kind)
                                                                                      for a in pre]),
                         "pot_type": pot_type, "cards": h.hole_cards.get(self.hero, []), "board": h.board[:3],
                         "pot_bb": round(sum(a.amount for a in h.actions if a.street == "preflop") / h.bb, 1),
                         "total_bb": round(h.total_pot / h.bb, 1), "net_bb": round(h.net(self.hero) / h.bb, 1),
                         "status": status})
        return sorted(rows, key=lambda r: -r["total_bb"])

    def find_hand(self, hand_id: str) -> Optional[tuple[Hand, str]]:
        """Une main et son joueur : parmi les tiennes, puis celles des élèves."""
        hand = self.by_id.get(hand_id)
        if hand is not None and self.hero:
            return hand, self.hero
        for meta in students.all_students():
            lib = self.student(meta["id"])
            if hand_id in lib.by_id and lib.hero:
                return lib.by_id[hand_id], lib.hero
        return None

    # --- leakfinding ----------------------------------------------------------------------
    def leaks_report(self) -> "leaks.Report":
        """Le rapport, recalculé quand des mains ou des analyses du solveur s'ajoutent."""
        digests = review.review_dir()
        count = sum(1 for _ in digests.glob("*.json")) if digests.is_dir() else 0
        return self._cached(("leaks", count) + self._kinds_key(),
                            lambda: leaks.build(self.hands, self.hero, self.kinds()))

    def leaks_page(self, standalone: bool = False) -> str:
        if not self.hands:
            raise UnknownPlayer("aucune main")
        return build_leaks_page(self.leaks_report(), api=f"{self.api}/leaks", pages=self.pages,
                                embed=not standalone, standalone=standalone, name=self.display_name,
                                opponents=self.summary()["opponents"])

    def leaks_state(self, start: bool = False) -> dict:
        """Les mains choisies contre les réguliers : analysées, à analyser, en cours ; start=True les met en file."""
        report = self.leaks_report()
        picks = report.picks.get("reg", [])
        ready = postflop.status()["ready"]
        busy, current = 0, None
        for spot in leaks.selection_spots(report):
            view = self.solves.lookup(spot)
            if start and ready and view["state"] not in ("waiting", "running", "done"):
                view = self.solves.analyze(spot, review.save_digest)
            if view["state"] in ("waiting", "running"):
                busy += 1
                if view["state"] == "running":
                    current = {"hand": spot.hand.hand_id, "progress": view.get("progress"),
                               "max_iterations": view.get("max_iterations")}
        done = sum(1 for p in picks if p.digest is not None)
        return {"total": sum(1 for p in picks if p.digest is not None or p.spot is not None), "done": done,
                "busy": busy, "current": current, "ready": ready}

    def leaks_cancel(self) -> dict:
        for spot in leaks.selection_spots(self.leaks_report()):
            view = self.solves.lookup(spot)
            if view["state"] in ("waiting", "running"):
                self.solves.cancel(view["job"])
        return self.leaks_state()

    # --- résolution postflop ----------------------------------------------------
    def _spot(self, hand_id: str):
        """Une main jouée (son numéro), ou un spot d'étude (« spot:srp:KsKd4c ») ; avec tes ranges ajustées."""
        if hand_id.startswith("spot:"):
            spot = studyspots.parse_ident(hand_id, custom=True)
            if spot is None:
                raise UnknownPlayer(hand_id)
            return spot
        found = self.find_hand(hand_id)
        if found is None:
            raise UnknownPlayer(hand_id)
        return postflop.build_spot(*found)

    def solve(self, hand_id: str, start: bool = False, force: bool = False) -> dict:
        """État de la résolution GTOpen d'une main (ou d'un spot d'étude) ; start=True la lance si besoin.

        force=True relance une main déjà résolue pour rouvrir une session navigable.
        """
        try:
            spot = self._spot(hand_id)
        except postflop.Unsupported as exc:
            return {"hand": hand_id, "state": "unsupported", "message": str(exc)}
        view = self.solves.lookup(spot)
        reopen = force and view["state"] == "done" and not view["live"]
        if start and (view["state"] in ("absent", "error", "cancelled") or reopen):
            solver = postflop.status()
            if not solver["ready"]:
                return {"hand": hand_id, "state": "unavailable", "message": solver["message"],
                        "install": solver["install"]}
            board = "".join(getattr(spot, "board", []))
            if (isinstance(spot, studyspots.StudySpot) and not spot.plan and not spot.adjusted
                    and board in studyspots.flop_set(spot.family) and not postflop.study_path(spot.request()).is_file()):
                # flop de la série sans tailles choisies : le choix d'abord, comme pour la série entière
                family = spot.family
                view = self.solves.choose_and_solve(
                    spot.ident, lambda job: self._choose(family, board, job),
                    lambda: studyspots.StudySpot(family, studyspots.cards_of(board)))
            else:
                view = self.solves.start(spot, force=reopen)
        if view["state"] == "absent":
            solver = postflop.status()
            view["solver"] = {k: solver[k] for k in ("ready", "message", "install")}
        view["adjusted"] = spot.adjusted
        return view

    # --- ranges préflop ajustées (panneau Ranges de l'explorateur) ------------------------
    @staticmethod
    def _range_players(spot) -> list[tuple[str, str, Optional[str]]]:
        """(clé de la range dans le spot, position, H/V) du joueur hors de position, puis de celui en position."""
        if isinstance(spot, studyspots.StudySpot):
            return [(spot.oop, spot.oop, None), (spot.ip, spot.ip, None)]
        return [(p, spot.hand.position(p), "H" if p == spot.hero else "V") for p in (spot.oop, spot.ip)]

    def ranges_state(self, hand_id: str) -> dict:
        """Les ranges préflop du coup : celles de la référence, celles en jeu, et d'où elles viennent."""
        try:
            spot = self._spot(hand_id)
        except postflop.Unsupported as exc:
            return {"hand": hand_id, "supported": False, "message": str(exc)}
        study = isinstance(spot, studyspots.StudySpot)
        data = custom_ranges.load()
        by_hand = data["coups"].get(spot.ident, {})
        by_line = {} if study else data["lignes"].get(spot.context, {})
        table_format, key = spot.context.split("|", 1)
        steps = [tuple(step.split(":", 1)) for step in key.split()]
        if table_format == "HU":
            reference = "solution préflop heads-up"
        else:
            source = (ring_ranges.solution(table_format) or {}).get("source") or ""
            reference = (source.split(" — ")[0] or "ta solution") + f" ({table_format})"
        players = []
        for name, pos, role in self._range_players(spot):
            source = "coup" if pos in by_hand else "ligne" if pos in by_line else "reference"
            current = (ring_ranges.parse_range(by_hand.get(pos) or by_line[pos]) if source != "reference"
                       else spot.reference[name])
            players.append({"position": pos, "role": role, "source": source, "reference": spot.reference[name],
                            "line": ring_ranges.parse_range(by_line[pos]) if pos in by_line else None,
                            "current": current})
        return {"hand": hand_id, "supported": True, "spot": study, "format": table_format,
                "line": ring_ranges.describe(steps), "can_line": not study, "adjusted": spot.adjusted,
                "reference": reference, "players": players, "order": custom_ranges.hand_order()}

    def save_ranges(self, hand_id: str, scope: str, ranges: dict) -> dict:
        """Enregistre tes ranges pour ce coup (scope « coup ») ou pour sa ligne (« ligne ») ; seules celles qui
        diffèrent de ce qui s'appliquerait sans ce réglage sont gardées. ValueError si la demande ne va pas."""
        spot = self._spot(hand_id)
        study = isinstance(spot, studyspots.StudySpot)
        if scope not in custom_ranges.SCOPES or (study and scope != "coup"):
            raise ValueError("Portée inconnue.")
        names = {pos: name for name, pos, _ in self._range_players(spot)}
        if not ranges or set(ranges) - set(names) or not all(isinstance(t, str) for t in ranges.values()):
            raise ValueError("Positions inconnues pour ce coup.")
        weights = {pos: custom_ranges.check(text) for pos, text in ranges.items()}
        data = custom_ranges.load()
        by_line = data["lignes"].get(spot.context, {}) if scope == "coup" and not study else {}
        keep = {}
        for pos, w in weights.items():
            base = ring_ranges.parse_range(by_line[pos]) if pos in by_line else spot.reference[names[pos]]
            if not custom_ranges.same(w, base):
                keep[pos] = postflop.range_text(w)
        key = spot.ident if scope == "coup" else spot.context
        if keep:
            custom_ranges.save(scope, key, keep)
        else:
            custom_ranges.clear(scope, key)
        if scope == "ligne":  # la range de la ligne s'applique à ce coup : son réglage à lui s'efface
            custom_ranges.clear("coup", spot.ident)
        with self._lock:
            self._cache.clear()
        return self.ranges_state(hand_id)

    def clear_ranges(self, hand_id: str, scope: str) -> dict:
        """Revient à la référence : efface tes ranges de ce coup, ou celles de sa ligne."""
        spot = self._spot(hand_id)
        if scope not in custom_ranges.SCOPES:
            raise ValueError("Portée inconnue.")
        custom_ranges.clear(scope, spot.ident if scope == "coup" else spot.context)
        with self._lock:
            self._cache.clear()
        return self.ranges_state(hand_id)

    def explorer_state(self, hand_id: str) -> dict:
        """Ce que l'explorateur affiche d'une main : le coup, et l'état de sa résolution."""
        view = self.solve(hand_id)
        view["categories"] = handclass.labels()
        if hand_id.startswith("spot:"):
            family = hand_id.split(":")[1]
            info = studyspots.family_info(family)
            pair = info.get("pair", "BTN contre BB")
            if view["state"] == "unsupported":  # spot 6-max sans tes charts
                view["meta"] = {"spot": True, "hand": hand_id, "board": studyspots.cards_of(hand_id.split(":")[2]),
                                "pot_type": info["name"], "family_label": info["label"], "pair": pair,
                                "hero_cards": [], "villain_cards": [], "hero_position": None}
                return view
            spot = self._spot(hand_id)
            view["meta"] = {"spot": True, "hand": hand_id, "board": spot.board, "texture": spot.texture,
                            "pot_type": spot.name, "family_label": spot.label, "pot": spot.pot_bb,
                            "stack": spot.stack_bb, "sizes": spot.menu_text(), "hero": None, "villain": None,
                            "hero_cards": [], "positions": [spot.oop, spot.ip], "pair": pair,
                            "format": info.get("group", "HU"),
                            "villain_cards": [], "hero_position": None}
            return view
        hand, hero = self.find_hand(hand_id)
        bb = hand.bb
        pair = postflop.flop_pair(hand)  # (hors de position, en position) au flop
        villain = (pair[1] if pair[0] == hero else pair[0]) if pair and hero in pair else hand.opponent_of(hero)
        positions = [hand.position(p) for p in pair] if pair else ["BB", "BTN"]
        try:
            spot = self._spot(hand_id)
        except postflop.Unsupported:
            spot = None
        sizes = spot.sizes_info() if spot is not None else None
        if sizes and sizes["choose"]:
            sizes["choose_time"] = studyspots.CHOOSE_TIME[sizes["choose"]]
        view["meta"] = {
            "sizes": sizes,
            "hand": hand_id, "date": hand.date.strftime("%d/%m/%Y %H:%M"), "hero": hero, "villain": villain,
            "board": hand.board, "hero_cards": hand.hole_cards.get(hero, []),
            "villain_cards": hand.hole_cards.get(villain, []),
            "hero_position": hand.position(hero), "hero_oop": bool(pair) and pair[0] == hero,
            "positions": positions, "table_format": hand.table_format,
            "net": round(hand.net(hero) / bb, 2),
            "stack": round(min(hand.seats[p].stack for p in (pair or hand.seats)) / bb, 2),
            "preflop": postflop.preflop_steps(hand, hero),
        }
        return view

    def explorer_node(self, hand_id: str, path: list) -> dict:
        spot = self._spot(hand_id)
        reply = self.solves.node(spot, path)
        handclass.annotate(reply["node"])  # catégories des mains, pour les filtres
        if isinstance(spot, postflop.PostflopSpot):
            postflop.mark_played_sizes(reply["node"], spot)  # la taille jouée ajoutée à l'arbre
        return reply

    def choose_hand_sizes(self, hand_id: str) -> dict:
        """Choisit les tailles théoriques du flop de ce coup (comme pour un spot d'étude, long), puis résout le coup
        avec elles ; ensuite, le spot d'étude de ce flop se résout à son tour et rejoint sa série dans les études."""
        spot = self._spot(hand_id)
        if not isinstance(spot, postflop.PostflopSpot) or not spot.size_target:
            raise ValueError("Pas de tailles théoriques pour ce type de pot.")
        if not spot.sizes_info()["choose"]:
            return self.solve(hand_id, start=True)  # déjà choisies
        solver = postflop.status()
        if not solver["ready"]:
            return {"hand": hand_id, "state": "unavailable", "message": solver["message"], "install": solver["install"]}
        family = spot.size_target
        if family in studyspots.RING_FAMILIES:
            studyspots.ring_spot_ranges(family)  # tes charts 6-max (Unsupported sinon)
        board = "".join(studyspots.normalize_board(family, spot.board))
        study = lambda: studyspots.StudySpot(family, studyspots.cards_of(board))  # noqa: E731
        return self.solves.choose_and_solve(
            f"spot:{family}:{board}", lambda job: self._choose(family, board, job), lambda: self._spot(hand_id),
            aliases=(hand_id,), keep_live=True, after=lambda: self.solves.start(study(), keep_live=False))

    def spot_set(self, family: str = "srp", start: bool = False) -> dict:
        """Série de spots d'étude : état de chaque flop ; start=True met en file ceux qui manquent.

        Un flop de la série sans tailles choisies passe d'abord par le choix des tailles (long)."""
        if not studyspots.known_family(family):
            raise KeyError(family)
        try:
            if family in studyspots.RING_FAMILIES:
                studyspots.ring_spot_ranges(family)  # tes charts 6-max
        except postflop.Unsupported as exc:
            return {"family": family, "label": studyspots.family_info(family)["label"], "ready": False,
                    "error": str(exc), "total": 0, "done": 0, "busy": 0, "rows": []}
        studies = studyspots.spot_studies((family,))
        rows = []
        ready = postflop.status()["ready"]
        series = set(studyspots.flop_set(family))
        for board in studyspots.family_boards(family):
            spot = studyspots.StudySpot(family, studyspots.cards_of(board))
            meta = studies.get(spot.ident)
            row = {"id": spot.ident, "board": spot.board, "texture": spot.texture, "series": board in series,
                   "done": bool(meta and meta.get("summary")), "key": meta["key"] if meta else None,
                   "sizes": bool(spot.plan)}
            if not row["done"]:
                view = self.solves.lookup(spot)
                if start and ready and view["state"] not in ("waiting", "running"):
                    if board in series and not spot.plan:
                        view = self.solves.choose_and_solve(
                            spot.ident, lambda job, b=board: self._choose(family, b, job),
                            lambda b=board: studyspots.StudySpot(family, studyspots.cards_of(b)))
                    else:
                        view = self.solves.start(spot, force=view["state"] == "done", keep_live=False)
                row.update(state=view["state"], progress=view.get("progress"), job=view.get("job"),
                           max_iterations=view.get("max_iterations"), mode=view.get("mode"))
            rows.append(row)
        return {"family": family, "label": studyspots.family_info(family)["label"], "ready": ready,
                "total": len(rows), "done": sum(r["done"] for r in rows),
                "busy": sum(r.get("state") in ("waiting", "running") for r in rows), "rows": rows}

    @staticmethod
    def _choose(family: str, board: str, job) -> None:
        """Choix des tailles d'un flop dans une tâche de la file (étape en cours dans job.progress)."""
        def log(message: str) -> None:
            job.progress["stage"] = message.strip()

        def started(proc) -> None:
            job.process = proc
            if job.cancelled:  # arrêt demandé pendant le lancement
                proc.terminate()
        studyspots.choose_sizes(family, board, log, on_start=started, stopped=lambda: job.cancelled)

    def spot_cancel(self, family: str = "srp") -> dict:
        """Arrête les résolutions en lot de cette série (en attente ou en cours)."""
        for row in self.spot_set(family)["rows"]:
            if row.get("state") in ("waiting", "running"):
                self.solves.cancel(row["job"])
        return self.spot_set(family)

    # --- plan de jeu suggéré -----------------------------------------------------------
    def plan_state(self, start: bool = False) -> dict:
        """Études sans plan de jeu lu ; start=True les met en file (une étude ouverte à la fois)."""
        todo = coach.missing()
        ready = postflop.status()["ready"]
        busy, current = 0, None
        for ident in todo:
            spot = studyspots.parse_ident(ident)
            if spot is None:
                continue
            view = self.solves.plan_view(spot)
            if start and ready and (view is None or view["state"] not in ("waiting", "running")):
                view = self.solves.prepare_plan(spot, coach.extract_and_save)
            if view and view["state"] in ("waiting", "running"):
                busy += 1
                if view["state"] == "running":
                    current = {"spot": ident, "stage": view["progress"].get("stage")}
        return {"ready": ready, "missing": len(todo), "busy": busy, "current": current}

    def plan_cancel(self) -> dict:
        for ident in coach.missing():
            spot = studyspots.parse_ident(ident)
            view = self.solves.plan_view(spot) if spot else None
            if view and view["state"] in ("waiting", "running"):
                self.solves.cancel(view["job"])
        return self.plan_state()

    def plan_page(self) -> str:
        return build_coach_page(self.plan_state(), villains=tuple(o["name"] for o in self.opponents()))

    # --- analyse des mains jouées ---------------------------------------------------
    def review_state(self, villain: Optional[str] = None, start: bool = False) -> dict:
        """Mains allées au flop (toutes, ou face à cet adversaire) : analysées, à analyser, en cours ;
        start=True met en file celles qui restent (les plus gros pots d'abord)."""
        hands = self.hands_against(villain) if villain else self.regular_hands()[0]
        done, todo = review.collect(hands, self.hero)
        ready = postflop.status()["ready"]
        busy, current = 0, None
        for spot in todo:
            view = self.solves.lookup(spot)
            if start and ready and view["state"] not in ("waiting", "running"):
                view = self.solves.analyze(spot, review.save_digest)
            if view["state"] in ("waiting", "running"):
                busy += 1
                if view["state"] == "running":
                    current = {"hand": spot.hand.hand_id, "progress": view.get("progress"),
                               "max_iterations": view.get("max_iterations"), "job": view.get("job")}
        return {"total": len(done) + len(todo), "done": len(done), "busy": busy, "current": current,
                "ready": ready}

    def review_cancel(self, villain: Optional[str] = None) -> dict:
        hands = self.hands_against(villain) if villain else self.regular_hands()[0]
        _, todo = review.collect(hands, self.hero)
        for spot in todo:
            view = self.solves.lookup(spot)
            if view["state"] in ("waiting", "running"):
                self.solves.cancel(view["job"])
        return self.review_state(villain)

    # --- import -----------------------------------------------------------------
    def import_files(self, files: list[dict]) -> dict:
        """Enregistre dans le dossier les fichiers reconnus qui apportent de nouvelles mains.

        Chaque fichier : {"name", "content"} (texte), ou {"name", "zip"} (archive zip en base64, dossiers compris :
        chacun de ses historiques s'importe comme un fichier)."""
        results: list[dict] = []
        known = set(self.known_ids)
        added = 0
        for item in files[:MAX_IMPORT_FILES]:
            name = str(item.get("name") or "fichier")[:200]
            if isinstance(item.get("zip"), str):
                added += self._import_zip(name, item["zip"], known, results)
            else:
                added += self._import_one(name, item.get("content"), known, results)
        if added:
            self.reload()
        return {"files": results, "added": added, "state": self.summary()}

    def _import_zip(self, name: str, data: str, known: set, results: list[dict]) -> int:
        try:
            content = read_zip(base64.b64decode(data, validate=True))
        except binascii.Error:
            content, reason = None, "archive zip illisible (envoi abîmé)"
        except ValueError as exc:  # illisible, trop grosse
            content, reason = None, str(exc)
        if content is None:
            results.append({"name": name, "status": reason, "hands": 0, "new": 0})
            return 0
        start = len(results)
        added = sum(self._import_one(f"{name} › {path}"[:300], text, known, results) for path, text in content.files)
        results.extend({"name": f"{name} › {path}"[:300], "status": "illisible (chiffré ou abîmé)", "hands": 0, "new": 0}
                       for path in content.unreadable)
        parts = [f"{len(content.files)} historique(s)"]
        if content.ignored:
            parts.append(f"{content.ignored} autre(s) fichier(s) laissé(s) de côté")
        results.append({"name": name, "status": "archive : " + ", ".join(parts), "archive": True,
                        "hands": sum(r["hands"] for r in results[start:]), "new": added})
        return added

    def _import_one(self, name: str, content, known: set, results: list[dict]) -> int:
        if not isinstance(content, str) or not content.strip():
            results.append({"name": name, "status": "vide", "hands": 0, "new": 0})
            return 0
        try:
            hands = parse_text(content)
        except ValueError:
            results.append({"name": name, "status": "format non reconnu", "hands": 0, "new": 0})
            return 0
        new = [h for h in hands if f"{h.site}:{h.hand_id}" not in known]
        detail = {"sites": sorted({h.site for h in hands}), "formats": _formats(hands)}
        if not new:
            results.append(dict(detail, name=name, status="déjà importé", hands=len(hands), new=0))
            return 0
        self._save(name.rsplit(" › ", 1)[-1], content)  # nom du fichier dans l'archive
        known |= {f"{h.site}:{h.hand_id}" for h in new}
        results.append(dict(detail, name=name, status="importé", hands=len(hands), new=len(new)))
        return len(new)

    def _save(self, original_name: str, content: str) -> Path:
        # Le nom d'origine ne sert qu'à lire le fichier plus tard : jamais utilisé comme chemin.
        stem = slugify(Path(original_name).stem)[:40]
        base = f"import-{datetime.now():%Y%m%d-%H%M%S}-{stem}"
        path = self.folder / f"{base}.txt"
        n = 2
        while path.exists():
            path = self.folder / f"{base}-{n}.txt"
            n += 1
        path.write_text(content, encoding="utf-8")
        return path
