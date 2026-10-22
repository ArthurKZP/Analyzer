"""Bibliothèque de mains de l'application : chargement du dossier, import, analyses en cache."""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html import escape
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from .. import (aliases, bluffcatch, bluffs, db, field, gains, handplay, leaks, memory, players, replay, ring,
               ring_leaks, settings, situations, spots, stakes, store, students, tracking)
from .. import period as periods
from .. import reference as refstudy
from ..db import analyses as db_analyses
from ..errors import NotFound
from ..db import documents
from ..db import hands as db_hands
from ..cli import detect_hero, unify_hero
from ..lines import villain_lines
from ..models import CALL, RAISE, Hand
from ..parsers import count_hands, decode, read_zip
from ..report import build_plan_page, build_report, format_query, format_switch, html_page
from ..selfreport import build_self_report, build_stats_page, opponent_results
from ..stats import analyze
from ..theory import (coach, custom_ranges, custom_tree, handclass, postflop, review, ring_preflop, ring_ranges, sizing,
                      stake_charts, studyspots)
from ..theory.page import build_preflop_page, build_ring_preflop_page
from ..viewer import build_viewer
from . import bilan_page, catch_trainer, gains_page, synthesis
from .report_pdf import build_pdf
from .report_pptx import build_pptx
from .bluffs_page import build_bluffs_page
from .import_job import ImportJob
from .field_leaks_page import build_group_page, build_list_page, build_player_page, stakes_switch
from .field_lines_page import build_crowd_page
from .field_stats_page import build_field_stats_page
from .field_page import build_players_page
from .ranges_page import build_ranges_page
from .settings_page import build_settings_page
from .hands_page import build_hands_page
from .leaks_page import build_leaks_page
from .tracking_page import build_tracking_page
from .reference_page import build_comparison_page, build_lines_page
from .ring_page import MAX_SPOTS as MAX_RING_SPOTS
from .ring_page import build_ring_page
from .plan_page import build_coach_page
from .review_page import build_review_page
from .situations_page import situations_html
from .solver_pages import PANEL_SCRIPT, build_solver_bilan, panel_html, slot_html
from .backups import Backups
from .coach_chat import Coach
from .solves import NeedSession, SolveQueue

PLAYER_PAGES = ("plan", "preflop", "rapport", "spots", "solveur", "bluffs")
HEADS_UP_ONLY = ('<p class="note">Aucune main heads-up : cette page analyse tes parties en heads-up. Tes tables à '
                 'plusieurs (3 à 9 joueurs, ensemble) ont leur bilan (avec leurs stats par position), leur Leakfinding, '
                 'leur préflop et leurs mains de départ : choisis « Tables à plusieurs » en haut de ces pages.</p>')
SELF_PAGES = ("bilan", "stats", "preflop", "spots", "solveur", "bluffs", "leaks", "tables", "mains", "suivi")
REFERENCE_PAGES = ("comparaison", "lignes", "bilan", "tables", "preflop", "mains")  # un joueur de référence
REFERENCE_PREFIX = "ref:"  # sa période d'analyse se garde sous cette clé
# Étude du field : leurs stats globales, puis réguliers et récréatifs, chacun avec son leakfinding, ses bluffs et sa
# value (« joueurs » : aussi dans Paramètres)
FIELD_PAGES = ("stats", "regs", "recs", "bluffs", "value", "rec-bluffs", "rec-value", "joueurs")
FIELD_LINES = {"bluffs": ("reg", "bluffs"), "value": ("reg", "value"), "rec-bluffs": ("rec", "bluffs"),
               "rec-value": ("rec", "value")}
MAX_IMPORT_FILES = 5000  # fichiers (ou archives zip) par import
MAX_REMOVED_PSEUDOS = 200  # pseudos dont on supprime les mains d'un coup


class UnknownPlayer(NotFound):
    pass


def _theory_changes() -> tuple:
    """Les révisions de ce qui fait la théorie : plans de jeu, charts, charts par limites (pour les caches)."""
    return documents.revision(db.current(), "plan", "ranges", stake_charts.KIND)[2:]


def _excluded_note(excluded: dict) -> str:
    if not excluded["hands"]:
        return ""
    return (f"{excluded['hands']} mains contre des récréatifs ({', '.join(excluded['players'])}) sont exclues : "
            "contre eux, l'exploitation prime sur la théorie.")


def _solver_match(status: Optional[dict], wanted: str) -> bool:
    """Le filtre « Solveur » du visualiseur : « done » (analysée), « todo » (à résoudre), « err » (une erreur)."""
    if status is None:
        return False
    if wanted == "todo":
        return "todo" in status
    return "todo" not in status and (wanted == "done" or status["errors"] > 0)


def _solver_sorts(status: dict[str, dict]) -> dict:
    """Le tri du visualiseur sur ce que dit le solveur : l'EV perdue, les plus chères d'abord (puis les autres)."""
    return {"evloss": (lambda r: (status.get(r["id"]) or {}).get("lost", -1.0), True)}


def _solver_summary(found: list[dict]) -> dict:
    """Ce que dit le solveur d'une sélection de mains : analysées, EV perdue et erreurs, mains à résoudre."""
    done = [s for s in found if "todo" not in s]
    return {"done": len(done), "todo": len(found) - len(done), "lost": round(sum(s["lost"] for s in done), 2),
            "errors": sum(s["errors"] for s in done),
            "minutes": round(sum(s["todo"] for s in found if "todo" in s), 1)}


def _note(text: str) -> str:
    return (f'<p class="note">{escape(text)} Le type de chaque adversaire se règle dans Paramètres › Joueurs et alias '
            "(ou en haut de sa fiche).</p>")


def _kind_note(kind: dict) -> str:
    if kind["kind"] != "rec":
        return ""
    return ("Joueur classé récréatif : tes décisions contre lui ne sont pas comparées à la théorie ; "
            "ses fréquences ci-dessous servent à l'exploiter.")



def _ring_replay(hand: Hand, api: str = "/api") -> str:
    """Revoir une main d'une table à plusieurs : au replayer, et au solveur quand elle est allée au flop à deux."""
    link = (f'<a class="spots-link" href="{escape(replay.href(hand.hand_id, api))}" target="_blank" '
            'rel="noopener">rejouer ↗</a>')
    if postflop.flop_pair(hand) is None:
        return link
    return (f'{link} <a class="spots-link" href="/explorateur/{quote(hand.hand_id, safe="")}" target="_blank" '
            'rel="noopener">solveur ↗</a>')


def _round(x: Optional[float], digits: int = 1) -> Optional[float]:
    return None if x is None else round(x, digits)


def _digest_count() -> int:
    """Le nombre de mains passées au solveur : les pages qui s'en servent sont recalculées quand il change."""
    return db_analyses.count(db.current())


def _solver_key() -> tuple:
    """Ce qui change quand une main passe au solveur (analyse, résolution, étude) ou quand ses réglages changent : les
    pages qui montrent tes mains face au solveur sont recalculées."""
    solved = postflop.solved_keys()
    return _digest_count(), len(solved[0]), len(solved[1]), review.signature()


_LABEL_RE = re.compile(r'(<(?:(?:th|h[1-4])\b[^>]*|div class="label")>)([^<]+)')
_SCRIPT_RE = re.compile(r"(<script\b.*?</script>)", re.S)
_HIS = (("Tes ", "Ses "), ("Ton ", "Son "), ("Ta ", "Sa "))


def as_reference(html: str, name: str) -> str:
    """Une page de ton jeu, pour celui d'un joueur de référence : ses colonnes et ses titres le nomment (« Toi » : son
    nom ; « Tes statistiques » : « Ses statistiques »…), hors des scripts ; le reste du texte garde le tutoiement."""
    who = escape(name)

    def label(found: re.Match) -> str:
        tag, text = found.group(1), found.group(2)
        if text == "Toi" or text.startswith("Toi ("):
            return tag + who + text[3:]
        for mine, his in _HIS:
            if text.startswith(mine):
                return tag + his + text[len(mine):]
        return found.group(0)

    return "".join(part if part.startswith("<script") else _LABEL_RE.sub(label, part)
                   for part in _SCRIPT_RE.split(html))


_WARMING = threading.Lock()  # un préchargement à la fois (toi, tes élèves)
_MISSING = object()  # (une page pas encore dans le cache)
_WARM = threading.local()  # .active : ce fil est celui du préchargement


class Library:
    """Les mains d'un dossier et les pages d'analyse, calculées à la demande puis gardées en cache."""

    def __init__(self, folder: Path | str, hero: Optional[str] = None, solves: Optional[SolveQueue] = None,
                 backups: Optional[Backups] = None, api: str = "/api", pages: str = "/moi", space: str = "moi",
                 space_name: str = "Moi", source: Optional[Callable[[], list[Hand]]] = None, warm_up: bool = False):
        """folder : la boîte d'arrivée des historiques (importés dans la base au chargement) ; space : l'espace de la
        base (« moi », ou « eleve:<identifiant> ») ; solves, backups : ceux de la bibliothèque principale, partagés par
        celles des élèves (une résolution à la fois) ; api, pages : préfixes des adresses de ses pages et de leurs
        actions. source : les mains d'un joueur de référence, lues dans ton espace (pas d'espace à lui ; space ne sert
        qu'à garder sa période) ; hero : son nom. warm_up : après chaque chargement, les pages principales se calculent
        en arrière-plan (l'application ; jamais dans les tests)."""
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = db.current()
        self._source = source
        self.space_id = None if source is not None else db_hands.space(self.db, space, space_name, hero)
        self.space_key = space
        self.period = periods.load(space)  # la période d'analyse de l'espace (toutes les mains par défaut)
        self._period_day = date.today()
        self.hero_override = hero
        self.version = 0
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._cache: dict[tuple, object] = {}
        self._solver_status: dict[bool, tuple] = {}  # format -> (clé, statuts) : le dernier calculé seulement
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
        self._references: dict[str, Library] = {}  # les joueurs de référence ouverts
        self._reference_hands: dict[str, list[Hand]] = {}  # leurs mains (nom -> mains), lues avec les tiennes
        self._snapshot: Optional[dict] = None  # ce qui a décidé de tes mains au dernier chargement (_load_new)
        self._imports: dict[str, ImportJob] = {}  # les imports suivis (barre d'avancement), les derniers
        self._import_pool: Optional[ThreadPoolExecutor] = None  # un import à la fois, en arrière-plan
        self._import_lock = threading.Lock()  # les historiques s'enregistrent un envoi à la fois
        self.solves = solves or SolveQueue()
        self.backups = backups or Backups()
        self.coach = Coach(self)
        self.warm_up = warm_up
        self._busy = 0  # pages en calcul pour une demande (le préchargement attend qu'il n'y en ait plus)
        if solves is None:
            self.solves.on_done.append(lambda job: self.backups.schedule())
        self.reload()

    # --- chargement -------------------------------------------------------------
    def reload(self, progress=None, new_only: bool = False) -> None:
        """Importe les historiques nouveaux du dossier, puis lit les mains de l'espace dans la base (progress(k, n) :
        k mains relues sur n). new_only : après un import, seulement les mains ajoutées depuis le dernier chargement
        (celles déjà en mémoire gardent ce qui a été lu d'elles), sauf si autre chose a changé (alias, pseudos, mains
        retirées…) : tout est alors relu."""
        if self._source is not None:  # un joueur de référence : ses mains, lues avec les tiennes
            return self._reload_reference()
        with memory.loading() as state:  # des millions d'objets : le ramasse-miettes attend la fin du chargement
            if new_only and self._load_new(progress):
                state.release = False  # rien de remplacé : rien à rendre
            else:
                self._load(progress)

    @staticmethod
    def _count(hands: list[Hand], pseudos: Counter, seen: Counter, unmarked: Counter) -> None:
        """Compte tes pseudos tels que tes historiques les marquent (pseudo, site), tous les joueurs, et ceux des mains
        sans héros marqué."""
        for h in hands:
            marked = h.hero
            seen.update(h.seats.keys())
            if marked:
                pseudos[(marked, h.site)] += 1
            else:
                unmarked.update(h.seats.keys())

    @staticmethod
    def _conf_key(conf: dict) -> tuple:
        """Ce qui, dans tes Paramètres, décide quelles mains sont les tiennes."""
        return (conf["hero"], tuple(sorted(conf["hero_excluded"])), tuple(sorted(conf["hero_added"])),
                tuple(sorted((ref["name"], tuple(sorted(ref["pseudos"]))) for ref in conf["references"])))

    def _load(self, progress=None) -> None:
        db_hands.sync_folder(self.db, self.space_id, self.folder)
        hands = db_hands.load(self.db, self.space_id, progress)
        mapping = aliases.load()  # les pseudos regroupés sous un alias prennent son nom
        aliases.apply(hands, mapping)
        pseudos: Counter = Counter()  # tes pseudos, tels que tes historiques les marquent
        seen: Counter = Counter()  # tous les joueurs (ton nom n'est pas le leur)
        unmarked: Counter = Counter()  # ceux des mains sans héros marqué
        self._count(hands, pseudos, seen, unmarked)
        if self.is_me:  # les mains de tes joueurs de référence : à part (Paramètres)
            hands = self._take_references(hands)
        conf = settings.load()
        snapshot: Optional[dict] = {"rows": db_hands.rows(self.db, self.space_id), "mapping": mapping,
                                    "conf": self._conf_key(conf)}
        if self.is_me and not self.hero_override:  # tes pseudos réunis sous ton nom (Paramètres)
            hero, mine, pseudo_set, owners = self._group_me(hands, pseudos, conf)
            snapshot.update(path="moi", mine=pseudo_set, owners=owners)
        elif self.chosen_pseudos():  # les pseudos choisis de l'élève : seules leurs mains, sous un seul nom
            chosen = self.chosen_pseudos()
            hero = self._chosen_hero(pseudos, chosen, mapping)
            mine = self._claim_chosen(hands, chosen, hero)
            snapshot = dict(snapshot, path="choisis", chosen=tuple(chosen))
        else:  # --hero (ligne de commande) ou le pseudo de l'élève, sinon le plus fréquent : ses pseudos sous celui-là
            hero = mapping.get(self.hero_override, self.hero_override) if self.hero_override else detect_hero(hands)
            unify_hero(hands, hero)
            mine = [h for h in hands if hero and hero in h.seats and (not h.hero or h.hero == hero)]
            snapshot = dict(snapshot, path="pseudo") if self.hero_override else None  # le plus fréquent : tout relire
        heads_up = [h for h in mine if len(h.seats) == 2 and h.button and h.bb]
        ring = [h for h in mine if len(h.seats) > 2 and h.button and h.bb]
        with self._lock:
            self._all_hands, self._all_ring = heads_up, ring
            self.by_id = {h.hand_id: h for h in heads_up + ring}  # le solveur résout aussi leurs pots à deux
            self.hero = hero
            self.hero_pseudos = pseudos
            self.players_seen, self.unmarked_seen = seen, unmarked
            self._snapshot = snapshot
            self._select()

    def _load_new(self, progress=None) -> bool:
        """Ajoute les mains importées depuis le dernier chargement, lues comme lui (alias, joueurs de référence, tes
        pseudos sous ton nom) ; False s'il faut tout relire : mains retirées ou relues, alias, Paramètres ou pseudos
        changés, ou ton nom (le pseudo le plus fréquent) qui en change."""
        snap = self._snapshot
        if snap is None:
            return False
        found = db_hands.load_after(self.db, self.space_id, snap["rows"])
        if found is None:
            return False
        new, rows = found
        mapping, conf = aliases.load(), settings.load()
        if mapping != snap["mapping"] or self._conf_key(conf) != snap["conf"]:
            return False
        aliases.apply(new, mapping)
        pseudos, seen, unmarked = Counter(self.hero_pseudos), Counter(self.players_seen), Counter(self.unmarked_seen)
        self._count(new, pseudos, seen, unmarked)
        if snap["path"] == "moi" and self.my_pseudos(pseudos, conf) != snap["mine"]:
            return False  # un pseudo de plus (ou de moins) : les mains d'avant sans héros marqué changent de main
        if self.is_me:  # les mains de tes joueurs de référence : à part, avec celles d'avant
            new = self._take_references(new, keep=True)
        if snap["path"] == "moi":
            owners = self._owners(new, snap["mine"])
            counts = snap["owners"] + Counter(me for _, me in owners)
            top = counts.most_common(2)
            name = conf["hero"] or (top[0][0] if top else None)
            if name != self.hero or (not conf["hero"] and len(top) > 1 and top[0][1] == top[1][1]):
                return False  # ton nom (le pseudo le plus fréquent) change, ou deux pseudos à égalité
            mine = self._claim(owners, name)
        elif snap["path"] == "choisis":  # les pseudos choisis de l'élève : comme au chargement
            if tuple(self.chosen_pseudos()) != snap["chosen"]:
                return False
            mine = self._claim_chosen(new, list(snap["chosen"]), self.hero)
            counts = snap.get("owners")
        else:  # le pseudo donné (un élève) : comme au chargement
            unify_hero(new, self.hero)
            mine = [h for h in new if self.hero and self.hero in h.seats and (not h.hero or h.hero == self.hero)]
            counts = snap.get("owners")
        heads_up = [h for h in mine if len(h.seats) == 2 and h.button and h.bb]
        ring = [h for h in mine if len(h.seats) > 2 and h.button and h.bb]
        order = lambda h: (h.date, h.hand_id)  # noqa: E731
        with self._lock:
            self._all_hands = sorted(self._all_hands + heads_up, key=order) if heads_up else self._all_hands
            self._all_ring = sorted(self._all_ring + ring, key=order) if ring else self._all_ring
            self.by_id.update({h.hand_id: h for h in heads_up + ring})
            self.hero_pseudos = pseudos
            self.players_seen, self.unmarked_seen = seen, unmarked
            self._snapshot = dict(snap, rows=rows, owners=counts)
            self._select()
        if progress is not None:
            progress(len(new), len(new))
        return True

    def _take_references(self, hands: list[Hand], keep: bool = False) -> list[Hand]:
        """Met à part les mains de tes joueurs de référence (celles où l'historique marque un de leurs pseudos comme
        héros), sous leur nom, et renvoie les autres ; leurs bibliothèques déjà ouvertes se relisent à la prochaine
        visite. keep : des mains ajoutées, mises à part avec celles d'avant."""
        refs = settings.load()["references"]
        owner = {p: ref["name"] for ref in refs for p in ref["pseudos"]}
        taken: dict[str, list[Hand]] = {ref["name"]: list(self._reference_hands.get(ref["name"], [])) if keep else []
                                        for ref in refs}
        rest = []
        for h in hands:
            name = owner.get(h.hero)
            if name is None:
                rest.append(h)
                continue
            h.rename(h.hero, name)  # sans effet si un autre joueur de la main porte ce nom : elle n'est pas comptée
            if h.hero == name:
                taken[name].append(h)
        with self._lock:
            self._reference_hands = taken
            self._references.clear()
        return rest

    def _reload_reference(self) -> None:
        """Les mains d'un joueur de référence (source), analysées comme les tiennes : il est le héros de chacune."""
        hero = self.hero_override
        mine = [h for h in self._source() if hero in h.seats]
        heads_up = [h for h in mine if len(h.seats) == 2 and h.button and h.bb]
        ring = [h for h in mine if len(h.seats) > 2 and h.button and h.bb]
        with self._lock:
            self._all_hands, self._all_ring = heads_up, ring
            self.by_id = {h.hand_id: h for h in heads_up + ring}
            self.hero = hero
            self.hero_pseudos = Counter((hero, h.site) for h in mine)
            self.players_seen = Counter(name for h in mine for name in h.seats)
            self.unmarked_seen = Counter()
            self._select()

    def chosen_pseudos(self) -> list[str]:
        """Les pseudos choisis de l'élève (vide : tous les héros de ses historiques ; jamais ailleurs que chez un
        élève)."""
        if not (self.space_key or "").startswith(students.PREFIX) or self.space_id is None:
            return []
        return students.pseudos(self.space_key[len(students.PREFIX):])

    def _chosen_hero(self, pseudos: Counter, chosen: list[str], mapping: dict) -> str:
        """Le nom de l'élève dans ses analyses : le pseudo donné à sa création, sinon celui de ses pseudos choisis qui
        a le plus de mains."""
        if self.hero_override:
            return mapping.get(self.hero_override, self.hero_override)
        counts: Counter = Counter()
        for (pseudo, _), n in pseudos.items():
            if pseudo in chosen:
                counts[pseudo] += n
        return counts.most_common(1)[0][0] if counts else chosen[0]

    @staticmethod
    def _claim_chosen(hands: list[Hand], chosen: list[str], hero: str) -> list[Hand]:
        """Les mains de l'élève : celles dont le héros marqué est un de ses pseudos, ou, sans héros marqué, où l'un
        d'eux joue ; ce pseudo y prend son nom."""
        wanted = set(chosen)
        mine = []
        for h in hands:
            marked = h.hero
            if marked:
                if marked not in wanted:
                    continue
                h.rename(marked, hero)
            else:
                present = [p for p in chosen if p in h.seats]
                if not present:
                    continue
                if hero not in h.seats:
                    h.rename(present[0], hero)
            if hero in h.seats:
                mine.append(h)
        return mine

    def chosen_formats(self) -> list[str]:
        """Les formats joués : les tiens (Paramètres), ceux de l'élève ; vide : tous."""
        if self.is_me:
            return settings.load()["formats"] or []
        if (self.space_key or "").startswith(students.PREFIX) and self.space_id is not None:
            return students.formats(self.space_key[len(students.PREFIX):])
        return []

    def set_formats(self, chosen: object) -> dict:
        """Les formats que joue l'élève (None : tous) ; ValueError : pas un élève, ou aucun format valide. Un format
        qu'il ne joue pas sort de ses onglets et de ses analyses ; ses mains restent gardées."""
        if not (self.space_key or "").startswith(students.PREFIX):
            raise ValueError("Seulement pour un élève.")
        students.set_formats(self.space_key[len(students.PREFIX):], chosen)
        with self._lock:
            self._select()
        return self.summary()

    def set_chosen_pseudos(self, names: object) -> dict:
        """Choisit les pseudos de l'élève parmi ceux de ses historiques (une liste vide : tous les héros de ses
        historiques) ; ValueError : pas un élève, ou un pseudo absent de ses mains."""
        if not (self.space_key or "").startswith(students.PREFIX):
            raise ValueError("Seulement pour un élève.")
        if not (isinstance(names, list) and len(names) <= students.MAX_PSEUDOS
                and all(isinstance(n, str) and 0 < len(n.strip()) <= 60 for n in names)):
            raise ValueError("Requête invalide.")
        known = {p for p, _ in self.hero_pseudos} | set(self.players_seen) | set(self.chosen_pseudos())
        unknown = [n.strip() for n in names if n.strip() not in known]
        if unknown:
            raise ValueError(f"« {unknown[0]} » ne joue dans aucune de ses mains.")
        students.set_pseudos(self.space_key[len(students.PREFIX):], names)
        self.reload()
        return {"pseudos": self.pseudos_view(), "files": self.files_view(), "state": self.summary()}

    @staticmethod
    def my_pseudos(pseudos: Counter, conf: dict) -> set[str]:
        """Tes pseudos : ceux que tes historiques marquent comme toi, sans ceux que tu as retirés, avec ceux que tu as
        ajoutés (Paramètres) ; jamais ceux d'un joueur de référence."""
        return (({p for p, _ in pseudos} - set(conf["hero_excluded"])) | set(conf["hero_added"])) \
            - settings.reference_pseudos(conf)

    def _group_me(self, hands: list[Hand], pseudos: Counter, conf: dict
                  ) -> tuple[Optional[str], list[Hand], set[str], Counter]:
        """Réunit tes pseudos sous ton nom : renvoie ce nom (celui que tu as choisi, sinon ton pseudo le plus fréquent),
        tes mains, tes pseudos et leur nombre de mains. Une main est à toi quand son héros (marqué par l'historique) est
        un de tes pseudos, ou, sans héros marqué, quand un de tes pseudos y joue : la main d'un pseudo décoché n'est
        plus la tienne."""
        mine = self.my_pseudos(pseudos, conf)
        if not mine:  # ni héros marqué ni pseudo ajouté : le joueur le plus fréquent
            guess = detect_hero(hands)
            mine = {guess} if guess else set()
        owners = self._owners(hands, mine)
        counts = Counter(me for _, me in owners)
        if not owners:
            return conf["hero"] or (min(mine, key=str.lower) if mine else None), [], mine, counts
        name = conf["hero"] or counts.most_common(1)[0][0]
        return name, self._claim(owners, name), mine, counts

    @staticmethod
    def _owners(hands: list[Hand], mine: set[str]) -> list[tuple[Hand, str]]:
        """Les mains où tu joues, et sous quel pseudo : le héros marqué par l'historique, sinon un de tes pseudos
        assis."""
        owners = []
        for h in hands:
            me = h.hero or next((p for p in h.seats if p in mine), None)
            if me in mine:
                owners.append((h, me))
        return owners

    @staticmethod
    def _claim(owners: list[tuple[Hand, str]], name: str) -> list[Hand]:
        """Tes mains sous ton nom (ta place marquée comme celle du héros)."""
        kept = []
        for h, me in owners:
            h.seats[me].is_hero = True  # sans héros marqué : ta place
            h.rename(me, name)  # sans effet si un autre joueur de la main porte ce nom : elle n'est alors pas comptée
            if h.hero == name:
                kept.append(h)
        return kept

    @property
    def is_me(self) -> bool:
        """Ton espace (pas celui d'un élève ni d'un joueur de référence) : tes Paramètres s'y appliquent."""
        return self.space_key == "moi" and self._source is None

    def _select(self) -> None:
        """Les mains de la période (sous self._lock) ; les analyses se refont (préchargées si warm_up)."""
        self._period_day = date.today()
        self.hands = periods.select(self._all_hands, self.period, self._period_day)
        self.ring = periods.select(self._all_ring, self.period, self._period_day)  # tables à plusieurs (3 à 9 joueurs)
        self.version += 1
        self._cache.clear()
        self._key_locks.clear()
        if self.warm_up:
            threading.Thread(target=self._warm, args=(self.version,), daemon=True, name="prechargement").start()

    # --- préchargement -----------------------------------------------------------
    WARM_PAGES = ("bilan", "leaks", "mains", "preflop", "tables", "spots")  # dans l'ordre des onglets de Mon jeu

    def _warm_tasks(self):
        """Ce que l'application demandera sans doute : l'état (le menu), puis les pages de Mon jeu dans l'ordre de leurs
        onglets, pour chaque format joué."""
        yield self.summary
        formats = [fmt for fmt, _ in self.leak_formats()]
        for page in self.WARM_PAGES:
            if page == "tables":
                if ring_leaks.FORMAT in formats:
                    yield lambda: self.self_page("tables")
            elif page in ("mains", "spots"):
                if page == "mains" or "HU" in formats:
                    yield lambda p=page: self.self_page(p, "HU")
            else:
                for fmt in formats:
                    yield (lambda p=page, f=fmt: self.leaks_page(table_format=f) if p == "leaks"
                           else self.self_page(p, f))

    def _warm(self, version: int) -> None:
        """Calcule les pages en arrière-plan, une à la fois et un préchargement à la fois (élèves compris) ; attend
        qu'aucune page demandée ne soit en calcul avant d'en commencer une ; s'arrête si les mains changent."""
        with _WARMING:
            _WARM.active = True
            try:
                for task in self._warm_tasks():
                    while self._busy and self.version == version:
                        time.sleep(0.2)
                    if self.version != version:
                        return
                    try:
                        task()
                    except Exception:  # noqa: BLE001 — une page qui ne se fait pas (pas de mains…) : la suivante
                        continue
            except Exception:  # noqa: BLE001 — la liste des pages (les formats joués) : rien à précharger
                return
            finally:
                _WARM.active = False

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
        if self.period["kind"] in periods.RELATIVE and self._period_day != date.today():  # un jour a passé
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
        owners = {p: ref["name"] for ref in conf["references"] for p in ref["pseudos"]}
        for row in by_name.values():
            row["reference"] = owners.get(row["name"])  # le pseudo d'un joueur de référence : pas le tien
            row["included"] = row["name"] not in excluded and row["reference"] is None
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
        if {"hero", "hero_excluded", "hero_added", "references"} & set(checked):
            self.reload()
        else:
            with self._lock:
                self._select()
        return self.summary()

    def _app_settings(self) -> dict:
        """Ce que le menu de l'application montre : les formats que tu joues (None : tous), les Élèves, le seuil
        de l'Étude du field."""
        conf = settings.load()
        return {"plays": self.chosen_formats() or None,
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
        """Calcule une seule fois par version, même si deux requêtes arrivent en même temps. Les mains peuvent changer
        pendant un long calcul (un import, la période, les Paramètres : le cache se vide) : le résultat est rendu à qui
        l'a demandé, et gardé seulement si la version n'a pas changé."""
        version = self.version
        full_key = (version,) + key
        found = self._cache.get(full_key, _MISSING)
        if found is not _MISSING:
            return found
        with self._lock:
            lock = self._key_locks.setdefault(full_key, threading.Lock())
        with lock:
            found = self._cache.get(full_key, _MISSING)
            if found is not _MISSING:
                return found
            asked = not getattr(_WARM, "active", False)  # une demande (pas le préchargement)
            if asked:
                with self._lock:
                    self._busy += 1
            try:
                found = build()
            finally:
                if asked:
                    with self._lock:
                        self._busy -= 1
            with self._lock:
                if self.version == version:
                    self._cache[full_key] = found
            store.flush()  # les calculs gardés sur disque (équités, spots…) sont écrits tout de suite
            return found

    def _analysis(self, player: str):
        def build():
            hands = self.hands_against(player)
            return hands, analyze(hands), villain_lines(hands, player, self.hero)
        return self._cached(("analysis", player), build)

    def player_page(self, player: str, page: str) -> str:
        if page not in PLAYER_PAGES:
            raise NotFound(page)
        hands = self.hands_against(player)  # 404 si le joueur est inconnu
        kind = self.kinds()[player]
        if page == "solveur":  # change au fil des analyses : jamais en cache
            return build_review_page(hands, self.hero, player, kind=kind, orders=self.review_orders(player))

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
                                         note=_note(f"{player} : {players.describe(kind)}."),
                                         catch=bluffcatch.spots(bluffcatch.faced(hands, [player], self.hero)))
            return build_viewer(hands, self.hero, player, embed=True, solver=True, api=f"{self.api}/coups",
                                records=self._spot_index(player))
        return self._cached(("player", player, page) + ((kind["kind"],) if page == "preflop" else ()), build)

    def self_page(self, page: str, table_format: Optional[str] = None, filters: Optional[dict] = None) -> str:
        """Une page de Mon jeu ; table_format : le heads-up (« HU ») ou les tables à plusieurs (« ring »), pour le
        Leakfinding et le préflop ; filters : le filtre des gains du bilan (gains.clean)."""
        filters = gains.clean(filters or {}) if page == "bilan" else {}
        if page not in SELF_PAGES:
            raise NotFound(page)
        if page == "tables":  # tables à 3 joueurs et plus : pas besoin de mains heads-up
            changes = _theory_changes()  # charts, plans des flops 6-max
            return self._cached(("self", "tables") + changes + self._ring_kinds_key() + _solver_key(), self._ring_page)
        if page == "mains":  # heads-up et tables à plusieurs ; les analyses du solveur s'y ajoutent
            key = (("self", "mains", tuple(sorted(ring_ranges.available().items())), _digest_count()) + self._kinds_key()
                   + self._ring_kinds_key())
            return self._cached(key, self._hands_page)
        if page == "leaks":  # change au fil des analyses : le rapport a son propre cache
            return self.leaks_page(table_format=table_format)
        if page == "solveur":  # le bilan du solveur, format par format : change au fil des analyses
            return self.solver_bilan()
        if page == "suivi":
            return self.tracking_page(table_format)
        if page == "preflop" and self._leak_format(table_format) != "HU":
            return self._ring_preflop_page()
        if page == "bilan" and self._leak_format(table_format) != "HU":
            return self._ring_bilan(filters)
        if page == "stats" and self._leak_format(table_format) != "HU":
            return self._ring_stats_page()
        if not self.hands:
            if self.ring:  # seulement des tables à plusieurs : la page heads-up le dit
                return html_page("Heads-up", HEADS_UP_ONLY, True)
            raise UnknownPlayer("moi")
        regular, excluded = self.regular_hands()

        def build():
            if page == "bilan":
                return build_self_report(self.hands, self.all_stats(), self.hero, embed=True, spots_href="",
                                         kinds=self.kinds(), head=self._bilan_head("HU", filters),
                                         leaks_href="" if self.pages.startswith("/reference/") else "leaks",
                                         gains=self._hu_gains(filters), stats_href=self._stats_href(),
                                         situations=self._situations_html("HU", filters))
            if page == "stats":  # toutes tes stats heads-up : toutes tes mains, contre les réguliers, les récréatifs
                recs = {h.hand_id for h in self.hands} - {h.hand_id for h in regular}
                others = [("Contre réguliers", analyze(regular).get(self.hero) if recs else None),
                          ("Contre récréatifs", analyze([h for h in self.hands if h.hand_id in recs]).get(self.hero)
                           if recs else None)]
                return build_stats_page(self.all_stats()[self.hero], self.hero, others,
                                        head=format_switch(self.leak_formats(), "HU"), hands=len(self.hands))
            if page == "preflop":  # les charts du niveau de limites où tu as le plus de mains
                band, charts = stake_charts.page_band("HU", regular)
                note = " ".join(x for x in (_excluded_note(excluded), charts) if x)
                return build_preflop_page(regular, self.hero, embed=True, spots_href="spots", note=note,
                                          switch=format_switch(self.leak_formats(), "HU"),
                                          solution=stake_charts.hu_solution(band))
            if page == "bluffs":
                return self._population_bluffs()
            panel = bool(self.hero) and "HU" in self.solver_formats()  # le panneau « Face au solveur »
            return build_viewer(self.hands, self.hero, None, embed=True, solver=True, api=f"{self.api}/coups",
                                records=self._spot_index(None),
                                top=slot_html(f"{self.api}/revue/panneau?format=HU") if panel else "",
                                extra_script=PANEL_SCRIPT if panel else "")
        kinds = self._kinds_key() if page in ("bilan", "stats", "preflop", "bluffs") else ()
        charts = stake_charts.revision()[2:] if page == "preflop" else ()
        return self._cached(("self", page) + kinds + charts + tuple(sorted(filters.items())), build)

    def _ring_overview(self) -> tuple[list, list]:
        """Tes stats aux tables à plusieurs (toutes ensemble) sur toutes tes mains, contre les réguliers et contre les
        récréatifs (ring.analyze), et tes écarts les plus importants contre les réguliers : pour la page des tables et le
        bilan."""
        def build():
            hero = self.hero or ""
            found = self._ring_found()
            scopes = [(scope, label, found[scope]) for scope, label in leaks.SCOPES]
            stats = ring_leaks.stats(self.ring, hero, self.ring_kinds(), plays=self._plays().get("ring"))
            return scopes, leaks.top_stat_gaps(stats, "reg", 5)
        key = ("ring_overview",) + _theory_changes() + self._ring_kinds_key()
        return self._cached(key, build)

    def _ring_found(self) -> dict:
        """Tes stats aux tables à plusieurs (ring.analyze, toutes ensemble) sur toutes tes mains, contre les réguliers
        et contre les récréatifs : pour le bilan, la page des tables et le Leakfinding."""
        def build():
            hero = self.hero or ""
            parts = ring_leaks.split(ring_leaks.mine(self.ring, hero), hero, self.ring_kinds())
            return {scope: ring.analyze(parts[scope], hero, merge=True) for scope, _ in leaks.SCOPES}
        return self._cached(("ring_found",) + self._ring_kinds_key(), build)

    def _ring_page(self) -> str:
        """Tes coups à deux joueurs au flop aux tables à plusieurs, à ouvrir au solveur."""
        scopes, _ = self._ring_overview()
        panel = slot_html(f"{self.api}/revue/panneau?format={ring_leaks.FORMAT}") if self.hero else ""
        return build_ring_page(scopes, self.hero or "", spots=self.ring_spots(MAX_RING_SPOTS),
                               ranges=ring_ranges.available(), status=self.solver_status(ring_game=True),
                               api=f"{self.api}/coups", panel=panel)

    def _bilan_head(self, fmt: str, filters: Optional[dict] = None) -> str:
        """En tête du bilan : le filtre des gains, tes résultats tous formats confondus (puis en heads-up et aux tables
        à plusieurs), puis le choix du format qui suit (le filtre gardé)."""
        hero = self.hero or ""
        filters = filters or {}
        heads_up, tables = gains.mine(self.hands, hero), gains.mine(ring_leaks.mine(self.ring, hero), hero)
        both = heads_up + tables
        kept_hu, kept_ring = gains.apply(heads_up, filters), gains.apply(tables, filters)
        kept = len(kept_hu) + len(kept_ring) if filters else len(both)
        bar = (f"<style>{gains_page.STYLE}</style>" + gains_page.filter_bar(both, filters, fmt)
               + gains_page.active_note(filters, kept, len(both)))
        return (bar + bilan_page.overall_html(bilan_page.results(kept_hu, hero), bilan_page.results(kept_ring, hero))
                + format_switch(self.leak_formats(), fmt, keep=gains.query(filters)))

    def _hu_gains(self, filters: dict) -> dict:
        """Le bilan heads-up filtré : tes résultats et ta courbe sur les mains que le filtre garde (rien à changer sans
        filtre), et la répartition de tes gains."""
        hero = self.hero or ""
        mine = gains.mine(self.hands, hero)
        out = {"detail": gains_page.breakdown_html(mine, hero, filters, "HU"), "script": gains_page.SCRIPT}
        if filters:
            res = bilan_page.results(gains.apply(mine, filters), hero)
            out.update(tiles=bilan_page.result_tiles(res), curve=res["curve"])
        return out

    def _ring_bilan(self, filters: Optional[dict] = None) -> str:
        """Ton bilan aux tables à plusieurs, sur le plan de celui du heads-up."""
        def build():
            hero = self.hero or ""
            scopes, gaps = self._ring_overview()
            labels = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}
            shown = [(scope, labels[scope], found[0] if found else None) for scope, _, found in scopes]
            return bilan_page.build_ring_bilan(ring_leaks.mine(self.ring, hero), hero, shown,
                                               self.ring_opponents_view(), gaps, head=self._bilan_head("ring", filters),
                                               filters=filters, stats_href=self._stats_href(),
                                               situations=self._situations_html(ring_leaks.FORMAT, filters))
        filters = filters or {}
        key = ("self", "bilan", "ring") + _theory_changes() + self._ring_kinds_key() + tuple(sorted(filters.items()))
        return self._cached(key, build)

    def _situations_html(self, fmt: str, filters: Optional[dict] = None) -> str:
        """Tes résultats par situation dans ce format (situations.build), face à tes repères (aux tables à plusieurs)
        et aux réguliers de ton field ; filters : le filtre des gains du bilan, sur tes mains comme sur les leurs."""
        if not self.hero:
            return ""
        hands = self.hands if fmt == "HU" else self.ring
        kinds = self.kinds() if fmt == "HU" else self.ring_kinds()
        regulars = [name for name, info in kinds.items() if info["kind"] == "reg"]
        kept = gains.apply(hands, filters) if filters else hands
        references = situations.RING_REFERENCES if fmt != "HU" else None
        return situations_html(situations.build(kept, self.hero, regulars, references))

    def _stats_href(self) -> str:
        """L'onglet de toutes tes stats, depuis le bilan (rien pour un joueur de référence : pas d'onglet)."""
        return "" if self.pages.startswith("/reference/") else "stats"

    def _ring_stats_page(self) -> str:
        """L'onglet Statistiques aux tables à plusieurs : toutes tes stats, toutes les positions."""
        def build():
            hero = self.hero or ""
            scopes, _ = self._ring_overview()
            labels = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}
            shown = [(scope, labels[scope], found[0] if found else None) for scope, _, found in scopes]
            return bilan_page.build_ring_stats(ring_leaks.mine(self.ring, hero), hero, shown,
                                               head=format_switch(self.leak_formats(), "ring"))
        key = ("self", "stats", "ring") + _theory_changes() + self._ring_kinds_key()
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
            band, charts = stake_charts.page_band(ring_leaks.FORMAT, parts["reg"])
            note = " ".join(x for x in (note, charts) if x)
            return build_ring_preflop_page(
                ring_preflop.collect(parts["reg"], hero, stake_charts.ring_lines(band)), hero, embed=True, note=note,
                switch=format_switch(self.leak_formats(), ring_leaks.FORMAT),
                replay=lambda hand: _ring_replay(hand, self.api),
                load_hint=" Charge-les dans Paramètres › Ranges préflop (« Charger les charts 6-max »).")
        key = (("self", "preflop", "ring") + documents.revision(db.current(), "ranges", stake_charts.KIND)[2:]
               + self._ring_kinds_key())
        return self._cached(key, build)

    def _plays(self) -> dict[str, list]:
        """Tes mains de départ lues (handplay), en heads-up et aux tables à plusieurs (3 à 9 joueurs ensemble), avec
        l'EV perdue après le flop des coups déjà passés au solveur."""
        def build():
            if not self.hero:
                return {}
            theory = stake_charts.theory()  # les charts du niveau de limites de chaque main
            losses = review.hero_losses(review.saved_digests())
            plays = {"HU": handplay.collect(self.hands, self.hero, theory, losses),
                     "ring": handplay.collect(self.ring, self.hero, theory, losses)}
            return {k: v for k, v in plays.items() if v}
        return self._cached(("plays", tuple(sorted(ring_ranges.available().items())), _digest_count())
                            + stake_charts.revision()[2:], build)

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
            raise NotFound(name)
        actions = {"agg": ("raise", "allin"), "pas": ("call", "check")}.get(query.get("act") or "")
        situation = query.get("sit") or "all"
        if situation != "all" and situation not in dict(handplay.SITUATIONS):
            raise NotFound(situation)
        return handplay.detail(plays, self._kind_map() if query.get("fmt", "HU") == "HU" else self._ring_versus(),
                               name=name,
                               kind=query.get("kind") or "", position=query.get("pos") or "", situation=situation,
                               actions=actions)

    # --- visualiseur de spots : recherche côté serveur -----------------------------------
    SPOT_FILTERS = ("opp", "pot", "pfa", "pos", "reach", "st", "cb", "h", "v", "l", "end", "known", "res", "sort", "q")
    SOLVER_FILTERS = ("done", "todo", "err")  # le filtre « Solveur » : analysées, à résoudre, avec une erreur

    def _spot_index(self, villain: Optional[str]) -> list[dict]:
        """Les fiches de tes mains (sans le détail), contre cet adversaire ou tous."""
        def build():
            hands = self.hands_against(villain) if villain else self.hands
            return spots.list_records(hands, self.hero, villain) if self.hero else []
        return self._cached(("spot_index", villain or ""), build)

    def _spot_selection(self, query: dict[str, str]) -> tuple[list[dict], dict, dict[str, dict]]:
        """Les fiches de la sélection du visualiseur (ses filtres, dont « sv » : face au solveur), ses filtres et le
        statut de tes mains face au solveur (solver_status)."""
        index = self._spot_index(query.get("adversaire") or None)
        filters = {k: query[k] for k in self.SPOT_FILTERS if query.get(k)}
        if "reach" in filters and not filters["reach"].isdigit():
            del filters["reach"]
        status = self.solver_status()
        wanted = query.get("sv") if query.get("sv") in self.SOLVER_FILTERS else None
        found = [r for r in index if spots.matches(r, filters)
                 and (wanted is None or _solver_match(status.get(r["id"]), wanted))]
        return found, filters, status

    def spots_search(self, query: dict[str, str]) -> dict:
        """Une page de mains du visualiseur : filtres (ceux de la page), tri, offset, limit ; adversaire : ses mains.
        Chaque ligne porte ce qu'en dit le solveur (« sv ») ; la sélection, combien de ses mains sont analysées."""
        found, filters, status = self._spot_selection(query)
        number = lambda key, default: int(query[key]) if (query.get(key) or "").isdigit() else default  # noqa: E731
        out = spots.search(found, filters, number("offset", 0), number("limit", 150), extra_sorts=_solver_sorts(status))
        for row in out["rows"]:
            if row["id"] in status:
                row["sv"] = status[row["id"]]
        out["solver"] = _solver_summary([status[r["id"]] for r in found if r["id"] in status])
        return out

    def solver_status(self, ring_game: bool = False) -> dict[str, dict]:
        """Tes mains allées au flop face au solveur (review.statuses) : en heads-up, ou tes pots à deux joueurs au
        flop aux tables à plusieurs. Recalculé quand une analyse, une résolution ou un réglage du solveur change."""
        if not self.hero:
            return {}
        key = (self.version,) + _solver_key()
        with self._lock:
            known = self._solver_status.get(ring_game)
        if known is not None and known[0] == key:
            return known[1]
        hands = [h for h in self.ring if self.hero in (postflop.flop_pair(h) or ())] if ring_game else self.hands
        found = review.statuses(hands, self.hero)
        with self._lock:
            self._solver_status[ring_game] = (key, found)
        return found

    def spots_solve(self, query: dict[str, str], action: str = "etat", limit: Optional[int] = None,
                    ids: Optional[list[str]] = None) -> dict:
        """Les mains de la sélection du visualiseur (ses filtres) ou ces mains (ids, des tables à plusieurs aussi) face
        au solveur : « etat » (l'avancement), « resoudre » (met en file les limit premières qui restent à résoudre, dans
        l'ordre de la liste ; toutes sans limit), « arreter »."""
        if ids is not None:
            status = {**self.solver_status(), **self.solver_status(ring_game=True)}
            chosen = [i for i in ids if i in status]
        else:
            found, _, status = self._spot_selection(query)
            sorts = {**spots.SORTS, **_solver_sorts(status)}
            key, reverse = sorts.get(query.get("sort") or "date", spots.SORTS["date"])
            found.sort(key=key, reverse=reverse)
            chosen = [r["id"] for r in found if r["id"] in status]
        todo = [i for i in chosen if "todo" in status[i]]
        if action == "resoudre":
            self._queue_hands(todo[:limit] if limit else todo)
        elif action == "arreter":
            for view in self.solves.active_for(todo):
                self.solves.cancel(view["job"])
        return dict(self._solve_view(todo, len(chosen) - len(todo)),
                    minutes=round(sum(status[i]["todo"] for i in todo), 1))

    def hand_fiche(self, hand_id: str) -> dict:
        """La fiche complète d'une de tes mains heads-up (pour la rejouer)."""
        hand = self.by_id.get(hand_id)
        if hand is None or not self.hero or self.hero not in hand.seats or len(hand.seats) != 2 or not hand.button:
            raise NotFound(hand_id)
        return spots.hand_record(hand, self.hero, hand.opponent_of(self.hero))

    # --- étude du field -------------------------------------------------------------------
    # Chaque adversaire est étudié à un niveau de limites (stakes) : ses mains de ce niveau, face aux repères et aux
    # exploits de ce niveau (field.band_defs) ; les pages partent du niveau où tu as le plus de mains.
    def field_page(self, page: str, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """Étude du field : le leakfinding, les bluffs et la value des réguliers et des récréatifs, tes adversaires et
        leur type."""
        if page not in FIELD_PAGES:
            raise NotFound(page)
        if not self.hands and not self.ring:
            raise UnknownPlayer("moi")
        if page == "stats":
            return self.field_stats(table_format, band)
        if page in ("regs", "recs"):
            return self.field_list(page[:3], table_format, band)
        if page in FIELD_LINES:
            return self.field_lines(*FIELD_LINES[page], table_format, band)
        return build_players_page(self.summary()["opponents"] if self.hands else [], self.ring_opponents_view(),
                                  aliases.groups())

    def _field_format(self, table_format: Optional[str]) -> str:
        """Le format d'une page du field : celui demandé s'il a des mains, sinon le premier que tu joues."""
        formats = [fmt for fmt, _ in self.leak_formats()]
        if not formats:
            raise UnknownPlayer("aucune main")
        wanted = "HU" if table_format == "HU" else ring_leaks.FORMAT if table_format else None
        return wanted if wanted in formats else formats[0]

    def field_bands(self, fmt: str) -> list[tuple[str, int]]:
        """Les niveaux de limites de tes mains d'un format, et leur nombre."""
        return self._cached(("field_bands", fmt), lambda: stakes.counts(self.hands if fmt == "HU" else self.ring))

    def _field_band(self, fmt: str, wanted: Optional[str]) -> str:
        band = stakes.choose(wanted, self.field_bands(fmt))
        if band is None:
            raise UnknownPlayer("aucune main")
        return band

    def _band_hands(self, fmt: str, band: str) -> list[Hand]:
        """Tes mains d'un format à un niveau de limites (toutes, s'il n'y en a qu'un)."""
        hands = self.hands if fmt == "HU" else self.ring
        if len(self.field_bands(fmt)) <= 1:
            return hands
        return self._cached(("band_hands", fmt, band), lambda: stakes.select(hands, band))

    def _field_rows(self, fmt: str, band: str) -> list[dict]:
        """Tes adversaires d'un format à un niveau de limites, du plus joué au moins joué : mains, ton résultat,
        type."""
        def build():
            if not self.hero:
                return []
            hands = self._band_hands(fmt, band)
            return opponent_results(hands, self.hero) if fmt == "HU" else ring.opponents(hands, self.hero)
        kinds = self.kinds() if fmt == "HU" else self.ring_kinds()
        return [{"name": o["name"], "hands": o["hands"], "net_bb": o["net_bb"], "bb100": o["bb100"],
                 "info": kinds[o["name"]]} for o in self._cached(("field_rows", fmt, band), build)
                if o["name"] in kinds]

    def _field_index(self, fmt: str, band: str) -> dict[str, list[Hand]]:
        """Les mains d'un format et d'un niveau par joueur (pour étudier chacun sans relire toutes les mains)."""
        def build():
            index: dict[str, list[Hand]] = {}
            for h in self._band_hands(fmt, band):
                for name in h.seats:
                    index.setdefault(name, []).append(h)
            return index
        return self._cached(("field_index", fmt, band), build)

    def _band_stats(self, band: str) -> dict:
        """Les stats heads-up de tes adversaires à un niveau de limites."""
        if len(self.field_bands("HU")) <= 1:
            return self.all_stats()
        return self._cached(("stats", band), lambda: analyze(self._band_hands("HU", band)))

    def field_study(self, fmt: str, names: tuple, band: str) -> "field.Study":
        """L'étude d'un adversaire ou d'un groupe à un niveau de limites (field.study), gardée en cache."""
        def build():
            index = self._field_index(fmt, band)
            hands = list({h.hand_id: h for n in names for h in index.get(n, [])}.values())
            hands.sort(key=lambda h: h.date)
            if fmt == "HU":
                stats = self._band_stats(band)
                found = [stats[n] for n in names if n in stats]
                ps = found[0] if len(found) == 1 else field.merge_stats(found)
                return field.study(hands, names, "HU", ps, band)
            return field.study(hands, names, "ring", band=band)
        return self._cached(("field_study", fmt, band, names), build)

    def _field_switch(self, fmt: str, band: str) -> str:
        return format_switch(self.leak_formats(), fmt) + stakes_switch(self.field_bands(fmt), band, fmt)

    def field_list(self, kind: str, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """Les réguliers (« reg ») ou les récréatifs (« rec ») que tu croises le plus à un niveau de limites, avec leurs
        leaks à exploiter ; les récréatifs aussi par style."""
        fmt = self._field_format(table_format)
        band = self._field_band(fmt, band)
        min_hands = settings.load()["min_hands"]
        kinds_key = self._kinds_key() if fmt == "HU" else (self._ring_kinds_key(),)

        def build():
            every = [r for r in self._field_rows(fmt, band) if r["info"]["kind"] == kind]
            rows = [(r, self.field_study(fmt, (r["name"],), band)) for r in every if r["hands"] >= min_hands]
            names = tuple(r["name"] for r, _ in rows)
            population = self.field_study(fmt, names, band) if len(names) > 1 else None
            groups = []
            if kind == "rec":
                for style in field.STYLE_ORDER:
                    members = [r for r, st in rows if st.style[0] == style]
                    if members:
                        groups.append({"style": style, "members": members,
                                       "study": self.field_study(fmt, tuple(m["name"] for m in members), band)})
            return build_list_page(kind, fmt, rows, population, min_hands, len(every), self._field_switch(fmt, band),
                                   groups, band=band)
        return self._cached(("field", kind, fmt, band, min_hands) + tuple(kinds_key), build)

    def field_stats(self, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """Les stats globales de tes adversaires d'un format à un niveau de limites : réguliers, récréatifs, tous, face
        aux repères de ce niveau et à toi ; aux tables à plusieurs, par position ; puis tes écarts que leurs réponses
        justifient et ceux qui te rapporteraient (le Leakfinding de ce format)."""
        fmt = self._field_format(table_format)
        band = self._field_band(fmt, band)
        kinds_key = self._kinds_key() if fmt == "HU" else (self._ring_kinds_key(),)
        hero = self.hero or ""

        def build():
            rows = self._field_rows(fmt, band)
            groups = {kind: [r["name"] for r in rows if r["info"]["kind"] == kind] for kind in ("reg", "rec")}
            groups["all"] = groups["reg"] + groups["rec"]
            index = self._field_index(fmt, band)
            players = {k: len(names) for k, names in groups.items()}
            hands = {k: sum(len(index.get(n, [])) for n in names) for k, names in groups.items()}
            labels = {"reg": "Réguliers", "rec": "Récréatifs", "all": "Tous"}
            # les populations qui ont des joueurs (« Tous » : quand il y a des deux)
            shown = [k for k in ("reg", "rec") if groups[k]] + (["all"] if groups["reg"] and groups["rec"] else [])
            positions = None
            if fmt == "HU":
                stats = self._band_stats(band)
                merged = {k: field.merge_stats([stats[n] for n in names if n in stats]) for k, names in groups.items()}
                columns = [(labels[k], dict(merged[k].ratios)) for k in shown]
                columns.append(("Toi", dict(stats[hero].ratios) if hero in stats else {}))
            else:
                hands_list = self._band_hands(fmt, band)
                columns = [(labels[k], field.ring_ratios(hands_list, groups[k])) for k in shown]
                columns.append(("Toi", field.ring_ratios(hands_list, [hero]) if hero else {}))
                found = [(f"pop-{k}", labels[k], ring.population(hands_list, set(groups[k]))) for k in shown]
                positions = [x for x in found if x[2] is not None]
            report = self.leaks_report(fmt) if hero else None
            return build_field_stats_page(fmt, band, self._field_switch(fmt, band), columns, players, hands, positions,
                                          report.exploits if report else [], report.opportunities if report else [],
                                          leaks_href=f"{self.pages}/leaks{format_query(fmt)}")
        return self._cached(("field_stats", fmt, band, _digest_count()) + _theory_changes() + tuple(kinds_key), build)

    def field_lines(self, kind: str, focus: str, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """Les bluffs (focus « bluffs ») ou la value (« value ») des réguliers (« reg ») ou des récréatifs (« rec »)
        d'un format et d'un niveau de limites, tous ensemble ; en heads-up, les bluffs face au solveur
        (bluffs.analyze)."""
        fmt = self._field_format(table_format)
        band = self._field_band(fmt, band)
        kinds_key = self._kinds_key() if fmt == "HU" else (self._ring_kinds_key(),)
        switch = self._field_switch(fmt, band)

        def build():
            names = tuple(r["name"] for r in self._field_rows(fmt, band) if r["info"]["kind"] == kind)
            if fmt == "HU" and focus == "bluffs":
                return self._population_bluffs(kind, band, switch)
            study = self.field_study(fmt, names, band) if names else None
            patterns = []
            if fmt == "HU" and names:  # là où ils misent bien moins que la théorie : de la value
                voice = bluffs.Voice(f"les {'réguliers' if kind == 'reg' else 'récréatifs'}", plural=True)
                report = bluffs.analyze(self._band_hands(fmt, band), names, self.hero, voice)
                patterns = [p for p in report.patterns if p.direction == "moins"]
            return build_crowd_page(kind, focus, fmt, study, switch, band, patterns)
        return self._cached(("field_lines", kind, focus, fmt, band) + tuple(kinds_key), build)

    def field_player(self, name: str, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """La fiche d'un adversaire dans l'Étude du field, à un niveau de limites (celui demandé, sinon celui où il
        a le plus de mains) : ses leaks, sa value et ses bluffs par ligne."""
        found = {}  # format -> {niveau: sa ligne}
        for fmt, _ in self.leak_formats():
            by_band = {b: r for b, _ in self.field_bands(fmt) for r in self._field_rows(fmt, b) if r["name"] == name}
            if by_band:
                found[fmt] = by_band
        if not found:
            raise UnknownPlayer(name)
        wanted = "HU" if table_format == "HU" else ring_leaks.FORMAT if table_format else None
        fmt = wanted if wanted in found else next(iter(found))
        mine = found[fmt]
        band = stakes.choose(band, [(b, r["hands"]) for b, r in mine.items()])
        row = mine[band]
        switch = format_switch([(f, sum(r["hands"] for r in rows.values())) for f, rows in found.items()], fmt)
        switch += stakes_switch([(b, r["hands"]) for b, r in mine.items()], band, fmt)
        fiche = f"/#/adversaire/{quote(name, safe='')}/plan" if "HU" in found else ""
        return build_player_page(row, self.field_study(fmt, (name,), band), fmt, row["info"], switch, fiche, band=band)

    def field_group(self, style: str, table_format: Optional[str] = None, band: Optional[str] = None) -> str:
        """La fiche d'un groupe de récréatifs (un style) à un niveau de limites : ses joueurs, son plan, ses leaks et
        ses lignes réunis."""
        if style not in field.STYLES:
            raise NotFound(style)
        fmt = self._field_format(table_format)
        band = self._field_band(fmt, band)
        min_hands = settings.load()["min_hands"]
        rows = [r for r in self._field_rows(fmt, band) if r["info"]["kind"] == "rec" and r["hands"] >= min_hands]
        members = [(r, st) for r in rows for st in [self.field_study(fmt, (r["name"],), band)] if st.style[0] == style]
        if not members:
            raise UnknownPlayer(style)
        study = self.field_study(fmt, tuple(r["name"] for r, _ in members), band)
        return build_group_page(style, fmt, study, members, self._field_switch(fmt, band), band=band)

    def _population_bluffs(self, kind: str = "reg", band: Optional[str] = None, head: str = "") -> str:
        """Les bluffs des réguliers (ou des récréatifs) en heads-up, ensemble puis un par un ; band : leurs mains d'un
        niveau de limites (toutes sinon) ; head : le choix du format et des limites."""
        crowd, others = ("réguliers", "récréatifs") if kind == "reg" else ("récréatifs", "réguliers")
        hands = self.hands if band is None else self._band_hands("HU", band)
        index = None if band is None else self._field_index("HU", band)
        names = [n for n, info in self.kinds().items() if info["kind"] == kind]
        if index is not None:
            names = [n for n in names if index.get(n)]
        rest = sum(1 for n, info in self.kinds().items() if info["kind"] != kind and (index is None or index.get(n)))
        if not names:
            return build_crowd_page(kind, "bluffs", "HU", None, head, band)
        rows = []
        for name in names:
            report = bluffs.analyze(self.hands_against(name) if index is None else index[name], [name], self.hero)
            top = next((p for p in report.patterns if p.confidence == "solide" and p.direction == "plus"), None)
            rows.append({"name": name, "hands": report.hands, "shown": len(report.shown),
                         "river": sum(1 for s in report.shown if s.street == "river" and s.bluff),
                         "top": top.title if top else None})
        note = (f"Les {len(names)} {crowd} ensemble (chacun dans « Adversaire par adversaire », en bas de page)."
                if len(names) > 1 else f"Un seul joueur parmi les {crowd}.")
        if rest:
            note += f" Les {others} ({rest}) ont leur propre page : {others.capitalize()} › Bluffs."
        note += " Là où leurs mises sont surtout de la value : le sous-onglet Value."
        report = bluffs.analyze(hands, names, self.hero, bluffs.Voice(f"les {crowd}", plural=True))
        profile = {p.name: p for p in report.profiles}
        for row in rows:
            prof = profile.get(row["name"])
            row.update(weight=prof.weight if prof else None, share=prof.share if prof else None,
                       group=prof.group if prof else None)
        groups = []
        for cluster in report.clusters:  # ce qui ressort de chaque groupe, ses membres pesant chacun au plus 1
            sub = (bluffs.analyze(hands, cluster.names, self.hero, bluffs.Voice("les joueurs du groupe", plural=True))
                   if len(cluster.members) > 1 else None)
            found = [p for p in (sub.patterns if sub else []) if p.direction == "plus"]
            groups.append({"cluster": cluster, "patterns": found[:3]})
        return build_bluffs_page(report, f"des {crowd}", note=_note(note),
                                 players=sorted(rows, key=lambda r: -r["hands"]), groups=groups,
                                 catch=bluffcatch.spots(bluffcatch.faced(hands, names, self.hero)), head=head,
                                 direction="plus", crowd=crowd)

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
                          space_name=meta["name"], warm_up=self.warm_up)
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

    # --- joueurs de référence --------------------------------------------------------------
    def references_conf(self) -> list[dict]:
        """Tes joueurs de référence (Paramètres), avec leur identifiant d'adresse."""
        return [dict(ref, id=settings.reference_id(ref["name"])) for ref in settings.load()["references"]]

    def reference(self, ident: str) -> "Library":
        """La bibliothèque d'un joueur de référence (ouverte à la première visite) : ses mains, lues dans ton espace,
        analysées comme les tiennes ; elle partage la file du solveur."""
        meta = next((r for r in self.references_conf() if r["id"] == ident), None)
        if meta is None:
            raise UnknownPlayer(ident)
        with self._lock:
            lib = self._references.get(ident)
        if lib is None:
            name = meta["name"]
            lib = Library(self.folder, name, solves=self.solves, backups=self.backups, api=f"/api/references/{ident}",
                          pages=f"/reference/{ident}", space=REFERENCE_PREFIX + ident, space_name=name,
                          source=lambda: self._reference_hands.get(name, []), warm_up=self.warm_up)
            lib.display_name = name
            with self._lock:
                lib = self._references.setdefault(ident, lib)
        return lib

    def references_summary(self) -> list[dict]:
        """Tes joueurs de référence : nom, pseudos (sites et mains), mains et winrate de chaque format, dates."""
        sites: dict[str, list[str]] = {}
        for (pseudo, site), _ in self.hero_pseudos.most_common():
            sites.setdefault(pseudo, []).append(site)
        out = []
        for meta in self.references_conf():
            lib = self.reference(meta["id"])
            every = lib._all_hands + lib._all_ring
            heads_up, tables = refstudy.result(lib._all_hands, lib.hero), refstudy.result(lib._all_ring, lib.hero)
            first = min((h.date for h in every), default=None)
            last = max((h.date for h in every), default=None)
            out.append({"id": meta["id"], "name": meta["name"],
                        "pseudos": [{"name": p, "sites": sites.get(p, []),
                                     "hands": sum(n for (q, _), n in self.hero_pseudos.items() if q == p)}
                                    for p in meta["pseudos"]],
                        "hands": heads_up.hands, "ring_hands": tables.hands,
                        "bb100": _round(heads_up.bb100), "ring_bb100": _round(tables.bb100),
                        "sites": sorted({h.site for h in every}),
                        "first": first.strftime("%d/%m/%Y") if first else None,
                        "last": last.strftime("%d/%m/%Y") if last else None})
        return out

    def references_view(self) -> dict:
        """La page des joueurs de référence : les tiens (references_summary) et les pseudos qui peuvent le devenir,
        ceux que tes historiques marquent comme héros (sites, mains ; « mine » : un de tes pseudos aujourd'hui)."""
        conf = settings.load()
        taken, mine = settings.reference_pseudos(conf), self.my_pseudos(self.hero_pseudos, conf)
        rows: dict[str, dict] = {}
        for (pseudo, site), n in self.hero_pseudos.most_common():
            if pseudo in taken:
                continue
            row = rows.setdefault(pseudo, {"name": pseudo, "sites": [], "hands": 0, "mine": pseudo in mine})
            row["hands"] += n
            if site not in row["sites"]:
                row["sites"].append(site)
        candidates = sorted(rows.values(), key=lambda r: (r["mine"], -r["hands"], r["name"].lower()))
        return {"references": self.references_summary(), "candidates": candidates, "hero": self.hero}

    def create_reference(self, name: object, pseudos: object) -> dict:
        """Nouveau joueur de référence : un nom et ses pseudos, parmi ceux que tes historiques marquent comme héros. Ses
        pseudos sortent des tiens (Paramètres › Toi) ; ValueError si la demande ne va pas."""
        conf = settings.load()
        refs = conf["references"]
        wanted = settings.check({"references": refs + [{"name": name, "pseudos": pseudos}]})["references"][-1]
        marked = {p for p, _ in self.hero_pseudos}
        for p in wanted["pseudos"]:
            if p not in marked:
                raise ValueError(f"« {p} » n'est le héros d'aucun de tes historiques : choisis un pseudo dont tu as "
                                 "importé les mains (toutes ses cartes y sont connues).")
        if wanted["name"] == self.hero:
            raise ValueError(f"« {wanted['name']} » est ton nom (tes pseudos réunis) : choisis un autre nom.")
        if wanted["name"] in self.players_seen and wanted["name"] not in wanted["pseudos"]:
            raise ValueError(f"« {wanted['name']} » est déjà le nom d'un joueur de tes mains : choisis un autre nom.")
        excluded = sorted(set(conf["hero_excluded"]) | set(wanted["pseudos"]), key=str.lower)
        added = [p for p in conf["hero_added"] if p not in wanted["pseudos"]]
        settings.save({"references": refs + [wanted], "hero_excluded": excluded, "hero_added": added})
        self.reload()
        return dict(self.references_view(), id=settings.reference_id(wanted["name"]), state=self.summary())

    def remove_reference(self, ident: str) -> dict:
        """Retire un joueur de référence : ses pseudos restent hors des tiens (recoche-les dans Paramètres › Toi s'ils
        sont à toi)."""
        conf = settings.load()
        kept = [r for r in conf["references"] if settings.reference_id(r["name"]) != ident]
        if len(kept) == len(conf["references"]):
            raise UnknownPlayer(ident)
        settings.save({"references": kept})
        self.reload()
        return dict(self.references_view(), state=self.summary())

    def reference_page(self, ident: str, page: str, table_format: Optional[str] = None,
                       filters: Optional[dict] = None) -> str:
        """Une page d'un joueur de référence : « comparaison » (toi et lui), « lignes » (sa value et ses bluffs), ou une
        page de son jeu analysée comme les tiennes (bilan, tables, préflop, mains de départ)."""
        if page not in REFERENCE_PAGES:
            raise NotFound(page)
        lib = self.reference(ident)
        if not table_format and lib.leak_formats():  # par défaut, le format où il a le plus de mains
            table_format = max(lib.leak_formats(), key=lambda f: f[1])[0]
        if page not in ("comparaison", "lignes"):  # son jeu, analysé comme le tien
            name = lib.hero or ""
            note = (f'<p class="note" style="margin:0 0 12px">Le jeu de <b>{escape(name)}</b>, analysé comme le tien : '
                    "les colonnes et les titres portent son nom, et dans les textes, « tu », « ton » et « tes » le "
                    "désignent aussi.</p>")
            html = as_reference(lib.self_page(page, table_format=table_format, filters=filters), name)
            return html.replace("<main>\n", "<main>\n" + note, 1)
        fmt = lib._leak_format(table_format)  # UnknownPlayer s'il n'a pas de mains

        def build():
            ring_game = fmt != "HU"
            mine = self.ring if ring_game else self.hands
            his = lib.ring if ring_game else lib.hands
            me, him = self.hero or "", lib.hero or ""
            switch = format_switch(lib.leak_formats(), fmt)
            context = {"me": me, "him": him, "format": fmt, "switch": switch, "my_hands": len(mine),
                       "his_hands": len(his), "period": periods.label(self.period)}
            if page == "lignes":
                return build_lines_page(refstudy.lines(his, him, mine, me), context)
            if ring_game:
                rows = refstudy.compare_ring(field.ring_ratios(mine, [me]), field.ring_ratios(his, [him]))
                positions = (ring.analyze(mine, me, merge=True), ring.analyze(his, him, merge=True))
            else:
                rows = refstudy.compare_hu(self.all_stats().get(me), lib.all_stats().get(him))
                positions = None
            return build_comparison_page(rows, (refstudy.result(mine, me), refstudy.result(his, him)),
                                         (refstudy.by_position(mine, me), refstudy.by_position(his, him)),
                                         positions, context)
        return self._cached(("reference", ident, page, fmt, lib.version), build)

    def load_hand2note(self) -> dict:
        """Télécharge les charts 6-max de Hand2Note Guide (usage personnel) : les pots à deux au flop s'ouvrent au
        solveur."""
        ring_ranges.install_hand2note(log=lambda message: None)
        with self._lock:
            self._cache.clear()
        return {"ranges": ring_ranges.available()}

    def ring_spots(self, limit: Optional[int] = None) -> list[dict]:
        """Tes coups des tables à plusieurs où il ne reste que deux joueurs au flop, les plus gros pots d'abord :
        de quoi les ouvrir au solveur (ou pourquoi ils ne se résolvent pas encore). limit : seuls les premiers sont
        préparés pour le solveur (status, pot_type ; les autres : None), la page n'en montre pas plus."""
        found = []
        for h in self.ring:
            pair = postflop.flop_pair(h)
            if pair and self.hero in pair:
                found.append((h, pair[1] if pair[0] == self.hero else pair[0]))
        found.sort(key=lambda hv: -round(hv[0].total_pot / hv[0].bb, 1))
        rows = []
        for k, (h, villain) in enumerate(found):
            status = pot_type = None
            if limit is None or k < limit:
                try:
                    pot_type = postflop.build_spot(h, self.hero).pot_type
                except postflop.Unsupported as exc:
                    status = str(exc)
                except Exception:  # noqa: BLE001 — un coup que le solveur ne sait pas lire : la page reste
                    print(f"Coup {h.hand_id} illisible pour le solveur :", file=sys.stderr)
                    traceback.print_exc()
                    status = "Coup illisible pour le solveur (détails dans le terminal)"
            pre = [a for a in h.actions if a.street == "preflop" and a.kind in (RAISE, CALL)]
            rows.append({"id": h.hand_id, "date": h.date, "format": h.table_format, "hero": h.position(self.hero),
                         "villain": h.position(villain), "line": ring_ranges.describe([(h.position(a.player), a.kind)
                                                                                      for a in pre]),
                         "pot_type": pot_type, "cards": h.hole_cards.get(self.hero, []), "board": h.board[:3],
                         "pot_bb": round(sum(a.amount for a in h.actions if a.street == "preflop") / h.bb, 1),
                         "total_bb": round(h.total_pot / h.bb, 1), "net_bb": round(h.net(self.hero) / h.bb, 1),
                         "status": status})
        return rows

    def catch_trainer_page(self) -> str:
        """Entraîneur › Bluffcatch : les mises river que tu as affrontées, à payer ou coucher (catch_trainer)."""
        def build():
            played = dict(self.leak_formats())
            groups = [("HU", self.hands, self.kinds())] if "HU" in played else []
            groups += [("ring", self.ring, self.ring_kinds())] if ring_leaks.FORMAT in played else []
            return catch_trainer.build_page(catch_trainer.exercises(groups, self.hero or "", self.api))
        return self._cached(("entraineur", "bluffcatch") + self._kinds_key() + self._ring_kinds_key(), build)

    def replay_record(self, hand_id: str) -> dict:
        """Une main pour le replayer (replay.record), vue de cet espace ; dans le tien, aussi celles des élèves et de
        tes joueurs de référence. NotFound si elle n'y est pas."""
        found = (self.by_id[hand_id], self.hero) if hand_id in self.by_id else (
            self.find_hand(hand_id) if self.is_me else None)
        if found is None:
            raise NotFound(hand_id)
        hand, hero = found
        return dict(replay.record(hand, hero), explorer=len(hand.board) >= 3 and postflop.flop_pair(hand) is not None)

    def find_hand(self, hand_id: str) -> Optional[tuple[Hand, str]]:
        """Une main et son joueur : parmi les tiennes, puis celles des élèves et de tes joueurs de référence."""
        hand = self.by_id.get(hand_id)
        if hand is not None and self.hero:
            return hand, self.hero
        libs = [self.student(meta["id"]) for meta in students.all_students()]
        if self.is_me:
            libs += [self.reference(meta["id"]) for meta in self.references_conf()]
        for lib in libs:
            if hand_id in lib.by_id and lib.hero:
                return lib.by_id[hand_id], lib.hero
        return None

    # --- leakfinding ----------------------------------------------------------------------
    def leak_formats(self) -> list[tuple[str, int]]:
        """Les formats de tes mains et leur nombre : le heads-up, puis les tables à plusieurs (3 à 9 joueurs
        ensemble) ; un rapport chacun. Seulement ceux que tu joues (Paramètres), ou que joue l'élève."""
        out = [("HU", len(self.hands))] if self.hands else []
        out += [(ring_leaks.FORMAT, len(self.ring))] if self.ring else []
        chosen = self.chosen_formats()
        if not chosen:
            return out
        kept = settings.enabled_formats([fmt for fmt, _ in out], chosen)
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
        changes = (_digest_count(),) + _theory_changes()
        if table_format == "HU":  # (les mains de départ et les stats déjà lues pour les autres pages servent)
            return self._cached(("leaks",) + changes + self._kinds_key(),
                                lambda: leaks.build(self.hands, self.hero, self.kinds(), plays=self._plays().get("HU"),
                                                    stats_all=self.all_stats()))
        return self._cached(("leaks", ring_leaks.FORMAT) + changes + self._ring_kinds_key(),
                            lambda: ring_leaks.build(self.ring, self.hero or "", self.ring_kinds(),
                                                     plays=self._plays().get("ring"), found=self._ring_found()))

    def leaks_page(self, standalone: bool = False, table_format: Optional[str] = None) -> str:
        fmt = self._leak_format(table_format)
        return build_leaks_page(self.leaks_report(fmt), api=f"{self.api}/leaks", pages=self.pages,
                                embed=not standalone, standalone=standalone, name=self.display_name,
                                opponents=self.summary()["opponents"] if fmt == "HU" else self.ring_opponents_view(),
                                formats=self.leak_formats(), period=periods.describe(self.period),
                                follows=None if standalone else tracking.followed(self.pages, fmt))

    def tracking(self, table_format: Optional[str] = None) -> tracking.Tracking:
        """Le suivi de ses leaks dans le temps (analyzer/tracking.py), dans ce format : ceux qu'il suit, et ceux du
        Leakfinding à suivre."""
        fmt = self._leak_format(table_format)
        report = self.leaks_report(fmt)
        follow_list = tracking.followed(self.pages, fmt)
        current = [(x.key, x.title, x.direction) for x in report.leaks
                   if x.key and x.direction and not x.key.startswith("solveur:")]
        hero = self.hero or ""
        changes = _theory_changes()
        if fmt == "HU":
            kinds = self.kinds()
            hands = self.hands
            stats_of = lambda part: leaks.stats_for(part, hero, kinds)  # noqa: E731
            kinds_key = self._kinds_key()
        else:
            kinds, plays = self.ring_kinds(), self._plays().get("ring")
            hands = ring_leaks.mine(self.ring, hero)
            stats_of = lambda part: ring_leaks.stats(part, hero, kinds, plays)  # noqa: E731
            kinds_key = self._ring_kinds_key()
        key = ("suivi", fmt, json.dumps(follow_list, sort_keys=True), tuple(current)) + changes + kinds_key
        return self._cached(key, lambda: tracking.build(hands, stats_of, follow_list, current))

    def tracking_page(self, table_format: Optional[str] = None) -> str:
        fmt = self._leak_format(table_format)
        return build_tracking_page(self.tracking(fmt), api=f"{self.api}/suivi", table_format=fmt,
                                   formats=self.leak_formats())

    def follow_leak(self, key: str, since: Optional[date], title: str = "", table_format: Optional[str] = None) -> dict:
        """Suit ce leak (la clé de sa stat) à partir de cette date, ou ne le suit plus (since None)."""
        fmt = self._leak_format(table_format)
        return {"suivis": tracking.follow(self.pages, fmt, key, since, title)}

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

    def _queue_hands(self, hand_ids: list[str]) -> None:
        """Met ces mains en file au solveur (celles déjà résolues ou en cours n'y retournent pas)."""
        if not postflop.status()["ready"]:
            return
        for hand_id in hand_ids:
            found = self.by_id.get(hand_id)
            if found is None or not self.hero:
                continue
            try:
                spot = postflop.build_spot(found, self.hero)
            except postflop.Unsupported:
                continue
            if self.solves.lookup(spot)["state"] not in ("waiting", "running", "done"):
                self.solves.analyze(spot, review.save_digest)

    def _solve_view(self, all_ids: list[str], done: int) -> dict:
        """L'avancement des résolutions de ces mains (toutes celles qui restent à résoudre) : en file, en cours."""
        jobs = self.solves.active_for(all_ids)
        running = next((v for v in jobs if v["state"] == "running"), None)
        current = None if running is None else {"hand": running["hand"], "progress": running.get("progress"),
                                                "max_iterations": running.get("max_iterations"), "job": running["job"]}
        return {"total": done + len(all_ids), "done": done, "busy": len({v["hand"] for v in jobs}),
                "current": current, "ready": postflop.status()["ready"]}

    def leaks_state(self, start: bool = False, table_format: Optional[str] = None, order: str = "leaks",
                    limit: Optional[int] = None, leak: Optional[str] = None) -> dict:
        """Les mains de ses pots contre les réguliers passées au solveur : analysées, à analyser, en cours ; start=True
        met en file les limit premières (toutes sans limit) dans cet ordre (leaks.SOLVE_ORDERS), ou celles d'un leak
        (sa clé)."""
        report = self.leaks_report(self._leak_format(table_format))
        if start:
            chosen = leaks.solve_candidates(report, order, leak)
            self._queue_hands([hand_id for hand_id, _ in (chosen[:limit] if limit else chosen)])
        return self._solve_view([hand_id for hand_id, _ in report.solve_orders.get("pertes", [])],
                                report.review["analyzed"])

    def leaks_cancel(self, table_format: Optional[str] = None) -> dict:
        report = self.leaks_report(self._leak_format(table_format))
        for view in self.solves.active_for([hand_id for hand_id, _ in report.solve_orders.get("pertes", [])]):
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
        waiting = custom_tree.pending(spot.edits)  # ton arbre : des tailles que le solveur choisit d'abord
        if waiting and view["state"] not in ("waiting", "running"):
            view = {"job": view["job"], "hand": hand_id, "state": "absent", "live": False, "study": False,
                    "target": view.get("target")}
            if start:
                solver = postflop.status()
                if not solver["ready"]:
                    return {"hand": hand_id, "state": "unavailable", "message": solver["message"],
                            "install": solver["install"]}
                try:
                    view = self._choose_tree(hand_id, spot, waiting)
                except postflop.Unsupported as exc:
                    return {"hand": hand_id, "state": "unsupported", "message": str(exc)}
            start = False
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
                family, rset = spot.family, spot.ranges_set
                view = self.solves.choose_and_solve(
                    spot.ident, lambda job: self._choose(family, board, job),
                    lambda: studyspots.StudySpot(family, studyspots.cards_of(board), ranges_set=rset))
            else:
                view = self.solves.start(spot, force=reopen)
        if view["state"] == "absent":
            solver = postflop.status()
            view["solver"] = {k: solver[k] for k in ("ready", "message", "install")}
        view["adjusted"] = spot.adjusted
        view["edits"] = ({"sizes": len(spot.edits["plan"]), "locks": len(spot.edits["locks"]),
                          "auto": len(spot.edits["auto"]), "pending": len(waiting)} if spot.edits else None)
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

    @staticmethod
    def _ranges_set(spot) -> str:
        """Le jeu de ranges d'un coup (stake_charts.SETS) : celui du spot d'étude, ou celui de la limite de la main."""
        if isinstance(spot, studyspots.StudySpot):
            return spot.ranges_set
        return stake_charts.set_of(spot.hand.bb)

    def ranges_state(self, hand_id: str) -> dict:
        """Les ranges préflop du coup : celles de la référence, celles en jeu, et d'où elles viennent."""
        try:
            spot = self._spot(hand_id)
        except postflop.Unsupported as exc:
            return {"hand": hand_id, "supported": False, "message": str(exc)}
        study = isinstance(spot, studyspots.StudySpot)
        data = custom_ranges.load()
        rset = self._ranges_set(spot)
        by_hand = data["coups"].get(spot.ident, {})
        can_line = not study or data["spots"]  # un spot d'étude : avec l'option des Paramètres
        by_line = data[custom_ranges.lines_key(rset)].get(spot.context, {}) if can_line else {}
        table_format, key = spot.context.split("|", 1)
        steps = [tuple(step.split(":", 1)) for step in key.split()]
        reference = custom_ranges.reference_name(table_format, rset)
        players = []
        for name, pos, role in self._range_players(spot):
            source = "coup" if pos in by_hand else "ligne" if pos in by_line else "reference"
            current = (ring_ranges.parse_range(by_hand.get(pos) or by_line[pos]) if source != "reference"
                       else spot.reference[name])
            players.append({"position": pos, "role": role, "source": source, "reference": spot.reference[name],
                            "line": ring_ranges.parse_range(by_line[pos]) if pos in by_line else None,
                            "current": current})
        names = self._range_players(spot)
        other = None
        if study:  # le même flop avec l'autre jeu de ranges : une étude à part
            twin = next(k for k in stake_charts.SETS if k != rset)
            ident = f"spot:{spot.family}:{''.join(spot.board)}" + ("" if twin == "petites" else ":hautes")
            other = {"set": twin, "href": "/explorateur/" + ident,
                     "label": f"Ouvrir ce flop avec les ranges des {stake_charts.SET_NAMES[twin].lower()} ↗"}
        return {"hand": hand_id, "supported": True, "spot": study, "format": table_format, "set": rset,
                "set_name": stake_charts.SET_NAMES[rset],
                "line": ring_ranges.describe(steps), "can_line": can_line, "for_spots": data["spots"],
                "adjusted": spot.adjusted, "reference": reference, "players": players,
                "choices": self._range_choices(spot, names, data), "other": other,
                "order": custom_ranges.hand_order()}

    @staticmethod
    def _base_ranges(spot, band: Optional[str], names) -> dict[str, dict[str, float]]:
        """Les ranges de base du coup (position -> poids) avec les charts de ce niveau de limites (None : la
        référence) ; postflop.Unsupported quand ils ne donnent pas cette ligne."""
        if isinstance(spot, studyspots.StudySpot):
            info = studyspots.family_info(spot.family)
            if spot.family not in studyspots.FAMILIES:
                return studyspots.ring_spot_ranges(spot.family, band)
            solution = stake_charts.hu_solution(band) if band else studyspots.load_solution()
            return {spot.oop: postflop.range_weights(solution, *info["oop"]),
                    spot.ip: postflop.range_weights(solution, *info["ip"])}
        other = postflop.build_spot(spot.hand, spot.hero, custom=False, band=band)
        return {pos: other.reference[name] for name, pos, _ in names}

    def _range_choices(self, spot, names, data: dict) -> list[dict]:
        """Les ranges dont le coup peut partir (onglet Ranges de l'explorateur) : celles de la référence, celles de tes
        charts de chaque niveau de limites, et tes ranges de sa ligne dans chaque jeu (petites, hautes limites) ;
        « base » : celles dont le coup part sans réglage. Les mêmes ranges ne sont proposées qu'une fois."""
        positions = [pos for _, pos, _ in names]
        base = [spot.reference[name] for name, _, _ in names]
        heads_up = isinstance(spot, studyspots.StudySpot) and spot.family in studyspots.FAMILIES or (
            not isinstance(spot, studyspots.StudySpot) and len(spot.hand.seats) == 2)
        found = stake_charts.available()
        levels = (found.get(stake_charts.HU, []) if heads_up else
                  sorted({b for fmt, bands in found.items() if fmt != stake_charts.HU for b in bands},
                         key=stakes.BANDS.index))
        out, bases = [], {}
        for band in (None, *levels):
            try:
                ranges = self._base_ranges(spot, band, names)
            except postflop.Unsupported:
                continue
            bases[band] = ranges
            label = "Référence (micro limites, rake élevé)" if band is None else f"Tes charts · {stakes.NAMES[band]}"
            out.append({"id": f"charts:{band or 'reference'}", "label": label,
                        "ranges": [ranges[pos] for pos in positions]})
        for rset in stake_charts.SETS:
            line = data[custom_ranges.lines_key(rset)].get(spot.context, {})
            fallback = bases.get(stake_charts.set_band(rset)) or bases.get(None)
            if not line or fallback is None:
                continue
            out.append({"id": f"ligne:{rset}",
                        "label": f"Tes ranges de la ligne · {stake_charts.SET_NAMES[rset]}",
                        "ranges": [ring_ranges.parse_range(line[pos]) if pos in line else fallback[pos]
                                   for pos in positions]})
        kept = []
        for choice in out:
            if any(all(custom_ranges.same(a, b) for a, b in zip(choice["ranges"], k["ranges"])) for k in kept):
                continue
            choice["base"] = all(custom_ranges.same(a, b) for a, b in zip(choice["ranges"], base))
            kept.append(choice)
        return kept

    def save_ranges(self, hand_id: str, scope: str, ranges: dict) -> dict:
        """Enregistre tes ranges pour ce coup (scope « coup ») ou pour sa ligne (« ligne ») ; seules celles qui
        diffèrent de ce qui s'appliquerait sans ce réglage sont gardées. ValueError si la demande ne va pas."""
        spot = self._spot(hand_id)
        data = custom_ranges.load()
        can_line = not isinstance(spot, studyspots.StudySpot) or data["spots"]
        if scope not in custom_ranges.SCOPES or (scope == "ligne" and not can_line):
            raise ValueError("Portée inconnue.")
        names = {pos: name for name, pos, _ in self._range_players(spot)}
        if not ranges or set(ranges) - set(names) or not all(isinstance(t, str) for t in ranges.values()):
            raise ValueError("Positions inconnues pour ce coup.")
        weights = {pos: custom_ranges.check(text) for pos, text in ranges.items()}
        rset = self._ranges_set(spot)
        by_line = data[custom_ranges.lines_key(rset)].get(spot.context, {}) if scope == "coup" and can_line else {}
        keep = {}
        for pos, w in weights.items():
            base = ring_ranges.parse_range(by_line[pos]) if pos in by_line else spot.reference[names[pos]]
            if not custom_ranges.same(w, base):
                keep[pos] = postflop.range_text(w)
        key = spot.ident if scope == "coup" else spot.context
        if keep:
            custom_ranges.save(scope, key, keep, rset)
        else:
            custom_ranges.clear(scope, key, rset)
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
        custom_ranges.clear(scope, spot.ident if scope == "coup" else spot.context, self._ranges_set(spot))
        with self._lock:
            self._cache.clear()
        return self.ranges_state(hand_id)

    # --- Paramètres › Ranges préflop : tes ranges par défaut, ligne par ligne, sans passer par un coup ---------------

    def stake_charts_view(self) -> dict:
        """Les charts préflop par niveau de limites (theory/stake_charts.py) : pour chaque format que tu joues (le
        heads-up, les tables à plusieurs : les charts 6-max et les formats qui ont les leurs) et chaque niveau, ceux
        importés ou ceux de la référence ; et tes mains de chaque niveau."""
        plays = settings.load()["formats"]
        found = stake_charts.available()
        hero = self.hero or ""
        formats = []
        if not plays or "HU" in plays:
            formats.append((stake_charts.HU, "Heads-up", stake_charts.mix(self._all_hands)))
        if not plays or "ring" in plays:
            ring_mix = stake_charts.mix(ring_leaks.mine(self._all_ring, hero)) if hero else {}
            ring_formats = sorted({"6-max", *ring_ranges.available(), *(f for f in found if f != stake_charts.HU)}
                                  - {"HU"}, key=lambda f: (f != "6-max", f))
            formats += [(fmt, fmt, ring_mix) for fmt in ring_formats]
        out = []
        references = ring_ranges.available()
        for fmt, label, counts in formats:
            bands = [{"band": band, "label": stakes.label(band), "hands": counts.get(band, 0),
                      "charts": stake_charts.summary(fmt, band)} for band in stakes.BANDS]
            reference = fmt == stake_charts.HU or ring_ranges.chart_mapping(fmt, references) is not None
            out.append({"format": fmt, "label": label, "bands": bands, "reference": reference})
        return {"formats": out}

    def save_stake_charts(self, table_format: str, band: str, data: Optional[dict] = None,
                          text: Optional[str] = None) -> dict:
        """Garde des charts pour ce format et ce niveau : un JSON (data), ou l'export texte des tables à plusieurs
        (text), qui complète ceux déjà là."""
        if text is not None:
            stake_charts.import_text(text, band, table_format)
        else:
            stake_charts.save(table_format, band, data or {})
        return self.stake_charts_view()

    def delete_stake_charts(self, table_format: str, band: str) -> dict:
        stake_charts.delete(table_format, band)
        return self.stake_charts_view()

    def default_ranges_view(self, rset: str = "petites") -> dict:
        """Les lignes de chaque format que tu joues (custom_ranges.formats) dans ce jeu de ranges (petites ou hautes
        limites) et les positions où ta range remplace celle de base ; les réglages dont la ligne n'a plus de base (à
        effacer) ; l'option des spots d'étude ; tes ranges par défaut (le choix) et tes mains de chaque jeu."""
        plays = settings.load()["formats"]
        counts = Counter(h.table_format for h in self._all_ring)
        counts["HU"] = len(self._all_hands)
        data = custom_ranges.load()
        mine = data[custom_ranges.lines_key(rset)]
        groups, listed = [], set()
        for table_format in custom_ranges.formats(counts, heads_up=not plays or "HU" in plays,
                                                  ring=not plays or "ring" in plays):
            lines = []
            for line in custom_ranges.reference_lines(table_format, rset):
                context = custom_ranges.context(table_format, line["steps"])
                listed.add(context)
                lines.append({"context": context, "line": ring_ranges.describe(line["steps"]),
                              "pot_type": line["pot_type"], "positions": list(line["ranges"]),
                              "adjusted": [pos for pos in line["ranges"] if pos in mine.get(context, {})]})
            groups.append({"format": table_format, "hands": counts.get(table_format, 0), "lines": lines,
                           "reference": custom_ranges.reference_name(table_format, rset)})
        orphans = []
        for context in sorted(set(mine) - listed):
            table_format, _, key = context.partition("|")
            steps = [tuple(step.split(":", 1)) for step in key.split() if ":" in step]
            orphans.append({"context": context, "format": table_format, "line": ring_ranges.describe(steps),
                            "positions": sorted(mine[context])})
        ring_played = not plays or "ring" in plays
        level = stake_charts.bands()
        sets = Counter("hautes" if level(h.bb) == "hautes" else "petites"
                       for h in self._all_hands + self._all_ring if h.bb)
        return {"formats": groups, "orphans": orphans, "for_spots": data["spots"],
                "charts": bool(ring_ranges.available()) or not ring_played, "set": rset,
                "sets": [{"set": k, "name": stake_charts.SET_NAMES[k], "level": stake_charts.SET_LEVELS[k],
                          "hands": sets.get(k, 0)} for k in stake_charts.SETS],
                "choice": stake_charts.choice(), "reference": stake_charts.reference_set()}

    def default_range_line(self, context: str, rset: str = "petites") -> dict:
        """Une ligne pour l'éditeur, dans ce jeu de ranges : la range de base de chaque position, et la tienne.
        NotFound si la ligne n'a pas de base."""
        line = custom_ranges.reference_line(context, rset)
        if line is None:
            raise NotFound(context)
        mine = custom_ranges.load()[custom_ranges.lines_key(rset)].get(context, {})
        table_format = context.split("|", 1)[0]
        players = [{"position": pos, "role": None, "source": "ligne" if pos in mine else "reference",
                    "reference": reference, "line": None,
                    "current": ring_ranges.parse_range(mine[pos]) if pos in mine else dict(reference)}
                   for pos, reference in line["ranges"].items()]
        return {"context": context, "supported": True, "format": table_format, "set": rset,
                "line": ring_ranges.describe(line["steps"]), "pot_type": line["pot_type"],
                "reference": custom_ranges.reference_name(table_format, rset), "players": players,
                "adjusted": "ligne" if mine else None, "order": custom_ranges.hand_order()}

    def save_default_ranges(self, context: str, ranges: object, rset: str = "petites") -> dict:
        """Enregistre tes ranges par défaut d'une ligne dans ce jeu ; une range égale à celle de base n'est pas
        gardée. Les positions absentes de la demande gardent leur réglage. ValueError si la demande ne va pas."""
        line = custom_ranges.reference_line(context, rset)
        if line is None:
            raise NotFound(context)
        if not isinstance(ranges, dict) or not ranges or set(ranges) - set(line["ranges"]) \
                or not all(isinstance(t, str) for t in ranges.values()):
            raise ValueError("Positions inconnues pour cette ligne.")
        weights = {pos: custom_ranges.check(text) for pos, text in ranges.items()}
        mine = dict(custom_ranges.load()[custom_ranges.lines_key(rset)].get(context, {}))
        for pos, w in weights.items():
            if custom_ranges.same(w, line["ranges"][pos]):
                mine.pop(pos, None)
            else:
                mine[pos] = postflop.range_text(w)
        if mine:
            custom_ranges.save("ligne", context, mine, rset)
        else:
            custom_ranges.clear("ligne", context, rset)
        with self._lock:
            self._cache.clear()
        return self.default_range_line(context, rset)

    def clear_default_ranges(self, context: str, rset: str = "petites") -> dict:
        """Efface tes ranges par défaut d'une ligne dans ce jeu (aussi celles d'une ligne qui n'a plus de base)."""
        cleared = custom_ranges.clear("ligne", context, rset)
        with self._lock:
            self._cache.clear()
        return {"context": context, "cleared": cleared}

    def set_ranges_choice(self, value: object) -> dict:
        """Tes ranges par défaut : selon la limite de chaque main (« auto »), petites ou hautes limites (le jugement
        préflop, le Leakfinding et les ranges du solveur de tes mains en dépendent). ValueError si le choix est
        inconnu."""
        stake_charts.set_choice(str(value))
        with self._lock:
            self._cache.clear()
        return {"choice": stake_charts.choice()}

    def set_ranges_reference(self, value: object) -> dict:
        """La référence : le jeu de ranges des études du solveur (séries, plans de jeu, entraîneur, repères du
        Leakfinding après le flop) et de l'explorateur préflop. ValueError si le jeu est inconnu."""
        stake_charts.set_reference(str(value))
        with self._lock:
            self._cache.clear()
        return {"reference": stake_charts.reference_set()}

    def set_ranges_for_spots(self, value: bool) -> dict:
        """Tes ranges par défaut valent aussi (ou non) pour les spots d'étude ouverts dans l'explorateur."""
        custom_ranges.set_for_spots(value)
        with self._lock:
            self._cache.clear()
        return {"for_spots": custom_ranges.for_spots()}

    def ranges_settings_page(self) -> str:
        return build_ranges_page()

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

    # --- ton arbre, construit avant la résolution ------------------------------------
    @staticmethod
    def _builder_family(spot) -> Optional[str]:
        """La famille dont viennent les situations de l'arbre (et les tailles que le solveur compare), ou None."""
        if isinstance(spot, studyspots.StudySpot):
            family = spot.family
        else:
            family = spot.size_target or (spot.family if spot.plan else None)
        return family if family in sizing.PROFILES else None

    @staticmethod
    def _positions(spot) -> tuple[str, str]:
        return (spot.oop, spot.ip) if isinstance(spot, studyspots.StudySpot) else spot.positions

    def tree_builder(self, hand_id: str) -> dict:
        """Ton arbre avant la résolution, street par street : pour chaque situation (c-bet, 2e barrel,
        check-raise…), ses tailles d'origine et ton choix : « default » (celles d'origine), « fixed » (tes tailles),
        « none » (aucune mise ou relance : la branche disparaît) ou « auto » (le solveur choisit lui-même parmi des
        tailles, avant la résolution). « reachable » : la situation peut encore arriver ; « played » : elle est sur la
        ligne jouée (elle garde une mise)."""
        spot = self._spot(hand_id)
        family = self._builder_family(spot)
        if family is None:
            return {"hand": hand_id, "available": False,
                    "message": "Pas de constructeur d'arbre pour ce type de pot : résous le coup, puis change ses "
                               "tailles dans l'onglet Arbre."}
        data = spot.edits or {"plan": {}, "locks": [], "auto": {}}
        positions = self._positions(spot)
        base_plan = dict(spot.plan or {})
        played = {p["key"] for p in getattr(spot, "played_sizes", []) if p.get("key")}
        sits = sorted(sizing.situations(family), key=lambda x: self._situation_order(x.key))
        keys = {x.key for x in sits}
        rows, effective = [], {}
        for sit in sits:
            key = sit.key
            base = list(base_plan[key]) if key in base_plan else spot.default_sizes_for(key)
            auto = data["auto"].get(key)
            if auto:
                mode, sizes = "auto", list(auto.get("chosen") or [])
                effective[key] = sizes or list(auto["candidates"])
            elif key in data["plan"]:
                sizes = list(data["plan"][key])
                mode = "fixed" if sizes else "none"
                effective[key] = sizes
            else:
                mode, sizes = "default", base
                effective[key] = base
            rows.append({"key": key, "street": sit.street, "raise": key.startswith("raise"),
                         "player": positions[sit.player], "line": sit.past,
                         "title": sizing.label(key, family, positions), "base": base, "sizes": sizes, "mode": mode,
                         "candidates": list(auto["candidates"]) if auto else list(sit.candidates),
                         "choose": sit.choose, "chosen": list(auto["chosen"]) if auto and auto.get("chosen") else None,
                         "played": key in played})
        for row in rows:
            row["reachable"] = sizing.reachable(row["key"], effective, keys)
        profile = sizing.PROFILES[family]
        turn = {"i": "mise du BTN payée à la turn", "x": "turn checkée", "o": "mise de la BB payée à la turn"}
        lines = {}
        for past in sorted({r["line"] for r in rows if r["line"]}, key=lambda x: (len(x), x)):
            parts = [profile.flop_line.get(past[0], past[0])] + ([turn.get(past[1], past[1])] if len(past) > 1 else [])
            lines[past] = sizing.rename_roles(", ".join(parts), *positions)
        return {"hand": hand_id, "available": True, "family": family, "family_title": studyspots.family_title(family),
                "positions": list(positions), "rows": rows, "lines": lines, "edited": custom_tree.edited(data),
                "pending": len(custom_tree.pending(data)), "locks": len(data["locks"]),
                "compared": sizing.description(family)}

    def save_tree_builder(self, hand_id: str, choices: object) -> dict:
        """Enregistre ton arbre : {clé: {"mode": "default" | "fixed" | "none" | "auto", "sizes": […]}} (sizes : tes
        tailles, ou celles que le solveur compare) ; les situations absentes gardent leurs tailles d'origine. Les
        verrous sont retirés si l'arbre change. ValueError si un choix ne va pas."""
        view = self.tree_builder(hand_id)
        if not view["available"]:
            raise ValueError(view["message"])
        rows = {r["key"]: r for r in view["rows"]}
        if not isinstance(choices, dict) or len(choices) > len(rows):
            raise ValueError("Requête invalide.")
        plan, auto = {}, {}
        for key, choice in choices.items():
            row = rows.get(key)
            if row is None or not isinstance(choice, dict):
                raise ValueError("Situation inconnue.")
            mode, sizes = choice.get("mode"), choice.get("sizes") or []
            if mode == "default":
                continue
            if mode == "none":
                if row["played"]:
                    raise ValueError(f"{row['title']} : c'est la ligne jouée, elle garde au moins une taille.")
                plan[key] = []
            elif mode == "fixed":
                sizes = custom_tree.check_sizes(key, sizes)
                if not sizes:
                    raise ValueError(f"{row['title']} : choisis au moins une taille (ou « aucune »).")
                if sizes != custom_tree.check_sizes(key, row["base"]):
                    plan[key] = sizes
            elif mode == "auto":
                sizes = custom_tree.check_sizes(key, sizes or row["candidates"])
                if len(sizes) <= row["choose"]:
                    raise ValueError(f"{row['title']} : donne au solveur au moins {row['choose'] + 1} tailles à "
                                     "comparer.")
                auto[key] = sizes
            else:
                raise ValueError("Requête invalide.")
        changed = custom_tree.set_tree(self._spot(hand_id).ident, plan, auto)
        return dict(self.tree_builder(hand_id), changed=changed)

    def _choose_tree(self, hand_id: str, spot, waiting: dict) -> dict:
        """Ton arbre : le solveur choisit d'abord les tailles que tu lui as laissées (sizing.Selection sur le flop de
        la famille, tes autres tailles gardées), puis le coup se résout avec elles (une étude à part)."""
        family = self._builder_family(spot)
        if family in studyspots.RING_FAMILIES:
            studyspots.ring_spot_ranges(family)  # tes charts 6-max (Unsupported sinon)
        board = "".join(spot.board if isinstance(spot, studyspots.StudySpot)
                        else studyspots.normalize_board(family, spot.board))
        fixed = dict(spot.edits["plan"])
        base_plan = dict(spot.plan or {})
        base = {s.key: list(base_plan[s.key]) if s.key in base_plan else spot.default_sizes_for(s.key)
                for s in sizing.situations(family) if s.key not in fixed and s.key not in waiting}
        ident = spot.ident
        rset = spot.ranges_set if isinstance(spot, studyspots.StudySpot) else ""  # celles du spot, ou la référence

        def choose(job) -> None:
            def log(message: str) -> None:
                job.progress["stage"] = message.strip()

            def started(proc) -> None:
                job.process = proc
                if job.cancelled:
                    proc.terminate()
            study = studyspots.StudySpot(family, studyspots.cards_of(board), plan={}, ranges_set=rset)
            result = sizing.Selection(study, log, on_start=started, stopped=lambda: job.cancelled, fixed=fixed,
                                      auto=waiting, base=base).run()
            custom_tree.set_chosen(ident, {k: result["plan"][k] for k in waiting}, result["report"])

        return self.solves.choose_and_solve(f"arbre:{ident}", choose, lambda: self._spot(hand_id), aliases=(ident,),
                                            keep_live=True)

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
        data = spot.edits or {"plan": {}, "locks": [], "auto": {}}
        base_plan = dict(getattr(spot, "plan", None) or {})
        overrides = custom_tree.merged_plan({}, data) or {}
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
        study = lambda: studyspots.StudySpot(family, studyspots.cards_of(board))  # noqa: E731  (la référence)
        return self.solves.choose_and_solve(
            studyspots.ident_of(family, board), lambda job: self._choose(family, board, job),
            lambda: self._spot(hand_id),
            aliases=(hand_id,), keep_live=True, after=lambda: self.solves.start(study(), keep_live=False))

    def spot_set(self, family: str = "srp", start: bool = False, rset: Optional[str] = None) -> dict:
        """Série de spots d'étude : état de chaque flop ; start=True met en file ceux qui manquent ; rset : avec les
        ranges des petites limites (la série) ou des hautes limites (une étude à part par flop).

        Un flop de la série sans tailles choisies passe d'abord par le choix des tailles (long) ; avec les ranges des
        hautes limites, il prend les mêmes tailles. Par défaut, les ranges de la référence."""
        rset = rset or studyspots.reference_set()
        if not studyspots.is_series(family) or rset not in stake_charts.SETS:
            raise NotFound(family)
        try:
            if family in studyspots.RING_FAMILIES:
                studyspots.ring_spot_ranges(family, stake_charts.set_band(rset))  # tes charts 6-max
        except postflop.Unsupported as exc:
            return {"family": family, "label": studyspots.family_info(family)["label"], "ready": False,
                    "error": str(exc), "total": 0, "done": 0, "busy": 0, "rows": [], "ranges": rset}
        studies = studyspots.spot_studies((family,), rset)
        rows = []
        ready = postflop.status()["ready"]
        series = set(studyspots.flop_set(family))
        for board in studyspots.family_boards(family):
            spot = studyspots.StudySpot(family, studyspots.cards_of(board), ranges_set=rset)
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
                "precision": self.solves.precision()[1], "ranges": rset,
                "total": len(rows), "done": sum(r["done"] for r in rows),
                "busy": sum(r.get("state") in ("waiting", "running") for r in rows), "rows": rows}

    def _queue_flop(self, family: str, board: str, spot, view: dict, series: bool) -> dict:
        """Met un flop d'une série en file : le choix de ses tailles d'abord s'il n'en a pas, puis sa résolution
        (avec les ranges du spot : celles des petites ou des hautes limites)."""
        if series and not spot.plan:
            rset = spot.ranges_set
            return self.solves.choose_and_solve(
                spot.ident, lambda job: self._choose(family, board, job),
                lambda: studyspots.StudySpot(family, studyspots.cards_of(board), ranges_set=rset))
        return self.solves.start(spot, force=view["state"] == "done", keep_live=False)

    RING_ORDER = ("4bet", "3bet", "srp")  # les pots les plus rapides à résoudre d'abord
    SPOT_GROUPS = {"hu": studyspots.FAMILIES, "6max": studyspots.RING_FAMILIES}  # les onglets des séries

    @staticmethod
    def _pot_kind(family: str) -> str:
        """« srp », « 3bet » ou « 4bet »."""
        return studyspots.RING_FAMILIES[family]["kind"] if family in studyspots.RING_FAMILIES else family

    def group_spot_sets(self, group: str, start: bool = False, rset: Optional[str] = None) -> dict:
        """Toutes les séries d'un onglet (« hu » : SRP, pots 3bet et 4bet heads-up ; « 6max » : celles que tes charts
        couvrent) : leur état et la durée de ce qui manque ; start=True met en file tous les flops manquants, un flop
        de chaque série à tour de rôle (pots 4bet et 3bet d'abord) : chaque plan de jeu se dessine vite, au lieu
        d'une série après l'autre ; rset : avec les ranges des petites ou des hautes limites. KeyError pour un autre
        onglet."""
        families = list(self.SPOT_GROUPS[group])
        rset = rset or studyspots.reference_set()
        sets = {family: self.spot_set(family, rset=rset) for family in families}
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
                        spot = studyspots.StudySpot(family, studyspots.cards_of(board), ranges_set=rset)
                        self._queue_flop(family, board, spot, self.solves.lookup(spot), row["series"])
            sets = {family: self.spot_set(family, rset=rset) for family in families}
        rows = [(f, r) for f, state in sets.items() for r in state["rows"]]
        current = next(((f, r) for f, r in rows if r.get("state") == "running"), None)
        seconds = sum(studyspots.SOLVE_SECONDS[f] + (0 if r["sizes"] or not r["series"] else studyspots.CHOOSE_SECONDS[f])
                      for f, r in rows if not r["done"])
        summary = [{"family": f, "title": studyspots.family_title(f), "total": state["total"], "done": state["done"],
                    "busy": state["busy"], "error": state.get("error")} for f, state in sets.items()]
        out = {"group": group, "ranges": rset, "ready": ready, "total": len(rows),
               "done": sum(r["done"] for _, r in rows),
               "busy": sum(r.get("state") in ("waiting", "running") for _, r in rows),
               "waiting": sum(r.get("state") == "waiting" for _, r in rows), "seconds": seconds,
               "covered": sum(1 for x in summary if not x["error"]), "families": summary, "current": None}
        if current:
            family, row = current
            out["current"] = {"family": family, "title": studyspots.family_title(family), "board": "".join(row["board"]),
                              "texture": row["texture"], "mode": row.get("mode"), "progress": row.get("progress") or {},
                              "max_iterations": row.get("max_iterations")}
        return out

    def group_spot_cancel(self, group: str, rset: Optional[str] = None) -> dict:
        """Arrête toutes les résolutions des séries de cet onglet (en attente ou en cours), avec ces ranges."""
        for family in self.SPOT_GROUPS[group]:
            for row in self.spot_set(family, rset=rset)["rows"]:
                if row.get("state") in ("waiting", "running"):
                    self.solves.cancel(row["job"])
        return self.group_spot_sets(group, rset=rset)

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

    def spot_cancel(self, family: str = "srp", rset: Optional[str] = None) -> dict:
        """Arrête les résolutions en lot de cette série (en attente ou en cours), avec ces ranges."""
        for row in self.spot_set(family, rset=rset)["rows"]:
            if row.get("state") in ("waiting", "running"):
                self.solves.cancel(row["job"])
        return self.spot_set(family, rset=rset)

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

    REFINE = "affiner|"  # tâches « affiner un niveau du plan » de la file : « affiner|famille|côté|façon|niveau »

    def plan_refine_state(self) -> dict:
        """Les niveaux du plan en cours d'affinage au solveur (ou affinés depuis le lancement) :
        {"famille|côté|façon|niveau": tâche}."""
        return {"ready": postflop.status()["ready"],
                "jobs": {k[len(self.REFINE):]: v for k, v in self.solves.views(self.REFINE).items()}}

    def plan_refine(self, family: str, level: str, side: str = "attack", mode: str = "flop") -> dict:
        """Affine un niveau du plan au solveur (coach.refine_spots) : quelques flops résolus de nouveau avec une partie
        du niveau imposée (side, mode : coach.REFINE_MODES), à la précision de leur résolution d'origine, en une
        tâche de la file."""
        if (family not in coach.plan_families() or (side, mode) not in coach.REFINE_MODES
                or level not in {key for key, _, _ in coach.STEPS[side]}):
            raise ValueError("Niveau inconnu.")
        if not postflop.status()["ready"]:
            raise ValueError("Installe d'abord le solveur : python -m analyzer gtopen --installer")
        items = coach.refine_spots(family, level, side, mode)
        if not items:
            raise ValueError("Pas encore de flop lu pour ce niveau : clique d'abord sur « Préparer le plan ».")

        def run_one(job, item: dict) -> None:
            def started(proc) -> None:
                job.process = proc
                if job.cancelled:  # arrêt demandé pendant le lancement
                    proc.terminate()
            base = item["base"]
            iterations, target = ((base["max_iterations"], base["target_exploit_pct"]) if base
                                  else self.solves.precision())
            coach.refine_one(item, iterations, target, on_progress=job.progress.update, on_start=started)
        self.solves.batch(f"{self.REFINE}{family}|{side}|{mode}|{level}", items, run_one,
                          lambda item: studyspots.board_text(item["spot"].board))
        return self.plan_refine_state()

    def plan_refine_cancel(self, family: str, level: str, side: str = "attack", mode: str = "flop") -> dict:
        self.solves.cancel(f"{self.REFINE}{family}|{side}|{mode}|{level}")
        return self.plan_refine_state()

    def plan_field(self, band: Optional[str] = None) -> dict:
        """Les populations de l'Étude du field, pour ajuster le plan de jeu (field_plan) : par format de tes mains, à
        un niveau de limites (celui demandé, sinon celui où tu as le plus de mains), les réguliers, les récréatifs et
        chaque style de récréatifs (ceux qui ont assez de mains pour en avoir un), avec leur étude."""
        out = {}
        min_hands = settings.load()["min_hands"]
        for fmt, _ in self.leak_formats():
            found = self.field_bands(fmt)
            chosen = stakes.choose(band, found)
            if chosen is None:
                continue
            rows = self._field_rows(fmt, chosen)
            populations = []
            for kind, label in (("reg", "Les réguliers"), ("rec", "Les récréatifs")):
                names = tuple(r["name"] for r in rows if r["info"]["kind"] == kind)
                if names:
                    populations.append({"key": kind, "label": label, "players": len(names),
                                        "study": self.field_study(fmt, names, chosen)})
            styles: dict[str, list[str]] = {}
            for r in rows:
                if r["info"]["kind"] == "rec" and r["hands"] >= min_hands:
                    style = self.field_study(fmt, (r["name"],), chosen).style[0]
                    if style != "autre":
                        styles.setdefault(style, []).append(r["name"])
            for style in field.STYLE_ORDER:
                if style in styles:
                    label = "Récréatifs " + field.STYLES[style].name.lower()
                    populations.append({"key": f"rec-{style}", "label": label, "players": len(styles[style]),
                                        "study": self.field_study(fmt, tuple(styles[style]), chosen)})
            out[fmt] = {"band": chosen, "bands": found, "populations": populations}
        return out

    def plan_page(self, band: Optional[str] = None) -> str:
        return build_coach_page(self.plan_state(), villains=tuple(o["name"] for o in self.opponents()),
                                field=self.plan_field(band))

    # --- analyse des mains jouées ---------------------------------------------------
    def solver_formats(self) -> list[str]:
        """Les formats dont les mains passent au solveur : ceux que tu joues (leak_formats), sans les mélanger."""
        return [fmt for fmt, _ in self.leak_formats()]

    def _review_formats(self, table_format: Optional[str]) -> list[str]:
        """Les formats d'une analyse : celui demandé (« HU », « ring » ; « 6-max »… : les tables à plusieurs), ou tous
        ceux que tu joues (None, « all »)."""
        formats = self.solver_formats()
        if table_format in (None, "", "all"):
            return formats
        wanted = "HU" if table_format == "HU" else ring_leaks.FORMAT
        return [wanted] if wanted in formats else []

    def _review_scope(self, fmt: str) -> tuple[list[Hand], dict, int]:
        """Tes mains d'un format que le solveur juge, contre les réguliers (contre un récréatif, l'exploitation prime) :
        en heads-up, toutes celles-là ; aux tables à plusieurs, celles allées au flop à deux avec toi, et leurs pots
        (ring_leaks.two_way_pots) ; puis le nombre de tes mains contre des récréatifs, laissées de côté."""
        def build():
            if fmt == "HU":
                regular, excluded = self.regular_hands()
                return regular, {}, excluded["hands"]
            hero = self.hero or ""
            parts = ring_leaks.split(ring_leaks.mine(self.ring, hero), hero, self.ring_kinds())
            pots = ring_leaks.two_way_pots(parts["reg"], hero)
            return [h for h in parts["reg"] if h.hand_id in pots], pots, len(parts["rec"])
        kinds = self._kinds_key() if fmt == "HU" else self._ring_kinds_key()
        return self._cached(("review_scope", fmt) + kinds, build)

    def solver_review(self, fmt: str) -> dict:
        """Tes mains d'un format face au solveur, contre les réguliers (leaks.solver_review) : résumés des mains
        analysées (aux tables à plusieurs, situations nommées avec les vraies positions), mains à résoudre, situations,
        erreurs, EV perdue ; « excluded » : tes mains contre des récréatifs, laissées de côté. Recalculé quand une main
        passe au solveur."""
        def build():
            hands, pots, excluded = self._review_scope(fmt)
            found = (leaks.solver_review(hands, self.hero or "") if fmt == "HU"
                     else ring_leaks.solver_review(hands, self.hero or "", pots))
            return dict(found, excluded=excluded)
        kinds = self._kinds_key() if fmt == "HU" else self._ring_kinds_key()
        return self._cached(("solver_review", fmt) + kinds + _solver_key(), build)

    def review_orders(self, villain: Optional[str] = None, table_format: Optional[str] = None) -> dict:
        """Les mains encore à résoudre (face à cet adversaire, ou tes mains contre les réguliers d'un format, de tous
        ceux que tu joues sans format), dans chaque ordre : « pots » (les plus gros pots d'abord), « pertes » (les plus
        chères d'abord) et, pour tes mains, « leaks » (celles où se joue un de tes leaks) : [(main, minutes)]."""
        if villain:
            _, todo = review.collect(self.hands_against(villain), self.hero)
            entries, leak_lists = list(zip(todo.hands, todo.pot_types)), []
        else:
            formats = self._review_formats(table_format)
            pending = [self.solver_review(fmt)["todo"] for fmt in formats]
            entries = [e for todo in pending for e in zip(todo.hands, todo.pot_types)]
            leak_lists = [self.leaks_report(fmt).solve_orders.get("leaks", []) for fmt in formats] if self.hero else []
        hero = self.hero or ""
        loss = {h.hand_id: (h.net(hero) / h.bb, -sum(a.amount for a in h.actions)) for h, _ in entries}
        pots = sorted(entries, key=lambda e: loss[e[0].hand_id][1])
        costly = sorted(entries, key=lambda e: loss[e[0].hand_id])
        out = {"pertes": [(h.hand_id, review.minutes(t)) for h, t in costly],
               "pots": [(h.hand_id, review.minutes(t)) for h, t in pots]}
        if leak_lists:
            found = [x for found in leak_lists for x in found if x[0] in loss]
            out["leaks"] = sorted(found, key=lambda x: loss[x[0]]) if len(leak_lists) > 1 else found
        return out

    def _review_todo(self, villain: Optional[str], table_format: Optional[str]) -> tuple[list[str], int]:
        """Les mains qui restent à résoudre et le nombre de mains analysées (face à cet adversaire, ou tes mains d'un
        format, de tous sans format)."""
        if villain:
            done, todo = review.collect(self.hands_against(villain), self.hero)
            return todo.hand_ids, len(done)
        found = [self.solver_review(fmt) for fmt in self._review_formats(table_format)]
        return [i for r in found for i in r["todo"].hand_ids], sum(r["analyzed"] for r in found)

    def review_state(self, villain: Optional[str] = None, start: bool = False, order: str = "pots",
                     limit: Optional[int] = None, table_format: Optional[str] = None) -> dict:
        """Mains allées au flop (face à cet adversaire, ou tes mains d'un format, de tous sans format) : analysées, à
        analyser, en cours ; start=True met en file les limit premières de celles qui restent (toutes sans limit), dans
        cet ordre (review_orders)."""
        if start:
            chosen = self.review_orders(villain, table_format).get(order) or []
            self._queue_hands([hand_id for hand_id, _ in (chosen[:limit] if limit else chosen)])
        todo, done = self._review_todo(villain, table_format)
        return self._solve_view(todo, done)

    def solver_panel(self, table_format: Optional[str]) -> str:
        """Le panneau « Face au solveur » de l'onglet d'un format (« HU », « ring ») ; NotFound pour un autre format,
        ou un format que tu ne joues pas."""
        if table_format not in ("HU", ring_leaks.FORMAT) or not self._review_formats(table_format):
            raise NotFound(table_format or "")
        return panel_html(table_format, self.solver_review(table_format),
                          self.review_orders(table_format=table_format), f"{self.api}/revue", self.pages)

    def solver_bilan(self) -> str:
        """Mes mains › Bilan : ce que le solveur dit de tes mains analysées, format par format, et de quoi en analyser
        d'autres (toutes tes mains, ou un format)."""
        formats = self.solver_formats()
        sections = [(fmt, self.solver_review(fmt)) for fmt in formats]
        orders = {fmt: self.review_orders(table_format=fmt) for fmt in formats}
        if len(formats) > 1:
            orders["all"] = self.review_orders(table_format="all")
        return build_solver_bilan(sections, orders, f"{self.api}/revue", self.pages,
                                  empty_note="Aucune main pour l'instant : importe des historiques.")

    def review_cancel(self, villain: Optional[str] = None, table_format: Optional[str] = None) -> dict:
        todo, _ = self._review_todo(villain, table_format)
        for view in self.solves.active_for(todo):
            self.solves.cancel(view["job"])
        return self.review_state(villain, table_format=table_format)

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

    # --- les mains par pseudo : supprimer celles d'un pseudo, le rétablir ----------------
    def pseudos_view(self) -> dict:
        """Les pseudos que les historiques de cet espace marquent comme héros, avec leurs mains et leurs sites, et leur
        rôle : « me » (toi, ou l'élève), « other » (décoché dans tes Paramètres : pas toi), « reference » (un joueur de
        référence) ; puis les pseudos dont les mains sont supprimées."""
        conf = settings.load() if self.is_me else None
        chosen = self.chosen_pseudos()
        mine = self.my_pseudos(self.hero_pseudos, conf) if conf is not None else set(chosen) if chosen else None
        owners = {p: ref["name"] for ref in conf["references"] for p in ref["pseudos"]} if conf is not None else {}
        rows: dict[str, dict] = {}
        for (pseudo, site), n in self.hero_pseudos.items():
            row = rows.setdefault(pseudo, {"name": pseudo, "sites": [], "hands": 0})
            row["hands"] += n
            if site not in row["sites"]:
                row["sites"].append(site)
        for row in rows.values():
            row["reference"] = owners.get(row["name"])
            row["role"] = ("reference" if row["reference"] else "me" if mine is None or row["name"] in mine
                           else "other")
        removed = [{"name": name, "hands": r["hands"], "removed": r["removed"]}
                   for name, r in db_hands.removed_pseudos(self.db, self.space_id).items()]
        # les pseudos choisis d'un élève qu'aucun historique ne marque comme héros : ses mains sans héros marqué
        extra = [{"name": p, "hands": self.unmarked_seen.get(p, 0)} for p in chosen if p not in rows]
        return {"pseudos": sorted(rows.values(), key=lambda r: (-r["hands"], r["name"].lower())), "removed": removed,
                "chosen": chosen, "extra": extra, "student": self.space_key.startswith(students.PREFIX)}

    def _raw_pseudos(self, names: set[str]) -> set[str]:
        """Ces pseudos tels que les historiques les écrivent (un alias réunit les pseudos qu'il regroupe)."""
        return set(names) | {p for p, alias in aliases.load().items() if alias in names}

    def remove_pseudos(self, names: object) -> dict:
        """Supprime les mains importées de ces pseudos, celles où l'historique les marque comme héros
        (db_hands.remove_pseudos : une main qu'un de tes autres historiques contient aussi revient de ce point de vue) ;
        ValueError : pas une liste de pseudos marqués comme héros ici."""
        if not (isinstance(names, list) and 0 < len(names) <= MAX_REMOVED_PSEUDOS
                and all(isinstance(n, str) for n in names)):
            raise ValueError("Requête invalide.")
        heroes = {p for p, _ in self.hero_pseudos}
        unknown = sorted(set(names) - heroes, key=str.lower)
        if unknown:
            raise ValueError(f"« {unknown[0]} » n'est le héros d'aucun historique importé ici.")
        found = db_hands.remove_pseudos(self.db, self.space_id, self._raw_pseudos(set(names)),
                                        keep=self._raw_pseudos(heroes - set(names)))
        self.reload()
        return dict(found, pseudos=self.pseudos_view(), files=self.files_view(), state=self.summary())

    def restore_pseudo(self, name: object) -> dict:
        """Rétablit un pseudo supprimé (ses mains reviennent des historiques gardés) ; KeyError s'il ne l'est pas."""
        if not isinstance(name, str):
            raise ValueError("Requête invalide.")
        added = db_hands.restore_pseudo(self.db, self.space_id, name)
        self.reload()
        return {"added": added, "pseudos": self.pseudos_view(), "files": self.files_view(), "state": self.summary()}

    # --- import -----------------------------------------------------------------
    def import_files(self, files: list[dict], job: Optional[ImportJob] = None) -> dict:
        """Importe dans la base les historiques reconnus (le texte d'origine est gardé ; un historique ou une main
        déjà importés ne le sont pas deux fois) ; job : son avancement, pour la barre de la page Importer.

        Chaque fichier : {"name", "content"} (texte), {"name", "zip"} (archive zip en base64, dossiers compris : chacun
        de ses historiques s'importe comme un fichier) ou {"name", "data"} (ses octets : un historique ou une archive
        zip)."""
        job = job or ImportJob("", 0)
        self._import_part(job, files[:MAX_IMPORT_FILES])
        return self._finish_import(job)

    def _import_part(self, job: ImportJob, files: list[dict]) -> None:
        """Enregistre ces historiques dans la base (un envoi à la fois), sans relire les mains de l'espace : leurs
        résultats et les mains ajoutées s'accumulent dans job."""
        with self._import_lock:
            items = [self._import_item(item) for item in files]
            job.plan([count_hands(text) for text in self._import_texts(items)])
            removed = frozenset(db_hands.removed_pseudos(self.db, self.space_id))  # leurs mains sont écartées
            for name, kind, payload in items:
                if kind == "zip":
                    job.added += self._import_zip(name, payload, job.results, job, removed)
                elif kind == "bad":
                    job.results.append({"name": name, "status": payload, "hands": 0, "new": 0})
                else:
                    job.added += self._import_one(name, payload, job.results, job, removed)

    def _finish_import(self, job: ImportJob) -> dict:
        """Après le dernier envoi : les mains ajoutées rejoignent les analyses (seulement elles, quand rien d'autre n'a
        changé) ; le résultat de l'import."""
        if job.added:
            self.reload(job.loading, new_only=True)
        skipped = sum(r.get("skipped", 0) for r in job.results if not r.get("archive"))
        return {"files": job.results, "added": job.added, "skipped": skipped, "state": self.summary()}

    @staticmethod
    def _import_item(item: dict) -> tuple[str, str, object]:
        """(nom, nature, contenu) d'un fichier envoyé : « text » et son texte, « zip » et ses octets, ou « bad » et
        pourquoi il ne se lit pas."""
        name = str(item.get("name") or "fichier")[:200]
        data = item.get("data")
        if isinstance(data, (bytes, bytearray)):
            if name.lower().endswith(".zip") or bytes(data[:4]) == b"PK\x03\x04":
                return name, "zip", bytes(data)
            return name, "text", decode(bytes(data))
        if isinstance(item.get("zip"), str):
            try:
                return name, "zip", base64.b64decode(item["zip"], validate=True)
            except (binascii.Error, ValueError):
                return name, "bad", "archive zip illisible (envoi abîmé)"
        return name, "text", item.get("content")

    @staticmethod
    def _import_texts(items: list[tuple[str, str, object]]):
        """Les textes d'un envoi, archives ouvertes (ceux qui ne se lisent pas sont laissés de côté)."""
        for _, kind, payload in items:
            if kind == "zip":
                try:
                    yield from (text for _, text in read_zip(payload).files)
                except ValueError:
                    continue
            elif kind == "text" and isinstance(payload, str):
                yield payload

    def start_import(self, files: list[dict]) -> dict:
        """Lance l'import en arrière-plan (un à la fois) ; son avancement se lit avec import_status."""
        job = self._new_import()
        self._import_pool_of().submit(self._run_import, job, files)
        return job.view()

    def open_import(self) -> dict:
        """Ouvre un import envoyé en plusieurs fois (la page Importer : des lots de petits historiques, les gros un par
        un, pour que ni la page ni le serveur n'aient tout en mémoire) : import_part pour chaque envoi, puis
        close_import ; son avancement se lit avec import_status."""
        return self._new_import().view()

    def import_part(self, job_id: str, files: list[dict]) -> dict:
        """Un envoi d'un import ouvert (open_import), enregistré tout de suite ; NotFound : import inconnu ou fini.
        Un envoi qui échoue laisse l'import ouvert : ce qui est déjà enregistré rejoint les analyses à close_import."""
        job = self._imports.get(job_id)
        if job is None or job.state != "running" or job.step != "lecture":
            raise NotFound(job_id)
        self._import_part(job, files[:MAX_IMPORT_FILES])
        return job.view()

    def close_import(self, job_id: str) -> dict:
        """Le dernier envoi est arrivé : les analyses se mettent à jour en arrière-plan (import_status)."""
        job = self._imports.get(job_id)
        if job is None or job.state != "running":
            raise NotFound(job_id)
        self._import_pool_of().submit(self._run_finish, job)
        return job.view()

    def _new_import(self) -> ImportJob:
        job = ImportJob(secrets.token_hex(8), db_hands.count(self.db, self.space_id))
        with self._lock:
            self._imports[job.id] = job
            for old in list(self._imports)[:-10]:  # les 10 derniers
                del self._imports[old]
        return job

    def _import_pool_of(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._import_pool is None:
                self._import_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import")
            return self._import_pool

    def _run_import(self, job: ImportJob, files: list[dict]) -> None:
        try:
            job.finish(self.import_files(files, job))
        except Exception as exc:  # noqa: BLE001 — l'import échoue, la page le dit
            traceback.print_exc()
            job.fail(f"Import impossible : {exc}")

    def _run_finish(self, job: ImportJob) -> None:
        try:
            job.finish(self._finish_import(job))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            job.fail(f"Import impossible : {exc}")

    def import_status(self, job_id: str) -> Optional[dict]:
        """L'avancement d'un import lancé par start_import (None : inconnu)."""
        job = self._imports.get(job_id)
        return job.view() if job is not None else None

    def _import_zip(self, name: str, data: bytes, results: list[dict], job: Optional[ImportJob] = None,
                    removed: frozenset = frozenset()) -> int:
        try:
            content, reason = read_zip(data), ""
        except ValueError as exc:  # illisible, trop grosse
            content, reason = None, str(exc)
        if content is None:
            results.append({"name": name, "status": reason, "hands": 0, "new": 0})
            return 0
        start = len(results)
        added = sum(self._import_one(f"{name} › {path}"[:300], text, results, job, removed)
                    for path, text in content.files)
        results.extend({"name": f"{name} › {path}"[:300], "status": "illisible (chiffré ou abîmé)", "hands": 0, "new": 0}
                       for path in content.unreadable)
        parts = [f"{len(content.files)} historique(s)"]
        if content.ignored:
            parts.append(f"{content.ignored} autre(s) fichier(s) laissé(s) de côté")
        results.append({"name": name, "status": "archive : " + ", ".join(parts), "archive": True,
                        "hands": sum(r["hands"] for r in results[start:]), "new": added})
        return added

    def _import_one(self, name: str, content, results: list[dict], job: Optional[ImportJob] = None,
                    removed: frozenset = frozenset()) -> int:
        """Importe un historique ; removed : les pseudos supprimés, dont les mains sont écartées (comptées à part)."""
        if not isinstance(content, str) or not content.strip():
            results.append({"name": name, "status": "vide", "hands": 0, "new": 0})
            if job is not None and isinstance(content, str):
                job.file_done(0)
            return 0
        try:
            # le nom d'origine n'est qu'une étiquette (jamais un chemin) : celui du fichier, ou sa place dans l'archive
            label = name if " › " in name else Path(name.replace("\\", "/")).name
            read, new, _ = db_hands.import_text(self.db, self.space_id, label, content,
                                                on_hands=job.reading if job is not None else None)
        except ValueError:
            results.append({"name": name, "status": "format non reconnu", "hands": 0, "new": 0})
            return 0
        finally:
            if job is not None:
                job.file_done(count_hands(content))
        detail = {"sites": sorted(read.sites), "formats": read.formats}
        skipped = sum(read.heroes[pseudo] for pseudo in removed) if removed else 0
        status = ("importé" if new else "écarté (pseudo supprimé)" if skipped else "déjà importé" if read.hands
                  else "aucune main lue")  # (aucune main lue : ex. des tournois)
        results.append(dict(detail, name=name, status=status, hands=read.hands, new=new, skipped=skipped))
        return new
