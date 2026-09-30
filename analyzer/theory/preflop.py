"""Comparaison des décisions préflop à une solution de référence (solveur HU).

La solution est un arbre de nœuds (open du bouton, BB face à l'open, bouton face au 3bet,
BB face au 4bet). Pour chaque nœud et chaque main, elle donne la fréquence de chaque action.
Chaque décision réelle est rangée dans son nœud et jugée selon la fréquence que le solveur
donne à l'action choisie avec cette main.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Optional

from ..cards import combo_notation
from ..models import CALL, FOLD, RAISE, VOLUNTARY, Hand

DEFAULT_SOLUTION = Path(__file__).parent / "data" / "hu_100bb.json"
ACTION_ORDER = ("allin", "raise", "call", "fold")
ACTION_WORDS = {
    "sb_open": {"raise": "open", "call": "limp", "fold": "fold"},
    "bb_vs_open": {"allin": "tapis", "raise": "3bet", "call": "call", "fold": "fold"},
    "sb_vs_3bet": {"allin": "tapis", "raise": "4bet", "call": "call", "fold": "fold"},
    "bb_vs_4bet": {"allin": "tapis", "call": "call", "fold": "fold"},
}
MAIN, MIXED, DEVIATION, OUT_OF_RANGE = "principale", "secondaire", "écart", "hors range"
MIXED_THRESHOLD, MAIN_THRESHOLD = 0.10, 0.50
ALLIN_SHARE = 0.6  # une relance qui engage plus de 60 % du tapis effectif compte comme un tapis


@dataclass
class Node:
    key: str
    label: str
    player: str  # "sb" ou "bb"
    facing_bb: float
    parent: Optional[tuple[str, str]]
    sizes: dict[str, float]
    totals: dict[str, float]
    hands: dict[str, dict[str, float]]

    @property
    def actions(self) -> list[str]:
        present = {a for freqs in self.hands.values() for a in freqs} | set(self.totals)
        return [a for a in ACTION_ORDER if a in present]

    def word(self, action: str) -> str:
        return ACTION_WORDS.get(self.key, {}).get(action, action)

    def strategy(self, hand: str) -> Optional[dict[str, float]]:
        """Fréquences normalisées du solveur pour cette main, ou None si elle n'arrive jamais ici."""
        freqs = self.hands.get(hand)
        if not freqs:
            return None
        total = sum(freqs.values())
        return {a: freqs[a] / total for a in ACTION_ORDER if freqs.get(a)}

    def total(self, action: str) -> float:
        """Fréquence globale (%) affichée par le solveur ; « raise » inclut le tapis pour les stats."""
        return self.totals.get(action, 0.0)


@dataclass
class Solution:
    name: str
    description: str
    source: str
    stack_bb: float
    nodes: dict[str, Node]

    def weight(self, node_key: str, hand: str) -> float:
        """Part de la main qui arrive au nœud (produit des fréquences le long de l'arbre)."""
        node = self.nodes[node_key]
        if node.parent is None:
            return 1.0
        parent_key, action = node.parent
        freqs = self.nodes[parent_key].strategy(hand) or {}
        return self.weight(parent_key, hand) * freqs.get(action, 0.0)

    def aggressive_total(self, node_key: str) -> float:
        node = self.nodes[node_key]
        return node.total("raise") + node.total("allin")


@lru_cache(maxsize=8)
def load_solution(path: Optional[str] = None) -> Solution:
    data = json.loads(Path(path or DEFAULT_SOLUTION).read_text(encoding="utf-8"))
    nodes = {
        key: Node(
            key=key,
            label=n["label"],
            player=n["player"],
            facing_bb=n["facing_bb"],
            parent=tuple(n["parent"]) if n.get("parent") else None,
            sizes=n.get("sizes", {}),
            totals=n.get("totals", {}),
            hands=n.get("hands", {}),
        )
        for key, n in data["nodes"].items()
    }
    return Solution(data["name"], data.get("description", ""), data.get("source", ""), data["stack_bb"], nodes)


def gto_value(node_key: str, action: str) -> float:
    """Fréquence globale (%) du solveur ; « raise » inclut le tapis, « vpip » = relances + calls."""
    solution = load_solution()
    if action == "raise":
        return solution.aggressive_total(node_key)
    if action == "vpip":
        return solution.aggressive_total(node_key) + solution.nodes[node_key].total("call")
    return solution.nodes[node_key].total(action)


def gto_ref(node_key: str, action: str, tolerance: Optional[float] = None) -> tuple[float, float]:
    """Plage de référence (%) autour de la fréquence globale du solveur."""
    value = gto_value(node_key, action)
    tol = tolerance if tolerance is not None else (5.0 if value >= 15 else 3.0)
    return max(0.0, round(value - tol, 1)), min(100.0, round(value + tol, 1))


# --- Décisions -------------------------------------------------------------------------

@dataclass
class Decision:
    hand: Hand
    player: str
    node: str
    combo: str
    action: str
    size_bb: Optional[float]
    strategy: Optional[dict[str, float]]
    effective_bb: float

    @property
    def frequency(self) -> Optional[float]:
        return None if self.strategy is None else self.strategy.get(self.action, 0.0)

    @property
    def best(self) -> Optional[str]:
        return max(self.strategy, key=self.strategy.get) if self.strategy else None

    @property
    def verdict(self) -> str:
        f = self.frequency
        if f is None:
            return OUT_OF_RANGE
        if f >= MAIN_THRESHOLD:
            return MAIN
        return MIXED if f >= MIXED_THRESHOLD else DEVIATION


def _node_for(raises: int, is_sb: bool, first: bool, sb_opened: bool) -> Optional[str]:
    if raises == 0 and is_sb and first:
        return "sb_open"
    if not sb_opened:
        return None  # pots limpés et iso-raises : hors de l'arbre de référence
    if raises == 1 and not is_sb:
        return "bb_vs_open"
    if raises == 2 and is_sb:
        return "sb_vs_3bet"
    if raises == 3 and not is_sb:
        return "bb_vs_4bet"
    return None


def decisions(hands: list[Hand], player: str, solution: Optional[Solution] = None) -> list[Decision]:
    """Décisions préflop du joueur rangées dans l'arbre de la solution (cartes connues uniquement)."""
    solution = solution or load_solution()
    out = []
    for h in hands:
        cards = h.hole_cards.get(player, [])
        if len(cards) != 2 or player not in h.seats or not h.button or not h.bb:
            continue
        combo = combo_notation(cards)
        effective = h.effective_stack()
        raises, sb_opened, acted = 0, False, set()
        for a in h.actions:
            if a.street != "preflop" or a.kind not in VOLUNTARY:
                continue
            is_sb = a.player == h.button
            node_key = _node_for(raises, is_sb, a.player not in acted, sb_opened)
            if a.player == player and node_key in solution.nodes:
                action = _action(a, node_key, effective)
                if action:
                    node = solution.nodes[node_key]
                    out.append(Decision(h, player, node_key, combo, action,
                                        round(a.to / h.bb, 2) if a.kind == RAISE else None,
                                        node.strategy(combo), effective / h.bb))
            if a.kind == RAISE:
                raises += 1
                if raises == 1 and is_sb:
                    sb_opened = True
            acted.add(a.player)
    return out


def _action(a, node_key: str, effective: float) -> Optional[str]:
    if a.kind == FOLD:
        return "fold"
    if a.kind == CALL:
        return "call"
    if a.kind == RAISE:
        if node_key == "bb_vs_4bet" or a.all_in or (effective and a.to >= ALLIN_SHARE * effective):
            return "allin" if node_key != "sb_open" else "raise"
        return "raise"
    return None  # check


# --- Synthèse par nœud ---------------------------------------------------------------------

@dataclass
class NodeSummary:
    node: Node
    decisions: list[Decision] = field(default_factory=list)

    @property
    def in_range(self) -> list[Decision]:
        return [d for d in self.decisions if d.strategy is not None]

    @property
    def actual(self) -> dict[str, float]:
        """Ta fréquence (%) de chaque action, sur les mains que le solveur joue à ce nœud."""
        ds = self.in_range
        counts = Counter(d.action for d in ds)
        return {a: 100 * counts[a] / len(ds) for a in self.node.actions} if ds else {}

    @property
    def expected(self) -> dict[str, float]:
        """Ce qu'aurait fait le solveur avec exactement les mêmes mains (%)."""
        ds = self.in_range
        if not ds:
            return {}
        return {a: 100 * sum(d.strategy.get(a, 0.0) for d in ds) / len(ds) for a in self.node.actions}

    @property
    def verdicts(self) -> Counter:
        return Counter(d.verdict for d in self.decisions)

    def deviations(self) -> list[tuple[tuple[str, str], list[Decision]]]:
        """Écarts groupés par (action jouée, action du solveur), du plus fréquent au plus rare."""
        groups: dict[tuple[str, str], list[Decision]] = defaultdict(list)
        for d in self.decisions:
            if d.verdict == DEVIATION:
                groups[(d.action, d.best)].append(d)
        return sorted(groups.items(), key=lambda kv: -len(kv[1]))

    def sizes(self) -> Optional[float]:
        values = [d.size_bb for d in self.decisions if d.size_bb and d.action == "raise"]
        return median(values) if values else None

    def by_hand(self) -> dict[str, list[Decision]]:
        out: dict[str, list[Decision]] = defaultdict(list)
        for d in self.decisions:
            out[d.combo].append(d)
        return out


def summarize(decisions_list: list[Decision], solution: Optional[Solution] = None) -> list[NodeSummary]:
    solution = solution or load_solution()
    summaries = {key: NodeSummary(node) for key, node in solution.nodes.items()}
    for d in decisions_list:
        summaries[d.node].decisions.append(d)
    return list(summaries.values())


# Stats de fréquence (PlayerStats) correspondant à chaque action d'un nœud.
RANGE_STATS = {
    "sb_open": {"raise": "sb_first.raise", "call": "sb_first.call", "fold": "sb_first.fold"},
    "bb_vs_open": {"raise": "bb_vs_open.raise", "call": "bb_vs_open.call", "fold": "bb_vs_open.fold"},
    "sb_vs_3bet": {"raise": "sb_vs_3bet.raise", "call": "sb_vs_3bet.call", "fold": "sb_vs_3bet.fold"},
    "bb_vs_4bet": {"raise": "bb_vs_4bet.raise", "call": "bb_vs_4bet.call", "fold": "bb_vs_4bet.fold"},
}
