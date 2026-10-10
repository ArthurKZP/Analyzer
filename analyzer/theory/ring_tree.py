"""L'arbre préflop d'une table 6-max d'après tes charts, nœud par nœud, au format des nœuds de l'explorateur (comme
preflop_tree pour le heads-up) : de quoi étudier un coup à plusieurs jusqu'au flop, puis le résoudre au postflop.

Une ligne est la suite des décisions dans l'ordre de parole (UTG, HJ, CO, BTN, SB, BB) : « fold », « raise » (open,
3bet, 4bet) ou « call ». Les charts couvrent les pots à deux joueurs : premier à parler, open ou fold ; face à l'open,
fold, call ou 3bet (le premier qui paie ou relance reste seul face à l'ouvreur, les suivants se couchent) ; face au
3bet, l'ouvreur folde, paie ou 4bette ; face au 4bet, le 3bettor folde ou paie. Un call mène au flop, dont on choisit
les cartes : le spot d'étude de la famille « 6max_<hors de position>_<en position>_<pot> » (studyspots).

Tailles comme les spots d'étude 6-max : open à 2,5 bb, 3bet à 7,5 bb en position et 10 bb hors de position, 4bet à
22 bb en position et 20 bb hors de position, 100 bb. Les deux joueurs du nœud : 0 = celui qui sera hors de position
au flop, 1 = l'autre ; tant que personne n'a ouvert, seul celui qui parle (0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import handplay
from ..errors import NotFound
from . import ring_ranges, studyspots
from .preflop_tree import MIN_REACH, classes, combos

TABLE = "6-max"
ORDER = studyspots.SEATS_PREFLOP
ACTIONS = ("fold", "call", "raise")  # ordre d'affichage, du plus passif au plus agressif
STACK = 100.0
BLINDS = {"SB": 0.5, "BB": 1.0}


class NoCharts(ValueError):
    """Pas de charts 6-max : l'arbre ne peut pas se construire."""


def _ip(a: str, b: str) -> str:
    """Celui des deux qui sera en position au flop."""
    return max((a, b), key=studyspots.SEATS_POSTFLOP.index)


def _of(pos: str) -> str:
    return studyspots._de(pos)


@dataclass
class State:
    """Où en est la ligne : qui a ouvert, relancé, payé ; ce que chacun a mis ; la part de chaque main encore là."""
    next: int = 0                       # prochain à parler, au premier tour (index dans ORDER)
    opener: Optional[str] = None
    threebettor: Optional[str] = None
    fourbet: bool = False
    caller: Optional[str] = None
    end: Optional[str] = None           # « terminal_fold » ou « flop »
    puts: dict = field(default_factory=lambda: dict(BLINDS))
    reach: dict = field(default_factory=dict)  # position -> {classe: poids}

    def weights(self, pos: str) -> dict[str, float]:
        return self.reach.setdefault(pos, dict.fromkeys(classes(), 1.0))


@dataclass
class Decision:
    actor: str
    situation: str                      # open, vs_open, vs_3bet, vs_4bet
    other: Optional[str]                # l'ouvreur, le 3bettor ou le 4bettor qu'il affronte
    strategies: dict                    # classe -> {action: fréquence}
    keys: list

    @property
    def label(self) -> str:
        if self.situation == "open":
            return f"{self.actor}, premier à parler"
        word = {"vs_open": "l'open", "vs_3bet": "au 3bet", "vs_4bet": "au 4bet"}[self.situation]
        return (f"{self.actor} face à {word} {_of(self.other)}" if self.situation == "vs_open"
                else f"{self.actor} face {word} {_of(self.other)}")


def _vs4bet(lines: dict, threebettor: str, opener: str, cls: str) -> Optional[dict[str, float]]:
    """Le 3bettor face au 4bet : le call de la ligne du pot 4bet, rapporté à sa range de 3bet (pas de 5bet dans les
    charts)."""
    three = handplay._range(lines, f"{opener}:raise {threebettor}:raise {opener}:call", threebettor) or {}
    four = handplay._range(lines, f"{opener}:raise {threebettor}:raise {opener}:raise {threebettor}:call", threebettor)
    if four is None or not three.get(cls):
        return None
    call = min(1.0, four.get(cls, 0.0) / three[cls])
    return {"call": call, "fold": 1 - call}


def _strategy(lines: dict, situation: str, actor: str, other: Optional[str], cls: str) -> dict[str, float]:
    if situation == "open":
        found = handplay._chart_open(lines, actor, cls)
    elif situation == "vs_open":
        found = handplay._chart_vs_open(lines, other, actor, cls)
    elif situation == "vs_3bet":
        found = handplay._chart_vs_3bet(lines, actor, other, cls)
    else:
        found = _vs4bet(lines, actor, other, cls)
    return {a: f for a, f in (found or {"fold": 1.0}).items() if f > 0} or {"fold": 1.0}


def _decision(state: State, lines: dict) -> Optional[Decision]:
    """La décision à prendre, ou None si la ligne est finie (fold de tous, ou flop)."""
    if state.end:
        return None
    if state.opener is None:
        actor, situation, other = ORDER[state.next], "open", None
    elif state.threebettor is None:
        actor, situation, other = ORDER[state.next], "vs_open", state.opener
    elif not state.fourbet:
        actor, situation, other = state.opener, "vs_3bet", state.threebettor
    else:
        actor, situation, other = state.threebettor, "vs_4bet", state.opener
    reach = state.weights(actor)
    strategies = {cls: _strategy(lines, situation, actor, other, cls) for cls, w in reach.items() if w >= MIN_REACH}
    used = {a for s in strategies.values() for a in s}
    keys = [a for a in ACTIONS if a in used or a == "fold"]
    return Decision(actor, situation, other, strategies, keys)


def _size(state: State, decision: Decision) -> float:
    """Le montant (total mis) d'une relance."""
    if decision.situation == "open":
        return studyspots.OPEN
    if decision.situation == "vs_open":
        return studyspots.THREEBET["ip" if _ip(decision.actor, decision.other) == decision.actor else "oop"]
    return studyspots.FOURBET["ip" if _ip(decision.actor, decision.other) == decision.actor else "oop"]


def _apply(state: State, decision: Decision, action: str) -> None:
    actor = decision.actor
    reach = state.weights(actor)
    for cls in reach:
        reach[cls] *= decision.strategies.get(cls, {}).get(action, 0.0)
    if action == "fold":
        if decision.situation == "open":
            state.next += 1
            if ORDER[state.next] == "BB":  # tout le monde s'est couché : la BB prend les blindes
                state.end = "terminal_fold"
        elif decision.situation == "vs_open":
            state.next += 1
            if state.next >= len(ORDER):  # personne ne paie l'open
                state.end = "terminal_fold"
        else:
            state.end = "terminal_fold"
        return
    if action == "call":
        state.puts[actor] = max(state.puts.values())
        state.caller = actor
        state.end = "flop"
        return
    state.puts[actor] = _size(state, decision)
    if decision.situation == "open":
        state.opener = actor
        state.next += 1
    elif decision.situation == "vs_open":
        state.threebettor = actor
    else:
        state.fourbet = True


def _pair(state: State, decision: Optional[Decision]) -> list[str]:
    """Les deux joueurs du nœud : hors de position au flop, puis l'autre ; un seul tant que personne n'a ouvert."""
    if state.end == "flop":
        players = [state.opener, state.threebettor or state.caller]
    elif decision is not None and decision.other:
        players = [decision.actor, decision.other]
    elif decision is not None:
        return [decision.actor]
    elif state.threebettor:
        players = [state.opener, state.threebettor]
    elif state.opener:
        return [state.opener]
    else:
        return ["BB"]
    oop = min(players, key=studyspots.SEATS_POSTFLOP.index)
    return [oop, _ip(*players)]


def _step(state: State, decision: Decision, chosen: Optional[int]) -> dict:
    pair = _pair(state, decision)
    puts = [state.puts.get(p, 0.0) for p in pair]
    to_call = max(state.puts.values())
    actions = []
    for a in decision.keys:
        if a == "fold":
            actions.append({"kind": "fold", "amount": 0.0, "allin": False, "name": "Fold"})
        elif a == "call":
            actions.append({"kind": "call", "amount": to_call, "allin": False, "name": "Call"})
        else:
            size = _size(state, decision)
            name = {"open": "Open", "vs_open": "3bet", "vs_3bet": "4bet"}.get(decision.situation, "Relance")
            actions.append({"kind": "raise", "amount": size, "allin": False, "name": f"{name} {size:g}".replace(".", ",")})
    return {"kind": "action", "player": pair.index(decision.actor), "position": decision.actor,
            "key": f"{decision.situation}|{decision.actor}", "label": decision.label, "street": -1,
            "pot": round(sum(state.puts.values()), 3), "put": puts + [0.0] * (2 - len(puts)),
            "stack": round(STACK - state.puts.get(decision.actor, 0.0), 3), "actions": actions,
            "keys": list(decision.keys), "chosen": chosen, "card": None}


def charts() -> dict:
    """Les lignes de tes charts 6-max ; NoCharts sans elles."""
    lines = handplay.ring_lines((TABLE,)).get(TABLE)
    if not lines:
        raise NoCharts("Pas encore de charts 6-max : charge-les dans Mon jeu › Tables à plusieurs (« Charger les "
                       "charts »), ou en ligne de commande : python -m analyzer ranges --hand2note.")
    return lines


def node(line: list[str] | tuple[str, ...], lines: Optional[dict] = None) -> dict:
    """Le nœud au bout de la ligne ; ValueError si la ligne sort des charts (NoCharts sans charts)."""
    lines = charts() if lines is None else lines
    state = State()
    history = []
    for action in line:
        decision = _decision(state, lines)
        if decision is None:
            raise ValueError("ligne trop longue")
        if action not in decision.keys:
            raise ValueError(f"action inconnue ici : {action}")
        history.append(_step(state, decision, decision.keys.index(action)))
        _apply(state, decision, action)
    decision = _decision(state, lines)
    pair = _pair(state, decision)
    if decision is not None:
        current = _step(state, decision, None)
    else:
        current = {"kind": state.end, "player": None, "position": None, "street": -1,
                   "pot": round(sum(state.puts.values()), 3),
                   "put": [state.puts.get(p, 0.0) for p in pair] + [0.0] * (2 - len(pair)), "actions": [],
                   "keys": [], "chosen": None, "card": None}
    history.append(current)
    rows: list[list] = [[], []]
    for i, pos in enumerate(pair):
        strategies = decision.strategies if decision is not None and pos == decision.actor else None
        for cls, weight in state.weights(pos).items():
            if weight < MIN_REACH:
                continue
            tail = ([round(strategies.get(cls, {}).get(a, 0.0), 3) for a in decision.keys]
                    if strategies is not None else [])
            for combo in combos(cls):
                rows[i].append([combo, round(weight, 4), None, None, *tail])
    data = ring_ranges.solution(TABLE) or {}
    family = None
    if state.end == "flop":
        kind = {0: "srp", 1: "3bet"}.get(int(state.threebettor is not None) + int(state.fourbet), "4bet")
        family = studyspots.ring_family_of(pair[0], pair[1], kind)
    out = {
        "type": "action" if decision is not None else state.end, "street": -1, "board": [], "pot": current["pot"],
        "put": current["put"], "stacks": [round(STACK - x, 3) for x in current["put"]],
        "player": pair.index(decision.actor) if decision is not None else None, "actions": current["actions"],
        "cards": None, "history": history, "hands": rows, "positions": pair + [""] * (2 - len(pair)),
        "preflop": {"line": list(line), "key": current.get("key"), "label": decision.label if decision else None,
                    "keys": current["keys"], "family": family, "table": TABLE, "solution": f"Charts {TABLE}",
                    "description": f"charts {TABLE}" + (f" · {data['source'].split(' — ')[0]}" if data.get("source")
                                                         else "")},
    }
    if family:
        out["preflop"].update(_flops(family))
    return out


def _flops(family: str) -> dict:
    """Le choix du flop : les flops de la série (ou, hors des séries, les flops types) et ceux déjà résolus."""
    info = studyspots.ring_family(family)
    series = studyspots.flop_set(family)
    boards = series or studyspots.flop_set("srp")
    boards = boards + [b for b in studyspots.family_boards(family) if b not in boards]
    solved = studyspots.spot_studies((family,))
    spots = []
    for board in boards:
        ident = f"spot:{family}:{board}"
        spots.append({"id": ident, "board": studyspots.cards_of(board),
                      "texture": studyspots.flop_texture(studyspots.cards_of(board)), "solved": ident in solved,
                      "series": board in series})
    return {"family_label": info["label"], "family_name": f"{info['name']} · {info['pair']}", "spots": spots,
            "textures": list(studyspots.TEXTURES), "series": bool(series)}


def line_for_family(family: str) -> list[str]:
    """La ligne qui mène au flop d'une famille 6-max : les autres se couchent, l'ouvreur ouvre, l'autre paie (ou
    relance, puis l'ouvreur paie ou 4bette…)."""
    info = studyspots.ring_family(family)
    if info is None:
        raise NotFound(family)
    steps = info["steps"]
    opener, other = steps[0][0], steps[1][0]
    first, second = ORDER.index(opener), ORDER.index(other)
    line = ["fold"] * first + ["raise"] + ["fold"] * (second - first - 1)
    return line + [action for _, action in steps[1:]]
