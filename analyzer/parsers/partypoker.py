"""Parseur des historiques partypoker (« ***** Hand History for Game … ***** »), cash game NLHE.

Particularités du format : les montants sont entre crochets ; « raises [X] » et « is all-In [X] » donnent ce que le
joueur ajoute ; les blindes se lisent sur les blindes postées (l'en-tête donne parfois la cave, « €10 EUR NL ») ; un
joueur qui revient poste « big blind + dead » (la grosse blinde, vivante, et la petite, morte) ; ni pot total ni rake :
les gains (« P wins X ») comptent la mise rendue quand tout le monde s'est couché, pas l'excédent d'un tapis à
l'abattage. Le pot et le rake se déduisent donc des mises et des gains.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITE = "partypoker"
START = "***** Hand History for Game "
_MONTHS = {m: k + 1 for k, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"))}

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"\[\s*[€$£]?" + _NUM + r"(?:\s*[€$£])?(?: [A-Z]{3})?\s*\]"
_HEADER_RE = re.compile(r"^\*\*\*\*\* Hand History for Game (\d+) \*\*\*\*\*")
_GAME_RE = re.compile(r"^(.*?)\s*(NL Texas Hold'em) - \w+, ?(\w+) (\d{1,2}), (\d{1,2}:\d{2}:\d{2}) \w+ (\d{4})")
_STAKES_RE = re.compile(r"[€$£]?" + _NUM)
_TABLE_RE = re.compile(r"^Table (.+?)(?: \(.*\))?$")
_BUTTON_RE = re.compile(r"^Seat (\d+) is the button")
_PLAYERS_RE = re.compile(r"^Total number of players : \d+/(\d+)")
_SEAT_RE = re.compile(r"^Seat (\d+): (.+?) \( [€$£]?" + _NUM + r"(?: [A-Z]{3})? \)$")
_DEALT_RE = re.compile(r"^Dealt to (.+?) \[\s*([^\]]*?)\s*\]$")
_STREET_RE = re.compile(r"^\*\* Dealing (Flop|Turn|River) \*\* \[\s*([^\]]*?)\s*\]")
_SHOWS_RE = re.compile(r"^(.+?) shows \[\s*([^\]]*?)\s*\]\s*(.*?)\.?$")
_WINS_RE = re.compile(r"^(.+?) wins [€$£]?" + _NUM)

_ALLIN = "allin"
_BOTH = "both_blinds"  # retour à la table : la grosse blinde (vivante) et la petite (morte) d'un coup
_ACTIONS = [
    (POST_SB, re.compile(r"^posts small blind " + _MONEY)),
    (_BOTH, re.compile(r"^posts big blind \+ dead " + _MONEY)),
    (POST_BB, re.compile(r"^posts big blind " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts (?:ante|dead blind) " + _MONEY)),
    (FOLD, re.compile(r"^folds")),
    (CHECK, re.compile(r"^checks")),
    (CALL, re.compile(r"^calls " + _MONEY)),
    (BET, re.compile(r"^bets " + _MONEY)),
    (RAISE, re.compile(r"^raises " + _MONEY)),
    (_ALLIN, re.compile(r"^is all-In\s+" + _MONEY)),
]


def _money(text: str) -> float:
    return float(text.replace(",", ""))


def _cards(text: str) -> list[str]:
    return text.replace(",", " ").split()


def looks_like(text: str) -> bool:
    return START in text


def count_hands(text: str) -> int:
    return text.count(START)


def parse(text: str) -> Iterator[Hand]:
    for chunk in re.split(r"\n(?=\*\*\*\*\* Hand History for Game )", text.lstrip("﻿")):
        if _HEADER_RE.match(chunk.strip()):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.match(lines[0])
    game = _GAME_RE.match(lines[1]) if len(lines) > 1 else None
    if not head or not game:
        return None
    month = _MONTHS.get(game.group(3))
    if month is None:
        return None
    hh, mm, ss = map(int, game.group(5).split(":"))
    stakes = [_money(x) for x in _STAKES_RE.findall(game.group(1))]
    hand = Hand(site=SITE, hand_id=head.group(1), table_id="", game_name=game.group(2),
                date=datetime(int(game.group(6)), month, int(game.group(4)), hh, mm, ss),
                sb=stakes[-2] if len(stakes) >= 2 else 0.0, bb=stakes[-1] if len(stakes) >= 2 else 0.0,
                total_pot=0.0, rake=0.0)
    button_seat = 0
    listed: dict[str, Seat] = {}
    acted: set[str] = set()
    street = "preflop"
    committed: dict[str, float] = {}
    betting = committed  # les mises de la dernière street jouée (la mise non payée s'y lit)
    pot = 0.0
    names: list[str] = []
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        m = _STREET_RE.match(line)
        if m:
            street = m.group(1).lower()
            hand.board += _cards(m.group(2))
            committed = dict.fromkeys(committed, 0.0)
            continue
        m = _TABLE_RE.match(line)
        if m and not listed:
            hand.table_id = m.group(1)
            continue
        m = _BUTTON_RE.match(line)
        if m:
            button_seat = int(m.group(1))
            continue
        m = _PLAYERS_RE.match(line)
        if m:
            hand.max_seats = int(m.group(1))
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
        m = _DEALT_RE.match(line)
        if m and m.group(1) in listed:
            listed[m.group(1)].is_hero = True
            hand.hole_cards[m.group(1)] = _cards(m.group(2))
            continue
        m = _SHOWS_RE.match(line)
        if m and m.group(1) in listed:
            cards = _cards(m.group(2))
            if len(cards) == 2:
                hand.hole_cards[m.group(1)] = cards
            if m.group(3):
                hand.shown_hand[m.group(1)] = m.group(3)
            continue
        m = _WINS_RE.match(line)
        if m and m.group(1) in listed:
            hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + _money(m.group(2)), 2)
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
        top = max(committed.values(), default=0.0)
        facing = max(top - before, 0.0)
        all_in = kind == _ALLIN
        if all_in:  # ce qu'il ajoute : une mise, une relance au-dessus de la mise en cours, ou un suivi
            kind = BET if top == 0 else RAISE if before + value > top else CALL
        if kind == POST_BB and any(a.kind == POST_BB for a in hand.actions):
            kind = _BOTH  # une deuxième grosse blinde : un joueur qui revient à la table
        elif kind == POST_SB and any(a.kind == POST_SB for a in hand.actions):
            kind = POST_ANTE  # une deuxième petite blinde : celle qu'il a manquée, morte
        if kind in (CALL, BET, RAISE, POST_SB, POST_BB):  # partypoker donne ce que le joueur ajoute
            amount, to = value, before + value
        elif kind == _BOTH:  # seule la grosse blinde (celle de la main) compte dans sa mise
            bb = next((a.amount for a in hand.actions if a.kind == POST_BB), value)
            kind, amount, to = POST_ANTE, value, before + min(value, bb)
        elif kind == POST_ANTE:
            amount, to = value, before
        else:
            amount, to = 0.0, before
        hand.actions.append(Action(player=player, kind=kind, street=street, amount=round(amount, 2),
                                   to=round(to, 2), all_in=all_in, pot_before=round(pot, 2),
                                   facing=round(facing, 2)))
        acted.add(player)
        committed[player] = to
        betting = committed
        pot += amount
    dealt = acted | set(hand.winnings) | {p for p, s in listed.items() if s.is_hero}
    hand.seats = {p: s for p, s in listed.items() if p in dealt}
    if len(hand.seats) < 2 or not hand.winnings:
        return None
    hand.place_button(button_seat)
    _blinds_from_posts(hand)
    if not hand.bb:
        return None
    # Le pot : les mises, sans la mise que personne n'a payée (sur la dernière street jouée, l'écart entre la plus grosse
    # mise et la suivante) ; quand tout le monde s'est couché, les gains la comptent : elle en sort, comme sur les autres
    # sites. Le rake : ce qui manque aux gains.
    total = sum(a.amount for a in hand.actions)
    ranked = sorted(betting.items(), key=lambda kv: kv[1], reverse=True)
    excess = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else 0.0
    won = sum(hand.winnings.values())
    if excess > 0.005 and won > total - excess + 0.005:  # la mise rendue est dans ses gains
        top = ranked[0][0]
        if hand.winnings.get(top, 0.0) >= excess:
            hand.winnings[top] = round(hand.winnings[top] - excess, 2)
            won -= excess
        else:
            excess = 0.0
    hand.total_pot = round(total - excess, 2)
    hand.rake = round(max(hand.total_pot - won, 0.0), 2)
    hand.finalize()
    return hand


def _blinds_from_posts(hand: Hand) -> None:
    """Les blindes : celles que la main a postées (l'en-tête de partypoker donne parfois la cave à la place)."""
    sb = next((a.amount for a in hand.actions if a.kind == POST_SB), None)
    bb = next((a.amount for a in hand.actions if a.kind == POST_BB), None)
    if bb:
        hand.bb = bb
        hand.sb = sb if sb else bb / 2
    elif hand.bb and hand.bb >= 20 * hand.sb:  # « €10 EUR NL » : une cave de 100 grosses blindes
        hand.bb, hand.sb = hand.bb / 100, hand.bb / 200


def _action(text: str) -> tuple[str, float] | None:
    for kind, pattern in _ACTIONS:
        m = pattern.match(text)
        if m:
            return kind, _money(m.groups()[-1]) if m.groups() else 0.0
    return None
