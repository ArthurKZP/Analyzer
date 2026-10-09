"""Parseur des historiques au format PokerStars : PokerStars (Zoom compris), GGPoker (« Poker Hand #… ») et HHPoker
(« PokerMaster Hand #… »), cash game NLHE en argent réel, toutes tailles de table.

Particularités du format : « raises X to Y » (Y : la mise totale sur la street) ; une mise non payée est rendue
(« Uncalled bet (X) returned to … ») et le pot total du résumé est brut, rake compris (chez GGPoker s'y ajoutent les
frais Jackpot, Bingo, Fortune et Tax) ; les gains se lisent sur « … collected X from pot ». GGPoker écrit
« *** SHOWDOWN *** » même sans abattage, et son héros s'appelle « Hero ». Une main jouée plusieurs fois (« run it
twice ») garde son premier tableau, ses gains s'additionnent. L'EV Cashout de GGPoker corrige le gain du joueur : la
prime payée (« Pays Cashout Risk ») s'en retire, le montant reçu (« Receives Cashout ») s'y ajoute. Avec l'All-in Cash
Out de PokerStars, le joueur reçoit le montant encaissé (« cashed out the hand for X ») et, s'il gagne, pas le pot.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..models import BET, CALL, CHECK, FOLD, POST_ANTE, POST_BB, POST_SB, RAISE, Action, Hand, Seat

SITES = {"PokerStars": "PokerStars", "Poker": "GGPoker", "PokerMaster": "HHPoker"}
_START_RE = re.compile(r"^(?:PokerStars|PokerMaster|Poker)(?: Zoom)? Hand #", re.M)

_NUM = r"([\d,]+(?:\.\d+)?)"
_MONEY = r"[€$£]?" + _NUM
_HEADER_RE = re.compile(r"^(PokerStars|PokerMaster|Poker)(?: Zoom)? Hand #(\w+):\s+(.*?)\s+\(([^)]*)\)\s+-\s+"
                        r"(\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2})")
_TABLE_RE = re.compile(r"^Table '(.*?)' (\d+)-max .*?Seat #(\d+) is the button")
_SEAT_RE = re.compile(r"^Seat (\d+): (.+?) \(" + _MONEY + r" in chips(?:, [^)]*)?\)(.*)$")
_DEALT_RE = re.compile(r"^Dealt to (.+?)(?: \[([^\]]*)\])?$")
_STREET_RE = re.compile(r"^\*\*\* (FIRST |SECOND |THIRD )?(FLOP|TURN|RIVER) \*\*\* ((?:\[[^\]]*\] ?)+)")
_SHOWS_RE = re.compile(r"^(.+?): shows \[([^\]]*)\](?: \((.*)\))?$")
_SUMMARY_CARDS_RE = re.compile(r"^Seat \d+: (.+?) (?:\([^)]*\) )?(?:showed|mucked) \[([^\]]*)\]")
_COLLECTED_RE = re.compile(r"^(.+?) collected " + _MONEY + r" from ")
_CASHOUT_RE = re.compile(r"^(.+?): (Pays Cashout Risk|Receives Cashout) \(" + _MONEY + r"\)")
_CASHED_RE = re.compile(r"^(.+?) cashed out the hand for " + _MONEY)
_TOTAL_RE = re.compile(r"^Total pot " + _MONEY)
_FEE_RE = re.compile(r"\| (?:Rake|Jackpot|Bingo|Fortune|Tax|JP Fee) " + _MONEY)

_LIVE = "live_post"  # une blinde vivante qui n'est pas la grosse blinde (straddle, retour à la table)
_BOTH = "both_blinds"  # retour à la table : la grosse blinde (vivante) et la petite (morte) d'un coup
_ACTIONS = [
    (POST_SB, re.compile(r"^posts small blind " + _MONEY)),
    (POST_BB, re.compile(r"^posts big blind " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts (?:the )?ante " + _MONEY)),
    (POST_ANTE, re.compile(r"^posts (?:missed|dead) blind " + _MONEY)),  # mise morte
    (_LIVE, re.compile(r"^posts straddle " + _MONEY)),
    (_BOTH, re.compile(r"^posts small & big blinds " + _MONEY)),
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
    for chunk in re.split(r"\n(?=(?:PokerStars|PokerMaster|Poker)(?: Zoom)? Hand #)", text.lstrip("﻿")):
        if _HEADER_RE.match(chunk.strip()):
            hand = parse_hand(chunk)
            if hand is not None:
                yield hand


def parse_hand(chunk: str) -> Hand | None:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    head = _HEADER_RE.match(lines[0])
    if not head or "Hold'em No Limit" not in head.group(3) or "Tournament" in head.group(3):
        return None  # le cash game en hold'em sans limite seulement
    blinds = [_money(x) for x in re.findall(_NUM, head.group(4))]
    if len(blinds) < 2 or not re.search(r"[€$£]|USD|EUR|GBP", head.group(4)):  # sans monnaie : de l'argent fictif
        return None
    hand = Hand(site=SITES[head.group(1)], hand_id=head.group(2), table_id="", game_name=head.group(3).strip(),
                date=datetime.strptime(head.group(5), "%Y/%m/%d %H:%M:%S"), sb=blinds[-2], bb=blinds[-1],
                total_pot=0.0, rake=0.0)
    button_seat = 0
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
                if m.group(1) in ("SECOND ", "THIRD "):  # un autre tableau : on garde le premier
                    continue
                street = m.group(2).lower()
                hand.board = " ".join(re.findall(r"\[([^\]]*)\]", m.group(3))).split()
                committed = dict.fromkeys(committed, 0.0)
                section = "actions"
            else:
                title = line.strip("* ").strip()
                if title == "HOLE CARDS":
                    section = "actions"
                elif title == "SUMMARY":
                    section = "summary"
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
                hand.total_pot = _money(m.group(1))
                hand.rake = round(sum(_money(x) for x in _FEE_RE.findall(line)), 2)
                continue
            m = _SUMMARY_CARDS_RE.match(line)
            if m and m.group(1) in listed and len(m.group(2).split()) == 2:
                hand.hole_cards.setdefault(m.group(1), m.group(2).split())
            continue
        m = _COLLECTED_RE.match(line)
        if m and m.group(1) in listed:
            hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + _money(m.group(2)), 2)
            continue
        m = _DEALT_RE.match(line)
        if m and m.group(1) in listed:
            if m.group(2):  # « Dealt to X [Ah Kd] » : le héros (GGPoker sert aussi les autres, sans les cartes)
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
        m = _CASHOUT_RE.match(line)
        if m and m.group(1) in listed:
            sign = -1 if m.group(2).startswith("Pays") else 1
            hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + sign * _money(m.group(3)), 2)
            continue
        m = _CASHED_RE.match(line)
        if m and m.group(1) in listed:  # « All-in Cash Out » de PokerStars : le montant reçu (frais déduits)
            hand.winnings[m.group(1)] = round(hand.winnings.get(m.group(1), 0.0) + _money(m.group(2)), 2)
            continue
        player = next((p for p in names if line.startswith(p + ": ")), None)
        if player is None:
            continue
        text = line[len(player) + 2:]
        parsed = _action(text)
        if parsed is None:
            continue
        kind, value = parsed
        before = committed.get(player, 0.0)
        facing = max(max(committed.values(), default=0.0) - before, 0.0)
        if kind == POST_BB and any(a.kind == POST_BB for a in hand.actions):
            kind = _LIVE  # une deuxième grosse blinde : un joueur qui revient à la table
        elif kind == POST_SB and any(a.kind == POST_SB for a in hand.actions):
            kind = POST_ANTE  # une deuxième petite blinde : celle qu'il a manquée, morte
        if kind == RAISE:
            to, amount = value, value - before
        elif kind in (CALL, BET, POST_SB, POST_BB):
            amount, to = value, before + value
        elif kind == _LIVE:  # gardée comme une mise forcée : les positions viennent des vraies blindes
            kind, amount, to = POST_ANTE, value, before + value
        elif kind == _BOTH:  # seule la grosse blinde compte dans sa mise
            kind, amount, to = POST_ANTE, value, before + min(value, hand.bb)
        elif kind == POST_ANTE:  # l'ante et les blindes mortes ne comptent pas dans la mise à suivre
            amount, to = value, before
        else:
            amount, to = 0.0, before
        hand.actions.append(Action(player=player, kind=kind, street=street, amount=round(amount, 2),
                                   to=round(to, 2), all_in="all-in" in text, pot_before=round(pot, 2),
                                   facing=round(facing, 2)))
        acted.add(player)
        committed[player] = to
        pot += amount
    # les joueurs servis : ceux qui ont agi (blindes comprises), le héros, et qui a gagné
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
