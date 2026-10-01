#!/usr/bin/env python3
"""Faux analyzer-solve pour les tests : même format de sortie, stratégie fictive, sans calcul."""
import json
import os
import sys
import time

request = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"tree_nodes": 12, "arena_mb": 0.1}), file=sys.stderr, flush=True)
if os.environ.get("FAKE_SOLVER_SLEEP"):
    time.sleep(float(os.environ["FAKE_SOLVER_SLEEP"]))
print(json.dumps({"iteration": 10, "exploit_pct": 4.2, "elapsed": 0.1}), file=sys.stderr, flush=True)
if os.environ.get("FAKE_SOLVER_FAIL"):
    print(json.dumps({"error": os.environ["FAKE_SOLVER_FAIL"]}), file=sys.stderr)
    sys.exit(2)

decisions, player = [], 0
for k, step in enumerate(request["line"]):
    if "card" in step:
        player = 0
        continue
    kinds = ["check", "bet"] if step["action"] in ("check", "bet") else ["fold", "call", "raise"]
    actions = [{"label": x, "kind": x, "amount": step.get("to", 0.0) if x == step["action"] else 1.0, "allin": False}
               for x in kinds]
    chosen = kinds.index(step["action"])
    strategy = [0.05] * len(kinds)
    strategy[chosen] = 1 - 0.05 * (len(kinds) - 1)
    combos = {}
    for combo, owner in request["combos"].items():
        combos[combo] = {"player": owner, "reach": 1.0, "eq": 0.5, "ev": 2.0}
        if owner == player:
            combos[combo].update(s=strategy, evs=[2.0 - 0.5 * (a != chosen) for a in range(len(kinds))])
    decisions.append({"step": k, "street": 0, "board": [], "pot": request["spot"]["tree"]["starting_pot"],
                      "player": player, "actions": actions, "chosen": chosen, "range": strategy,
                      "classes": {"AKs": {"w": 2.0, "n": 4, "s": strategy}}, "combos": combos})
    player = 1 - player
print(json.dumps({"engine": "cpu", "iterations": 10, "exploit_pct": 4.2, "seconds": 0.1, "tree_nodes": 12,
                  "decisions": decisions, "stopped": None}))
