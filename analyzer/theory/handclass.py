"""Catégories de mains pour les filtres de l'explorateur : main faite et tirages, selon le board.

Chaque combo reçoit une main faite (une seule, la plus forte où ses cartes comptent) et un masque de
tirages (plusieurs possibles). Les règles suivent les filtres usuels des solveurs :
- les mains fortes (quinte et mieux) ne comptent que si les cartes du joueur améliorent le board ;
- set = paire servie + une carte du board ; trips = une carte + une paire du board ;
- overpair / underpair : paire servie au-dessus / en dessous de la plus haute carte du board ;
- top pair, seconde paire, paire faible : une carte du joueur avec la 1re, 2e ou une autre hauteur du board ;
- tirages (flop et turn) : tirage couleur à 4 cartes, quinte ouverte ou double ventrale (oesd),
  ventrale (gutshot), backdoor couleur avec les deux cartes au flop (bdfd).
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from ..cards import _straight_high, evaluate, parse_card

MADE = (
    ("sf", "Quinte flush"), ("quads", "Carré"), ("full", "Full"), ("flush", "Couleur"), ("straight", "Quinte"),
    ("set", "Set"), ("trips", "Trips"), ("twopair", "Deux paires"), ("overpair", "Overpair"),
    ("toppair", "Top pair"), ("underpair", "Underpair"), ("secondpair", "Seconde paire"),
    ("weakpair", "Paire faible"), ("acehigh", "Ace high"), ("kinghigh", "King high"),
    ("nothing", "Mains non faites"),
)
DRAWS = (("fd", "Tirage couleur"), ("oesd", "Oesd"), ("gutshot", "Gutshot"), ("bdfd", "BDFD 2 cartes"),
         ("nodraw", "Pas de draw"))
MADE_INDEX = {key: i for i, (key, _) in enumerate(MADE)}
DRAW_BIT = {key: 1 << i for i, (key, _) in enumerate(DRAWS)}
STRONG = {8: "sf", 7: "quads", 6: "full", 5: "flush", 4: "straight"}


def _made(hole: list[tuple[int, str]], board: list[tuple[int, str]], value: tuple) -> str:
    if value[0] >= 4 and value > evaluate(board):
        return STRONG[value[0]]
    h1, h2 = sorted((r for r, _ in hole), reverse=True)
    counts = Counter(r for r, _ in board)
    ranks = sorted(counts, reverse=True)
    if h1 == h2:
        if counts[h1] == 1:
            return "set"
        return "overpair" if h1 > ranks[0] else "underpair"
    if counts[h1] >= 2 or counts[h2] >= 2:
        return "trips"
    if counts[h1] and counts[h2]:
        return "twopair"
    paired = next((r for r in (h1, h2) if counts[r]), None)
    if paired is not None:
        place = ranks.index(paired)
        return "toppair" if place == 0 else "secondpair" if place == 1 else "weakpair"
    return "acehigh" if h1 == 14 else "kinghigh" if h1 == 13 else "nothing"


def _draws(hole: list[tuple[int, str]], board: list[tuple[int, str]], category: int) -> int:
    mask = 0
    cards = hole + board
    if category < 5:
        for suit in {s for _, s in hole}:
            if sum(1 for _, s in cards if s == suit) == 4:
                mask |= DRAW_BIT["fd"]
    if len(board) == 3 and hole[0][1] == hole[1][1] and sum(1 for _, s in board if s == hole[0][1]) == 1:
        mask |= DRAW_BIT["bdfd"]
    if category < 4:
        ranks = {r for r, _ in cards}
        board_ranks = {r for r, _ in board}
        # cartes qui complètent une quinte grâce aux cartes du joueur (pas le board seul)
        outs = sum(1 for r in range(2, 15) if r not in ranks
                   and _straight_high(ranks | {r}) and not _straight_high(board_ranks | {r}))
        if outs >= 2:
            mask |= DRAW_BIT["oesd"]
        elif outs == 1:
            mask |= DRAW_BIT["gutshot"]
    return mask or DRAW_BIT["nodraw"]


@lru_cache(maxsize=400_000)
def classify(combo: str, board: tuple[str, ...]) -> tuple[int, int]:
    """'AhKd', ('Kc', '7h', '2s') -> (indice dans MADE, masque des tirages selon DRAWS)."""
    hole = [parse_card(combo[:2]), parse_card(combo[2:])]
    cards = [parse_card(c) for c in board]
    value = evaluate(hole + cards)
    draws = _draws(hole, cards, value[0]) if len(cards) < 5 else 0
    return MADE_INDEX[_made(hole, cards, value)], draws


def annotate(node: dict) -> dict:
    """Ajoute au nœud la catégorie de chaque main présente : node["cats"][joueur][i] = [main faite, tirages]."""
    board = tuple(node.get("board", []))
    if len(board) >= 3:
        node["cats"] = [[list(classify(row[0], board)) for row in node["hands"][p]] for p in (0, 1)]
    return node


def labels() -> dict:
    return {"made": [list(m) for m in MADE], "draws": [list(d) for d in DRAWS]}
