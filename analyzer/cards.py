"""Cartes, notation des mains et évaluateur 7 cartes (pour l'EV all-in)."""
from __future__ import annotations

import itertools
import random
from collections import Counter

RANKS = "23456789TJQKA"
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}
SUITS = "cdhs"
DECK = [r + s for r in RANKS for s in SUITS]

HAND_CATEGORIES = [
    "Hauteur",
    "Paire",
    "Double paire",
    "Brelan",
    "Quinte",
    "Couleur",
    "Full",
    "Carré",
    "Quinte flush",
]


def parse_card(card: str) -> tuple[int, str]:
    return RANK_VALUE[card[0].upper()], card[1].lower()


def combo_notation(cards: list[str]) -> str:
    """['Js', 'Th'] -> 'JTo' ; ['Ah', 'Ad'] -> 'AA'."""
    if len(cards) != 2:
        return " ".join(cards)
    (r1, s1), (r2, s2) = sorted((parse_card(c) for c in cards), reverse=True)
    hi, lo = RANKS[r1 - 2], RANKS[r2 - 2]
    if r1 == r2:
        return hi + lo
    return hi + lo + ("s" if s1 == s2 else "o")


def _straight_high(ranks: set[int]) -> int:
    if 14 in ranks:
        ranks = ranks | {1}
    for high in range(14, 4, -1):
        if all(r in ranks for r in range(high - 4, high + 1)):
            return high
    return 0


def evaluate(cards: list[tuple[int, str]]) -> tuple:
    """Évalue 5 à 7 cartes ; renvoie un tuple comparable (catégorie, départages...)."""
    ranks = [r for r, _ in cards]
    counts = Counter(ranks)
    flush = None
    for suit, n in Counter(s for _, s in cards).items():
        if n >= 5:
            suited = sorted((r for r, s in cards if s == suit), reverse=True)
            high = _straight_high(set(suited))
            if high:
                return (8, high)
            flush = (5, *suited[:5])
    quads = [r for r, n in counts.items() if n == 4]
    trips = sorted((r for r, n in counts.items() if n == 3), reverse=True)
    pairs = sorted((r for r, n in counts.items() if n == 2), reverse=True)
    if quads:
        return (7, quads[0], max((r for r in ranks if r != quads[0]), default=0))
    if trips and (len(trips) > 1 or pairs):
        return (6, trips[0], max(trips[1:] + pairs))
    if flush:
        return flush
    high = _straight_high(set(ranks))
    if high:
        return (4, high)
    if trips:
        kickers = sorted((r for r in ranks if r != trips[0]), reverse=True)[:2]
        return (3, trips[0], *kickers)
    if len(pairs) >= 2:
        kicker = max((r for r in ranks if r not in pairs[:2]), default=0)
        return (2, pairs[0], pairs[1], kicker)
    if pairs:
        kickers = sorted((r for r in ranks if r != pairs[0]), reverse=True)[:3]
        return (1, pairs[0], *kickers)
    return (0, *sorted(ranks, reverse=True)[:5])


def equity(hand_a: list[str], hand_b: list[str], board: list[str], samples: int = 20000, seed: str = "") -> float:
    """Équité de hand_a contre hand_b (égalités comptées pour moitié).

    Énumération exacte quand il reste 2 cartes ou moins à tirer, Monte-Carlo sinon.
    """
    a = [parse_card(c) for c in hand_a]
    b = [parse_card(c) for c in hand_b]
    known = [parse_card(c) for c in board]
    used = set(hand_a) | set(hand_b) | set(board)
    deck = [parse_card(c) for c in DECK if c not in used]
    missing = 5 - len(known)
    if missing <= 0:
        runouts = [()]
    elif missing <= 2:
        runouts = itertools.combinations(deck, missing)
    else:
        rng = random.Random(seed or "".join(hand_a + hand_b + board))
        runouts = (rng.sample(deck, missing) for _ in range(samples))
    score = total = 0.0
    for runout in runouts:
        full = known + list(runout)
        va, vb = evaluate(a + full), evaluate(b + full)
        score += 1.0 if va > vb else 0.5 if va == vb else 0.0
        total += 1
    return score / total if total else 0.5


def _has_flush_draw(hole: list[tuple[int, str]], board: list[tuple[int, str]]) -> bool:
    suits = [s for _, s in hole + board]
    return any(suits.count(s) == 4 and any(hs == s for _, hs in hole) for s in set(suits))


def _has_straight_draw(hole: list[tuple[int, str]], board: list[tuple[int, str]]) -> bool:
    ranks = {r for r, _ in hole + board}
    hole_ranks = {r for r, _ in hole}
    if 14 in ranks:
        ranks.add(1)
    if 14 in hole_ranks:
        hole_ranks.add(1)
    for low in range(1, 11):
        window = set(range(low, low + 5))
        if len(window & ranks) == 4 and window & hole_ranks:
            return True
    return False


def _pair_label(pair: int, hole_ranks: list[int], board_ranks: list[int]) -> str:
    if hole_ranks[0] == hole_ranks[1]:
        return "Overpair" if pair > board_ranks[0] else "Petite paire servie"
    if pair == board_ranks[0]:
        return "Top paire"
    if len(board_ranks) > 1 and pair == board_ranks[1]:
        return "2e paire"
    return "Petite paire"


def describe_holding(hole: list[str], board: list[str]) -> str:
    """Force de la main sur un board donné : « Top paire », « Double paire », « Rien + tirage couleur »..."""
    h = [parse_card(c) for c in hole]
    b = [parse_card(c) for c in board]
    value = evaluate(h + b)
    category = value[0]
    board_value = evaluate(b) if b else (0,)
    board_ranks = sorted({r for r, _ in b}, reverse=True)
    hole_ranks = [r for r, _ in h]
    if category >= 1 and category == board_value[0] and value[:3] == board_value[:3]:
        category = 0  # la main est entièrement sur le board
    if category in (1, 2):
        # Seules comptent les paires faites avec ses cartes privatives
        own_pairs = [p for p in value[1:category + 1] if p in hole_ranks]
        if category == 2 and len(own_pairs) == 2:
            label = "Double paire"
        elif own_pairs:
            category = 1
            label = _pair_label(own_pairs[0], hole_ranks, board_ranks)
        else:
            category = 0
            label = "Rien"
    elif category == 0:
        label = "Rien"
    else:
        label = HAND_CATEGORIES[category]
    if len(b) < 5 and category <= 1:
        draws = []
        if _has_flush_draw(h, b):
            draws.append("tirage couleur")
        if _has_straight_draw(h, b):
            draws.append("tirage quinte")
        if draws:
            label += " + " + " & ".join(draws)
    return label
