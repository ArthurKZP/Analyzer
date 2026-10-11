"""Les études du solveur, par compte : la fiche de chaque étude (ce que la bibliothèque affiche) ; l'arbre résolu,
lui, est un fichier à part (analyzer/blobs.py : <clé>.etude, de 20 Mo à quelques centaines de Mo)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import Database, now
from .documents import bump, pack, unpack

KIND = "etudes"  # type de révision : la liste des études change


def save(db: Database, key: str, fiche: dict, size: int, account: Optional[int] = None) -> None:
    account = account or db.account()
    with db.transaction():
        db.execute("INSERT INTO etudes (compte_id, cle, type, fiche, taille, maj_le) VALUES (?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT (compte_id, cle) DO UPDATE SET type = excluded.type, fiche = excluded.fiche, "
                   "taille = excluded.taille, maj_le = excluded.maj_le",
                   (account, key, str(fiche.get("kind", "")), pack(fiche), int(size), now()))
        bump(db, KIND, account)


def get(db: Database, key: str, account: Optional[int] = None) -> Optional[dict]:
    blob = db.value("SELECT fiche FROM etudes WHERE compte_id = ? AND cle = ?", (account or db.account(), key))
    return unpack(blob) if blob is not None else None


def keys(db: Database, account: Optional[int] = None) -> set[str]:
    return {r[0] for r in db.all("SELECT cle FROM etudes WHERE compte_id = ?", (account or db.account(),))}


def _stamp(text: str) -> float:
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def every(db: Database, account: Optional[int] = None) -> list[dict]:
    """Les fiches, avec la taille de l'arbre (« size ») et le moment de l'enregistrement (« mtime »)."""
    rows = db.all("SELECT cle, fiche, taille, maj_le FROM etudes WHERE compte_id = ?", (account or db.account(),))
    return [dict(unpack(blob), key=key, size=size, mtime=_stamp(when)) for key, blob, size, when in rows]


def delete(db: Database, key: str, account: Optional[int] = None) -> bool:
    account = account or db.account()
    with db.transaction():
        gone = db.one("DELETE FROM etudes WHERE compte_id = ? AND cle = ? RETURNING cle", (account, key)) is not None
        if gone:
            bump(db, KIND, account)
    return gone
