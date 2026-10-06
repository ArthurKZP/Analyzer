"""Ce que rapporte chaque main de départ : en tout, par position, puis à chaque décision préflop comparée au fold
et à la théorie.

Pour chaque main où tes cartes sont connues : son résultat (en bb, et en EV all-in quand un tapis payé avant la
river a été montré, pour ôter la chance), ta position, et chacune de tes décisions préflop :

  open     tu parles le premier (personne n'est entré) : relancer, limper ou folder ;
  vs_limp  quelqu'un a limpé avant toi ;
  vs_open  face à une ouverture : payer, relancer (3bet) ou folder ;
  vs_3bet  ton open a été relancé ;
  vs_4bet  ton 3bet a été relancé.

Le fold d'une décision coûte ce que tu as déjà mis au pot (la SB 0,5 bb, la BB 1 bb, ton open face au 3bet…) :
jouer la main est rentable si elle rapporte en moyenne plus que ce coût (mieux que -100 bb/100 pour défendre la
BB, que -250 bb/100 pour payer un 3bet après un open à 2,5 bb). Le résultat d'une décision est celui de toute la
main qui suit.

Théorie : en heads-up, la fréquence de la solution préflop (theory/preflop.py) ; à une table à plusieurs, celle de
tes charts (ring_ranges), retrouvée à partir des ranges de leurs lignes.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .cards import combo_notation
from .models import CALL, CHECK, FOLD, RAISE, VOLUNTARY, Hand
from .stats import allin_ev

SITUATIONS = (("open", "Premier à parler"), ("vs_limp", "Après un limp"), ("vs_open", "Face à une ouverture"),
              ("vs_3bet", "Face au 3bet"), ("vs_4bet", "Face au 4bet"))
ACTIONS = ("raise", "allin", "call", "check", "fold")
ALLIN_SHARE = 0.6  # une relance qui engage plus de 60 % du tapis effectif compte comme un tapis
HU_NODES = {"open": "sb_open", "vs_open": "bb_vs_open", "vs_3bet": "sb_vs_3bet", "vs_4bet": "bb_vs_4bet"}


@dataclass
class Decision:
    situation: str
    action: str
    fold_bb: Optional[float]  # résultat d'un fold à ce moment (-ce que tu as déjà mis) ; None : pas de fold possible
    theory: Optional[dict[str, float]] = None  # fréquence de chaque action selon la théorie, pour ta main


@dataclass
class Played:
    hand: Hand
    combo: str  # classe de la main : AKs, 72o…
    position: str
    net_bb: float
    ev_bb: float  # résultat EV all-in (le réel sans tapis montré)
    opponent: Optional[str] = None  # en heads-up, l'adversaire
    decisions: list[Decision] = field(default_factory=list)


def _action(a, effective: float) -> Optional[str]:
    if a.kind == FOLD:
        return "fold"
    if a.kind == CALL:
        return "call"
    if a.kind == CHECK:
        return "check"
    if a.kind == RAISE:
        return "allin" if a.all_in or (effective and a.to >= ALLIN_SHARE * effective) else "raise"
    return None


def read(hand: Hand, hero: str, theory: Optional["Theory"] = None) -> Optional[Played]:
    """Ta main, son résultat et tes décisions préflop ; None sans tes cartes ou sans blindes connues."""
    cards = hand.hole_cards.get(hero, [])
    if len(cards) != 2 or hero not in hand.seats or not hand.bb or not hand.big_blind:
        return None
    bb = hand.bb
    ev = allin_ev(hand) if len(hand.seats) == 2 else None
    played = Played(hand, combo_notation(cards), hand.position(hero), round(hand.net(hero) / bb, 2),
                    round((ev[hero] if ev else hand.net(hero)) / bb, 2),
                    hand.opponent_of(hero) if len(hand.seats) == 2 else None)
    effective = hand.effective_stack()
    put = defaultdict(float)
    raises, limpers, raisers = 0, 0, []
    for a in hand.actions:
        if a.street != "preflop":
            continue
        if a.kind not in VOLUNTARY:  # blindes et antes
            put[a.player] += a.amount
            continue
        if a.player == hero:
            situation = _situation(raises, limpers, raisers, hero)
            action = _action(a, effective)
            if situation and action:
                fold = None if situation == "vs_limp" and hero == hand.big_blind and raises == 0 else \
                    round(-put[hero] / bb, 2) + 0.0
                played.decisions.append(Decision(situation, action, fold, theory.strategy(
                    hand, hero, situation, raisers, played.combo) if theory else None))
        if a.kind in (RAISE, CALL):
            put[a.player] = a.to  # ce qu'il a mis dans la street (blinde comprise)
        if a.kind == RAISE:
            raises += 1
            raisers.append(a.player)
        elif a.kind == CALL and raises == 0:
            limpers += 1
    return played


def _situation(raises: int, limpers: int, raisers: list[str], hero: str) -> Optional[str]:
    if raises == 0:
        return "vs_limp" if limpers else "open"
    if raises == 1 and raisers[0] != hero:
        return "vs_open"
    if raises == 2 and raisers[0] == hero:
        return "vs_3bet"
    if raises == 3 and raisers[1] == hero:
        return "vs_4bet"
    return None  # squeeze, cold 4bet… : hors des situations suivies


class Theory:
    """La fréquence de chaque action selon la théorie, pour une main à une décision préflop."""

    def __init__(self, heads_up_solution=None, ring_lines: Optional[dict[str, dict[str, dict[str, float]]]] = None):
        self.solution = heads_up_solution
        self.lines = ring_lines or {}  # format -> clé de ligne -> {position: range}

    def strategy(self, hand: Hand, hero: str, situation: str, raisers: list[str], combo: str
                 ) -> Optional[dict[str, float]]:
        if len(hand.seats) == 2:
            node = self.solution.nodes.get(HU_NODES.get(situation, "")) if self.solution else None
            if node is None or (situation != "open" and raisers and raisers[0] != hand.button):
                return None  # l'arbre de référence part de l'open du bouton
            return node.strategy(combo)
        lines = self.lines.get(hand.table_format)
        if not lines:
            return None
        me = hand.position(hero)
        if situation == "open":
            return _chart_open(lines, me, combo)
        if situation == "vs_open":
            return _chart_vs_open(lines, hand.position(raisers[0]), me, combo)
        if situation == "vs_3bet":
            return _chart_vs_3bet(lines, me, hand.position(raisers[1]), combo)
        return None  # face au 4bet : les charts ne donnent que les calls (pas les tapis)


def _range(lines: dict, key: str, position: str) -> Optional[dict[str, float]]:
    entry = lines.get(key)
    return entry.get(position) if entry else None


def _open_range(lines: dict, opener: str) -> Optional[dict[str, float]]:
    """L'ouverture d'une position : sa range dans un pot simple qu'elle a ouvert (le call ne la change pas)."""
    for key, ranges in lines.items():
        steps = key.split()
        if len(steps) == 2 and steps[0] == f"{opener}:raise" and steps[1].endswith(":call"):
            return ranges.get(opener)
    return None


def _chart_open(lines: dict, me: str, combo: str) -> Optional[dict[str, float]]:
    rng = _open_range(lines, me)
    if rng is None:
        return None
    w = rng.get(combo, 0.0)
    return {"raise": w, "fold": 1 - w}


def _chart_vs_open(lines: dict, opener: str, me: str, combo: str) -> Optional[dict[str, float]]:
    call = _range(lines, f"{opener}:raise {me}:call", me)
    raise_ = _range(lines, f"{opener}:raise {me}:raise {opener}:call", me)
    if call is None and raise_ is None:
        return None
    c, r = (call or {}).get(combo, 0.0), (raise_ or {}).get(combo, 0.0)
    return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}


def _chart_vs_3bet(lines: dict, me: str, threebettor: str, combo: str) -> Optional[dict[str, float]]:
    opened = (_open_range(lines, me) or {}).get(combo, 0.0)
    call = _range(lines, f"{me}:raise {threebettor}:raise {me}:call", me)
    four = _range(lines, f"{me}:raise {threebettor}:raise {me}:raise {threebettor}:call", me)
    if not opened or (call is None and four is None):
        return None
    c = min(1.0, (call or {}).get(combo, 0.0) / opened)  # dans la ligne, la range est celle qui ouvre ET paie
    r = min(1.0, (four or {}).get(combo, 0.0) / opened)
    return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}


def ring_lines(formats: tuple[str, ...] = ("6-max", "3-max")) -> dict[str, dict]:
    """Les ranges de tes charts (ring_ranges), ligne par ligne, pour chaque format présent."""
    from .theory import ring_ranges
    out = {}
    for table_format in formats:
        data = ring_ranges.solution(table_format)
        if data:
            out[table_format] = {key: {pos: ring_ranges.parse_range(text) for pos, text in entry["ranges"].items()}
                                 for key, entry in data["lines"].items() if isinstance(entry.get("ranges"), dict)}
    return out


def collect(hands: list[Hand], hero: str, theory: Optional[Theory] = None) -> list[Played]:
    out = []
    for hand in hands:
        played = read(hand, hero, theory)
        if played is not None:
            out.append(played)
    return out


def aggregate(plays: list[Played], kinds: Optional[dict[str, str]] = None) -> dict:
    """Sommes par (position, situation, action, type d'adversaire, main) pour la page : l'application y choisit ses
    filtres et calcule moyennes et intervalles. Situation « all » : chaque main une fois (son résultat)."""
    kinds = kinds or {}
    cells: dict[str, dict[str, list[float]]] = defaultdict(dict)

    def add(key: str, combo: str, net: float, ev: float, fold: Optional[float], freq: Optional[float]) -> None:
        cell = cells[key].setdefault(combo, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
        cell[0] += 1
        cell[1] += net
        cell[2] += net * net
        cell[3] += ev
        cell[4] += ev * ev
        cell[5] += fold or 0.0
        if freq is not None:
            cell[6] += freq
            cell[7] += 1

    for p in plays:
        kind = kinds.get(p.opponent or "", "") if p.opponent else ""
        add(f"{p.position}|all|all|{kind}", p.combo, p.net_bb, p.ev_bb, None, None)
        for d in p.decisions:
            freq = d.theory.get(d.action, 0.0) if d.theory is not None and d.action != "allin" else (
                (d.theory.get("allin", 0.0) + d.theory.get("raise", 0.0)) if d.theory is not None else None)
            add(f"{p.position}|{d.situation}|{d.action}|{kind}", p.combo, p.net_bb, p.ev_bb, d.fold_bb, freq)
    return {"cells": {k: {c: [round(x, 3) for x in v] for c, v in cell.items()} for k, cell in cells.items()},
            "positions": sorted({p.position for p in plays}, key=_position_order),
            "hands": len(plays)}


POSITION_ORDER = ("UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO", "BTN", "SB", "BB")


def _position_order(pos: str) -> int:
    return POSITION_ORDER.index(pos) if pos in POSITION_ORDER else len(POSITION_ORDER)


# --- Leaks : les mains jouées qui perdent nettement plus que le fold --------------------------------------------

WORDS = {"open": {"raise": "open", "allin": "tapis", "call": "limp"},
         "vs_limp": {"raise": "relance", "allin": "tapis", "check": "check", "call": "limp"},
         "vs_open": {"raise": "3bet", "allin": "tapis", "call": "call"},
         "vs_3bet": {"raise": "4bet", "allin": "tapis", "call": "call"},
         "vs_4bet": {"allin": "tapis", "call": "call", "raise": "5bet"}}
SITUATION_WORDS = {"open": "en premier", "vs_limp": "après un limp", "vs_open": "face à l'open",
                   "vs_3bet": "face au 3bet", "vs_4bet": "face au 4bet"}
MIN_LEAK = 15  # fois minimum pour un leak


def family(combo: str) -> str:
    """La famille d'une main (comme dans la page) : plus de mains par groupe, des verdicts plus sûrs."""
    ranks = "AKQJT98765432"
    hi, lo = combo[0], combo[1]
    suited = combo.endswith("s")
    if len(combo) == 2:
        return ("Paires hautes (TT+)" if ranks.index(hi) <= ranks.index("T") else
                "Paires moyennes (66-99)" if ranks.index(hi) <= ranks.index("6") else "Petites paires (22-55)")
    if hi == "A":
        return "As assortis" if suited else "As dépareillés"
    if ranks.index(lo) <= ranks.index("T"):
        return "Broadways assortis" if suited else "Broadways dépareillés"
    if suited and ranks.index(lo) - ranks.index(hi) <= 2:
        return "Connecteurs assortis"
    return "Autres assorties" if suited else "Autres dépareillées"


@dataclass
class Loser:
    """Une main (ou une famille) jouée d'une certaine façon qui rapporte nettement moins que le fold."""
    name: str
    situation: str
    position: str
    action: str
    n: int
    mean: float  # bb par main (EV all-in)
    fold: float  # bb du fold à ce moment
    half_width: float  # demi-intervalle à 95 %
    theory: Optional[float]  # fréquence théorique de cette action avec ces mains

    @property
    def gap(self) -> float:
        return self.mean - self.fold

    @property
    def label(self) -> str:
        word = WORDS.get(self.situation, {}).get(self.action, self.action)
        return f"{self.name} : {word} {SITUATION_WORDS.get(self.situation, '')} ({self.position})"


def losers(plays: list[Played], min_n: int = MIN_LEAK) -> list[Loser]:
    """Mains et familles jouées (hors fold) qui perdent plus que le fold, le hasard mis à part (intervalle à 95 %),
    des plus coûteuses aux moins coûteuses ; une main déjà dans une famille signalée n'est pas répétée."""
    groups: dict[tuple, list] = defaultdict(list)
    for p in plays:
        for d in p.decisions:
            if d.action == "fold" or d.fold_bb is None:
                continue
            freq = None if d.theory is None else (d.theory.get("allin", 0.0) + d.theory.get("raise", 0.0)
                                                   if d.action == "allin" else d.theory.get(d.action, 0.0))
            for name in (p.combo, family(p.combo)):
                groups[(name, d.situation, p.position, d.action)].append((p.ev_bb, d.fold_bb, freq))
    out = []
    for (name, situation, position, action), rows in groups.items():
        n = len(rows)
        if n < min_n:
            continue
        values = [r[0] for r in rows]
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        half = 1.96 * (var / n) ** 0.5
        fold = sum(r[1] for r in rows) / n
        freqs = [r[2] for r in rows if r[2] is not None]
        if mean - fold + half < 0:
            out.append(Loser(name, situation, position, action, n, mean, fold, half,
                             sum(freqs) / len(freqs) if freqs else None))
    out.sort(key=lambda x: x.gap * x.n)
    flagged = {(x.situation, x.position, x.action, x.name) for x in out}
    return [x for x in out if len(x.name) > 3 or (x.situation, x.position, x.action, family(x.name)) not in flagged]
