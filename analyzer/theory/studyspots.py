"""Spots d'étude : des situations résolues sans main jouée, pour travailler par texture de flop.

Une famille fixe le préflop (ex. SRP : open du bouton à 2,5 bb, call de la BB, 100 bb) ; les ranges
viennent de la solution préflop, l'arbre est le même que pour les mains jouées (sans tailles jouées).
Chaque flop est classé par texture : pairé, monotone, sinon par sa plus haute carte.

Les tailles de mise d'un flop se choisissent par situation (voir sizing.py) ; le choix est gardé dans la base
(documents « tailles »), ou livré avec Analyzer (data/<famille>_tailles.json), et le spot l'utilise.
"""
from __future__ import annotations

import itertools
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import db
from ..cards import RANK_VALUE
from ..db import documents
from . import postflop, sizing
from .preflop import Solution, load_solution

TEXTURES = ("Pairé", "Monotone", "Ace high", "King high", "Queen high", "Jack high", "Ten high", "Low board")
HIGH_CARD = {14: "Ace high", 13: "King high", 12: "Queen high", 11: "Jack high", 10: "Ten high"}

FAMILIES = {
    "srp": {"name": "SRP", "label": "Pot relancé simple : open du bouton à 2,5 bb, call de la BB, 100 bb",
            "ip": ("sb_open", "raise"), "oop": ("bb_vs_open", "call"), "pot": 5.0, "stack": 97.5,
            "oop_initiative": False, "steps": (("BTN", "raise"), ("BB", "call"))},
    # La solution préflop 3bette à 11,5 bb (bb_vs_open) : pot de 23 bb, 88,5 bb derrière.
    "3bet": {"name": "pot 3bet",
             "label": "Pot 3bet : open du bouton à 2,5 bb, 3bet de la BB à 11,5 bb, call du bouton, 100 bb",
             "ip": ("sb_vs_3bet", "call"), "oop": ("bb_vs_open", "raise"), "pot": 23.0, "stack": 88.5,
             "oop_initiative": True, "steps": (("BTN", "raise"), ("BB", "raise"), ("BTN", "call"))},
    # 4bet du bouton à 26 bb (sb_vs_3bet), payé par la BB : pot de 52 bb, 74 bb derrière.
    "4bet": {"name": "pot 4bet",
             "label": "Pot 4bet : open du bouton à 2,5 bb, 3bet de la BB à 11,5 bb, 4bet du bouton à 26 bb, "
                      "call de la BB, 100 bb",
             "ip": ("sb_vs_3bet", "raise"), "oop": ("bb_vs_4bet", "call"), "pot": 52.0, "stack": 74.0,
             "oop_initiative": False,
             "steps": (("BTN", "raise"), ("BB", "raise"), ("BTN", "raise"), ("BB", "call"))},
}

# Spots d'étude des tables à plusieurs (6-max, 100 bb) : un autre jeu que le heads-up, aux ranges bien plus serrées.
# Ranges : tes charts 6-max (ring_ranges, dans la base). Tailles préflop : open à 2,5 bb (celui des
# charts), 3bet à 3 fois l'open en position (7,5 bb) et 4 fois hors de position (10 bb), 4bet à 22 bb en position et
# 20 bb hors de position ; la blinde d'un joueur qui a foldé reste au pot.
RING_FORMAT = "6-max"
OPEN, THREEBET, FOURBET = 2.5, {"ip": 7.5, "oop": 10.0}, {"ip": 22.0, "oop": 20.0}
POT_NAMES = {"srp": "SRP", "3bet": "pot 3bet", "4bet": "pot 4bet"}


def _de(pos: str) -> str:
    return ("de la " if pos in ("SB", "BB") else "du ") + pos


def _ring_family(oop: str, ip: str, kind: str, opener: str) -> tuple[str, dict]:
    other = ip if opener == oop else oop
    aggressor = opener if kind != "3bet" else other
    put = {"srp": OPEN, "3bet": THREEBET["ip" if other == ip else "oop"],
           "4bet": FOURBET["ip" if opener == ip else "oop"]}[kind]
    dead = (0.5 if "SB" not in (oop, ip) else 0.0) + (1.0 if "BB" not in (oop, ip) else 0.0)
    steps = ((opener, "raise"), (other, "call")) if kind == "srp" else \
        ((opener, "raise"), (other, "raise"), (opener, "call")) if kind == "3bet" else \
        ((opener, "raise"), (other, "raise"), (opener, "raise"), (other, "call"))
    words = [f"open {_de(opener)} à 2,5 bb"]
    if kind != "srp":
        words.append(f"3bet {_de(other)} à {THREEBET['ip' if other == ip else 'oop']:g} bb".replace(".", ","))
    if kind == "4bet":
        words.append(f"4bet {_de(opener)} à {put:g} bb")
    words.append(f"call {_de(steps[-1][0])}")
    structure = f"{kind}_{'oop' if aggressor == oop else 'ip'}"
    family = f"6max_{oop.lower()}_{ip.lower()}_{kind}"
    return family, {
        "name": POT_NAMES[kind], "pair": f"{oop} contre {ip}", "group": RING_FORMAT, "kind": kind,
        "label": f"6-max, {oop} contre {ip}, {POT_NAMES[kind]} : " + ", ".join(words) + ", 100 bb",
        "oop": oop, "ip": ip, "pot": round(2 * put + dead, 2), "stack": round(100 - put, 2),
        "oop_initiative": aggressor == oop, "steps": steps, "structure": structure,
    }


RING_FAMILIES = dict(_ring_family(*args) for args in (
    ("SB", "BB", "srp", "SB"), ("SB", "BB", "3bet", "SB"), ("SB", "BB", "4bet", "SB"),
    ("SB", "BTN", "3bet", "BTN"), ("SB", "BTN", "4bet", "BTN"),
    ("BB", "BTN", "srp", "BTN"), ("BB", "BTN", "3bet", "BTN"), ("BB", "BTN", "4bet", "BTN"),
    ("BB", "CO", "3bet", "CO"), ("BB", "CO", "4bet", "CO"),
))
for _family, _info in RING_FAMILIES.items():
    sizing.register(_family, _info["structure"], _info["oop"], _info["ip"])


def ring_spot_ranges(family: str) -> dict[str, dict[str, float]]:
    """Les ranges d'une famille 6-max, tirées de tes charts ; postflop.Unsupported s'ils manquent."""
    from . import ring_ranges
    info = RING_FAMILIES[family]
    found = ring_ranges.lookup(RING_FORMAT, list(info["steps"]))
    if found is None or any(p not in found[1] for p in (info["oop"], info["ip"])):
        raise postflop.Unsupported(f"Pas de ranges 6-max pour « {ring_ranges.describe(list(info['steps']))} » : "
                                   "charge les charts 6-max (onglet Tables à plusieurs de Mon jeu) ou "
                                   f"{ring_ranges.missing_hint(RING_FORMAT)}.")
    return {p: dict(found[1][p]) for p in (info["oop"], info["ip"])}


HU_STRUCTURES = {"srp": "srp_ip", "3bet": "3bet_oop", "4bet": "4bet_ip"}  # qui a l'initiative, hors de position ou non


def size_families(heads_up: bool, oop: str, ip: str, pot_type: str, oop_initiative: bool) -> list[str]:
    """Les familles dont les tailles choisies servent à l'arbre d'un coup joué, de la plus proche à la moins
    proche : en heads-up, la famille heads-up de même structure ; à une table à plusieurs, la famille 6-max de ces
    positions, les autres familles 6-max de même structure, puis la famille heads-up de même structure."""
    kind = {"SRP": "srp", "pot 3bet": "3bet", "pot 4bet": "4bet"}.get(pot_type)
    if kind is None:
        return []
    structure = f"{kind}_{'oop' if oop_initiative else 'ip'}"
    hu = [f for f in FAMILIES if HU_STRUCTURES[f] == structure]
    if heads_up:
        return hu
    pair = f"6max_{oop.lower()}_{ip.lower()}_{kind}"
    ring = [f for f, info in RING_FAMILIES.items() if info["structure"] == structure and f != pair]
    return ([pair] if pair in RING_FAMILIES else []) + ring + hu


def family_title(family: str) -> str:
    """« heads-up, SRP » ou « 6-max, BB contre BTN, pot 3bet »."""
    info = family_info(family)
    return f"6-max, {info['pair']}, {info['name']}" if family in RING_FAMILIES else f"heads-up, {info['name']}"


def family_info(family: str) -> dict:
    """Une famille de spots : heads-up (FAMILIES) ou d'une table à plusieurs (RING_FAMILIES) ; KeyError sinon."""
    return FAMILIES[family] if family in FAMILIES else RING_FAMILIES[family]


def known_family(family: str) -> bool:
    return family in FAMILIES or family in RING_FAMILIES


# Trois flops par texture : sec, connecté, deux couleurs (ou leurs équivalents pour pairé et monotone).
SRP_FLOPS = {
    "Pairé": ("KsKd4c", "8h8c5s", "Jd3c3h"),
    "Monotone": ("As8s3s", "Jh9h5h", "7d5d2d"),
    "Ace high": ("As7h2d", "AcKd9h", "Ah6h4c"),
    "King high": ("Ks8d3h", "KhQc9d", "Kd7d5c"),
    "Queen high": ("Qs7d2h", "QhJc9d", "Qc8c4d"),
    "Jack high": ("Js6d3h", "JhTc8d", "Jc9c4d"),
    "Ten high": ("Ts5d2h", "Th9c7d", "Tc8c3d"),
    "Low board": ("9s5d2h", "8h7c5d", "6c4c2d"),
}


def cards_of(board: str) -> list[str]:
    return [board[i:i + 2] for i in range(0, len(board), 2)]


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def find_texture(name: str) -> Optional[str]:
    """Texture d'après son nom, sans tenir compte des majuscules ni des accents (« paire », « ace high »)."""
    return next((t for t in TEXTURES if _plain(t) == _plain(name)), None)


def flop_texture(board: list[str]) -> str:
    """Pairé, puis monotone, sinon la plus haute carte (Ace high … Ten high, Low board pour 9 et moins)."""
    ranks = [c[0] for c in board[:3]]
    if len(set(ranks)) < 3:
        return "Pairé"
    if len({c[1] for c in board[:3]}) == 1:
        return "Monotone"
    return HIGH_CARD.get(max(RANK_VALUE[r] for r in ranks), "Low board")


FLOPS = {"srp": SRP_FLOPS, "3bet": SRP_FLOPS, "4bet": SRP_FLOPS}  # les mêmes flops d'une famille à l'autre
FLOPS.update({family: SRP_FLOPS for family in RING_FAMILIES})  # et en 6-max, pour comparer au heads-up

# Durée d'une résolution sur 4 cœurs, tailles déjà choisies (mesurée sur K♠K♦4♣).
SOLVE_TIME = {"srp": "une dizaine de minutes", "3bet": "2 à 3 minutes", "4bet": "moins d'une minute"}
# Durée du choix des tailles d'un flop sur 4 cœurs (mesurée sur K♠K♦4♣).
CHOOSE_TIME = {"srp": "1 h 10 environ", "3bet": "25 minutes environ", "4bet": "2 minutes environ"}
for _family, _info in RING_FAMILIES.items():  # ranges plus serrées qu'en heads-up : au plus aussi long
    SOLVE_TIME[_family] = "au plus " + SOLVE_TIME[_info["kind"]]
    CHOOSE_TIME[_family] = "au plus " + CHOOSE_TIME[_info["kind"]].replace(" environ", "")


def board_text(board: list[str]) -> str:
    return "".join(c[0] + {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}[c[1]] for c in board)


def suit_pattern(board: list[str]) -> str:
    """Structure de couleurs du flop : rainbow, deux couleurs (tirage couleur possible) ou monotone."""
    return {3: "rainbow", 2: "deux couleurs", 1: "monotone"}[len({c[1] for c in board[:3]})]


def canonical(board: list[str]) -> str:
    """Forme commune des flops identiques aux couleurs près (même stratégie, couleurs renommées)."""
    best = None
    for perm in itertools.permutations("cdhs"):
        rename = dict(zip("cdhs", perm))
        cards = sorted((c[0] + rename[c[1]] for c in board[:3]), key=lambda c: (-RANK_VALUE[c[0]], c[1]))
        text = "".join(cards)
        if best is None or text < best:
            best = text
    return best


def flop_distance(a: list[str], b: list[str]) -> int:
    """Écart entre deux flops : texture, structure de couleurs, puis hauteur de chaque carte."""
    ra = sorted((RANK_VALUE[c[0]] for c in a[:3]), reverse=True)
    rb = sorted((RANK_VALUE[c[0]] for c in b[:3]), reverse=True)
    distance = sum(abs(x - y) for x, y in zip(ra, rb))
    if flop_texture(a) != flop_texture(b):
        distance += 20
    if suit_pattern(a) != suit_pattern(b):
        distance += 10
    return distance


def flop_set(family: str = "srp", textures: Optional[list[str]] = None) -> list[str]:
    """Flops de la série, un par texture à tour de rôle : une série interrompue couvre déjà chaque texture."""
    flops = FLOPS[family]
    wanted = [t for t in TEXTURES if t in flops and (not textures or t in textures)]
    rounds = max(len(flops[t]) for t in wanted) if wanted else 0
    return [flops[t][k] for k in range(rounds) for t in wanted if k < len(flops[t])]


@dataclass
class StudySpot(postflop.SpotTree):
    family: str
    board: list[str]
    solution: Optional[Solution] = field(default=None, repr=False)
    oop: str = "BB"
    ip: str = "BTN"
    line: list = field(default_factory=list)
    # Situation -> tailles (voir native/arbre.rs). None : les tailles choisies pour ce flop s'il y en a ; hors
    # série, celles du flop le plus proche dont les tailles sont choisies ; sinon l'arbre par défaut.
    # {} : toujours l'arbre par défaut.
    plan: Optional[dict] = None
    sizes_from: Optional[str] = field(default=None, repr=False)  # flop dont les tailles sont empruntées
    # True : tes ranges ajustées pour ce spot (custom_ranges, portée « coup ») ; les séries, plans et références
    # restent faits avec les ranges de la solution.
    custom: bool = field(default=False, repr=False)

    def __post_init__(self):
        from . import custom_ranges
        info = family_info(self.family)
        self.name, self.label = info["name"], info["label"]
        self.pot_bb, self.stack_bb = info["pot"], info["stack"]
        if self.family in RING_FAMILIES:
            self.oop, self.ip = info["oop"], info["ip"]
            self.ranges = ring_spot_ranges(self.family)
            self.context = custom_ranges.context(RING_FORMAT, info["steps"])
        else:
            solution = self.solution or load_solution()
            self.ranges = {self.oop: postflop.range_weights(solution, *info["oop"]),
                           self.ip: postflop.range_weights(solution, *info["ip"])}
            self.context = custom_ranges.context("HU", info["steps"])
        self.reference = {p: dict(r) for p, r in self.ranges.items()}
        self.adjusted = None
        if self.custom:
            scope, mine = custom_ranges.lookup(self.ident, None)
            for p in (self.oop, self.ip):
                if p in mine:
                    self.ranges[p], self.adjusted = dict(mine[p]), scope
        self.sizes = postflop.default_sizes(self.oop, self.ip, info["oop_initiative"])
        if self.plan is None:
            board = "".join(self.board)
            chosen = load_selection(self.family, board)
            # Un flop de la série a son propre choix des tailles (fait avant sa résolution) ; un autre flop
            # prend celles du flop le plus proche, pour rester comparable à la série.
            if chosen is None and board not in flop_set(self.family):
                near = closest_selection(self.family, self.board)
                if near is not None:
                    self.sizes_from, chosen = near
            self.plan = dict(chosen["plan"]) if chosen else {}

    def menu_text(self) -> str:
        text = sizing.plan_text(self.plan, self.family) if self.plan else super().menu_text()
        if self.sizes_from:
            text += " (tailles de " + board_text(cards_of(self.sizes_from)) + ")"
        return text

    @property
    def ident(self) -> str:
        return f"spot:{self.family}:{''.join(self.board)}"

    @property
    def texture(self) -> str:
        return flop_texture(self.board)

    def interpret(self, raw: dict) -> dict:
        return {"spot": True, "pot_type": self.name, "pot": self.pot_bb, "stack": self.stack_bb,
                "board": self.board, "texture": self.texture, "oop": None, "menu": self.menu_text(), "added": [],
                "adjusted": self.adjusted,
                "iterations": raw.get("iterations"), "exploit_pct": raw.get("exploit_pct"),
                "seconds": raw.get("seconds"), "tree_nodes": raw.get("tree_nodes"), "stopped": None,
                "decisions": []}

    def after_solve(self, session: postflop.Session) -> None:
        """Juste après la résolution, l'étude encore ouverte : son plan de jeu (quelques secondes)."""
        from . import coach
        if not self.adjusted and self.family in FAMILIES:  # les plans de jeu : la théorie, en heads-up
            coach.extract_and_save(session, self)

    def write_meta(self, request: dict, raw: dict, session: Optional[postflop.Session] = None) -> None:
        if self.adjusted:  # étude à part, avec les coups joués : hors des séries et des plans
            meta = {
                "kind": "spot-ajuste", "key": postflop.study_key(request), "base": postflop.base_key(request),
                "hand": self.ident, "id": self.ident,
                "date": "Spot " + self.name, "villain": "–", "hero_cards": [], "board": self.board,
                "pot_type": self.name, "hero_position": None, "pot": self.pot_bb, "stack": self.stack_bb,
                "net": None, "iterations": raw.get("iterations"), "exploit_pct": raw.get("exploit_pct"),
                "seconds": raw.get("seconds"), "menu": self.menu_text(), "created": time.strftime("%d/%m/%Y %H:%M"),
                "adjusted": self.adjusted,
            }
            postflop.save_study_meta(request, meta)
            return
        meta = {
            "kind": "spot", "key": postflop.study_key(request), "base": postflop.base_key(request), "id": self.ident,
            "family": self.family,
            "family_label": self.label, "pot_type": self.name, "texture": self.texture, "board": self.board,
            "pot": self.pot_bb, "stack": self.stack_bb, "iterations": raw.get("iterations"),
            "exploit_pct": raw.get("exploit_pct"), "seconds": raw.get("seconds"), "menu": self.menu_text(),
            "created": time.strftime("%d/%m/%Y %H:%M"), "sizes_from": self.sizes_from,
        }
        if session is not None:
            meta["summary"] = flop_summary(session, self.family)
        postflop.save_study_meta(request, meta)


def _entry(title: str, node: dict, path: list) -> dict:
    freqs, _ = postflop.node_summary(node)
    return {"title": title, "path": path, "freqs": freqs,
            "actions": [{"label": label, "kind": a["kind"], "allin": a.get("allin", False)}
                        for a, label in zip(node["actions"], postflop.action_labels(node))]}


def _index(node: dict, kind: str) -> Optional[int]:
    return next((i for i, a in enumerate(node["actions"]) if a["kind"] == kind), None)


# Synthèse du flop d'une famille : (titre, actions depuis la racine). Une étape n'apparaît que si son nœud
# laisse un choix (en SRP, la BB qui ne mène pas ne fait que checker).
SUMMARY = {
    "srp": (("BB au flop", []), ("C-bet du BTN", ["check"]), ("BB face à la c-bet", ["check", "bet"]),
            ("BTN face au check-raise", ["check", "bet", "raise"])),
    "4bet": (("BB au flop", []), ("C-bet du BTN", ["check"]), ("BB face à la c-bet", ["check", "bet"]),
             ("BTN face au check-raise", ["check", "bet", "raise"])),
    "3bet": (("C-bet de la BB", []), ("BTN face à la c-bet", ["bet"]), ("BB face à la relance", ["bet", "raise"]),
             ("Stab du BTN", ["check"]), ("BB face au stab", ["check", "bet"])),
}
for _family, _info in RING_FAMILIES.items():  # même synthèse que la famille heads-up de même structure
    _model = SUMMARY["3bet" if _info["oop_initiative"] else "srp"]
    SUMMARY[_family] = tuple((sizing.rename_roles(title, _info["oop"], _info["ip"]), kinds) for title, kinds in _model)


def flop_summary(session: postflop.Session, family: str = "srp") -> list[dict]:
    """Stratégies de toute la range aux nœuds principaux du flop (c-bet, réponses, stab…)."""
    nodes: dict[str, dict] = {}

    def node_at(path: list) -> dict:
        key = json.dumps(path)
        if key not in nodes:
            nodes[key] = session.node(path)
        return nodes[key]

    out = []
    for title, kinds in SUMMARY[family]:
        node, path = node_at([]), []
        for kind in kinds:
            k = _index(node, kind) if node["type"] == "action" else None
            if k is None:
                node = None
                break
            path = path + [{"type": "action", "index": k}]
            node = node_at(path)
        if node is not None and node["type"] == "action" and len(node["actions"]) > 1:
            out.append(_entry(title, node, path))
    return out


def _current(meta: dict) -> Optional[bool]:
    """L'étude correspond-elle à l'arbre actuel du spot (tailles choisies ou par défaut) ? None : on ne peut
    pas le dire (spot 6-max sans tes charts)."""
    try:
        spot = parse_ident(meta.get("id", ""))
    except postflop.Unsupported:
        return None
    if spot is None:
        return False
    request = spot.request()
    if meta.get("base"):  # la précision de la résolution ne compte pas
        return meta["base"] == postflop.base_key(request)
    return meta.get("key") == postflop.study_key(request)


_SPOT_STUDIES: dict[tuple, dict[str, dict]] = {}


DEPENDS = ("tailles", "ranges", "ranges-ajustees", postflop.SETTINGS)  # ce dont dépend l'arbre d'un spot


def _studies_signature() -> tuple:
    """Ce dont dépend la liste des spots d'étude à jour : les études, les tailles choisies, les ranges et la
    précision (leurs révisions dans la base), et les arbres présents dans le dossier des études."""
    folder = postflop.studies_dir()
    try:
        stamp = folder.stat().st_mtime_ns  # un arbre ajouté ou effacé à la main
    except OSError:
        stamp = 0
    return documents.revision(db.current(), "etudes", *DEPENDS) + (str(folder), stamp)


def spot_studies(families: Optional[tuple[str, ...]] = None) -> dict[str, dict]:
    """Fiches des spots d'étude enregistrés avec l'arbre actuel de leur spot, par identifiant ; par défaut ceux
    des familles heads-up (plans de jeu, entraîneur, coach et leakfinding ne connaissent qu'elles). Recalculées
    seulement quand une étude, un choix de tailles, une range ou la précision change."""
    wanted = families or tuple(FAMILIES)
    key = (wanted, _studies_signature())
    if key not in _SPOT_STUDIES:
        if len(_SPOT_STUDIES) > 32:
            _SPOT_STUDIES.clear()
        _SPOT_STUDIES[key] = {m["id"]: m for m in postflop.list_studies()
                              if m.get("kind") == "spot" and m.get("family") in wanted and _current(m)}
    return {k: dict(v) for k, v in _SPOT_STUDIES[key].items()}


def stale_spot_studies() -> list[dict]:
    """Études de spots faites avec un autre arbre (avant le choix des tailles, par exemple)."""
    return [m for m in postflop.list_studies() if m.get("kind") == "spot" and _current(m) is False]


# --- Tailles choisies ------------------------------------------------------------------------

def selection_key(family: str, board: str) -> str:
    return f"{family}:{board}"


def shipped_path(family: str) -> Path:
    return Path(__file__).parent / "data" / f"{family}_tailles.json"


_JSON: dict[Path, tuple[tuple[int, int], object]] = {}


def _read_json(path: Path) -> Optional[object]:
    """Un fichier JSON (relu seulement quand il change) ; None s'il manque ou ne se lit pas."""
    try:
        stat = path.stat()
    except OSError:
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    known = _JSON.get(path)
    if known is None or known[0] != stamp:
        try:
            known = (stamp, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            known = (stamp, None)
        _JSON[path] = known
    return known[1]


def load_selection(family: str, board: str) -> Optional[dict]:
    """Choix des tailles d'un flop : fait ici (dans la base), sinon livré avec Analyzer ; None sinon."""
    local = documents.get(db.current(), "tailles", selection_key(family, board))
    if isinstance(local, dict):
        return dict(local, source="local")
    shipped = _read_json(shipped_path(family))
    chosen = shipped.get("flops", {}).get(board) if isinstance(shipped, dict) else None
    if chosen:
        return dict(chosen, source="livré")
    return None


def selection_boards(family: str) -> list[str]:
    """Flops dont les tailles sont choisies : ici (dans la base) ou livrées avec Analyzer."""
    boards = {key.split(":", 1)[1] for key in documents.keys(db.current(), "tailles", family + ":")}
    shipped = _read_json(shipped_path(family))
    if isinstance(shipped, dict):
        boards |= set(shipped.get("flops", {}))
    return sorted(b for b in boards if re.fullmatch(r"(?:[2-9TJQKA][cdhs]){3}", b))


def closest_selection(family: str, board: list[str]) -> Optional[tuple[str, dict]]:
    """Le flop le plus proche dont les tailles sont choisies, et ce choix ; None s'il n'y en a aucun."""
    own = "".join(board)
    boards = [b for b in selection_boards(family) if b != own]
    for near in sorted(boards, key=lambda b: (flop_distance(cards_of(b), board), b)):
        chosen = load_selection(family, near)
        if chosen and chosen.get("plan"):
            return near, chosen
    return None


_CHOICES: dict[str, tuple] = {}  # famille -> (signature, {flop: choix})


def _choices(family: str) -> dict[str, dict]:
    """Les choix de tailles d'une famille (faits ici ou livrés), par flop ; relus quand un choix change (les
    analyses en lot construisent un spot par main)."""
    shipped = shipped_path(family)
    signature = documents.revision(db.current(), "tailles") + ((shipped.stat().st_mtime_ns,) if shipped.is_file() else ())
    cached = _CHOICES.get(family)
    if cached and cached[0] == signature:
        return cached[1]
    choices = {}
    for board in selection_boards(family):
        chosen = load_selection(family, board)
        if chosen and chosen.get("plan"):
            choices[board] = chosen
    _CHOICES[family] = (signature, choices)
    return choices


def _nearest(family: str, board: list[str]) -> Optional[tuple[dict, str, bool]]:
    choices = _choices(family)
    if not choices:
        return None
    own = canonical(board[:3])
    for near, chosen in choices.items():
        if canonical(cards_of(near)) == own:
            return chosen, near, True
    near = min(choices, key=lambda b: (flop_distance(cards_of(b), board[:3]), b))
    return choices[near], near, False


def sizes_for(family: str, board: list[str]) -> Optional[tuple[dict, str, bool]]:
    """Les tailles théoriques d'un flop : (plan, flop dont elles viennent, True si c'est ce flop aux couleurs
    près). Sans choix pour ce flop, celles du flop choisi le plus proche (même texture et mêmes couleurs
    d'abord) ; None si la famille n'a encore aucun choix."""
    found = _nearest(family, board)
    return (dict(found[0]["plan"]), found[1], found[2]) if found else None


def hand_sizes_for(family: str, board: list[str]) -> Optional[tuple[dict, str, bool]]:
    """Les tailles théoriques pour l'arbre d'un coup joué : celles du flop (sizes_for), avec une seule taille par
    situation à la river (la plus employée des deux choisies) et sans relance à la river, sauf jouée, comme dans
    l'arbre par défaut. Avec les deux tailles et les relances, l'arbre d'un SRP compte 5 fois plus de nœuds
    (1,7 million, 6 Go) ; ainsi, moins de 2 fois."""
    found = _nearest(family, board)
    if not found:
        return None
    chosen, near, exact = found
    candidates = {s.key: s.candidates for s in sizing.situations(family)}
    plan = {}
    for key, sizes in chosen["plan"].items():
        if key.startswith("raise:r"):
            continue  # la configuration par défaut : pas de relance à la river, sauf jouée
        if key.startswith("bet:r") and len(sizes) > 1:
            usage = (chosen.get("report") or {}).get(key, {}).get("usage") or []
            options = candidates.get(key, [])
            used = {s: usage[options.index(s)] for s in sizes if s in options and len(usage) == len(options)}
            sizes = [max(sizes, key=lambda s: used.get(s, 0.0))] if used else sizes[:1]
        plan[key] = list(sizes)
    return plan, near, exact


def normalize_board(family: str, board: list[str]) -> list[str]:
    """Les trois cartes dans l'ordre d'un flop déjà connu (série, étude, tailles), sinon par hauteur : un même
    flop choisi dans un autre ordre ne fait pas une seconde étude."""
    wanted = set(board[:3])
    known = family_boards(family) + selection_boards(family)
    for other in known:
        if set(cards_of(other)) == wanted:
            return cards_of(other)
    return sorted(board[:3], key=lambda c: (-RANK_VALUE[c[0]], "shdc".index(c[1])))


def flop_options(family: str, board: list[str]) -> dict:
    """Le flop choisi dans l'explorateur, et les flops résolus qui s'en approchent : d'abord le même aux
    couleurs près, puis les mêmes hauteurs avec la même structure de couleurs, puis la même texture."""
    board = normalize_board(family, board)
    target = StudySpot(family, board)
    solved = spot_studies((family,))
    canon, pattern, texture = canonical(board), suit_pattern(board), target.texture
    ranks = sorted(c[0] for c in board)
    suggestions = []
    for ident, meta in solved.items():
        other = meta["board"]
        if ident == target.ident:
            continue
        same_pattern = suit_pattern(other) == pattern
        if canonical(other) == canon:
            order, relation = 0, "le même flop aux couleurs près : même stratégie"
        elif sorted(c[0] for c in other) == ranks and same_pattern:
            order, relation = 1, "mêmes hauteurs et même structure de couleurs (la couleur commune change de carte)"
        elif flop_texture(other) == texture and same_pattern:
            order, relation = 2, "même texture, même structure de couleurs"
        elif flop_texture(other) == texture:
            order, relation = 3, "même texture"
        else:
            continue
        suggestions.append({"id": ident, "board": other, "texture": flop_texture(other),
                            "pattern": suit_pattern(other), "relation": relation, "order": order,
                            "distance": flop_distance(other, board)})
    suggestions.sort(key=lambda x: (x["order"], x["distance"], x["id"]))
    series = "".join(board) in flop_set(family)
    if series and not target.plan:
        cost = "flop de la série : choix des tailles d'abord, puis résolution"
    else:
        cost = SOLVE_TIME[family] + " sur 4 cœurs"
    return {"id": target.ident, "board": board, "texture": texture, "pattern": pattern,
            "solved": target.ident in solved, "series": series, "suggestions": suggestions[:6],
            "sizes": target.menu_text(), "sizes_from": target.sizes_from, "cost": cost}


def save_selection(family: str, board: str, result: dict) -> None:
    documents.put(db.current(), "tailles", selection_key(family, board), dict(result, family=family, board=board))


def export_selections(family: str = "srp", path: Optional[Path] = None) -> Path:
    """Rassemble les choix de tailles faits ici (dans la base) dans le fichier livré avec Analyzer."""
    flops = {}
    for board in flop_set(family):
        local = documents.get(db.current(), "tailles", selection_key(family, board), memo=False)
        if isinstance(local, dict):
            flops[board] = local
    path = path or shipped_path(family)
    # Une ligne par flop : le fichier reste lisible sans grossir (le détail de chaque comparaison y est).
    lines = [f"  {json.dumps(board)}: {json.dumps(chosen, ensure_ascii=False, separators=(',', ':'))}"
             for board, chosen in flops.items()]
    body = ",\n".join(lines)
    path.write_text(f'{{"family": {json.dumps(family)}, "flops": {{\n{body}\n}}}}\n', encoding="utf-8")
    return path


def parse_ident(ident: str, custom: bool = False) -> Optional[StudySpot]:
    """« spot:srp:KsKd4c » -> le spot, si la famille existe et que le flop est valide ; sinon None.
    custom=True : avec tes ranges ajustées pour ce spot, s'il y en a (l'explorateur)."""
    if not is_ident(ident):
        return None
    _, family, board = ident.split(":")
    return StudySpot(family, cards_of(board), custom=custom)


def is_ident(ident: str) -> bool:
    """« spot:<famille>:<flop> » bien formé (sans construire le spot : un spot 6-max demande tes charts)."""
    parts = ident.split(":")
    return (len(parts) == 3 and parts[0] == "spot" and known_family(parts[1])
            and bool(re.fullmatch(r"(?:[2-9TJQKA][cdhs]){3}", parts[2])) and len(set(cards_of(parts[2]))) == 3)


def family_boards(family: str = "srp") -> list[str]:
    """Flops de la série, puis les autres flops étudiés dans cette famille (ouverts depuis l'explorateur)."""
    boards = flop_set(family)
    extra = sorted({"".join(m["board"]) for m in spot_studies((family,)).values()} - set(boards))
    return boards + extra


def reference_path(family: str = "srp") -> Path:
    return Path(__file__).parent / "data" / f"{family}_reference.json"


def reference(family: str = "srp") -> dict[str, dict]:
    """Synthèses de référence livrées avec Analyzer (stratégies au flop de la série), par identifiant.

    Seules celles calculées avec l'arbre actuel sont gardées (même clé d'étude) : elles s'affichent tant que
    le spot n'est pas résolu sur cet ordinateur."""
    path = reference_path(family)
    if not path.is_file():
        return {}
    spots = json.loads(path.read_text(encoding="utf-8")).get("spots", {})
    out = {}
    for ident, ref in spots.items():
        spot = parse_ident(ident)
        if spot is not None and ref.get("key") == postflop.study_key(spot.request()):
            out[ident] = ref
    return out


def export_reference(family: str = "srp", path: Optional[Path] = None) -> Path:
    """Écrit la référence de la série à partir des études de cet ordinateur (synthèses seulement, quelques Ko)."""
    spots, studies = {}, spot_studies((family,))
    for board in flop_set(family):
        spot = StudySpot(family, cards_of(board))
        meta = studies.get(spot.ident)
        if not meta or not meta.get("summary") or not _current(meta):
            continue
        spots[spot.ident] = {
            "key": meta["key"], "texture": spot.texture, "iterations": meta["iterations"],
            "exploit_pct": meta["exploit_pct"],
            "summary": [dict(e, freqs=[round(f, 4) for f in e["freqs"]]) for e in meta["summary"]],
        }
    path = path or reference_path(family)
    data = {"family": family, "label": family_info(family)["label"], "spots": spots}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def choose_sizes(family: str, board: str, log: Callable[[str], None] = print, threads: int = 0,
                 cards: int = sizing.TURN_CARDS, **options) -> dict:
    """Choisit les tailles de ce flop (voir sizing.py) et garde le choix."""
    result = sizing.Selection(StudySpot(family, cards_of(board), plan={}), log, threads, cards, **options).run()
    save_selection(family, board, result)
    return result


def solve_set(family: str = "srp", textures: Optional[list[str]] = None, log: Callable[[str], None] = print,
              iterations: int = postflop.DEFAULT_ITERATIONS, target: float = postflop.DEFAULT_TARGET,
              threads: int = 0, on_progress: Optional[Callable[[dict], None]] = None, choose: bool = True,
              solve: bool = True, cards: int = sizing.TURN_CARDS) -> int:
    """Résout les flops de la série qui ne le sont pas encore ; renvoie le nombre de spots résolus.

    choose=True choisit d'abord les tailles des flops qui n'en ont pas (long : de l'ordre de 45 minutes
    par flop sur 4 cœurs) ; solve=False s'arrête au choix des tailles."""
    done = spot_studies((family,))
    boards = flop_set(family, textures)
    solved = 0
    for k, board in enumerate(boards, 1):
        head = f"[{k}/{len(boards)}] {board} ({flop_texture(cards_of(board))})"
        if choose and load_selection(family, board) is None:
            log(f"{head} : choix des tailles…")
            result = choose_sizes(family, board, log, threads, cards)
            log(f"    {sizing.plan_text(result['plan'])} ({result['seconds'] // 60} min)")
        if not solve:
            continue
        spot = StudySpot(family, cards_of(board))
        meta = done.get(spot.ident)
        if meta and meta.get("summary"):
            log(f"{head} : déjà résolu")
            continue
        log(f"{head} : résolution…")
        request = spot.request(iterations, target, threads)
        session = postflop.Session(request)
        try:
            raw = session.start(on_progress=on_progress)
            spot.write_meta(request, raw, session)
        finally:
            session.close()
        log(f"    {raw['iterations']} itérations, {raw['exploit_pct']} % du pot, {raw['seconds']:.0f} s")
        solved += 1
    return solved
