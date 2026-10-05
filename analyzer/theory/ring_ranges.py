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
from functools import lru_cache
from pathlib import Path
from typing import Optional

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
