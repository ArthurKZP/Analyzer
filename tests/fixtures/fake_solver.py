#!/usr/bin/env python3
"""Faux analyzer-solve pour les tests : même format de sortie (et mode --serve), stratégie fictive."""
import json
import os
import sys
import time

RANKS = "AKQJT98765432"
args = sys.argv[1:]
option = lambda name: args[args.index(name) + 1] if name in args else None  # noqa: E731


def plan_ev(plan, pot):
    """EV fictive : chaque situation rapporte plus quand ses tailles approchent 40 % (BB) ou 70 % (bouton)."""
    value = lambda s: 120.0 if str(s).startswith("geo") else 300.0 if s == "a" else float(s)  # noqa: E731
    ev = [pot / 2, pot / 2]
    for key, sizes in (plan or {}).items():
        p = 0 if key.split(":")[1][1] == "o" else 1
        ev[p] += sum(-abs(value(s) - (40.0 if p == 0 else 70.0)) / 100 for s in sizes)
    return ev


if option("--lot"):  # variantes : EV fictive, situations du sous-jeu fréquentes (sur-relances rares)
    lot = json.load(open(option("--lot"), encoding="utf-8"))
    for i, run in enumerate(lot["runs"]):
        base = lot["bases"][run["base"]]
        plan, past = run.get("plan", {}), base.get("past", "")
        stats = {}
        for key, sizes in plan.items():
            kind, where, key_past, *level = key.split(":")
            if where[0] == "f" or not key_past.startswith(past) or not sizes:
                continue
            reach = 0.001 if level == ["1"] else 0.5
            stats[key] = {"reach": reach, "usage": [reach / len(sizes)] * len(sizes)}
        print(json.dumps({"i": i, "iterations": 10, "exploit_pct": 0.4, "seconds": 0.01, "tree_nodes": 9,
                          "root_ev": plan_ev(plan, base["spot"]["tree"]["starting_pot"]), "stats": stats}))
    sys.exit(0)
if option("--load"):  # étude : ici, simplement la requête d'origine
    request = json.load(open(option("--load"), encoding="utf-8"))
    print(json.dumps({"loaded": 0.1}), file=sys.stderr, flush=True)
else:
    request = json.load(open(args[0], encoding="utf-8"))
    if option("--save"):
        json.dump(request, open(option("--save"), "w", encoding="utf-8"))
spot = request["spot"]
board = [spot["board"][i:i + 2] for i in range(0, len(spot["board"]), 2)]


def expand(range_text):
    """Combos d'une range « AA,AKs:0.5,… » (classes seulement), sans les cartes du board."""
    out = []
    for token in filter(None, range_text.split(",")):
        cls = token.split(":")[0]
        r1, r2 = cls[0], cls[1]
        for s1 in "cdhs":
            for s2 in "cdhs":
                a, b = r1 + s1, r2 + s2
                if a == b or a in board or b in board:
                    continue
                if len(cls) == 2 and s1 >= s2 or len(cls) == 3 and (cls[2] == "s") != (s1 == s2):
                    continue
                out.append(a + b)
    return out


ranges = [expand(spot["range_oop"]), expand(spot["range_ip"])]
print(json.dumps({"tree_nodes": 12, "arena_mb": 0.1}), file=sys.stderr, flush=True)
if os.environ.get("FAKE_SOLVER_SLEEP"):
    time.sleep(float(os.environ["FAKE_SOLVER_SLEEP"]))
print(json.dumps({"iteration": 10, "exploit_pct": 4.2, "elapsed": 0.1}), file=sys.stderr, flush=True)
if os.environ.get("FAKE_SOLVER_FAIL"):
    print(json.dumps({"error": os.environ["FAKE_SOLVER_FAIL"]}), file=sys.stderr)
    sys.exit(2)


def node(player, kinds, chosen, pot, history):
    actions = [{"label": k, "kind": k, "amount": 1.0, "allin": False} for k in kinds]
    strategy = [0.05] * len(kinds)
    strategy[chosen] = 1 - 0.05 * (len(kinds) - 1)
    hands = [[], []]
    for p in (0, 1):
        for combo in ranges[p]:
            row = [combo, 1.0, 0.5, 2.0]
            if p == player:
                row += strategy + [2.0 - 0.5 * (a != chosen) for a in range(len(kinds))]
            hands[p].append(row)
    current = {"kind": "action", "player": player, "stack": 90.0, "pot": pot, "street": 0,
               "actions": actions, "chosen": None, "card": None}
    return {"type": "action", "street": 0, "board": board[:3], "pot": pot, "put": [pot / 2, pot / 2], "stacks": [90.0, 90.0],
            "player": player, "actions": actions, "cards": None, "hands": hands, "history": history + [current]}


decisions, player, path, history = [], 0, [], []
for k, step in enumerate(request["line"]):
    if "card" in step:
        path.append({"type": "card", "card": step["card"]})
        history.append({"kind": "card", "player": None, "stack": 90.0, "pot": 1.0, "street": 1, "actions": [],
                        "chosen": None, "card": step["card"]})
        player = 0
        continue
    kinds = ["check", "bet"] if step["action"] in ("check", "bet") else ["fold", "call", "raise"]
    chosen = kinds.index(step["action"])
    n = node(player, kinds, chosen, spot["tree"]["starting_pot"], history)
    decisions.append({"step": k, "path": list(path), "chosen": chosen, "node": n})
    history = n["history"][:-1] + [dict(n["history"][-1], chosen=chosen)]
    path.append({"type": "action", "index": chosen})
    player = 1 - player
print(json.dumps({"engine": "etude" if option("--load") else "cpu", "iterations": 10, "exploit_pct": 4.2,
                  "seconds": 0.1, "tree_nodes": 12, "decisions": decisions, "stopped": None,
                  "root_ev": plan_ev(request.get("plan"), spot["tree"]["starting_pot"])}), flush=True)

if "--serve" in sys.argv:
    for line in sys.stdin:
        query = json.loads(line)["path"]
        if any(s.get("type") == "action" and s["index"] > 1 for s in query):
            reply = {"error": "action index out of range"}
        elif query == [{"type": "action", "index": 0}] * 2:  # check-check : la turn
            deck = [r + s for r in RANKS for s in "cdhs" if r + s not in board]
            reply = {"node": {"type": "chance", "street": 1, "board": board[:3], "pot": 5.0, "put": [2.5, 2.5],
                              "stacks": [97.5, 97.5], "player": None, "actions": [], "cards": deck[:8],
                              "hands": [[[c, 1.0, 0.5, 2.5] for c in ranges[p]] for p in (0, 1)], "history": []}}
        else:
            reply = {"node": node(len(query) % 2, ["check", "bet"], 0, 5.0, [])}
        print(json.dumps(reply), flush=True)
