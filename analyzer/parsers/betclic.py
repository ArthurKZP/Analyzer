"""Parseur des historiques de mains Betclic.fr (cash game NLHE)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterator

from ..models import (
    BET,
    CALL,
    CHECK,
    FOLD,
    POST_BB,
    POST_SB,
    RAISE,
    Action,
    Hand,
    Seat,
)

SITE = "Betclic"
HAND_SEPARATOR = "*** HEADER ***"

_MONEY = r"[€$£]?\s?([\d,]+(?:\.\d+)?)"
_SEAT_RE = re.compile(r"^Seat (\d+): (.+) \(" + _MONEY + r"\)(?: \[([^\]]*)\])?$")  # étiquettes : BTN, SB, BB, Hero
_ACTION_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}) - (.+)$")
_STREET_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER) \*\*\* \[([^\]]*)\]")
_SHOWS_RE = re.compile(r"^(.+?) shows \[([^\]]*)\](?: \(([^)]*)\))?")
_WINS_RE = re.compile(r"^(.+?) wins (?:the )?(?:main |side )?pot(?: \d+)? of " + _MONEY)
_BLINDS_RE = re.compile(_MONEY + r"\s*/\s*" + _MONEY)
_MAX_RE = re.compile(r"\b(\d+)[ -]?max\b", re.IGNORECASE)

_ACTION_PATTERNS = [
    (POST_SB, re.compile(r"^Posts SB " + _MONEY)),
    (POST_BB, re.compile(r"^Posts BB " + _MONEY)),
    (FOLD, re.compile(r"^Folds")),
    (CHECK, re.compile(r"^Checks")),
    (CALL, re.compile(r"^Calls " + _MONEY)),
    (BET, re.compile(r"^Bets " + _MONEY)),
    (RAISE, re.compile(r"^Raises to " + _MONEY)),
]


def _money(text: str) -> float:
    return float(text.replace(",", ""))


def looks_like(text: str) -> bool:
    return HAND_SEPARATOR in text and "Site: Betclic" in text


def split_hands(text: str) -> Iterator[str]:
    for chunk in text.split(HAND_SEPARATOR):
        if chunk.strip():
            yield chunk


def parse(text: str) -> Iterator[Hand]:
    for chunk in split_hands(text):
        hand = parse_hand(chunk)
        if hand is not None:
            yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    header: dict[str, str] = {}
    section = "header"
    seats: dict[str, Seat] = {}
    hand: Hand | None = None
    street = "preflop"
    committed: dict[str, float] = {}
    pot = 0.0

    def start_hand() -> Hand:
        date = datetime.strptime(header["Date & Time"][:19], "%Y-%m-%d %H:%M:%S")
        blinds = _BLINDS_RE.search(header.get("Blinds", ""))
        sb, bb = (_money(blinds.group(1)), _money(blinds.group(2))) if blinds else (0.0, 0.0)
        return Hand(
            site=SITE,
            hand_id=header.get("Hand ID", ""),
            table_id=header.get("Table ID", ""),
            game_name=header.get("Game Name", ""),
            date=date,
            sb=sb,
            bb=bb,
            total_pot=_money(header.get("Total Pot", "0").lstrip("€$£")),
            rake=_money(header.get("Rake", "0").lstrip("€$£")),
            max_seats=_max_seats(header.get("Game Name", "")),
        )

    for line in lines:
        if not line:
            continue
        if line.startswith("*** "):
            m = _STREET_RE.match(line)
            if m:
                street = m.group(1).lower()
                hand.board = m.group(2).split()
                committed = {p: 0.0 for p in seats}
                continue
            name = line.strip("* ").strip()
            section = {
                "PLAYERS": "players",
                "HOLE CARDS": "hole",
                "PRE-FLOP": "actions",
                "SHOWDOWN": "showdown",
                "SUMMARY": "summary",
            }.get(name, section)
            if section in ("hole", "actions") and hand is None:
                hand = start_hand()
                hand.seats = seats
                committed = {p: 0.0 for p in seats}
            if section == "showdown":
                hand.showdown = True
            continue

        if section == "header":
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
        elif section == "players":
            m = _SEAT_RE.match(line)
            if m:
                tags = (m.group(4) or "").split()
                seats[m.group(2)] = Seat(
                    name=m.group(2),
                    seat=int(m.group(1)),
                    stack=_money(m.group(3)),
                    is_button="BTN" in tags,
                    is_hero="Hero" in tags,
                )
        elif section == "hole":
            name, _, cards = line.partition(": ")
            if name in seats:
                hand.hole_cards[name] = cards.strip("[] ").split()
        elif section == "actions":
            m = _ACTION_RE.match(line)
            if not m:
                continue
            ts, rest = m.groups()
            player = next((p for p in seats if rest.startswith(p + ": ")), None)
            if player is None:
                continue
            text = rest[len(player) + 2 :]
            action = _parse_action(text)
            if action is None:  # Sits in, Disconnected, ...
                continue
            kind, value = action
            before = committed.get(player, 0.0)
            facing = max(committed.values(), default=0.0) - before
            if kind in (RAISE,):
                to, amount = value, value - before
            elif kind in (CALL, BET, POST_SB, POST_BB):
                amount, to = value, before + value
            else:
                amount, to = 0.0, before
            hh, mm, ss = map(int, ts.split(":"))
            time = hand.date.replace(hour=hh, minute=mm, second=ss)
            if time < hand.date - timedelta(hours=12):  # passage de minuit
                time += timedelta(days=1)
            hand.actions.append(
                Action(
                    player=player,
                    kind=kind,
                    street=street,
                    amount=round(amount, 2),
                    to=round(to, 2),
                    all_in="all-in" in text,
                    time=time,
                    pot_before=round(pot, 2),
                    facing=round(max(facing, 0.0), 2),
                )
            )
            committed[player] = to
            pot += amount
        elif section == "showdown":
            m = _SHOWS_RE.match(line)
            if m and m.group(1) in seats:
                hand.hole_cards[m.group(1)] = m.group(2).split()
                if m.group(3):
                    hand.shown_hand[m.group(1)] = m.group(3)
        elif section == "summary":
            m = _WINS_RE.match(line)
            if m and m.group(1) in seats:
                name = m.group(1)
                hand.winnings[name] = round(hand.winnings.get(name, 0.0) + _money(m.group(2)), 2)

    if hand is None or len(hand.seats) < 2:
        return None
    if hand.button is None and len(hand.seats) == 2:  # en HU, le bouton est la petite blinde
        sb = hand.small_blind or next((s for s in hand.seats if s != hand.big_blind), None)
        if sb in hand.seats:
            hand.seats[sb].is_button = True
    hand.finalize()
    return hand


def _max_seats(game_name: str) -> int:
    """« NLHE 1/2 6 max Deep » -> 6 ; « NLHE 2.5/5 HU Deep » -> 2."""
    m = _MAX_RE.search(game_name)
    if m:
        return int(m.group(1))
    return 2 if re.search(r"\bHU\b", game_name) else 0


def _parse_action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTION_PATTERNS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.group(1)) if m.groups() else 0.0
    return None
