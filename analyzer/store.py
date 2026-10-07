"""Cache sur disque des calculs longs et déterministes (équités, mains lues, analyses par main) : une base SQLite dans
~/.analyzer/cache (ANALYZER_HOME pour changer de dossier). Rien d'indispensable : le cache se reconstruit tout seul,
il n'est pas sauvegardé, et ANALYZER_CACHE=0 le coupe.

Chaque espace (« equite », …) a sa version (VERSIONS) : changer la façon de calculer une valeur demande d'augmenter
la version, et les anciennes valeurs sont ignorées puis effacées. Les valeurs sont gardées en JSON (texte) ou en
pickle (objets Python, pour les mains lues). Lecture : tout un espace est chargé en mémoire à la première demande ;
écriture : groupée, une transaction toutes les WRITE_BATCH valeurs, sur flush() et à la sortie.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import pickle
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Optional

VERSIONS = {"equite": 1, "mains": 1, "spots": 2, "fiches": 1}
PICKLED = {"mains"}  # espaces gardés en pickle (objets Python)
BY_KEY = {"mains"}  # espaces volumineux : lus clé par clé, pas chargés en entier
WRITE_BATCH = 500

_lock = threading.RLock()
_db: Optional[sqlite3.Connection] = None
_db_path: Optional[Path] = None
_loaded: dict[str, dict[str, Any]] = {}
_pending: list[tuple[str, str, int, bytes]] = []


def fingerprint(*paths: Path) -> str:
    """Empreinte de fichiers de code (à mettre dans les clés de ce qu'ils calculent)."""
    digest = hashlib.sha1()
    for p in paths:
        try:
            digest.update(Path(p).read_bytes())
        except OSError:
            digest.update(str(p).encode())
    return digest.hexdigest()[:12]


def enabled() -> bool:
    return os.environ.get("ANALYZER_CACHE", "1") not in ("0", "non", "off")


_PATHS: dict[str, Path] = {}


def path() -> Path:
    env = os.environ.get("ANALYZER_HOME") or ""
    found = _PATHS.get(env)
    if found is None:
        found = _PATHS[env] = (Path(env) if env else Path.home() / ".analyzer") / "cache" / "analyses.sqlite"
    return found


def _connect() -> Optional[sqlite3.Connection]:
    """La base (ouverte une fois par dossier ; ANALYZER_HOME peut changer, dans les tests)."""
    global _db, _db_path
    target = path()
    if _db is not None and _db_path == target:
        return _db
    _flush_locked()
    if _db is not None:
        _db.close()
    _loaded.clear()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("CREATE TABLE IF NOT EXISTS cache (espace TEXT, cle TEXT, version INTEGER, valeur BLOB, "
                   "PRIMARY KEY (espace, cle))")
    except (OSError, sqlite3.Error):
        _db, _db_path = None, target  # dossier en lecture seule… : on calcule sans cache
        return None
    _db, _db_path = db, target
    return db


def _decode(space: str, blob: bytes) -> Any:
    return pickle.loads(blob) if space in PICKLED else json.loads(blob)


def _encode(space: str, value: Any) -> bytes:
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL) if space in PICKLED else \
        json.dumps(value, separators=(",", ":")).encode()


def _space(space: str) -> Optional[dict[str, Any]]:
    """Toutes les valeurs à jour d'un espace, chargées une fois."""
    db = _connect()  # (vide la mémoire si le dossier a changé)
    if db is None:
        return None
    if space in _loaded:
        return _loaded[space]
    version = VERSIONS[space]
    values: dict[str, Any] = {}
    try:
        db.execute("DELETE FROM cache WHERE espace = ? AND version != ?", (space, version))
        for key, blob in db.execute("SELECT cle, valeur FROM cache WHERE espace = ?", (space,)):
            try:
                values[key] = _decode(space, blob)
            except (ValueError, pickle.UnpicklingError, EOFError, AttributeError, ImportError):
                continue  # valeur illisible (ancienne version du code) : recalculée
    except sqlite3.Error:
        return None
    _loaded[space] = values
    return values


def get(space: str, key: str) -> Any:
    """La valeur gardée, ou None."""
    if not enabled():
        return None
    with _lock:
        if space in BY_KEY:
            return _get_one(space, key)
        values = _space(space)
        return None if values is None else values.get(key)


def _get_one(space: str, key: str) -> Any:
    db = _connect()
    if db is None:
        return None
    _flush_locked()
    try:
        row = db.execute("SELECT valeur FROM cache WHERE espace = ? AND cle = ? AND version = ?",
                         (space, key, VERSIONS[space])).fetchone()
        return _decode(space, row[0]) if row else None
    except (sqlite3.Error, ValueError, pickle.UnpicklingError, EOFError, AttributeError, ImportError):
        return None


def put(space: str, key: str, value: Any) -> None:
    if not enabled():
        return
    with _lock:
        if space in BY_KEY:
            if _connect() is not None:
                _pending.append((space, key, VERSIONS[space], _encode(space, value)))
                _flush_locked()
            return
        values = _space(space)
        if values is None:
            return
        values[key] = value
        _pending.append((space, key, VERSIONS[space], _encode(space, value)))
        if len(_pending) >= WRITE_BATCH:
            _flush_locked()


def remember(space: str, key: str, compute: Callable[[], Any]) -> Any:
    """La valeur gardée, ou calculée puis gardée."""
    value = get(space, key)
    if value is None:
        value = compute()
        put(space, key, value)
    return value


def _flush_locked() -> None:
    if not _pending or _db is None:
        _pending.clear()
        return
    try:
        _db.execute("BEGIN")
        _db.executemany("INSERT OR REPLACE INTO cache (espace, cle, version, valeur) VALUES (?, ?, ?, ?)", _pending)
        _db.execute("COMMIT")
    except sqlite3.Error:
        try:
            _db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
    _pending.clear()


def close() -> None:
    """Écrit ce qui attend et ferme la base (la prochaine demande la rouvre)."""
    global _db, _db_path
    with _lock:
        _flush_locked()
        if _db is not None:
            _db.close()
        _db, _db_path = None, None
        _loaded.clear()


def flush() -> None:
    """Écrit ce qui attend (après une page, un import…)."""
    with _lock:
        _flush_locked()


def forget(space: str, keys: Optional[list[str]] = None) -> None:
    """Efface un espace, ou certaines de ses clés."""
    with _lock:
        db = _connect()
        if db is None:
            return
        _flush_locked()
        if keys is None:
            db.execute("DELETE FROM cache WHERE espace = ?", (space,))
            _loaded.pop(space, None)
        else:
            db.executemany("DELETE FROM cache WHERE espace = ? AND cle = ?", [(space, k) for k in keys])
            for k in keys:
                _loaded.get(space, {}).pop(k, None)


def retain(space: str, keep: Callable[[str], bool]) -> int:
    """Efface les valeurs d'un espace dont la clé ne passe pas `keep` (les clés d'un ancien réglage…) ; renvoie
    leur nombre."""
    with _lock:
        values = _space(space)
        if values is None:
            return 0
        drop = [k for k in values if not keep(k)]
    if drop:
        forget(space, drop)
    return len(drop)


atexit.register(flush)
