"""Les élèves : un espace par élève dans la base (nom, pseudo de l'élève à la table, date de création), avec ses
historiques et ses mains. Son dossier ~/.analyzer/eleves/<identifiant>/ sert de boîte d'arrivée (les historiques
qu'on y dépose sont importés), comme le dossier de tes mains.

Le pseudo est facultatif : sans lui, le joueur des mains est détecté comme pour tes propres mains.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import db
from .cli import slugify
from .db import hands as db_hands
from .theory import postflop

PREFIX = "eleve:"


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
