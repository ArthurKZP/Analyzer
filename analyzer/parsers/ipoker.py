"""Parseur des historiques du réseau iPoker (exportés en texte : « GAME #… »), cash game NLHE.

Particularités du format : les cartes s'écrivent couleur puis rang (« D2 S10 CA » : 2♦ 10♠ A♣) ; « Raise X » donne la
mise totale sur la street, « Allin X » ce que le joueur ajoute (tout son tapis restant) ; le pot total du résumé est
net du rake, et les gains se lisent sur « P: wins X ». Le bouton est la place marquée « DEALER ». Les mains d'une table
anonyme regardée (tous les joueurs nommés « Player N », d'après leur place) sont écartées.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITE = "iPoker"
_START_RE = re.compile(r"^GAME #\d+ ", re.M)

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"[€$£]?" + _NUM
_HEADER_RE = re.compile(r"^GAME #(\d+) .*?(Texas Hold'em NL) " + _MONEY + r"/" + _MONEY +
                        r" (\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2})")
_SIZE_RE = re.compile(r"^Table Size (\d+)$")
_TABLE_RE = re.compile(r"^Table (.+)$")
_SEAT_RE = re.compile(r"^Seat (\d+): (.+?) \(" + _MONEY + r" in chips\)(\s+DEALER)?$")
_DEALT_RE = re.compile(r"^Dealt to (.+?) \[([^\]]*)\]$")
_STREET_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER) \*\*\* \[([^\]]*)\]")
_SHOWS_RE = re.compile(r"^(.+?): (?:Shows|Mucks) \[([^\]]*)\]\s*(.*)$")
_WINS_RE = re.compile(r"^(.+?): wins " + _MONEY)
_TOTAL_RE = re.compile(r"^Total pot " + _MONEY + r"(?: Rake " + _MONEY + r")?")
_ANONYMOUS_RE = re.compile(r"^Player \d+$")  # le nom donné aux joueurs d'une table anonyme : leur place
_RANKS = {"10": "T"}

_ALLIN = "allin"  # « Allin X » : ce que le joueur ajoute ; mise, relance ou suivi selon ce qu'il a en face
_LIVE = "live_post"  # une blinde vivante qui n'est pas la grosse blinde (retour à la table)
_ACTIONS = [
    (POST_SB, re.compile(r"^Post SB " + _MONEY)),
    (POST_BB, re.compile(r"^Post BB " + _MONEY)),
    (POST_ANTE, re.compile(r"^Post (?:Ante|Dead) " + _MONEY)),
    (FOLD, re.compile(r"^Fold")),
    (CHECK, re.compile(r"^Check")),
    (CALL, re.compile(r"^Call " + _MONEY)),
    (BET, re.compile(r"^Bet " + _MONEY)),
    (RAISE, re.compile(r"^Raise(?: \(\w+\))? " + _MONEY)),
    (_ALLIN, re.compile(r"^Allin " + _MONEY)),
]


def _money(text: str) -> float:
    return float(text.replace(",", ""))


def card(code: str) -> str:
    """« D2 » → « 2d », « S10 » → « Ts », « CA » → « Ac »."""
    return _RANKS.get(code[1:], code[1:]) + code[0].lower()


def looks_like(text: str) -> bool:
    return _START_RE.search(text) is not None


def count_hands(text: str) -> int:
    return len(_START_RE.findall(text))


def parse(text: str) -> Iterator[Hand]:
    for chunk in re.split(r"\n(?=GAME #\d+ )", text.lstrip("﻿")):
        if _HEADER_RE.match(chunk.strip()):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.match(lines[0])
    if not head:
        return None
    hand = Hand(site=SITE, hand_id=head.group(1), table_id="", game_name=head.group(2),
                date=datetime.strptime(head.group(5), "%Y-%m-%d %H:%M:%S"), sb=_money(head.group(3)),
                bb=_money(head.group(4)), total_pot=0.0, rake=0.0)
    listed: dict[str, Seat] = {}
    acted: set[str] = set()
    street, section = "preflop", "seats"
    committed: dict[str, float] = {}
    pot = 0.0
    names: list[str] = []
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith("*** "):
            m = _STREET_RE.match(line)
            if m:
                street = m.group(1).lower()
                hand.board += [card(c) for c in m.group(2).split()]
                committed = dict.fromkeys(committed, 0.0)
                section = "actions"
            elif line.strip("* ").strip() == "SUMMARY":
                section = "summary"
            elif line.strip("* ").strip() == "HOLE CARDS":
                section = "actions"
            continue
        if section == "seats":
            m = _SIZE_RE.match(line)
            if m:
                hand.max_seats = int(m.group(1))
                continue
            m = _SEAT_RE.match(line)
            if m:
                listed[m.group(2)] = Seat(name=m.group(2), seat=int(m.group(1)), stack=_money(m.group(3)),
                                          is_button=bool(m.group(4)))
                names = []  # à refaire : un nom de plus
                continue
            m = _TABLE_RE.match(line)
            if m and not listed:
                hand.table_id = m.group(1)
                continue
        if not names:
            names = sorted(listed, key=len, reverse=True)
            for p in listed:
                committed.setdefault(p, 0.0)
        if section == "summary":
            m = _TOTAL_RE.match(line)
            if m:
                hand.rake = _money(m.group(2)) if m.group(2) else 0.0
                hand.total_pot = round(_money(m.group(1)) + hand.rake, 2)  # le résumé est net : rake compris
                continue
            m = _WINS_RE.match(line)
            if m and m.group(1) in listed:
                hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + _money(m.group(2)), 2)
                continue
            m = _SHOWS_RE.match(line)
            if m and m.group(1) in listed:
                cards = [card(c) for c in m.group(2).split()]
                if len(cards) == 2:
                    hand.hole_cards[m.group(1)] = cards
                if m.group(3):
                    hand.shown_hand[m.group(1)] = m.group(3).strip()
            continue
        m = _DEALT_RE.match(line)
        if m and m.group(1) in listed:
            listed[m.group(1)].is_hero = True
            hand.hole_cards[m.group(1)] = [card(c) for c in m.group(2).split()]
            continue
        player = next((p for p in names if line.startswith(p + ": ")), None)
        if player is None:
            continue
        text = line[len(player) + 2:]
        parsed = _action(text)
        if parsed is None:
            continue
        kind, value = parsed
        if kind in (POST_SB, POST_BB) and any(a.kind == kind for a in hand.actions):
            kind = POST_ANTE if kind == POST_SB else _LIVE  # un joueur qui revient : petite blinde morte, grosse vivante
        before = committed.get(player, 0.0)
        top = max(committed.values(), default=0.0)
        facing = max(top - before, 0.0)
        all_in = kind == _ALLIN
        if all_in:  # tout le tapis : une mise, une relance au-dessus de la mise en cours, ou un suivi (même pour moins)
            kind = BET if top == 0 else RAISE if before + value > top else CALL
            if kind == RAISE:
                value = before + value
        if kind == RAISE:
            to, amount = value, value - before
        elif kind in (CALL, BET, POST_SB, POST_BB):
            amount, to = value, before + value
        elif kind == _LIVE:  # gardée comme une mise forcée : les positions viennent des vraies blindes
            kind, amount, to = POST_ANTE, value, before + value
        elif kind == POST_ANTE:
            amount, to = value, before
        else:
            amount, to = 0.0, before
        hand.actions.append(Action(player=player, kind=kind, street=street, amount=round(amount, 2),
                                   to=round(to, 2), all_in=all_in, pot_before=round(pot, 2),
                                   facing=round(facing, 2)))
        acted.add(player)
        committed[player] = to
        pot += amount
    dealt = acted | set(hand.winnings) | {p for p, s in listed.items() if s.is_hero}
    button_seat = next((s.seat for s in listed.values() if s.is_button), 0)
    hand.seats = {p: s for p, s in listed.items() if p in dealt}
    if len(hand.seats) < 2 or not hand.total_pot:
        return None
    if not hand.hero and all(_ANONYMOUS_RE.match(p) for p in hand.seats):
        return None  # une table anonyme regardée : ni toi ni des joueurs reconnaissables
    hand.place_button(button_seat)
    hand.finalize()
    return hand


def _action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTIONS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.groups()[-1]) if m.groups() else 0.0
    return None
