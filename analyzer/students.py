"""Les élèves : un espace par élève dans la base (nom, pseudo de l'élève à la table, date de création), avec ses
historiques et ses mains. Son dossier ~/.analyzer/eleves/<identifiant>/ sert de boîte d'arrivée (les historiques
qu'on y dépose sont importés), comme le dossier de tes mains.

Le pseudo est facultatif : sans lui, le joueur des mains est détecté comme pour tes propres mains. Ses pseudos se
choisissent ensuite parmi ceux de ses historiques (pseudos()) : seules les mains où l'un d'eux est le héros sont les
siennes ; sans choix, tous les héros de ses historiques sont lui (sur GGPoker, le héros s'appelle « Hero »).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import db
from .cli import slugify
from .db import documents
from .db import hands as db_hands
from .theory import postflop

PREFIX = "eleve:"
PSEUDOS = "eleve-pseudos"  # documents : les pseudos choisis de chaque élève
FORMATS = "eleve-formats"  # documents : les formats que joue chaque élève
TABLE_FORMATS = ("HU", "ring")  # le heads-up, les tables à plusieurs
MAX_PSEUDOS = 200


def root() -> Path:
    return postflop.home() / "eleves"


def folder(ident: str) -> Path:
    return root() / ident


def _valid(ident: str) -> bool:
    return bool(ident) and slugify(ident) == ident


def _student(row: dict) -> dict:
    return {"id": row["key"][len(PREFIX):], "name": row["name"], "pseudo": row["pseudo"], "created": row["created"]}


def get(ident: str) -> Optional[dict]:
    if not _valid(ident):
        return None
    found = [r for r in db_hands.spaces(db.current(), PREFIX + ident) if r["key"] == PREFIX + ident]
    return _student(found[0]) if found else None


def all_students() -> list[dict]:
    return sorted((_student(r) for r in db_hands.spaces(db.current(), PREFIX)), key=lambda s: s["name"].lower())


def create(name: str, pseudo: Optional[str] = None) -> dict:
    """Nouvel élève (ou l'élève existant du même nom)."""
    name = (name or "").strip()[:60]
    ident = slugify(name)[:40]
    if not name or not ident:
        raise ValueError("Donne un nom à l'élève.")
    existing = get(ident)
    if existing:
        return existing
    db_hands.space(db.current(), PREFIX + ident, name, (pseudo or "").strip()[:60] or None)
    return get(ident)


def pseudos(ident: str) -> list[str]:
    """Les pseudos choisis de l'élève (vide : tous les héros de ses historiques)."""
    data = documents.get(db.current(), PSEUDOS, ident)
    return list(data.get("pseudos") or []) if isinstance(data, dict) else []


def formats(ident: str) -> list[str]:
    """Les formats que joue l'élève (« HU », « ring ») ; vide : tous."""
    data = documents.get(db.current(), FORMATS, ident)
    return [f for f in TABLE_FORMATS if f in (data.get("formats") or [])] if isinstance(data, dict) else []


def set_formats(ident: str, chosen: object) -> list[str]:
    """Enregistre les formats que joue l'élève ; None (ou tous) : tous. ValueError sans format valide."""
    if chosen is not None and (not isinstance(chosen, list) or not chosen or not set(chosen) <= set(TABLE_FORMATS)):
        raise ValueError("Choisis au moins un format : heads-up ou tables à plusieurs.")
    kept = [f for f in TABLE_FORMATS if chosen is not None and f in chosen]
    if kept and len(kept) < len(TABLE_FORMATS):
        documents.put(db.current(), FORMATS, ident, {"formats": kept})
        return kept
    documents.delete(db.current(), FORMATS, ident)
    return []


def set_pseudos(ident: str, names: list[str]) -> list[str]:
    """Enregistre ses pseudos (sans doublons, dans l'ordre donné) ; une liste vide revient au choix automatique."""
    kept = list(dict.fromkeys(n.strip() for n in names if n.strip()))[:MAX_PSEUDOS]
    if kept:
        documents.put(db.current(), PSEUDOS, ident, {"pseudos": kept})
    else:
        documents.delete(db.current(), PSEUDOS, ident)
    return kept
