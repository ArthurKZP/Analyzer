"""Les résumés des mains passées au solveur (theory/review.py), par compte : une ligne par spot (clé de l'étude)."""
from __future__ import annotations

import json
import zlib
from typing import Optional

from . import Database, now


def _pack(data: dict) -> bytes:
    return zlib.compress(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(), 6)


def keys(db: Database, account: Optional[int] = None) -> set[str]:
    return {r[0] for r in db.all("SELECT cle FROM analyses WHERE compte_id = ?", (account or db.account(),))}


def count(db: Database, account: Optional[int] = None) -> int:
    return db.value("SELECT COUNT(*) FROM analyses WHERE compte_id = ?", (account or db.account(),), 0)


def get(db: Database, key: str, account: Optional[int] = None) -> Optional[dict]:
    blob = db.value("SELECT donnees FROM analyses WHERE compte_id = ? AND cle = ?", (account or db.account(), key))
    return json.loads(zlib.decompress(bytes(blob))) if blob is not None else None


def put(db: Database, key: str, data: dict, account: Optional[int] = None) -> None:
    db.execute("INSERT INTO analyses (compte_id, cle, main, donnees, maj_le) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT (compte_id, cle) DO UPDATE SET main = excluded.main, donnees = excluded.donnees, "
               "maj_le = excluded.maj_le", (account or db.account(), key, str(data.get("hand", "")), _pack(data), now()))


def every(db: Database, account: Optional[int] = None) -> list[tuple[str, dict]]:
    return [(key, json.loads(zlib.decompress(bytes(blob)))) for key, blob in
            db.all("SELECT cle, donnees FROM analyses WHERE compte_id = ? ORDER BY cle", (account or db.account(),))]
