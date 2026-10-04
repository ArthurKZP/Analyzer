"""L'arbre préflop de la solution HU, nœud par nœud, au format des nœuds de l'explorateur (static/explorer.js).

Une ligne est la suite des actions prises depuis l'open du bouton (« raise », « call », « fold », « allin »).
Elle mène à un nœud de la solution, à un fold, à un tapis (pas de jeu postflop étudié), ou à un flop : SRP,
pot 3bet ou pot 4bet, dont on choisit ensuite le flop parmi les spots d'étude (studyspots).

Indices des joueurs comme au postflop : 0 = BB (hors de position), 1 = bouton.
"""
from __future__ import annotations

from typing import Optional

from . import studyspots
from .preflop import Solution, load_solution

ORDER = ("sb_open", "bb_vs_open", "sb_vs_3bet", "bb_vs_4bet")  # nœud de la solution après 0, 1, 2, 3 relances
FAMILY_LINES = {"srp": ("raise", "call"), "3bet": ("raise", "raise", "call"),
                "4bet": ("raise", "raise", "raise", "call")}
ACTIONS = ("fold", "call", "raise", "allin")  # ordre d'affichage, du plus passif au plus agressif
RAISE_NAMES = {"sb_open": "Open", "bb_vs_open": "3bet", "sb_vs_3bet": "4bet"}
INDEX = {"bb": 0, "sb": 1}
RANKS = "AKQJT98765432"
MIN_REACH = 0.001


def _bb(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def combos(cls: str) -> list[str]:
    """« AKs » -> ['AsKs', 'AhKh', …] ; « QQ » -> 6 combos ; « 72o » -> 12 combos."""
    r1, r2 = cls[0], cls[1]
    if r1 == r2:
        suits = "shdc"
        return [r1 + a + r2 + b for i, a in enumerate(suits) for b in suits[i + 1:]]
    if cls.endswith("s"):
        return [r1 + s + r2 + s for s in "shdc"]
    return [r1 + a + r2 + b for a in "shdc" for b in "shdc" if a != b]


def classes() -> list[str]:
    out = []
    for i, a in enumerate(RANKS):
        for j, b in enumerate(RANKS):
            out.append(a + b if i == j else a + b + "s" if i < j else b + a + "o")
    return out


def family_of(line: tuple[str, ...]) -> Optional[str]:
    return next((f for f, fl in FAMILY_LINES.items() if fl == tuple(line)), None)


def _step(solution: Solution, key: str, puts: dict, chosen: Optional[int]) -> dict:
    node = solution.nodes[key]
    actor = node.player
    other = "bb" if actor == "sb" else "sb"
    keys = [a for a in ACTIONS if a in node.actions]
    actions = []
    for a in keys:
        if a == "fold":
            actions.append({"kind": "fold", "amount": 0.0, "allin": False, "name": "Fold"})
        elif a == "call":
            actions.append({"kind": "call", "amount": puts[other], "allin": False, "name": "Call"})
        elif a == "allin":
            actions.append({"kind": "raise", "amount": solution.stack_bb, "allin": True,
                            "name": f"Tapis {_bb(solution.stack_bb)}"})
        else:
            size = node.sizes["raise"]
            actions.append({"kind": "raise", "amount": size, "allin": False,
                            "name": f"{RAISE_NAMES.get(key, 'Relance')} {_bb(size)}"})
    return {"kind": "action", "player": INDEX[actor], "key": key, "label": node.label, "street": -1,
            "pot": round(puts["sb"] + puts["bb"], 3), "put": [puts["bb"], puts["sb"]],
            "stack": round(solution.stack_bb - puts[actor], 3), "actions": actions, "keys": keys,
            "chosen": chosen, "card": None}


def node(line: list[str] | tuple[str, ...], solution: Optional[Solution] = None) -> dict:
    """Nœud au bout de la ligne ; ValueError si la ligne sort de la solution."""
    solution = solution or load_solution()
    line = tuple(line)
    reach = {p: dict.fromkeys(classes(), 1.0) for p in ("sb", "bb")}
    puts = {"sb": 0.5, "bb": 1.0}
    history, raises, end = [], 0, None
    for k, action in enumerate(line):
        if end is not None or raises >= len(ORDER):
            raise ValueError("ligne trop longue")
        key = ORDER[raises]
        n = solution.nodes[key]
        if action not in n.actions:
            raise ValueError(f"action inconnue : {action}")
        step = _step(solution, key, puts, None)
        step["chosen"] = step["keys"].index(action)
        history.append(step)
        actor, other = n.player, "bb" if n.player == "sb" else "sb"
        for cls in reach[actor]:
            reach[actor][cls] *= (n.strategy(cls) or {}).get(action, 0.0)
        if action == "fold":
            end = "terminal_fold"
        elif action == "call":
            puts[actor] = puts[other]
            end = "flop"
        elif action == "allin":
            puts[actor] = solution.stack_bb
            end = "allin"
        else:
            puts[actor] = n.sizes["raise"]
            raises += 1
    key = None if end else ORDER[raises]
    if key is not None:
        current = _step(solution, key, puts, None)
        history.append(current)
    else:
        current = {"kind": end, "player": None, "street": -1, "pot": round(puts["sb"] + puts["bb"], 3),
                   "put": [puts["bb"], puts["sb"]], "actions": [], "keys": [], "chosen": None, "card": None}
        history.append(current)
    actor = solution.nodes[key].player if key else None
    rows: list[list] = [[], []]
    for p in ("bb", "sb"):
        n = solution.nodes[key] if key and p == actor else None
        for cls, weight in reach[p].items():
            if weight < MIN_REACH:
                continue
            row_tail = []
            if n is not None:
                strategy = n.strategy(cls) or {}
                row_tail = [round(strategy.get(a, 0.0), 3) for a in current["keys"]]
            for combo in combos(cls):
                rows[INDEX[p]].append([combo, round(weight, 4), None, None, *row_tail])
    family = family_of(line) if end == "flop" else None
    out = {
        "type": "action" if key else end, "street": -1, "board": [], "pot": current["pot"], "put": current["put"],
        "stacks": [round(solution.stack_bb - puts["bb"], 3), round(solution.stack_bb - puts["sb"], 3)],
        "player": INDEX[actor] if actor else None, "actions": current["actions"], "cards": None,
        "history": history, "hands": rows,
        "preflop": {"line": list(line), "key": key, "label": solution.nodes[key].label if key else None,
                    "keys": current["keys"], "family": family, "solution": solution.name,
                    "description": solution.description},
    }
    if family:
        info = studyspots.FAMILIES[family]
        studies = studyspots.spot_studies()
        series = set(studyspots.flop_set(family))
        spots = []
        for board in studyspots.family_boards(family):
            spot = studyspots.StudySpot(family, studyspots.cards_of(board))
            spots.append({"id": spot.ident, "board": spot.board, "texture": spot.texture,
                          "solved": spot.ident in studies, "series": board in series})
        out["preflop"].update(family_label=info["label"], family_name=info["name"], spots=spots,
                              textures=list(studyspots.TEXTURES))
    return out
