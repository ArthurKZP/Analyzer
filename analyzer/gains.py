"""Tes gains selon la limite jouée, la room, le mois, le moment du mois et le créneau horaire.

Un filtre garde les mains d'une valeur pour une ou plusieurs de ces dimensions (« ?limite=NL500&heure=soir ») ; la
répartition donne, pour chaque dimension, tes résultats valeur par valeur sur les mains que les autres filtres gardent
(pour comparer tes limites entre elles, le filtre de limite ne s'y applique pas). L'heure est celle de l'historique.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlencode

from .models import Hand
from .stats import allin_ev
from .tracking import MONTHS

DIMENSIONS = (("limite", "Limite"), ("room", "Room"), ("mois", "Mois"), ("jour", "Moment du mois"),
              ("heure", "Créneau horaire"))
NAMES = dict(DIMENSIONS)
EVERY = {"limite": "Toutes", "room": "Toutes", "mois": "Tous", "jour": "Tout le mois", "heure": "Toute la journée"}
PARTS = (("debut", "Début du mois (1–10)", 10), ("milieu", "Milieu du mois (11–20)", 20),
         ("fin", "Fin du mois (21–31)", 31))
SLOTS = (("nuit", "Nuit (0h–6h)", 6), ("matin", "Matin (6h–12h)", 12), ("apres-midi", "Après-midi (12h–18h)", 18),
         ("soir", "Soir (18h–24h)", 24))
_PATTERNS = {"limite": re.compile(r"NL\d+(\.\d+)?"), "mois": re.compile(r"\d{4}-(0[1-9]|1[0-2])")}
MAX_ROOM = 40
Z95 = 1.96  # marge du hasard à 95 %


def stake(bb: float) -> str:
    """« NL500 » pour une grosse blinde de 5 (la monnaie ne compte pas), « NL2 » pour 0,02."""
    return f"NL{round(bb * 100, 2):g}"


def key(hand: Hand, dimension: str) -> str:
    """La valeur d'une main pour une dimension."""
    if dimension == "limite":
        return stake(hand.bb)
    if dimension == "room":
        return hand.site
    if dimension == "mois":
        return f"{hand.date:%Y-%m}"
    if dimension == "jour":
        return next(name for name, _, top in PARTS if hand.date.day <= top)
    return next(name for name, _, top in SLOTS if hand.date.hour < top)


def label(dimension: str, value: str) -> str:
    """Le nom affiché d'une valeur : « sept. 2026 », « Soir (18h–24h) »…"""
    if dimension == "mois":
        year, month = value.split("-")
        return f"{MONTHS[int(month) - 1]} {year}"
    if dimension == "jour":
        return dict((n, t) for n, t, _ in PARTS).get(value, value)
    if dimension == "heure":
        return dict((n, t) for n, t, _ in SLOTS).get(value, value)
    return value


def _order(dimension: str, value: str):
    if dimension == "limite":
        return (float(value[2:]), value)
    if dimension == "jour":
        return ([n for n, _, _ in PARTS].index(value), value)
    if dimension == "heure":
        return ([n for n, _, _ in SLOTS].index(value), value)
    return (0, value.lower())


def clean(query: dict) -> dict[str, str]:
    """Les filtres valides d'une requête (les autres paramètres et les valeurs vides sont ignorés)."""
    out = {}
    for dimension, _ in DIMENSIONS:
        value = str(query.get(dimension) or "").strip()
        if not value:
            continue
        if dimension in _PATTERNS and not _PATTERNS[dimension].fullmatch(value):
            continue
        if dimension == "jour" and value not in {n for n, _, _ in PARTS}:
            continue
        if dimension == "heure" and value not in {n for n, _, _ in SLOTS}:
            continue
        if dimension == "room" and len(value) > MAX_ROOM:
            continue
        out[dimension] = value
    return out


def apply(hands: Iterable[Hand], filters: dict[str, str], skip: Optional[str] = None) -> list[Hand]:
    """Les mains que les filtres gardent (skip : une dimension dont le filtre est laissé de côté)."""
    wanted = [(d, v) for d, v in filters.items() if d != skip]
    if not wanted:
        return list(hands)
    return [h for h in hands if all(key(h, d) == v for d, v in wanted)]


def describe(filters: dict[str, str]) -> str:
    """« NL500 · Soir (18h–24h) »."""
    return " · ".join(label(d, filters[d]) for d, _ in DIMENSIONS if d in filters)


def query(filters: dict[str, str]) -> str:
    """Les filtres pour une adresse (« limite=NL500&heure=soir »), dans l'ordre des dimensions."""
    return urlencode([(d, filters[d]) for d, _ in DIMENSIONS if d in filters])


@dataclass
class Row:
    """Tes résultats sur les mains d'une valeur : en bb, en monnaie, EV all-in, et la marge du hasard."""
    value: str
    label: str
    hands: int = 0
    net: float = 0.0
    net_bb: float = 0.0
    ev_bb: float = 0.0
    squares: float = 0.0  # somme des carrés des résultats main par main (bb), pour la marge

    @property
    def bb100(self) -> Optional[float]:
        return 100 * self.net_bb / self.hands if self.hands else None

    @property
    def ev100(self) -> Optional[float]:
        return 100 * self.ev_bb / self.hands if self.hands else None

    @property
    def margin(self) -> Optional[float]:
        """La marge du hasard à 95 % sur le winrate (bb/100) : l'écart-type des résultats main par main."""
        if self.hands < 2:
            return None
        mean = self.net_bb / self.hands
        var = max(0.0, (self.squares - self.hands * mean * mean) / (self.hands - 1))
        return 100 * Z95 * math.sqrt(var / self.hands)


def add(row: Row, hand: Hand, hero: str) -> None:
    """Ajoute cette main aux résultats du joueur (en bb, en monnaie, en EV all-in)."""
    net = hand.net(hero)
    ev = allin_ev(hand)
    net_bb = net / hand.bb
    row.hands += 1
    row.net += net
    row.net_bb += net_bb
    row.ev_bb += ev[hero] / hand.bb if ev is not None and hero in ev else net_bb
    row.squares += net_bb * net_bb


def mine(hands: Iterable[Hand], hero: str) -> list[Hand]:
    """Tes mains (assis, grosse blinde connue)."""
    return [h for h in hands if hero in h.seats and h.bb]


def breakdown(hands: list[Hand], hero: str, dimension: str, filters: dict[str, str]) -> list[Row]:
    """Tes résultats valeur par valeur pour une dimension, sur les mains que les autres filtres gardent."""
    rows: dict[str, Row] = {}
    for h in apply(hands, filters, skip=dimension):
        value = key(h, dimension)
        row = rows.get(value)
        if row is None:
            row = rows[value] = Row(value, label(dimension, value))
        add(row, h, hero)
    return sorted(rows.values(), key=lambda r: _order(dimension, r.value))


def total(hands: list[Hand], hero: str) -> Row:
    row = Row("", "Total")
    for h in hands:
        add(row, h, hero)
    return row


def options(hands: list[Hand], dimension: str, filters: dict[str, str]) -> list[tuple[str, str, int]]:
    """Les valeurs à proposer pour une dimension : (valeur, nom, nombre de mains que les autres filtres gardent),
    la valeur choisie toujours comprise."""
    counts: dict[str, int] = {}
    for h in apply(hands, filters, skip=dimension):
        value = key(h, dimension)
        counts[value] = counts.get(value, 0) + 1
    chosen = filters.get(dimension)
    if chosen and chosen not in counts:
        counts[chosen] = 0
    return [(v, label(dimension, v), counts[v]) for v in sorted(counts, key=lambda v: _order(dimension, v))]
