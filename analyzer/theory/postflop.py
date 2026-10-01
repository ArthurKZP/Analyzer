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
NATIVE_SOURCE = Path(__file__).parent / "native" / "main.rs"
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
    return hashlib.sha256(NATIVE_SOURCE.read_bytes()).hexdigest()[:16]


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
    shutil.copyfile(NATIVE_SOURCE, root / "main.rs")
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


def _size_order(size) -> float:
    return float("inf") if size == "a" else size


def _size_json(sizes: list) -> list:
    return ["AllIn" if s == "a" else {"PotPct": s} for s in sizes]


@dataclass
class PostflopSpot:
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

    @property
    def board(self) -> list[str]:
        return self.hand.board[:3]

    def combos(self) -> dict[str, int]:
        """Mains connues -> joueur (0 = hors de position, 1 = en position)."""
        return {"".join(self.hand.hole_cards[p]): 0 if p == self.oop else 1 for p in (self.hero, self.villain)
                if len(self.hand.hole_cards.get(p, [])) == 2}

    def tree(self) -> dict:
        def streets(player: str) -> list[dict]:
            return [{k: _size_json(v) for k, v in s.items()} for s in self.sizes[player]]
        return {"starting_pot": self.pot_bb, "effective_stack": self.stack_bb, "rake_pct": 0.0, "rake_cap": 0.0,
                "oop": streets(self.oop), "ip": streets(self.ip), "allin_threshold": 0.85,
                "add_allin": False, "max_raises": MAX_RAISES}

    def request(self, iterations: int = DEFAULT_ITERATIONS, target: float = DEFAULT_TARGET,
                threads: int = 0) -> dict:
        return {
            "spot": {"board": "".join(self.board), "range_oop": range_text(self.ranges[self.oop]),
                     "range_ip": range_text(self.ranges[self.ip]), "tree": self.tree()},
            "line": self.line, "combos": self.combos(),
            "max_iterations": iterations, "target_exploit_pct": target, "threads": threads,
            "gpu": gpu_enabled(),
        }

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


def build_spot(hand: Hand, hero: str, solution: Optional[Solution] = None) -> PostflopSpot:
    solution = solution or load_solution()
    sb, bb_player = hand.button, hand.big_blind
    villain = hand.opponent_of(hero)
    if len(hand.seats) != 2 or not sb or not bb_player or villain is None:
        raise Unsupported("Seules les mains heads-up se résolvent.")
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY]
    if any(a.all_in for a in pre):
        raise Unsupported("Tapis préflop : il n'y a plus de décision après le flop.")
    if len(hand.board) < 3 or not any(a.street == "flop" for a in hand.actions):
        raise Unsupported("La main s'arrête avant le flop : rien à résoudre après le flop.")
    pattern = tuple(("sb" if a.player == sb else "bb", a.kind) for a in pre)
    if pattern not in PREFLOP_LINES:
        if pattern[:1] == (("sb", CALL),):
            raise Unsupported("Pot limpé : la solution préflop ne donne pas de range pour ce cas.")
        raise Unsupported("Ligne préflop hors de la solution (seuls SRP, pots 3bet et 4bet sont couverts).")
    pot_type, sb_source, bb_source = PREFLOP_LINES[pattern]
    ranges = {sb: range_weights(solution, *sb_source), bb_player: range_weights(solution, *bb_source)}

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
    pot = sum(put.values())
    stack = min(hand.seats[p].stack - put[p] for p in hand.seats)
    if stack <= 0:
        raise Unsupported("Tapis préflop : il n'y a plus de décision après le flop.")

    oop, ip = bb_player, sb
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

    sizes = {p: [{"bet": _merge_sizes([DEFAULT_BETS[s]], played[p][s]["bet"]),
                  "raise": _merge_sizes(list(DEFAULT_RAISES[s]), played[p][s]["raise"]),
                  "donk": _merge_sizes([], played[p][s]["donk"]) if p == oop else []}
                 for s in range(3)] for p in (oop, ip)}
    return PostflopSpot(hand, hero, villain, pot_type, oop, ip, round(pot / bb, 4), round(stack / bb, 4),
                        ranges, line, index, sizes, added)


# --- Exécution et cache --------------------------------------------------------------------

def cache_key(request: dict) -> str:
    text = json.dumps(request, sort_keys=True, ensure_ascii=False) + _native_hash()
    return hashlib.sha256(text.encode()).hexdigest()[:20]


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


def solve(request: dict, on_progress: Optional[Callable[[dict], None]] = None,
          on_start: Optional[Callable[[subprocess.Popen], None]] = None, use_cache: bool = True) -> dict:
    """Lance analyzer-solve ; on_progress reçoit chaque mesure (itération, exploitabilité)."""
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
        error = None
        with out_path.open("w", encoding="utf-8") as out:
            # stdout vers un fichier : le résultat peut dépasser la taille d'un tube.
            proc = subprocess.Popen([str(exe), str(req_path)], stdout=out, stderr=subprocess.PIPE, text=True)
            if on_start:
                on_start(proc)
            with proc.stderr:
                for raw in proc.stderr:
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        continue
                    if "error" in data:
                        error = data["error"]
                    elif on_progress:
                        on_progress(data)
            proc.wait()
        if proc.returncode != 0:
            raise SolverError(error or f"analyzer-solve s'est arrêté (code {proc.returncode}).")
        result = json.loads(out_path.read_text(encoding="utf-8"))
    path = cache_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


# --- Lecture du résultat -------------------------------------------------------------------

def _num(x: float, digits: int = 1) -> str:
    text = f"{x:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{x:.0f}"
    return text.replace(".", ",")


def action_label(action: dict, pot: float) -> str:
    kind, amount = action["kind"], action["amount"]
    if kind in (BET, RAISE) and action.get("allin"):
        return f"tapis ({_num(amount)} bb)"
    if kind == BET:
        return f"mise {round(100 * amount / pot)} % ({_num(amount)} bb)"
    if kind == RAISE:
        return f"relance à {_num(amount)} bb"
    return {CHECK: "check", CALL: "call", FOLD: "fold"}.get(kind, kind)


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


def interpret(spot: PostflopSpot, raw: dict) -> dict:
    """Résultat lisible : chaque décision de la main face au solveur (données pour l'application)."""
    hand = spot.hand
    who = {spot.hero: "H", spot.villain: "V"}
    roles = {0: spot.oop, 1: spot.ip}
    decisions = []
    for d in raw["decisions"]:
        i = spot.action_index[d["step"]]
        player = roles[d["player"]]
        cards = hand.hole_cards.get(player, [])
        combo = "".join(cards) if len(cards) == 2 else None
        mine = d["combos"].get(combo) if combo else None
        strategy = mine.get("s") if mine and mine.get("player") == d["player"] else None
        evs = mine.get("evs") if strategy else None
        chosen = d.get("chosen")
        freq = strategy[chosen] if strategy is not None and chosen is not None else None
        ev_loss = None
        if evs and chosen is not None and evs[chosen] is not None:
            ev_loss = round(max(e for e in evs if e is not None) - evs[chosen], 2)
        tree_amount = d["actions"][chosen]["amount"] if chosen is not None else 0.0
        real = hand.actions[i].to / hand.bb
        decisions.append({
            "i": i, "street": STREET_CODE[hand.actions[i].street], "who": who[player],
            "pot": d["pot"], "board": d["board"],
            "actions": [{"label": action_label(a, d["pot"]), "kind": a["kind"], "allin": a["allin"]}
                        for a in d["actions"]],
            "chosen": chosen, "played": played_label(hand, i),
            "approx": bool(chosen is not None and hand.actions[i].kind in (BET, RAISE)
                           and abs(tree_amount - real) > max(0.1 * real, 0.05)),
            "range": d["range"], "combo": combo if strategy is not None else None,
            "strategy": strategy, "evs": evs, "eq": mine.get("eq") if mine else None,
            "reach": mine.get("reach") if mine else None,
            "frequency": freq, "verdict": verdict(freq), "ev_loss": ev_loss,
            "classes": {c: [round(v["w"] / v["n"], 3) if v["n"] else 0.0, *v["s"]]
                        for c, v in d["classes"].items() if v["w"] > 0},
        })
    return {
        "hand": hand.hand_id, "pot_type": spot.pot_type, "pot": spot.pot_bb, "stack": spot.stack_bb,
        "board": spot.board, "oop": who[spot.oop], "menu": spot.menu_text(),
        "added": [who[p] for p in spot.added],
        "iterations": raw["iterations"], "exploit_pct": raw["exploit_pct"], "seconds": raw["seconds"],
        "tree_nodes": raw["tree_nodes"], "stopped": raw.get("stopped"), "decisions": decisions,
    }


def _strategy_text(actions: list[dict], freqs: list[float]) -> str:
    order = sorted(range(len(actions)), key=lambda k: -freqs[k])
    return ", ".join(f"{actions[k]['label']} {round(100 * freqs[k])} %" for k in order if freqs[k] >= 0.005)


def result_text(result: dict, hero: str) -> str:
    pos = "en BB (hors position)" if result["oop"] == "H" else "au bouton (en position)"
    lines = [f"Main {result['hand']} — {result['pot_type']}, toi {pos} · flop {' '.join(result['board'])} · "
             f"pot {_num(result['pot'])} bb · tapis {_num(result['stack'])} bb",
             f"GTOpen : {result['iterations']} itérations, exploitabilité {_num(result['exploit_pct'], 2)} % du pot, "
             f"{_num(result['seconds'], 0)} s · {result['tree_nodes']:_} nœuds".replace("_", " "),
             f"Tailles de l'arbre — {result['menu']}"]
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
                         + (f", perte d'EV {_num(d['ev_loss'], 2)} bb" if d["ev_loss"] else ""))
    if result["stopped"]:
        lines.append(f"\nArrêt du suivi de la ligne : {result['stopped']}")
    return "\n".join(lines)
