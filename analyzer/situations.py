"""Tes résultats par situation : les pots joués à deux au flop après une ligne préflop simple (open, 3bet, 4bet, puis
call), rangés comme tes repères : type de pot (SRP, 3B : pot 3bet, 4B : pot 4bet), place après le flop (IP : en
position, OOP : hors de position), rôle (PFR : le dernier relanceur préflop, l'attaquant ; PFC : celui qui a payé, le
défenseur) et, pour les pots simples, les positions (« SRP IP PFR (IP vs BB) »), puis le détail par positions des deux
joueurs (« CO vs BB »), en heads-up comme aux tables à plusieurs.

Pour chacune : tes pots et ton winrate dans la situation (bb/100 : bb pour 100 pots de cette situation, en EV all-in,
le hasard des tapis retiré), avec sa marge à 95 %, face à ton repère (REFERENCES, aux tables à plusieurs) et aux
réguliers de ton field dans la même situation (leurs pots dans tes historiques, contre toi ou contre d'autres). L'écart,
multiplié par la fréquence de la situation, dit ce qu'elle te fait gagner ou perdre sur ton winrate (bb/100 de toutes
tes mains) : où tu es le plus fort et le plus faible."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from . import gains, ring_leaks
from .models import CALL, FOLD, RAISE, VOLUNTARY, Hand


@dataclass(frozen=True)
class Group:
    """Une situation : type de pot, place après le flop (None : l'une ou l'autre), rôle, et au besoin tes positions
    et celles de l'adversaire (vide : toutes)."""
    key: str
    label: str
    kind: str
    ip: Optional[bool]
    attacker: bool
    positions: tuple = ()
    versus: tuple = ()

    def matches(self, spot: "Spot") -> bool:
        return (spot.kind == self.kind and self.ip in (None, spot.ip) and spot.attacker == self.attacker
                and (not self.positions or spot.position in self.positions)
                and (not self.versus or spot.versus in self.versus))


GROUPS = (
    Group("srp_ip_pfr", "SRP IP PFR (IP vs BB)", "srp", True, True, versus=("BB",)),
    Group("srp_oop_pfr", "SRP OOP PFR (SB vs BB)", "srp", False, True, ("SB",), ("BB",)),
    Group("srp_oop_pfc", "SRP OOP PFC (BB vs IP)", "srp", False, False, ("BB",)),
    Group("srp_ip_pfc", "SRP IP PFC (BB vs SB)", "srp", True, False, ("BB",), ("SB",)),
    Group("3b_oop_pfc", "3B OOP PFC", "3bet", False, False),
    Group("3b_ip_pfc", "3B IP PFC", "3bet", True, False),
    Group("3b_ip_pfr", "3B IP PFR", "3bet", True, True),
    Group("3b_oop_pfr", "3B OOP PFR", "3bet", False, True),
    Group("4b_pfr", "4B PFR", "4bet", None, True),
    Group("4b_pfc", "4B PFC", "4bet", None, False),
)
# Les pots simples hors des positions des situations ci-dessus (le bouton contre la SB, le CO payé par le bouton…).
OTHERS = tuple(Group(f"srp_{'ip' if ip else 'oop'}_{'pfr' if pfr else 'pfc'}_autres",
                     f"SRP {'IP' if ip else 'OOP'} {'PFR' if pfr else 'PFC'} (autres positions)", "srp", ip, pfr)
               for ip, pfr in ((True, True), (False, True), (False, False), (True, False)))
ORDER = GROUPS[:4] + OTHERS + GROUPS[4:]
# Tes repères aux tables à plusieurs (« Wr moyen ») : le winrate dans la situation, en bb/100 (bb pour 100 pots).
RING_REFERENCES = {"srp_ip_pfr": 60, "srp_oop_pfr": 15, "srp_oop_pfc": -60, "srp_ip_pfc": -45, "3b_oop_pfc": -160,
                   "3b_ip_pfc": -115, "3b_ip_pfr": 250, "3b_oop_pfr": 150, "4b_pfr": 450, "4b_pfc": -360}
MIN_POTS = 20      # pots minimum (toi) pour classer une situation parmi tes plus fortes ou tes plus faibles
SMALL = 1000       # pots à deux en tout : en dessous, l'échantillon reste petit


@dataclass(frozen=True)
class Spot:
    """La situation d'un joueur dans un pot à deux au flop."""
    kind: str        # srp, 3bet, 4bet
    ip: bool         # en position après le flop
    attacker: bool   # le dernier relanceur préflop (PFR)
    position: str
    versus: str

    @property
    def group(self) -> Group:
        return next(g for g in GROUPS + OTHERS if g.matches(self))

    @property
    def label(self) -> str:
        return self.group.label

    @property
    def pair(self) -> str:
        return f"{self.position} vs {self.versus}"


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
    """Une situation (ou, pair rempli, ses positions) : tes résultats, ton repère, ceux des réguliers de ton field."""
    group: Group
    pair: str
    mine: gains.Row
    field: gains.Row
    hands: int                         # toutes tes mains (pour ramener l'écart à 100 mains)
    reference: Optional[float] = None  # ton repère de la situation (bb/100 dans la situation)

    @property
    def label(self) -> str:
        return self.group.label + (f" · {self.pair}" if self.pair else "")

    @property
    def winrate(self) -> Optional[float]:
        """Ton winrate dans la situation : bb pour 100 pots, en EV all-in."""
        return 100 * self.mine.ev_bb / self.mine.hands if self.mine.hands else None

    @property
    def field_winrate(self) -> Optional[float]:
        return 100 * self.field.ev_bb / self.field.hands if self.field.hands else None

    @property
    def margin(self) -> Optional[float]:
        """La marge du hasard à 95 % sur ton winrate dans la situation (bb/100)."""
        return self.mine.margin

    @property
    def base(self) -> Optional[float]:
        """Ce à quoi ton winrate se compare : ton repère, sinon les réguliers du field."""
        return self.reference if self.reference is not None else self.field_winrate

    @property
    def gap(self) -> Optional[float]:
        """Ton winrate dans la situation moins le repère (ou celui des réguliers du field), en bb/100."""
        return None if self.winrate is None or self.base is None else self.winrate - self.base

    @property
    def impact(self) -> Optional[float]:
        """L'écart ramené à toutes tes mains (bb/100) : ce que la situation te fait gagner (+) ou perdre (−) sur ton
        winrate par rapport au repère."""
        return None if self.gap is None or not self.hands else self.gap * self.mine.hands / self.hands

    @property
    def weight(self) -> Optional[float]:
        """Ce que la situation apporte à ton winrate : ton résultat (EV all-in) pour 100 de toutes tes mains."""
        return 100 * self.mine.ev_bb / self.hands if self.hands else None

    @property
    def clear(self) -> bool:
        """L'écart sort de la marge du hasard à 95 % (la tienne, et celle du field sans repère)."""
        if self.gap is None or self.mine.margin is None:
            return False
        if self.reference is not None:
            return abs(self.gap) > self.mine.margin
        return self.field.margin is not None and abs(self.gap) > math.hypot(self.mine.margin, self.field.margin)


@dataclass
class Report:
    hands: int
    pots: int
    groups: list[Line]
    lines: dict[str, list[Line]]  # situation (Group.key) -> ses positions
    references: bool = False      # avec tes repères

    def ranked(self) -> list[Line]:
        """Les situations assez jouées (MIN_POTS), de celle qui te fait gagner le plus par rapport au repère (ou au
        field) à celle qui te fait perdre le plus (impact)."""
        found = [x for x in self.groups if x.mine.hands >= MIN_POTS and x.impact is not None]
        return sorted(found, key=lambda x: -x.impact)


def _rows(hands: Iterable[Hand], players: set[str]) -> dict[tuple, gains.Row]:
    """Les résultats de ces joueurs par (situation, positions) et par situation (positions : « »)."""
    out: dict[tuple, gains.Row] = {}
    for h in hands:
        pot = pot_of(h) if h.bb else None
        if pot is None:
            continue
        for player in (pot.oop, pot.ip):
            if player not in players:
                continue
            spot = spot_of(h, player)
            for key in ((spot.group.key, spot.pair), (spot.group.key, "")):
                row = out.get(key)
                if row is None:
                    row = out[key] = gains.Row(key[1], key[1])
                gains.add(row, h, player)
    return out


def build(hands: list[Hand], hero: str, regulars: Iterable[str],
          references: Optional[dict[str, float]] = None) -> Report:
    """Tes résultats par situation, face à tes repères (references : Group.key -> bb/100 dans la situation) et à ceux
    des réguliers de ton field (regulars : leurs noms) dans ces mains."""
    references = references or {}
    mine = gains.mine(hands, hero)
    rows = _rows(mine, {hero})
    field = _rows(hands, set(regulars) - {hero})
    total = len(mine)
    empty = lambda key: gains.Row(key[1], key[1])  # noqa: E731
    groups, lines = [], {}
    for group in ORDER:
        key = (group.key, "")
        if key not in rows:
            continue
        ref = references.get(group.key)
        groups.append(Line(group, "", rows[key], field.get(key) or empty(key), total, ref))
        pairs = sorted((k for k in rows if k[0] == group.key and k[1]), key=lambda k: -rows[k].hands)
        lines[group.key] = [Line(group, k[1], rows[k], field.get(k) or empty(k), total, ref) for k in pairs]
    return Report(total, sum(x.mine.hands for x in groups), groups, lines, bool(references))
