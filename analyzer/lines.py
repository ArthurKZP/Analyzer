"""Lignes de value / lignes de bluff de l'adversaire.

Chaque mise ou relance postflop de l'adversaire est rangée dans une « ligne »
(street, contexte, taille). Pour les mains allées à l'abattage on connaît sa main
et la tienne : on classe alors son intention au moment de la mise.

- Value : top paire ou mieux.
- Value fine : paire moyenne ou faible.
- Semi-bluff : rien de fait mais au moins 25 % d'équité contre ta main (flop, turn).
- Bluff : rien de fait et moins de 25 % d'équité.

Biais à garder en tête : une ligne n'est vue que si le coup va à l'abattage.
À la river, ta décision de payer ne dépend pas de ses cartes : les mains vues
quand tu paies sont donc un échantillon honnête de sa ligne. Au flop et au turn,
les mains vues excluent celles où tu as payé puis foldé plus tard.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .cards import describe_holding, equity
from .insights import strength_class, wilson
from .models import BET, CALL, CHECK, FOLD, POSTFLOP, RAISE, Hand
from .stats import HandReader, Ratio, think_times

INTENTS = ("bluff", "semi", "thin", "value")
HOLDINGS = ("rien", "tirage", "paire", "fort")
HOLDING_LABELS = {"rien": "rien", "tirage": "tirage", "paire": "paire faible/moyenne", "fort": "top paire ou mieux"}
INTENT_LABELS = {"bluff": "Bluff", "semi": "Semi-bluff", "thin": "Value fine", "value": "Value"}
BOARD_SIZE = {"flop": 3, "turn": 4, "river": 5}
SEMI_BLUFF_EQUITY = 0.25
SIZE_CLASSES = [(55, "petite (≤ 55 %)"), (120, "grosse (56–120 %)"), (float("inf"), "overbet (> 120 %)")]
PREVIOUS_PHRASE = {
    BET: "Barrel (a misé le {prev})",
    CHECK: "Mise après check au {prev}",
    CALL: "Mise après call au {prev}",
    RAISE: "Mise après relance au {prev}",
}


def size_class(pct: float) -> str:
    return next(label for limit, label in SIZE_CLASSES if pct <= limit)


def holding_class(cards: list[str], board: list[str]) -> str:
    """Force simplifiée de ta main : rien, tirage, paire (faible/moyenne), fort (top paire ou mieux)."""
    strength = strength_class(describe_holding(cards, board))
    return {"Rien": "rien", "Tirage": "tirage", "Paire moyenne/faible": "paire"}.get(strength, "fort")


def classify(villain_cards: list[str], hero_cards: list[str], board: list[str]) -> tuple[str, float, str]:
    """(intention, équité de l'adversaire contre ta main, description de sa main)."""
    description = describe_holding(villain_cards, board)
    strength = strength_class(description)
    eq = equity(villain_cards, hero_cards, board)
    if strength in ("Top paire / overpair", "Deux paires +"):
        intent = "value"
    elif strength == "Paire moyenne/faible":
        intent = "thin"
    elif len(board) < 5 and eq >= SEMI_BLUFF_EQUITY:
        intent = "semi"
    else:
        intent = "bluff"
    return intent, eq, description


@dataclass
class Seen:
    """Une mise de la ligne dont on a vu les cartes à l'abattage."""
    hand: Hand
    intent: str
    equity: float  # équité de l'adversaire contre ta main au moment de la mise
    description: str
    hero_description: str
    hero_reply: Optional[str]
    think: Optional[float]


@dataclass
class Line:
    street: str
    label: str
    size: str = ""
    count: int = 0
    replies: Counter = field(default_factory=Counter)
    required: list = field(default_factory=list)  # équité nécessaire pour payer, par occurrence
    seen: list = field(default_factory=list)
    fold_holdings: Counter = field(default_factory=Counter)  # ta main quand tu as foldé

    @property
    def name(self) -> str:
        return " · ".join(part for part in (self.label, self.size) if part)

    @property
    def intents(self) -> Counter:
        return Counter(s.intent for s in self.seen)

    @property
    def fold_rate(self) -> Optional[float]:
        faced = sum(self.replies.values())
        return self.replies[FOLD] / faced if faced else None


def _previous_action(hand: Hand, player: str, street: str) -> Optional[str]:
    prev = POSTFLOP[POSTFLOP.index(street) - 1]
    kinds = [a.kind for a in hand.actions if a.street == prev and a.player == player]
    for kind in (RAISE, BET, CALL, CHECK):
        if kind in kinds:
            return kind
    return None


def line_label(hand: Hand, index: int, pfa: Optional[str]) -> tuple[str, str]:
    """(libellé de la ligne, taille) pour l'action agressive hand.actions[index]."""
    a = hand.actions[index]
    before = [b for b in hand.actions[:index] if b.street == a.street]
    checked_first = any(b.player == a.player and b.kind == CHECK for b in before)
    if a.kind == RAISE:
        return ("Check-raise" if checked_first else "Relance"), ""
    size = size_class(100.0 * a.amount / a.pot_before) if a.pot_before else ""
    if a.street == "flop":
        if pfa is None:
            label = "Mise (pot limpé)"
        elif pfa == a.player:
            label = "C-bet"
        elif not before:
            label = "Donk"
        else:
            label = "Mise sur ton check"
        return label, size
    prev = POSTFLOP[POSTFLOP.index(a.street) - 1]
    previous = _previous_action(hand, a.player, a.street)
    phrase = PREVIOUS_PHRASE.get(previous, "Mise")
    return phrase.format(prev=prev), size


def _hero_reply(hand: Hand, index: int, hero: str) -> Optional[str]:
    a = hand.actions[index]
    for b in hand.actions[index + 1:]:
        if b.street != a.street:
            return None
        if b.player == hero and b.kind in (FOLD, CALL, RAISE):
            return b.kind
    return None


def _required_equity(hand: Hand, index: int, hero: str) -> float:
    """Équité dont tu as besoin pour payer cette mise (cotes du pot)."""
    a = hand.actions[index]
    committed = sum(b.amount for b in hand.actions[:index] if b.street == a.street and b.player == hero)
    to_call = max(a.to - committed, 0.0)
    pot_after = a.pot_before + a.amount
    return to_call / (pot_after + to_call) if to_call else 0.0


def villain_lines(hands: list[Hand], villain: str, hero: str) -> list[Line]:
    lines: dict[tuple[str, str, str], Line] = {}
    for h in hands:
        if villain not in h.seats or hero not in h.seats:
            continue
        pfa = HandReader(h).pfa
        times = think_times(h)
        known = h.showdown and len(h.hole_cards.get(villain, [])) == 2 and len(h.hole_cards.get(hero, [])) == 2
        for i, a in enumerate(h.actions):
            if a.player != villain or a.street == "preflop" or a.kind not in (BET, RAISE):
                continue
            label, size = line_label(h, i, pfa)
            line = lines.setdefault((a.street, label, size), Line(a.street, label, size))
            line.count += 1
            reply = _hero_reply(h, i, hero)
            if reply:
                line.replies[reply] += 1
            if reply == FOLD and len(h.hole_cards.get(hero, [])) == 2:
                line.fold_holdings[holding_class(h.hole_cards[hero], h.board[: BOARD_SIZE[a.street]])] += 1
            line.required.append(_required_equity(h, i, hero))
            if known:
                board = h.board[: BOARD_SIZE[a.street]]
                intent, eq, desc = classify(h.hole_cards[villain], h.hole_cards[hero], board)
                line.seen.append(Seen(h, intent, eq, desc, describe_holding(h.hole_cards[hero], board), reply, times[i]))
    order = {s: i for i, s in enumerate(POSTFLOP)}
    return sorted(lines.values(), key=lambda ln: (order[ln.street], -ln.count))


def pooled_by_size(lines: list[Line]) -> list[Line]:
    """Toutes lignes confondues, regroupées par street et par taille (échantillons plus gros)."""
    pooled: dict[tuple[str, str], Line] = {}
    for ln in lines:
        label = "Toutes ses relances" if not ln.size else "Toutes ses mises"
        key = (ln.street, ln.size)
        pool = pooled.setdefault(key, Line(ln.street, label, ln.size))
        pool.count += ln.count
        pool.replies.update(ln.replies)
        pool.required.extend(ln.required)
        pool.seen.extend(ln.seen)
        pool.fold_holdings.update(ln.fold_holdings)
    order = {s: i for i, s in enumerate(POSTFLOP)}
    sizes = {label: i for i, (_, label) in enumerate(SIZE_CLASSES)}
    return sorted(pooled.values(), key=lambda ln: (order[ln.street], sizes.get(ln.size, 99)))


@dataclass
class Verdict:
    kind: str  # "value", "bluff", "semi-bluff", "mixte", "inconnue"
    confidence: str  # "fiable", "indicatif", "à confirmer", ""
    reading: str  # ce que la ligne représente
    decision: str  # conséquence pour tes calls


def verdict(line: Line) -> Verdict:
    n = len(line.seen)
    faced = sum(line.replies.values())
    required = 100 * median(line.required) if line.required else 0.0
    if n == 0:
        return Verdict("inconnue", "", "Jamais vue à l'abattage", _fold_advice(line) if faced else "")

    c = line.intents
    bluffy = c["bluff"] + c["semi"]
    share = bluffy / n
    if n < 3:
        kind = "peu vue"
    elif share <= 0.25:
        kind = "value"
    elif share >= 0.6:
        kind = "semi-bluff" if c["semi"] > c["bluff"] else "bluff"
    else:
        kind = "mixte"
    confidence = "fiable" if n >= 8 else "indicatif" if n >= 4 else ""
    reading = f"{bluffy}/{n} sans main faite" + (f" (dont {c['semi']} gros tirages)" if c["semi"] else "")

    called = [s for s in line.seen if s.hero_reply == CALL]
    if line.street == "river":
        lo, hi = wilson(Ratio(c["bluff"], n))
        if lo > required:
            decision = f"Paye tes bluff-catchers : il bluffe au moins {lo:.0f} % du temps, il en faut {required:.0f} %."
        elif hi < required:
            decision = f"Folder tes bluff-catchers est correct : au plus {hi:.0f} % de bluffs, il en faut {required:.0f} %."
        else:
            decision = (f"Pas encore tranché : {c['bluff']}/{n} bluffs vus, il en faut {required:.0f} % "
                        "pour payer un bluff-catcher.")
        if called:
            won = sum(s.equity < 0.5 for s in called)
            decision += f" Quand tu as payé, tu as gagné {won}/{len(called)} fois."
    else:
        if called:
            hero_eq = 100 * sum(1 - s.equity for s in called) / len(called)
            decision = (f"Quand tu as payé, ton équité moyenne était de {hero_eq:.0f} % ({len(called)} main(s)) "
                        f"pour {required:.0f} % nécessaires.")
        else:
            decision = ""
    advice = _fold_advice(line)
    if advice and line.fold_rate is not None and line.fold_rate >= 0.5:
        decision = f"{decision} {advice}".strip()
    return Verdict(kind, confidence, reading, decision)


def _fold_advice(line: Line) -> str:
    """Tes folds sur cette ligne étaient-ils forcés (rien en main) ?"""
    folds = sum(line.fold_holdings.values())
    if not folds:
        return ""
    made = line.fold_holdings["paire"] + line.fold_holdings["fort"]
    empty = line.fold_holdings["rien"]
    if made == 0:
        return f"Tes {folds} folds étaient sans paire : rien à te reprocher."
    if line.fold_holdings["fort"] and not line.seen:
        return (f"Tu as foldé {line.fold_holdings['fort']} fois avec top paire ou mieux sans jamais voir ce qu'il avait : "
                "c'est la ligne à tester avec tes meilleurs bluff-catchers.")
    if empty >= 0.7 * folds:
        return f"{empty}/{folds} de tes folds étaient sans paire ni tirage : des folds forcés."
    return f"{made}/{folds} de tes folds avaient une paire ou mieux."


def passive_showdowns(hands: list[Hand], villain: str, hero: str) -> dict[str, Counter]:
    """Ce qu'il montre quand il checke une street (pour repérer ses pièges)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for h in hands:
        if not (h.showdown and len(h.hole_cards.get(villain, [])) == 2 and len(h.hole_cards.get(hero, [])) == 2):
            continue
        for street in POSTFLOP:
            acts = [a for a in h.actions if a.street == street and a.player == villain]
            if acts and acts[0].kind == CHECK and not any(a.kind in (BET, RAISE) for a in acts):
                board = h.board[: BOARD_SIZE[street]]
                out[street][classify(h.hole_cards[villain], h.hole_cards[hero], board)[0]] += 1
    return out


def think_by_intent(lines: list[Line]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for line in lines:
        for s in line.seen:
            if s.think is not None:
                out[s.intent].append(s.think)
    return out


def fold_holdings_by_street(lines: list[Line]) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for line in lines:
        out[line.street].update(line.fold_holdings)
    return out


def check_ranges(hands: list[Hand], player: str, opponent: str) -> dict[str, dict]:
    """Ta main quand tu checkes en premier sur une street, et ce que fait l'adversaire derrière."""
    out: dict[str, dict] = {s: {"holdings": Counter(), "bets": 0, "faced": 0} for s in POSTFLOP}
    for h in hands:
        cards = h.hole_cards.get(player, [])
        if len(cards) != 2:
            continue
        for street in POSTFLOP:
            acts = [a for a in h.actions if a.street == street]
            if not acts:
                break
            level = 0
            for i, a in enumerate(acts):
                if a.player == player and level == 0:
                    if a.kind == CHECK:
                        cell = out[street]
                        cell["holdings"][holding_class(cards, h.board[: BOARD_SIZE[street]])] += 1
                        reply = next((b for b in acts[i + 1:] if b.player == opponent), None)
                        if reply is not None:
                            cell["faced"] += 1
                            cell["bets"] += reply.kind == BET
                    break
                if a.kind in (BET, RAISE):
                    level += 1
    return out


def _summary(hand: Hand, player: str, street: str) -> Optional[str]:
    kinds = [a.kind for a in hand.actions if a.street == street and a.player == player]
    for kind in (RAISE, BET, CALL, CHECK):
        if kind in kinds:
            return kind
    return None


STREET_STORY = {
    (CALL, BET): "tu paies sa mise",
    (BET, CALL): "il paie ta mise",
    (CHECK, CHECK): "check-check",
    (RAISE, BET): "tu relances sa mise",
    (BET, RAISE): "il relance ta mise",
    (CALL, RAISE): "tu paies sa relance",
    (RAISE, CALL): "il paie ta relance",
    (RAISE, RAISE): "relances des deux côtés",
}


def nonshowdown_losses(hands: list[Hand], hero: str) -> dict:
    """Résultat des mains finies sans abattage : par street et par déroulé quand tu folds."""
    by_street = {s: {"hero_folds": [0, 0.0], "villain_folds": [0, 0.0]} for s in ("preflop",) + POSTFLOP}
    stories: dict[str, list] = defaultdict(lambda: [0, 0.0, Counter()])
    for h in hands:
        if h.showdown:
            continue
        fold = next((a for a in h.actions if a.kind == FOLD), None)
        if fold is None:
            continue
        net_bb = h.net(hero) / h.bb
        cell = by_street[fold.street]["hero_folds" if fold.player == hero else "villain_folds"]
        cell[0] += 1
        cell[1] += net_bb
        if fold.player == hero and fold.street in ("turn", "river"):
            opponent = h.opponent_of(hero)
            parts = []
            for street in POSTFLOP[: POSTFLOP.index(fold.street)]:
                key = (_summary(h, hero, street), _summary(h, opponent, street))
                parts.append(f"{street.capitalize()} : {STREET_STORY.get(key, 'autre')}")
            parts.append(f"{fold.street.capitalize()} : tu folds")
            story = stories[" · ".join(parts)]
            story[0] += 1
            story[1] += net_bb
            if len(h.hole_cards.get(hero, [])) == 2:
                story[2][holding_class(h.hole_cards[hero], h.board[: BOARD_SIZE[fold.street]])] += 1
    ranked = sorted(stories.items(), key=lambda kv: kv[1][1])
    return {"by_street": by_street, "stories": ranked}
