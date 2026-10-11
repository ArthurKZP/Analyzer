"""Préflop aux tables à plusieurs (3 à 9 joueurs, ensemble) face aux charts : tes décisions rangées en nœuds, comme
celles du heads-up face à la solution (theory/preflop.py), pour la même page (theory/page.py).

Un nœud par situation et positions, dans les charts qui jugent la décision (ring_ranges.chart_mapping : ceux de la
table, sinon ceux du 6-max à même nombre de joueurs derrière, le LJ d'une table de 7 à 9 joueurs comme l'UTG) :

  open      premier à parler (personne n'est entré) : open ou fold (un limp est un écart) ;
  vs_open   face à une ouverture : 3bet, call ou fold ;
  vs_3bet   ton open relancé : 4bet, call ou fold.

La stratégie d'une main à un nœud vient des ranges des lignes des charts, comme pour les mains de départ
(handplay.Theory) ; la part d'une main qui arrive au nœud : toute la main à l'open et face à une ouverture, sa
fréquence d'open face au 3bet. Les places sans chart (UTG à UTG+2 à 7-9 joueurs) et les situations que les charts
ne couvrent pas (après un limp, squeeze, face au 4bet) ne sont pas comparées.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import handplay
from ..cards import combo_notation
from ..models import CALL, RAISE, VOLUNTARY, Hand
from . import ring_ranges
from .extract import combos, hand_at
from .preflop import Decision, Node, Solution

SITUATIONS = (("open", "Premier à parler"), ("vs_open", "Face à une ouverture"),
              ("vs_3bet", "Face au 3bet, après ton open"))
WORDS = {
    "open": {"raise": "open", "call": "limp", "fold": "fold"},
    "vs_open": {"raise": "3bet", "call": "call", "fold": "fold"},
    "vs_3bet": {"raise": "4bet", "call": "call", "fold": "fold"},
}
HANDS = [hand_at(i, j) for i in range(13) for j in range(13)]


@dataclass
class RingNode(Node):
    """Un nœud des charts : situation, format des charts, positions (la sienne, puis celle de l'ouvreur ou du
    3bettor)."""
    situation: str = ""
    chart: str = ""
    seen: dict = field(default_factory=dict)  # (format de la table, sa position) -> décisions

    def word(self, action: str) -> str:
        return WORDS.get(self.situation, {}).get(action, action)


def node_key(situation: str, chart: str, me: str, other: str = "") -> str:
    return "|".join(x for x in (situation, chart, me, other) if x)


def _of(position: str) -> str:
    """« du CO », « de l'UTG », « de la BB »."""
    if position in ("SB", "BB"):
        return f"de la {position}"
    return f"de l'{position}" if position.startswith("U") else f"du {position}"


def _label(situation: str, chart: str, me: str, other: str) -> str:
    text = (f"{me}, premier à parler" if situation == "open" else
            f"{me} face à l'open {_of(other)}" if situation == "vs_open" else f"{me} face au 3bet {_of(other)}")
    return text if chart == "6-max" else f"{text} ({chart})"


def _strategies(situation: str, lines: dict, me: str, other: str) -> dict[str, dict[str, float]]:
    """La stratégie des charts pour chaque main qui arrive au nœud (handplay._chart_*)."""
    out = {}
    for hand in HANDS:
        if situation == "open":
            found = handplay._chart_open(lines, me, hand)
        elif situation == "vs_open":
            found = handplay._chart_vs_open(lines, other, me, hand)
        else:
            found = handplay._chart_vs_3bet(lines, me, other, hand)
        if found and any(v > 0 for v in found.values()):
            out[hand] = {a: round(v, 4) for a, v in found.items()}
    return out


def build_node(situation: str, chart: str, lines: dict, me: str, other: str = "") -> Optional[RingNode]:
    """Le nœud, ou None si les charts ne couvrent pas la situation."""
    hands = _strategies(situation, lines, me, other)
    if not hands:
        return None
    parent = (node_key("open", chart, me), "raise") if situation == "vs_3bet" else None
    return RingNode(node_key(situation, chart, me, other), _label(situation, chart, me, other), "", 0.0, parent, {}, {},
                    hands, situation=situation, chart=chart)


def _totals(node: RingNode, solution: Solution) -> dict[str, float]:
    """La fréquence (%) de chaque action sur toute la range qui arrive au nœud."""
    sums: dict[str, float] = {}
    reach = 0.0
    for hand in HANDS:
        weight = combos(hand) * solution.weight(node.key, hand)
        strategy = node.strategy(hand)
        if not weight or not strategy:
            continue
        reach += weight
        for action, freq in strategy.items():
            sums[action] = sums.get(action, 0.0) + weight * freq
    return {a: round(100 * v / reach, 1) for a, v in sums.items()} if reach else {}


@dataclass
class RingPreflop:
    """Tes décisions préflop comparées aux charts, et l'arbre des nœuds (une Solution, pour la page)."""
    solution: Solution
    decisions: list[Decision]
    hands: int        # mains lues (cartes connues)
    charts: list[str]  # formats des charts présents


def _effective(hand: Hand, hero: str) -> float:
    """Le tapis effectif du joueur : le sien, ou le plus gros des autres s'il est plus petit."""
    others = [s.stack for name, s in hand.seats.items() if name != hero]
    return min(hand.seats[hero].stack, max(others)) if others else hand.seats[hero].stack


def collect(hands: list[Hand], hero: str, lines: Optional[dict[str, dict]] = None) -> RingPreflop:
    """Tes décisions préflop aux tables à plusieurs, rangées dans les nœuds des charts (lines : format -> ligne ->
    {position: range}, handplay.ring_lines)."""
    lines = handplay.ring_lines() if lines is None else lines
    nodes: dict[str, Optional[RingNode]] = {}

    def node_for(situation: str, chart: str, me: str, other: str = "") -> Optional[RingNode]:
        key = node_key(situation, chart, me, other)
        if key not in nodes:
            nodes[key] = build_node(situation, chart, lines[chart], me, other)
            if situation == "vs_3bet" and nodes[key] is not None:
                node_for("open", chart, me)  # le parent : la part de chaque main qui ouvre
        return nodes[key]

    out, read = [], 0
    for h in hands:
        cards = h.hole_cards.get(hero, [])
        if len(h.seats) <= 2 or len(cards) != 2 or hero not in h.seats or not h.bb or not h.button:
            continue
        found = ring_ranges.chart_mapping(h.table_format, lines)
        if found is None:
            continue
        read += 1
        chart, mapping = found
        at = (lambda pos: mapping.get(pos)) if mapping else (lambda pos: pos)  # noqa: E731
        combo = combo_notation(cards)
        effective = _effective(h, hero)
        raises, limpers, raisers = 0, 0, []
        for a in h.actions:
            if a.street != "preflop" or a.kind not in VOLUNTARY:
                continue
            if a.player == hero:
                situation = handplay._situation(raises, limpers, raisers, hero)
                me = at(h.position(hero))
                other = (at(h.position(raisers[0])) if situation == "vs_open" else
                         at(h.position(raisers[1])) if situation == "vs_3bet" else "")
                action = handplay._action(a, effective)
                action = "raise" if action == "allin" else action  # les charts ne séparent pas le tapis
                if situation in WORDS and me and other is not None and action in ("raise", "call", "fold"):
                    node = node_for(situation, chart, me, other or "")
                    if node is not None:
                        out.append(Decision(h, hero, node.key, combo, action,
                                            round(a.to / h.bb, 2) if a.kind == RAISE else None,
                                            node.strategy(combo), effective / h.bb))
                        where = (h.table_format, h.position(hero))
                        node.seen[where] = node.seen.get(where, 0) + 1
            if a.kind == RAISE:
                raises += 1
                raisers.append(a.player)
            elif a.kind == CALL and raises == 0:
                limpers += 1
    built = {k: n for k, n in nodes.items() if n is not None}
    sources = [(ring_ranges.solution(fmt) or {}).get("source") or "" for fmt in lines]
    solution = Solution("Charts des tables à plusieurs", ", ".join(sorted(lines, key=_chart_order)),
                        next((s for s in sources if s), ""), 100.0, dict(sorted(built.items(), key=_node_order)))
    for node in built.values():
        node.totals = _totals(node, solution)
        if node.situation == "open" and any(d.node == node.key and d.action == "call" for d in out):
            node.totals["call"] = 0.0  # tes limps : une ligne de plus, à 0 % pour les charts
    return RingPreflop(solution, out, read, sorted(lines, key=_chart_order))


def _chart_order(fmt: str) -> tuple:
    return (fmt != "6-max", fmt)


def _node_order(item: tuple[str, RingNode]) -> tuple:
    node = item[1]
    parts = node.key.split("|")
    positions = [handplay.position_order(p) for p in parts[2:]]
    return ([s for s, _ in SITUATIONS].index(node.situation), _chart_order(node.chart), positions)
