"""Les élèves : un dossier de mains par élève, dans ~/.analyzer/eleves/<identifiant>/.

Chaque dossier contient eleve.json (nom, pseudo de l'élève à la table, date de création) et les historiques
importés. Le pseudo est facultatif : sans lui, le joueur des mains est détecté comme pour tes propres mains.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .cli import slugify
from .theory import postflop

META = "eleve.json"


def root() -> Path:
    return postflop.home() / "eleves"


def folder(ident: str) -> Path:
    return root() / ident


def _valid(ident: str) -> bool:
    return bool(ident) and slugify(ident) == ident


def get(ident: str) -> Optional[dict]:
    if not _valid(ident):
        return None
    try:
        data = json.loads((folder(ident) / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(data, id=ident) if isinstance(data, dict) and data.get("name") else None


def all_students() -> list[dict]:
    if not root().is_dir():
        return []
    found = (get(p.name) for p in sorted(root().iterdir()) if p.is_dir())
    return sorted((s for s in found if s), key=lambda s: s["name"].lower())


def create(name: str, pseudo: Optional[str] = None) -> dict:
    """Nouvel élève (ou l'élève existant du même nom)."""
    name = (name or "").strip()[:60]
    ident = slugify(name)[:40]
    if not name or not ident:
        raise ValueError("Donne un nom à l'élève.")
    existing = get(ident)
    if existing:
        return existing
    path = folder(ident)
    path.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "pseudo": (pseudo or "").strip()[:60] or None, "created": datetime.now().isoformat(timespec="seconds")}
    (path / META).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(data, id=ident)
