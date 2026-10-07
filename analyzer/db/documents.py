"""Les documents du solveur, par compte : un JSON (compressé) par type et par clé.

Types : « tailles » (choix des tailles de mise d'un flop, clé « famille:flop »), « plan » (plan de jeu d'un spot
d'étude, clé de l'étude), « resolution » (résultat de la ligne jouée, clé postflop.cache_key), « precision »
(précision du dernier résultat d'un spot, clé postflop.base_key), « ranges » (solution préflop d'un format de table,
clé « 6-max »…), « ranges-ajustees » (tes ranges ajustées, clé « perso »).

Révisions : chaque écriture d'un type change son numéro (table revisions) ; les calculs qui dépendent d'un type
(liste des spots d'étude à jour, clés des spots des mains…) gardent leur résultat tant que ce numéro ne change pas,
y compris quand l'écriture vient d'un autre programme (la ligne de commande, un autre serveur).
"""
from __future__ import annotations

import json
import secrets
import zlib
from typing import Any, Iterable, Optional

from . import Database, now


def pack(data: Any) -> bytes:
    return zlib.compress(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(), 6)


def unpack(blob) -> Any:
    return json.loads(zlib.decompress(bytes(blob)))


# --- Révisions ---------------------------------------------------------------------------------

def bump(db: Database, kind: str, account: Optional[int] = None) -> None:
    """Nouvelle révision : un nombre tiré au hasard (jamais le même, même dans une base recréée, où un compteur
    repartirait de zéro et ferait passer pour à jour des calculs gardés d'avant)."""
    db.execute("INSERT INTO revisions (compte_id, type, numero) VALUES (?, ?, ?) ON CONFLICT (compte_id, type) "
               "DO UPDATE SET numero = excluded.numero", (account or db.account(), kind, secrets.randbits(62)))


def revision(db: Database, *kinds: str, account: Optional[int] = None) -> tuple:
    """Les numéros de révision de ces types (et la base et le compte : une autre base, d'autres données)."""
    account = account or db.account()
    marks = ",".join("?" * len(kinds))
    found = dict(db.all(f"SELECT type, numero FROM revisions WHERE compte_id = ? AND type IN ({marks})",
                        (account, *kinds)))
    return (db.url, account) + tuple(found.get(k, 0) for k in kinds)


# --- Documents ---------------------------------------------------------------------------------

_MISSING = object()
_MEMO: dict[tuple, tuple[tuple, dict]] = {}  # (base, compte, type) -> (révision, {clé: document ou _MISSING})


def get(db: Database, kind: str, key: str, account: Optional[int] = None, memo: bool = True) -> Any:
    """Le document, ou None. memo : gardé en mémoire tant que le type n'a pas de nouvelle révision (ne pas modifier
    le document rendu)."""
    account = account or db.account()
    if not memo:
        blob = db.value("SELECT donnees FROM documents WHERE compte_id = ? AND type = ? AND cle = ?",
                        (account, kind, key))
        return unpack(blob) if blob is not None else None
    current = revision(db, kind, account=account)
    slot = (db.url, account, kind)
    entry = _MEMO.get(slot)
    if entry is None or entry[0] != current:
        entry = _MEMO[slot] = (current, {})
    found = entry[1].get(key)
    if found is None:
        blob = db.value("SELECT donnees FROM documents WHERE compte_id = ? AND type = ? AND cle = ?",
                        (account, kind, key))
        found = entry[1][key] = unpack(blob) if blob is not None else _MISSING
    return None if found is _MISSING else found


def put(db: Database, kind: str, key: str, data: Any, account: Optional[int] = None) -> None:
    account = account or db.account()
    with db.transaction():
        db.execute("INSERT INTO documents (compte_id, type, cle, donnees, maj_le) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT (compte_id, type, cle) DO UPDATE SET donnees = excluded.donnees, "
                   "maj_le = excluded.maj_le", (account, kind, key, pack(data), now()))
        bump(db, kind, account)


def delete(db: Database, kind: str, key: str, account: Optional[int] = None) -> bool:
    account = account or db.account()
    with db.transaction():
        gone = db.one("DELETE FROM documents WHERE compte_id = ? AND type = ? AND cle = ? RETURNING cle",
                      (account, kind, key)) is not None
        if gone:
            bump(db, kind, account)
    return gone


def _where(prefix: str) -> tuple[str, tuple]:
    return (" AND substr(cle, 1, ?) = ?", (len(prefix), prefix)) if prefix else ("", ())


def keys(db: Database, kind: str, prefix: str = "", account: Optional[int] = None) -> list[str]:
    extra, params = _where(prefix)
    return [r[0] for r in db.all(f"SELECT cle FROM documents WHERE compte_id = ? AND type = ?{extra} ORDER BY cle",
                                 (account or db.account(), kind, *params))]


def items(db: Database, kind: str, prefix: str = "", account: Optional[int] = None) -> list[tuple[str, Any]]:
    extra, params = _where(prefix)
    return [(key, unpack(blob)) for key, blob in
            db.all(f"SELECT cle, donnees FROM documents WHERE compte_id = ? AND type = ?{extra} ORDER BY cle",
                   (account or db.account(), kind, *params))]


def count(db: Database, kinds: Optional[Iterable[str]] = None, account: Optional[int] = None) -> dict[str, int]:
    """Le nombre de documents par type."""
    rows = db.all("SELECT type, COUNT(*) FROM documents WHERE compte_id = ? GROUP BY type", (account or db.account(),))
    found = dict(rows)
    return {k: found.get(k, 0) for k in kinds} if kinds is not None else found
