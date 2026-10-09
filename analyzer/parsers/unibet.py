"""Parseur des historiques de mains Unibet (cash game NLHE, toutes tailles de table).

Le compte qui a exporté les mains porte son identifiant entre crochets (« Pseudo[Unibet_1a2b…] ») : c'est le
héros ; il garde son pseudo seul. Les joueurs assis mais absents (« sitting out ») ne sont pas servis et ne
comptent pas dans la main. Pas d'heure par action.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITE = "Unibet"
START = "Unibet Hand #"

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"[€$£]?" + _NUM
_HEADER_RE = re.compile(r"^Unibet Hand #(\d+) - " + _NUM + r"/" + _NUM + r" - .*? - UTC (\d{2}:\d{2}:\d{2}) (\d{4}/\d{2}/\d{2})")
_TABLE_RE = re.compile(r'^Table "(.*)" (\d+)-max')
_SEAT_RE = re.compile(r"^Seat (\d+): (.+?) \(" + _MONEY + r"\)( \(sitting out\))?$")
_ACCOUNT_RE = re.compile(r"^(.+?)\[([^\]]+)\]$")
_BUTTON_RE = re.compile(r"^(.+) has the button$")
_DEALT_RE = re.compile(r"^Dealt (?:in (.+)|to (.+?) \[([^\]]*)\])$")
_STREET_RE = re.compile(r"^\*\*\* (Flop|Turn|River) \*\*\* ((?:\[[^\]]*\] ?)+)")
_SHOWS_RE = re.compile(r"^(.+?) shows \[([^\]]*)\](?:, (.*))?$")
_WINS_RE = re.compile(r"^(.+?) wins " + _MONEY)  # « X wins €5.15 », « … from main pot », « … from side pot 1 »
_RETURNED_RE = re.compile(r"^Uncalled bet returned to (.+): " + _MONEY + r"$")
_SUMMARY_WON_RE = re.compile(r"^Seat \d+: (.+?): .*\bwon " + _MONEY)
_TOTAL_RE = re.compile(r"^Total pot " + _MONEY + r"(?: Rake " + _MONEY + r")?")

_LIVE_POST = "live_post"  # blinde d'entrée (« new player's blind », « missed big blind ») : compte dans sa mise
_ACTIONS = [
    (POST_SB, re.compile(r"^posts small blind " + _MONEY)),
    (POST_BB, re.compile(r"^posts big blind " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts (?:ante|missed small blind) " + _MONEY)),  # mises mortes
    (_LIVE_POST, re.compile(r"^posts (?:new player's blind|missed big blind) " + _MONEY)),
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


def count_hands(text: str) -> int:
    return text.count("\n" + START) + text.lstrip().startswith(START)


def parse(text: str) -> Iterator[Hand]:
    for chunk in re.split(r"\n(?=" + re.escape(START) + ")", text):
        if chunk.strip().startswith(START):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.match(lines[0])
    if not head:
        return None
    hand = Hand(site=SITE, hand_id=head.group(1), table_id="", game_name=lines[0].split(" - ")[2].strip(),
                date=datetime.strptime(f"{head.group(5)} {head.group(4)}", "%Y/%m/%d %H:%M:%S"),
                sb=_money(head.group(2)), bb=_money(head.group(3)), total_pot=0.0, rake=0.0)
    seated: dict[str, Seat] = {}
    full: dict[str, str] = {}  # nom affiché dans l'historique -> pseudo (sans l'identifiant du compte)
    dealt: list[str] = []
    button = None
    street, section = "preflop", "head"
    committed: dict[str, float] = {}
    pot = 0.0
    saw_wins = False  # une ligne « X wins … » lue
    returned: dict[str, float] = {}  # mises non payées rendues
    summary_won: dict[str, float] = {}  # « won » du résumé (mise non payée comprise)
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith("*** "):
            m = _STREET_RE.match(line)
            if m:
                street = m.group(1).lower()
                hand.board = " ".join(re.findall(r"\[([^\]]*)\]", m.group(2))).split()
                committed = dict.fromkeys(committed, 0.0)
                section = "actions"
            else:
                title = line.strip("* ").strip().lower()
                section = {"seated players": "seats", "blinds and button": "actions", "hole cards": "hole",
                           "preflop": "actions", "showdown": "showdown", "summary": "summary"}.get(title, section)
                if title == "showdown":
                    hand.showdown = True
            continue
        if section == "head":
            m = _TABLE_RE.match(line)
            if m:
                hand.table_id, hand.max_seats = m.group(1), int(m.group(2))
            continue
        if section == "seats":
            m = _SEAT_RE.match(line)
            if m and not m.group(4):
                display = m.group(2)
                account = _ACCOUNT_RE.match(display)
                name = account.group(1) if account else display
                full[display] = name
                seated[name] = Seat(name=name, seat=int(m.group(1)), stack=_money(m.group(3)),
                                    is_hero=bool(account))
            continue
        player = next((d for d in sorted(full, key=len, reverse=True) if line.startswith(d + " ")), None)
        if section in ("actions", "showdown"):
            # Les gains : « X wins €5.15 », après la dernière action ou à l'abattage, sans la mise non payée (« Uncalled
            # bet returned to X: €1.29 ») que le résumé, lui, compte dans « won ».
            m = _WINS_RE.match(line)
            if m and m.group(1) in full:
                name = full[m.group(1)]
                hand.winnings[name] = round(hand.winnings.get(name, 0.0) + _money(m.group(2)), 2)
                saw_wins = True
                continue
            m = _RETURNED_RE.match(line)
            if m and m.group(1) in full:
                returned[full[m.group(1)]] = returned.get(full[m.group(1)], 0.0) + _money(m.group(2))
                continue
        if section == "hole":
            m = _DEALT_RE.match(line)
            if m:
                display = m.group(1) or m.group(2)
                if display in full:
                    dealt.append(full[display])
                    if m.group(3):
                        hand.hole_cards[full[display]] = m.group(3).split()
            continue
        if section == "actions":
            m = _BUTTON_RE.match(line)
            if m:
                button = full.get(m.group(1), m.group(1))
                continue
            if player is None:
                continue
            name = full[player]
            parsed = _action(line[len(player) + 1:])
            if parsed is None:
                continue
            kind, value = parsed
            committed.setdefault(name, 0.0)
            before = committed[name]
            facing = max(max(committed.values(), default=0.0) - before, 0.0)
            if kind == RAISE:
                to, amount = value, value - before
            elif kind in (CALL, BET, POST_SB, POST_BB):
                amount, to = value, before + value
            elif kind == POST_ANTE:
                amount, to = value, before
            elif kind == _LIVE_POST:  # gardée comme une mise forcée (pas la grosse blinde : les positions n'en dépendent pas)
                kind, amount, to = POST_ANTE, value, before + value
            else:
                amount, to = 0.0, before
            hand.actions.append(Action(player=name, kind=kind, street=street, amount=round(amount, 2),
                                       to=round(to, 2), all_in="all-in" in line, pot_before=round(pot, 2),
                                       facing=round(facing, 2)))
            committed[name] = to
            pot += amount
        elif section == "showdown":
            m = _SHOWS_RE.match(line)
            if m and m.group(1) in full:
                hand.hole_cards[full[m.group(1)]] = m.group(2).split()
                if m.group(3):
                    hand.shown_hand[full[m.group(1)]] = m.group(3)
                continue
        elif section == "summary":
            m = _TOTAL_RE.match(line)
            if m:
                hand.total_pot = _money(m.group(1))  # rake compris, sans les mises non payées
                hand.rake = _money(m.group(2)) if m.group(2) else 0.0
                continue
            m = _SUMMARY_WON_RE.match(line)  # « Seat 3: X: bet €3.79 and won €6.44, net result: €2.65 »
            if m and m.group(1) in full:
                summary_won[full[m.group(1)]] = _money(m.group(2))
    if not saw_wins:  # sans ligne « wins » : le gain du résumé, moins la mise non payée qu'il y compte
        for name, won in summary_won.items():
            if won - returned.get(name, 0.0) > 0.005:
                hand.winnings[name] = round(won - returned.get(name, 0.0), 2)
    players = dealt or list(seated)
    hand.seats = {name: seated[name] for name in sorted(players, key=lambda n: seated[n].seat) if name in seated}
    if button in hand.seats:
        hand.seats[button].is_button = True
    if len(hand.seats) < 2 or not hand.total_pot:
        return None
    hand.finalize()
    return hand


def _action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTIONS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.groups()[-1]) if m.groups() else 0.0
    return None
