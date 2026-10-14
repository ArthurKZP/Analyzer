"""Les charts préflop selon les limites jouées. Le rake change la théorie : plus il pèse, plus on ouvre et on défend
serré, moins on paie et on limpe. Les charts fournis (la solution heads-up 100 bb, les charts 6-max de Hand2Note Guide)
sont ceux d'un jeu de micro limites, au rake élevé ; un jeu de charts par niveau de limites (stakes.BANDS : micro,
milieu, hautes) peut les remplacer pour les mains de ce niveau.

Gardés dans la base (documents « charts-limites », clé « <format>|<niveau> ») :

- en heads-up (« HU »), au format de la solution heads-up (theory/data/hu_100bb.json : ses quatre nœuds, main par
  main), ou plus simplement en ranges, action par action (hu_from_ranges) :

      {"format": "HU", "limites": "hautes", "source": "…", "rake": "…",
       "nodes": {"sb_open": {"raise": "AA,AKs,…,72o:0.5", "call": "…"},
                 "bb_vs_open": {"raise": "…", "call": "…", "allin": "…"},
                 "sb_vs_3bet": {"raise": "…", "call": "…", "allin": "…"},
                 "bb_vs_4bet": {"call": "…", "allin": "…"}}}

  (le poids d'une main dans une range : la part de ses combos qui prend l'action ; le reste folde ; tailles
  facultatives : "sizes": {"sb_open": {"raise": 2.5}}) ;
- aux tables à plusieurs (« 6-max », « 3-max »…), au format des solutions de theory/ring_ranges.py, ou en texte,
  combo par combo, comme les exporte GTO Wizard (parse_text) : les opens de chaque position, puis la défense face à
  chaque open (3bet, call) ; plusieurs fichiers se complètent.

Ce qui en dépend : le jugement préflop de chaque main (handplay.Theory, avec les charts de son niveau : Leakfinding,
mains de départ, vérification en jeu, suivi), les repères préflop du Leakfinding heads-up (la théorie de chaque niveau,
pondérée par tes mains de chaque niveau), les pages Préflop (les charts du niveau où tu as le plus de mains), et les
ranges du solveur pour les coups de ce niveau (postflop.build_spot : explorateur, mains face au solveur ; tes ranges
ajustées ligne par ligne passent avant). Une main déjà analysée avec les ranges de la référence garde son analyse
(review : ses clés d'avant comptent toujours) ; les suivantes partent de tes charts.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .. import db, stakes
from ..db import documents
from . import ring_ranges
from .preflop import DEFAULT_SOLUTION, Solution, load_solution, parse_solution

KIND = "charts-limites"
DEFAULT_BAND = "micro"  # les charts fournis : micro limites, rake élevé
HU = "HU"
HU_NODES = ("sb_open", "bb_vs_open", "sb_vs_3bet", "bb_vs_4bet")
HU_ACTIONS = ("allin", "raise", "call")  # le reste folde
_CLASS_RE = re.compile(r"^(?:([2-9TJQKA])\1|[2-9TJQKA]{2}[so])$")
_FORMAT_RE = re.compile(r"(HU|[3-9]-max)")


def _key(table_format: str, band: str) -> str:
    return f"{table_format}|{band}"


def _check(table_format: str, band: str) -> None:
    if band not in stakes.BANDS:
        raise ValueError(f"Niveau de limites inconnu : {band} ({', '.join(stakes.BANDS)}).")
    if not _FORMAT_RE.fullmatch(str(table_format)):
        raise ValueError(f"Format de table inconnu : {table_format} (« HU », « 6-max », « 3-max »…).")


def stored(table_format: str, band: Optional[str]) -> Optional[dict]:
    """Les charts de ce format pour ce niveau de limites (dans la base), ou None."""
    if not band:
        return None
    data = documents.get(db.current(), KIND, _key(table_format, band))
    return data if isinstance(data, dict) else None


def available() -> dict[str, list[str]]:
    """Format -> niveaux de limites qui ont leurs charts."""
    out: dict[str, list[str]] = {}
    for key in documents.keys(db.current(), KIND):
        fmt, _, band = key.partition("|")
        if band in stakes.BANDS:
            out.setdefault(fmt, []).append(band)
    return {fmt: sorted(bands, key=stakes.BANDS.index) for fmt, bands in sorted(out.items())}


def revision() -> tuple:
    """Change à chaque import ou retrait de charts : pour les caches des calculs qui en dépendent."""
    return documents.revision(db.current(), KIND)


# --- Heads-up : des ranges à la solution ------------------------------------------------------------------------

def _combos(hand_class: str) -> int:
    return 6 if len(hand_class) == 2 else 4 if hand_class.endswith("s") else 12


def _ranges(text, where: str) -> dict[str, float]:
    if not isinstance(text, str):
        raise ValueError(f"{where} : une range en texte (« AA,AKs,KQo:0.5 »).")
    found = ring_ranges.parse_range(text)
    wrong = [h for h in found if not _CLASS_RE.match(h)]
    if wrong:
        raise ValueError(f"{where} : main inconnue « {wrong[0]} » (AA, AKs, KQo…).")
    return found


def hu_from_ranges(data: dict) -> dict:
    """Une solution heads-up (au format de theory/data/hu_100bb.json) depuis des ranges action par action : les nœuds,
    leurs libellés et leurs tailles viennent de la solution fournie (tailles changées par "sizes") ; la part de chaque
    action se calcule sur les combos qui arrivent au nœud (ses parents : l'open du bouton, le 3bet de la BB)."""
    base = json.loads(Path(DEFAULT_SOLUTION).read_text(encoding="utf-8"))
    given = data.get("nodes")
    if not isinstance(given, dict) or not given or any(k not in HU_NODES for k in given):
        raise ValueError(f"Il faut les nœuds de la solution ({', '.join(HU_NODES)}), avec une range par action.")
    sizes = data.get("sizes") if isinstance(data.get("sizes"), dict) else {}
    classes = list(base["nodes"]["sb_open"]["hands"]) + [h for n in base["nodes"].values() for h in n["hands"]]
    classes = list(dict.fromkeys(classes))
    nodes: dict[str, dict] = {}
    strategies: dict[str, dict[str, dict[str, float]]] = {}
    for key in HU_NODES:
        ref = base["nodes"][key]
        actions = given.get(key, {})
        if not isinstance(actions, dict) or any(a not in HU_ACTIONS for a in actions):
            raise ValueError(f"{key} : des ranges pour {', '.join(HU_ACTIONS)} (le reste folde).")
        weights = {a: _ranges(text, f"{key} · {a}") for a, text in actions.items()} if actions else None
        parent = ref.get("parent")
        hands: dict[str, dict[str, float]] = {}
        for h in classes:
            reach = 1.0
            if parent:  # la part de la main qui arrive ici : l'action de son parent
                reach = strategies.get(parent[0], {}).get(h, {}).get(parent[1], 0.0)
            if reach <= 0:
                continue
            if weights is None:  # nœud non donné : celui de la référence
                freqs = dict(ref["hands"].get(h, {}))
            else:
                freqs = {a: round(min(1.0, w.get(h, 0.0)), 4) for a, w in weights.items() if w.get(h, 0.0) > 0}
                taken = sum(freqs.values())
                if taken > 1.0001:
                    raise ValueError(f"{key} : {h} dépasse 100 % ({round(100 * taken)} %).")
                if taken < 0.9999:
                    freqs["fold"] = round(1 - taken, 4)
            if freqs:
                hands[h] = freqs
        strategies[key] = hands
        reach_of = {h: 1.0 if not parent else strategies[parent[0]].get(h, {}).get(parent[1], 0.0) for h in hands}
        combos = sum(_combos(h) * reach_of[h] for h in hands) or 1.0
        totals = {a: round(100 * sum(_combos(h) * reach_of[h] * f.get(a, 0.0) for h, f in hands.items()) / combos, 1)
                  for a in ("allin", "raise", "call", "fold")}
        nodes[key] = dict(ref, sizes=dict(ref.get("sizes", {}), **(sizes.get(key) or {})), hands=hands,
                          totals={a: v for a, v in totals.items() if v or a in ref["totals"]})
        nodes[key].pop("measured", None)
    return {"name": data.get("name") or "Heads-Up 100bb", "description": data.get("description") or "",
            "source": data.get("source") or "", "stack_bb": data.get("stack_bb") or base["stack_bb"],
            "nodes": nodes}


def _hu_solution_data(data: dict) -> dict:
    """La solution heads-up d'un import : déjà main par main (le format de la solution fournie), ou en ranges."""
    nodes = data.get("nodes")
    if isinstance(nodes, dict) and nodes and all(isinstance(n, dict) and "hands" in n for n in nodes.values()):
        if any(k not in nodes for k in HU_NODES):
            raise ValueError(f"Il manque des nœuds à la solution ({', '.join(HU_NODES)}).")
        parse_solution(data)  # lisible ?
        return data
    return hu_from_ranges(data)


# --- Tables à plusieurs : des ranges en texte, combo par combo -----------------------------------------------------
#
# L'export texte de GTO Wizard : un titre de position (« UTG », « BU », « Utg »…) avant chaque range, la range en
# « AcKd: 0.5, … » (le poids de chaque combo, les absents à 0). Les opens d'abord ; « VS OPEN … UTG » ouvre la défense
# face à l'open de l'UTG, où chaque range est un 3bet (« 3bet or fold ») sauf si « 3b : » ou « Call : » la précède
# (la BB, qui paie aussi) ; « VS HJ 3BET » (ou « VERSUS HJ 3BET ») ouvre la réponse de chaque ouvreur au 3bet du HJ,
# « 4b : » et « Call : » avant chaque range. Les poids sont ceux de toute la range de départ : un call face au 3bet est
# une main qui ouvre ET paie. Les tailles et les autres lignes de titre sont ignorées.

POSITION_NAMES = {"UTG": "UTG", "LJ": "LJ", "HJ": "HJ", "CO": "CO", "BTN": "BTN", "BU": "BTN", "SB": "SB", "BB": "BB"}
RANKS = "23456789TJQKA"
_COMBO_RE = re.compile(r"\b([2-9TJQKA][cdhs])([2-9TJQKA][cdhs])\s*:\s*(\d+(?:\.\d+)?)")
_ACTION_RE = re.compile(r"^(3b|3bet|4b|4bet|call|flat)\s*:?$", re.I)
MAX_OPEN = 0.90  # un open de plus de 90 % des mains : la range de départ copiée à la place de celle de l'open


def _class_of(a: str, b: str) -> str:
    """« Kc », « Qd » -> « KQo »."""
    if a[0] == b[0]:
        return a[0] + b[0]
    hi, lo = sorted((a, b), key=lambda c: RANKS.index(c[0]), reverse=True)
    return hi[0] + lo[0] + ("s" if a[1] == b[1] else "o")


def class_range(line: str) -> str:
    """« AcKd: 0.5, AhKh: 1, … » (le poids de chaque combo) -> « AKo:0.0833,AKs:0.25,… » : le poids moyen de chaque
    main sur ses combos (les combos absents à 0)."""
    sums: dict[str, float] = defaultdict(float)
    for a, b, weight in _COMBO_RE.findall(line):
        if a == b:
            raise ValueError(f"Combo impossible : {a}{b}.")
        sums[_class_of(a, b)] += float(weight)
    items = []
    for hand, total in sums.items():
        weight = round(min(1.0, total / _combos(hand)), 4)
        if weight > 0:
            items.append(hand if weight >= 1 else f"{hand}:{weight:g}")
    return ",".join(items)


def share(text: str) -> float:
    """La part des 1 326 combos dans une range par main (0 à 1)."""
    return sum(_combos(h) * w for h, w in ring_ranges.parse_range(text).items()) / 1326


def _position(line: str) -> Optional[str]:
    """La position d'un titre (« UTG », « Utg », « BB (3b et call) »), ou None (pas une ligne de tailles : « UTG vs
    bb : 22bb »)."""
    found = re.match(r"([A-Za-z]+)\b", line)
    return POSITION_NAMES.get(found.group(1).upper()) if found and len(line) <= 40 and ":" not in line else None


def parse_text(text: str) -> dict[str, str]:
    """Les ranges d'un export texte : {"open:UTG": range, "3bet:UTG:HJ": range, "call:UTG:BB": range,
    "call3:UTG:HJ": range, "4bet:UTG:HJ": range…} (ranges par main, ring_ranges.parse_range ; call3 : l'ouvreur paie
    le 3bet)."""
    parts: dict[str, str] = {}
    opener: Optional[str] = None  # None : les opens ; sinon, la défense face à l'open de cette position
    raiser: Optional[str] = None  # la réponse au 3bet de cette position (who : l'ouvreur)
    who: Optional[str] = None
    action: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(_COMBO_RE.findall(line)) >= 2:  # une range
            if who is None:
                raise ValueError("Une range sans titre de position avant elle.")
            if opener is None:
                key = f"open:{who}"
                found = class_range(line)
                if share(found) > MAX_OPEN:
                    raise ValueError(f"L'open {who} couvre {round(100 * share(found))} % des mains : c'est la range de "
                                     "départ, pas celle de l'open (dans GTO Wizard, copie la range de l'action Raise).")
            elif raiser is not None:
                if action is None:
                    raise ValueError(f"{who} face au 3bet {raiser} : « 4b : » ou « Call : » avant chaque range.")
                key = f"{'call3' if action == 'call' else '4bet'}:{who}:{raiser}"
            elif action is None and who == "BB":
                raise ValueError(f"Face à l'open {opener}, la BB : « 3b : » ou « Call : » avant chaque range.")
            else:
                key = f"{action or '3bet'}:{opener}:{who}"
            parts[key] = class_range(line)
            action = None
            continue
        upper = line.upper()
        if upper.startswith("VS ") or upper.startswith("VERSUS "):
            found = next((POSITION_NAMES[t] for t in re.findall(r"[A-Z]+", upper)[1:] if t in POSITION_NAMES), None)
            if found is None:
                raise ValueError(f"Face à quel open ou quel 3bet ? « {line[:60]} »")
            if "3BET" in upper.replace(" ", ""):
                opener, raiser = "", found  # la réponse des ouvreurs au 3bet de cette position
            else:
                opener, raiser = found, None
            who, action = None, None
        elif _ACTION_RE.match(line):
            word = _ACTION_RE.match(line).group(1).lower()
            action = "call" if word in ("call", "flat") else "4bet" if word.startswith("4") else "3bet"
        elif _position(line):
            who, action = _position(line), None
    if not parts:
        raise ValueError("Aucune range lue : il faut des combos « AcKd: 0.5 » sous un titre de position.")
    return parts


def lines_from_parts(parts: dict[str, str]) -> dict[str, dict]:
    """Les lignes d'une solution des tables à plusieurs (ring_ranges) depuis les ranges lues : open puis call de la BB
    (pot simple ; l'open seul s'il manque la défense), open puis 3bet (pot 3bet, la range du 3bettor)."""
    lines: dict[str, dict] = {}

    def line(key: str, pot_type: str) -> dict:
        return lines.setdefault(key, {"pot_type": pot_type, "ranges": {}})["ranges"]

    for key, text in parts.items():
        kind, *who = key.split(":")
        if kind == "open":
            line(f"{who[0]}:raise BB:call", "SRP")[who[0]] = text
        elif kind == "call":
            opener, caller = who
            ranges = line(f"{opener}:raise {caller}:call", "SRP")
            ranges[caller] = text
            if f"open:{opener}" in parts:
                ranges[opener] = parts[f"open:{opener}"]
        elif kind == "3bet":
            opener, raiser = who
            line(f"{opener}:raise {raiser}:raise {opener}:call", "pot 3bet")[raiser] = text
        elif kind == "call3":  # l'ouvreur paie le 3bet : sa range au flop du pot 3bet
            opener, raiser = who
            line(f"{opener}:raise {raiser}:raise {opener}:call", "pot 3bet")[opener] = text
        elif kind == "4bet":
            opener, raiser = who
            line(f"{opener}:raise {raiser}:raise {opener}:raise {raiser}:call", "pot 4bet")[opener] = text
    pairs = {(k.split(":")[0] in ("call3", "4bet"), *k.split(":")[1:]) for k in parts if not k.startswith("open:")}
    for (reply, opener, other) in pairs:  # deux actions d'une même main : au plus 100 % (de ce qu'il ouvre)
        kinds = ("call3", "4bet") if reply else ("3bet", "call")
        total = defaultdict(float)
        for kind in kinds:
            for hand, weight in ring_ranges.parse_range(parts.get(f"{kind}:{opener}:{other}", "")).items():
                total[hand] += weight
        top = ring_ranges.parse_range(parts.get(f"open:{opener}", "")) if reply else {}
        over = next((h for h, w in total.items() if w > (top.get(h, 1.0) if reply and top else 1.0) + 0.0101), None)
        if over:
            what = (f"{opener} face au 3bet {other} : {over} dépasse ce qu'il ouvre (call et 4bet)" if reply else
                    f"{other} face à l'open {opener} : {over} dépasse 100 % (3bet et call)")
            raise ValueError(what + ".")
    return lines


def import_text(text: str, band: str, table_format: str = "6-max") -> dict:
    """Ajoute les ranges d'un export texte aux charts de ce format pour ce niveau (les fichiers se complètent : opens
    d'un côté, défense de l'autre) ; renvoie ce qui est gardé."""
    _check(table_format, band)
    if table_format == HU:
        raise ValueError("En heads-up, importe un fichier JSON (une range par action à chaque nœud, voir le README).")
    parts = parse_text(text)
    old = stored(table_format, band) or {}
    merged = dict(old.get("parts") or {}, **parts)
    title = next((t.strip() for t in text.splitlines() if t.strip() and not _COMBO_RE.search(t)), "")[:150]
    sources = [x for x in (old.get("source") or "").split(" · ") if x and old.get("parts")]
    source = " · ".join(dict.fromkeys(sources + ([title] if title else [])))[:300]
    doc = {"format": table_format, "band": band, "source": source, "parts": merged, "lines": lines_from_parts(merged)}
    documents.put(db.current(), KIND, _key(table_format, band), doc)
    return summary(table_format, band)


# --- Enregistrer, retirer ---------------------------------------------------------------------------------------

def save(table_format: str, band: str, data: dict) -> dict:
    """Garde ces charts pour ce format et ce niveau de limites ; renvoie ce qui est gardé (sans les ranges)."""
    _check(table_format, band)
    if not isinstance(data, dict):
        raise ValueError("Charts illisibles : il faut un objet JSON.")
    extra = {k: str(data[k])[:300] for k in ("source", "rake", "description") if data.get(k)}
    if table_format == HU:
        solution = _hu_solution_data(data)
        doc = dict(solution, **extra, format=HU, band=band)
    else:
        if not isinstance(data.get("lines"), dict) or not data["lines"]:
            raise ValueError("Charts illisibles : il faut leurs lignes (« lines »), comme une solution des tables à "
                             "plusieurs.")
        doc = dict(data, **extra, format=table_format, band=band)
    documents.put(db.current(), KIND, _key(table_format, band), doc)
    return summary(table_format, band)


def delete(table_format: str, band: str) -> bool:
    _check(table_format, band)
    return documents.delete(db.current(), KIND, _key(table_format, band))


def import_file(path: Path, band: str, table_format: Optional[str] = None) -> str:
    """Importe des charts d'un fichier pour ce niveau de limites (JSON, ou l'export texte des tables à plusieurs) ;
    renvoie leur format de table."""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except ValueError:
        import_text(text, band, table_format or "6-max")
        return table_format or "6-max"
    if not isinstance(data, dict):
        raise ValueError("Charts illisibles : il faut un objet JSON.")
    table_format = table_format or data.get("format") or (HU if "nodes" in data else Path(path).stem)
    save(table_format, band, data)
    return table_format


def summary(table_format: str, band: str) -> Optional[dict]:
    """Ce qu'on sait des charts de ce format et de ce niveau : source, rake, nombre de nœuds ou de lignes."""
    data = stored(table_format, band)
    if data is None:
        return None
    size = len(data.get("nodes") or {}) if table_format == HU else len(data.get("lines") or {})
    return {"format": table_format, "band": band, "label": stakes.label(band), "source": data.get("source") or "",
            "rake": data.get("rake") or "", "size": size, "covers": covers(data)}


def covers(data: dict) -> str:
    """Ce que donnent des charts importés en texte (« opens de l'UTG, du HJ… ; défense face à l'open de… »), ou vide."""
    parts = data.get("parts") or {}
    order = list(dict.fromkeys(POSITION_NAMES.values()))
    opens = sorted({k.split(":")[1] for k in parts if k.startswith("open:")}, key=order.index)
    faced = sorted({k.split(":")[1] for k in parts if k.split(":")[0] in ("3bet", "call")}, key=order.index)
    replies = sorted({k.split(":")[1] for k in parts if k.split(":")[0] in ("call3", "4bet")}, key=order.index)
    out = []
    if parts:
        out.append(f"opens : {', '.join(opens)}" if opens else "pas d'open (ceux de la référence)")
        out.append(f"défense face à l'open : {', '.join(faced)}" if faced else "pas de défense (celle de la référence)")
        out.append(f"réponse au 3bet : {', '.join(replies)}" if replies else "pas de réponse au 3bet")
    return " ; ".join(out)


# --- La théorie de chaque main ----------------------------------------------------------------------------------

_SOLUTIONS: dict[tuple, Solution] = {}


def hu_solution(band: Optional[str]) -> Solution:
    """La solution heads-up de ce niveau de limites : la sienne si elle est importée, sinon celle fournie."""
    data = stored(HU, band)
    if data is None:
        return load_solution()
    key = (band, revision())
    if key not in _SOLUTIONS:
        _SOLUTIONS.clear()
        _SOLUTIONS[key] = parse_solution(data)
    return _SOLUTIONS[key]


def _merge(reference: dict, mine: dict) -> dict:
    """Les lignes de la référence, avec tes ranges à la place des siennes (ligne par ligne, position par position) :
    ce que tes charts ne donnent pas reste celui de la référence. Quand tes charts donnent l'open d'une position mais
    pas sa réponse au 3bet, celle de la référence (faite pour un autre open) n'est plus lue."""
    out = {key: dict(ranges) for key, ranges in reference.items()}
    opens = set()
    for key, ranges in mine.items():
        out.setdefault(key, {}).update(ranges)
        steps = key.split()
        opener = steps[0].split(":")[0]
        if len(steps) == 2 and steps[0].endswith(":raise") and opener in ranges:
            opens.add(opener)
    for key, ranges in out.items():
        opener = key.split()[0].split(":")[0]
        if len(key.split()) >= 3 and opener in opens and opener not in mine.get(key, {}):
            ranges.pop(opener, None)
    return out


_LINES: dict[tuple, dict] = {}


def ring_lines(band: Optional[str]) -> dict[str, dict]:
    """Les charts des tables à plusieurs pour ce niveau : ceux importés pour lui à la place de ceux de la référence
    (handplay.ring_lines), là où ils en donnent (_merge). Gardés tant que les charts ne changent pas (à lire, pas à
    modifier)."""
    key = (band, documents.revision(db.current(), KIND, "ranges"))
    if key not in _LINES:
        if len(_LINES) >= 8:
            _LINES.clear()
        _LINES[key] = _ring_lines(band)
    return _LINES[key]


def _ring_lines(band: Optional[str]) -> dict[str, dict]:
    from .. import handplay
    lines = handplay.ring_lines()
    for fmt in available():
        if fmt == HU:
            continue
        data = stored(fmt, band)
        if data:
            mine = {key: {pos: ring_ranges.parse_range(text) for pos, text in entry["ranges"].items()}
                    for key, entry in data["lines"].items()
                    if isinstance(entry, dict) and isinstance(entry.get("ranges"), dict)}
            lines[fmt] = _merge(lines.get(fmt, {}), mine)
    return lines


def band_for(hand) -> Optional[str]:
    """Le niveau de limites d'une main quand des charts sont importés pour lui dans sa famille de format (le heads-up,
    les tables à plusieurs), sinon None : ses ranges sont celles de la référence."""
    band = stakes.band_of(hand.bb)
    if band is None:
        return None
    found = available()
    if len(hand.seats) == 2:
        return band if band in found.get(HU, []) else None
    return band if any(band in bands for fmt, bands in found.items() if fmt != HU) else None


def ring_lookup(table_format: str, steps: list[tuple[str, str]], band: Optional[str]
                ) -> Optional[tuple[str, dict[str, dict[str, float]], list[tuple[str, str]]]]:
    """Comme ring_ranges.resolve puis lookup, dans les charts de ce niveau (ceux importés, sinon ceux de la
    référence) : (type de pot, {position des charts : range}, étapes aux positions des charts), ou None."""
    lines = ring_lines(band)
    found = ring_ranges.chart_mapping(table_format, lines)
    if found is None:
        return None
    chart_format, mapping = found
    if mapping and any(pos not in mapping for pos, _ in steps):
        return None
    chart_steps = [(mapping[pos], kind) for pos, kind in steps] if mapping else list(steps)
    key = ring_ranges.line_key(chart_steps)
    ranges = lines[chart_format].get(key)
    if not ranges:
        return None
    entry = ((stored(chart_format, band) or {}).get("lines") or {}).get(key) or \
        ((ring_ranges.solution(chart_format) or {}).get("lines") or {}).get(key) or {}
    raises = sum(1 for _, kind in steps if kind == "raise")
    pot_type = (entry.get("pot_type") if isinstance(entry, dict) else None) or ring_ranges.POT_TYPES.get(
        raises, "pot limpé" if raises == 0 else f"pot {raises + 1}bet")
    return pot_type, {pos: dict(r) for pos, r in ranges.items()}, chart_steps


def theory(heads_up: bool = True, ring: bool = True):
    """Le jugement préflop de chaque main (handplay.Theory) avec les charts de son niveau de limites : ceux importés
    pour lui, sinon ceux de la référence."""
    from .. import handplay
    found = available()
    bands = {}
    for band in stakes.BANDS:
        has_hu = heads_up and band in found.get(HU, [])
        has_ring = ring and any(band in b for fmt, b in found.items() if fmt != HU)
        if has_hu or has_ring:
            bands[band] = handplay.Theory(hu_solution(band) if heads_up else None,
                                          ring_lines(band) if ring else None)
    return handplay.Theory(load_solution() if heads_up else None, handplay.ring_lines() if ring else None,
                           bands=bands)


def mix(hands) -> dict[Optional[str], int]:
    """Le nombre de mains de chaque niveau de limites (None : grosse blinde inconnue)."""
    out: dict[Optional[str], int] = {}
    for h in hands:
        band = stakes.band_of(h.bb)
        out[band] = out.get(band, 0) + 1
    return out


def main_band(hands) -> Optional[str]:
    """Le niveau où ces mains sont les plus nombreuses (None : aucune)."""
    found = {b: n for b, n in mix(hands).items() if b}
    return max(found, key=lambda b: (found[b], -stakes.BANDS.index(b))) if found else None


def hu_value(node: str, action: str, weights: dict[Optional[str], int]) -> float:
    """La fréquence (%) de la théorie heads-up à ce nœud, pondérée par tes mains de chaque niveau de limites (la
    solution de chaque niveau, sinon celle fournie)."""
    from . import preflop
    total = sum(weights.values())
    if not total or not available().get(HU):
        return preflop.gto_value(node, action)
    return sum(n * preflop.gto_value(node, action, hu_solution(band)) for band, n in weights.items()) / total


def used(table_format: str, hands) -> str:
    """Quels charts jugent ces mains, en une phrase (vide quand ce sont ceux de la référence pour toutes)."""
    fmt = HU if table_format == HU else None
    found = available()
    counts = {b: n for b, n in mix(hands).items() if b}
    mine = [b for b in counts if b in (found.get(HU, []) if fmt else
                                       [x for f, bs in found.items() if f != HU for x in bs])]
    if not mine:
        if not found:
            return ""
        return ("Préflop jugé avec les charts de la référence (micro limites, rake élevé) : pas de charts importés "
                "pour tes limites.")
    parts = [f"{stakes.NAMES[b].lower()} ({counts[b]} mains)" for b in stakes.BANDS if b in mine]
    rest = sum(n for b, n in counts.items() if b not in mine)
    tail = f" ; les autres ({rest} mains), avec ceux de la référence (micro limites, rake élevé)" if rest else ""
    return f"Préflop jugé avec tes charts par limites : {', '.join(parts)}{tail}."


def page_band(table_format: str, hands) -> tuple[Optional[str], str]:
    """Le niveau de limites dont les charts font une page Préflop (celui où ces mains sont les plus nombreuses, s'il a
    ses charts ; None : ceux de la référence) et une phrase pour le dire (vide sans charts importés)."""
    found = available()
    has = found.get(HU, []) if table_format == HU else sorted({b for f, bs in found.items() if f != HU for b in bs})
    if not has:
        return None, ""
    band = main_band(hands)
    if band in has:
        return band, (f"Charts des {stakes.NAMES[band].lower()} (le niveau où tu as le plus de mains), importés pour "
                      "elles ; le Leakfinding juge chaque main avec ceux de son niveau.")
    return None, ("Charts de la référence (micro limites, rake élevé) : pas de charts importés pour le niveau où tu as "
                  "le plus de mains.")
