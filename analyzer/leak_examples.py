"""Les exemples d'un leak : ses décisions où se joue l'écart, les plus parlantes d'abord, pour entrer dans le détail
d'un leak après ses catégories (app/leaks_page.py).

- Avant le flop (écart d'une fréquence) : ses décisions dans le sens de l'écart, celles que la théorie joue le moins
  ainsi d'abord (un open que la théorie folde à 95 %), avec ce qu'elle fait de sa main.
- Après le flop : ses décisions dans le sens de l'écart, avec ce qu'il avait ; quand il en fait trop (miser, payer,
  relancer), ses mains les plus faibles d'abord ; quand il n'en fait pas assez (checker, folder, payer au lieu de
  relancer), ses mains les plus fortes d'abord.
- Face au solveur : ses décisions de la situation, les plus chères d'abord.
- Une main de départ qui perd : ses coups, les plus coûteux d'abord.

Seules ses mains de la portée comparée à la théorie comptent (contre les réguliers)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import handplay
from .field import intent_of
from .lines import BOARD_SIZE
from .models import BET, CALL, CHECK, FOLD, RAISE
from .theory import review

MAX = 8  # exemples gardés par leak

STRENGTH = {"value": 3, "thin": 2, "semi": 1, "bluff": 0}
ACTION_WORDS = {"raise": "relance", "allin": "tapis", "call": "call", "check": "check", "fold": "fold"}
POSTFLOP_WORDS = {CHECK: "check", CALL: "paie", FOLD: "se couche"}


@dataclass
class Example:
    hand: str                    # la main
    cards: list                  # ses cartes
    board: list                  # le board au moment de la décision
    did: str                     # ce qu'il a fait : « open », « mise 4,5 bb (60 % du pot) », « se couche »
    why: str = ""                # ce qui en fait un exemple : « théorie : fold 92 % », « Paire moyenne »
    value: Optional[float] = None  # bb : le résultat (voir Examples.value_label)
    at: Optional[int] = None     # sa décision dans la main (action), où le replayer se place
    solver: Optional[int] = None  # sa décision au solveur (explorateur, #d=), pour une main analysée


@dataclass
class Examples:
    shown: list = field(default_factory=list)  # les plus parlantes (MAX au plus)
    total: int = 0               # ses décisions de l'écart, en tout
    order: str = ""              # comment elles sont classées, en quelques mots
    value_label: str = ""        # ce que mesure Example.value


def _num(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _mix(theory: Optional[dict]) -> str:
    """« théorie : fold 88 %, open 12 % » : ce que la théorie fait de sa main (actions à 5 % et plus)."""
    if not theory:
        return ""
    groups: dict[str, float] = {}
    for action, freq in theory.items():
        word = ACTION_WORDS.get(action, action)
        groups[word] = groups.get(word, 0.0) + freq
    parts = [f"{word} {round(100 * f)} %" for word, f in sorted(groups.items(), key=lambda x: -x[1]) if f >= 0.05]
    return "théorie : " + ", ".join(parts) if parts else ""


def _shares(theory: Optional[dict]) -> Optional[list[float]]:
    if not theory:
        return None
    out = [0.0, 0.0, 0.0]
    for action, freq in theory.items():
        if action in handplay.GROUPS:
            out[handplay.GROUPS[action]] += freq
    return out


def _preflop_cell(key: str) -> Optional[tuple[str, str, int]]:
    """(situation, position, groupe d'actions de handplay.GROUPS) d'une stat préflop : heads-up (« sb_first.raise »)
    ou tables à plusieurs (« pre:open:CO:open »)."""
    from . import leakcheck, ring_leaks  # ring_leaks importe leaks, qui importe ce module
    if key in leakcheck.HU_PREFLOP:
        return leakcheck.HU_PREFLOP[key]
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "pre":
        return None
    _, situation, position, word = parts
    groups = {(sit, w): group for sit, _, rows in ring_leaks.PREFLOP for group, w, _, _ in rows}
    group = groups.get((situation, word))
    return None if group is None else (situation, position, group)


def preflop(key: str, direction: str, plays: list, hero: str) -> Optional[Examples]:
    """Ses décisions préflop dans le sens de l'écart : celles que la théorie joue le moins ainsi d'abord (« plus » :
    ce qu'il fait en trop), ou le plus (« moins » : ce qu'il ne fait pas assez)."""
    cell = _preflop_cell(key)
    if cell is None:
        return None
    situation, position, group = cell
    more = direction == "plus"
    rows = []
    for p in plays:
        if p.position != position:
            continue
        for d in p.decisions:
            if d.situation != situation or d.action not in handplay.GROUPS:
                continue
            if (handplay.GROUPS[d.action] == group) != more:
                continue
            shares = _shares(d.theory)
            share = shares[group] if shares else None
            rows.append((p, d, share))
    if any(share is not None for _, _, share in rows):  # jugées par la théorie : celles qu'elle joue autrement
        rows = [r for r in rows if r[2] is not None and (r[2] < 0.5 if more else r[2] >= 0.5)]
    if not rows:
        return None
    # des plus éloignées de ce que fait la théorie aux moins éloignées
    rows.sort(key=lambda r: (r[2] is None, (r[2] if more else -r[2]) if r[2] is not None else 0.0,
                             r[0].ev_bb - (r[1].fold_bb or 0.0)))
    words = handplay.WORDS.get(situation, {})
    shown = [Example(p.hand_id, list(p.hand.hole_cards.get(hero, [])), [],
                     words.get(d.action, ACTION_WORDS.get(d.action, d.action)) + f" ({p.combo})", _mix(d.theory),
                     None if d.fold_bb is None else round(p.ev_bb - d.fold_bb, 2), d.index if d.index >= 0 else None)
             for p, d, _ in rows[:MAX]]
    order = ("des mains que la théorie joue le plus souvent autrement, celles qu'elle joue le moins ainsi d'abord"
             if more else "des mains que la théorie joue le plus souvent ainsi, celles qu'elle joue le plus d'abord")
    return Examples(shown, len(rows), order, "Par rapport au fold (EV all-in)")


def _did(point) -> str:
    a = point.action
    if a.kind == BET:
        pct = f" ({round(100 * point.size / point.pot)} % du pot)" if point.pot else ""
        return f"mise {_num(point.size)} bb{pct}" + (" (tapis)" if a.all_in else "")
    if a.kind == RAISE:
        return f"relance de {_num(point.size)} bb" + (" (tapis)" if a.all_in else "")
    return POSTFLOP_WORDS.get(a.kind, a.kind)


def _hit(measure: str, kind: str) -> Optional[bool]:
    """La décision compte-t-elle comme « fait » pour la stat (mise, fold, relance) ? None : hors de la stat (un fold
    ne compte pas parmi les mains qui continuent, pour la relance)."""
    if measure == "bet":
        return kind in (BET, RAISE)
    if measure == "fold":
        return kind == FOLD
    if kind == FOLD:
        return None
    return kind == RAISE


def postflop(measure: str, direction: str, points: list, hero: str) -> Optional[Examples]:
    """Ses décisions après le flop dans le sens de l'écart (measure : « bet », « fold », « raise »)."""
    more = direction == "plus"
    chosen = [p for p in points if p.hand is not None and _hit(measure, p.action.kind) is more]
    if not chosen:
        return None
    # il en fait trop (mise, paie, relance) : ses mains les plus faibles d'abord ; pas assez : les plus fortes
    aggressive = (measure != "fold") == more
    rows = []
    for p in chosen:
        cards = list(p.hand.hole_cards.get(hero, []))
        board = list(p.hand.board[:BOARD_SIZE[p.action.street]])
        intent, holding = (intent_of(cards, board, p.action.street)
                           if len(cards) == 2 and len(board) == BOARD_SIZE[p.action.street] else ("", ""))
        strength = STRENGTH.get(intent, 1.5)
        rows.append((strength if aggressive else -strength, -p.pot, p, cards, board, holding))
    rows.sort(key=lambda r: (r[0], r[1]))
    shown = [Example(p.hand.hand_id, cards, board, _did(p), holding, round(p.value, 2),
                     p.index if p.index >= 0 else None) for _, _, p, cards, board, holding in rows[:MAX]]
    order = "ses mains les plus faibles d'abord" if aggressive else "ses mains les plus fortes d'abord"
    return Examples(shown, len(rows), order + ", puis les plus gros pots", "Rapporté à partir de là")


def solver(family: str, key: str, digests: list[dict]) -> Optional[Examples]:
    """Ses erreurs de la situation face au solveur (EV perdue), les plus chères d'abord."""
    rows = [(review.loss(d), g, d) for g, d in review.decisions_of(digests, "H")
            if g["family"] == family and d["key"] == key and (review.loss(d) or 0.0) >= 0.005]
    if not rows:
        return None
    rows.sort(key=lambda r: -r[0])
    shown = []
    for lost, g, d in rows[:MAX]:
        cards = [d["combo"][:2], d["combo"][2:]] if d.get("combo") else []
        parts = sorted(zip(d.get("strategy") or [], (a["label"] for a in d["actions"])), reverse=True)
        mix = ", ".join(f"{label} {round(100 * f)} %" for f, label in parts if f >= 0.05)
        shown.append(Example(g["hand"], cards, list(d.get("board") or []), d["played"],
                             f"solveur : {mix}" if mix else "", -round(lost, 2),
                             solver=d["d"]))
    return Examples(shown, len(rows), "les plus chères d'abord", "EV perdue face au solveur")


def hand_loser(x, plays: list, hero: str) -> Optional[Examples]:
    """Les coups d'une main de départ (ou d'une famille) jouée ainsi qui perd : les plus coûteux d'abord
    (handplay.Loser)."""
    rows = [(p, d) for p in plays if p.position == x.position and x.name in (p.combo, handplay.family(p.combo))
            for d in p.decisions if d.situation == x.situation and d.action == x.action and d.fold_bb is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: r[0].ev_bb - r[1].fold_bb)
    word = handplay.WORDS.get(x.situation, {}).get(x.action, x.action)
    shown = [Example(p.hand_id, list(p.hand.hole_cards.get(hero, [])), list(p.hand.board), f"{word} ({p.combo})",
                     handplay.POT_SOURCES.get(p.outcome.pot, "") if p.outcome is not None else "",
                     round(p.ev_bb - d.fold_bb, 2), d.index if d.index >= 0 else None)
             for p, d in rows[:MAX]]
    return Examples(shown, len(rows), "les plus coûteux d'abord", "Par rapport au fold (EV all-in)")
