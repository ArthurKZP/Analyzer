"""Ranges préflop des tables à plusieurs (3-max, 6-max), tirées de tes solutions : de quoi résoudre au postflop les
coups où il ne reste que deux joueurs au flop.

Une solution par format de table, dans ~/.analyzer/ranges/<format>.json (« 6-max.json », « 3-max.json ») :

    {"format": "6-max", "stack_bb": 100, "source": "…",
     "lines": {"CO:raise BB:call": {"pot_type": "SRP", "ranges": {"CO": "AA,AKs,KQo:0.5,…", "BB": "…"}},
               "BTN:raise SB:raise BTN:call": {"pot_type": "pot 3bet", "ranges": {"SB": "…", "BTN": "…"}}}}

La clé d'une ligne : les actions préflop qui mettent de l'argent (relances et calls), dans l'ordre, avec la position
de leur auteur ; les folds n'y figurent pas. La range de chaque joueur est celle qu'il a au flop, au format des
solveurs (« main » ou « main:poids », poids de 0 à 1).
"""
from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from . import postflop

POT_TYPES = {1: "SRP", 2: "pot 3bet", 3: "pot 4bet"}


def folder() -> Path:
    return postflop.home() / "ranges"


def line_key(steps: list[tuple[str, str]]) -> str:
    """[("CO", "raise"), ("BB", "call")] -> "CO:raise BB:call"."""
    return " ".join(f"{pos}:{kind}" for pos, kind in steps)


def describe(steps: list[tuple[str, str]]) -> str:
    """La ligne en mots : « CO open, BB call »."""
    words, raises = [], 0
    for pos, kind in steps:
        if kind == "raise":
            raises += 1
            words.append(f"{pos} {('open', '3bet', '4bet', '5bet')[min(raises, 4) - 1]}")
        else:
            words.append(f"{pos} {'limp' if raises == 0 else 'call'}")
    return ", ".join(words)


def parse_range(text: str) -> dict[str, float]:
    """« AA,AKs:0.5,KQo » -> {"AA": 1.0, "AKs": 0.5, "KQo": 1.0} (classes de mains, poids de 0 à 1)."""
    out: dict[str, float] = {}
    for item in text.replace(" ", "").split(","):
        if not item:
            continue
        hand, _, weight = item.partition(":")
        value = float(weight) if weight else 1.0
        if value > 0:
            out[hand] = round(min(value, 1.0), 4)
    return out


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("lines"), dict) else None


def solution(table_format: str) -> Optional[dict]:
    path = folder() / f"{table_format}.json"
    if not path.is_file():
        return None
    return _load(str(path), path.stat().st_mtime)


def lookup(table_format: str, steps: list[tuple[str, str]]) -> Optional[tuple[str, dict[str, dict[str, float]]]]:
    """(type de pot, {position: range}) pour cette ligne, ou None si ta solution ne la couvre pas."""
    data = solution(table_format)
    entry = (data or {}).get("lines", {}).get(line_key(steps))
    if not isinstance(entry, dict) or not isinstance(entry.get("ranges"), dict):
        return None
    raises = sum(1 for _, kind in steps if kind == "raise")
    pot_type = entry.get("pot_type") or POT_TYPES.get(raises, "pot limpé" if raises == 0 else f"pot {raises + 1}bet")
    return pot_type, {pos: parse_range(text) for pos, text in entry["ranges"].items() if isinstance(text, str)}


def available() -> dict[str, int]:
    """Formats dont tu as donné une solution, avec leur nombre de lignes."""
    out = {}
    for path in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        data = solution(path.stem)
        if data:
            out[path.stem] = len(data["lines"])
    return out


# --- Charts préflop gratuits de Hand2Note Guide (6-max, 100 bb) -----------------------------------------------
#
# Le site publie ses charts dans un fichier de données (open de chaque position, réponse à un open, réponse au 3bet
# de l'ouvreur ; fréquences en %, arrondies à 25 %). Ses conditions d'utilisation les réservent à un usage
# personnel : on les télécharge sur ta machine, dans ~/.analyzer/ranges, jamais dans le dépôt.
# Pots couverts : pots simples (open, call), pots 3bet (open, 3bet, call) et pots 4bet (open, 3bet, 4bet, call) :
# le site ne publiant pas la réponse au 4bet, celle du 3bettor vient d'une réponse type (data/vs4bet_reference.json,
# la SB face au 4bet du bouton) appliquée à toutes les positions. Le 3-max reprend les charts du BTN, de la SB et
# de la BB.
VS4BET_REFERENCE = Path(__file__).parent / "data" / "vs4bet_reference.json"
THREE_MAX = ("BTN", "SB", "BB")
HAND2NOTE_URL = "https://hand2noteguide.com/wp-content/themes/sequential/js/preflop-gto-data.js"
HAND2NOTE_PAGE = "https://hand2noteguide.com/fr/poker/free-poker-tools/preflop-gto-charts/"
SIX_MAX = ("UTG", "HJ", "CO", "BTN", "SB", "BB")

_BLOCK_RE = re.compile(r"\b(rfi|vs_rfi|vs_3bet_base|vs_3bet)\.(\w+)\s*=\s*\{(.*?)\};", re.S)
_ENTRY_RE = re.compile(r'"([2-9TJQKA]{2}[so]?)"\s*:\s*(?:(R|C)\((\d+(?:\.\d+)?)\)|RC\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)\)|\{([^}]*)\})')
_FIELD_RE = re.compile(r"([RBC])\s*:\s*(\d+(?:\.\d+)?)")
_DEFAULTS_RE = re.compile(r"assignVs3betDefaults\(\s*'(\w+)'\s*,\s*\[([^\]]*)\]\s*\)")


def _entries(body: str) -> dict[str, dict[str, float]]:
    """{"AA": {"R": 1.0}, "ATs": {"R": 0.25, "C": 0.75}, …} (fréquences de 0 à 1, fold = le reste)."""
    out = {}
    for hand, kind, value, r, c, obj in _ENTRY_RE.findall(body):
        if kind:
            freqs = {kind: float(value)}
        elif r:
            freqs = {"R": float(r), "C": float(c)}
        else:
            freqs = {k: float(v) for k, v in _FIELD_RE.findall(obj)}
        out[hand] = {k: v / 100 for k, v in freqs.items() if v > 0}
    return out


def parse_hand2note(js: str) -> dict[str, dict[str, dict]]:
    """Le fichier de données des charts en {"rfi": {"CO": …}, "vs_rfi": {"BB_vs_CO": …}, "vs_3bet": {"CO_vs_BB": …}}."""
    data: dict[str, dict] = {"rfi": {}, "vs_rfi": {}, "vs_3bet_base": {}, "vs_3bet": {}}
    for category, name, body in _BLOCK_RE.findall(js):
        data[category][name] = _entries(body)
    defaults = {}  # comme le script du site : la range de base d'un ouvreur, avant les ranges par adversaire
    for hero, villains in _DEFAULTS_RE.findall(js):
        for villain in re.findall(r"'(\w+)'", villains):
            if hero in data["vs_3bet_base"]:
                defaults[f"{hero}_vs_{villain}"] = data["vs_3bet_base"][hero]
    data["vs_3bet"] = {**defaults, **data["vs_3bet"]}
    del data["vs_3bet_base"]
    return data


def _text(weights: dict[str, float]) -> str:
    return ",".join(h if w >= 0.999 else f"{h}:{round(w, 4):g}" for h, w in weights.items() if w >= 0.001)


def _times(a: dict[str, dict], key_a: str, b: Optional[dict[str, dict]] = None, key_b: str = "") -> dict[str, float]:
    """Poids de chaque main : fréquence de l'action dans un chart, multipliée par celle d'un second chart."""
    out = {}
    for hand, freqs in a.items():
        w = freqs.get(key_a, 0.0) * ((b or {}).get(hand, {}).get(key_b, 0.0) if b is not None else 1.0)
        if w > 0:
            out[hand] = w
    return out


def vs4bet_reference() -> dict[str, dict[str, float]]:
    """Réponse type du 3bettor au 4bet, main par main : {"AKs": {"allin": 1.0}, "AQs": {"C": 1.0}, …}."""
    hands = json.loads(VS4BET_REFERENCE.read_text(encoding="utf-8"))["hands"]
    return {h: {"C": f["call"]} for h, f in hands.items() if f.get("call")}


def lines_from_charts(charts: dict[str, dict[str, dict]], positions: tuple[str, ...] = SIX_MAX,
                      vs4bet: Optional[dict[str, dict]] = None) -> dict[str, dict]:
    """Les lignes à deux joueurs entre ces positions (de la première à parler à la BB) : pots simples, pots 3bet et,
    avec une réponse au 4bet, pots 4bet ; la range de chacun au flop."""
    lines = {}
    for k, opener in enumerate(positions[:-1]):
        opens = charts["rfi"].get(opener)
        if not opens:
            continue
        for caller in positions[k + 1:]:
            facing = charts["vs_rfi"].get(f"{caller}_vs_{opener}")
            if not facing:
                continue
            lines[line_key([(opener, "raise"), (caller, "call")])] = {
                "pot_type": "SRP", "ranges": {opener: _text(_times(opens, "R")), caller: _text(_times(facing, "C"))}}
            versus = charts["vs_3bet"].get(f"{opener}_vs_{caller}")
            if versus:
                lines[line_key([(opener, "raise"), (caller, "raise"), (opener, "call")])] = {
                    "pot_type": "pot 3bet",
                    "ranges": {opener: _text(_times(opens, "R", versus, "C")), caller: _text(_times(facing, "R"))}}
                if vs4bet:  # 4bet hors tapis (R ; B = tapis), payé par le 3bettor selon la réponse type
                    fourbets = _times(opens, "R", versus, "R")
                    callers = {h: w * vs4bet.get(h, {}).get("C", 0.0) for h, w in _times(facing, "R").items()}
                    lines[line_key([(opener, "raise"), (caller, "raise"), (opener, "raise"), (caller, "call")])] = {
                        "pot_type": "pot 4bet",
                        "ranges": {opener: _text(fourbets), caller: _text({h: w for h, w in callers.items() if w > 0})}}
    return {key: entry for key, entry in lines.items() if all(entry["ranges"].values())}


def install_hand2note(log: Callable[[str], None] = print, js: Optional[str] = None) -> list[Path]:
    """Télécharge les charts 6-max de Hand2Note Guide et écrit ~/.analyzer/ranges/6-max.json et 3-max.json (ce
    dernier : les charts du BTN, de la SB et de la BB), pour ton usage personnel."""
    if js is None:
        log(f"Téléchargement des charts préflop de Hand2Note Guide ({HAND2NOTE_PAGE})…")
        request = urllib.request.Request(HAND2NOTE_URL, headers={"User-Agent": "Mozilla/5.0 (Analyzer)"})
        with urllib.request.urlopen(request, timeout=30) as response:
            js = response.read().decode("utf-8", errors="replace")
    charts = parse_hand2note(js)
    vs4bet = vs4bet_reference()
    source = ("Hand2Note Guide — charts préflop GTO 6-max 100 bb (PioSolver, fréquences arrondies à 25 %), usage "
              "personnel ; réponse au 4bet : capture de solveur (SB face au 4bet du bouton), pour toutes les positions")
    paths = []
    folder().mkdir(parents=True, exist_ok=True)
    for table_format, positions in (("6-max", SIX_MAX), ("3-max", THREE_MAX)):
        lines = lines_from_charts(charts, positions, vs4bet)
        if not lines:
            continue
        data = {"format": table_format, "stack_bb": 100, "url": HAND2NOTE_PAGE, "lines": lines,
                "source": source + ("" if table_format == "6-max" else " ; 3-max : charts 6-max du BTN, de la SB et de la BB")}
        path = folder() / f"{table_format}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        counts = {t: sum(1 for e in lines.values() if e["pot_type"] == t) for t in ("SRP", "pot 3bet", "pot 4bet")}
        log(f"{table_format} : {len(lines)} lignes ({counts['SRP']} pots simples, {counts['pot 3bet']} pots 3bet, "
            f"{counts['pot 4bet']} pots 4bet) dans {path}")
        paths.append(path)
    if not paths:
        raise ValueError("Format des charts de Hand2Note Guide non reconnu : aucune ligne lue.")
    return paths


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        prog="python -m analyzer ranges",
        description="Ranges préflop des tables à plusieurs (pour résoudre les pots à deux au flop). Sans option : "
                    "les solutions présentes.")
    parser.add_argument("--hand2note", action="store_true",
                        help="télécharge les charts 6-max 100 bb de Hand2Note Guide (usage personnel)")
    args = parser.parse_args(argv)
    if args.hand2note:
        try:
            install_hand2note()
        except (OSError, ValueError) as exc:
            print(f"Échec : {exc}", file=sys.stderr)
            return 1
    found = available()
    print("Solutions : " + (", ".join(f"{fmt} ({n} lignes)" for fmt, n in found.items()) if found else "aucune")
          + f" — dossier {folder()}")
    return 0
