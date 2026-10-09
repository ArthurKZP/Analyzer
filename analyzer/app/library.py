"""Bibliothèque de mains de l'application : chargement du dossier, import, analyses en cache."""
from __future__ import annotations

import base64
import binascii
import secrets
import threading
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html import escape
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .. import aliases, bluffs, db, field, handplay, leaks, players, ring, ring_leaks, settings, spots, store, students
from .. import period as periods
from ..db import analyses as db_analyses
from ..db import documents
from ..db import hands as db_hands
from ..cli import detect_hero, unify_hero
from ..lines import villain_lines
from ..models import CALL, RAISE, Hand
from ..parsers import count_hands, read_zip
from ..report import build_plan_page, build_report, format_switch, html_page
from ..selfreport import build_self_report, opponent_results
from ..stats import analyze
from ..theory import (coach, custom_ranges, custom_tree, handclass, postflop, review, ring_preflop, ring_ranges, sizing,
                      studyspots)
from ..theory.page import build_preflop_page, build_ring_preflop_page
from ..theory.preflop import load_solution
from ..viewer import build_viewer
from . import bilan_page, synthesis
from .report_pdf import build_pdf
from .report_pptx import build_pptx
from .bluffs_page import build_bluffs_page
from .import_job import ImportJob
from .field_leaks_page import build_group_page, build_list_page, build_player_page
from .field_page import build_players_page
from .settings_page import build_settings_page
from .hands_page import build_hands_page
from .leaks_page import build_leaks_page
from .ring_page import build_ring_page
from .plan_page import build_coach_page
from .review_page import build_review_page
from .backups import Backups
from .coach_chat import Coach
from .solves import NeedSession, SolveQueue

PLAYER_PAGES = ("plan", "preflop", "rapport", "spots", "solveur", "bluffs")
HEADS_UP_ONLY = ('<p class="note">Aucune main heads-up : cette page analyse tes parties en heads-up. Tes tables à '
                 'plusieurs (3 à 9 joueurs, ensemble) ont leur Leakfinding, leur préflop, leurs mains de départ et leurs '
                 'stats par position (onglet Tables à plusieurs).</p>')
SELF_PAGES = ("bilan", "preflop", "spots", "solveur", "bluffs", "leaks", "tables", "mains")
FIELD_PAGES = ("regs", "recs", "bluffs", "joueurs")  # Étude du field (« joueurs » : aussi dans Paramètres)
MAX_IMPORT_FILES = 5000  # fichiers (ou archives zip) par import


class UnknownPlayer(KeyError):
    pass


def _excluded_note(excluded: dict) -> str:
    if not excluded["hands"]:
        return ""
    return (f"{excluded['hands']} mains contre des récréatifs ({', '.join(excluded['players'])}) sont exclues : "
            "contre eux, l'exploitation prime sur la théorie.")


def _note(text: str) -> str:
    return (f'<p class="note">{escape(text)} Le type de chaque adversaire se règle dans Paramètres › Joueurs et alias '
            "(ou en haut de sa fiche).</p>")


def _kind_note(kind: dict) -> str:
    if kind["kind"] != "rec":
        return ""
    return ("Joueur classé récréatif : tes décisions contre lui ne sont pas comparées à la théorie ; "
            "ses fréquences ci-dessous servent à l'exploiter.")



def _ring_replay(hand: Hand) -> str:
    """Revoir une main d'une table à plusieurs : au solveur, quand elle est allée au flop à deux."""
    if postflop.flop_pair(hand) is None:
        return ""
    return (f'<a class="spots-link" href="/explorateur/{quote(hand.hand_id, safe="")}" target="_blank" '
            'rel="noopener">revoir ↗</a>')


def _digest_count() -> int:
    """Le nombre de mains passées au solveur : les pages qui s'en servent sont recalculées quand il change."""
    return db_analyses.count(db.current())


def _formats(hands: list[Hand]) -> dict[str, int]:
    """Mains par format de table (HU, 3-max, 6-max…), dans cet ordre."""
    counts: dict[str, int] = {}
    for h in sorted(hands, key=lambda h: h.size):
        counts[h.table_format] = counts.get(h.table_format, 0) + 1
    return counts

class Library:
    """Les mains d'un dossier et les pages d'analyse, calculées à la demande puis gardées en cache."""

    def __init__(self, folder: Path | str, hero: Optional[str] = None, solves: Optional[SolveQueue] = None,
                 backups: Optional[Backups] = None, api: str = "/api", pages: str = "/moi", space: str = "moi",
                 space_name: str = "Moi"):
        """folder : la boîte d'arrivée des historiques (importés dans la base au chargement) ; space : l'espace de la
        base (« moi », ou « eleve:<identifiant> ») ; solves, backups : ceux de la bibliothèque principale, partagés par
        celles des élèves (une résolution à la fois) ; api, pages : préfixes des adresses de ses pages et de leurs
        actions."""
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = db.current()
        self.space_id = db_hands.space(self.db, space, space_name, hero)
        self.space_key = space
        self.period = periods.load(space)  # la période d'analyse de l'espace (toutes les mains par défaut)
        self._period_day = date.today()
        self.hero_override = hero
        self.version = 0
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._cache: dict[tuple, object] = {}
        self.hands: list[Hand] = []  # tes mains heads-up de la période
        self.ring: list[Hand] = []  # … et aux tables à plusieurs
        self._all_hands: list[Hand] = []  # toutes, quelle que soit la période
        self._all_ring: list[Hand] = []
        self.by_id: dict[str, Hand] = {}
        self.hero: Optional[str] = None
        self.hero_pseudos: Counter = Counter()  # (pseudo, site) -> mains où tes historiques te marquent ainsi
        self.players_seen: Counter = Counter()  # joueur -> mains (toutes, avant de réunir tes pseudos)
        self.unmarked_seen: Counter = Counter()  # joueur -> mains sans héros marqué (où un pseudo peut s'ajouter)
        self.api, self.pages = api, pages
        self.display_name: Optional[str] = None  # nom de l'élève (rapport)
        self._students: dict[str, Library] = {}
        self._imports: dict[str, ImportJob] = {}  # les imports suivis (barre d'avancement), les derniers
        self._import_pool: Optional[ThreadPoolExecutor] = None  # un import à la fois, en arrière-plan
        self.solves = solves or SolveQueue()
        self.backups = backups or Backups()
        self.coach = Coach(self)
        if solves is None:
            self.solves.on_done.append(lambda job: self.backups.schedule())
        self.reload()

    # --- chargement -------------------------------------------------------------
    def reload(self, progress=None) -> None:
        """Importe les historiques nouveaux du dossier, puis lit les mains de l'espace dans la base (progress(k, n) :
        k mains relues sur n)."""
        db_hands.sync_folder(self.db, self.space_id, self.folder)
        hands = db_hands.load(self.db, self.space_id, progress)
        mapping = aliases.load()  # les pseudos regroupés sous un alias prennent son nom
        aliases.apply(hands, mapping)
        pseudos = Counter((h.hero, h.site) for h in hands if h.hero)  # tes pseudos, tels que tes historiques les marquent
        seen = Counter(name for h in hands for name in h.seats)  # tous les joueurs (ton nom n'est pas le leur)
        unmarked = Counter(name for h in hands if not h.hero for name in h.seats)
        if self.is_me and not self.hero_override:  # tes pseudos réunis sous ton nom (Paramètres)
            hero, mine = self._group_me(hands, pseudos, settings.load())
        else:  # --hero (ligne de commande) ou le pseudo de l'élève, sinon le plus fréquent : ses pseudos sous celui-là
            hero = mapping.get(self.hero_override, self.hero_override) if self.hero_override else detect_hero(hands)
            unify_hero(hands, hero)
            mine = [h for h in hands if hero and hero in h.seats and (not h.hero or h.hero == hero)]
        heads_up = [h for h in mine if len(h.seats) == 2 and h.button and h.bb]
        ring = [h for h in mine if len(h.seats) > 2 and h.button and h.bb]
        with self._lock:
            self._all_hands, self._all_ring = heads_up, ring
            self.by_id = {h.hand_id: h for h in heads_up + ring}  # le solveur résout aussi leurs pots à deux
            self.hero = hero
            self.hero_pseudos = pseudos
            self.players_seen, self.unmarked_seen = seen, unmarked
            self._select()

    @staticmethod
    def my_pseudos(pseudos: Counter, conf: dict) -> set[str]:
        """Tes pseudos : ceux que tes historiques marquent comme toi, sans ceux que tu as retirés, avec ceux que tu as
        ajoutés (Paramètres)."""
        return ({p for p, _ in pseudos} - set(conf["hero_excluded"])) | set(conf["hero_added"])

    def _group_me(self, hands: list[Hand], pseudos: Counter, conf: dict) -> tuple[Optional[str], list[Hand]]:
        """Réunit tes pseudos sous ton nom : renvoie ce nom (celui que tu as choisi, sinon ton pseudo le plus fréquent)
        et tes mains. Une main est à toi quand son héros (marqué par l'historique) est un de tes pseudos, ou, sans héros
        marqué, quand un de tes pseudos y joue : la main d'un pseudo décoché n'est plus la tienne."""
        mine = self.my_pseudos(pseudos, conf)
        if not mine:  # ni héros marqué ni pseudo ajouté : le joueur le plus fréquent
            guess = detect_hero(hands)
            mine = {guess} if guess else set()
        owners = []
        for h in hands:
            me = h.hero or next((p for p in h.seats if p in mine), None)
            if me in mine:
                owners.append((h, me))
        if not owners:
            return conf["hero"] or (min(mine, key=str.lower) if mine else None), []
        name = conf["hero"] or Counter(me for _, me in owners).most_common(1)[0][0]
        kept = []
        for h, me in owners:
            h.seats[me].is_hero = True  # sans héros marqué : ta place
            h.rename(me, name)  # sans effet si un autre joueur de la main porte ce nom : elle n'est alors pas comptée
            if h.hero == name:
                kept.append(h)
        return name, kept

    @property
    def is_me(self) -> bool:
        """Ton espace (pas celui d'un élève) : tes Paramètres s'y appliquent."""
        return self.space_key == "moi"

    def _select(self) -> None:
        """Les mains de la période (sous self._lock) ; les analyses se refont."""
        self._period_day = date.today()
        self.hands = periods.select(self._all_hands, self.period, self._period_day)
        self.ring = periods.select(self._all_ring, self.period, self._period_day)  # tables à plusieurs (3 à 9 joueurs)
        self.version += 1
        self._cache.clear()
        self._key_locks.clear()

    # --- période d'analyse ----------------------------------------------------------------
    def set_period(self, raw: object) -> dict:
        """Choisit la période d'analyse de l'espace (period.clean ; ValueError si elle ne va pas) et la garde."""
        chosen = periods.clean(raw)
        periods.save(self.space_key, chosen)
        with self._lock:
            self.period = chosen
            self._select()
        return self.summary()

    def period_view(self) -> dict:
        """La période et de quoi la choisir : toutes tes mains (par format) et leurs dates."""
        if self.period["kind"] == "days" and self._period_day != date.today():  # « 30 derniers jours » : un jour a passé
            with self._lock:
                self._select()
        every = self._all_hands + self._all_ring
        first = min((h.date for h in every), default=None)
        last = max((h.date for h in every), default=None)
        return dict(self.period, label=periods.label(self.period), text=periods.describe(self.period),
                    all_hands=len(self._all_hands), all_ring=len(self._all_ring),
                    first=first.date().isoformat() if first else None, last=last.date().isoformat() if last else None)

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
        if not self.knows(player) and not any(self.student(s["id"]).knows(player) for s in students.all_students()):
            raise UnknownPlayer(player)  # ni ton adversaire, ni celui d'un élève
        players.set_kind(player, kind)
        return self.summary()

    def knows(self, player: str) -> bool:
        """Un de tes adversaires, en heads-up ou à une table à plusieurs (quelle que soit la période)."""
        return any(player in h.seats for h in self._all_hands) or any(player in h.seats for h in self._all_ring)

    # --- tables à plusieurs : tes adversaires et leur type --------------------------------
    def ring_opponents(self) -> list[dict]:
        """Tes adversaires aux tables à plusieurs (ring.opponents), du plus joué au moins joué."""
        return self._cached(("ring_opponents",), lambda: ring.opponents(self.ring, self.hero) if self.hero else [])

    def _ring_profiles(self) -> dict:
        return self._cached(("ring_profiles",), lambda: ring.profiles(
            self.ring, {o["name"] for o in self.ring_opponents()}))

    def ring_kinds(self) -> dict[str, dict]:
        """Type retenu pour chaque adversaire des tables à plusieurs : ton choix (le même qu'en heads-up), sinon une
        suggestion d'après ses fréquences à ces tables (players.ring_suggest)."""
        return players.classify([o["name"] for o in self.ring_opponents()], {}, ring_profiles=self._ring_profiles())

    def _ring_kinds_key(self) -> tuple:
        return tuple(sorted(name for name, info in self.ring_kinds().items() if info["kind"] == "rec"))

    def ring_opponents_view(self) -> list[dict]:
        """Tes adversaires aux tables à plusieurs, pour les listes : mains à la même table, ton résultat dans les pots
        disputés ensemble, leur type."""
        kinds = self.ring_kinds()
        return [{"name": o["name"], "hands": o["hands"], "pots": o["pots"], "net_bb": round(o["net_bb"], 1),
                 "bb100": round(o["bb100"], 1), "last": o["last"].strftime("%d/%m/%Y"), **kinds[o["name"]]}
                for o in self.ring_opponents()]

    def _ring_versus(self) -> dict[str, str]:
        """Main des tables à plusieurs -> « reg » ou « rec » (ring_leaks.versus)."""
        kinds = self.ring_kinds()
        return {h.hand_id: ring_leaks.versus(h, self.hero, kinds) for h in self.ring} if self.hero else {}

    # --- paramètres ------------------------------------------------------------------------
    def settings_view(self) -> dict:
        """Tes paramètres et de quoi les choisir : tes pseudos (ceux que tes historiques marquent comme toi, avec leurs
        sites et leurs mains, cochés s'ils sont réunis sous ton nom ; ceux que tu as ajoutés), les joueurs à proposer
        pour en ajouter, tes mains par format (toutes périodes), tes élèves, la précision du solveur."""
        conf = settings.load()
        excluded, added = set(conf["hero_excluded"]), set(conf["hero_added"])
        by_name: dict[str, dict] = {}
        for (pseudo, site), n in self.hero_pseudos.items():
            row = by_name.setdefault(pseudo, {"name": pseudo, "sites": [], "hands": 0, "added": False})
            row["hands"] += n
            if site not in row["sites"]:
                row["sites"].append(site)
        for pseudo in added - set(by_name):  # ses mains : celles sans héros marqué où il joue
            by_name[pseudo] = {"name": pseudo, "sites": [], "hands": self.unmarked_seen.get(pseudo, 0), "added": True}
        for row in by_name.values():
            row["included"] = row["name"] not in excluded
        pseudos = sorted(by_name.values(), key=lambda r: (r["added"], -r["hands"], r["name"].lower()))
        # à ajouter : les joueurs des mains sans héros marqué (ailleurs, ce sont tes adversaires)
        mine = self.my_pseudos(self.hero_pseudos, conf) | {self.hero or ""}
        others = [name for name, _ in self.unmarked_seen.most_common() if name not in mine][:500]
        return {"hero": self.hero, "chosen_hero": conf["hero"], "pseudos": pseudos, "players": others,
                "my_hands": len(self._all_hands) + len(self._all_ring),
                "formats": {"HU": len(self._all_hands), "ring": len(self._all_ring)}, "plays": conf["formats"],
                "coach": conf["coach"], "students": len(students.all_students()), "min_hands": conf["min_hands"],
                "precision": self.solves.precision()[1], "period": periods.label(self.period)}

    def settings_page(self) -> str:
        return build_settings_page(self.settings_view())

    def set_settings(self, changes: object) -> dict:
        """Enregistre des paramètres (settings.check ; ValueError sinon). Tes pseudos : un pseudo marqué qu'on rajoute
        se recoche ; un pseudo ajouté joue dans tes mains sans héros marqué (ailleurs, c'est un adversaire) ; il t'en
        reste au moins un ; ton nom n'est pas celui d'un autre joueur de tes mains. Les pages se recalculent."""
        checked = settings.check(changes)
        before = settings.load()
        marked = {p for p, _ in self.hero_pseudos}
        if "hero_added" in checked:
            back = marked & set(checked["hero_added"])
            if back:  # un pseudo que tes historiques marquent : il revient en se recochant
                checked["hero_added"] = [p for p in checked["hero_added"] if p not in back]
                checked["hero_excluded"] = [p for p in checked.get("hero_excluded", before["hero_excluded"])
                                            if p not in back]
            for p in sorted(set(checked["hero_added"]) - set(before["hero_added"]), key=str.lower):
                if p not in self.players_seen:
                    raise ValueError(f"Pseudo introuvable dans tes mains : {p}.")
                if p not in self.unmarked_seen:
                    raise ValueError(f"« {p} » joue contre toi dans tes mains : ce n'est pas un de tes pseudos.")
        conf = dict(before, **checked)
        mine = self.my_pseudos(self.hero_pseudos, conf)
        if {"hero_excluded", "hero_added"} & set(checked) and (marked or conf["hero_added"]) and not mine:
            raise ValueError("Garde au moins un pseudo : tes analyses ont besoin de toi.")
        name = checked.get("hero")
        if name is not None and name not in mine and name != self.hero and name in self.players_seen:
            raise ValueError(f"« {name} » est le pseudo d'un autre joueur de tes mains : choisis un autre nom.")
        if conf["hero"] and conf["hero"] not in mine and conf["hero"] in self.players_seen:
            checked["hero"] = None  # ton nom était un pseudo que tu retires : ton pseudo le plus fréquent le remplace
        settings.save(checked)
        if {"hero", "hero_excluded", "hero_added"} & set(checked):
            self.reload()
        else:
            with self._lock:
                self._select()
        return self.summary()

    def _app_settings(self) -> dict:
        """Ce que le menu de l'application montre : les formats que tu joues (None : tous), les Élèves, le seuil
        de l'Étude du field."""
        conf = settings.load()
        return {"plays": conf["formats"] if self.is_me else None,
                "coach": settings.coach_shown(len(students.all_students()), conf["coach"]),
                "min_hands": conf["min_hands"]}

    SIDEBAR_RING_MIN = 10  # mains ensemble minimum pour qu'un adversaire des tables à plusieurs soit dans le menu

    def summary(self) -> dict:
        period = self.period_view()
        opponents = self.opponents()
        kinds = self.kinds()
        ring_kinds = self.ring_kinds() if self.ring else {}
        return {
            "settings": self._app_settings(),
            # les adversaires des tables à plusieurs (menu de l'application), avec assez de mains ensemble
            "ring_opponents": [
                {"name": o["name"], "hands": o["hands"], "net_bb": round(o["net_bb"], 1), "bb100": round(o["bb100"], 1),
                 "last": o["last"].strftime("%d/%m/%Y"), "kind": ring_kinds[o["name"]]["kind"]}
                for o in self.ring_opponents() if o["hands"] >= self.SIDEBAR_RING_MIN],
            "hero": self.hero,
            "period": period,  # la période d'analyse ; hands, ring_hands, first, last : ceux de la période
            "folder": str(self.folder),
            "hands": len(self.hands),
            "ring_hands": len(self.ring),  # tables à plusieurs
            "aliases": aliases.groups(),  # {alias: [ses pseudos]}
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
                store.flush()  # les calculs gardés sur disque (équités, spots…) sont écrits tout de suite
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
            return build_viewer(hands, self.hero, player, embed=True, solver=True, api=f"{self.api}/coups",
                                records=self._spot_index(player))
        return self._cached(("player", player, page) + ((kind["kind"],) if page == "preflop" else ()), build)

    def self_page(self, page: str, table_format: Optional[str] = None) -> str:
        """Une page de Mon jeu ; table_format : le heads-up (« HU ») ou les tables à plusieurs (« ring »), pour le
        Leakfinding et le préflop."""
        if page not in SELF_PAGES:
            raise KeyError(page)
        if page == "tables":  # tables à 3 joueurs et plus : pas besoin de mains heads-up
            changes = documents.revision(db.current(), "plan", "ranges")[2:]  # charts, plans des flops 6-max
            return self._cached(("self", "tables") + changes + self._ring_kinds_key(), self._ring_page)
        if page == "mains":  # heads-up et tables à plusieurs ; les analyses du solveur s'y ajoutent
            key = (("self", "mains", tuple(sorted(ring_ranges.available().items())), _digest_count()) + self._kinds_key()
                   + self._ring_kinds_key())
            return self._cached(key, self._hands_page)
        if page == "leaks":  # change au fil des analyses : le rapport a son propre cache
            return self.leaks_page(table_format=table_format)
        if page == "preflop" and self._leak_format(table_format) != "HU":
            return self._ring_preflop_page()
        if page == "bilan" and self._leak_format(table_format) != "HU":
            return self._ring_bilan()
        if not self.hands:
            if self.ring:  # seulement des tables à plusieurs : la page heads-up le dit
                return html_page("Heads-up", HEADS_UP_ONLY, True)
            raise UnknownPlayer("moi")
        regular, excluded = self.regular_hands()
        if page == "solveur":
            return build_review_page(regular, self.hero, excluded=excluded, api=f"{self.api}/revue")

        def build():
            if page == "bilan":
                return build_self_report(self.hands, self.all_stats(), self.hero, embed=True, spots_href="",
                                         kinds=self.kinds(), head=self._bilan_head("HU"))
            if page == "preflop":
                return build_preflop_page(regular, self.hero, embed=True, spots_href="spots",
                                          note=_excluded_note(excluded), switch=format_switch(self.leak_formats(), "HU"))
            if page == "bluffs":
                return self._population_bluffs()
            return build_viewer(self.hands, self.hero, None, embed=True, solver=True, api=f"{self.api}/coups",
                                records=self._spot_index(None))
        kinds = self._kinds_key() if page in ("bilan", "preflop", "bluffs") else ()
        return self._cached(("self", page) + kinds, build)

    def _ring_overview(self) -> tuple[list, list]:
        """Tes stats aux tables à plusieurs (toutes ensemble) sur toutes tes mains, contre les réguliers et contre les
        récréatifs (ring.analyze), et tes écarts les plus importants contre les réguliers : pour la page des tables et le
        bilan."""
        def build():
            hero = self.hero or ""
            kinds = self.ring_kinds()
            parts = ring_leaks.split(ring_leaks.mine(self.ring, hero), hero, kinds)
            scopes = [(scope, label, ring.analyze(parts[scope], hero, merge=True)) for scope, label in leaks.SCOPES]
            return scopes, leaks.top_stat_gaps(ring_leaks.stats(self.ring, hero, kinds), "reg", 5)
        key = (("ring_overview",) + documents.revision(db.current(), "plan", "ranges")[2:] + self._ring_kinds_key())
        return self._cached(key, build)

    def _ring_page(self) -> str:
        """Tes stats par position aux tables à plusieurs (toutes ensemble), sur toutes tes mains, contre les réguliers
        et contre les récréatifs, après tes écarts les plus importants contre les réguliers."""
        scopes, gaps = self._ring_overview()
        return build_ring_page(scopes, self.hero or "", spots=self.ring_spots(), ranges=ring_ranges.available(),
                               gaps=gaps)

    def _bilan_head(self, fmt: str) -> str:
        """En tête du bilan : le choix du format, et tes résultats tous formats confondus."""
        hero = self.hero or ""
        return format_switch(self.leak_formats(), fmt) + bilan_page.overall_html(
            bilan_page.results(self.hands, hero), bilan_page.results(ring_leaks.mine(self.ring, hero), hero))

    def _ring_bilan(self) -> str:
        """Ton bilan aux tables à plusieurs, sur le plan de celui du heads-up."""
        def build():
            hero = self.hero or ""
            scopes, gaps = self._ring_overview()
            labels = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}
            shown = [(scope, labels[scope], found[0] if found else None) for scope, _, found in scopes]
            return bilan_page.build_ring_bilan(ring_leaks.mine(self.ring, hero), hero, shown,
                                               self.ring_opponents_view(), gaps, head=self._bilan_head("ring"))
        key = (("self", "bilan", "ring") + documents.revision(db.current(), "plan", "ranges")[2:]
               + self._ring_kinds_key())
        return self._cached(key, build)

    def _ring_preflop_page(self) -> str:
        """Mon préflop aux tables à plusieurs : tes décisions contre les réguliers face aux charts, avec les mêmes
        cartes (theory/ring_preflop.py)."""
        def build():
            hero = self.hero or ""
            parts = ring_leaks.split(ring_leaks.mine(self.ring, hero), hero, self.ring_kinds())
            note = (f"{len(parts['rec'])} mains contre des récréatifs sont exclues (un récréatif a mis de l'argent dans le "
                    "pot pendant que tu y étais) : contre eux, l'exploitation prime sur la théorie."
                    if parts["rec"] else "")
            return build_ring_preflop_page(
                ring_preflop.collect(parts["reg"], hero), hero, embed=True, note=note,
                switch=format_switch(self.leak_formats(), ring_leaks.FORMAT), replay=_ring_replay,
                load_hint=" Charge-les dans Mon jeu › Tables à plusieurs (« Charger les charts »).")
        key = ("self", "preflop", "ring") + documents.revision(db.current(), "ranges")[2:] + self._ring_kinds_key()
        return self._cached(key, build)

    def _plays(self) -> dict[str, list]:
        """Tes mains de départ lues (handplay), en heads-up et aux tables à plusieurs (3 à 9 joueurs ensemble), avec
        l'EV perdue après le flop des coups déjà passés au solveur."""
        def build():
            if not self.hero:
                return {}
            theory = handplay.Theory(load_solution(), handplay.ring_lines())
            losses = review.hero_losses(review.saved_digests())
            plays = {"HU": handplay.collect(self.hands, self.hero, theory, losses),
                     "ring": handplay.collect(self.ring, self.hero, theory, losses)}
            return {k: v for k, v in plays.items() if v}
        return self._cached(("plays", tuple(sorted(ring_ranges.available().items())), _digest_count()), build)

    def _kind_map(self) -> dict[str, str]:
        return {name: info["kind"] for name, info in self.kinds().items()}

    def _hands_page(self) -> str:
        """Ce que rapporte chaque main de départ ; le détail d'une main se demande à hands_detail."""
        shown = [fmt for fmt, _ in self.leak_formats()]  # les formats que tu joues
        formats = {fmt: handplay.aggregate(plays, self._kind_map() if fmt == "HU" else self._ring_versus())
                   for fmt, plays in self._plays().items() if fmt in shown}
        return build_hands_page(formats, api=f"{self.api}/mains")

    def hands_detail(self, query: dict[str, str]) -> dict:
        """D'où vient le résultat d'une main (ou d'une famille) : fmt, main, et les filtres de la page (type
        d'adversaire, position, décision, groupe d'actions : agg, pas)."""
        plays = self._plays().get(query.get("fmt") or "HU")
        name = query.get("main") or ""
        if plays is None or not name:
            raise KeyError(name)
        actions = {"agg": ("raise", "allin"), "pas": ("call", "check")}.get(query.get("act") or "")
        situation = query.get("sit") or "all"
        if situation != "all" and situation not in dict(handplay.SITUATIONS):
            raise KeyError(situation)
        return handplay.detail(plays, self._kind_map() if query.get("fmt", "HU") == "HU" else self._ring_versus(),
                               name=name,
                               kind=query.get("kind") or "", position=query.get("pos") or "", situation=situation,
                               actions=actions)

    # --- visualiseur de spots : recherche côté serveur -----------------------------------
    SPOT_FILTERS = ("opp", "pot", "pfa", "pos", "reach", "st", "cb", "h", "v", "l", "end", "known", "res", "sort", "q")

    def _spot_index(self, villain: Optional[str]) -> list[dict]:
        """Les fiches de tes mains (sans le détail), contre cet adversaire ou tous."""
        def build():
            hands = self.hands_against(villain) if villain else self.hands
            return spots.list_records(hands, self.hero, villain) if self.hero else []
        return self._cached(("spot_index", villain or ""), build)

    def spots_search(self, query: dict[str, str]) -> dict:
        """Une page de mains du visualiseur : filtres (ceux de la page), tri, offset, limit ; adversaire : ses mains."""
        index = self._spot_index(query.get("adversaire") or None)
        filters = {k: query[k] for k in self.SPOT_FILTERS if query.get(k)}
        if "reach" in filters and not filters["reach"].isdigit():
            del filters["reach"]
        number = lambda key, default: int(query[key]) if (query.get(key) or "").isdigit() else default  # noqa: E731
        return spots.search(index, filters, number("offset", 0), number("limit", 150))

    def hand_fiche(self, hand_id: str) -> dict:
        """La fiche complète d'une de tes mains heads-up (pour la rejouer)."""
        hand = self.by_id.get(hand_id)
        if hand is None or not self.hero or self.hero not in hand.seats or len(hand.seats) != 2 or not hand.button:
            raise KeyError(hand_id)
        return spots.hand_record(hand, self.hero, hand.opponent_of(self.hero))

    # --- étude du field -------------------------------------------------------------------
    def field_page(self, page: str, table_format: Optional[str] = None) -> str:
        """Étude du field : le leakfinding des réguliers et des récréatifs, les bluffs des réguliers (heads-up), tes
        adversaires et leur type."""
        if page not in FIELD_PAGES:
            raise KeyError(page)
        if page == "bluffs":
            return self.self_page("bluffs")
        if not self.hands and not self.ring:
            raise UnknownPlayer("moi")
        if page in ("regs", "recs"):
            return self.field_list(page[:3], table_format)
        return build_players_page(self.summary()["opponents"] if self.hands else [], self.ring_opponents_view(),
                                  aliases.groups())

    def _field_format(self, table_format: Optional[str]) -> str:
        """Le format d'une page du field : celui demandé s'il a des mains, sinon le premier que tu joues."""
        formats = [fmt for fmt, _ in self.leak_formats()]
        if not formats:
            raise UnknownPlayer("aucune main")
        wanted = "HU" if table_format == "HU" else ring_leaks.FORMAT if table_format else None
        return wanted if wanted in formats else formats[0]

    def _field_rows(self, fmt: str) -> list[dict]:
        """Tes adversaires d'un format, du plus joué au moins joué : mains, ton résultat, type."""
        if fmt == "HU":
            kinds = self.kinds()
            return [{"name": o["name"], "hands": o["hands"], "net_bb": o["net_bb"], "bb100": o["bb100"],
                     "info": kinds[o["name"]]} for o in self.opponents()]
        kinds = self.ring_kinds()
        return [{"name": o["name"], "hands": o["hands"], "net_bb": o["net_bb"], "bb100": o["bb100"],
                 "info": kinds[o["name"]]} for o in self.ring_opponents()]

    def _field_index(self, fmt: str) -> dict[str, list[Hand]]:
        """Les mains d'un format par joueur (pour étudier chacun sans relire toutes les mains)."""
        def build():
            index: dict[str, list[Hand]] = {}
            for h in self.hands if fmt == "HU" else self.ring:
                for name in h.seats:
                    index.setdefault(name, []).append(h)
            return index
        return self._cached(("field_index", fmt), build)

    def field_study(self, fmt: str, names: tuple) -> "field.Study":
        """L'étude d'un adversaire ou d'un groupe (field.study), gardée en cache."""
        def build():
            index = self._field_index(fmt)
            hands = list({h.hand_id: h for n in names for h in index.get(n, [])}.values())
            hands.sort(key=lambda h: h.date)
            if fmt == "HU":
                stats = self.all_stats()
                found = [stats[n] for n in names if n in stats]
                ps = found[0] if len(found) == 1 else field.merge_stats(found)
                return field.study(hands, names, "HU", ps)
            return field.study(hands, names, "ring")
        return self._cached(("field_study", fmt, names), build)

    def _field_switch(self, fmt: str) -> str:
        return format_switch(self.leak_formats(), fmt)

    def field_list(self, kind: str, table_format: Optional[str] = None) -> str:
        """Les réguliers (« reg ») ou les récréatifs (« rec ») que tu croises le plus, avec leurs leaks à exploiter ;
        les récréatifs aussi par style."""
        fmt = self._field_format(table_format)
        min_hands = settings.load()["min_hands"]
        kinds_key = self._kinds_key() if fmt == "HU" else (self._ring_kinds_key(),)

        def build():
            every = [r for r in self._field_rows(fmt) if r["info"]["kind"] == kind]
            rows = [(r, self.field_study(fmt, (r["name"],))) for r in every if r["hands"] >= min_hands]
            names = tuple(r["name"] for r, _ in rows)
            population = self.field_study(fmt, names) if len(names) > 1 else None
            groups = []
            if kind == "rec":
                for style in field.STYLE_ORDER:
                    members = [r for r, st in rows if st.style[0] == style]
                    if members:
                        groups.append({"style": style, "members": members,
                                       "study": self.field_study(fmt, tuple(m["name"] for m in members))})
            return build_list_page(kind, fmt, rows, population, min_hands, len(every), self._field_switch(fmt), groups)
        return self._cached(("field", kind, fmt, min_hands) + tuple(kinds_key), build)

    def field_player(self, name: str, table_format: Optional[str] = None) -> str:
        """La fiche d'un adversaire dans l'Étude du field : ses leaks, sa value et ses bluffs par ligne."""
        rows = {fmt: next((r for r in self._field_rows(fmt) if r["name"] == name), None)
                for fmt, _ in self.leak_formats()}
        rows = {fmt: r for fmt, r in rows.items() if r}
        if not rows:
            raise UnknownPlayer(name)
        wanted = "HU" if table_format == "HU" else ring_leaks.FORMAT if table_format else None
        fmt = wanted if wanted in rows else next(iter(rows))
        row = rows[fmt]
        switch = format_switch([(f, r["hands"]) for f, r in rows.items()], fmt)
        fiche = f"/#/adversaire/{quote(name, safe='')}/plan" if "HU" in rows else ""
        return build_player_page(row, self.field_study(fmt, (name,)), fmt, row["info"], switch, fiche)

    def field_group(self, style: str, table_format: Optional[str] = None) -> str:
        """La fiche d'un groupe de récréatifs (un style) : ses joueurs, son plan, ses leaks et ses lignes réunis."""
        if style not in field.STYLES:
            raise KeyError(style)
        fmt = self._field_format(table_format)
        min_hands = settings.load()["min_hands"]
        rows = [r for r in self._field_rows(fmt) if r["info"]["kind"] == "rec" and r["hands"] >= min_hands]
        members = [(r, st) for r in rows for st in [self.field_study(fmt, (r["name"],))] if st.style[0] == style]
        if not members:
            raise UnknownPlayer(style)
        study = self.field_study(fmt, tuple(r["name"] for r, _ in members))
        return build_group_page(style, fmt, study, members, self._field_switch(fmt))

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
        note = (f"Les {len(regs)} réguliers ensemble (chacun dans « Adversaire par adversaire », en bas de page)."
                if len(regs) > 1 else "Un seul régulier." if regs else "Aucun adversaire classé régulier.")
        if recs:
            note += f" Les {len(recs)} récréatifs n'y sont pas : ils ont leur page, dans l'onglet Récréatifs."
        report = bluffs.analyze(self.hands, regs, self.hero, bluffs.Voice("les réguliers", plural=True))
        profile = {p.name: p for p in report.profiles}
        for row in rows:
            prof = profile.get(row["name"])
            row.update(weight=prof.weight if prof else None, share=prof.share if prof else None,
                       group=prof.group if prof else None)
        groups = []
        for cluster in report.clusters:  # ce qui ressort de chaque groupe, ses membres pesant chacun au plus 1
            sub = (bluffs.analyze(self.hands, cluster.names, self.hero, bluffs.Voice("les joueurs du groupe", plural=True))
                   if len(cluster.members) > 1 else None)
            groups.append({"cluster": cluster, "patterns": (sub.patterns if sub else [])[:3]})
        return build_bluffs_page(report, "des réguliers", note=_note(note),
                                 players=sorted(rows, key=lambda r: -r["hands"]), groups=groups)

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
                          api=f"/api/eleves/{ident}", pages=f"/eleve/{ident}", space=students.PREFIX + ident,
                          space_name=meta["name"])
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
    def leak_formats(self) -> list[tuple[str, int]]:
        """Les formats de tes mains et leur nombre : le heads-up, puis les tables à plusieurs (3 à 9 joueurs
        ensemble) ; un rapport chacun. Dans ton espace, seulement ceux que tu joues (Paramètres)."""
        out = [("HU", len(self.hands))] if self.hands else []
        out += [(ring_leaks.FORMAT, len(self.ring))] if self.ring else []
        if not self.is_me:
            return out
        kept = settings.enabled_formats([fmt for fmt, _ in out])
        return [(fmt, n) for fmt, n in out if fmt in kept]

    def _leak_format(self, table_format: Optional[str]) -> str:
        """Le format demandé (par défaut le heads-up, ou les tables à plusieurs) ; « 6-max », « 3-max »… : les tables
        à plusieurs. UnknownPlayer sans mains de ce format."""
        formats = [fmt for fmt, _ in self.leak_formats()]
        if not formats:
            raise UnknownPlayer("aucune main")
        if not table_format:
            return formats[0]
        fmt = "HU" if table_format == "HU" else ring_leaks.FORMAT
        if fmt not in formats:
            raise UnknownPlayer(table_format)
        return fmt

    def leaks_report(self, table_format: str = "HU") -> "leaks.Report":
        """Le rapport d'un format, recalculé quand des mains, des analyses du solveur, tes charts, les plans de jeu ou
        le type de tes adversaires changent."""
        changes = (_digest_count(),) + documents.revision(db.current(), "plan", "ranges")[2:]
        if table_format == "HU":
            return self._cached(("leaks",) + changes + self._kinds_key(),
                                lambda: leaks.build(self.hands, self.hero, self.kinds()))
        return self._cached(("leaks", ring_leaks.FORMAT) + changes + self._ring_kinds_key(),
                            lambda: ring_leaks.build(self.ring, self.hero or "", self.ring_kinds()))

    def leaks_page(self, standalone: bool = False, table_format: Optional[str] = None) -> str:
        fmt = self._leak_format(table_format)
        return build_leaks_page(self.leaks_report(fmt), api=f"{self.api}/leaks", pages=self.pages,
                                embed=not standalone, standalone=standalone, name=self.display_name,
                                opponents=self.summary()["opponents"] if fmt == "HU" else self.ring_opponents_view(),
                                formats=self.leak_formats(), period=periods.describe(self.period))

    def report_synthesis(self, table_format: Optional[str] = None) -> synthesis.Synthesis:
        """L'essentiel du Leakfinding d'un format, sur la période (pour le rapport PDF et la présentation)."""
        fmt = self._leak_format(table_format)
        hero = self.hero or ""
        hands = self.hands if fmt == "HU" else ring_leaks.mine(self.ring, hero)
        period = "" if self.period["kind"] == "all" else periods.label(self.period)
        return synthesis.collect(self.leaks_report(fmt), hands, self.display_name or hero, period,
                                 student=self.display_name is not None)

    def report_pdf(self, table_format: Optional[str] = None) -> bytes:
        """Le rapport de Leakfinding en PDF, synthétique."""
        return build_pdf(self.report_synthesis(table_format))

    def report_pptx(self, table_format: Optional[str] = None) -> bytes:
        """La présentation PowerPoint du Leakfinding, pour la séance du coach avec l'élève."""
        return build_pptx(self.report_synthesis(table_format))

    def leaks_state(self, start: bool = False, table_format: Optional[str] = None) -> dict:
        """Les mains choisies (contre les réguliers en heads-up) : analysées, à analyser, en cours ; start=True les
        met en file."""
        report = self.leaks_report(self._leak_format(table_format))
        picks = report.picks.get(report.solver_scope, [])
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

    def leaks_cancel(self, table_format: Optional[str] = None) -> dict:
        for spot in leaks.selection_spots(self.leaks_report(self._leak_format(table_format))):
            view = self.solves.lookup(spot)
            if view["state"] in ("waiting", "running"):
                self.solves.cancel(view["job"])
        return self.leaks_state(table_format=table_format)

    # --- résolution postflop ----------------------------------------------------
    def _spot(self, hand_id: str):
        """Une main jouée (son numéro), ou un spot d'étude (« spot:srp:KsKd4c ») ; avec tes ranges ajustées et tes
        modifications de l'arbre (tailles, verrous : custom_tree)."""
        if hand_id.startswith("spot:"):
            spot = studyspots.parse_ident(hand_id, custom=True)
            if spot is None:
                raise UnknownPlayer(hand_id)
        else:
            found = self.find_hand(hand_id)
            if found is None:
                raise UnknownPlayer(hand_id)
            spot = postflop.build_spot(*found)
        edits = custom_tree.load(spot.ident)
        spot.edits = edits if custom_tree.edited(edits) else None
        return spot

    def solve(self, hand_id: str, start: bool = False, force: bool = False, fresh: bool = False) -> dict:
        """État de la résolution GTOpen d'une main (ou d'un spot d'étude) ; start=True la lance si besoin.

        force=True relance une main déjà résolue pour rouvrir une session navigable ; fresh=True la résout à
        nouveau à la précision réglée (affiner).
        """
        try:
            spot = self._spot(hand_id)
        except postflop.Unsupported as exc:
            return {"hand": hand_id, "state": "unsupported", "message": str(exc)}
        view = self.solves.lookup(spot)
        reopen = force and view["state"] == "done" and not view["live"]
        if start and fresh and view["state"] not in ("waiting", "running"):
            solver = postflop.status()
            if not solver["ready"]:
                return {"hand": hand_id, "state": "unavailable", "message": solver["message"],
                        "install": solver["install"]}
            view = self.solves.start(spot, fresh=True)
        elif start and (view["state"] in ("absent", "error", "cancelled") or reopen):
            solver = postflop.status()
            if not solver["ready"]:
                return {"hand": hand_id, "state": "unavailable", "message": solver["message"],
                        "install": solver["install"]}
            board = "".join(getattr(spot, "board", []))
            if (isinstance(spot, studyspots.StudySpot) and not spot.plan and not spot.adjusted and not spot.edits
                    and board in studyspots.flop_set(spot.family) and postflop.solved_request(spot.request()) is None):
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
        view["edits"] = ({"sizes": len(spot.edits["plan"]), "locks": len(spot.edits["locks"])} if spot.edits else None)
        view["precision"] = self.solves.precision()[1]  # le réglage, pour la prochaine résolution
        return view

    def estimate(self, hand_id: str) -> dict:
        """Durée estimée de la résolution d'un coup ou d'un spot d'étude, pour chaque précision."""
        spot = self._spot(hand_id)
        out = postflop.estimate(spot.request())
        out["precision"] = self.solves.precision()[1]
        return out

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
        lock = custom_tree.lock_at(spot.edits, path)
        if lock is not None:  # ton verrou à ce nœud : tes changements et les mains concernées
            reply["node"]["lock"] = {"edits": lock["edits"], "edited": lock.get("edited", [])}
        return reply

    # --- arbre modifié : tailles de mise et nœuds verrouillés (onglet Arbre de l'explorateur) ----------
    def _situation(self, spot, key: str, base_plan: dict, overrides: dict) -> dict:
        """Une situation de l'arbre : son nom, ses tailles en jeu et celles d'origine."""
        family = getattr(spot, "family", None)
        family = family if family in sizing.PROFILES else "srp"
        positions = (spot.oop, spot.ip) if isinstance(spot, studyspots.StudySpot) else spot.positions
        title = sizing.label(key, family, positions)
        street = {"f": "flop", "t": "turn", "r": "river"}[key.split(":")[1][0]]
        if street not in title.lower():
            title += f" ({street})"
        base = list(base_plan[key]) if key in base_plan else spot.default_sizes_for(key)
        return {"key": key, "title": title, "raise": key.startswith("raise"), "street": "ftr".index(key.split(":")[1][0]),
                "base": base, "sizes": list(overrides.get(key, base)), "changed": key in overrides}

    @staticmethod
    def _situation_order(key: str) -> tuple:
        """Dans l'ordre du coup : par street, puis par ce qui s'est passé avant, la mise avant les relances, le joueur
        hors de position d'abord."""
        parts = key.split(":")
        level = int(parts[3]) if parts[0] == "raise" else -1
        return "ftr".index(parts[1][0]), parts[2], level, parts[1][1] != "o"

    def tree_state(self, hand_id: str, path: Optional[list] = None) -> dict:
        """L'arbre du coup : ses situations (tailles en jeu, d'origine, modifiées), celle du nœud au bout du chemin, et
        tes verrous."""
        spot = self._spot(hand_id)
        data = spot.edits or {"plan": {}, "locks": []}
        base_plan = dict(getattr(spot, "plan", None) or {})
        overrides = data["plan"]
        keys = sorted(set(base_plan) | set(overrides), key=self._situation_order)
        here = None
        if path is not None:
            try:
                node = self.solves.node(spot, path)["node"]
            except (NeedSession, postflop.SolverError):
                node = None
            key = postflop.node_situation(node) if node else None
            if key is not None:
                here = self._situation(spot, key, base_plan, overrides)
        locks = [{"index": k, "path": x["path"], "title": x.get("title", ""), "edits": x["edits"],
                  "actions": x.get("actions", [])} for k, x in enumerate(data["locks"])]
        return {"hand": hand_id, "edited": bool(spot.edits), "here": here,
                "situations": [self._situation(spot, k, base_plan, overrides) for k in keys], "locks": locks,
                "live": self.solves.live_for(spot) is not None}

    def set_tree_sizes(self, hand_id: str, plan: dict) -> dict:
        """Tes tailles pour ces situations ({clé: tailles}) ; les mêmes que l'arbre d'origine effacent la modification.
        Les verrous sont retirés (ils ne mènent plus aux mêmes nœuds). ValueError si une taille ne va pas."""
        spot = self._spot(hand_id)
        if not isinstance(plan, dict) or not plan or len(plan) > 40:
            raise ValueError("Requête invalide.")
        base_plan = dict(getattr(spot, "plan", None) or {})
        checked = {custom_tree.check_key(k): custom_tree.check_sizes(k, v) for k, v in plan.items()}
        removed = 0
        for key, sizes in checked.items():
            base = base_plan[key] if key in base_plan else spot.default_sizes_for(key)
            removed += custom_tree.set_sizes(spot.ident, key, sizes, base)
        return dict(self.tree_state(hand_id), removed_locks=removed)

    def reset_tree(self, hand_id: str) -> dict:
        """Revient à l'arbre d'origine : tes tailles et tes verrous sont effacés."""
        spot = self._spot(hand_id)
        custom_tree.save(spot.ident, {})
        return self.tree_state(hand_id)

    def add_lock(self, hand_id: str, path: list, edits: list, labels: Optional[list] = None) -> dict:
        """Verrouille le nœud au bout du chemin : toutes les mains du joueur gardent la stratégie de la résolution
        ouverte, sauf celles que tu changes (edits : [{"label", "combos", "freqs"}]). labels : les libellés des actions
        tels que l'explorateur les affiche (gardés avec le verrou). Il faut la session de cette résolution. ValueError
        si le verrou ne va pas."""
        spot = self._spot(hand_id)
        session = self.solves.live_for(spot)
        if session is None:
            raise ValueError("Ouvre d'abord la résolution complète de ce coup (session active) pour verrouiller un nœud.")
        if not isinstance(edits, list) or not 0 < len(edits) <= 40:
            raise ValueError("Choisis des mains et leur stratégie.")
        reply = session.ask({"path": path, "strategy": True})
        node = reply["node"]
        positions = (spot.oop, spot.ip) if isinstance(spot, studyspots.StudySpot) else spot.positions
        if not (isinstance(labels, list) and len(labels) == len(node["actions"])
                and all(isinstance(x, str) and 0 < len(x) <= 40 for x in labels)):
            labels = [x[:1].upper() + x[1:] for x in postflop.action_labels(node)]
        node = dict(node, path=path, labels=labels, title=self._lock_title(node, positions))
        previous = custom_tree.lock_at(spot.edits, path)
        lock = custom_tree.lock_from(node, reply.get("strategy") or [], edits, previous)
        custom_tree.put_lock(spot.ident, lock)
        return self.tree_state(hand_id, path)

    @staticmethod
    def _lock_title(node: dict, positions) -> str:
        """« Turn K♥ · BTN, après BB check, BTN mise 33 %, BB call, BB check »."""
        streets = ("Flop", "Turn", "River")
        steps = []
        for h in node.get("history", []):
            if h.get("kind") == "action" and h.get("chosen") is not None:
                steps.append(f"{positions[h['player']]} {postflop.action_labels(h)[h['chosen']].lower()}")
        board = "".join(node.get("board", [])[3:])
        where = streets[node.get("street", 0)] + (f" {board}" if board else "")
        who = positions[node["player"]] if node.get("player") is not None else ""
        return f"{where} · {who}" + (f", après {', '.join(steps)}" if steps else "")

    def remove_lock(self, hand_id: str, index: Optional[int]) -> dict:
        """Retire un verrou (son rang), ou tous (index None)."""
        spot = self._spot(hand_id)
        custom_tree.remove_lock(spot.ident, index)
        return self.tree_state(hand_id)

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
        if not studyspots.is_series(family):
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
                    view = self._queue_flop(family, board, spot, view, board in series)
                row.update(state=view["state"], progress=view.get("progress"), job=view.get("job"),
                           max_iterations=view.get("max_iterations"), mode=view.get("mode"))
            rows.append(row)
        return {"family": family, "label": studyspots.family_info(family)["label"], "ready": ready,
                "precision": self.solves.precision()[1],
                "total": len(rows), "done": sum(r["done"] for r in rows),
                "busy": sum(r.get("state") in ("waiting", "running") for r in rows), "rows": rows}

    def _queue_flop(self, family: str, board: str, spot, view: dict, series: bool) -> dict:
        """Met un flop d'une série en file : le choix de ses tailles d'abord s'il n'en a pas, puis sa résolution."""
        if series and not spot.plan:
            return self.solves.choose_and_solve(
                spot.ident, lambda job: self._choose(family, board, job),
                lambda: studyspots.StudySpot(family, studyspots.cards_of(board)))
        return self.solves.start(spot, force=view["state"] == "done", keep_live=False)

    RING_ORDER = ("4bet", "3bet", "srp")  # les pots les plus rapides à résoudre d'abord
    SPOT_GROUPS = {"hu": studyspots.FAMILIES, "6max": studyspots.RING_FAMILIES}  # les onglets des séries

    @staticmethod
    def _pot_kind(family: str) -> str:
        """« srp », « 3bet » ou « 4bet »."""
        return studyspots.RING_FAMILIES[family]["kind"] if family in studyspots.RING_FAMILIES else family

    def group_spot_sets(self, group: str, start: bool = False) -> dict:
        """Toutes les séries d'un onglet (« hu » : SRP, pots 3bet et 4bet heads-up ; « 6max » : celles que tes charts
        couvrent) : leur état et la durée de ce qui manque ; start=True met en file tous les flops manquants, un flop
        de chaque série à tour de rôle (pots 4bet et 3bet d'abord) : chaque plan de jeu se dessine vite, au lieu
        d'une série après l'autre. KeyError pour un autre onglet."""
        families = list(self.SPOT_GROUPS[group])
        sets = {family: self.spot_set(family) for family in families}
        ready = postflop.status()["ready"]
        if start and ready:
            order = sorted(sets, key=lambda f: self.RING_ORDER.index(self._pot_kind(f)))
            todo = {f: [r for r in sets[f]["rows"] if not r["done"] and r.get("state") not in ("waiting", "running")]
                    for f in order}
            for k in range(max(map(len, todo.values()), default=0)):
                for family in order:
                    if k < len(todo[family]):
                        row = todo[family][k]
                        board = "".join(row["board"])
                        spot = studyspots.StudySpot(family, studyspots.cards_of(board))
                        self._queue_flop(family, board, spot, self.solves.lookup(spot), row["series"])
            sets = {family: self.spot_set(family) for family in families}
        rows = [(f, r) for f, state in sets.items() for r in state["rows"]]
        current = next(((f, r) for f, r in rows if r.get("state") == "running"), None)
        seconds = sum(studyspots.SOLVE_SECONDS[f] + (0 if r["sizes"] or not r["series"] else studyspots.CHOOSE_SECONDS[f])
                      for f, r in rows if not r["done"])
        summary = [{"family": f, "title": studyspots.family_title(f), "total": state["total"], "done": state["done"],
                    "busy": state["busy"], "error": state.get("error")} for f, state in sets.items()]
        out = {"group": group, "ready": ready, "total": len(rows), "done": sum(r["done"] for _, r in rows),
               "busy": sum(r.get("state") in ("waiting", "running") for _, r in rows),
               "waiting": sum(r.get("state") == "waiting" for _, r in rows), "seconds": seconds,
               "covered": sum(1 for x in summary if not x["error"]), "families": summary, "current": None}
        if current:
            family, row = current
            out["current"] = {"family": family, "title": studyspots.family_title(family), "board": "".join(row["board"]),
                              "texture": row["texture"], "mode": row.get("mode"), "progress": row.get("progress") or {},
                              "max_iterations": row.get("max_iterations")}
        return out

    def group_spot_cancel(self, group: str) -> dict:
        """Arrête toutes les résolutions des séries de cet onglet (en attente ou en cours)."""
        for family in self.SPOT_GROUPS[group]:
            for row in self.spot_set(family)["rows"]:
                if row.get("state") in ("waiting", "running"):
                    self.solves.cancel(row["job"])
        return self.group_spot_sets(group)

    def ring_spot_sets(self, start: bool = False) -> dict:
        """Toutes les séries 6-max (group_spot_sets)."""
        return self.group_spot_sets("6max", start)

    def ring_spot_cancel(self) -> dict:
        return self.group_spot_cancel("6max")

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
        if start and ready:  # les spots ne se construisent qu'ici, pour les mettre en file
            active = {v["hand"] for v in self.solves.active_for(todo.hand_ids)}
            for spot in todo:
                if spot.ident not in active:
                    self.solves.analyze(spot, review.save_digest)
        jobs = self.solves.active_for(todo.hand_ids)
        running = next((v for v in jobs if v["state"] == "running"), None)
        current = None if running is None else {"hand": running["hand"], "progress": running.get("progress"),
                                                "max_iterations": running.get("max_iterations"), "job": running["job"]}
        return {"total": len(done) + len(todo), "done": len(done), "busy": len({v["hand"] for v in jobs}),
                "current": current, "ready": ready}

    def review_cancel(self, villain: Optional[str] = None) -> dict:
        hands = self.hands_against(villain) if villain else self.regular_hands()[0]
        _, todo = review.collect(hands, self.hero)
        for view in self.solves.active_for(todo.hand_ids):
            self.solves.cancel(view["job"])
        return self.review_state(villain)

    # --- alias : plusieurs pseudos d'un même joueur ---------------------------------------
    def set_alias(self, alias: str, pseudos: list[str]) -> dict:
        """Regroupe ces pseudos sous un alias (pour tout le compte : tes mains et celles des élèves) ; ValueError si
        rien à regrouper."""
        found = aliases.group(alias, pseudos)
        self._reload_all()
        return {"aliases": found, "state": self.summary()}

    def remove_alias(self, alias: str) -> dict:
        found = aliases.ungroup(alias)
        self._reload_all()
        return {"aliases": found, "state": self.summary()}

    def _reload_all(self) -> None:
        """Relit tes mains et celles des élèves déjà ouverts (les noms des joueurs ont changé)."""
        self.reload()
        with self._lock:
            loaded = list(self._students.values())
        for lib in loaded:
            lib.reload()

    # --- historiques importés : liste, retrait, rétablissement --------------------------
    def files_view(self) -> list[dict]:
        """Les historiques importés dans cet espace (db/hands.files)."""
        return db_hands.files(self.db, self.space_id)

    def remove_file(self, file_id: int) -> dict:
        """Retire un historique (ses mains quittent les analyses) ; KeyError s'il n'est pas de cet espace."""
        found = db_hands.remove_file(self.db, self.space_id, file_id)
        self.reload()
        return dict(found, files=self.files_view(), state=self.summary())

    def restore_file(self, file_id: int) -> dict:
        """Rétablit un historique retiré ; KeyError s'il n'est pas de cet espace."""
        added = db_hands.restore_file(self.db, self.space_id, file_id)
        self.reload()
        return {"added": added, "files": self.files_view(), "state": self.summary()}

    # --- import -----------------------------------------------------------------
    def import_files(self, files: list[dict], job: Optional[ImportJob] = None) -> dict:
        """Importe dans la base les historiques reconnus (le texte d'origine est gardé ; un historique ou une main
        déjà importés ne le sont pas deux fois) ; job : son avancement, pour la barre de la page Importer.

        Chaque fichier : {"name", "content"} (texte), ou {"name", "zip"} (archive zip en base64, dossiers compris :
        chacun de ses historiques s'importe comme un fichier)."""
        items = files[:MAX_IMPORT_FILES]
        if job is not None:  # les mains à lire, comptées d'abord (les archives s'ouvrent deux fois : c'est rapide)
            job.plan([count_hands(text) for text in self._import_texts(items)])
        results: list[dict] = []
        added = 0
        for item in items:
            name = str(item.get("name") or "fichier")[:200]
            if isinstance(item.get("zip"), str):
                added += self._import_zip(name, item["zip"], results, job)
            else:
                added += self._import_one(name, item.get("content"), results, job)
        if added:
            self.reload(job.loading if job is not None else None)
        return {"files": results, "added": added, "state": self.summary()}

    @staticmethod
    def _import_texts(items: list[dict]):
        """Les textes d'un import, archives ouvertes (ceux qui ne se lisent pas sont laissés de côté)."""
        for item in items:
            if isinstance(item.get("zip"), str):
                try:
                    yield from (text for _, text in read_zip(base64.b64decode(item["zip"], validate=True)).files)
                except (binascii.Error, ValueError):
                    continue
            elif isinstance(item.get("content"), str):
                yield item["content"]

    def start_import(self, files: list[dict]) -> dict:
        """Lance l'import en arrière-plan (un à la fois) ; son avancement se lit avec import_status."""
        job = ImportJob(secrets.token_hex(8), db_hands.count(self.db, self.space_id))
        with self._lock:
            self._imports[job.id] = job
            for old in list(self._imports)[:-10]:  # les 10 derniers
                del self._imports[old]
            if self._import_pool is None:
                self._import_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import")
            pool = self._import_pool
        pool.submit(self._run_import, job, files)
        return job.view()

    def _run_import(self, job: ImportJob, files: list[dict]) -> None:
        try:
            job.finish(self.import_files(files, job))
        except Exception as exc:  # noqa: BLE001 — l'import échoue, la page le dit
            traceback.print_exc()
            job.fail(f"Import impossible : {exc}")

    def import_status(self, job_id: str) -> Optional[dict]:
        """L'avancement d'un import lancé par start_import (None : inconnu)."""
        job = self._imports.get(job_id)
        return job.view() if job is not None else None

    def _import_zip(self, name: str, data: str, results: list[dict], job: Optional[ImportJob] = None) -> int:
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
        added = sum(self._import_one(f"{name} › {path}"[:300], text, results, job) for path, text in content.files)
        results.extend({"name": f"{name} › {path}"[:300], "status": "illisible (chiffré ou abîmé)", "hands": 0, "new": 0}
                       for path in content.unreadable)
        parts = [f"{len(content.files)} historique(s)"]
        if content.ignored:
            parts.append(f"{content.ignored} autre(s) fichier(s) laissé(s) de côté")
        results.append({"name": name, "status": "archive : " + ", ".join(parts), "archive": True,
                        "hands": sum(r["hands"] for r in results[start:]), "new": added})
        return added

    def _import_one(self, name: str, content, results: list[dict], job: Optional[ImportJob] = None) -> int:
        if not isinstance(content, str) or not content.strip():
            results.append({"name": name, "status": "vide", "hands": 0, "new": 0})
            if job is not None and isinstance(content, str):
                job.file_done(0)
            return 0
        try:
            # le nom d'origine n'est qu'une étiquette (jamais un chemin) : celui du fichier, ou sa place dans l'archive
            label = name if " › " in name else Path(name.replace("\\", "/")).name
            hands, new, _ = db_hands.import_text(self.db, self.space_id, label, content,
                                                 on_hands=job.reading if job is not None else None)
        except ValueError:
            results.append({"name": name, "status": "format non reconnu", "hands": 0, "new": 0})
            return 0
        finally:
            if job is not None:
                job.file_done(count_hands(content))
        detail = {"sites": sorted({h.site for h in hands}), "formats": _formats(hands)}
        status = "importé" if new else "déjà importé" if hands else "aucune main lue"  # ex. des tournois
        results.append(dict(detail, name=name, status=status, hands=len(hands), new=new))
        return new
