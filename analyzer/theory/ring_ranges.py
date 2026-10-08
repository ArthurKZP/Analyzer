"""Ranges préflop des tables à plusieurs (3 à 9 joueurs), tirées de tes solutions : de quoi résoudre au postflop les
coups où il ne reste que deux joueurs au flop, et juger tes décisions préflop.

Une solution par format de table (« 6-max », « 3-max »), gardée dans la base (documents « ranges ») ; importée
d'un fichier JSON (python -m analyzer ranges --importer FICHIER) ou des charts de Hand2Note Guide (--hand2note).
Une table sans solution à elle prend les charts 6-max à même nombre de joueurs derrière (chart_mapping) :

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
from pathlib import Path
from typing import Callable, Optional

from .. import db
from ..db import documents

POT_TYPES = {1: "SRP", 2: "pot 3bet", 3: "pot 4bet"}


def missing_hint(table_format: str) -> str:
    """Comment donner sa solution d'un format de table ; aux tables à plusieurs, les charts 6-max couvrent toutes les
    tailles de table."""
    if table_format in ("HU", "2-max"):
        return f"importe ta solution {table_format} (python -m analyzer ranges --importer FICHIER.json)"
    return ("importe tes charts 6-max, qui servent à toutes les tables à plusieurs (python -m analyzer ranges "
            "--hand2note pour les charts de Hand2Note Guide, ou --importer FICHIER.json), ou ta solution "
            f"{table_format}")


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


def _valid(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("lines"), dict)


def solution(table_format: str) -> Optional[dict]:
    """Ta solution pour ce format de table (dans la base), ou None."""
    data = documents.get(db.current(), "ranges", table_format)
    return data if _valid(data) else None


def save_solution(table_format: str, data: dict) -> None:
    if not _valid(data):
        raise ValueError("Solution illisible : il faut un objet JSON avec ses lignes (« lines »).")
    documents.put(db.current(), "ranges", table_format, dict(data, format=table_format))


def import_file(path: Path, table_format: Optional[str] = None) -> str:
    """Importe une solution (fichier JSON au format ci-dessus) ; renvoie son format de table."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    table_format = table_format or (data.get("format") if isinstance(data, dict) else None) or Path(path).stem
    if not re.fullmatch(r"(HU|[2-9]-max)", str(table_format)):
        raise ValueError(f"Format de table inconnu : {table_format} (« 6-max », « 3-max »…).")
    save_solution(table_format, data)
    return table_format


def lookup(table_format: str, steps: list[tuple[str, str]]) -> Optional[tuple[str, dict[str, dict[str, float]]]]:
    """(type de pot, {position: range}) pour cette ligne, ou None si ta solution ne la couvre pas."""
    data = solution(table_format)
    entry = (data or {}).get("lines", {}).get(line_key(steps))
    if not isinstance(entry, dict) or not isinstance(entry.get("ranges"), dict):
        return None
    raises = sum(1 for _, kind in steps if kind == "raise")
    pot_type = entry.get("pot_type") or POT_TYPES.get(raises, "pot limpé" if raises == 0 else f"pot {raises + 1}bet")
    return pot_type, {pos: parse_range(text) for pos, text in entry["ranges"].items() if isinstance(text, str)}


# Une table sans solution à elle prend les charts 6-max, position pour position à même nombre de joueurs derrière : le
# 3-max (BTN, SB, BB, comme ses charts tirés du 6-max), une table de 7 à 9 joueurs (le LJ ouvre comme l'UTG de 6-max ;
# les places plus éloignées du bouton, UTG à UTG+2, n'ont pas de chart).
CHART_POSITIONS = {"UTG": "UTG", "HJ": "HJ", "CO": "CO", "BTN": "BTN", "SB": "SB", "BB": "BB"}
FULL_RING_POSITIONS = {"LJ": "UTG", "HJ": "HJ", "CO": "CO", "BTN": "BTN", "SB": "SB", "BB": "BB"}


def chart_mapping(table_format: str, formats) -> Optional[tuple[str, dict[str, str]]]:
    """Les charts d'un format de table, parmi les formats qui ont une solution : (format des charts, position à la
    table -> position dans les charts), ou None. Le format a sa solution, sinon les charts 6-max (voir plus haut)."""
    if table_format in formats:
        return table_format, {}
    if "6-max" not in formats or table_format == "HU":
        return None
    if table_format in ("3-max", "6-max"):
        return "6-max", dict(CHART_POSITIONS)
    return "6-max", dict(FULL_RING_POSITIONS)


def resolve(table_format: str, steps: list[tuple[str, str]]) -> Optional[tuple[str, list[tuple[str, str]]]]:
    """La ligne dans les charts qui la couvrent : (format des charts, étapes aux positions des charts), ou None si
    une position n'a pas de chart."""
    found = chart_mapping(table_format, available())
    if found is None:
        return None
    chart_format, mapping = found
    if not mapping:
        return chart_format, list(steps)
    if any(pos not in mapping for pos, _ in steps):
        return None
    return chart_format, [(mapping[pos], kind) for pos, kind in steps]


def available() -> dict[str, int]:
    """Formats dont tu as donné une solution, avec leur nombre de lignes."""
    out = {}
    for table_format in documents.keys(db.current(), "ranges"):
        data = solution(table_format)
        if data:
            out[table_format] = len(data["lines"])
    return out


# --- Charts préflop gratuits de Hand2Note Guide (6-max, 100 bb) -----------------------------------------------
#
# Le site publie ses charts dans un fichier de données (open de chaque position, réponse à un open, réponse au 3bet
# de l'ouvreur ; fréquences en %, arrondies à 25 %). Ses conditions d'utilisation les réservent à un usage
# personnel : on les télécharge pour toi, dans ta base, jamais dans le dépôt.
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


def install_hand2note(log: Callable[[str], None] = print, js: Optional[str] = None) -> list[str]:
    """Télécharge les charts 6-max de Hand2Note Guide et en tire tes solutions 6-max et 3-max (cette dernière : les
    charts du BTN, de la SB et de la BB), pour ton usage personnel ; renvoie les formats enregistrés."""
    if js is None:
        log(f"Téléchargement des charts préflop de Hand2Note Guide ({HAND2NOTE_PAGE})…")
        request = urllib.request.Request(HAND2NOTE_URL, headers={"User-Agent": "Mozilla/5.0 (Analyzer)"})
        with urllib.request.urlopen(request, timeout=30) as response:
            js = response.read().decode("utf-8", errors="replace")
    charts = parse_hand2note(js)
    vs4bet = vs4bet_reference()
    source = ("Hand2Note Guide — charts préflop GTO 6-max 100 bb (PioSolver, fréquences arrondies à 25 %), usage "
              "personnel ; réponse au 4bet : capture de solveur (SB face au 4bet du bouton), pour toutes les positions")
    formats = []
    for table_format, positions in (("6-max", SIX_MAX), ("3-max", THREE_MAX)):
        lines = lines_from_charts(charts, positions, vs4bet)
        if not lines:
            continue
        data = {"format": table_format, "stack_bb": 100, "url": HAND2NOTE_PAGE, "lines": lines,
                "source": source + ("" if table_format == "6-max" else " ; 3-max : charts 6-max du BTN, de la SB et de la BB")}
        save_solution(table_format, data)
        counts = {t: sum(1 for e in lines.values() if e["pot_type"] == t) for t in ("SRP", "pot 3bet", "pot 4bet")}
        log(f"{table_format} : {len(lines)} lignes ({counts['SRP']} pots simples, {counts['pot 3bet']} pots 3bet, "
            f"{counts['pot 4bet']} pots 4bet)")
        formats.append(table_format)
    if not formats:
        raise ValueError("Format des charts de Hand2Note Guide non reconnu : aucune ligne lue.")
    return formats


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        prog="python -m analyzer ranges",
        description="Ranges préflop des tables à plusieurs (pour résoudre les pots à deux au flop). Sans option : "
                    "les solutions présentes.")
    parser.add_argument("--hand2note", action="store_true",
                        help="télécharge les charts 6-max 100 bb de Hand2Note Guide (usage personnel)")
    parser.add_argument("--importer", metavar="FICHIER",
                        help="importe ta solution d'un format de table (JSON, voir l'aide du module)")
    parser.add_argument("--format", metavar="FORMAT", help="avec --importer : son format (« 6-max »…), s'il n'y "
                                                           "est pas écrit")
    args = parser.parse_args(argv)
    try:
        if args.hand2note:
            install_hand2note()
        if args.importer:
            print(f"Solution {import_file(Path(args.importer).expanduser(), args.format)} importée.")
    except (OSError, ValueError) as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 1
    found = available()
    print("Solutions : " + (", ".join(f"{fmt} ({n} lignes)" for fmt, n in found.items()) if found else "aucune"))
    return 0
