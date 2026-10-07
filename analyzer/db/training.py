"""Le journal de l'entraîneur, par compte : une ligne par décision jugée (app/trainer.py)."""
from __future__ import annotations

import json
from typing import Iterable, Optional

from . import Database


def add(db: Database, rows: Iterable[dict], account: Optional[int] = None, skip_known: bool = False) -> int:
    """Ajoute les décisions ; skip_known : sans celles déjà au journal (reprise d'un ancien fichier, restauration)."""
    account = account or db.account()
    rows = [(account, int(r.get("t", 0)), json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in rows]
    with db.transaction():
        if skip_known and rows:
            known = {text for (text,) in db.all("SELECT donnees FROM entrainement WHERE compte_id = ?", (account,))}
            rows = [r for r in rows if r[2] not in known]
            rows = list(dict.fromkeys(rows))
        db.executemany("INSERT INTO entrainement (compte_id, le, donnees) VALUES (?, ?, ?)", rows)
    return len(rows)


def every(db: Database, account: Optional[int] = None) -> list[dict]:
    out = []
    for (text,) in db.all("SELECT donnees FROM entrainement WHERE compte_id = ? ORDER BY le, id",
                          (account or db.account(),)):
        try:
            out.append(json.loads(text))
        except ValueError:
            continue
    return out


def clear(db: Database, account: Optional[int] = None) -> None:
    db.execute("DELETE FROM entrainement WHERE compte_id = ?", (account or db.account(),))
