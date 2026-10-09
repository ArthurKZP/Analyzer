"""Parseur des historiques Winning Poker Network (WPN, ACR), cash game NLHE, toutes tailles de table.

Particularités du format : pas de deux-points après le nom (« P raises $12.00 to $15.00 ») ; le pot total du résumé
est net du rake et des frais (« Total pot $31.35 | Rake $1.41 | JP Fee $0.24 ») ; les gains se lisent sur « P
collected X from main pot » quand la main en a, sinon dans le résumé (« … did not show and won $12.00 ») : jamais
les deux. Une main jouée deux fois (« run it twice ») garde son premier tableau, ses gains s'additionnent. Les bomb pots
(tout le monde mise une ante, pas de préflop) sont écartés : ils fausseraient les stats préflop.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITE = "WPN"
_START_RE = re.compile(r"^Hand #\d+ - Holdem ?\(No Limit\)", re.M)

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"[€$£]?" + _NUM
_HEADER_RE = re.compile(r"^Hand #(\d+) - (Holdem ?\(No Limit\)) - " + _MONEY + r"/" + _MONEY +
                        r" - (\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2})")
_TABLE_RE = re.compile(r"^(.*?) (\d+)-max Seat #(\d+) is the button")
_SEAT_RE = re.compile(r"^Seat (\d+): (.+?) \(" + _MONEY + r"\)(.*)$")
_DEALT_RE = re.compile(r"^Dealt to (.+?) \[([^\]]*)\]$")
_STREET_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER)(?: (\d))? \*\*\* ((?:\[[^\]]*\] ?)+)")
_SHOWS_RE = re.compile(r"^(.+?) shows \[([^\]]*)\](?: \((.*)\))?$")
_COLLECTED_RE = re.compile(r"^(.+?) collected " + _MONEY + r" from ")
_WON_RE = re.compile(r"^Seat \d+: (.+?) (?:\([^)]*\) )?(?:did not show and won|showed \[[^\]]*\] and won|won) "
                     + _MONEY)
_SUMMARY_CARDS_RE = re.compile(r"^Seat \d+: (.+?) (?:\([^)]*\) )?(?:showed|mucked) \[([^\]]*)\]")
_TOTAL_RE = re.compile(r"^Total pot " + _MONEY)
_FEE_RE = re.compile(r"\| (?:Rake|JP Fee|Jackpot) " + _MONEY)

_LIVE = "live_post"  # une blinde vivante qui n'est pas la grosse blinde (retour à la table)
_ACTIONS = [
    (POST_SB, re.compile(r"^posts the small blind " + _MONEY)),
    (POST_BB, re.compile(r"^posts the big blind " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts (?:the )?ante " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts dead blind " + _MONEY)),
    (FOLD, re.compile(r"^folds")),
    (CHECK, re.compile(r"^checks")),
    (CALL, re.compile(r"^calls " + _MONEY)),
    (BET, re.compile(r"^bets " + _MONEY)),
    (RAISE, re.compile(r"^raises " + _MONEY + r" to " + _MONEY)),
]


def _money(text: str) -> float:
    return float(text.replace(",", ""))


def looks_like(text: str) -> bool:
    return _START_RE.search(text) is not None


def count_hands(text: str) -> int:
    return len(_START_RE.findall(text))


def parse(text: str) -> Iterator[Hand]:
    for chunk in re.split(r"\n(?=Hand #\d+ - )", text.lstrip("﻿")):
        if _HEADER_RE.match(chunk.strip()):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.match(lines[0])
    if not head or any(line.strip() == "BombPot" for line in lines):
        return None
    hand = Hand(site=SITE, hand_id=head.group(1), table_id="", game_name="Hold'em No Limit",
                date=datetime.strptime(head.group(5), "%Y/%m/%d %H:%M:%S"), sb=_money(head.group(3)),
                bb=_money(head.group(4)), total_pot=0.0, rake=0.0)
    button_seat = 0
    listed: dict[str, Seat] = {}
    acted: set[str] = set()
    collected: dict[str, float] = {}
    won: dict[str, float] = {}
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
                if m.group(2) not in (None, "1"):  # le deuxième tableau : on garde le premier
                    continue
                street = m.group(1).lower()
                hand.board = " ".join(re.findall(r"\[([^\]]*)\]", m.group(3))).split()
                committed = dict.fromkeys(committed, 0.0)
                section = "actions"
            elif line.strip("* ").strip() == "SUMMARY":
                section = "summary"
            elif line.strip("* ").strip() == "HOLE CARDS":
                section = "actions"
            continue
        if section == "seats":
            m = _TABLE_RE.match(line)
            if m:
                hand.table_id, hand.max_seats, button_seat = m.group(1), int(m.group(2)), int(m.group(3))
                continue
            m = _SEAT_RE.match(line)
            if m:
                listed[m.group(2)] = Seat(name=m.group(2), seat=int(m.group(1)), stack=_money(m.group(3)),
                                          is_button=int(m.group(1)) == button_seat)
                names = []  # à refaire : un nom de plus
                continue
        if not names:
            names = sorted(listed, key=len, reverse=True)
            for p in listed:
                committed.setdefault(p, 0.0)
        if section == "summary":
            m = _TOTAL_RE.match(line)
            if m:
                hand.rake = round(sum(_money(x) for x in _FEE_RE.findall(line)), 2)
                hand.total_pot = round(_money(m.group(1)) + hand.rake, 2)  # le résumé est net : rake compris
                continue
            m = _WON_RE.match(line)
            if m and m.group(1) in listed:
                won[m.group(1)] = round(won.get(m.group(1), 0.0) + _money(m.group(2)), 2)
            m = _SUMMARY_CARDS_RE.match(line)
            if m and m.group(1) in listed and len(m.group(2).split()) == 2:
                hand.hole_cards.setdefault(m.group(1), m.group(2).split())
            continue
        m = _COLLECTED_RE.match(line)
        if m and m.group(1) in listed:
            collected[m.group(1)] = round(collected.get(m.group(1), 0.0) + _money(m.group(2)), 2)
            continue
        m = _DEALT_RE.match(line)
        if m and m.group(1) in listed:
            listed[m.group(1)].is_hero = True
            hand.hole_cards[m.group(1)] = m.group(2).split()
            continue
        m = _SHOWS_RE.match(line)
        if m and m.group(1) in listed:
            if len(m.group(2).split()) == 2:
                hand.hole_cards[m.group(1)] = m.group(2).split()
            if m.group(3):
                hand.shown_hand[m.group(1)] = m.group(3)
            continue
        player = next((p for p in names if line.startswith(p + " ")), None)
        if player is None:
            continue
        text = line[len(player) + 1:]
        parsed = _action(text)
        if parsed is None:
            continue
        kind, value = parsed
        if kind in (POST_SB, POST_BB) and any(a.kind == kind for a in hand.actions):
            kind = POST_ANTE if kind == POST_SB else _LIVE  # un joueur qui revient : petite blinde morte, grosse vivante
        before = committed.get(player, 0.0)
        facing = max(max(committed.values(), default=0.0) - before, 0.0)
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
                                   to=round(to, 2), all_in="all-in" in text, pot_before=round(pot, 2),
                                   facing=round(facing, 2)))
        acted.add(player)
        committed[player] = to
        pot += amount
    hand.winnings = collected or won  # les « collected » de la main, sinon le résumé : jamais les deux
    dealt = acted | set(hand.winnings) | {p for p, s in listed.items() if s.is_hero}
    hand.seats = {p: s for p, s in listed.items() if p in dealt}
    if len(hand.seats) < 2 or not hand.total_pot:
        return None
    hand.place_button(button_seat)
    hand.finalize()
    return hand


def _action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTIONS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.groups()[-1]) if m.groups() else 0.0
    return None
