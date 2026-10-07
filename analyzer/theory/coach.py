"""Plan de jeu suggéré : ce que disent les études résolues, réduit à des règles simples à appliquer.

1. Extraction, une fois par étude (quelques secondes une fois l'étude ouverte) : aux nœuds clés de la ligne
   principale (c-bet, réponse à la c-bet, 2e barrel à chaque turn, 3e barrel sur un échantillon de rivers,
   c-bet retardée, probe…), la stratégie de toute la range regroupée par famille de mains (deux paires et
   mieux, overpair, top pair…, tirage couleur, air) et, à la turn et à la river, par type de carte
   (overcard, brique, board pairé, couleur ou quinte possible). Gardée dans la base (documents « plan »).
2. Synthèse, à l'affichage : les flops regroupés par catégorie (hauteur haut, moyen ou bas ; structure
   sèche, deux couleurs ou connectée ; pairé ; monotone), chacune avec son niveau de c-bet (mise presque
   tout, mise souvent, checke souvent), des règles pour quatre familles de mains (fortes, moyennes, tirages,
   rien), la suite selon la carte de turn et de river, et le pourquoi (avantage d'équité, avantage de nuts).
   Plus il y a de flops résolus, plus le plan est précis.

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
from . import postflop, sizing, studyspots
from .handclass import DRAW_BIT, MADE, MADE_INDEX, classify

VERSION = 1  # change quand l'extraction change : les plans plus anciens sont refaits

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
    ("barrel3", "3e barrel du bouton", ["check", "bet", "call", "*", "check", "bet", "call", "*", "check"]),
    ("vs_barrel3", "BB face au 3e barrel", ["check", "bet", "call", "*", "check", "bet", "call", "*", "check", "bet"]),
)
LINES_OOP = (  # la BB à l'initiative : pot 3bet
    ("cbet", "C-bet de la BB", []),
    ("vs_cbet", "Bouton face à la c-bet", ["bet"]),
    ("vs_xr", "BB face à la relance", ["bet", "raise"]),
    ("stab", "Stab du bouton (la BB checke)", ["check"]),
    ("barrel", "2e barrel de la BB", ["bet", "call", "*"]),
    ("vs_barrel", "Bouton face au 2e barrel", ["bet", "call", "*", "bet"]),
    ("delayed", "C-bet retardée de la BB", ["check", "check", "*"]),
    ("barrel3", "3e barrel de la BB", ["bet", "call", "*", "bet", "call", "*"]),
    ("vs_barrel3", "Bouton face au 3e barrel", ["bet", "call", "*", "bet", "call", "*", "bet"]),
)
RIVER_TURNS = 12  # turns gardées pour les nœuds de river (toutes les rivers de chacune)


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
    info = studyspots.RING_FAMILIES.get(family or "")
    return (info["oop"], info["ip"]) if info else ("BB", "BTN")


def seat_words(family: str) -> tuple[str, str]:
    """Les deux joueurs avec leur article, pour les phrases : « la BB », « le bouton », « le CO »…"""
    if family in studyspots.FAMILIES:
        return "la BB", "le bouton"
    return tuple(("la " if pos in ("SB", "BB") else "le ") + pos for pos in seat_names(family))


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


def _accumulate(acc: dict, node: dict, flop: list[str], dealt: list[str]) -> None:
    actor = node["player"]
    na = len(node["actions"])
    classes = [action_class(node, a) for a in node["actions"]]
    group = card_class(flop + dealt[:-1], dealt[-1]) if dealt else "flop"
    board = node["board"]
    g = acc.setdefault(group, {"n": 0, "w": 0.0, "f": {}, "b": {}})
    g["n"] += 1
    for row in node["hands"][actor]:
        reach = row[1]
        if reach <= 0:
            continue
        b = g["b"].setdefault(bucket(row[0], board), {"w": 0.0, "f": {}})
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
        sizes = set()
        actor = None
        for node, dealt in _walk(query, steps, [], root, [], stride):
            _accumulate(acc, node, spot.board, dealt)
            actor = node["player"]
            for a in node["actions"]:
                if a["kind"] == "bet" and not a.get("allin") and node["pot"]:
                    sizes.add(round(100 * a["amount"] / node["pot"]))
        if acc:
            nodes[name] = {"title": title, "actor": actor, "sizes": sorted(sizes), "groups": _normalize(acc)}
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
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def extract_and_save(session: postflop.Session, spot: studyspots.StudySpot) -> dict:
    key = postflop.study_key(session.request)  # l'étude ouverte, à sa précision
    data = extract(session.node, spot, key)
    save_plan(data)
    return json.loads(json.dumps(data))  # tel qu'il sera relu


def missing(family: Optional[str] = None) -> list[str]:
    """Spots résolus (arbre actuel) sans plan extrait : ceux d'une famille, ou de toutes (heads-up et 6-max)."""
    wanted = (family,) if family else plan_families()
    out = []
    for ident, meta in studyspots.spot_studies(wanted).items():
        if meta.get("family") in wanted and load_plan(meta["key"]) is None:
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

# Trois niveaux de c-bet, du plus simple à jouer au plus délicat.
LEVELS = (("range", "Mise presque tout", 0.75), ("often", "Mise souvent", 0.5), ("check", "Checke souvent", 0.0))
PATTERNS = tuple((key, label) for key, label, _ in LEVELS)
PATTERN_LABEL = dict(PATTERNS)
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


def pattern_of(share: float) -> str:
    return next(key for key, _, low in LEVELS if share >= low)


def aggressor_of(family: str) -> int:
    """Joueur à l'initiative : 0 = hors de position (la BB en pot 3bet heads-up), 1 = en position (le bouton en SRP
    et en pot 4bet heads-up)."""
    return 0 if studyspots.family_info(family)["oop_initiative"] else 1


def flop_row(data: dict) -> dict:
    """Une ligne du tableau des flops : catégorie, c-bet, taille, avantages, niveau."""
    cbet = data["nodes"].get("cbet", {}).get("groups", {}).get("flop", {"f": {}})
    share = aggression(cbet["f"])
    adv = data.get("advantages") or {"eq": [0.5, 0.5], "nuts": [0.0, 0.0]}
    a = aggressor_of(data["family"])
    return {"id": data["id"], "board": data["board"], "texture": data["texture"], "pattern": pattern_of(share),
            "category": board_category(data["board"]), "suit_pattern": data["pattern"], "cbet": share,
            "size": main_size(cbet["f"]), "sizes": data["nodes"].get("cbet", {}).get("sizes", []),
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


def why(pattern: str, eq_adv: float, nut_adv: float, who: str, other: str) -> str:
    """Pourquoi ce niveau de c-bet, en deux phrases, d'après l'avantage d'équité et de nuts de celui qui a
    l'initiative."""
    cap = lambda text: text[0].upper() + text[1:]  # noqa: E731
    equity = ("un avantage d'équité" if eq_adv >= 0.05 else "moins d'équité" if eq_adv <= -0.05
              else "à peu près autant d'équité")
    nuts = ("plus de mains très fortes" if nut_adv >= 0.02 else "moins de mains très fortes" if nut_adv <= -0.02
            else "autant de mains très fortes")
    facts = f"{cap(who)} a {equity} ({_pts(eq_adv)}) et {nuts} ({_pts(nut_adv)}) que {other}."
    if pattern == "range":
        reason = f"{cap(other)} a peu de mains qui supportent une mise : une petite mise avec tout coûte peu et rapporte."
    elif nut_adv <= -0.02:
        reason = f"{cap(other)} a plus de mains très fortes : on mise moins et on garde des mains fortes dans les checks."
    elif pattern == "often":
        reason = "On mise souvent, mais on checke une partie des mains moyennes qui préfèrent aller à l'abattage."
    else:
        reason = "Sans avantage net, on mise surtout les mains fortes et des tirages, et on checke le reste."
    return facts + " " + reason


def card_rules(node: Optional[dict], defender: bool = False, river: bool = False) -> list[dict]:
    """Par type de carte (turn ou river) : fréquence de mise (ou de fold), règles simples et détail."""
    if not node:
        return []
    out = []
    for key, label in CARDS:
        entries = node.get(key, [])
        if not entries:
            continue
        merged = merge(entries)
        out.append({"key": key, "label": label, "text": CARD_TEXT[key], "aggr": aggression(merged["f"]),
                    "fold": merged["f"].get("fold", 0.0), "size": main_size(merged["f"]),
                    "rules": simple_rules(merged, defender, river),
                    "detail": defender_rules(merged) if defender else bettor_rules(merged, river)})
    return out


def _facing(merged: dict) -> Optional[dict]:
    if not merged["flops"]:
        return None
    return {"fold": merged["f"].get("fold", 0.0), "raise": aggression(merged["f"]),
            "rules": simple_rules(merged, defender=True), "detail": defender_rules(merged)}


def family_plan(family: str) -> dict:
    """Le plan d'une famille de pots, à partir des plans extraits de ses flops résolus : une entrée par catégorie
    de flop (hauteur et structure, pairé, monotone)."""
    data = plans(family)
    info = studyspots.family_info(family)
    a = aggressor_of(family)
    oop, ip = seat_words(family)
    who, other = (oop, ip) if a == 0 else (ip, oop)
    out = {"family": family, "name": info["name"], "label": info["label"], "title": studyspots.family_title(family),
           "ring": family in studyspots.RING_FAMILIES, "pair": info.get("pair"), "count": len(data),
           "missing": len(missing(family)), "who": who, "other": other, "groups": []}
    rows = [flop_row(d) for d in data]
    out["flops"] = rows
    for category, label in CATEGORIES:
        members = [(d, r) for d, r in zip(data, rows) if r["category"] == category]
        if not members:
            continue

        def gather(name: str, members=members) -> dict:
            """Les groupes de ce nœud, flop par flop, rangés par type de carte."""
            by_card: dict[str, list] = {}
            for d, _ in members:
                for group, g in d["nodes"].get(name, {}).get("groups", {}).items():
                    by_card.setdefault(group, []).append(g)
            return by_card

        cbet = merge(gather("cbet").get("flop", []))
        share = aggression(cbet["f"])
        pattern = pattern_of(share)
        eq_adv = sum(r["eq_adv"] for _, r in members) / len(members)
        nut_adv = sum(r["nut_adv"] for _, r in members) / len(members)
        group = {
            "category": category, "label": label, "pattern": pattern, "level": PATTERN_LABEL[pattern],
            "flops": [r for _, r in members], "cbet": share, "size": main_size(cbet["f"]),
            "sizes": sorted({s for _, r in members for s in r["sizes"]}),
            "eq_adv": eq_adv, "nut_adv": nut_adv, "why": why(pattern, eq_adv, nut_adv, who, other),
            "flop": simple_rules(cbet), "flop_detail": bettor_rules(cbet),
            "turn": card_rules(gather("barrel")), "river": card_rules(gather("barrel3"), river=True),
            "delayed": card_rules(gather("delayed")),
            "defense": {
                "vs_cbet": _facing(merge(gather("vs_cbet").get("flop", []))),
                "vs_xr": _facing(merge(gather("vs_xr").get("flop", []))),
                "vs_barrel": card_rules(gather("vs_barrel"), defender=True),
                "vs_barrel3": card_rules(gather("vs_barrel3"), defender=True, river=True),
                "probe": card_rules(gather("probe")), "stab": None,
            },
        }
        stab = merge(gather("stab").get("flop", []))
        if stab["flops"]:
            group["defense"]["stab"] = {"aggr": aggression(stab["f"]), "rules": simple_rules(stab)}
        out["groups"].append(group)
    return out


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
