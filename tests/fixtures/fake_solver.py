#!/usr/bin/env python3
"""Faux analyzer-solve pour les tests : même format de sortie (et mode --serve), stratégie fictive."""
import json
import os
import sys
import time

RANKS = "AKQJT98765432"
request = json.load(open(sys.argv[1], encoding="utf-8"))
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
    return {"type": "action", "street": 0, "board": board[:3], "pot": pot, "stacks": [90.0, 90.0],
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
print(json.dumps({"engine": "cpu", "iterations": 10, "exploit_pct": 4.2, "seconds": 0.1, "tree_nodes": 12,
                  "decisions": decisions, "stopped": None}), flush=True)

if "--serve" in sys.argv:
    for line in sys.stdin:
        query = json.loads(line)["path"]
        if any(s.get("type") == "action" and s["index"] > 1 for s in query):
            reply = {"error": "action index out of range"}
        else:
            reply = {"node": node(len(query) % 2, ["check", "bet"], 0, 5.0, [])}
        print(json.dumps(reply), flush=True)
