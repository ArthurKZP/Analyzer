"""Parseur des historiques de mains Winamax (cash game NLHE, toutes tailles de table).

Particularités du format : pas d'heure par action ; le pot total du résumé est net du rake ; une mise non payée
revient au joueur comme un « side pot » qu'il « collecte ». Aux tables anonymes, les joueurs s'appellent
« Incognito 2 » et la ligne « Player Info » donne un identifiant par place : le joueur prend le nom
« Incognito-<8 premiers caractères de l'identifiant> », pour ne pas confondre deux adversaires assis à la même place.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITE = "Winamax"
START = "Winamax Poker - "

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"[€$£]?" + _NUM + r"\s?[€$£]?"
_HEADER_RE = re.compile(r"HandId: #([\w-]+) - .*?\(([^)]*)\) - (\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})")
_TABLE_RE = re.compile(r"^Table: '(.*)' (\d+)-max .*?Seat #(\d+) is the button")
_SEAT_RE = re.compile(r"^Seat (\d+): (.+) \(" + _MONEY + r"(?:, [^)]*)?\)$")
_INFO_RE = re.compile(r"Seat(\d+): (P\d+-([0-9a-f]+))")
_DEALT_RE = re.compile(r"^Dealt to (.+) \[([^\]]*)\]$")
_STREET_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER) \*\*\* ((?:\[[^\]]*\])+)")
_SHOWS_RE = re.compile(r"^(.+?) shows \[([^\]]*)\](?: \((.*)\))?$")
_COLLECTED_RE = re.compile(r"^(.+?) collected " + _MONEY + r" from ")
_TOTAL_RE = re.compile(r"^Total pot " + _MONEY + r"(?: \| Rake " + _MONEY + r")?")

_ACTIONS = [
    (POST_SB, re.compile(r"^posts small blind " + _MONEY)),
    (POST_BB, re.compile(r"^posts big blind " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts ante " + _MONEY)),
    (FOLD, re.compile(r"^folds")),
    (CHECK, re.compile(r"^checks")),
    (CALL, re.compile(r"^calls " + _MONEY)),
    (BET, re.compile(r"^bets " + _MONEY)),
    (RAISE, re.compile(r"^raises " + _MONEY + r" to " + _MONEY)),
]


def _money(text: str) -> float:
    return float(text.replace(",", ""))


def looks_like(text: str) -> bool:
    return text.lstrip().startswith(START) or ("\n" + START) in text


def parse(text: str) -> Iterator[Hand]:
    for chunk in re.split(r"\n(?=" + re.escape(START) + ")", text):
        if chunk.strip().startswith(START):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.search(lines[0])
    if not head:
        return None
    blinds = [_money(x) for x in re.findall(_NUM, head.group(2))]
    if len(blinds) < 2:
        return None
    hand = Hand(site=SITE, hand_id=head.group(1), table_id="", game_name=lines[0].split(" - ")[1].strip(),
                date=datetime.strptime(head.group(3), "%Y/%m/%d %H:%M:%S"), sb=blinds[-2], bb=blinds[-1],
                total_pot=0.0, rake=0.0)
    button_seat = 0
    seats: dict[str, Seat] = {}
    rename: dict[str, str] = {}
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
                hand.board = " ".join(re.findall(r"\[([^\]]*)\]", m.group(2))).split()
                committed = dict.fromkeys(seats, 0.0)
                section = "actions"
            else:
                title = line.strip("* ").strip()
                section = {"ANTE/BLINDS": "actions", "PRE-FLOP": "actions", "SHOW DOWN": "showdown",
                           "SUMMARY": "summary"}.get(title, section)
                if title == "SHOW DOWN":
                    hand.showdown = True
            continue
        if section == "seats":
            m = _TABLE_RE.match(line)
            if m:
                hand.table_id, hand.max_seats, button_seat = m.group(1), int(m.group(2)), int(m.group(3))
                continue
            m = _SEAT_RE.match(line)
            if m:
                seats[m.group(2)] = Seat(name=m.group(2), seat=int(m.group(1)), stack=_money(m.group(3)),
                                         is_button=int(m.group(1)) == button_seat)
                continue
            if line.startswith("Player Info:"):
                by_seat = {s.seat: s.name for s in seats.values()}
                for seat, _, ident in _INFO_RE.findall(line):
                    name = by_seat.get(int(seat))
                    if name and name.startswith("Incognito"):
                        rename[name] = f"Incognito-{ident[:8]}"
            continue
        if not names:
            names = sorted(seats, key=len, reverse=True)  # « Incognito 2 » avant « Incognito »
            committed = dict.fromkeys(seats, 0.0)
        m = _COLLECTED_RE.match(line)  # gain : à l'abattage, ou avant le résumé quand tout le monde a foldé
        if m and m.group(1) in seats:
            hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + _money(m.group(2)), 2)
            continue
        if section == "actions":
            m = _DEALT_RE.match(line)
            if m and m.group(1) in seats:
                seats[m.group(1)].is_hero = True
                hand.hole_cards[m.group(1)] = m.group(2).split()
                continue
            player = next((p for p in names if line.startswith(p + " ")), None)
            if player is None:
                continue
            text = line[len(player) + 1:]
            parsed = _action(text)
            if parsed is None:
                continue
            kind, value = parsed
            before = committed.get(player, 0.0)
            facing = max(max(committed.values(), default=0.0) - before, 0.0)
            if kind == RAISE:
                to, amount = value, value - before
            elif kind in (CALL, BET, POST_SB, POST_BB):
                amount, to = value, before + value
            elif kind == POST_ANTE:  # l'ante ne compte pas dans la mise à suivre
                amount, to = value, before
            else:
                amount, to = 0.0, before
            hand.actions.append(Action(player=player, kind=kind, street=street, amount=round(amount, 2),
                                       to=round(to, 2), all_in="all-in" in text, pot_before=round(pot, 2),
                                       facing=round(facing, 2)))
            committed[player] = to
            pot += amount
        elif section == "showdown":
            m = _SHOWS_RE.match(line)
            if m and m.group(1) in seats:
                hand.hole_cards[m.group(1)] = m.group(2).split()
                if m.group(3):
                    hand.shown_hand[m.group(1)] = m.group(3)
        elif section == "summary":
            m = _TOTAL_RE.match(line)
            if m:
                hand.rake = _money(m.group(2)) if m.group(2) else 0.0
                hand.total_pot = round(_money(m.group(1)) + hand.rake, 2)  # comme Betclic : rake compris
    hand.seats = seats
    if len(seats) < 2 or not hand.total_pot:
        return None
    hand.finalize()
    for old, new in rename.items():
        hand.rename(old, new)
    return hand


def _action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTIONS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.groups()[-1]) if m.groups() else 0.0
    return None
