"""Résolution postflop d'une main avec le moteur de GTOpen.

GTOpen (https://github.com/MatthewPDingle/GTOpen, de Matthew Dingle) n'est pas copié dans
Analyzer : il est récupéré à part (`python -m analyzer gtopen --installer`), puis un petit
programme, `analyzer-solve` (source : native/main.rs), est compilé contre son moteur. Il résout
un spot et suit la ligne réellement jouée.

Le spot d'une main :
- ranges de départ tirées de la solution préflop (open / call, 3bet / call, 4bet / call) ;
- board, pot et tapis effectif au flop, en bb ;
- un menu de tailles simple, complété par les tailles réellement jouées pour que chaque
  décision de la main tombe sur une branche de l'arbre.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..cards import combo_notation
from ..models import BET, CALL, CHECK, FOLD, RAISE, VOLUNTARY, Hand
from .extract import hand_at
from .preflop import MAIN_THRESHOLD, MIXED_THRESHOLD, Solution, load_solution

GTOPEN_URL = "https://github.com/MatthewPDingle/GTOpen"
GTOPEN_COMMIT = "b69ea07c79884fc598757dc45c712e73976db810"  # version de GTOpen testée avec analyzer-solve
GTOPEN_PATHS = ("/crates/solver/", "/cache/contextual/")  # le moteur ; les fichiers qu'il lit sont ajoutés
INCLUDE_RE = re.compile(r'include_(?:str|bytes)!\(\s*"([^"]+)"\s*\)')
NATIVE_DIR = Path(__file__).parent / "native"  # sources du pont analyzer-solve (main.rs, arbre.rs)
EXE = "analyzer-solve" + (".exe" if os.name == "nt" else "")
INSTALL_COMMAND = "python -m analyzer gtopen --installer"

STREET_INDEX = {"flop": 0, "turn": 1, "river": 2}
STREET_CODE = {"flop": "f", "turn": "t", "river": "r"}
STREET_NAME = {"flop": "Flop", "turn": "Turn", "river": "River"}
DEFAULT_BETS = (33.0, 75.0, 75.0)  # % du pot, flop / turn / river
# Relance (% du pot après le call) proposée par défaut au flop et à la turn ; à la river, seules
# les relances réellement jouées entrent dans l'arbre (le diviser par deux en taille et en temps).
DEFAULT_RAISES = ((60.0,), (60.0,), ())
SIZE_MERGE = 10.0  # une taille jouée remplace la taille par défaut à moins de 10 points
MIN_WEIGHT = 0.002  # poids donné à une main jouée que la range du solveur ne contient pas
MAX_RAISES = 2
DEFAULT_ITERATIONS, DEFAULT_TARGET = 120, 1.5

# Ligne préflop -> (type de pot, range du bouton, range de la BB) : (nœud, action) de la solution.
PREFLOP_LINES = {
    (("sb", RAISE), ("bb", CALL)): ("SRP", ("sb_open", "raise"), ("bb_vs_open", "call")),
    (("sb", RAISE), ("bb", RAISE), ("sb", CALL)): ("pot 3bet", ("sb_vs_3bet", "call"), ("bb_vs_open", "raise")),
    (("sb", RAISE), ("bb", RAISE), ("sb", RAISE), ("bb", CALL)):
        ("pot 4bet", ("sb_vs_3bet", "raise"), ("bb_vs_4bet", "call")),
}

CARGO_TOML = """[package]
name = "analyzer-solve"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "analyzer-solve"
path = "main.rs"

[dependencies]
solver = {{ path = "{solver}" }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
rayon = "1.10"
flate2 = "1"

[features]
gpu = ["solver/gpu"]

[profile.release]
opt-level = 3
lto = "thin"
codegen-units = 1

[workspace]
"""


class SolverError(RuntimeError):
    pass


class Unsupported(ValueError):
    """La main ne se prête pas à une résolution postflop (message pour l'utilisateur)."""


# --- Installation ------------------------------------------------------------------------

def home() -> Path:
    return Path(os.environ.get("ANALYZER_HOME") or Path.home() / ".analyzer")


def build_dir() -> Path:
    return home() / "solveur"


def binary_path() -> Path:
    env = os.environ.get("ANALYZER_SOLVER")
    return Path(env) if env else build_dir() / "target" / "release" / EXE


def _native_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(NATIVE_DIR.glob("*.rs")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:16]


def _valid_source(path: Path) -> bool:
    return (path / "crates" / "solver" / "Cargo.toml").is_file()


def find_source(explicit: Optional[str] = None) -> Optional[Path]:
    recorded = build_dir() / "source.txt"
    candidates = [explicit, os.environ.get("GTOPEN_DIR"),
                  recorded.read_text(encoding="utf-8").strip() if recorded.is_file() else None,
                  home() / "GTOpen"]
    for candidate in candidates:
        if candidate and _valid_source(Path(candidate)):
            return Path(candidate).resolve()
    return None


def status() -> dict:
    binary = binary_path()
    ready = binary.is_file()
    built = build_dir() / "native.sha"
    outdated = ready and built.is_file() and built.read_text().strip() != _native_hash()
    source = find_source()
    if not ready:
        message = (f"Solveur non installé. Lance « {INSTALL_COMMAND} » dans un terminal "
                   "(Rust nécessaire, quelques minutes).")
    elif outdated:
        message = f"Le pont vers GTOpen a changé : relance « {INSTALL_COMMAND} » pour le recompiler."
    else:
        message = "Solveur GTOpen prêt."
    return {"ready": ready, "outdated": outdated, "binary": str(binary),
            "source": str(source) if source else None, "message": message, "install": INSTALL_COMMAND}


def _run(cmd: list[str], log: Callable[[str], None], **kwargs) -> None:
    log("$ " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, **kwargs)
    except FileNotFoundError as exc:
        raise SolverError(f"Commande introuvable : {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SolverError(f"Échec de : {' '.join(cmd)} (code {exc.returncode})") from exc


def external_files(src: Path) -> list[str]:
    """Fichiers hors du moteur que sa compilation lit (include_str!), relatifs à la racine de GTOpen."""
    root = src.resolve()
    crate = root / "crates" / "solver"
    found = set()
    for rs in (crate / "src").rglob("*.rs"):
        for rel in INCLUDE_RE.findall(rs.read_text(encoding="utf-8", errors="replace")):
            target = Path(os.path.normpath(rs.parent / rel))
            if target.is_relative_to(root) and not target.is_relative_to(crate):
                found.add(target.relative_to(root).as_posix())
    return sorted(found)


def missing_files(src: Path) -> list[str]:
    return [f for f in external_files(src) if not (src / f).is_file()]


def _git(src: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True)
    except FileNotFoundError:  # git absent : la copie est utilisée telle quelle
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _managed(src: Path) -> bool:
    """Copie partielle faite par Analyzer (ou toute copie partielle git) : on peut la compléter."""
    return (src / ".git").exists() and _git(src, "config", "--get", "core.sparseCheckout") == "true"


def prepare(src: Path, log: Callable[[str], None] = print) -> None:
    """Met une copie partielle sur la version testée et y ajoute les fichiers que la compilation lit."""
    if _managed(src):
        if _git(src, "rev-parse", "HEAD") != GTOPEN_COMMIT:
            _run(["git", "-C", str(src), "fetch", "--depth", "1", "origin", GTOPEN_COMMIT], log)
            _run(["git", "-C", str(src), "checkout", "--quiet", GTOPEN_COMMIT], log)
        for _ in range(3):  # un fichier ajouté peut en inclure d'autres
            if not missing_files(src):
                break
            patterns = [*GTOPEN_PATHS, *("/" + f for f in external_files(src))]
            _run(["git", "-C", str(src), "sparse-checkout", "set", "--no-cone", *patterns], log)
    missing = missing_files(src)
    if missing:
        raise SolverError(f"Il manque dans {src} des fichiers de GTOpen nécessaires à la compilation : "
                          + ", ".join(missing) + ". Copie-les depuis le dépôt de GTOpen en gardant le même chemin.")


def clone(dest: Path, log: Callable[[str], None] = print) -> Path:
    """Copie partielle du dépôt GTOpen : le moteur et les quelques fichiers qu'il lit, sans la recherche."""
    if shutil.which("git") is None:
        raise SolverError("git est introuvable : installe-le, ou passe --source vers une copie de GTOpen.")
    if dest.exists() and any(dest.iterdir()):
        raise SolverError(f"{dest} existe déjà mais ne contient pas GTOpen : vide-le ou passe --source.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", GTOPEN_URL, str(dest)], log)
    _run(["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *GTOPEN_PATHS], log)
    prepare(dest, log)
    return dest


def gpu_enabled() -> bool:
    flag = build_dir() / "gpu.txt"
    return flag.is_file() and flag.read_text(encoding="utf-8").strip() == "1"


def install(source: Optional[str] = None, log: Callable[[str], None] = print, gpu: bool = False) -> Path:
    """Récupère GTOpen si besoin, puis compile analyzer-solve contre son moteur.

    gpu=True : compile aussi le moteur CUDA de GTOpen (carte NVIDIA ; voir son README).
    """
    if shutil.which("cargo") is None:
        raise SolverError("Rust (cargo) est introuvable : installe-le depuis https://rustup.rs puis relance.")
    src = find_source(source)
    if src is None:
        if source:
            raise SolverError(f"{source} ne contient pas GTOpen (crates/solver/Cargo.toml introuvable).")
        log(f"Récupération de GTOpen ({GTOPEN_URL}) dans {home() / 'GTOpen'}…")
        src = clone(home() / "GTOpen", log)
    else:
        prepare(src, log)
    root = build_dir()
    root.mkdir(parents=True, exist_ok=True)
    for path in NATIVE_DIR.glob("*.rs"):
        shutil.copyfile(path, root / path.name)
    (root / "Cargo.toml").write_text(CARGO_TOML.format(solver=(src / "crates" / "solver").as_posix()),
                                     encoding="utf-8")
    (root / "source.txt").write_text(str(src), encoding="utf-8")
    log(f"Compilation de analyzer-solve contre {src} (quelques minutes la première fois)…")
    # Dossier de compilation explicite : un CARGO_TARGET_DIR global placerait le programme ailleurs.
    _run(["cargo", "build", "--release", "--target-dir", str(root / "target")] + (["--features", "gpu"] if gpu else []),
         log, cwd=root)
    (root / "native.sha").write_text(_native_hash(), encoding="utf-8")
    (root / "gpu.txt").write_text("1" if gpu else "0", encoding="utf-8")
    return build_dir() / "target" / "release" / EXE


# --- Spot d'une main -----------------------------------------------------------------------

def range_weights(solution: Solution, node_key: str, action: str) -> dict[str, float]:
    """Part de chaque main (AKs, 72o…) qui prend cette action à ce nœud de la solution."""
    node = solution.nodes[node_key]
    out = {}
    for i in range(13):
        for j in range(13):
            hand = hand_at(i, j)
            weight = solution.weight(node_key, hand) * (node.strategy(hand) or {}).get(action, 0.0)
            if weight >= 0.001:
                out[hand] = round(weight, 3)
    return out


def range_text(weights: dict[str, float]) -> str:
    return ",".join(h if w >= 0.999 else f"{h}:{w:g}" for h, w in weights.items())


def _merge_sizes(defaults: list, played: list) -> list:
    sizes = list(defaults)
    for p in played:
        if p == "a":
            if "a" not in sizes:
                sizes.append("a")
            continue
        near = [k for k, d in enumerate(sizes) if d != "a" and abs(d - p) <= SIZE_MERGE]
        if near:
            sizes[near[0]] = p  # la taille jouée remplace la taille par défaut proche
        elif all(abs(p - d) > 1 for d in sizes if d != "a"):
            sizes.append(p)
    return sizes


def default_sizes(oop: str, ip: str, oop_initiative: bool) -> dict[str, list[dict[str, list]]]:
    """Tailles par défaut. Sans l'initiative préflop (SRP, pot 4bet), la BB ne mène pas au flop :
    c'est un donk, écarté comme aux streets suivantes (seuls les donks joués entrent dans l'arbre)."""
    return {p: [{"bet": [] if s == 0 and p == oop and not oop_initiative else [DEFAULT_BETS[s]],
                 "raise": list(DEFAULT_RAISES[s]), "donk": []} for s in range(3)] for p in (oop, ip)}


def _size_order(size) -> float:
    return float("inf") if size == "a" else size


def _size_json(sizes: list) -> list:
    return ["AllIn" if s == "a" else {"PotPct": s} for s in sizes]


class SpotTree:
    """Arbre et requête d'un spot : attend board, ranges, sizes, oop, ip, pot_bb, stack_bb et line."""

    def tree(self) -> dict:
        def streets(player: str) -> list[dict]:
            return [{k: _size_json(v) for k, v in s.items()} for s in self.sizes[player]]
        return {"starting_pot": self.pot_bb, "effective_stack": self.stack_bb, "rake_pct": 0.0, "rake_cap": 0.0,
                "oop": streets(self.oop), "ip": streets(self.ip), "allin_threshold": 0.85,
                "add_allin": False, "max_raises": MAX_RAISES}

    def request(self, iterations: int = DEFAULT_ITERATIONS, target: float = DEFAULT_TARGET,
                threads: int = 0) -> dict:
        out = {
            "spot": {"board": "".join(self.board), "range_oop": range_text(self.ranges[self.oop]),
                     "range_ip": range_text(self.ranges[self.ip]), "tree": self.tree()},
            "line": self.line,
            "max_iterations": iterations, "target_exploit_pct": target, "threads": threads,
            "gpu": gpu_enabled(),
        }
        # Tailles par situation de la ligne (c-bet, 2e barrel, probe…) : % du pot, "geo" ou "a" (tapis),
        # voir native/arbre.rs. Sans plan,
        # la requête (et donc la clé des études déjà enregistrées) ne change pas.
        plan = getattr(self, "plan", None)
        if plan:
            out["plan"] = {key: [s if isinstance(s, str) else float(s) for s in sizes]
                           for key, sizes in sorted(plan.items())}
        return out

    def menu_text(self) -> str:
        def fmt(values: list) -> str:
            return " / ".join("tapis" if v == "a" else f"{_num(v)} %" for v in values)
        parts = []
        for s, street in enumerate(("flop", "turn", "river")):
            bets = sorted({v for p in (self.oop, self.ip) for v in self.sizes[p][s]["bet"]}, key=_size_order)
            raises = sorted({v for p in (self.oop, self.ip) for v in self.sizes[p][s]["raise"]}, key=_size_order)
            text = f"{street} : mise {fmt(bets)}" + (f", relance {fmt(raises)}" if raises else "")
            donks = self.sizes[self.oop][s]["donk"]
            if donks:
                text += f", donk {fmt(donks)}"
            parts.append(text)
        return " · ".join(parts)


@dataclass
class PostflopSpot(SpotTree):
    hand: Hand
    hero: str
    villain: str
    pot_type: str
    oop: str
    ip: str
    pot_bb: float
    stack_bb: float
    ranges: dict[str, dict[str, float]]
    line: list[dict]
    action_index: list[int]  # indice dans hand.actions de chaque étape de la ligne (-1 : carte)
    sizes: dict[str, list[dict[str, list]]]  # joueur -> street -> {"bet", "raise", "donk"}
    added: list[str] = field(default_factory=list)  # joueurs dont la main a été ajoutée à la range
    context: str = ""  # la ligne préflop et son format, pour tes ranges ajustées (custom_ranges.context)
    adjusted: Optional[str] = None  # tes ranges ajustées en jeu : « coup », « ligne », ou None (référence)
    reference: dict[str, dict[str, float]] = field(default_factory=dict, repr=False)  # ranges de la référence

    @property
    def board(self) -> list[str]:
        return self.hand.board[:3]

    def combos(self) -> dict[str, int]:
        """Mains connues -> joueur (0 = hors de position, 1 = en position)."""
        return {"".join(self.hand.hole_cards[p]): 0 if p == self.oop else 1 for p in (self.hero, self.villain)
                if len(self.hand.hole_cards.get(p, [])) == 2}

    @property
    def ident(self) -> str:
        return self.hand.hand_id

    def interpret(self, raw: dict) -> dict:
        return interpret(self, raw)

    def write_meta(self, request: dict, raw: dict, session: "Optional[Session]" = None) -> None:
        write_study_meta(self, request, raw)


POSTFLOP_ORDER = ("SB", "BB", "UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN")  # ordre de parole après le flop


def build_spot(hand: Hand, hero: str, solution: Optional[Solution] = None, custom: bool = True) -> PostflopSpot:
    """Le spot postflop d'une main : heads-up (ranges de la solution HU), ou table à plusieurs quand il ne reste que
    deux joueurs au flop (ranges de ta solution du format, voir ring_ranges). Tes ranges ajustées pour ce coup ou
    pour sa ligne (custom_ranges) remplacent celles de la référence, sauf avec custom=False."""
    from . import custom_ranges
    if len(hand.seats) == 2 and (not hand.button or not hand.big_blind or hand.opponent_of(hero) is None):
        raise Unsupported("Seules les mains heads-up se résolvent.")
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY]
    if any(a.all_in for a in pre):
        raise Unsupported("Tapis préflop : il n'y a plus de décision après le flop.")
    if len(hand.board) < 3 or not any(a.street == "flop" for a in hand.actions):
        raise Unsupported("La main s'arrête avant le flop : rien à résoudre après le flop.")
    if len(hand.seats) == 2:
        oop, ip, pot_type, ranges = _heads_up_ranges(hand, pre, solution or load_solution())
    else:
        oop, ip, pot_type, ranges = _ring_ranges(hand, hero, pre)
    villain = ip if hero == oop else oop
    context = custom_ranges.context(hand.table_format, [(hand.position(a.player), a.kind) for a in pre
                                                        if a.kind != FOLD])
    reference = {p: dict(r) for p, r in ranges.items()}
    adjusted = None
    if custom:
        scope, mine = custom_ranges.lookup(hand.hand_id, context)
        for p in (oop, ip):
            if hand.position(p) in mine:
                ranges[p], adjusted = dict(mine[hand.position(p)]), scope

    added = []
    for player in (hero, villain):
        cards = hand.hole_cards.get(player, [])
        if len(cards) == 2:
            cls = combo_notation(cards)
            if ranges[player].get(cls, 0.0) < MIN_WEIGHT:
                ranges[player][cls] = MIN_WEIGHT
                added.append(player)

    bb = hand.bb
    put = {p: sum(a.amount for a in hand.actions if a.player == p and a.street == "preflop") for p in hand.seats}
    pot = sum(put.values())  # l'argent mort des joueurs qui ont foldé compris
    stack = min(hand.seats[p].stack - put[p] for p in (oop, ip))
    if stack <= 0:
        raise Unsupported("Tapis préflop : il n'y a plus de décision après le flop.")

    played = {p: [{"bet": [], "raise": [], "donk": []} for _ in range(3)] for p in (oop, ip)}
    line: list[dict] = []
    index: list[int] = []
    street, aggressor, prev_aggressor = "flop", None, None
    for i, a in enumerate(hand.actions):
        if a.street == "preflop" or a.kind not in VOLUNTARY:
            continue
        if a.street != street:
            prev_aggressor, aggressor = aggressor, None
            street = a.street
            line.append({"card": hand.board[2 + STREET_INDEX[street]]})
            index.append(-1)
        s = STREET_INDEX[street]
        step: dict = {"action": a.kind}
        if a.kind in (BET, RAISE, CALL):
            step["to"] = round(a.to / bb, 4)
        if a.all_in:
            step["allin"] = True
        if a.kind == BET:
            size = "a" if a.all_in else round(100 * a.to / a.pot_before, 1)
            kind = "donk" if a.player == oop and s > 0 and prev_aggressor == ip else "bet"
            played[a.player][s][kind].append(size)
        elif a.kind == RAISE:
            opponent_to = (a.to - a.amount) + a.facing
            size = "a" if a.all_in else round(100 * (a.to - opponent_to) / (a.pot_before + a.facing), 1)
            played[a.player][s]["raise"].append(size)
        if a.kind in (BET, RAISE):
            aggressor = a.player
        line.append(step)
        index.append(i)

    initiative = [a.player for a in pre if a.kind == RAISE][-1] == oop
    defaults = default_sizes(oop, ip, initiative)
    sizes = {p: [{k: _merge_sizes(defaults[p][s][k], played[p][s][k]) for k in ("bet", "raise", "donk")}
                 for s in range(3)] for p in (oop, ip)}
    return PostflopSpot(hand, hero, villain, pot_type, oop, ip, round(pot / bb, 4), round(stack / bb, 4),
                        ranges, line, index, sizes, added, context, adjusted, reference)



def _heads_up_ranges(hand: Hand, pre: list, solution: Solution) -> tuple[str, str, str, dict]:
    """Heads-up : la ligne préflop dans la solution HU ; la BB est hors de position."""
    sb, bb_player = hand.button, hand.big_blind
    pattern = tuple(("sb" if a.player == sb else "bb", a.kind) for a in pre)
    if pattern not in PREFLOP_LINES:
        if pattern[:1] == (("sb", CALL),):
            raise Unsupported("Pot limpé : la solution préflop ne donne pas de range pour ce cas.")
        raise Unsupported("Ligne préflop hors de la solution (seuls SRP, pots 3bet et 4bet sont couverts).")
    pot_type, sb_source, bb_source = PREFLOP_LINES[pattern]
    ranges = {sb: range_weights(solution, *sb_source), bb_player: range_weights(solution, *bb_source)}
    return bb_player, sb, pot_type, ranges


def flop_pair(hand: Hand) -> Optional[tuple[str, str]]:
    """(hors de position, en position) s'il ne reste que deux joueurs au flop, sinon None."""
    if len(hand.seats) == 2:
        return (hand.big_blind, hand.button) if hand.button and hand.big_blind else None
    folded = {a.player for a in hand.actions if a.street == "preflop" and a.kind == FOLD}
    players = [p for p in hand.seats if p not in folded]
    if len(players) != 2 or len(hand.board) < 3 or any(hand.position(p) not in POSTFLOP_ORDER for p in players):
        return None
    oop, ip = sorted(players, key=lambda p: POSTFLOP_ORDER.index(hand.position(p)))
    return oop, ip


def _ring_ranges(hand: Hand, hero: str, pre: list) -> tuple[str, str, str, dict]:
    """Table à plusieurs : les deux joueurs qui voient le flop, leurs ranges d'après ta solution du format."""
    from . import ring_ranges
    folded = {a.player for a in pre if a.kind == FOLD}
    players = [p for p in hand.seats if p not in folded]
    if hero not in players:
        raise Unsupported("Tu as foldé avant le flop : rien à résoudre après le flop.")
    if len(players) != 2:
        raise Unsupported(f"Pot à {len(players)} joueurs au flop : le solveur ne résout que les pots à deux.")
    if any(a.player not in players and a.kind != FOLD for a in pre):
        raise Unsupported("Un troisième joueur a mis de l'argent avant de se coucher (call puis fold, squeeze…) : "
                          "cette ligne n'est pas encore couverte.")
    positions = {p: hand.position(p) for p in players}
    if any(pos not in POSTFLOP_ORDER for pos in positions.values()):
        raise Unsupported("Positions inconnues : le bouton manque dans l'historique.")
    steps = [(positions[a.player], a.kind) for a in pre if a.kind != FOLD]
    found = ring_ranges.lookup(hand.table_format, steps)
    if found is None:
        raise Unsupported(f"Pas de range préflop pour « {ring_ranges.describe(steps)} » en {hand.table_format} : "
                          f"ajoute ta solution ({ring_ranges.folder() / (hand.table_format + '.json')}).")
    pot_type, by_position = found
    if any(pos not in by_position for pos in positions.values()):
        raise Unsupported(f"Ta solution ne donne pas les deux ranges de « {ring_ranges.describe(steps)} ».")
    oop, ip = sorted(players, key=lambda p: POSTFLOP_ORDER.index(positions[p]))
    return oop, ip, pot_type, {p: dict(by_position[positions[p]]) for p in players}

PREFLOP_RAISES = ("Open", "3bet", "4bet", "5bet")


def preflop_steps(hand: Hand, hero: Optional[str] = None) -> list[dict]:
    """L'action préflop jouée, pour le déroulé de l'explorateur : position et joueur de l'arbre (0 = hors de
    position, 1 = en position ; None pour un joueur qui n'est plus là au flop), action, tapis restant avant d'agir
    (effectif en heads-up, le sien à une table à plusieurs) et pot (en bb), ligne de la solution préflop HU."""
    bb = hand.bb
    heads_up = len(hand.seats) == 2
    effective = min(seat.stack for seat in hand.seats.values())
    pair = flop_pair(hand)
    put = dict.fromkeys(hand.seats, 0.0)
    steps, line, raises = [], [], 0
    for a in hand.actions:
        if a.street != "preflop":
            continue
        if a.kind not in VOLUNTARY:  # blindes
            put[a.player] += a.amount
            continue
        if a.kind == RAISE:
            name = "Tapis" if a.all_in else PREFLOP_RAISES[min(raises, len(PREFLOP_RAISES) - 1)]
            name, key = f"{name} {_num(a.to / bb, 2)}", "allin" if a.all_in else "raise"
            raises += 1
        else:
            name = {CALL: "Call" if raises else "Limp", FOLD: "Fold", CHECK: "Check"}[a.kind]
            key = {CALL: "call", FOLD: "fold"}.get(a.kind)
        stack = (effective if heads_up else hand.seats[a.player].stack) - put[a.player]
        steps.append({"player": pair.index(a.player) if pair and a.player in pair else None,
                      "position": hand.position(a.player), "hero": a.player == hero, "name": name,
                      "stack": round(stack / bb, 2), "pot": round(sum(put.values()) / bb, 2),
                      "line": list(line) if heads_up else None})
        if a.kind in (RAISE, CALL):
            put[a.player] = a.to
        if key:
            line.append(key)
    return steps


# --- Exécution et cache --------------------------------------------------------------------

RUNTIME_FIELDS = ("threads", "gpu", "max_nodes")  # réglages d'exécution : ne changent pas la solution


def _identity(request: dict) -> str:
    return json.dumps({k: v for k, v in request.items() if k not in RUNTIME_FIELDS}, sort_keys=True, ensure_ascii=False)


def cache_key(request: dict) -> str:
    """Clé du résultat de la ligne jouée (dépend aussi du programme, dont le format de sortie peut changer)."""
    return hashlib.sha256((_identity(request) + _native_hash()).encode()).hexdigest()[:20]


def study_key(request: dict) -> str:
    """Clé d'une étude : le spot seul. Le fichier d'étude a son propre format versionné (en-tête)."""
    return hashlib.sha256(_identity(request).encode()).hexdigest()[:20]


def cache_path(request: dict) -> Path:
    return home() / "resolutions" / f"{cache_key(request)}.json"


def cached(request: dict) -> Optional[dict]:
    path = cache_path(request)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def _save_cache(request: dict, result: dict) -> None:
    path = cache_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")


def _progress_reader(stream, on_progress: Optional[Callable[[dict], None]], errors: list) -> None:
    """Lit la progression (stderr) d'analyzer-solve ; les erreurs sont gardées dans `errors`."""
    with stream:
        for raw in stream:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if "error" in data:
                errors.append(data["error"])
            elif on_progress:
                on_progress(data)


# Sortie du solveur en UTF-8, quel que soit l'encodage par défaut du système (Windows).
PIPE_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def solve(request: dict, on_progress: Optional[Callable[[dict], None]] = None,
          on_start: Optional[Callable[[subprocess.Popen], None]] = None, use_cache: bool = True,
          save_study: bool = False) -> dict:
    """Lance analyzer-solve ; on_progress reçoit chaque mesure (itération, exploitabilité).

    save_study=True garde aussi l'arbre résolu comme étude (voir study_path)."""
    if use_cache:
        hit = cached(request)
        if hit is not None:
            return hit
    exe = binary_path()
    if not exe.is_file():
        raise SolverError(status()["message"])
    with tempfile.TemporaryDirectory(prefix="analyzer-solve-") as tmp:
        req_path, out_path = Path(tmp) / "requete.json", Path(tmp) / "resultat.json"
        req_path.write_text(json.dumps(request), encoding="utf-8")
        errors: list = []
        with out_path.open("w", encoding="utf-8") as out:
            # stdout vers un fichier : le résultat peut dépasser la taille d'un tube.
            cmd = [str(exe), str(req_path)]
            if save_study:
                study_path(request).parent.mkdir(parents=True, exist_ok=True)
                cmd += ["--save", str(study_path(request))]
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.PIPE, **PIPE_TEXT)
            if on_start:
                on_start(proc)
            _progress_reader(proc.stderr, on_progress, errors)
            proc.wait()
        if proc.returncode != 0:
            raise SolverError(errors[-1] if errors else f"analyzer-solve s'est arrêté (code {proc.returncode}).")
        result = json.loads(out_path.read_text(encoding="utf-8"))
    _save_cache(request, result)
    return result


# --- Études : arbres résolus gardés sur disque ------------------------------------------------

def studies_dir() -> Path:
    return home() / "etudes"


def study_path(request: dict) -> Path:
    return studies_dir() / f"{study_key(request)}.etude"


def write_study_meta(spot: PostflopSpot, request: dict, raw: dict) -> None:
    """Fiche de l'étude (pour la bibliothèque), à côté du fichier de l'arbre."""
    hand = spot.hand
    meta = {
        "kind": "hand", "key": study_key(request), "hand": hand.hand_id, "date": hand.date.strftime("%d/%m/%Y %H:%M"),
        "hero": spot.hero, "villain": spot.villain, "hero_cards": hand.hole_cards.get(spot.hero, []),
        "board": hand.board, "pot_type": spot.pot_type, "table_format": hand.table_format,
        "hero_position": ("BB" if spot.oop == spot.hero else "BTN") if len(hand.seats) == 2 else hand.position(spot.hero),
        "pot": spot.pot_bb, "stack": spot.stack_bb, "net": round(hand.net(spot.hero) / hand.bb, 2),
        "iterations": raw.get("iterations"), "exploit_pct": raw.get("exploit_pct"), "seconds": raw.get("seconds"),
        "menu": spot.menu_text(), "created": time.strftime("%d/%m/%Y %H:%M"), "adjusted": spot.adjusted,
    }
    path = study_path(request).with_suffix(".json")
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def list_studies() -> list[dict]:
    """Études enregistrées, de la plus récente à la plus ancienne, avec la taille de leur fichier."""
    out = []
    folder = studies_dir()
    if not folder.is_dir():
        return out
    for meta_path in folder.glob("*.json"):
        tree = meta_path.with_suffix(".etude")
        if not tree.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        meta["size"] = tree.stat().st_size
        meta["mtime"] = tree.stat().st_mtime
        out.append(meta)
    return sorted(out, key=lambda m: -m["mtime"])


def delete_study(key: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{20}", key):
        return False
    removed = False
    for suffix in (".etude", ".json"):
        path = studies_dir() / f"{key}{suffix}"
        if path.is_file():
            path.unlink()
            removed = True
    return removed


class Session:
    """Résolution gardée en mémoire (analyzer-solve --serve) pour naviguer dans tout l'arbre.

    Sans étude enregistrée, résout puis enregistre l'étude ; sinon la recharge (quelques secondes).
    save=False : résolution de travail (choix des tailles), ni étude ni cache.
    """

    def __init__(self, request: dict, save: bool = True):
        self.request = request
        self.save = save
        self.study = study_path(request)
        self.loading = save and self.study.is_file()
        self.result: Optional[dict] = None
        self.proc: Optional[subprocess.Popen] = None
        self.last_used = time.time()
        self._lock = threading.Lock()
        self._errors: list = []
        self._tmp = tempfile.TemporaryDirectory(prefix="analyzer-session-")
        self.profile: Optional[dict] = None  # profil d'adversaire verrouillé dans le pont (Session.ask)

    def start(self, on_progress: Optional[Callable[[dict], None]] = None,
              on_start: Optional[Callable[[subprocess.Popen], None]] = None) -> dict:
        """Résout ou recharge l'étude (bloquant) ; renvoie le résultat de la ligne jouée, mis en cache."""
        exe = binary_path()
        if not exe.is_file():
            raise SolverError(status()["message"])
        if self.loading:
            cmd = [str(exe), "--load", str(self.study), "--serve"]
        else:
            req_path = Path(self._tmp.name) / "requete.json"
            req_path.write_text(json.dumps(self.request), encoding="utf-8")
            cmd = [str(exe), str(req_path), "--serve"]
            if self.save:
                self.study.parent.mkdir(parents=True, exist_ok=True)
                cmd[2:2] = ["--save", str(self.study)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     **PIPE_TEXT)
        if on_start:
            on_start(self.proc)
        reader = threading.Thread(target=_progress_reader, args=(self.proc.stderr, on_progress, self._errors),
                                  daemon=True)
        reader.start()
        line = self.proc.stdout.readline()
        if not line:
            self.proc.wait()
            reader.join(timeout=2)
            self.close()
            raise SolverError(self._errors[-1] if self._errors
                              else f"analyzer-solve s'est arrêté (code {self.proc.returncode}).")
        self.result = json.loads(line)
        if self.save:
            _save_cache(self.request, self.result)
        self.last_used = time.time()
        return self.result

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.result is not None

    def ask(self, query: dict) -> dict:
        """Requête au pont : {"path": [...]} et, au besoin, "profile" (profil d'adversaire verrouillé)
        et "exploit" (joueur dont on veut la meilleure réponse). Renvoie la réponse complète."""
        with self._lock:
            if not self.alive:
                raise SolverError("La session du solveur est fermée : relance la résolution.")
            self.last_used = time.time()
            try:
                self.proc.stdin.write(json.dumps(query) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except OSError as exc:
                raise SolverError("La session du solveur s'est arrêtée.") from exc
            reply = json.loads(line) if line else None
            # le pont garde le profil de la requête (sans profil : l'arbre du solveur) ; en cas d'erreur,
            # on le considère retiré, la requête suivante le remettra au besoin
            self.profile = query.get("profile") if reply and "error" not in reply else None
        if reply is None:
            raise SolverError("La session du solveur s'est arrêtée.")
        if "error" in reply:
            raise SolverError(reply["error"])
        return reply

    def node(self, path: list) -> dict:
        """Nœud de l'arbre au bout du chemin (étapes {"type": "action", "index": i} / {"type": "card", "card": c}),
        tel que le solveur le joue (un profil d'adversaire en place est retiré)."""
        return self.ask({"path": path})["node"]

    def exploit(self, path: list, profile: dict, player: int) -> dict:
        """Le nœud avec l'adversaire verrouillé sur son profil, et la meilleure réponse de `player` :
        {"node", "profile" (fréquences du solveur et du profil par situation), "exploit"}."""
        return self.ask({"path": path, "profile": profile, "exploit": player})

    def close(self) -> None:
        proc = self.proc
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
                    proc.wait()
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
        self._tmp.cleanup()


# --- Lecture du résultat -------------------------------------------------------------------

def _num(x: float, digits: int = 1) -> str:
    text = f"{x:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{x:.0f}"
    return text.replace(".", ",")


def action_label(action: dict, pot: float, raise_base: float = 0.0, call_to: float = 0.0) -> str:
    """Libellé d'une action de l'arbre. Une relance est exprimée en % du pot après le call
    (raise_base) : relancer à 4,5 sur une mise de 1,7 dans un pot de 5 = 2,8 / 8,4 = 33 %."""
    kind, amount = action["kind"], action["amount"]
    if kind in (BET, RAISE) and action.get("allin"):
        return f"tapis ({_num(amount)} bb)"
    if kind == BET:
        return f"mise {round(100 * amount / pot)} % ({_num(amount)} bb)"
    if kind == RAISE:
        share = f" ({round(100 * (amount - call_to) / raise_base)} %)" if raise_base else ""
        return f"relance à {_num(amount)} bb{share}"
    return {CHECK: "check", CALL: "call", FOLD: "fold"}.get(kind, kind)


def action_labels(node: dict) -> list[str]:
    actions, player, put = node["actions"], node.get("player"), node.get("put")
    call = next((a for a in actions if a["kind"] == CALL), None)
    base = 2 * put[1 - player] if call and put and player is not None else 0.0
    return [action_label(a, node["pot"], base, call["amount"] if call else 0.0) for a in actions]


def played_label(hand: Hand, i: int) -> str:
    a = hand.actions[i]
    bb = hand.bb
    if a.kind == BET:
        text = f"mise {_num(a.to / bb)} bb ({round(100 * a.to / a.pot_before)} %)"
    elif a.kind == RAISE:
        text = f"relance à {_num(a.to / bb)} bb"
    else:
        text = {CHECK: "check", CALL: "call", FOLD: "fold"}.get(a.kind, a.kind)
    return text + (" — tapis" if a.all_in else "")


def verdict(frequency: Optional[float]) -> Optional[str]:
    if frequency is None:
        return None
    if frequency >= MAIN_THRESHOLD:
        return "principale"
    return "secondaire" if frequency >= MIXED_THRESHOLD else "écart"


GRID_RANKS = "AKQJT98765432"


def class_of(combo: str) -> str:
    """'AhKd' -> 'AKo' ; 'QdQs' -> 'QQ'."""
    r1, s1, r2, s2 = combo[0], combo[1], combo[2], combo[3]
    if r1 == r2:
        return r1 + r2
    hi, lo = (r1, r2) if GRID_RANKS.index(r1) < GRID_RANKS.index(r2) else (r2, r1)
    return hi + lo + ("s" if s1 == s2 else "o")


def live_combos(board: list[str]) -> dict[str, int]:
    """Nombre de combos de chaque classe compatibles avec le board."""
    deck = [r + s for r in GRID_RANKS for s in "cdhs" if r + s not in board]
    counts: dict[str, int] = {}
    for i, a in enumerate(deck):
        for b in deck[i + 1:]:
            cls = class_of(a + b)
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def node_summary(node: dict) -> tuple[list[float], dict[str, list[float]]]:
    """Stratégie de toute la range du joueur qui agit, et par classe : [présence, fréquences…]."""
    actor = node.get("player")
    if actor is None:
        return [], {}
    na = len(node["actions"])
    counts = live_combos(node["board"])
    total, range_ = 0.0, [0.0] * na
    classes: dict[str, list[float]] = {}
    for row in node["hands"][actor]:
        combo, reach, strategy = row[0], row[1], row[4:4 + na]
        acc = classes.setdefault(class_of(combo), [0.0] * (na + 1))
        acc[0] += reach
        total += reach
        for a in range(na):
            acc[a + 1] += reach * strategy[a]
            range_[a] += reach * strategy[a]
    out = {}
    for cls, acc in classes.items():
        if acc[0] > 0:
            out[cls] = [round(acc[0] / counts.get(cls, 1), 3), *(round(x / acc[0], 3) for x in acc[1:])]
    return [round(x / total, 4) if total else 0.0 for x in range_], out


# --- Mains que le solveur ne joue (presque) jamais à un nœud ------------------------------------------
#
# La stratégie d'une main est une moyenne sur les itérations, pondérée par sa présence au nœud. Une main
# qui n'y arrive presque jamais (hors de la range, ou une ligne que le solveur ne prend pas avec elle) n'y
# apprend rien : ses fréquences sont un reste des premières itérations, arrondi sur quelques unités du
# stockage compressé. Son EV par action, elle, est calculée face à la stratégie finale de l'adversaire :
# pour ces mains, on montre la meilleure action selon l'EV.
RARE_PATH = 0.01    # la main suit cette ligne moins d'une fois sur cent dans la stratégie du solveur
RARE_MASS = 1e-3    # ou elle pèse mille fois moins que la main la plus présente du nœud
RARE_RANGE = 0.005  # part de la range arrivée au nœud sous laquelle la suite n'est pas optimisée
EV_TIE = 0.01       # bb : deux actions dont l'EV diffère de moins se valent


def weights_of(spot) -> list[dict[str, float]]:
    """Poids de départ des mains, par joueur de l'arbre (0 = hors de position)."""
    return [spot.ranges[spot.oop], spot.ranges[spot.ip]]


def best_response(evs: list[float]) -> list[float]:
    """La meilleure action selon l'EV (partagée entre les actions qui se valent)."""
    top = max(evs)
    best = [e >= top - EV_TIE for e in evs]
    return [round(1 / sum(best), 3) if b else 0.0 for b in best]


def settle(node: dict, weights: Optional[list[dict[str, float]]] = None) -> dict:
    """Remplace la stratégie des mains quasi absentes du nœud par leur meilleure action selon l'EV.

    node["settled"] : ces mains (du joueur qui agit). Avec les poids de départ, node["presence"] : la part
    de la range de chaque joueur qui arrive au nœud ; node["rare"] : les joueurs dont la range n'y arrive
    presque jamais (la suite du coup n'est alors pas optimisée par le solveur)."""
    if weights:
        counts = live_combos(node["board"])
        presence = []
        for p in (0, 1):
            start = sum(w * counts.get(cls, 0) for cls, w in weights[p].items())
            presence.append(round(sum(r[1] for r in node["hands"][p]) / start, 5) if start else None)
        node["presence"] = presence
        node["rare"] = [p for p in (0, 1) if presence[p] is not None and presence[p] < RARE_RANGE]
    actor = node.get("player")
    node["settled"] = []
    if actor is None or not node["hands"][actor]:
        return node
    na = len(node["actions"])
    rows = node["hands"][actor]
    top = max(r[1] for r in rows)
    for row in rows:
        evs = row[4 + na:4 + 2 * na]
        if len(evs) < na or any(e is None for e in evs):
            continue
        weight = weights[actor].get(class_of(row[0])) if weights else None
        if row[1] < RARE_MASS * top or (weight and row[1] / weight < RARE_PATH):
            row[4:4 + na] = best_response(evs)
            row[3] = round(max(evs), 3)  # l'EV de la main avec cette action
            node["settled"].append(row[0])
    return node


def combo_row(node: dict, player: int, cards: list[str]) -> Optional[list]:
    wanted = set(cards)
    for row in node["hands"][player]:
        if {row[0][:2], row[0][2:]} == wanted:
            return row
    return None


def interpret(spot: PostflopSpot, raw: dict) -> dict:
    """Résultat lisible : chaque décision de la main face au solveur (données pour l'application)."""
    hand = spot.hand
    who = {spot.hero: "H", spot.villain: "V"}
    roles = {0: spot.oop, 1: spot.ip}
    decisions = []
    weights = weights_of(spot)
    for d in raw["decisions"]:
        node = settle(dict(d["node"], hands=[[list(r) for r in rows] for rows in d["node"]["hands"]]), weights)
        i = spot.action_index[d["step"]]
        player = roles[node["player"]]
        na = len(node["actions"])
        cards = hand.hole_cards.get(player, [])
        row = combo_row(node, node["player"], cards) if len(cards) == 2 else None
        strategy = row[4:4 + na] if row else None
        evs = row[4 + na:4 + 2 * na] if row else None
        chosen = d.get("chosen")
        freq = strategy[chosen] if strategy is not None and chosen is not None else None
        ev_loss = None
        if evs and chosen is not None and evs[chosen] is not None:
            ev_loss = round(max(e for e in evs if e is not None) - evs[chosen], 2)
        tree_amount = node["actions"][chosen]["amount"] if chosen is not None else 0.0
        real = hand.actions[i].to / hand.bb
        range_, classes = node_summary(node)
        decisions.append({
            "i": i, "street": STREET_CODE[hand.actions[i].street], "who": who[player],
            "pot": node["pot"], "board": node["board"], "path": d["path"],
            "actions": [{"label": label, "kind": a["kind"], "allin": a["allin"]}
                        for a, label in zip(node["actions"], action_labels(node))],
            "chosen": chosen, "played": played_label(hand, i),
            "approx": bool(chosen is not None and hand.actions[i].kind in (BET, RAISE)
                           and abs(tree_amount - real) > max(0.1 * real, 0.05)),
            "range": range_, "combo": "".join(cards) if row else None,
            "strategy": strategy, "evs": evs, "eq": row[2] if row else None, "reach": row[1] if row else None,
            "frequency": freq, "verdict": verdict(freq), "ev_loss": ev_loss, "classes": classes,
            "settled": bool(row and row[0] in node["settled"]),
        })
    return {
        "hand": hand.hand_id, "pot_type": spot.pot_type, "pot": spot.pot_bb, "stack": spot.stack_bb,
        "table_format": hand.table_format, "positions": {"H": hand.position(spot.hero), "V": hand.position(spot.villain)},
        "board": spot.board, "oop": who[spot.oop], "menu": spot.menu_text(), "adjusted": spot.adjusted,
        "added": [who[p] for p in spot.added],
        "iterations": raw["iterations"], "exploit_pct": raw["exploit_pct"], "seconds": raw["seconds"],
        "tree_nodes": raw["tree_nodes"], "stopped": raw.get("stopped"), "decisions": decisions,
    }


def cached_node(raw: dict, path: list) -> Optional[dict]:
    """Nœud enregistré pour ce chemin (les décisions de la ligne jouée), sinon None."""
    for d in raw.get("decisions", []):
        if d["path"] == path:
            return d["node"]
    return None


def _strategy_text(actions: list[dict], freqs: list[float]) -> str:
    order = sorted(range(len(actions)), key=lambda k: -freqs[k])
    return ", ".join(f"{actions[k]['label']} {round(100 * freqs[k])} %" for k in order if freqs[k] >= 0.005)


def result_text(result: dict, hero: str) -> str:
    where = "hors position" if result["oop"] == "H" else "en position"
    if result.get("table_format", "HU") != "HU":  # pot à deux à une table à plusieurs
        pos = f"en {result['table_format']}, toi {result['positions']['H']} contre {result['positions']['V']} ({where})"
    else:
        pos = "toi " + ("en BB (hors position)" if result["oop"] == "H" else "au bouton (en position)")
    lines = [f"Main {result['hand']} — {result['pot_type']}, {pos} · flop {' '.join(result['board'])} · "
             f"pot {_num(result['pot'])} bb · tapis {_num(result['stack'])} bb",
             f"GTOpen : {result['iterations']} itérations, exploitabilité {_num(result['exploit_pct'], 2)} % du pot, "
             f"{_num(result['seconds'], 0)} s · {result['tree_nodes']:_} nœuds".replace("_", " "),
             f"Tailles de l'arbre — {result['menu']}"]
    if result.get("adjusted"):
        lines.append("Ranges préflop : les tiennes, ajustées " + ("pour ce coup" if result["adjusted"] == "coup"
                                                                  else "pour cette ligne") + " (pas la référence).")
    if result["added"]:
        lines.append("Note : " + " et ".join("ta main" if w == "H" else "sa main" for w in result["added"])
                     + " n'était pas dans la range du solveur ; elle y a été ajoutée avec un poids infime.")
    for d in result["decisions"]:
        name = "Toi" if d["who"] == "H" else "Lui"
        street = {"f": "Flop", "t": "Turn", "r": "River"}[d["street"]]
        lines.append("")
        lines.append(f"{street} · {name}{' (' + d['combo'] + ')' if d['combo'] else ''} — {d['played']}"
                     + (" (≈ " + d["actions"][d["chosen"]]["label"] + " dans l'arbre)" if d["approx"] else ""))
        lines.append(f"   {'ta' if d['who'] == 'H' else 'sa'} range : {_strategy_text(d['actions'], d['range'])}")
        if d["strategy"] is not None:
            what = "ta main" if d["who"] == "H" else "sa main"
            lines.append(f"   {what} : {_strategy_text(d['actions'], d['strategy'])} → {d['verdict']}"
                         + (f", perte d'EV {_num(d['ev_loss'], 2)} bb" if d["ev_loss"] else "")
                         + (" (le solveur n'arrive presque jamais ici avec cette main : meilleure action selon l'EV)"
                            if d.get("settled") else ""))
    if result["stopped"]:
        lines.append(f"\nArrêt du suivi de la ligne : {result['stopped']}")
    return "\n".join(lines)
