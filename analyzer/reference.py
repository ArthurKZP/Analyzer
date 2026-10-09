"""Joueurs de référence : un bon joueur dont tu as importé les historiques (il y est le héros : toutes ses cartes sont
connues, pas seulement à l'abattage), étudié pour apprendre de lui et te comparer à lui.

- result, by_position : son résultat et le tien, en tout et par position (bb/100), avec et sans abattage ;
- compare_hu, compare_ring : vos fréquences côte à côte (heads-up : stats.analyze et les repères d'insights ; tables à
  plusieurs : field.ring_ratios et les repères d'un régulier solide), avec l'écart de chacune et s'il est net (test de
  deux proportions à 90 %, au moins MIN_OPPS occasions chacun) ;
- gaps : vos écarts nets, les plus marqués d'abord, en clair ;
- lines : ses mises et relances après le flop, par street, ligne et taille, avec ce qu'il avait (value, value fine,
  semi-bluff, bluff : field.intent_of) et la part qui a fait coucher tout le monde ; les tiennes à côté. Ses cartes sont
  toujours connues : ses lignes se lisent sans le biais de l'abattage (on ne voit pas seulement les mains payées).

Les résultats par position varient beaucoup d'un échantillon à l'autre (il faut des dizaines de milliers de mains pour
qu'un écart de bb/100 soit sûr) ; les fréquences se stabilisent bien plus vite : ce sont elles qui disent ce qu'il fait
autrement.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import field as field_study
from . import ring
from .field import Bet, required
from .insights import SECTIONS
from .models import FOLD, POSTFLOP, VOLUNTARY, Hand
from .stats import PlayerStats, Ratio, allin_ev

MIN_OPPS = 20      # occasions minimum de chacun pour juger un écart de fréquence
CLEAR_Z = 1.645    # écart net : test de deux proportions à 90 %
MIN_LINE = 5       # mises minimum (de l'un ou de l'autre) pour montrer une ligne
POSITION_MIN = 30  # mains minimum à une position pour la montrer

# Tables à plusieurs : les fréquences comparées, par section (field.ring_ratios), et les libellés qui manquent aux
# repères du field.
RING_SECTIONS = (
    ("Avant le flop", ("vpip", "pfr", "open", "limp", "threebet", "flat", "fold_3bet", "fold_steal", "threebet_steal")),
    ("Après le flop", ("cbet_hu", "cbet_multi", "cbet_turn", "cbet_river", "stab_flop", "xr_flop", "fold_cbet",
                       "fold_turn", "fold_river", "afq")),
    ("Abattage", ("wtsd", "wsd")),
)
RING_LABELS = {"open": "Open (premier à entrer)", "flat": "Call d'une ouverture", "cbet_multi": "C-bet flop (à plusieurs)",
               "cbet_river": "3e barrel", "wsd": "Gagné à l'abattage (W$SD)"}


# --- Résultats ----------------------------------------------------------------------------------------------------------

@dataclass
class Result:
    """Un résultat en grosses blindes : net, aux mains allées à l'abattage (le joueur y était) et aux autres, et l'EV
    des tapis payés avant la river."""
    hands: int = 0
    net_bb: float = 0.0
    sd_bb: float = 0.0
    nosd_bb: float = 0.0
    ev_bb: float = 0.0

    def _per100(self, value: float) -> Optional[float]:
        return 100.0 * value / self.hands if self.hands else None

    @property
    def bb100(self) -> Optional[float]:
        return self._per100(self.net_bb)

    @property
    def sd100(self) -> Optional[float]:
        return self._per100(self.sd_bb)

    @property
    def nosd100(self) -> Optional[float]:
        return self._per100(self.nosd_bb)

    @property
    def ev100(self) -> Optional[float]:
        return self._per100(self.ev_bb)

    def add(self, hand: Hand, name: str) -> None:
        net_bb = hand.net(name) / hand.bb
        folded = any(a.player == name and a.kind == FOLD for a in hand.actions)
        ev = allin_ev(hand)
        self.hands += 1
        self.net_bb += net_bb
        if hand.showdown and not folded:
            self.sd_bb += net_bb
        else:
            self.nosd_bb += net_bb
        self.ev_bb += ev[name] / hand.bb if ev is not None and name in ev else net_bb


def result(hands: Iterable[Hand], name: str) -> Result:
    out = Result()
    for h in hands:
        if name in h.seats and h.bb:
            out.add(h, name)
    return out


def by_position(hands: Iterable[Hand], name: str) -> dict[str, Result]:
    out: dict[str, Result] = {}
    for h in hands:
        if name in h.seats and h.bb:
            out.setdefault(h.position(name) or "?", Result()).add(h, name)
    return out


# --- Fréquences côte à côte ----------------------------------------------------------------------------------------------

@dataclass
class Row:
    key: str
    label: str
    section: str
    mine: Ratio
    his: Ratio
    ref: Optional[tuple[float, float]] = None  # repère d'un régulier solide

    @property
    def comparable(self) -> bool:
        return self.mine.opps >= MIN_OPPS and self.his.opps >= MIN_OPPS

    @property
    def gap(self) -> Optional[float]:
        """Son écart à toi, en points de pourcentage."""
        if not self.mine.opps or not self.his.opps:
            return None
        return self.his.pct - self.mine.pct

    @property
    def z(self) -> float:
        """Test de deux proportions (variance commune) : l'écart en nombre d'écarts-types."""
        n1, n2 = self.mine.opps, self.his.opps
        if not n1 or not n2:
            return 0.0
        p = (self.mine.hits + self.his.hits) / (n1 + n2)
        spread = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
        return (self.his.hits / n2 - self.mine.hits / n1) / spread if spread else 0.0

    @property
    def clear(self) -> bool:
        return self.comparable and abs(self.z) >= CLEAR_Z

    def closer(self) -> Optional[str]:
        """Qui est le plus près du repère : « lui », « toi », ou None (pas de repère, ou autant l'un que l'autre)."""
        if self.ref is None or not self.comparable:
            return None
        mine, his = _distance(self.mine.pct, self.ref), _distance(self.his.pct, self.ref)
        if abs(mine - his) < 1:
            return None
        return "lui" if his < mine else "toi"


def _distance(pct: float, ref: tuple[float, float]) -> float:
    lo, hi = ref
    return lo - pct if pct < lo else pct - hi if pct > hi else 0.0


def compare_hu(mine: Optional[PlayerStats], his: Optional[PlayerStats]) -> list[Row]:
    """Vos stats heads-up (stats.analyze), section par section, avec les repères d'insights."""
    out = []
    for section, defs in SECTIONS:
        for d in defs:
            out.append(Row(d.key, d.label, section, mine.r(d.key) if mine else Ratio(), his.r(d.key) if his else Ratio(),
                           d.ref))
    return out


def compare_ring(mine: dict[str, Ratio], his: dict[str, Ratio]) -> list[Row]:
    """Vos fréquences aux tables à plusieurs (field.ring_ratios), avec les repères d'un régulier solide."""
    out = []
    for section, keys in RING_SECTIONS:
        for key in keys:
            d = field_study.RING_DEF.get(key)
            label = RING_LABELS.get(key) or (d.label if d else ring.LABEL.get(key, key))
            ref = d.ref if d else ring.REF_6MAX.get(key)
            out.append(Row(key, label, section, mine.get(key, Ratio()), his.get(key, Ratio()), ref))
    return out


def gaps(rows: list[Row], top: int = 8) -> list[Row]:
    """Vos écarts nets, les plus marqués d'abord."""
    return sorted((r for r in rows if r.clear), key=lambda r: -abs(r.z))[:top]


# --- Ses lignes de value et de bluff --------------------------------------------------------------------------------

def passed(bet: Bet) -> bool:
    """La mise a fait coucher tout le monde (personne n'a payé ni relancé ensuite à cette street)."""
    after = [a for a in bet.hand.actions[bet.index + 1:] if a.street == bet.street and a.kind in VOLUNTARY]
    return bool(after) and all(a.kind == FOLD for a in after)


@dataclass
class LineStats:
    """Les mises d'un joueur dans une ligne (street, ligne, taille)."""
    bets: list[Bet] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.bets)

    @property
    def known(self) -> list[Bet]:
        return [b for b in self.bets if b.intent]

    @property
    def intents(self) -> Counter:
        return Counter(b.intent for b in self.known)

    def share(self, *intents: str) -> Optional[float]:
        """Part de ces intentions parmi ses mises dont les cartes sont connues (en %)."""
        n = len(self.known)
        return 100.0 * sum(self.intents[i] for i in intents) / n if n else None

    @property
    def value(self) -> Optional[float]:
        return self.share("value", "thin")

    @property
    def bluff(self) -> Optional[float]:
        return self.share("semi", "bluff")

    @property
    def passes(self) -> Optional[float]:
        """Part de ses mises qui ont fait coucher tout le monde (en %)."""
        return 100.0 * sum(passed(b) for b in self.bets) / len(self.bets) if self.bets else None

    @property
    def median_pct(self) -> Optional[float]:
        pcts = sorted(b.pct for b in self.bets if b.pct is not None)
        return pcts[len(pcts) // 2] if pcts else None

    @property
    def balance(self) -> Optional[float]:
        """À la river, la part de bluffs d'une range équilibrée pour sa taille médiane (l'équité qu'il faut pour payer)."""
        return required(self.median_pct)


@dataclass
class LineRow:
    street: str
    label: str
    size: str
    his: LineStats
    mine: LineStats

    @property
    def name(self) -> str:
        return " · ".join(part for part in (self.label, self.size) if part)


def lines(his_hands: Iterable[Hand], him: str, my_hands: Iterable[Hand], me: str) -> list[LineRow]:
    """Ses lignes après le flop et les tiennes, réunies par street, ligne et taille (au moins MIN_LINE mises de l'un ou
    de l'autre), ses plus fréquentes d'abord à chaque street."""
    rows: dict[tuple, LineRow] = {}
    for who, bets in (("his", field_study.player_bets(his_hands, [him], all_cards=True)),
                      ("mine", field_study.player_bets(my_hands, [me], all_cards=True))):
        for b in bets:
            key = (b.street, b.label, b.size)
            row = rows.get(key)
            if row is None:
                row = rows[key] = LineRow(b.street, b.label, b.size, LineStats(), LineStats())
            getattr(row, who).bets.append(b)
    order = {s: k for k, s in enumerate(POSTFLOP)}
    kept = [r for r in rows.values() if max(r.his.count, r.mine.count) >= MIN_LINE]
    return sorted(kept, key=lambda r: (order[r.street], -r.his.count, -r.mine.count, r.name))


def street_totals(rows: list[LineRow]) -> dict[str, tuple[LineStats, LineStats]]:
    """Toutes lignes confondues, par street : (les siennes, les tiennes)."""
    out: dict[str, tuple[LineStats, LineStats]] = {}
    for r in rows:
        his, mine = out.setdefault(r.street, (LineStats(), LineStats()))
        his.bets.extend(r.his.bets)
        mine.bets.extend(r.mine.bets)
    return out


def examples(rows: list[LineRow], intents: tuple[str, ...], limit: int = 12) -> list[Bet]:
    """Ses mises de ces intentions (ex. ses bluffs), les plus récentes d'abord."""
    bets = [b for r in rows for b in r.his.bets if b.intent in intents]
    bets.sort(key=lambda b: (b.hand.date, b.hand.hand_id), reverse=True)
    return bets[:limit]
