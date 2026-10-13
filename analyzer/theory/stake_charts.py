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
- aux tables à plusieurs (« 6-max », « 3-max »…), au format des solutions de theory/ring_ranges.py.

Ce qui en dépend : le jugement préflop de chaque main (handplay.Theory, avec les charts de son niveau : Leakfinding,
mains de départ, vérification en jeu, suivi), les repères préflop du Leakfinding heads-up (la théorie de chaque niveau,
pondérée par tes mains de chaque niveau) et les pages Préflop (les charts du niveau où tu as le plus de mains). Les
ranges des coups résolus au solveur restent celles de la référence : un coup déjà résolu ne se résout pas à nouveau.
"""
from __future__ import annotations

import json
import re
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
    """Importe des charts d'un fichier JSON pour ce niveau de limites ; renvoie leur format de table."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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
            "rake": data.get("rake") or "", "size": size}


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


def ring_lines(band: Optional[str]) -> dict[str, dict]:
    """Les charts des tables à plusieurs pour ce niveau : ceux importés pour lui, format par format, sinon ceux de la
    référence (handplay.ring_lines)."""
    from .. import handplay
    lines = handplay.ring_lines()
    for fmt in available():
        if fmt == HU:
            continue
        data = stored(fmt, band)
        if data:
            lines[fmt] = {key: {pos: ring_ranges.parse_range(text) for pos, text in entry["ranges"].items()}
                          for key, entry in data["lines"].items()
                          if isinstance(entry, dict) and isinstance(entry.get("ranges"), dict)}
    return lines


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
