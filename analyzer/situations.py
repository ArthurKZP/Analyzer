"""Tes résultats par situation : les pots joués à deux au flop après une ligne préflop simple (open, 3bet, 4bet, puis
call), rangés par type de pot (SRP, pot 3bet « P3B », pot 4bet « P4B »), par place après le flop (en position « IP »
ou non « OOP ») et par rôle (attaquant : le dernier relanceur préflop ; défenseur : celui qui a payé), puis par
positions des deux joueurs (« CO vs BB »), en heads-up comme aux tables à plusieurs.

Pour chacune : tes pots, ton résultat par pot en EV all-in (le hasard des tapis retiré) et sa marge à 95 %, ce qu'elle
pèse dans ton winrate (bb/100 de toutes tes mains) ; comparé aux réguliers de ton field dans la même situation (leurs
pots dans tes historiques, contre toi ou contre d'autres) et, quand il y en a, à un repère (REFERENCES). L'écart avec
eux, multiplié par la fréquence de la situation, dit où tu gagnes ou perds le plus face à eux (bb/100)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from . import gains, ring_leaks
from .models import CALL, FOLD, RAISE, VOLUNTARY, Hand

KINDS = {"srp": "SRP", "3bet": "P3B", "4bet": "P4B"}
# Les catégories, des plus jouées aux plus rares : (type de pot, en position ?, attaquant ?)
CATEGORIES = (
    ("srp", True, True), ("srp", False, True), ("srp", False, False), ("srp", True, False),
    ("3bet", False, True), ("3bet", True, False), ("3bet", True, True), ("3bet", False, False),
    ("4bet", False, False), ("4bet", False, True), ("4bet", True, False), ("4bet", True, True),
)
# Repères (bb par pot, EV all-in) d'un régulier gagnant, par catégorie (« SRP IP attaquant ») ou par situation
# (« SRP IP attaquant · CO vs BB ») ; vide tant qu'aucun repère n'est choisi : la comparaison se fait avec le field.
REFERENCES: dict[str, float] = {}
MIN_POTS = 20      # pots minimum (toi) pour classer une situation parmi tes plus fortes ou tes plus faibles
SMALL = 1000       # pots à deux en tout : en dessous, l'échantillon reste petit
Z95 = 1.96


@dataclass(frozen=True)
class Spot:
    """La situation d'un joueur dans un pot à deux au flop."""
    kind: str        # srp, 3bet, 4bet
    ip: bool         # en position après le flop
    attacker: bool   # le dernier relanceur préflop
    position: str
    versus: str

    @property
    def category(self) -> tuple[str, bool, bool]:
        return self.kind, self.ip, self.attacker

    @property
    def label(self) -> str:
        return category_label(self.category)

    @property
    def pair(self) -> str:
        return f"{self.position} vs {self.versus}"


def category_label(category: tuple[str, bool, bool]) -> str:
    """« SRP IP attaquant », « P3B OOP défenseur »."""
    kind, ip, attacker = category
    return f"{KINDS[kind]} {'IP' if ip else 'OOP'} {'attaquant' if attacker else 'défenseur'}"


def _hu_pot(hand: Hand) -> Optional[ring_leaks.Pot]:
    """Le pot d'une main heads-up allée au flop après open, 3bet ou 4bet puis call (sans tapis avant le flop) : la BB
    hors de position, le bouton en position."""
    if len(hand.board) < 3 or not hand.button:
        return None
    pre = [a for a in hand.actions if a.street == "preflop" and a.kind in VOLUNTARY]
    if any(a.kind == FOLD or a.all_in for a in pre):
        return None
    pre = [a for a in pre if a.kind in (RAISE, CALL)]
    raises = sum(a.kind == RAISE for a in pre)
    if raises not in ring_leaks.POTS or [a.kind for a in pre] != [RAISE] * raises + [CALL]:
        return None
    ip = hand.button
    oop = hand.opponent_of(ip)
    if oop is None:
        return None
    return ring_leaks.Pot(oop, ip, ring_leaks.POTS[raises], pre[-2].player, ("BB", "BTN"))


def pot_of(hand: Hand) -> Optional[ring_leaks.Pot]:
    """Le pot à deux au flop d'une main (heads-up, ou table à plusieurs : ring_leaks.pot_of), ou None."""
    if len(hand.seats) == 2:
        if "_hu_pot" not in hand.__dict__:
            hand.__dict__["_hu_pot"] = _hu_pot(hand)
        return hand.__dict__["_hu_pot"]
    return ring_leaks.pot_of(hand)


def spot_of(hand: Hand, player: str) -> Optional[Spot]:
    """La situation de ce joueur dans la main, s'il a joué un pot à deux au flop."""
    pot = pot_of(hand)
    if pot is None or player not in (pot.oop, pot.ip):
        return None
    ip = player == pot.ip
    return Spot(pot.kind, ip, player == pot.aggressor, pot.positions[1 if ip else 0], pot.positions[0 if ip else 1])


@dataclass
class Line:
    """Une situation (ou une catégorie : pair vide) : tes résultats, ceux des réguliers de ton field, l'écart."""
    category: tuple[str, bool, bool]
    pair: str
    mine: gains.Row
    field: gains.Row
    hands: int  # toutes tes mains (pour ramener l'écart à 100 mains)

    @property
    def label(self) -> str:
        return category_label(self.category) + (f" · {self.pair}" if self.pair else "")

    @property
    def per_pot(self) -> Optional[float]:
        return self.mine.ev_bb / self.mine.hands if self.mine.hands else None

    @property
    def field_per_pot(self) -> Optional[float]:
        return self.field.ev_bb / self.field.hands if self.field.hands else None

    @property
    def margin(self) -> Optional[float]:
        """La marge du hasard à 95 % sur ton résultat par pot."""
        return None if self.mine.margin is None else self.mine.margin / 100

    @property
    def weight(self) -> Optional[float]:
        """Ce que la situation pèse dans ton winrate : ton résultat (EV all-in) pour 100 de toutes tes mains."""
        return 100 * self.mine.ev_bb / self.hands if self.hands else None

    @property
    def gap(self) -> Optional[float]:
        """Ton résultat par pot moins celui des réguliers du field."""
        if self.per_pot is None or self.field_per_pot is None:
            return None
        return self.per_pot - self.field_per_pot

    @property
    def impact(self) -> Optional[float]:
        """L'écart avec eux ramené à toutes tes mains (bb/100) : ce que tu gagnes (+) ou perds (−) de plus qu'eux."""
        return None if self.gap is None or not self.hands else self.gap * 100 * self.mine.hands / self.hands

    @property
    def clear(self) -> bool:
        """L'écart sort de la marge du hasard (la tienne et la leur, à 95 %)."""
        if self.gap is None or self.mine.margin is None or self.field.margin is None:
            return False
        return abs(self.gap) > math.hypot(self.mine.margin, self.field.margin) / 100

    @property
    def reference(self) -> Optional[float]:
        return REFERENCES.get(self.label)


@dataclass
class Report:
    hands: int
    pots: int
    categories: list[Line]
    lines: dict[tuple, list[Line]]  # catégorie -> ses situations (positions)

    def ranked(self) -> list[Line]:
        """Les situations assez jouées (MIN_POTS), de celle où tu gagnes le plus par rapport au field à celle où tu
        perds le plus (impact) ; les catégories quand aucune situation n'est assez jouée."""
        found = [x for rows in self.lines.values() for x in rows if x.mine.hands >= MIN_POTS and x.impact is not None]
        if not found:
            found = [x for x in self.categories if x.mine.hands >= MIN_POTS and x.impact is not None]
        return sorted(found, key=lambda x: -x.impact)


def _rows(hands: Iterable[Hand], players: set[str]) -> dict[tuple, gains.Row]:
    """Les résultats de ces joueurs par (catégorie, positions) et par catégorie (positions : « »)."""
    out: dict[tuple, gains.Row] = {}
    for h in hands:
        pot = pot_of(h) if h.bb else None
        if pot is None:
            continue
        for player in (pot.oop, pot.ip):
            if player not in players:
                continue
            spot = spot_of(h, player)
            for key in ((spot.category, spot.pair), (spot.category, "")):
                row = out.get(key)
                if row is None:
                    row = out[key] = gains.Row(key[1], key[1])
                gains.add(row, h, player)
    return out


def build(hands: list[Hand], hero: str, regulars: Iterable[str]) -> Report:
    """Tes résultats par situation, face à ceux des réguliers de ton field (regulars : leurs noms) dans ces mains."""
    mine = gains.mine(hands, hero)
    rows = _rows(mine, {hero})
    field = _rows(hands, set(regulars) - {hero})
    total = len(mine)
    empty = lambda key: gains.Row(key[1], key[1])  # noqa: E731
    categories, lines = [], {}
    for category in CATEGORIES:
        key = (category, "")
        if key not in rows:
            continue
        categories.append(Line(category, "", rows[key], field.get(key) or empty(key), total))
        pairs = sorted((k for k in rows if k[0] == category and k[1]), key=lambda k: -rows[k].hands)
        lines[category] = [Line(category, k[1], rows[k], field.get(k) or empty(k), total) for k in pairs]
    return Report(total, sum(x.mine.hands for x in categories), categories, lines)
