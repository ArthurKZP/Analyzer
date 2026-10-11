"""Entraîneur : jouer des mains face au solveur sur les spots d'étude résolus (static/trainer.js).

Le jeu tourne dans la page, sur les nœuds de l'explorateur (session de l'étude ouverte) ; le serveur
donne la liste des spots et des situations, juge les abattages et tient le journal des décisions (dans la base,
table entrainement) pour suivre les progrès situation par situation.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .. import db
from ..cards import describe_holding, evaluate, parse_card
from ..db import training
from ..theory import postflop, review, sizing, studyspots

MAX_ENTRIES = 40  # décisions enregistrées par envoi (une main en a bien moins)
RECENT_DAYS = 7


def situation_labels(family: str) -> dict[str, str]:
    """Libellé de chaque situation de la famille, vue du joueur qui agit : « bet:… » (miser ou checker)
    et « face:… » (face à une mise ou une relance) — mêmes clés que review.situation."""
    out = {}
    for s in sizing.situations(family):
        kind, where, past, *level = s.key.split(":")
        other = "i" if where[1] == "o" else "o"
        if kind == "bet":
            out[s.key] = review.situation_label(s.key, family)
            face = f"face:{where[0]}{other}:{past}:1"
        else:
            face = f"face:{where[0]}{other}:{past}:{int(level[0]) + 2}"
        out[face] = review.situation_label(face, family)
    return out


def overview() -> dict:
    """Spots résolus par famille, situations jouables et progrès."""
    studies = studyspots.spot_studies()
    families = {}
    for family, info in studyspots.FAMILIES.items():
        spots = sorted(({"id": m["id"], "board": m["board"], "texture": m.get("texture")}
                        for m in studies.values() if m.get("family") == family),
                       key=lambda s: (studyspots.TEXTURES.index(s["texture"])
                                      if s["texture"] in studyspots.TEXTURES else 99, s["id"]))
        families[family] = {"name": info["name"], "label": info["label"], "spots": spots,
                            "situations": situation_labels(family)}
    return {"families": families, "textures": list(studyspots.TEXTURES), "progress": progress(),
            "ready": postflop.status()["ready"]}


# --- Abattage ------------------------------------------------------------------------------------

def hands_info(board: list[str], holes: list[list[str]]) -> dict:
    """Force de chaque main sur le board, et le gagnant à la river (0, 1 ; None : partage)."""
    out = {"holdings": [describe_holding(h, board) for h in holes]}
    if len(board) == 5:
        values = [evaluate([parse_card(c) for c in h + board]) for h in holes]
        out["winner"] = None if values[0] == values[1] else 0 if values[0] > values[1] else 1
    return out


def valid_cards(cards, n: Optional[int] = None) -> bool:
    return (isinstance(cards, list) and (n is None or len(cards) == n)
            and all(isinstance(c, str) and re.fullmatch(r"[2-9TJQKA][cdhs]", c) for c in cards)
            and len(set(cards)) == len(cards))


# --- Journal -------------------------------------------------------------------------------------

FIELDS = {"family": str, "spot": str, "key": str, "combo": str, "played": str, "best": str}


def clean(entry) -> Optional[dict]:
    """Une décision envoyée par la page, réduite aux champs attendus (None si elle est invalide)."""
    if not isinstance(entry, dict):
        return None
    out = {}
    for name, kind in FIELDS.items():
        value = entry.get(name)
        if not isinstance(value, kind) or len(value) > 80:
            return None
        out[name] = value
    if out["family"] not in studyspots.FAMILIES:
        return None
    for name in ("loss", "freq"):
        value = entry.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value < 10_000:
            return None
        out[name] = round(float(value), 3)
    out["t"] = int(time.time())
    return out


def record(entries) -> dict:
    rows = [e for e in (clean(x) for x in (entries if isinstance(entries, list) else [])[:MAX_ENTRIES]) if e]
    if rows:
        training.add(db.current(), rows)
    return {"saved": len(rows), "progress": progress()}


def read_journal() -> list[dict]:
    return [row for row in training.every(db.current()) if isinstance(row, dict) and clean(row) is not None]


def progress() -> dict:
    """Bilan du journal : en tout, sur les 7 derniers jours, et par situation."""
    rows = read_journal()
    since = time.time() - RECENT_DAYS * 86400

    def total(selected: list[dict]) -> dict:
        n = len(selected)
        errors = sum(r["loss"] >= review.ERROR for r in selected)
        return {"n": n, "errors": errors, "lost": round(sum(r["loss"] for r in selected), 2),
                "good": round(1 - errors / n, 3) if n else None}

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["family"], r["key"]), []).append(r)
    labels = {f: situation_labels(f) for f in {fam for fam, _ in groups}}
    situations = []
    for (family, key), selected in groups.items():
        recent = [r for r in selected if r["t"] >= since]
        situations.append(dict(total(selected), family=family, key=key,
                                label=labels[family].get(key) or review.situation_label(key, family),
                                recent=total(recent), last=max(r["t"] for r in selected)))
    situations.sort(key=lambda s: (-s["lost"], -s["n"]))
    return {"all": total(rows), "recent": total([r for r in rows if r["t"] >= since]), "situations": situations}


def clear() -> dict:
    training.clear(db.current())
    return progress()
