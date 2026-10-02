"""Spots d'étude : des situations résolues sans main jouée, pour travailler par texture de flop.

Une famille fixe le préflop (ex. SRP : open du bouton à 2,5 bb, call de la BB, 100 bb) ; les ranges
viennent de la solution préflop, l'arbre est le même que pour les mains jouées (sans tailles jouées).
Chaque flop est classé par texture : pairé, monotone, sinon par sa plus haute carte.

Les tailles de mise d'un flop se choisissent par situation (voir sizing.py) ; le choix est gardé dans
~/.analyzer/tailles, ou livré avec Analyzer (data/<famille>_tailles.json), et le spot l'utilise.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..cards import RANK_VALUE
from . import postflop, sizing
from .preflop import Solution, load_solution

TEXTURES = ("Pairé", "Monotone", "Ace high", "King high", "Queen high", "Jack high", "Ten high", "Low board")
HIGH_CARD = {14: "Ace high", 13: "King high", 12: "Queen high", 11: "Jack high", 10: "Ten high"}

FAMILIES = {
    "srp": {"name": "SRP", "label": "Pot relancé simple : open du bouton à 2,5 bb, call de la BB, 100 bb",
            "ip": ("sb_open", "raise"), "oop": ("bb_vs_open", "call"), "pot": 5.0, "stack": 97.5,
            "oop_initiative": False},
}

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


FLOPS = {"srp": SRP_FLOPS}


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
    # Situation -> tailles (voir native/arbre.rs). None : les tailles choisies pour ce flop s'il y en a,
    # sinon l'arbre par défaut ; {} : toujours l'arbre par défaut.
    plan: Optional[dict] = None

    def __post_init__(self):
        info = FAMILIES[self.family]
        solution = self.solution or load_solution()
        self.name, self.label = info["name"], info["label"]
        self.pot_bb, self.stack_bb = info["pot"], info["stack"]
        self.ranges = {self.oop: postflop.range_weights(solution, *info["oop"]),
                       self.ip: postflop.range_weights(solution, *info["ip"])}
        self.sizes = postflop.default_sizes(self.oop, self.ip, info["oop_initiative"])
        if self.plan is None:
            chosen = load_selection(self.family, "".join(self.board))
            self.plan = dict(chosen["plan"]) if chosen else {}

    def menu_text(self) -> str:
        return sizing.plan_text(self.plan) if self.plan else super().menu_text()

    @property
    def ident(self) -> str:
        return f"spot:{self.family}:{''.join(self.board)}"

    @property
    def texture(self) -> str:
        return flop_texture(self.board)

    def interpret(self, raw: dict) -> dict:
        return {"spot": True, "pot_type": self.name, "pot": self.pot_bb, "stack": self.stack_bb,
                "board": self.board, "texture": self.texture, "oop": None, "menu": self.menu_text(), "added": [],
                "iterations": raw.get("iterations"), "exploit_pct": raw.get("exploit_pct"),
                "seconds": raw.get("seconds"), "tree_nodes": raw.get("tree_nodes"), "stopped": None,
                "decisions": []}

    def write_meta(self, request: dict, raw: dict, session: Optional[postflop.Session] = None) -> None:
        meta = {
            "kind": "spot", "key": postflop.study_key(request), "id": self.ident, "family": self.family,
            "family_label": self.label, "pot_type": self.name, "texture": self.texture, "board": self.board,
            "pot": self.pot_bb, "stack": self.stack_bb, "iterations": raw.get("iterations"),
            "exploit_pct": raw.get("exploit_pct"), "seconds": raw.get("seconds"), "menu": self.menu_text(),
            "created": time.strftime("%d/%m/%Y %H:%M"),
        }
        if session is not None:
            meta["summary"] = flop_summary(session)
        path = postflop.study_path(request).with_suffix(".json")
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _entry(title: str, node: dict, path: list) -> dict:
    freqs, _ = postflop.node_summary(node)
    return {"title": title, "path": path, "freqs": freqs,
            "actions": [{"label": label, "kind": a["kind"], "allin": a.get("allin", False)}
                        for a, label in zip(node["actions"], postflop.action_labels(node))]}


def _index(node: dict, kind: str) -> Optional[int]:
    return next((i for i, a in enumerate(node["actions"]) if a["kind"] == kind), None)


SUMMARY_STEPS = (("check", "C-bet du BTN"), ("bet", "BB face à la c-bet"), ("raise", "BTN face au check-raise"))


def flop_summary(session: postflop.Session) -> list[dict]:
    """Stratégies de toute la range au flop : c-bet du bouton après le check, réponse de la BB, puis du bouton
    face au check-raise (et la BB d'abord, si l'arbre lui laisse une mise)."""
    root = session.node([])
    out = [_entry("BB au flop", root, [])] if len(root["actions"]) > 1 else []
    node, path = root, []
    for kind, title in SUMMARY_STEPS:
        k = _index(node, kind)
        if k is None:
            break
        path = path + [{"type": "action", "index": k}]
        node = session.node(path)
        if node["type"] != "action":
            break
        out.append(_entry(title, node, path))
    return out


def _current(meta: dict) -> bool:
    """L'étude correspond-elle à l'arbre actuel du spot (tailles choisies ou par défaut) ?"""
    spot = parse_ident(meta.get("id", ""))
    return spot is not None and meta.get("key") == postflop.study_key(spot.request())


def spot_studies() -> dict[str, dict]:
    """Fiches des spots d'étude enregistrés avec l'arbre actuel de leur spot, par identifiant."""
    return {m["id"]: m for m in postflop.list_studies() if m.get("kind") == "spot" and _current(m)}


def stale_spot_studies() -> list[dict]:
    """Études de spots faites avec un autre arbre (avant le choix des tailles, par exemple)."""
    return [m for m in postflop.list_studies() if m.get("kind") == "spot" and not _current(m)]


# --- Tailles choisies ------------------------------------------------------------------------

def selection_path(family: str, board: str) -> Path:
    return postflop.home() / "tailles" / f"{family}-{board}.json"


def shipped_path(family: str) -> Path:
    return Path(__file__).parent / "data" / f"{family}_tailles.json"


def load_selection(family: str, board: str) -> Optional[dict]:
    """Choix des tailles d'un flop : fait sur cet ordinateur, sinon livré avec Analyzer ; None sinon."""
    path = selection_path(family, board)
    if path.is_file():
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")), source="local")
        except ValueError:
            pass
    shipped = shipped_path(family)
    if shipped.is_file():
        chosen = json.loads(shipped.read_text(encoding="utf-8")).get("flops", {}).get(board)
        if chosen:
            return dict(chosen, source="livré")
    return None


def save_selection(family: str, board: str, result: dict) -> Path:
    path = selection_path(family, board)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(result, family=family, board=board), ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def export_selections(family: str = "srp", path: Optional[Path] = None) -> Path:
    """Rassemble les choix de tailles faits sur cet ordinateur dans le fichier livré avec Analyzer."""
    flops = {}
    for board in flop_set(family):
        local = selection_path(family, board)
        if local.is_file():
            flops[board] = json.loads(local.read_text(encoding="utf-8"))
    path = path or shipped_path(family)
    path.write_text(json.dumps({"family": family, "flops": flops}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def parse_ident(ident: str) -> Optional[StudySpot]:
    """« spot:srp:KsKd4c » -> le spot, si la famille existe et que le flop est valide ; sinon None."""
    parts = ident.split(":")
    if len(parts) != 3 or parts[0] != "spot" or parts[1] not in FAMILIES:
        return None
    if not re.fullmatch(r"(?:[2-9TJQKA][cdhs]){3}", parts[2]) or len(set(cards_of(parts[2]))) != 3:
        return None
    return StudySpot(parts[1], cards_of(parts[2]))


def family_boards(family: str = "srp") -> list[str]:
    """Flops de la série, puis les autres flops étudiés dans cette famille (ouverts depuis l'explorateur)."""
    boards = flop_set(family)
    extra = sorted({"".join(m["board"]) for m in spot_studies().values() if m.get("family") == family} - set(boards))
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
    spots, studies = {}, spot_studies()
    for board in flop_set(family):
        spot = StudySpot(family, cards_of(board))
        meta = studies.get(spot.ident)
        if not meta or not meta.get("summary") or meta["key"] != postflop.study_key(spot.request()):
            continue
        spots[spot.ident] = {
            "key": meta["key"], "texture": spot.texture, "iterations": meta["iterations"],
            "exploit_pct": meta["exploit_pct"],
            "summary": [dict(e, freqs=[round(f, 4) for f in e["freqs"]]) for e in meta["summary"]],
        }
    path = path or reference_path(family)
    data = {"family": family, "label": FAMILIES[family]["label"], "spots": spots}
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
    done = spot_studies()
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
