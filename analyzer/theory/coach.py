"""Plan de jeu suggéré : ce que disent les études résolues, réduit à des règles simples à appliquer.

1. Extraction, une fois par étude (quelques secondes une fois l'étude ouverte) : aux nœuds clés de la ligne
   principale (c-bet, réponse à la c-bet, 2e barrel à chaque turn, 3e barrel sur un échantillon de rivers,
   c-bet retardée, probe…), la stratégie de toute la range regroupée par famille de mains (deux paires et
   mieux, overpair, top pair…, tirage couleur, air) et, à la turn et à la river, par type de carte
   (overcard, brique, board pairé, couleur ou quinte possible) ; à chacun de ces nœuds, ce que vaut chaque action
   pour chaque famille de mains (ev_summary), par type de carte. Gardée dans la base (documents « plan »).
2. Synthèse, à l'affichage : les flops regroupés en trois stratégies de c-bet (miser range, stratégie mixte,
   checker range) et, dans chacune, par taille de mise (petite, moyenne, grosse, tapis). Pour chaque groupe :
   ses flops (des exemples, chacun ouvrable dans l'explorateur), le pourquoi (avantage d'équité, avantage de
   nuts), des règles pour quatre familles de mains (fortes, moyennes, tirages, rien) et la suite à la turn et
   à la river selon la carte ; en attaque pour celui qui a l'initiative, en défense pour l'autre (face à la
   c-bet et aux barrels, et quand l'attaquant checke). Plus il y a de flops résolus, plus le plan est précis.
3. Niveaux (levels) : des plans de plus en plus fins — une seule action pour toute la range sur tous les flops,
   puis une par type de flop, puis une par type de flop et famille de mains —, chacun avec ce qu'il coûte face à
   la stratégie du solveur, et la suite à la turn et à la river (une action par famille de mains, puis par type de
   carte). Option : affiner la suite d'un niveau au solveur, sur quelques flops résolus de nouveau avec le flop du
   niveau imposé (refine_spots).

Un plan par famille de spots d'étude : heads-up (SRP, pot 3bet, pot 4bet) et tables à plusieurs (6-max, une famille
par paire de positions et type de pot, avec tes charts). Les arbres 6-max ont la forme du heads-up de même structure
(celui qui a l'initiative, hors de position ou en position) : mêmes lignes, avec les vraies positions.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Optional

from .. import db
from ..cards import RANK_VALUE
from ..db import documents
from . import postflop, preflop_tree, sizing, studyspots
from .handclass import DRAW_BIT, MADE, MADE_INDEX, classify

VERSION = 5  # change quand l'extraction change : les plans plus anciens sont relus (« Préparer le plan »)
# Versions encore affichées en attendant : la version 1 n'a pas l'EV des actions (pas de niveaux), la version 2
# seulement celle du flop (« ev » de la c-bet et de la réponse à la c-bet : les niveaux sans la turn ni la river), la
# version 3 pas la river après une turn checkée, la version 4 pas les boîtes d'équité (les niveaux attendent la
# version 5).
READABLE = (1, 2, 3, 4, 5)
ORDER = ("fold", "check", "call", "small", "medium", "big", "overbet", "raise", "allin")

BUCKETS = (
    ("nuts", "Deux paires et mieux"), ("overpair", "Overpair"), ("tp_good", "Top pair, bon kicker"),
    ("tp_weak", "Top pair, petit kicker"), ("midpair", "Paire moyenne"), ("weakpair", "Petite paire"),
    ("fd", "Tirage couleur"), ("sd", "Tirage quinte"), ("weakdraw", "Gutshot ou backdoor"),
    ("high", "Hauteur As ou Roi"), ("air", "Rien"),
)
BUCKET_LABEL = dict(BUCKETS)
VALUE = ("nuts", "overpair", "tp_good", "tp_weak")
DRAWS = ("fd", "sd", "weakdraw")

CLASSES = (("fold", "fold"), ("check", "check"), ("call", "call"), ("small", "petite mise"),
           ("medium", "mise moyenne"), ("big", "grosse mise"), ("overbet", "overbet"), ("raise", "relance"),
           ("allin", "tapis"))
CLASS_LABEL = dict(CLASSES)
AGGRESSIVE = ("small", "medium", "big", "overbet", "raise", "allin")
BETS = ("small", "medium", "big", "overbet")

# Boîtes d'équité : l'équité d'une main face à la range de l'adversaire, au nœud, avant d'agir. Chaque boîte se
# détaille ensuite par famille de mains (BUCKETS) : une case de la range (cell_of).
EQUITY = (("e4", "Très fortes", "75 % d'équité et plus", 0.75), ("e3", "Bonnes", "de 50 à 75 % d'équité", 0.50),
          ("e2", "Faibles", "de 25 à 50 % d'équité", 0.25), ("e1", "Très faibles", "moins de 25 % d'équité", 0.0))
EQUITY_LABEL = {key: label for key, label, _, _ in EQUITY}

CARDS = (("over", "Overcard"), ("brick", "Brique"), ("paired", "Board pairé"), ("flush", "Couleur possible"),
         ("straight", "Quinte possible"))
CARD_LABEL = dict(CARDS)

# Nœuds extraits : (clé, titre, actions depuis la racine). « * » : chaque carte de la turn ou de la river ;
# « bet » prend la mise la plus jouée par la range quand il y en a plusieurs. Un nœud forcé (la BB qui ne
# peut que checker) est traversé par son action unique.
# Titres écrits pour le heads-up (BB hors de position, bouton en position) ; en 6-max, renommés avec les vraies positions.
LINES_IP = (  # le bouton à l'initiative : SRP, pot 4bet
    ("cbet", "C-bet du bouton", ["check"]),
    ("vs_cbet", "BB face à la c-bet", ["check", "bet"]),
    ("vs_xr", "Bouton face au check-raise", ["check", "bet", "raise"]),
    ("barrel", "2e barrel du bouton", ["check", "bet", "call", "*", "check"]),
    ("vs_barrel", "BB face au 2e barrel", ["check", "bet", "call", "*", "check", "bet"]),
    ("delayed", "C-bet retardée du bouton", ["check", "check", "*", "check"]),
    ("probe", "Probe de la BB", ["check", "check", "*"]),
    ("vs_probe", "Bouton face au probe", ["check", "check", "*", "bet"]),
    ("barrel3", "3e barrel du bouton", ["check", "bet", "call", "*", "check", "bet", "call", "*", "check"]),
    ("vs_barrel3", "BB face au 3e barrel", ["check", "bet", "call", "*", "check", "bet", "call", "*", "check", "bet"]),
    ("river_xx", "River du bouton après la turn checkée",
     ["check", "bet", "call", "*", "check", "check", "*", "check"]),
    ("vs_river_xx", "BB face à la mise river après la turn checkée",
     ["check", "bet", "call", "*", "check", "check", "*", "check", "bet"]),
)
LINES_OOP = (  # la BB à l'initiative : pot 3bet
    ("cbet", "C-bet de la BB", []),
    ("vs_cbet", "Bouton face à la c-bet", ["bet"]),
    ("vs_xr", "BB face à la relance", ["bet", "raise"]),
    ("stab", "Stab du bouton (la BB checke)", ["check"]),
    ("vs_stab", "BB face au stab", ["check", "bet"]),
    ("barrel", "2e barrel de la BB", ["bet", "call", "*"]),
    ("vs_barrel", "Bouton face au 2e barrel", ["bet", "call", "*", "bet"]),
    ("delayed", "C-bet retardée de la BB", ["check", "check", "*"]),
    ("barrel3", "3e barrel de la BB", ["bet", "call", "*", "bet", "call", "*"]),
    ("vs_barrel3", "Bouton face au 3e barrel", ["bet", "call", "*", "bet", "call", "*", "bet"]),
    ("river_xx", "River de la BB après la turn checkée", ["bet", "call", "*", "check", "check", "*"]),
    ("vs_river_xx", "Bouton face à la mise river après la turn checkée",
     ["bet", "call", "*", "check", "check", "*", "bet"]),
)
RIVER_TURNS = 8  # turns gardées pour les nœuds de river (9 sur 49 : toutes les rivers de chacune)


def lines(family: str) -> tuple:
    info = studyspots.family_info(family)
    base = LINES_OOP if info["oop_initiative"] else LINES_IP
    if family in studyspots.FAMILIES:
        return base
    return tuple((name, sizing.rename_roles(title.replace("Bouton", "BTN"), info["oop"], info["ip"]), steps)
                 for name, title, steps in base)


def plan_families() -> tuple[str, ...]:
    """Les familles qui ont un plan de jeu : heads-up, puis 6-max."""
    return tuple(studyspots.FAMILIES) + tuple(studyspots.RING_FAMILIES)


def seat_names(family: Optional[str]) -> tuple[str, str]:
    """Les positions des deux joueurs (hors de position, en position) : BB et BTN en heads-up."""
    info = studyspots.ring_family(family) if family and family not in studyspots.FAMILIES else None
    return (info["oop"], info["ip"]) if info else ("BB", "BTN")


def seat_words(family: str) -> tuple[str, str]:
    """Les deux joueurs avec leur article, pour les phrases : « la BB », « le bouton », « le CO »…"""
    if family in studyspots.FAMILIES:
        return "la BB", "le bouton"
    return tuple(("la " if pos in ("SB", "BB") else "l'" if pos.startswith("U") else "le ") + pos
                 for pos in seat_names(family))


# --- Familles de mains et types de cartes ----------------------------------------------------------

def bucket(combo: str, board: Iterable[str]) -> str:
    board = tuple(board)
    made, draws = classify(combo, board)
    key = MADE[made][0]
    if made <= MADE_INDEX["twopair"]:
        return "nuts"
    if key == "overpair":
        return "overpair"
    if key == "toppair":
        top = max(RANK_VALUE[c[0]] for c in board)
        kicker = max(RANK_VALUE[c[0]] for c in (combo[:2], combo[2:]) if RANK_VALUE[c[0]] != top)
        return "tp_good" if kicker >= 10 else "tp_weak"
    if key in ("secondpair", "underpair"):
        return "midpair"
    if key == "weakpair":
        return "weakpair"
    if draws & DRAW_BIT["fd"]:
        return "fd"
    if draws & DRAW_BIT["oesd"]:
        return "sd"
    if draws & (DRAW_BIT["gutshot"] | DRAW_BIT["bdfd"]):
        return "weakdraw"
    return "high" if key in ("acehigh", "kinghigh") else "air"


def equity_box(eq: float) -> str:
    return next(key for key, _, _, low in EQUITY if eq >= low)


def cell_of(eq: float, family: str) -> str:
    """La case d'une main : « boîte d'équité|famille » (« e1|fd » : un tirage couleur à moins de 25 % d'équité)."""
    return f"{equity_box(eq)}|{family}"


def card_class(before: list[str], card: str) -> str:
    """Ce que la carte change au board : le paire, complète une couleur possible, overcard, ouvre une quinte,
    ou rien (brique)."""
    ranks = {RANK_VALUE[c[0]] for c in before}
    r = RANK_VALUE[card[0]]
    if r in ranks:
        return "paired"
    if sum(1 for c in before if c[1] == card[1]) >= 2:
        return "flush"
    if r > max(ranks):
        return "over"

    def low(values: set[int]) -> set[int]:
        return values | ({1} if 14 in values else set())
    before_v, after_v = low(ranks), low(ranks | {r})
    for lo in range(1, 11):
        window = range(lo, lo + 5)
        if (r in window or (r == 14 and lo == 1)) and sum(v in window for v in after_v) >= 3 \
                and sum(v in window for v in before_v) < 3:
            return "straight"
    return "brick"


def size_class(pct: float) -> str:
    """La classe d'une mise d'après sa taille en % du pot (comme action_class)."""
    return "small" if pct <= 40 else "medium" if pct <= 80 else "big" if pct <= 130 else "overbet"


def action_class(node: dict, action: dict) -> str:
    kind = action["kind"]
    if kind in ("fold", "check", "call"):
        return kind
    if action.get("allin"):
        return "allin"
    if kind == "raise":
        return "raise"
    share = action["amount"] / node["pot"] if node["pot"] else 0
    return "small" if share <= 0.4 else "medium" if share <= 0.8 else "big" if share <= 1.3 else "overbet"


# --- Extraction ------------------------------------------------------------------------------------

def _choose(node: dict, kind: str) -> Optional[int]:
    """Indice de l'action de ce type ; pour une mise ou une relance, la plus jouée par la range."""
    if node["type"] != "action":
        return None
    candidates = [i for i, a in enumerate(node["actions"]) if a["kind"] == kind]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    freqs, _ = postflop.node_summary(node)
    return max(candidates, key=lambda i: freqs[i])


def _walk(query: Callable[[list], dict], steps: list[str], path: list, node: dict, dealt: list[str],
          stride: int) -> Iterable[tuple[dict, list[str]]]:
    """Les nœuds au bout des étapes (de vraies décisions), avec les cartes tirées en route (turn, river).
    Un check forcé (la BB qui ne mène pas) est une étape « check » comme une autre."""
    if not steps:
        if node["type"] == "action" and len(node["actions"]) > 1:
            yield node, dealt
        return
    head, rest = steps[0], steps[1:]
    if head == "*":
        if node["type"] != "chance":
            return
        cards = sorted(node["cards"] or [])
        if "*" in rest and stride > 1:  # turn d'une ligne qui va jusqu'à la river : un échantillon
            cards = cards[::stride]
        for card in cards:
            p = path + [{"type": "card", "card": card}]
            yield from _walk(query, rest, p, query(p), dealt + [card], stride)
        return
    index = _choose(node, head)
    if index is None:
        return
    p = path + [{"type": "action", "index": index}]
    yield from _walk(query, rest, p, query(p), dealt, stride)


def _buckets(node: dict) -> list[Optional[str]]:
    """La famille de mains (BUCKETS) de chaque main du joueur qui agit ; None pour une main qui n'arrive pas ici."""
    return [bucket(row[0], node["board"]) if row[1] > 0 else None for row in node["hands"][node["player"]]]


def _accumulate(acc: dict, node: dict, group: str, buckets: list[Optional[str]]) -> None:
    actor = node["player"]
    na = len(node["actions"])
    classes = [action_class(node, a) for a in node["actions"]]
    g = acc.setdefault(group, {"n": 0, "w": 0.0, "f": {}, "b": {}})
    g["n"] += 1
    for row, key in zip(node["hands"][actor], buckets):
        reach = row[1]
        if reach <= 0:
            continue
        b = g["b"].setdefault(key, {"w": 0.0, "f": {}})
        b["w"] += reach
        g["w"] += reach
        for k in range(na):
            share = reach * row[4 + k]
            if share:
                b["f"][classes[k]] = b["f"].get(classes[k], 0.0) + share
                g["f"][classes[k]] = g["f"].get(classes[k], 0.0) + share


def _normalize(acc: dict) -> dict:
    out = {}
    for group, g in acc.items():
        w = g["w"] or 1.0
        out[group] = {"n": g["n"], "f": {c: round(v / w, 4) for c, v in g["f"].items()},
                      "b": {k: {"w": round(b["w"] / w, 4), "f": {c: round(v / (b["w"] or 1), 4) for c, v in b["f"].items()}}
                            for k, b in g["b"].items()}}
    return out


def ev_summary(node: dict, buckets: Optional[list[Optional[str]]] = None) -> Optional[dict]:
    """Ce que vaut chaque action à ce nœud pour chaque case de la range (cell_of : boîte d'équité et famille de mains),
    et ce qu'en fait le solveur : sommes pondérées par la présence des mains, pour une range de poids 1 (les flops se
    comparent ainsi entre eux) ; « r » : la part de la range, « f » : la part de chaque classe d'action (somme « r »),
    « e » : l'EV de la case si elle jouait toute cette classe, « s » : celle de la stratégie du solveur (EV en part du
    pot). Une action par classe (action_class) pour « e » : la plus jouée de sa classe. EV face à la stratégie de
    l'adversaire telle qu'elle est. Avec la taille de chaque classe de mise (% du pot) et de relance (× la mise). None
    sans EV par action (ou sans main qui arrive ici). buckets : _buckets(node), s'il est déjà calculé."""
    actor = node.get("player")
    if actor is None or not node.get("pot"):
        return None
    if buckets is None:
        buckets = _buckets(node)
    na = len(node["actions"])
    classes = [action_class(node, a) for a in node["actions"]]
    # (les fréquences de la range ne servent qu'à départager deux actions d'une même classe)
    freqs = postflop.node_summary(node)[0] if len(set(classes)) < na else [0.0] * na
    chosen: dict[str, int] = {}
    for i, c in enumerate(classes):
        if c not in chosen or freqs[i] > freqs[chosen[c]]:
            chosen[c] = i
    total, cells = 0.0, {}
    for row, family in zip(node["hands"][actor], buckets):
        reach, eq, strategy, evs = row[1], row[2], row[4:4 + na], row[4 + na:4 + 2 * na]
        if reach <= 0 or eq is None or len(evs) < na or any(e is None for e in evs):
            continue
        g = cells.setdefault(cell_of(eq, family), {"r": 0.0, "s": 0.0, "f": dict.fromkeys(chosen, 0.0),
                                                   "e": dict.fromkeys(chosen, 0.0)})
        g["r"] += reach
        g["s"] += reach * sum(p * e for p, e in zip(strategy, evs))
        for k, c in enumerate(classes):
            g["f"][c] += reach * strategy[k]
        for c, i in chosen.items():
            g["e"][c] += reach * evs[i]
        total += reach
    if not total:
        return None
    scale = 1 / (total * node["pot"])
    actions = node["actions"]
    call = next((a["amount"] for a in actions if a["kind"] == "call"), None)
    sizes = {c: round(100 * actions[i]["amount"] / node["pot"]) for c, i in chosen.items() if c in BETS}
    raises = {c: round(actions[i]["amount"] / call, 1) for c, i in chosen.items() if c == "raise" and call}
    return {"classes": sorted(chosen, key=ORDER.index), "sizes": sizes, "raises": raises,
            "b": {k: {"r": round(g["r"] / total, 5), "s": round(g["s"] * scale, 5),
                      "f": {c: round(v / total, 4) for c, v in g["f"].items() if v / total >= 5e-5},
                      "e": {c: round(v * scale, 5) for c, v in g["e"].items()}} for k, g in cells.items()}}


def _add_ev(acc: dict, node: dict, group: str, buckets: list[Optional[str]]) -> None:
    """Ajoute l'EV de ce nœud (ev_summary) à son groupe (« flop », ou le type de la carte de la turn ou de la river)."""
    ev = ev_summary(node, buckets)
    if ev is None:
        return
    g = acc.setdefault(group, {"n": 0, "classes": None, "sizes": {}, "raises": {}, "b": {}})
    g["n"] += 1
    g["classes"] = set(ev["classes"]) if g["classes"] is None else g["classes"] & set(ev["classes"])
    for kind in ("sizes", "raises"):
        for c, x in ev[kind].items():
            g[kind].setdefault(c, []).append(x)
    for key, b in ev["b"].items():
        t = g["b"].setdefault(key, {"r": 0.0, "s": 0.0, "f": {}, "e": {}})
        t["r"] += b["r"]
        t["s"] += b["s"]
        for kind in ("f", "e"):
            for c, v in b[kind].items():
                t[kind][c] = t[kind].get(c, 0.0) + v


def _normalize_ev(acc: dict) -> dict:
    """L'EV moyenne des nœuds de chaque groupe (chaque carte compte autant), comme ev_summary, avec « n » : le nombre
    de nœuds. Les actions gardées : celles de tous les nœuds du groupe (la part d'une autre va à la plus proche)."""
    out = {}
    for group, g in acc.items():
        n, classes = g["n"], sorted(g["classes"], key=ORDER.index)
        cells = {}
        for k, b in g["b"].items():
            f: dict[str, float] = {}
            for c, v in b["f"].items():
                to = c if c in classes else _substitute(c, classes)
                if to:
                    f[to] = f.get(to, 0.0) + v
            cells[k] = {"r": round(b["r"] / n, 5), "s": round(b["s"] / n, 5),
                        "f": {c: round(v / n, 4) for c, v in f.items() if v / n >= 5e-5},
                        "e": {c: round(b["e"][c] / n, 5) for c in classes if c in b["e"]}}
        out[group] = {"n": n, "classes": classes, "b": cells,
                      **{kind: {c: sorted(v)[len(v) // 2] for c, v in g[kind].items() if c in classes}
                         for kind in ("sizes", "raises")}}
    return out


def _advantages(node: dict) -> dict:
    """Équité moyenne de chaque range et part de « deux paires et mieux » (avantage de nuts)."""
    out = {"eq": [], "nuts": []}
    for p in (0, 1):
        rows = node["hands"][p]
        w = sum(r[1] for r in rows) or 1.0
        known = [r for r in rows if r[2] is not None]
        wk = sum(r[1] for r in known) or 1.0
        out["eq"].append(round(sum(r[1] * r[2] for r in known) / wk, 4))
        out["nuts"].append(round(sum(r[1] for r in rows if bucket(r[0], node["board"]) == "nuts") / w, 4))
    return out


def extract(query: Callable[[list], dict], spot: studyspots.StudySpot, key: str) -> dict:
    """Résumé de la stratégie d'une étude ouverte (query : chemin -> nœud, comme Session.node)."""
    root = query([])
    stride = max(1, round(49 / RIVER_TURNS))
    nodes = {}
    for name, title, steps in lines(spot.family):
        acc: dict = {}
        evs: dict = {}
        sizes = set()
        actor = None
        for node, dealt in _walk(query, steps, [], root, [], stride):
            group = card_class(spot.board + dealt[:-1], dealt[-1]) if dealt else "flop"
            buckets = _buckets(node)
            _accumulate(acc, node, group, buckets)
            _add_ev(evs, node, group, buckets)
            actor = node["player"]
            for a in node["actions"]:
                if a["kind"] == "bet" and not a.get("allin") and node["pot"]:
                    sizes.add(round(100 * a["amount"] / node["pot"]))
        if acc:
            nodes[name] = {"title": title, "actor": actor, "sizes": sorted(sizes), "groups": _normalize(acc)}
            if evs:
                nodes[name]["evs"] = _normalize_ev(evs)
    flop = query(_flop_path(query, spot.family))
    return {"version": VERSION, "key": key, "id": spot.ident, "family": spot.family, "board": spot.board,
            "texture": spot.texture, "pattern": studyspots.suit_pattern(spot.board),
            "advantages": _advantages(flop) if flop["type"] == "action" else None, "nodes": nodes}


def _flop_path(query: Callable[[list], dict], family: str) -> list:
    """Chemin du nœud de c-bet (le check forcé de la BB traversé en SRP et pot 4bet)."""
    steps = dict((n, s) for n, _, s in lines(family))["cbet"]
    path, node = [], query([])
    for kind in steps:
        index = _choose(node, kind)
        if index is None:
            break
        path = path + [{"type": "action", "index": index}]
        node = query(path)
    return path


def save_plan(data: dict) -> None:
    documents.put(db.current(), "plan", data["key"], data)


def load_plan(key: str) -> Optional[dict]:
    """Le plan de jeu enregistré (dans la base ; gardé en mémoire tant qu'aucun plan ne change)."""
    data = documents.get(db.current(), "plan", key)
    return data if isinstance(data, dict) and data.get("version") in READABLE else None


def extract_and_save(session: postflop.Session, spot: studyspots.StudySpot) -> dict:
    key = postflop.study_key(session.request)  # l'étude ouverte, à sa précision
    data = extract(session.node, spot, key)
    save_plan(data)
    return json.loads(json.dumps(data))  # tel qu'il sera relu


def missing(family: Optional[str] = None) -> list[str]:
    """Spots résolus (arbre actuel) sans plan extrait, ou lu par une version d'avant : ceux d'une famille, ou de
    toutes (heads-up et 6-max)."""
    wanted = (family,) if family else plan_families()
    out = []
    for ident, meta in studyspots.spot_studies(wanted).items():
        if meta.get("family") in wanted and (load_plan(meta["key"]) or {}).get("version") != VERSION:
            out.append(ident)
    return sorted(out)


def plans(family: str) -> list[dict]:
    out = []
    for meta in studyspots.spot_studies((family,)).values():
        if meta.get("family") != family:
            continue
        data = load_plan(meta["key"])
        if data:
            out.append(data)
    return sorted(out, key=lambda d: (studyspots.TEXTURES.index(d["texture"]), d["id"]))


# --- Synthèse ----------------------------------------------------------------------------------------

# Trois stratégies de c-bet, selon la fréquence de mise du solveur au flop.
RANGE_BET, CHECK_RANGE = 0.70, 0.35
STRATEGIES = (
    ("range", "Miser range", f"c-bet d'au moins {round(100 * RANGE_BET)} % : une mise avec presque toute la range"),
    ("mixte", "Stratégie mixte", f"c-bet de {round(100 * CHECK_RANGE)} à {round(100 * RANGE_BET)} % : on mise une partie "
                                 "des mains, on checke les autres"),
    ("check", "Checker range", f"c-bet de {round(100 * CHECK_RANGE)} % ou moins : on checke presque tout"),
)
STRATEGY_LABEL = {key: label for key, label, _ in STRATEGIES}
# Tailles de la c-bet, regroupées (classes de action_class).
SIZE_GROUPS = (("petite", "petite mise", ("small",)), ("moyenne", "mise moyenne", ("medium",)),
               ("grosse", "grosse mise", ("big", "overbet")), ("tapis", "tapis", ("allin",)))
SIZE_LABEL = {key: label for key, label, _ in SIZE_GROUPS}
MIN_SHARE = 0.02  # une famille de mains sous 2 % de la range n'apparaît pas dans les règles

# Catégories de flop : hauteur (carte la plus haute) et structure, plus les flops pairés et monotones.
HEIGHTS = (("haut", "Haut", "As ou Roi"), ("moyen", "Moyen", "Dame, Valet ou Dix"), ("bas", "Bas", "9 ou moins"))
SHAPES = (("sec", "Sec", "trois couleurs, pas de quinte"), ("couleur", "Deux couleurs", "tirage couleur possible"),
          ("connecte", "Connecté", "cartes proches, quintes possibles"))
SPECIALS = (("paire", "Pairé", "une paire au board"), ("monotone", "Monotone", "trois cartes d'une couleur"))
CATEGORIES = tuple((f"{h}-{s}", f"{hl} · {sl.lower()}") for h, hl, _ in HEIGHTS for s, sl, _ in SHAPES) + tuple(
    (k, label) for k, label, _ in SPECIALS)
CATEGORY_LABEL = dict(CATEGORIES)

# Quatre familles de mains pour les règles simples (les onze de BUCKETS restent dans le détail).
GROUPS = (("fortes", "Fortes", ("nuts", "overpair", "tp_good"), "deux paires et mieux, overpair, top pair bon kicker"),
          ("moyennes", "Moyennes", ("tp_weak", "midpair", "weakpair"), "top pair petit kicker, paires moyennes et petites"),
          ("tirages", "Tirages", ("fd", "sd"), "tirages couleur et quinte"),
          ("rien", "Rien", ("weakdraw", "high", "air"), "hauteur, gutshots, backdoors"))
CARD_TEXT = {"over": "plus haute que les cartes du flop", "brick": "petite carte qui ne change rien",
             "paired": "la carte paire le board", "flush": "troisième carte d'une couleur",
             "straight": "ouvre ou complète une quinte"}


def board_category(board: list[str]) -> str:
    ranks = [RANK_VALUE[c[0]] for c in board[:3]]
    suits = {c[1] for c in board[:3]}
    if len(set(ranks)) < 3:
        return "paire"
    if len(suits) == 1:
        return "monotone"
    top = max(ranks)
    height = "haut" if top >= 13 else "moyen" if top >= 10 else "bas"
    low = [1 if r == 14 else r for r in ranks]
    if max(ranks) - min(ranks) <= 4 or max(low) - min(low) <= 4:
        shape = "connecte"
    else:
        shape = "couleur" if len(suits) == 2 else "sec"
    return f"{height}-{shape}"


def aggression(freqs: dict) -> float:
    return sum(freqs.get(c, 0.0) for c in AGGRESSIVE)


def main_size(freqs: dict) -> Optional[str]:
    sized = {c: freqs.get(c, 0.0) for c in AGGRESSIVE if freqs.get(c)}
    return max(sized, key=sized.get) if sized else None


def strategy_of(share: float) -> str:
    """La stratégie d'une fréquence de c-bet, lue au pour cent près (comme elle s'affiche)."""
    pct = round(100 * share)
    return "range" if pct >= round(100 * RANGE_BET) else "check" if pct <= round(100 * CHECK_RANGE) else "mixte"


def size_group(size: Optional[str]) -> str:
    """La taille regroupée d'une classe de mise (small… allin) ; sans mise, « petite »."""
    return next((key for key, _, classes in SIZE_GROUPS if size in classes), "petite")


def max_fold(sizes: list[int]) -> Optional[float]:
    """Le fold au-delà duquel une mise de cette taille (% du pot) rapporte d'elle-même : taille / (pot + taille)."""
    if not sizes:
        return None
    size = sorted(sizes)[len(sizes) // 2] / 100
    return size / (1 + size)


def aggressor_of(family: str) -> int:
    """Joueur à l'initiative : 0 = hors de position (la BB en pot 3bet heads-up), 1 = en position (le bouton en SRP
    et en pot 4bet heads-up)."""
    return 0 if studyspots.family_info(family)["oop_initiative"] else 1


def flop_row(data: dict) -> dict:
    """Une ligne du tableau des flops : catégorie, stratégie, c-bet et sa taille, avantages, défense face à la
    c-bet."""
    cbet = data["nodes"].get("cbet", {}).get("groups", {}).get("flop", {"f": {}})
    facing = data["nodes"].get("vs_cbet", {}).get("groups", {}).get("flop", {"f": {}})["f"]
    share = aggression(cbet["f"])
    size = main_size(cbet["f"])
    adv = data.get("advantages") or {"eq": [0.5, 0.5], "nuts": [0.0, 0.0]}
    a = aggressor_of(data["family"])
    return {"id": data["id"], "board": data["board"], "texture": data["texture"], "strategy": strategy_of(share),
            "category": board_category(data["board"]), "suit_pattern": data["pattern"], "cbet": share,
            "size": size, "size_group": size_group(size), "sizes": data["nodes"].get("cbet", {}).get("sizes", []),
            "fold": facing.get("fold", 0.0) if facing else None, "raise": aggression(facing) if facing else None,
            "eq_adv": adv["eq"][a] - adv["eq"][1 - a], "nut_adv": adv["nuts"][a] - adv["nuts"][1 - a]}


def merge(entries: list[dict]) -> dict:
    """Moyenne de plusieurs groupes (un par flop) : chaque flop compte autant, chaque famille de mains selon
    sa part de range."""
    if not entries:
        return {"n": 0, "flops": 0, "f": {}, "b": {}}
    f: dict = {}
    b: dict = {}
    for e in entries:
        for c, v in e["f"].items():
            f[c] = f.get(c, 0.0) + v / len(entries)
        for k, bk in e["b"].items():
            acc = b.setdefault(k, {"w": 0.0, "f": {}})
            acc["w"] += bk["w"] / len(entries)
            for c, v in bk["f"].items():
                acc["f"][c] = acc["f"].get(c, 0.0) + bk["w"] * v
    for acc in b.values():
        total = sum(acc["f"].values()) or 1.0
        acc["f"] = {c: v / total for c, v in acc["f"].items()}
    return {"n": sum(e["n"] for e in entries), "flops": len(entries), "f": f, "b": b}


def _labels(keys: list[str]) -> str:
    return " · ".join(BUCKET_LABEL[k] for k in keys)


def bettor_rules(merged: dict, river: bool = False) -> list[tuple[str, str]]:
    """Détail : mise / check / mélange, par famille fine de mains. À la river, la mise se lit en valeur ou en
    bluff."""
    out: dict[str, list[str]] = {}
    for key, _ in BUCKETS:
        bk = merged["b"].get(key)
        if not bk or bk["w"] < MIN_SHARE:
            continue
        bet = aggression(bk["f"])
        if bet >= 0.7:
            what = ("Mise pour la valeur" if key in VALUE else "Bluffe") if river else "Mise"
        elif bet <= 0.3:
            what = "Check"
        else:
            what = "Mélange"
        out.setdefault(what, []).append(key)
    order = ("Mise", "Mise pour la valeur", "Bluffe", "Mélange", "Check")
    return [(what, _labels(out[what])) for what in order if what in out]


def _defend_verdict(fold: float, call: float, raise_: float) -> str:
    if raise_ >= 0.5:
        return "Relance"
    if fold >= 0.7:
        return "Folde"
    if call >= 0.7 or (call >= 0.5 and fold < 0.3):
        return "Paie"
    return "Paie ou relance" if raise_ >= 0.25 else "Paie ou folde"


def defender_rules(merged: dict) -> list[tuple[str, str]]:
    """Détail face à une mise : relance / paie / folde / mélange, par famille fine de mains."""
    out: dict[str, list[str]] = {}
    for key, _ in BUCKETS:
        bk = merged["b"].get(key)
        if not bk or bk["w"] < MIN_SHARE:
            continue
        out.setdefault(_defend_verdict(bk["f"].get("fold", 0.0), bk["f"].get("call", 0.0), aggression(bk["f"])),
                       []).append(key)
    order = ("Relance", "Paie ou relance", "Paie", "Paie ou folde", "Folde")
    return [(what, _labels(out[what])) for what in order if what in out]


def simple_rules(merged: dict, defender: bool = False, river: bool = False) -> list[dict]:
    """Les quatre familles de mains (fortes, moyennes, tirages, rien) : part de la range, ce qu'elles font et
    à quelle fréquence. kind : mise, mélange, check (ou relance, paie, folde face à une mise)."""
    out = []
    for key, label, members, _ in GROUPS:
        if river and key == "tirages":
            continue  # plus de tirage à la river : les tirages ratés sont dans « rien »
        w = sum(merged["b"][m]["w"] for m in members if m in merged["b"])
        if w < MIN_SHARE:
            continue
        freqs: dict = {}
        for m in members:
            bk = merged["b"].get(m)
            if bk:
                for c, v in bk["f"].items():
                    freqs[c] = freqs.get(c, 0.0) + bk["w"] * v / w
        if defender:
            fold, call, raise_ = freqs.get("fold", 0.0), freqs.get("call", 0.0), aggression(freqs)
            verdict = _defend_verdict(fold, call, raise_)
            kind = {"Relance": "raise", "Folde": "fold", "Paie": "call"}.get(verdict, "mix")
            pct = {"raise": raise_, "fold": fold, "call": call}.get(kind, call)
        else:
            bet = aggression(freqs)
            kind = "bet" if bet >= 0.65 else "check" if bet <= 0.35 else "mix"
            verdict = {"bet": "Mise", "check": "Checke", "mix": "Mélange"}[kind]
            if river and kind == "bet":
                verdict = "Bluffe" if key == "rien" else "Mise (valeur)"
            pct = bet
        out.append({"key": key, "label": label, "share": w, "kind": kind, "verdict": verdict, "pct": pct,
                    "size": None if defender else main_size(freqs)})
    return out


def _pts(x: float) -> str:
    return f"{100 * x:+.0f} pts".replace("-", "−")


def why(strategy: str, eq_adv: float, nut_adv: float, who: str, other: str) -> str:
    """Pourquoi cette stratégie de c-bet, en deux phrases, d'après l'avantage d'équité et de nuts de celui qui a
    l'initiative."""
    cap = lambda text: text[0].upper() + text[1:]  # noqa: E731
    equity = ("un avantage d'équité" if eq_adv >= 0.05 else "moins d'équité" if eq_adv <= -0.05
              else "à peu près autant d'équité")
    nuts = ("plus de mains très fortes" if nut_adv >= 0.02 else "moins de mains très fortes" if nut_adv <= -0.02
            else "autant de mains très fortes")
    facts = f"{cap(who)} a {equity} ({_pts(eq_adv)}) et {nuts} ({_pts(nut_adv)}) que {other}."
    if strategy == "range":
        reason = f"{cap(other)} a peu de mains qui supportent une mise : une mise avec tout coûte peu et rapporte."
    elif nut_adv <= -0.02:
        reason = f"{cap(other)} a plus de mains très fortes : on mise moins et on garde des mains fortes dans les checks."
    elif strategy == "mixte":
        reason = ("On mise les mains fortes, des tirages et une partie de l'air, et on checke une partie des mains "
                  "moyennes qui préfèrent aller à l'abattage.")
    else:
        reason = "Sans avantage net, on checke presque tout : les rares mises sont des mains très fortes et quelques bluffs."
    return facts + " " + reason


def defense_text(fold: float, raise_: float, limit: Optional[float], defender: str, size: str) -> str:
    """La défense face à la c-bet en une phrase, avec le fold au-delà duquel la mise rapporte d'elle-même."""
    cap = lambda text: text[0].upper() + text[1:]  # noqa: E731
    text = f"{cap(defender)} folde {round(100 * fold)} % et relance {round(100 * raise_)} %"
    if limit is None:
        return text + "."
    verdict = ("défense large" if fold <= limit - 0.03 else "défense serrée" if fold >= limit + 0.08
               else "à peu près la défense minimale")
    return (text + f" : {verdict} (face à une {size}, la mise rapporte d'elle-même dès que la défense folde plus de "
            f"{round(100 * limit)} %).")


SIZE_SHORT = {"small": "petite", "medium": "moyenne", "big": "grosse", "overbet": "overbet", "allin": "tapis"}


def size_mix(freqs: dict, pcts: Iterable[int] = ()) -> list[dict]:
    """Les tailles jouées, de la plus jouée à la moins jouée : classe, part des mises, tailles en % du pot de cette
    classe (pcts : les tailles de l'arbre à ce nœud)."""
    bets = {c: freqs.get(c, 0.0) for c in AGGRESSIVE if c != "raise" and freqs.get(c)}
    total = sum(bets.values())
    if not total:
        return []
    return [{"class": c, "label": SIZE_SHORT[c], "share": v / total,
             "pcts": sorted({x for x in pcts if c != "allin" and size_class(x) == c})}
            for c, v in sorted(bets.items(), key=lambda cv: -cv[1])]


def card_rules(node: Optional[dict], defender: bool = False, river: bool = False,
               sizes: Iterable[int] = ()) -> list[dict]:
    """Par type de carte (turn ou river) : fréquence de mise (ou de fold), tailles préférées (mises), règles
    simples et détail. sizes : les tailles de l'arbre à ce nœud, en % du pot."""
    if not node:
        return []
    out = []
    sizes = tuple(sizes)
    for key, label in CARDS:
        entries = node.get(key, [])
        if not entries:
            continue
        merged = merge(entries)
        out.append({"key": key, "label": label, "text": CARD_TEXT[key], "aggr": aggression(merged["f"]),
                    "fold": merged["f"].get("fold", 0.0), "size": main_size(merged["f"]), "n": merged["flops"],
                    "sizes": [] if defender else size_mix(merged["f"], sizes),
                    "rules": simple_rules(merged, defender, river),
                    "detail": defender_rules(merged) if defender else bettor_rules(merged, river)})
    return out


def _facing(merged: dict) -> Optional[dict]:
    if not merged["flops"]:
        return None
    return {"fold": merged["f"].get("fold", 0.0), "call": merged["f"].get("call", 0.0), "raise": aggression(merged["f"]),
            "rules": simple_rules(merged, defender=True), "detail": defender_rules(merged)}


# --- Niveaux du plan : du plus simple au plus précis -----------------------------------------------------------
# Deux sortes de règles :
# - une action pour toute la range (« range ») : celle qui vaut le plus sur les flops résolus de la case (tous les flops
#   ou un type de flop) ; « le bouton mise toute sa range petit » ;
# - des fractions (« equite ») : la range découpée en quatre boîtes d'équité (EQUITY, face à la range de l'adversaire,
#   avant d'agir), chacune détaillée par famille de mains ; chaque case (boîte et famille) joue la part de chaque action
#   que lui donne le solveur, en moyenne sur les flops ou les cartes du type, et les mises (ou les relances) réunies en
#   une seule taille par type de flop ou de carte, celle qui coûte le moins (« une ») ; ou les tailles du solveur
#   (« solveur »). On lit ainsi qui mise d'abord : les familles d'une boîte, de celle qui mise le plus à celle qui
#   mise le moins (des tirages avant les hauteurs, des bluffs river sans abattage…).
# La perte d'une règle : ce qu'elle coûte face à la stratégie du solveur, en % du pot par main de la range, en moyenne
# (chaque flop, chaque carte compte autant), face à un adversaire qui joue la stratégie du solveur sans s'adapter (un
# adversaire qui s'adapte ferait payer un peu plus : refine_spots le mesure). Chaque niveau a sa suite : les autres
# décisions de la main (face au check-raise, 2e et 3e barrels, c-bet retardée…), toujours selon le type de carte de la
# turn ou de la river, en fractions par boîte d'équité et une taille par type de carte ; leur perte, en % du pot de la
# street. La suite est lue aux nœuds du solveur, après sa propre stratégie du flop (refine_spots l'affine avec le
# niveau imposé).
SIZED = ("small", "medium", "big", "overbet", "allin")
RAISES = ("raise", "allin")
# (clé, première décision, décisions suivantes) ; une décision : (une règle par type de flop ou de carte, mains :
# « range » ou « equite », tailles : « une » ou « solveur »)
SUITE = (True, "equite", "une")
DETAIL = (True, "equite", "solveur")
STEPS = {
    "attack": (("une", (False, "range", "une"), SUITE), ("flop", (True, "range", "une"), SUITE),
               ("equite", (True, "equite", "une"), SUITE), ("detail", DETAIL, DETAIL)),
    "defense": (("une", (False, "equite", "une"), SUITE), ("flop", (True, "equite", "une"), SUITE),
                ("detail", DETAIL, DETAIL)),
}
# Les décisions de chaque côté, dans l'ordre de la main : la première (au flop), puis la suite (celles de la famille).
SIDE_NODES = {"attack": ("cbet", "vs_xr", "vs_stab", "barrel", "delayed", "vs_probe", "barrel3", "river_xx"),
              "defense": ("vs_cbet", "stab", "vs_barrel", "probe", "vs_barrel3", "vs_river_xx")}


def _substitute(cls: Optional[str], available: Iterable[str]) -> Optional[str]:
    """La classe jouée à la place de cls sur un flop qui ne l'a pas : la taille (ou la relance) la plus proche."""
    available = list(available)
    if cls is None or cls in available:
        return cls
    for family in (SIZED, RAISES):
        near = [c for c in available if c in family]
        if cls in family and near:
            return min(near, key=lambda c: abs(family.index(c) - family.index(cls)))
    return None


def _hand_key(key: str, hands: str) -> str:
    """La case d'une règle pour une case de la range (« e1|fd ») : toute la range, sa boîte d'équité, ou elle-même."""
    if hands == "range":
        return "range"
    if hands == "equite":
        return key.split("|")[0]
    return key


def _grouped(ev: dict, hands: str) -> dict:
    out: dict = {}
    for key, g in ev["b"].items():
        acc = out.setdefault(_hand_key(key, hands), {"r": 0.0, "s": 0.0, "e": {}})
        acc["r"] += g["r"]
        acc["s"] += g["s"]
        for c, v in g["e"].items():
            acc["e"][c] = acc["e"].get(c, 0.0) + v
    return out


def _eligible(available: list[list[str]]) -> list[str]:
    """Les actions qu'une règle peut prescrire sur ces flops : celles qu'ils ont tous (« miser grosse » ne veut rien
    dire sur un flop où l'arbre n'a qu'une petite mise) ; si aucune mise n'est commune, chaque taille, jouée à la
    taille la plus proche sur chaque flop (_substitute)."""
    union = sorted({c for classes in available for c in classes}, key=ORDER.index)
    common = [c for c in union if all(c in classes for classes in available)]
    if any(c in SIZED for c in common) or not any(c in SIZED for c in union):
        return common
    return [c for c in union if all(_substitute(c, classes) for classes in available)]


def _by_where(entries: list[tuple[str, dict]], by_flop: bool) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for category, ev in entries:
        out.setdefault(category if by_flop else "tous", []).append(ev)
    return out


def simplify(entries: list[tuple[str, dict]], by_flop: bool, hands: str = "range") -> Optional[dict]:
    """Le meilleur plan d'une action par case. entries : [(catégorie du flop ou type de carte, ev_summary du nœud)],
    chacune comptant pour son nombre de nœuds (« n » : les cartes d'un type) ; by_flop : une règle par catégorie
    (sinon une pour toutes, « tous ») ; hands : « range », « equite » (une action par boîte) ou les cases. Renvoie sa
    perte moyenne (part du pot), celle de chaque catégorie, et la règle de chaque case
    {catégorie: {mains: {class, share}}}."""
    if not entries:
        return None
    allowed = {where: _eligible([ev["classes"] for ev in evs]) for where, evs in _by_where(entries, by_flop).items()}
    flops, cells = [], {}
    for category, ev in entries:
        where = category if by_flop else "tous"
        weight = ev.get("n", 1)
        groups = _grouped(ev, hands)
        flops.append((where, groups, ev["classes"], weight))
        for hk, g in groups.items():
            cell = cells.setdefault((where, hk), {"v": dict.fromkeys(allowed[where], 0.0), "ok": set(allowed[where]),
                                                  "r": 0.0, "n": 0})
            cell["r"] += weight * g["r"]
            cell["n"] += weight
            for c in allowed[where]:
                cell["v"][c] += weight * g["e"].get(_substitute(c, ev["classes"]), 0.0)
    # à valeur égale, l'action la plus simple (check avant mise, petite mise avant grosse)
    choice = {key: max(sorted(cell["ok"], key=ORDER.index), key=lambda c: round(cell["v"][c], 6))
              for key, cell in cells.items() if cell["ok"]}
    losses: dict[str, list[tuple[float, float]]] = {}
    for where, groups, available, weight in flops:
        loss = 0.0
        for hk, g in groups.items():
            sub = _substitute(choice.get((where, hk)), available)
            loss += g["s"] - g["e"].get(sub, g["s"]) if sub else 0.0
        losses.setdefault(where, []).append((max(loss, 0.0), weight))
    rules: dict = {}
    for (where, hk), c in choice.items():
        cell = cells[(where, hk)]
        rules.setdefault(where, {})[hk] = {"class": c, "share": cell["r"] / cell["n"]}
    return {"kind": "pure", "loss": _mean([x for xs in losses.values() for x in xs]),
            "by": {w: _mean(xs) for w, xs in losses.items()}, "flops": {w: len(xs) for w, xs in losses.items()},
            "rules": rules}


def _is_facing(classes: Iterable[str]) -> bool:
    """Un nœud où l'on fait face à une mise (fold, call, relance), et non où l'on mise ou checke."""
    return any(c in ("fold", "call") for c in classes)


def aggressive_share(f: dict[str, float]) -> float:
    return sum(v for c, v in f.items() if c in AGGRESSIVE)


def _merge(f: dict[str, float], size: Optional[str], family: tuple) -> dict[str, float]:
    """Des fractions d'actions, les mises (ou les relances : family) réunies en une seule taille (size, sinon celles
    du solveur)."""
    if size is None:
        return dict(f)
    out: dict[str, float] = {}
    for c, v in f.items():
        to = size if c in family else c
        out[to] = out.get(to, 0.0) + v
    return out


def _value(g: dict, mix: dict[str, float], classes: list[str]) -> float:
    """L'EV d'une case (part du pot) qui joue ces fractions ; une action absente de cet arbre se joue à la plus proche,
    ou au check (au call)."""
    passive = next((c for c in ("check", "call", "fold") if c in classes), None)
    return sum(v * g["e"].get(_substitute(c, classes) or passive, 0.0) for c, v in mix.items())


def _median(values: list) -> Optional[float]:
    values = sorted(x for x in values if x is not None)
    return values[len(values) // 2] if values else None


def mixes(entries: list[tuple[str, dict]], by_flop: bool, sizes: str = "une") -> Optional[dict]:
    """La règle en fractions d'une décision : par catégorie (de flop ou de carte ; sinon « tous »), chaque case de la
    range (boîte d'équité et famille) joue les fractions moyennes du solveur, les mises (ou les relances) réunies en
    la taille qui coûte le moins (sizes « une »), ou aux tailles du solveur (« solveur »). Renvoie, comme simplify, la
    perte moyenne, celle de chaque catégorie et le nombre d'entrées, et pour chaque catégorie : la taille (« size » et
    « sizes » : % du pot d'une mise, × la mise pour une relance), les fractions de toute la range (« freq ») et de
    chaque boîte (« boxes » : sa part de la range, ses fractions, ses familles de la plus agressive à la moins
    agressive) ; « families » : les familles de chaque boîte, toutes catégories ensemble."""
    if not entries:
        return None
    rules, losses, overall = {}, {}, {}
    for where, evs in _by_where(entries, by_flop).items():
        allowed = _eligible([ev["classes"] for ev in evs])
        family = RAISES if _is_facing(allowed) else SIZED
        cells: dict[str, dict] = {}
        for ev in evs:
            w = ev.get("n", 1)
            for k, g in ev["b"].items():
                cell = cells.setdefault(k, {"r": 0.0, "f": {}})
                cell["r"] += w * g["r"]
                for c, v in g.get("f", {}).items():
                    to = _substitute(c, allowed) or c
                    cell["f"][to] = cell["f"].get(to, 0.0) + w * v
        mix = {k: {c: v / cell["r"] for c, v in cell["f"].items()} for k, cell in cells.items() if cell["r"]}
        options = [None] if sizes == "solveur" else [c for c in allowed if c in family] or [None]
        best = None
        for size in options:  # (à perte égale, la plus petite taille)
            merged = {k: _merge(m, size, family) for k, m in mix.items()}
            per = [(max(sum(g["s"] - _value(g, merged.get(k, {}), ev["classes"]) for k, g in ev["b"].items()), 0.0),
                    ev.get("n", 1)) for ev in evs]
            if best is None or round(_mean(per), 6) < round(best[0], 6):
                best = (_mean(per), size, merged, per)
        loss, size, merged, per = best
        losses[where] = per
        total = sum(c["r"] for c in cells.values()) or 1.0
        boxes: dict[str, dict] = {}
        for k, m in merged.items():
            box, fam = k.split("|")
            b = boxes.setdefault(box, {"r": 0.0, "f": {}, "families": []})
            r = cells[k]["r"] / total
            b["r"] += r
            for c, v in m.items():
                b["f"][c] = b["f"].get(c, 0.0) + r * v
            b["families"].append({"key": fam, "r": r, "f": m})
            o = overall.setdefault(box, {}).setdefault(fam, {"r": 0.0, "f": {}})
            o["r"] += cells[k]["r"]
            for c, v in m.items():
                o["f"][c] = o["f"].get(c, 0.0) + cells[k]["r"] * v
        for b in boxes.values():
            b["f"] = {c: v / b["r"] for c, v in b["f"].items()} if b["r"] else {}
            for fam in b["families"]:
                fam["r"] = fam["r"] / b["r"] if b["r"] else 0.0
            b["families"].sort(key=lambda x: (-round(aggressive_share(x["f"]), 4), -x["r"]))
        freq: dict[str, float] = {}
        for b in boxes.values():
            for c, v in b["f"].items():
                freq[c] = freq.get(c, 0.0) + b["r"] * v
        used = sorted({c for b in boxes.values() for c, v in b["f"].items() if v > 0.005 and c in AGGRESSIVE},
                      key=ORDER.index)
        pcts = {c: _median([(ev["raises"] if c == "raise" else ev["sizes"]).get(_substitute(c, ev["classes"]))
                            for ev in evs]) for c in used if c != "allin"}
        rules[where] = {"size": size, "sizes": {c: x for c, x in pcts.items() if x is not None}, "freq": freq,
                        "boxes": boxes, "facing": family is RAISES}
    families = {}
    for box, fams in overall.items():
        total = sum(x["r"] for x in fams.values()) or 1.0
        families[box] = sorted(({"key": fam, "r": x["r"] / total, "f": {c: v / x["r"] for c, v in x["f"].items()}}
                                for fam, x in fams.items() if x["r"]),
                               key=lambda x: (-round(aggressive_share(x["f"]), 4), -x["r"]))
    every = [x for xs in losses.values() for x in xs]
    return {"kind": "mix", "loss": _mean(every), "by": {w: _mean(xs) for w, xs in losses.items()},
            "flops": {w: len(xs) for w, xs in losses.items()}, "rules": rules, "families": families}


def pure_options(entries: list[tuple[str, dict]]) -> list[tuple[str, float]]:
    """Chaque action jouée par toute la range sur tous les flops, et ce qu'elle coûte : de la moins chère à la plus
    chère (« miser toute la range petit », « checker toute la range »…)."""
    out = []
    for c in _eligible([ev["classes"] for _, ev in entries]):
        losses = [max(sum(g["s"] - g["e"].get(_substitute(c, ev["classes"]), 0.0) for g in ev["b"].values()), 0.0)
                  for _, ev in entries]
        out.append((c, sum(losses) / len(losses)))
    return sorted(out, key=lambda cl: (round(cl[1], 6), ORDER.index(cl[0])))


def class_sizes(entries: list[tuple[str, dict]]) -> dict[str, int]:
    """La taille de chaque classe de mise (% du pot), la médiane sur les flops (ou les types de carte)."""
    found: dict[str, list[int]] = {}
    for _, ev in entries:
        for c, pct in ev.get("sizes", {}).items():
            found.setdefault(c, []).append(pct)
    return {c: sorted(v)[len(v) // 2] for c, v in found.items()}


def decide(entries: list[tuple[str, dict]], by_flop: bool, hands: str, sizes: str) -> Optional[dict]:
    """Une décision d'un niveau : une action pour toute la range (simplify) ou des fractions (mixes)."""
    found = simplify(entries, by_flop, hands) if hands == "range" else mixes(entries, by_flop, sizes)
    return dict(found, by_flop=by_flop, hands=hands, sizes_mode=sizes) if found else None


def _streets(later: dict[str, list], by_card: bool, hands: str, sizes: str) -> dict[str, dict]:
    return {name: decide(e, by_card, hands, sizes) for name, e in later.items()}


def levels(entries: list[tuple[str, dict]], side: str, later: Optional[dict[str, list]] = None) -> Optional[dict]:
    """Les niveaux d'un côté (side : « attack », la c-bet ; « defense », la réponse à la c-bet), du plus simple au plus
    précis, chacun avec sa règle et sa perte, et sa suite (« streets » : later, {nœud: entrées} des décisions
    suivantes de SIDE_NODES, de même forme que entries, avec le type de carte à la turn et à la river) ; en attaque,
    toutes les actions jouées par toute la range (options)."""
    if not entries:
        return None
    later = {name: e for name, e in (later or {}).items() if e}
    out = {"flops": len(entries), "sizes": class_sizes(entries),
           "steps": [dict(decide(entries, *first), key=key, streets=_streets(later, *rest))
                     for key, first, rest in STEPS[side]]}
    if side == "attack":
        out["options"] = pure_options(entries)
    return out


def node_evs(data: dict, name: str) -> dict[str, dict]:
    """L'EV des actions d'un nœud d'un plan extrait, par groupe (« flop » ou type de la carte) ; rien avant la version
    5 (les niveaux en ont besoin : « Préparer le plan » relit les études)."""
    if data.get("version") != VERSION:
        return {}
    return (data["nodes"].get(name) or {}).get("evs") or {}


def level_entries(data: list[dict], name: str) -> list[tuple[str, dict]]:
    """Les entrées des niveaux pour ce nœud : (catégorie du flop, EV) au flop, (type de la carte, EV) ensuite."""
    return [(board_category(d["board"]) if group == "flop" else group, ev)
            for d in data for group, ev in node_evs(d, name).items()]


def plan_levels(data: list[dict]) -> dict:
    """Les niveaux en attaque et en défense de ces plans extraits, avec leur suite."""
    return {side: levels(level_entries(data, nodes[0]), side, {name: level_entries(data, name) for name in nodes[1:]})
            for side, nodes in SIDE_NODES.items()}


def size_text(cls: str, sizes: dict) -> str:
    """La taille d'une classe : « 33 % » d'une mise (du pot), « ×4 » d'une relance (la mise) ; rien sans taille."""
    if cls not in sizes:
        return ""
    if cls == "raise":
        return "×" + f"{sizes[cls]:g}".replace(".", ",")
    return f"{sizes[cls]} %"


def class_text(cls: str, sizes: dict) -> str:
    """« petite mise (33 %) », « relance (×4) », « check »…"""
    size = size_text(cls, sizes)
    return CLASS_LABEL[cls] + (f" ({size})" if size else "")


def _mean(pairs: list[tuple[float, float]]) -> Optional[float]:
    """Moyenne pondérée de (valeur, poids) ; None sans poids."""
    weight = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / weight if weight else None


def baseline(plan: dict) -> dict:
    """Les grands chiffres du plan, tous flops ensemble : c-bet, défense face à la c-bet, au check-raise, aux
    barrels de la turn et de la river, 2e barrel, bluffs de la river (la famille « rien »), mises quand celui qui a
    l'initiative checke. Pour ajuster le plan à un adversaire (field_plan)."""
    groups = plan["groups"]

    def over(get) -> Optional[float]:
        return _mean([(v, len(g["flops"])) for g in groups for v in [get(g)] if v is not None])

    def cards(get_rows, value) -> Optional[float]:
        return _mean([(value(r), r["n"]) for g in groups for r in get_rows(g) if value(r) is not None])

    def rien(r: dict) -> Optional[float]:
        return next((x["pct"] for x in r["rules"] if x["key"] == "rien"), None)
    return {
        "cbet": _mean([(r["cbet"], 1) for r in plan["flops"]]),
        "fold_cbet": over(lambda g: (g["defense"]["vs_cbet"] or {}).get("fold")),
        "raise_cbet": over(lambda g: (g["defense"]["vs_cbet"] or {}).get("raise")),
        "fold_xr": over(lambda g: (g["attack"]["vs_xr"] or {}).get("fold")),
        "barrel": cards(lambda g: g["attack"]["turn"], lambda r: r["aggr"]),
        "river_bluff": cards(lambda g: g["attack"]["river"], rien),
        "river_value": cards(lambda g: g["attack"]["river"], lambda r: r["aggr"]),
        "fold_turn": cards(lambda g: g["defense"]["vs_barrel"], lambda r: r["fold"]),
        "fold_river": cards(lambda g: g["defense"]["vs_barrel3"], lambda r: r["fold"]),
        "stab": over(lambda g: (g["defense"]["stab"] or {}).get("aggr")),
        "probe": cards(lambda g: g["defense"]["probe"], lambda r: r["aggr"]),
    }


def family_plan(family: str) -> dict:
    """Le plan d'une famille de pots, à partir des plans extraits de ses flops résolus : les flops regroupés par
    stratégie de c-bet et taille de mise ; pour chaque groupe, l'attaque (celui qui a l'initiative) et la défense."""
    data = plans(family)
    info = studyspots.family_info(family)
    a = aggressor_of(family)
    oop, ip = seat_words(family)
    who, other = (oop, ip) if a == 0 else (ip, oop)
    out = {"family": family, "name": info["name"], "label": info["label"], "title": studyspots.family_title(family),
           "ring": family not in studyspots.FAMILIES, "pair": info.get("pair"), "count": len(data),
           "missing": len(missing(family)), "who": who, "other": other, "groups": [], "strategies": []}
    rows = [flop_row(d) for d in data]
    out["flops"] = rows
    order = [c for c, _ in CATEGORIES]
    for strategy, label, text in STRATEGIES:
        keys = []
        for size, size_label, _ in SIZE_GROUPS:
            members = [(d, r) for d, r in zip(data, rows) if r["strategy"] == strategy and r["size_group"] == size]
            if not members:
                continue
            group = _group(members, strategy, size, who, other)
            out["groups"].append(group)
            keys.append(group["key"])
        out["strategies"].append({"key": strategy, "label": label, "text": text, "groups": keys,
                                  "flops": sum(1 for r in rows if r["strategy"] == strategy)})
    for group in out["groups"]:
        group["flops"].sort(key=lambda r: (order.index(r["category"]), -r["cbet"]))
    out["levels"] = dict(plan_levels(data), read=sum(1 for d in data if node_evs(d, "cbet")),
                         older=sum(1 for d in data if d.get("version") != VERSION))
    sample = refine_sample(data)
    out["refine"] = {"flops": len(sample), "seconds": {f"{side}-{mode}": refine_seconds(family, sample, side, mode)
                                                       for side, mode in REFINE_MODES}}
    attach_refined(out["levels"], family)
    out["baseline"] = baseline(out)
    return out


def _group(members: list[tuple[dict, dict]], strategy: str, size: str, who: str, other: str) -> dict:
    """Un groupe de flops (même stratégie, même taille) : attaque et défense, flop par flop puis moyennées."""
    def gather(name: str) -> dict:
        """Les groupes de ce nœud, flop par flop, rangés par type de carte."""
        by_card: dict[str, list] = {}
        for d, _ in members:
            for card, g in d["nodes"].get(name, {}).get("groups", {}).items():
                by_card.setdefault(card, []).append(g)
        return by_card

    def sizes_of(name: str) -> set[int]:
        """Les tailles de mise de l'arbre à ce nœud (% du pot), sur les flops du groupe."""
        return {x for d, _ in members for x in d["nodes"].get(name, {}).get("sizes", [])}

    rows = [r for _, r in members]
    cbet = merge(gather("cbet").get("flop", []))
    sizes = sorted({s for r in rows for s in r["sizes"]})
    eq_adv = sum(r["eq_adv"] for r in rows) / len(rows)
    nut_adv = sum(r["nut_adv"] for r in rows) / len(rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    categories = [(CATEGORY_LABEL[c], counts[c]) for c, _ in CATEGORIES if c in counts]
    facing = _facing(merge(gather("vs_cbet").get("flop", [])))
    limit = max_fold(sizes)  # (un tapis n'a pas de taille dans la liste : pas de seuil)
    size_text = SIZE_LABEL[size] + (f" ({' / '.join(str(x) for x in sizes)} % du pot)" if sizes else "")
    stab = merge(gather("stab").get("flop", []))
    return {
        "key": f"{strategy}-{size}", "strategy": strategy, "strategy_label": STRATEGY_LABEL[strategy], "size": size,
        "size_label": SIZE_LABEL[size], "size_text": size_text, "sizes": sizes, "flops": rows,
        "categories": categories, "cbet": aggression(cbet["f"]), "eq_adv": eq_adv, "nut_adv": nut_adv,
        "why": why(strategy, eq_adv, nut_adv, who, other),
        "attack": {
            "flop": simple_rules(cbet), "flop_detail": bettor_rules(cbet),
            "turn": card_rules(gather("barrel"), sizes=sizes_of("barrel")),
            "river": card_rules(gather("barrel3"), river=True, sizes=sizes_of("barrel3")),
            "river_xx": card_rules(gather("river_xx"), river=True, sizes=sizes_of("river_xx")),
            "delayed": card_rules(gather("delayed"), sizes=sizes_of("delayed")),
            "vs_xr": _facing(merge(gather("vs_xr").get("flop", []))),
        },
        "defense": {
            "vs_cbet": facing, "max_fold": limit,
            "text": defense_text(facing["fold"], facing["raise"], limit, other, SIZE_LABEL[size]) if facing else None,
            "vs_barrel": card_rules(gather("vs_barrel"), defender=True),
            "vs_barrel3": card_rules(gather("vs_barrel3"), defender=True, river=True),
            "vs_river_xx": card_rules(gather("vs_river_xx"), defender=True, river=True),
            "probe": card_rules(gather("probe"), sizes=sizes_of("probe")),
            "stab": {"aggr": aggression(stab["f"]), "rules": simple_rules(stab), "detail": bettor_rules(stab)}
            if stab["flops"] else None,
        },
    }


# --- Affiner un niveau au solveur -----------------------------------------------------------------------------
# La suite d'un niveau (turn, river) est lue aux nœuds du solveur, après sa propre stratégie : avec un jeu simplifié,
# les ranges qui arrivent ensuite ne sont plus tout à fait les mêmes (en misant toute sa range au flop, on arrive à la
# turn avec plus de mains faibles). Pour l'affiner, quelques flops résolus le sont de nouveau avec une partie du niveau
# imposée par des verrous (nodelock) : chaque main y joue l'action (ou les fractions) de sa case. L'adversaire s'y
# adapte ; la suite se relit (extract), et l'écart d'EV à la racine avec la résolution d'origine dit ce que coûte
# vraiment la partie imposée face à un adversaire qui s'adapte. Trois façons (REFINE_MODES) :
# - en attaque, « flop » : la c-bet du niveau (l'arbre réduit à sa taille de c-bet) ; la turn et la river se relisent ;
# - en attaque, « turn » : en plus, la turn du niveau (2e barrel et c-bet retardée, telles que la page les montre) ; la
#   river se relit, après une turn jouée simplement (deux résolutions : le flop imposé, puis le flop et la turn) ;
# - en défense, « flop » : la réponse à la c-bet du niveau, à chaque taille de c-bet de l'arbre ; la suite de la défense
#   se relit.
# Un verrou en fractions se pose sur les nœuds de l'arbre lui-même : chaque main y prend sa case d'après son équité à ce
# nœud. Celle-ci se lit là où elle est juste : à la c-bet, sur l'arbre seul (une résolution d'une itération : la range
# de l'adversaire y est entière) ; face à la c-bet, dans la résolution d'origine ; à la turn, dans la résolution du flop
# imposé. Gardé dans la base (documents « plan-simple », clé « famille|côté|façon|niveau|flop »).
REFINE_KIND = "plan-simple"
REFINE_MODES = (("attack", "flop"), ("attack", "turn"), ("defense", "flop"))
REFINE_FLOPS = 6  # flops résolus de nouveau par niveau : un par type de flop, les plus fréquents d'abord
DEFAULT_PCT = {"small": 33, "medium": 66, "big": 100, "overbet": 150}  # une taille que les flops lus n'ont pas
TURN_LOCKS = ("barrel", "delayed")  # la turn imposée (« turn ») : 2e barrel après la c-bet payée, c-bet retardée
# Durée d'un flop affiné (mesurée sur 4 cœurs) : chaque résolution verrouillée, une part de celle d'origine, et sa
# relecture ; l'arbre lu avant. Selon le rapport tapis / pot au flop : sous 3, ou plus.
LOCKED_SPEED = {"attack": 0.7, "defense": 0.9}
READ_SECONDS = {"short": 20, "deep": 60}
TREE_SECONDS = {"short": 2, "deep": 6}


def refine_sample(data: list[dict], n: int = REFINE_FLOPS) -> list[dict]:
    """Les flops à résoudre de nouveau : un par type de flop à tour de rôle, les types les plus fréquents d'abord."""
    by_cat: dict[str, list[dict]] = {}
    for d in sorted(data, key=lambda d: d["id"]):
        by_cat.setdefault(board_category(d["board"]), []).append(d)
    order = [c for c, _ in CATEGORIES]
    cats = sorted(by_cat, key=lambda c: (-len(by_cat[c]), order.index(c)))
    out: list[dict] = []
    for k in range(max((len(v) for v in by_cat.values()), default=0)):
        out += [by_cat[c][k] for c in cats if k < len(by_cat[c])]
    return out[:n]


def play_of(step: dict, where: str, cell: Optional[str]) -> dict[str, float]:
    """Ce que joue une case de la range (cell_of ; None : une main sans équité connue) selon une règle (un niveau, ou
    une décision de sa suite), sur un flop de cette catégorie ou à une carte de ce type (where) : {classe: fraction}.
    Une case sans règle (absente des flops lus) joue comme sa boîte, ou comme toute la range."""
    rules = step["rules"].get(where if step["by_flop"] else "tous") or {}
    if step["kind"] == "pure":
        rule = rules.get("range") or max(rules.values(), key=lambda r: r["share"], default={"class": "check"})
        return {rule["class"]: 1.0}
    if not rules:
        return {}
    box = rules["boxes"].get(cell.split("|")[0]) if cell else None
    if box is None:
        return rules["freq"]
    return next((x["f"] for x in box["families"] if x["key"] == cell.split("|")[1]), box["f"])


def _rounded(rule: dict) -> dict:
    return {"size": rule["size"], "sizes": rule["sizes"],
            "boxes": {box: {x["key"]: {c: round(v, 2) for c, v in sorted(x["f"].items())} for x in b["families"]}
                      for box, b in sorted(rule["boxes"].items())}}


def refine_signature(step: dict, category: str, sizes: Optional[dict] = None) -> str:
    """Ce qui fixe la partie imposée sur un flop de cette catégorie : la règle de sa case (et pour une action pure, les
    tailles du niveau). Un flop affiné dont la signature a changé (des flops lus depuis ont changé la règle) est à
    refaire."""
    rule = step["rules"].get(category if step["by_flop"] else "tous") or {}
    if step["kind"] == "pure":
        rules = {hk: r["class"] for hk, r in sorted(rule.items())}
        used = sorted({c for c in rules.values() if c != "check"}, key=ORDER.index)
        return json.dumps({"hands": step["hands"], "rules": rules, "sizes": {c: (sizes or {}).get(c) for c in used}},
                          sort_keys=True)
    return json.dumps(_rounded(rule), sort_keys=True)


def turn_rules(step: dict, levels: dict) -> dict[str, dict]:
    """La turn d'un niveau d'attaque telle que la page la montre : les règles du 2e barrel et de la c-bet retardée,
    affinées au solveur (flop imposé) s'il y en a."""
    refined = step.get("refined") or {}
    out = {}
    for name in TURN_LOCKS:
        if refined.get("flops") and name in refined.get("streets", {}):
            out[name] = refined["streets"][name]
        elif name in step["streets"]:
            out[name] = step["streets"][name]
    return out


def _turn_signature(turn: dict[str, dict]) -> str:
    return json.dumps({name: {where: _rounded(rule) for where, rule in st["rules"].items()}
                       for name, st in turn.items()}, sort_keys=True)


def refine_edits(spot: studyspots.StudySpot, step: dict, sizes: dict[str, int]) -> dict:
    """Les modifications de l'arbre d'un flop pour l'affiner avec une action pour toute la range (format de
    custom_tree) : la seule taille de c-bet du niveau (sizes : celles des flops lus) et un verrou sur la c-bet, chaque
    main de celui qui a l'initiative y jouant l'action de sa case. Les actions de l'arbre (native/arbre.rs) : le check,
    puis les mises par montant croissant ; une mise d'au moins allin_threshold du tapis devient le tapis."""
    a = aggressor_of(spot.family)
    seat = spot.ip if a == 1 else spot.oop
    category = board_category(spot.board)
    blocked = set(spot.board)
    plays = {}
    for cls, weight in spot.ranges[seat].items():
        if weight <= 0:
            continue
        for combo in preflop_tree.combos(cls):
            if not {combo[:2], combo[2:]} & blocked:
                plays[combo] = next(iter(play_of(step, category, None)))
    used = sorted(set(plays.values()) - {"check"}, key=ORDER.index)
    pcts = {c: sizes.get(c) or DEFAULT_PCT[c] for c in used if c != "allin"}
    tree = spot.tree()
    stack, pot, threshold = tree["effective_stack"], tree["starting_pot"], tree["allin_threshold"]
    amounts = {}
    for c in used:
        to = stack if c == "allin" else pcts[c] / 100 * pot
        amounts[c] = round(stack if to >= threshold * stack - 1e-9 else to, 6)
    distinct = sorted(set(amounts.values()))
    index = {c: 1 + distinct.index(x) for c, x in amounts.items()}
    index["check"] = 0

    def freqs(c: str) -> list[int]:
        return [1 if k == index[c] else 0 for k in range(1 + len(distinct))]
    edits = {"plan": {_cbet_key(spot.family): sorted(pcts.values()) + (["a"] if "allin" in used else [])},
             "locks": [], "auto": {}}
    if used:  # (toute la range checke : l'arbre n'a plus de c-bet, rien à verrouiller)
        path = [{"type": "action", "index": 0}] if a == 1 else []  # (la BB qui ne mène pas checke d'abord)
        edits["locks"] = [{"path": path, "combos": {c: freqs(cls) for c, cls in sorted(plays.items())}}]
    return edits


def _cbet_key(family: str) -> str:
    """La situation de la c-bet dans le plan de l'arbre (native/arbre.rs)."""
    return "bet:fi:" if aggressor_of(family) == 1 else "bet:fo:"


def _mix_plan(spot: studyspots.StudySpot, step: dict) -> dict:
    """L'arbre d'un flop pour l'affiner avec une c-bet en fractions : sa seule taille de c-bet (« une ») ; les tailles
    de l'arbre d'origine (« solveur »)."""
    rule = step["rules"].get(board_category(spot.board) if step["by_flop"] else "tous") or {}
    size = rule.get("size")
    if step["sizes_mode"] != "une" or size is None:
        return {}
    return {_cbet_key(spot.family): ["a"] if size == "allin" else [rule["sizes"].get(size) or DEFAULT_PCT[size]]}


def _class_index(node: dict, cls: str, sizes: dict) -> int:
    """L'action d'un nœud de l'arbre qui joue cette classe : la taille (ou la relance) la plus proche s'il ne l'a pas,
    sinon check ou call ; parmi plusieurs actions de la classe, la mise la plus proche de sa taille (sizes, % du pot),
    ou la plus petite."""
    classes = [action_class(node, a) for a in node["actions"]]
    chosen = _substitute(cls, classes) or next((c for c in ("check", "call") if c in classes), classes[0])
    found = [i for i, c in enumerate(classes) if c == chosen]
    if len(found) > 1 and chosen in sizes and chosen != "raise" and node["pot"]:
        return min(found, key=lambda i: abs(100 * node["actions"][i]["amount"] / node["pot"] - sizes[chosen]))
    return found[0]


def _node_lock(node: dict, path: list, play: Callable[[Optional[str]], dict], sizes: dict) -> Optional[dict]:
    """Le verrou d'un nœud de l'arbre (format de custom_tree) : chaque main du joueur qui agit y joue les fractions de
    sa case (play : case -> {classe: fraction}, d'après l'équité de la main à ce nœud et sa famille) ; une main sans
    règle checke (ou paie). None pour un nœud sans choix."""
    if node.get("type") != "action" or len(node["actions"]) < 2:
        return None
    na, index, combos = len(node["actions"]), {}, {}
    for row in node["hands"][node["player"]]:
        cell = cell_of(row[2], bucket(row[0], node["board"])) if row[2] is not None else None
        vec = [0.0] * na
        for cls, v in play(cell).items():
            if v > 0:
                if cls not in index:
                    index[cls] = _class_index(node, cls, sizes)
                vec[index[cls]] += v
        total = sum(vec)
        if total <= 0:
            vec[_class_index(node, "check", {})], total = 1.0, 1.0
        combos[row[0]] = [round(x / total, 4) for x in vec]
    return {"path": path, "combos": combos}


def _walk_all(query: Callable[[list], dict], steps: list[str], path: list, node: dict) -> Iterable[tuple[dict, list]]:
    """Comme _walk, sans échantillon : toutes les mises d'une étape « bet » et toutes les cartes ; les nœuds au bout,
    avec leur chemin."""
    if not steps:
        if node["type"] == "action" and len(node["actions"]) > 1:
            yield node, path
        return
    head, rest = steps[0], steps[1:]
    if head == "*":
        if node["type"] == "chance":
            for card in sorted(node["cards"] or []):
                p = path + [{"type": "card", "card": card}]
                yield from _walk_all(query, rest, p, query(p))
        return
    if node["type"] != "action":
        return
    for i, a in enumerate(node["actions"]):
        if a["kind"] == head:
            p = path + [{"type": "action", "index": i}]
            yield from _walk_all(query, rest, p, query(p))


def _open(request: dict, on_start: Optional[Callable] = None, save: bool = False) -> postflop.Session:
    session = postflop.Session(request, save=save)
    try:
        session.start(on_start=on_start)
    except BaseException:
        session.close()
        raise
    return session


def _tree(spot: studyspots.StudySpot, on_start: Optional[Callable] = None) -> postflop.Session:
    """L'arbre du spot (avec ses modifications), lu sans le résoudre : une itération, ni étude ni cache."""
    return _open(spot.request(1, 100.0), on_start)


def _rule_sizes(step: dict, where: str) -> dict:
    return ((step["rules"].get(where if step["by_flop"] else "tous") or {}).get("sizes") or {}
            if step["kind"] == "mix" else {})


def _flop_lock(session: postflop.Session, spot: studyspots.StudySpot, step: dict) -> Optional[dict]:
    """Le verrou de la c-bet en fractions, sur l'arbre du flop."""
    path, category = _flop_path(session.node, spot.family), board_category(spot.board)
    return _node_lock(session.node(path), path, lambda cell: play_of(step, category, cell),
                      _rule_sizes(step, category))


def _turn_locks(session: postflop.Session, spot: studyspots.StudySpot, turn: dict[str, dict]) -> list[dict]:
    """Les verrous de la turn du niveau, dans la résolution du flop imposé : à chaque turn du 2e barrel (après la c-bet
    payée) et de la c-bet retardée (après le flop checké), chaque main jouée là joue les fractions de sa case."""
    steps = {name: st for name, _, st in lines(spot.family)}
    out = []
    for name, street in turn.items():
        for node, path in _walk_all(session.node, steps[name], [], session.node([])):
            if not any(row[1] > 0 for row in node["hands"][node["player"]]):
                continue  # (une ligne que le flop imposé ne joue pas)
            card = card_class(spot.board, next(s["card"] for s in path if s["type"] == "card"))
            lock = _node_lock(node, path, lambda cell, st=street, c=card: play_of(st, c, cell),
                              _rule_sizes(street, card))
            if lock:
                out.append(lock)
    return out


def _defense_locks(session: postflop.Session, spot: studyspots.StudySpot, step: dict) -> list[dict]:
    """Les verrous de la défense du niveau, dans la résolution d'origine : face à chaque taille de c-bet de l'arbre,
    chaque main joue les fractions de sa case."""
    path = _flop_path(session.node, spot.family)
    cbet, category = session.node(path), board_category(spot.board)
    out = []
    for i, a in enumerate(cbet["actions"]):
        if a["kind"] == "bet":
            p = path + [{"type": "action", "index": i}]
            lock = _node_lock(session.node(p), p, lambda cell: play_of(step, category, cell),
                              _rule_sizes(step, category))
            if lock:
                out.append(lock)
    return out


def refine_spots(family: str, level: str, side: str = "attack", mode: str = "flop") -> list[dict]:
    """Les flops à résoudre de nouveau pour affiner ce niveau (side, mode : REFINE_MODES) : chacun avec son spot (en
    attaque, l'arbre réduit à la c-bet du niveau : spot.edits), la requête de sa résolution d'origine (base, ou None),
    sa signature, et ce qu'il faut imposer une fois l'arbre lu (« step » : la c-bet en fractions, ou la défense ;
    « turn » : la turn)."""
    if (side, mode) not in REFINE_MODES:
        return []
    data = plans(family)
    view = plan_levels(data)
    attach_refined(view, family)
    found = view[side]
    step = next((st for st in (found or {}).get("steps", []) if st["key"] == level), None)
    if step is None:
        return []
    turn = turn_rules(step, found) if mode == "turn" else None
    out = []
    for d in refine_sample(data):
        spot = studyspots.StudySpot(family, list(d["board"]))
        category = board_category(spot.board)
        item = {"spot": spot, "base": postflop.solved_request(spot.request()), "side": side, "mode": mode,
                "level": level, "category": category, "id": d["id"],
                "signature": refine_signature(step, category, found["sizes"])}
        if side == "defense":
            item["step"] = step
        elif step["kind"] == "pure":
            spot.edits = refine_edits(spot, step, found["sizes"])
        else:
            spot.edits = {"plan": _mix_plan(spot, step), "locks": [], "auto": {}}
            item["step"] = step
        if turn is not None:
            item.update(turn=turn, flop_signature=item["signature"],
                        signature=item["signature"] + "|" + _turn_signature(turn))
        out.append(item)
    return out


def refine_seconds(family: str, items: list[dict], side: str = "attack", mode: str = "flop") -> Optional[float]:
    """La durée attendue de l'affinage de ces flops : l'arbre lu avant, chaque résolution verrouillée (d'après celle
    d'origine) et sa relecture (deux pour la turn imposée)."""
    metas = studyspots.spot_studies((family,))
    found = [metas.get(it["id"], {}).get("seconds") for it in items]
    if not found or not all(found):
        return None
    info = studyspots.family_info(family)
    kind = "short" if info["stack"] < 3 * info["pot"] else "deep"
    solves = 2 if mode == "turn" else 1
    return sum(solves * (LOCKED_SPEED[side] * x + READ_SECONDS[kind]) + TREE_SECONDS[kind] for x in found)


def _payload(item: dict, spot: studyspots.StudySpot, raw: dict, data: dict, base: Optional[dict], mode: str,
             signature: str) -> dict:
    """Un flop affiné, gardé dans la base : la suite relue et l'EV à la racine des deux résolutions."""
    side, a = item["side"], aggressor_of(spot.family)
    payload = {
        "version": VERSION, "family": spot.family, "side": side, "mode": mode, "level": item["level"],
        "board": spot.board, "category": item["category"], "signature": signature,
        "player": a if side == "attack" else 1 - a, "pot": spot.pot_bb, "root_ev": raw.get("root_ev"),
        "base_ev": (base or {}).get("root_ev"), "exploit": [raw.get("exploit_pct"), (base or {}).get("exploit_pct")],
        "seconds": raw.get("seconds"), "locks": len(spot.edits["locks"]),
        "nodes": {name: {"evs": node["evs"]} for name, node in data["nodes"].items()
                  if name in SIDE_NODES[side][1:] and node.get("evs")},
    }
    key = "|".join((spot.family, side, mode, item["level"], "".join(spot.board)))
    documents.put(db.current(), REFINE_KIND, key, payload)
    return payload


def refine_one(item: dict, iterations: int, target: float, on_progress: Optional[Callable[[dict], None]] = None,
               on_start: Optional[Callable] = None) -> dict:
    """Résout un flop avec la partie imposée du niveau (des résolutions de travail : ni étude ni cache), en tire la
    suite (extract) et la garde avec l'EV à la racine des deux résolutions. Pour la turn imposée, garde aussi le flop
    imposé (la première résolution)."""
    spot, side = item["spot"], item["side"]
    base = postflop.cached(item["base"]) if item["base"] else None
    if side == "defense":  # (les équités face à la c-bet : celles de la résolution d'origine)
        study = item["base"] and postflop.has_study(item["base"])
        tree = _open(item["base"], on_start, save=True) if study else _tree(spot, on_start)
        try:
            spot.edits = {"plan": {}, "locks": _defense_locks(tree, spot, item["step"]), "auto": {}}
        finally:
            tree.close()
    elif item.get("step"):  # c-bet en fractions : le verrou se pose sur l'arbre
        tree = _tree(spot, on_start)
        try:
            lock = _flop_lock(tree, spot, item["step"])
        finally:
            tree.close()
        spot.edits = dict(spot.edits, locks=[lock] if lock else [])
    turn = item.get("turn")
    session = postflop.Session(spot.request(iterations, target), save=False)
    try:
        raw = session.start(on_progress=on_progress, on_start=on_start)
        data = extract(session.node, spot, postflop.study_key(session.request))
        payload = _payload(item, spot, raw, data, base, "flop", item.get("flop_signature", item["signature"]))
        locks = _turn_locks(session, spot, turn) if turn else []
    finally:
        session.close()
    if not turn:
        return payload
    spot.edits = dict(spot.edits, locks=spot.edits["locks"] + locks)
    session = postflop.Session(spot.request(iterations, target), save=False)
    try:
        raw = session.start(on_progress=on_progress, on_start=on_start)
        data = extract(session.node, spot, postflop.study_key(session.request))
    finally:
        session.close()
    return _payload(item, spot, raw, data, base, "turn", item["signature"])


def refined(family: str) -> dict[tuple[str, str, str], list[dict]]:
    """Les flops affinés de cette famille : {(côté, façon, niveau): [résultats de refine_one]}."""
    out: dict[tuple[str, str, str], list[dict]] = {}
    for _, value in documents.items(db.current(), REFINE_KIND, family + "|"):
        if isinstance(value, dict) and value.get("version") == VERSION and value.get("family") == family:
            out.setdefault((value["side"], value["mode"], value["level"]), []).append(value)
    return out


def _refined_step(step: dict, found: list[dict], signature: Callable[[str], str], side: str) -> Optional[dict]:
    """Un niveau affiné au solveur : la suite lue sur les flops affinés à jour (signature : celle attendue pour une
    catégorie de flop), et ce que coûte vraiment la partie imposée (écart d'EV à la racine, en part du pot) ; None
    sans flop affiné."""
    if not found:
        return None
    fresh = [r for r in found if r["signature"] == signature(r["category"])]
    out = {"flops": len(fresh), "stale": len(found) - len(fresh),
           "boards": [r["board"] for r in sorted(fresh, key=lambda r: r["board"])]}
    if not fresh:
        return out
    losses = [((r["base_ev"][r["player"]] - r["root_ev"][r["player"]]) / r["pot"], 1) for r in fresh
              if r.get("base_ev") and r.get("root_ev")]
    margins = [(max(x for x in r["exploit"] if x is not None) / 100, 1) for r in fresh
               if any(x is not None for x in r["exploit"])]
    later: dict[str, list] = {}
    for r in fresh:
        for name, node in r["nodes"].items():
            later.setdefault(name, []).extend((r["category"] if group == "flop" else group, ev)
                                              for group, ev in node["evs"].items())
    rest = next(rest for key, _, rest in STEPS[side] if key == step["key"])
    out.update(loss=_mean(losses), margin=_mean(margins), streets=_streets(later, *rest))
    return out


def attach_refined(view: dict, family: str) -> None:
    """Les affinages au solveur de chaque niveau (view : plan_levels) : « refined » (en attaque le flop imposé, en
    défense la réponse à la c-bet imposée) et, en attaque, « refined_turn » (le flop et la turn imposés)."""
    found = refined(family)
    for side in ("attack", "defense"):
        levels = view.get(side)
        if not levels:
            continue
        sizes = levels["sizes"]
        for step in levels["steps"]:
            def flop(category: str, st: dict = step) -> str:
                return refine_signature(st, category, sizes)
            step["refined"] = _refined_step(step, found.get((side, "flop", step["key"]), []), flop, side)
            if side == "attack":
                turn = "|" + _turn_signature(turn_rules(step, levels))
                step["refined_turn"] = _refined_step(step, found.get(("attack", "turn", step["key"]), []),
                                                     lambda category, st=step: flop(category, st) + turn, side)


# --- Lecture d'un nœud, pour le coach conversationnel --------------------------------------------------

def _class(combo: str) -> str:
    return postflop.class_of(combo)


def node_brief(node: dict, examples: int = 6, names: tuple[str, str] = ("BB", "BTN")) -> dict:
    """Un nœud d'étude résumé pour une explication : qui agit, les actions et leurs fréquences dans toute la
    range, puis par famille de mains (part de la range, fréquences, équité et EV moyennes), et des exemples
    de mains pour chaque action. names : les positions des joueurs 0 (hors de position) et 1."""
    out = {"type": node["type"], "board": node.get("board"), "pot": node.get("pot")}
    if node.get("rare"):
        out["avertissement"] = ("Ligne que le solveur ne prend presque jamais ("
                                + ", ".join(f"{names[p]} y arrive avec {round(100 * node['presence'][p], 2)} % "
                                            "de sa range" for p in node["rare"])
                                + ") : les stratégies qui suivent n'y sont pas optimisées, lis les EV plutôt que les fréquences.")
    if node["type"] != "action":
        return out
    actor = node["player"]
    labels = postflop.action_labels(node)
    na = len(labels)
    rows = node["hands"][actor]
    total = sum(r[1] for r in rows) or 1.0
    freqs = [sum(r[1] * r[4 + k] for r in rows) / total for k in range(na)]
    families: dict[str, dict] = {}
    for r in rows:
        f = families.setdefault(bucket(r[0], node["board"]), {"w": 0.0, "f": [0.0] * na, "eq": 0.0, "eqw": 0.0,
                                                              "ev": 0.0, "evw": 0.0})
        f["w"] += r[1]
        for k in range(na):
            f["f"][k] += r[1] * r[4 + k]
        if r[2] is not None:
            f["eq"] += r[1] * r[2]
            f["eqw"] += r[1]
        if r[3] is not None:
            f["ev"] += r[1] * r[3]
            f["evw"] += r[1]
    by_family = []
    for key, label in BUCKETS:
        f = families.get(key)
        if not f or f["w"] / total < 0.005:
            continue
        by_family.append({"famille": label, "part_de_range": round(f["w"] / total, 3),
                          "frequences": {labels[k]: round(f["f"][k] / f["w"], 3) for k in range(na)},
                          "equite": round(f["eq"] / f["eqw"], 3) if f["eqw"] else None,
                          "ev_bb": round(f["ev"] / f["evw"], 2) if f["evw"] else None})
    samples = {}
    for k in range(na):
        ranked = sorted(rows, key=lambda r: -(r[1] * r[4 + k]))
        seen: list[str] = []
        for r in ranked:
            if r[4 + k] < 0.5 or r[1] < 0.05:
                continue
            cls = _class(r[0])
            if cls not in seen:
                seen.append(cls)
            if len(seen) >= examples:
                break
        samples[labels[k]] = seen
    eqs = []
    for p in (0, 1):
        known = [r for r in node["hands"][p] if r[2] is not None]
        w = sum(r[1] for r in known)
        eqs.append(round(sum(r[1] * r[2] for r in known) / w, 3) if w else None)
    out.update({"joueur": names[actor], "tapis": node.get("stacks"),
                "actions": labels, "frequences_range": {labels[k]: round(freqs[k], 3) for k in range(na)},
                "par_famille": by_family, "exemples": samples,
                "equite_ranges": {names[0]: eqs[0], names[1]: eqs[1]}})
    return out


def combo_brief(node: dict, combo: str, player: Optional[int] = None,
                names: tuple[str, str] = ("BB", "BTN")) -> Optional[dict]:
    """Une main précise à ce nœud : sa famille, son équité (et son rang dans la range), et pour le joueur qui
    agit sa stratégie et l'EV de chaque action. Sans joueur précisé : celui qui agit d'abord."""
    combo = combo.strip()
    if len(combo) != 4:
        return None
    actor = node.get("player")
    order = [player] if player in (0, 1) else ([actor, 1 - actor] if actor in (0, 1) else [0, 1])
    for p in order:
        row = postflop.combo_row(node, p, [combo[:2], combo[2:]])
        if row is None:
            continue
        rows = node["hands"][p]
        better = sum(r[1] for r in rows if r[2] is not None and row[2] is not None and r[2] > row[2])
        total = sum(r[1] for r in rows) or 1.0
        out = {"main": combo, "joueur": names[p], "famille": BUCKET_LABEL[bucket(combo, node["board"])],
               "presence": round(row[1], 3), "equite": row[2], "ev_bb": row[3],
               "rang_equite": f"meilleure que {round(100 * (1 - better / total))} % de la range" if row[2] is not None else None}
        if node["type"] == "action" and node["player"] == p:
            labels = postflop.action_labels(node)
            na = len(labels)
            out["strategie"] = {labels[k]: round(row[4 + k], 3) for k in range(na)}
            evs = row[4 + na:4 + 2 * na] if len(row) >= 4 + 2 * na else [None] * na
            out["ev_par_action_bb"] = {labels[k]: (round(evs[k], 2) if evs[k] is not None else None) for k in range(na)}
            if row[0] in node.get("settled", ()):
                out["note"] = ("Le solveur ne joue presque jamais cette main ici : sa fréquence n'y est pas apprise "
                               "(reste des premières itérations). La stratégie indiquée est sa meilleure action selon l'EV.")
        return out
    return None
