"""Bibliothèque de mains de l'application : chargement du dossier, import, analyses en cache."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..cli import detect_hero, slugify
from ..lines import villain_lines
from ..models import Hand
from ..parsers import load_hands, parse_text
from ..report import build_plan_page, build_report
from ..selfreport import build_self_report, opponent_results
from ..stats import analyze
from ..theory import handclass, postflop
from ..theory.page import build_preflop_page
from ..viewer import build_viewer
from .solves import SolveQueue

PLAYER_PAGES = ("plan", "preflop", "rapport", "spots")
SELF_PAGES = ("bilan", "preflop", "spots")
MAX_IMPORT_FILES = 200


class UnknownPlayer(KeyError):
    pass


class Library:
    """Les mains d'un dossier et les pages d'analyse, calculées à la demande puis gardées en cache."""

    def __init__(self, folder: Path | str, hero: Optional[str] = None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hero_override = hero
        self.version = 0
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._cache: dict[tuple, object] = {}
        self.hands: list[Hand] = []
        self.by_id: dict[str, Hand] = {}
        self.hero: Optional[str] = None
        self.known_ids: set[str] = set()
        self.solves = SolveQueue()
        self.reload()

    # --- chargement -------------------------------------------------------------
    def reload(self) -> None:
        hands = load_hands([self.folder])
        hero = self.hero_override or detect_hero(hands)
        heads_up = [h for h in hands if hero and hero in h.seats and len(h.seats) == 2 and h.button and h.bb]
        with self._lock:
            self.known_ids = {f"{h.site}:{h.hand_id}" for h in hands}
            self.hands = heads_up
            self.by_id = {h.hand_id: h for h in heads_up}
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

    def summary(self) -> dict:
        opponents = self.opponents()
        return {
            "hero": self.hero,
            "folder": str(self.folder),
            "hands": len(self.hands),
            "first": self.hands[0].date.strftime("%d/%m/%Y") if self.hands else None,
            "last": self.hands[-1].date.strftime("%d/%m/%Y") if self.hands else None,
            "net_bb": round(sum(o["net_bb"] for o in opponents), 1),
            "opponents": [
                {"name": o["name"], "hands": o["hands"], "net_bb": round(o["net_bb"], 1),
                 "bb100": round(o["bb100"], 1), "last": o["last"].strftime("%d/%m/%Y")}
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
        self.hands_against(player)  # 404 si le joueur est inconnu

        def build():
            hands, stats, lines = self._analysis(player)
            if page == "plan":
                return build_plan_page(hands, stats, self.hero, player, embed=True, lines=lines)
            if page == "preflop":
                return build_preflop_page(hands, self.hero, player, stats, embed=True, spots_href="spots")
            if page == "rapport":
                return build_report(hands, stats, self.hero, player, spots_href="spots", embed=True, lines=lines)
            return build_viewer(hands, self.hero, player, embed=True, solver=True)
        return self._cached(("player", player, page), build)

    def self_page(self, page: str) -> str:
        if page not in SELF_PAGES:
            raise KeyError(page)
        if not self.hands:
            raise UnknownPlayer("moi")

        def build():
            if page == "bilan":
                return build_self_report(self.hands, analyze(self.hands), self.hero, embed=True, spots_href="")
            if page == "preflop":
                return build_preflop_page(self.hands, self.hero, embed=True, spots_href="spots")
            return build_viewer(self.hands, self.hero, None, embed=True, solver=True)
        return self._cached(("self", page), build)

    # --- résolution postflop ----------------------------------------------------
    def _spot(self, hand_id: str) -> postflop.PostflopSpot:
        hand = self.by_id.get(hand_id)
        if hand is None or not self.hero:
            raise UnknownPlayer(hand_id)
        return postflop.build_spot(hand, self.hero)

    def solve(self, hand_id: str, start: bool = False, force: bool = False) -> dict:
        """État de la résolution GTOpen d'une main ; start=True la lance si besoin.

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
            view = self.solves.start(spot, force=reopen)
        if view["state"] == "absent":
            solver = postflop.status()
            view["solver"] = {k: solver[k] for k in ("ready", "message", "install")}
        return view

    def explorer_state(self, hand_id: str) -> dict:
        """Ce que l'explorateur affiche d'une main : le coup, et l'état de sa résolution."""
        view = self.solve(hand_id)
        hand = self.by_id[hand_id]
        villain = hand.opponent_of(self.hero)
        bb = hand.bb
        view["meta"] = {
            "hand": hand_id, "date": hand.date.strftime("%d/%m/%Y %H:%M"), "hero": self.hero, "villain": villain,
            "board": hand.board, "hero_cards": hand.hole_cards.get(self.hero, []),
            "villain_cards": hand.hole_cards.get(villain, []),
            "hero_position": "BTN" if hand.button == self.hero else "BB",
            "net": round(hand.net(self.hero) / bb, 2),
        }
        view["categories"] = handclass.labels()
        return view

    def explorer_node(self, hand_id: str, path: list) -> dict:
        reply = self.solves.node(self._spot(hand_id), path)
        handclass.annotate(reply["node"])  # catégories des mains, pour les filtres
        return reply

    # --- import -----------------------------------------------------------------
    def import_files(self, files: list[dict]) -> dict:
        """Enregistre dans le dossier les fichiers reconnus qui apportent de nouvelles mains."""
        results = []
        added = 0
        known = set(self.known_ids)
        for item in files[:MAX_IMPORT_FILES]:
            name = str(item.get("name") or "fichier")[:200]
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                results.append({"name": name, "status": "vide", "hands": 0, "new": 0})
                continue
            try:
                hands = parse_text(content)
            except ValueError:
                results.append({"name": name, "status": "format non reconnu", "hands": 0, "new": 0})
                continue
            new = [h for h in hands if f"{h.site}:{h.hand_id}" not in known]
            if not new:
                results.append({"name": name, "status": "déjà importé", "hands": len(hands), "new": 0})
                continue
            self._save(name, content)
            known |= {f"{h.site}:{h.hand_id}" for h in new}
            added += len(new)
            results.append({"name": name, "status": "importé", "hands": len(hands), "new": len(new)})
        if added:
            self.reload()
        return {"files": results, "added": added, "state": self.summary()}

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
