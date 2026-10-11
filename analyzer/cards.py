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


# --- Évaluateur rapide (équités) -----------------------------------------------------------------------------
# Une carte = rang (0 = deux … 12 = as) * 4 + couleur. Une main = des masques de bits : les rangs présents au moins
# 1, 2, 3, 4 fois, et les rangs de chaque couleur. Le score est un entier qui classe les mains exactement comme
# evaluate() (catégorie, puis départages, quatre bits par rang), six à sept fois plus vite.

def _build_tables() -> tuple[list[int], list[tuple[int, ...]], list[int]]:
    straight = [0] * 8192
    for mask in range(8192):
        wide = (mask << 1) | ((mask >> 12) & 1)  # bit 0 : l'as en bas (roue), bit r + 1 : le rang r
        for high in range(13, 3, -1):
            window = 0b11111 << (high - 4)
            if wide & window == window:
                straight[mask] = high + 1  # hauteur de la quinte, de 5 à 14
                break
    desc = [tuple(r + 2 for r in range(12, -1, -1) if m >> r & 1) for m in range(8192)]
    pop = [len(d) for d in desc]
    return straight, desc, pop


_STRAIGHT, _DESC, _POP = _build_tables()
CARD_INDEX = {c: RANKS.index(c[0]) * 4 + SUITS.index(c[1]) for c in DECK}


def card_index(card: str) -> int:
    return CARD_INDEX[card[0].upper() + card[1].lower()]


def _add(state: tuple, cards) -> tuple:
    """Ajoute des cartes (indices) à l'état (m1, m2, m3, m4, couleur c, d, h, s)."""
    m1, m2, m3, m4, s0, s1, s2, s3 = state
    for c in cards:
        b = 1 << (c >> 2)
        m4 |= m3 & b
        m3 |= m2 & b
        m2 |= m1 & b
        m1 |= b
        s = c & 3
        if s == 0:
            s0 |= b
        elif s == 1:
            s1 |= b
        elif s == 2:
            s2 |= b
        else:
            s3 |= b
    return m1, m2, m3, m4, s0, s1, s2, s3


EMPTY = (0, 0, 0, 0, 0, 0, 0, 0)


def _score(state: tuple) -> int:
    """Le score d'une main de 5 à 7 cartes (voir plus haut) : plus grand = meilleure main."""
    m1, m2, m3, m4, s0, s1, s2, s3 = state
    desc = _DESC
    flush = 0
    for sm in (s0, s1, s2, s3):
        if _POP[sm] >= 5:
            high = _STRAIGHT[sm]
            if high:
                return (8 << 24) | (high << 20)
            d = desc[sm]
            flush = (5 << 24) | (d[0] << 20) | (d[1] << 16) | (d[2] << 12) | (d[3] << 8) | (d[4] << 4)
    if m4:
        q = desc[m4][0]
        rest = desc[m1 & ~(1 << (q - 2))]
        return (7 << 24) | (q << 20) | ((rest[0] if rest else 0) << 16)
    trips = m3 & ~m4
    pairs = m2 & ~m3
    if trips:
        dt = desc[trips]
        if len(dt) > 1 or pairs:
            t = dt[0]
            return (6 << 24) | (t << 20) | (desc[(trips & ~(1 << (t - 2))) | pairs][0] << 16)
    if flush:
        return flush
    high = _STRAIGHT[m1]
    if high:
        return (4 << 24) | (high << 20)
    if trips:
        t = desc[trips][0]
        k = desc[m1 & ~(1 << (t - 2))]
        return (3 << 24) | (t << 20) | (k[0] << 16) | (k[1] << 12)
    if pairs:
        dp = desc[pairs]
        if len(dp) >= 2:
            p1, p2 = dp[0], dp[1]
            k = desc[m1 & ~(1 << (p1 - 2)) & ~(1 << (p2 - 2))]
            return (2 << 24) | (p1 << 20) | (p2 << 16) | ((k[0] if k else 0) << 12)
        p = dp[0]
        k = desc[m1 & ~(1 << (p - 2))]
        return (1 << 24) | (p << 20) | (k[0] << 16) | (k[1] << 12) | (k[2] << 8)
    d = desc[m1]
    return (d[0] << 20) | (d[1] << 16) | (d[2] << 12) | (d[3] << 8) | (d[4] << 4)


def score(cards: list[str]) -> int:
    """Le score de 5 à 7 cartes (« Ah », « 7c »…) : classe les mains comme evaluate(), en plus rapide."""
    return _score(_add(EMPTY, [card_index(c) for c in cards]))


_EQUITY_CACHE: dict[tuple, float] = {}


def equity(hand_a: list[str], hand_b: list[str], board: list[str], samples: int = 20000, seed: str = "") -> float:
    """Équité de hand_a contre hand_b (égalités comptées pour moitié).

    Énumération exacte quand il reste 2 cartes ou moins à tirer, Monte-Carlo sinon (tirages reproductibles : la
    graine dépend des cartes). Le résultat est gardé en mémoire, et sur disque (analyzer.store) d'une session à
    l'autre."""
    key = ("".join(hand_a), "".join(hand_b), "".join(board), samples, seed)
    cached = _EQUITY_CACHE.get(key)
    if cached is not None:
        return cached
    from . import store  # le cache sur disque (import tardif : cards ne dépend de rien d'autre)
    cached = store.get("equite", "|".join(map(str, key)))
    if cached is not None:
        _EQUITY_CACHE[key] = cached
        return cached
    value = _equity(hand_a, hand_b, board, samples, seed)
    _EQUITY_CACHE[key] = value
    store.put("equite", "|".join(map(str, key)), value)
    return value


def _equity(hand_a: list[str], hand_b: list[str], board: list[str], samples: int, seed: str) -> float:
    used = set(hand_a) | set(hand_b) | set(board)
    deck = [CARD_INDEX[c] for c in DECK if c not in used]  # même ordre qu'avant : mêmes tirages Monte-Carlo
    known = [card_index(c) for c in board]
    base_a = _add(EMPTY, [card_index(c) for c in hand_a] + known)
    base_b = _add(EMPTY, [card_index(c) for c in hand_b] + known)
    missing = 5 - len(known)
    if missing <= 0:
        runouts = [()]
    elif missing <= 2:
        runouts = itertools.combinations(deck, missing)
    else:
        rng = random.Random(seed or "".join(hand_a + hand_b + board))
        runouts = (rng.sample(deck, missing) for _ in range(samples))
    score_ = total = 0.0
    for runout in runouts:
        va, vb = _score(_add(base_a, runout)), _score(_add(base_b, runout))
        score_ += 1.0 if va > vb else 0.5 if va == vb else 0.0
        total += 1
    return score_ / total if total else 0.5


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
