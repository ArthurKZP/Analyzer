"""Tes ranges préflop ajustées pour le solveur : à la place de celles de la référence (solution heads-up, charts des
tables à plusieurs), pour un coup ou un spot d'étude précis, ou par défaut pour toute une ligne.

Fichier ~/.analyzer/ranges/perso.json (sauvegardé) :

    {"lignes": {"6-max|CO:raise BB:call": {"CO": "AA,KK,…", "BB": "…"}},
     "coups": {"<numéro de main ou spot:srp:KsKd4c>": {"BB": "…", "BTN": "…"}}}

Une ligne : le format (« HU », « 6-max »…) et la clé de ring_ranges (relances et calls préflop avec la position de
leur auteur). Les ranges sont rangées par position ; une position absente garde la range de référence. Le réglage d'un
coup passe avant celui de sa ligne. Une range ajustée change la requête du solveur : le coup se résout à nouveau, dans
une étude à part, et la résolution avec les ranges de référence reste.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import postflop
from .ring_ranges import line_key, parse_range

SCOPES = ("coup", "ligne")
ORDER_FILE = Path(__file__).parent / "data" / "hand_order.json"
_CLASS_RE = re.compile(r"^(?:([2-9TJQKA])\1|[2-9TJQKA]{2}[so])$")
RANKS = "AKQJT98765432"
STEP = 0.10  # « plus serré » / « plus large » : environ 10 % des combos de la range


def path() -> Path:
    return postflop.home() / "ranges" / "perso.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"lignes": dict(data.get("lignes") or {}), "coups": dict(data.get("coups") or {})}


def _save(data: dict) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def context(table_format: str, steps: list[tuple[str, str]]) -> str:
    """« 6-max|CO:raise BB:call » ; en heads-up, « HU|BTN:raise BB:call »."""
    return f"{table_format}|{line_key(steps)}"


def valid_class(hand: str) -> bool:
    if not _CLASS_RE.match(hand):
        return False
    return len(hand) == 2 or RANKS.index(hand[0]) < RANKS.index(hand[1])


def check(text: str) -> dict[str, float]:
    """La range lue dans le texte ; ValueError si une main est inconnue ou si la range est vide."""
    try:
        weights = parse_range(text or "")
    except ValueError as exc:
        raise ValueError(f"Range illisible : {exc}") from exc
    bad = [h for h in weights if not valid_class(h)]
    if bad:
        raise ValueError(f"Mains inconnues : {', '.join(bad[:5])} (écris AA, AKs, AKo…).")
    if not weights:
        raise ValueError("Range vide : garde au moins une main.")
    return weights


def lookup(ident: str, line: Optional[str]) -> tuple[Optional[str], dict[str, dict[str, float]]]:
    """(portée, {position: range}) des réglages qui s'appliquent : ceux du coup, complétés par ceux de sa ligne."""
    data = load()
    by_line = data["lignes"].get(line, {}) if line else {}
    by_hand = data["coups"].get(ident, {})
    if not by_line and not by_hand:
        return None, {}
    merged = {pos: parse_range(text) for pos, text in {**by_line, **by_hand}.items()}
    return ("coup" if by_hand else "ligne"), merged


def save(scope: str, key: str, ranges: dict[str, str]) -> None:
    if scope not in SCOPES or not key:
        raise ValueError("Portée inconnue.")
    texts = {pos: postflop.range_text(check(text)) for pos, text in ranges.items()}
    data = load()
    data["coups" if scope == "coup" else "lignes"][key] = texts
    _save(data)


def clear(scope: str, key: str) -> bool:
    data = load()
    table = data["coups" if scope == "coup" else "lignes"]
    if key not in table:
        return False
    del table[key]
    _save(data)
    return True


def same(a: dict[str, float], b: dict[str, float], tolerance: float = 0.002) -> bool:
    """Deux ranges égales, aux arrondis près."""
    return all(abs(a.get(h, 0.0) - b.get(h, 0.0)) <= tolerance for h in set(a) | set(b))


def hand_order() -> list[str]:
    """Les 169 mains de la plus forte à la plus faible (équité contre une main au hasard)."""
    return json.loads(ORDER_FILE.read_text(encoding="utf-8"))["order"]
