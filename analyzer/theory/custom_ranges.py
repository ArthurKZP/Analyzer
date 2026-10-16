"""Tes ranges préflop ajustées pour le solveur : à la place de celles de la référence (solution heads-up, charts des
tables à plusieurs), pour un coup ou un spot d'étude précis, ou par défaut pour toute une ligne.

Gardées dans la base (document « ranges-ajustees », clé « perso ») :

    {"lignes": {"6-max|CO:raise BB:call": {"CO": "AA,KK,…", "BB": "…"}},
     "lignes_hautes": {…},
     "coups": {"<numéro de main ou spot:srp:KsKd4c>": {"BB": "…", "BTN": "…"}}}

Deux jeux de ranges par ligne (stake_charts.SETS) : « lignes » pour les petites limites (à la place des ranges de la
référence), « lignes_hautes » pour les hautes limites (à la place de leurs charts, sinon de la référence) ; une main
prend le jeu de sa limite, selon tes ranges par défaut (stake_charts.set_of). Tant que les hautes limites n'ont pas
leurs lignes à elles (un document d'avant les deux jeux), elles prennent celles des petites limites : tes réglages
valaient alors pour toutes tes mains ; le premier enregistrement les sépare.

Une ligne : le format (« HU », « 6-max »…) et la clé de ring_ranges (relances et calls préflop avec la position de
leur auteur). Les ranges sont rangées par position ; une position absente garde la range de référence. Le réglage d'un
coup passe avant celui de sa ligne. Une range ajustée change la requête du solveur : le coup se résout à nouveau, dans
une étude à part, et la résolution avec les ranges de référence reste.

Les ranges d'une ligne se règlent depuis un coup (explorateur, onglet Ranges) ou toutes ensemble (Paramètres › Ranges
préflop : reference_lines). Elles valent pour tes coups ; pour les spots d'étude ouverts dans l'explorateur aussi avec
l'option « spots » du document (les séries, plans de jeu et repères restent faits avec la référence).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import db
from ..db import documents
from ..models import MIDDLE
from . import postflop, ring_ranges
from .preflop import load_solution
from .ring_ranges import line_key, parse_range

SCOPES = ("coup", "ligne")
ORDER_FILE = Path(__file__).parent / "data" / "hand_order.json"
_CLASS_RE = re.compile(r"^(?:([2-9TJQKA])\1|[2-9TJQKA]{2}[so])$")
RANKS = "AKQJT98765432"
STEP = 0.10  # « plus serré » / « plus large » : environ 10 % des combos de la range


KIND, KEY = "ranges-ajustees", "perso"


LINES = {"petites": "lignes", "hautes": "lignes_hautes"}  # jeu de ranges -> ses lignes dans le document


def lines_key(rset: str = "petites") -> str:
    if rset not in LINES:
        raise ValueError(f"Jeu de ranges inconnu : {rset}.")
    return LINES[rset]


def load() -> dict:
    data = documents.get(db.current(), KIND, KEY)
    data = data if isinstance(data, dict) else {}
    high = data["lignes_hautes"] if "lignes_hautes" in data else data.get("lignes")
    return {"lignes": dict(data.get("lignes") or {}), "lignes_hautes": dict(high or {}),
            "coups": dict(data.get("coups") or {}), "spots": data.get("spots") is True}


def _save(data: dict) -> None:
    documents.put(db.current(), KIND, KEY, data)


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


def lookup(ident: str, line: Optional[str], rset: str = "petites") -> tuple[Optional[str], dict[str, dict[str, float]]]:
    """(portée, {position: range}) des réglages qui s'appliquent : ceux du coup, complétés par ceux de sa ligne dans ce
    jeu de ranges."""
    data = load()
    by_line = data[lines_key(rset)].get(line, {}) if line else {}
    by_hand = data["coups"].get(ident, {})
    if not by_line and not by_hand:
        return None, {}
    merged = {pos: parse_range(text) for pos, text in {**by_line, **by_hand}.items()}
    return ("coup" if by_hand else "ligne"), merged


def save(scope: str, key: str, ranges: dict[str, str], rset: str = "petites") -> None:
    if scope not in SCOPES or not key:
        raise ValueError("Portée inconnue.")
    texts = {pos: postflop.range_text(check(text)) for pos, text in ranges.items()}
    data = load()
    data["coups" if scope == "coup" else lines_key(rset)][key] = texts
    _save(data)


def clear(scope: str, key: str, rset: str = "petites") -> bool:
    data = load()
    table = data["coups" if scope == "coup" else lines_key(rset)]
    if key not in table:
        return False
    del table[key]
    _save(data)
    return True


def for_spots() -> bool:
    """Tes ranges par défaut des lignes valent aussi pour les spots d'étude ouverts dans l'explorateur."""
    return load()["spots"]


def set_for_spots(value: bool) -> bool:
    data = load()
    data["spots"] = bool(value)
    _save(data)
    return data["spots"]


def same(a: dict[str, float], b: dict[str, float], tolerance: float = 0.002) -> bool:
    """Deux ranges égales, aux arrondis près."""
    return all(abs(a.get(h, 0.0) - b.get(h, 0.0)) <= tolerance for h in set(a) | set(b))


def hand_order() -> list[str]:
    """Les 169 mains de la plus forte à la plus faible (équité contre une main au hasard)."""
    return json.loads(ORDER_FILE.read_text(encoding="utf-8"))["order"]


# --- Les lignes de chaque format, pour régler leurs ranges sans passer par un coup (Paramètres) -------------------

SEAT_ORDER = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")  # l'ordre de parole avant le flop
POT_ORDER = ("pot limpé", "SRP", "pot 3bet", "pot 4bet")


def table_seats(table_format: str) -> Optional[tuple[str, ...]]:
    """Les positions d'un format de table (Hand.table_format), ou None pour un format que tes mains n'ont pas."""
    if table_format == "HU":
        return "BTN", "BB"
    if table_format == "3-max":
        return "BTN", "SB", "BB"
    if table_format == "6-max":
        return "UTG", "HJ", "CO", "BTN", "SB", "BB"
    found = re.fullmatch(r"([7-9]) joueurs", table_format)
    return MIDDLE[int(found.group(1)) - 3] + ("BTN", "SB", "BB") if found else None


def formats(seen=(), heads_up: bool = True, ring: bool = True) -> list[str]:
    """Les formats dont les lignes ont une référence : le heads-up (sa solution), les formats qui ont leurs charts,
    et ceux de tes mains (seen) que les charts 6-max couvrent ; du heads-up aux tables les plus pleines."""
    out = ["HU"] if heads_up else []
    if ring:
        charts = ring_ranges.available()
        found = {f for f in set(charts) | set(seen) if f != "HU" and table_seats(f)
                 and ring_ranges.chart_mapping(f, charts) is not None}
        out += sorted(found, key=lambda f: len(table_seats(f)))
    return out


def reference_name(table_format: str, rset: str = "petites") -> str:
    """D'où viennent les ranges de base d'un format dans ce jeu : « solution préflop heads-up », « Hand2Note Guide
    (6-max) »… ; pour les hautes limites, leurs charts quand ils sont enregistrés."""
    from . import stake_charts
    band = stake_charts.set_band(rset)
    mine = band and stake_charts.stored(table_format if table_format == "HU" else "6-max", band)
    if mine:
        return "charts des hautes limites" + (f" ({mine['source']})" if mine.get("source") else "")
    if table_format == "HU":
        return "solution préflop heads-up"
    found = ring_ranges.chart_mapping(table_format, ring_ranges.available())
    if found is None:
        return "aucune"
    chart_format, mapping = found
    source = (ring_ranges.solution(chart_format) or {}).get("source") or ""
    name = (source.split(" — ")[0] or "ta solution") + f" ({chart_format})"
    return name + (", position pour position" if mapping else "")


def _order(steps: list[tuple[str, str]], pot_type: str) -> tuple:
    seats = [SEAT_ORDER.index(pos) if pos in SEAT_ORDER else len(SEAT_ORDER) for pos, _ in steps]
    return (POT_ORDER.index(pot_type) if pot_type in POT_ORDER else len(POT_ORDER)), seats


def reference_lines(table_format: str, rset: str = "petites") -> list[dict]:
    """Les lignes à deux joueurs au flop d'un format, avec leurs ranges de référence au flop : la solution heads-up,
    ou les charts du format (ceux du 6-max à même nombre de joueurs derrière, ring_ranges.chart_mapping), aux
    positions de la table. [{"key": "CO:raise BB:call", "steps": [("CO", "raise"), ("BB", "call")],
    "pot_type": "SRP", "ranges": {"CO": {…}, "BB": {…}}}], des pots simples aux pots 4bet, dans l'ordre des
    positions."""
    from . import stake_charts
    band = stake_charts.set_band(rset)
    out = []
    if table_format == "HU":
        solution = stake_charts.hu_solution(band)
        for pattern, (pot_type, sb_source, bb_source) in postflop.PREFLOP_LINES.items():
            steps = [("BTN" if who == "sb" else "BB", kind) for who, kind in pattern]
            out.append({"key": line_key(steps), "steps": steps, "pot_type": pot_type,
                        "ranges": {"BTN": postflop.range_weights(solution, *sb_source),
                                   "BB": postflop.range_weights(solution, *bb_source)}})
        return out
    found = ring_ranges.chart_mapping(table_format, ring_ranges.available()) if table_seats(table_format) else None
    if found is None:
        return out
    chart_format, mapping = found
    seats = table_seats(table_format)
    back = {chart: seat for seat, chart in mapping.items() if seat in seats}  # position des charts -> à la table
    lines = stake_charts.ring_lines(band).get(chart_format, {})  # la référence, ou les hautes limites par-dessus
    for key, by_position in lines.items():
        chart_steps = [tuple(step.split(":", 1)) for step in str(key).split()]
        if not chart_steps or any(len(step) != 2 for step in chart_steps):
            continue
        if mapping and any(pos not in back for pos, _ in chart_steps):
            continue
        pot_type = stake_charts.line_pot_type(chart_format, key, band)
        steps = [(back.get(pos, pos) if mapping else pos, kind) for pos, kind in chart_steps]
        players = list(dict.fromkeys(pos for pos, _ in chart_steps))
        if len(players) != 2 or any(pos not in by_position for pos in players):
            continue
        out.append({"key": line_key(steps), "steps": steps, "pot_type": pot_type,
                    "ranges": {(back[pos] if mapping else pos): by_position[pos] for pos in players}})
    return sorted(out, key=lambda line: _order(line["steps"], line["pot_type"]))


def reference_line(line: str, rset: str = "petites") -> Optional[dict]:
    """Une ligne (« 6-max|CO:raise BB:call ») et ses ranges de base dans ce jeu, ou None si elle n'en a pas."""
    table_format, _, key = (line or "").partition("|")
    return next((found for found in reference_lines(table_format, rset) if found["key"] == key), None)
