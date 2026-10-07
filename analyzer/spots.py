"""Données du visualiseur de spots : une fiche par main, avec tout ce qu'il faut pour
la filtrer (type de pot, agresseur, position, actions par street, lignes) et la rejouer.

Montants exprimés en big blinds. « H » = toi, « V » = l'adversaire.
"""
from __future__ import annotations

from typing import Optional

from .cards import combo_notation, describe_holding, equity
from .lines import BOARD_SIZE, line_label
from .models import BET, CALL, CHECK, FOLD, POSTFLOP, RAISE, VOLUNTARY, Hand
from .stats import HandReader, think_times

STREET_CODES = {"preflop": "p", "flop": "f", "turn": "t", "river": "r"}
PREFLOP_EQUITY_SAMPLES = 1000  # estimation préflop (± 1,5 %), exacte ensuite


def line_tag(player: str, street: str, label: str, size: str) -> str:
    """Identifiant d'une ligne, partagé entre le rapport et le visualiseur."""
    return f"{player}|{street}|{label}|{size}"


def _street_codes(hand: Hand, player: str, street: str) -> list[str]:
    """Ce qu'a fait le joueur sur la street : bet, raise, call, fold, xr, xc, xf, xx."""
    kinds = [a.kind for a in hand.actions if a.street == street and a.player == player and a.kind in VOLUNTARY]
    codes = {k for k in kinds if k in (BET, RAISE, CALL, FOLD)}
    if kinds and kinds[0] == CHECK:
        follow = {RAISE: "xr", CALL: "xc", FOLD: "xf"}
        codes |= {follow[k] for k in kinds[1:] if k in follow}
        if all(k == CHECK for k in kinds):
            codes.add("xx")
    return sorted(codes)


def _cbets(reader: HandReader) -> dict[str, int]:
    """Opportunités de c-bet de l'agresseur préflop : 1 = misé, 0 = checké."""
    out = {}
    if reader.pfa is None:
        return out
    for key, made in reader.events.get(reader.pfa, []):
        if key in ("cbet_flop", "cbet_turn", "cbet_river"):
            out[STREET_CODES[key.split("_")[1]]] = int(made)
    return out


def _pot_type(hand: Hand, reader: HandReader) -> str:
    """Type de pot ; « walk » quand le bouton folde d'entrée (pas un pot limpé)."""
    first = next((a for a in hand.actions if a.kind in VOLUNTARY), None)
    if reader.pf_raises == 0 and first is not None and first.kind == FOLD:
        return "walk"
    return reader.pot_type


def _end(hand: Hand, tags: dict[str, str]) -> str:
    if hand.showdown:
        return "sd"
    fold = next((a for a in hand.actions if a.kind == FOLD), None)
    if fold is None:
        return "sd"
    return ("hf" if tags[fold.player] == "H" else "vf") + STREET_CODES[fold.street]


def _equities(hand: Hand, hero: str, villain: str) -> dict[str, float]:
    out = {}
    for street, n in (("p", 0), ("f", 3), ("t", 4), ("r", 5)):
        if len(hand.board) >= n:
            eq = equity(hand.hole_cards[hero], hand.hole_cards[villain], hand.board[:n],
                        samples=PREFLOP_EQUITY_SAMPLES, seed=hand.hand_id)
            out[street] = round(eq, 3)
    return out


def _descriptions(cards: list[str], board: list[str]) -> dict[str, str]:
    return {STREET_CODES[s]: describe_holding(cards, board[:n])
            for s, n in BOARD_SIZE.items() if len(board) >= n}


def hand_record(hand: Hand, hero: str, villain: str) -> dict:
    tags = {hero: "H", villain: "V"}
    bb = hand.bb
    reader = HandReader.of(hand)
    times = think_times(hand)
    hero_cards = hand.hole_cards.get(hero, [])
    villain_cards = hand.hole_cards.get(villain, []) if hand.showdown else []

    actions = []
    for a, t in zip(hand.actions, times):
        actions.append({
            "s": STREET_CODES[a.street], "p": tags[a.player], "k": a.kind,
            "a": round(a.amount / bb, 2), "to": round(a.to / bb, 2), "pot": round(a.pot_before / bb, 2),
            "ai": a.all_in, "t": t,
        })

    line_tags = []
    for i, a in enumerate(hand.actions):
        if a.street == "preflop" or a.kind not in (BET, RAISE):
            continue
        label, size = line_label(hand, i, reader.pfa)
        who = tags[a.player]
        if who == "H":
            label = label.replace("sur ton check", "sur son check")
        pooled = ("Toutes ses " if who == "V" else "Toutes tes ") + ("relances" if a.kind == RAISE else "mises")
        line_tags += [line_tag(who, a.street, label, size), line_tag(who, a.street, pooled, size)]

    record = {
        "id": hand.hand_id,
        "opp": villain,
        "d": hand.date.strftime("%d/%m %H:%M"),
        "ts": hand.date.strftime("%Y-%m-%d %H:%M:%S"),
        "g": hand.game_name,
        "bb": bb,
        "hp": "BTN" if hand.button == hero else "BB",
        "hc": hero_cards,
        "vc": villain_cards,
        "hn": combo_notation(hero_cards) if len(hero_cards) == 2 else "",
        "vn": combo_notation(villain_cards) if len(villain_cards) == 2 else "",
        "hs": round(hand.seats[hero].stack / bb, 2),
        "vs": round(hand.seats[villain].stack / bb, 2),
        "b": hand.board,
        "x": actions,
        "pt": _pot_type(hand, reader),
        "pfa": tags.get(reader.pfa, "") if reader.pfa else "",
        "hl": reader.preflop_line(hero),
        "vl": reader.preflop_line(villain),
        "reach": {0: 0, 3: 1, 4: 2, 5: 3}.get(len(hand.board), 0),
        "sa": {STREET_CODES[s]: {"H": _street_codes(hand, hero, s), "V": _street_codes(hand, villain, s)}
               for s in POSTFLOP},
        "cb": _cbets(reader),
        "tags": sorted(set(line_tags)),
        "end": _end(hand, tags),
        "ai": any(a.all_in for a in hand.actions),
        "net": round(hand.net(hero) / bb, 2),
        "tot": round(hand.total_pot / bb, 2),
        "rake": round(hand.rake / bb, 2),
        "win": {tags[p]: round(v / bb, 2) for p, v in hand.winnings.items() if p in tags},
        "ret": {tags[p]: round(v / bb, 2) for p, v in hand.uncalled.items() if p in tags and v},
        "hd": _descriptions(hero_cards, hand.board) if len(hero_cards) == 2 else {},
        "vd": _descriptions(villain_cards, hand.board) if villain_cards else {},
        "eq": _equities(hand, hero, villain) if villain_cards and len(hero_cards) == 2 else {},
    }
    return record


def spot_records(hands: list[Hand], hero: str, villain: Optional[str] = None) -> list[dict]:
    """Fiches des mains contre `villain`, ou contre tous tes adversaires si villain=None."""
    records = []
    for h in hands:
        if hero not in h.seats or len(h.seats) != 2 or not h.button or not h.bb:
            continue
        opponent = h.opponent_of(hero)
        if villain is None or opponent == villain:
            records.append(hand_record(h, hero, opponent))
    return records


def line_options(records: list[dict]) -> list[tuple[str, int]]:
    """Toutes les lignes présentes, avec leur nombre de mains, dans un ordre lisible."""
    counts: dict[str, int] = {}
    for rec in records:
        for tag in rec["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    order = {"flop": 0, "turn": 1, "river": 2}

    def key(item: tuple[str, int]) -> tuple:
        who, street, label, _ = item[0].split("|")
        return (who != "V", order[street], not label.startswith("Toutes"), -item[1])

    return sorted(counts.items(), key=key)


def find_hand(records: list[dict], hand_id: str) -> Optional[dict]:
    return next((r for r in records if r["id"] == hand_id), None)
