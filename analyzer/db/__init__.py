"""La base de données d'Analyzer : tes mains et celles de tes élèves (avec leurs historiques d'origine), le type de
tes adversaires et les résumés des mains passées au solveur.

- SQLite par défaut : ~/.analyzer/analyzer.db (ANALYZER_HOME pour changer de dossier), rien à installer.
- PostgreSQL pour la version en ligne : ANALYZER_DB=postgresql://utilisateur:motdepasse@hôte:5432/base
  (pip install "psycopg[binary]"). ANALYZER_DB peut aussi désigner un autre fichier SQLite (sqlite:///chemin).

Le même code sert aux deux : les requêtes s'écrivent avec des « ? » (convertis pour PostgreSQL) et la syntaxe
commune (ON CONFLICT, RETURNING). Le schéma évolue par migrations numérotées (schema.py), appliquées à l'ouverture.
Les études du solveur (fichiers .etude, jusqu'à quelques centaines de Mo) restent des fichiers.

Tout appartient à un compte (« local » sur ton ordinateur ; un client, en ligne) ; un compte a ses espaces (toi,
tes élèves), chacun avec ses historiques et ses mains.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

LOCAL_ACCOUNT = "local"


class DatabaseError(RuntimeError):
    pass


def home() -> Path:
    return Path(os.environ.get("ANALYZER_HOME") or Path.home() / ".analyzer")


def default_url() -> str:
    """ANALYZER_DB, sinon le fichier SQLite du dossier d'Analyzer."""
    return os.environ.get("ANALYZER_DB") or f"sqlite:///{home() / 'analyzer.db'}"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Une base (SQLite ou PostgreSQL), une connexion par fil d'exécution. Les requêtes s'écrivent avec des « ? »."""

    def __init__(self, url: str):
        self.url = url
        if url.startswith(("postgresql://", "postgres://")):
            self.dialect = "postgres"
            self.path: Optional[Path] = None
        elif url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self.path = Path(url[len("sqlite:///"):])
        else:
            raise DatabaseError(f"Adresse de base inconnue : {url} (sqlite:///chemin ou postgresql://…)")
        self._local = threading.local()
        self._connections: list[Any] = []
        self._lock = threading.Lock()
        from .schema import migrate
        migrate(self)

    # --- connexions -------------------------------------------------------------------------
    def connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn, self._local.depth = conn, 0
            with self._lock:
                self._connections.append(conn)
        return conn

    def _open(self):
        if self.dialect == "sqlite":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        try:
            import psycopg
        except ImportError as exc:
            raise DatabaseError('PostgreSQL : installe le pilote (pip install "psycopg[binary]").') from exc
        return psycopg.connect(self.url.replace("postgres://", "postgresql://", 1), autocommit=True)

    def close(self) -> None:
        """Ferme toutes les connexions (elles se rouvrent à la prochaine requête)."""
        with self._lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — une connexion déjà perdue
                pass
        self._local = threading.local()

    # --- requêtes ---------------------------------------------------------------------------
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params: Iterable = ()) -> Any:
        cur = self.connection().cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def executemany(self, sql: str, rows: Iterable[Iterable]) -> None:
        rows = [tuple(r) for r in rows]
        if rows:
            self.connection().cursor().executemany(self._sql(sql), rows)

    def all(self, sql: str, params: Iterable = ()) -> list[tuple]:
        return [tuple(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Iterable = ()) -> Optional[tuple]:
        rows = self.execute(sql, params).fetchall()  # tout lire : la requête est finie (INSERT … RETURNING)
        return tuple(rows[0]) if rows else None

    def value(self, sql: str, params: Iterable = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None and row[0] is not None else default

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Tout ou rien (les transactions imbriquées font partie de la plus extérieure)."""
        conn = self.connection()
        if self._local.depth:
            self._local.depth += 1
            try:
                yield self
            finally:
                self._local.depth -= 1
            return
        self._local.depth = 1
        try:
            if self.dialect == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            else:
                with conn.transaction():
                    yield self
        finally:
            self._local.depth = 0

    # --- comptes et réglages ----------------------------------------------------------------
    def account(self, key: str = LOCAL_ACCOUNT) -> int:
        """Le compte (créé au besoin) : « local » pour l'application sur ton ordinateur."""
        found = self.value("SELECT id FROM comptes WHERE cle = ?", (key,))
        if found is None:
            self.execute("INSERT INTO comptes (cle, nom, cree_le) VALUES (?, ?, ?) ON CONFLICT (cle) DO NOTHING",
                         (key, "Moi" if key == LOCAL_ACCOUNT else key, now()))
            found = self.value("SELECT id FROM comptes WHERE cle = ?", (key,))
        return found

    def setting(self, key: str, default: Any = None, account: Optional[int] = None) -> Any:
        import json
        raw = self.value("SELECT valeur FROM reglages WHERE compte_id = ? AND cle = ?",
                         (account or self.account(), key))
        return json.loads(raw) if raw is not None else default

    def set_setting(self, key: str, value: Any, account: Optional[int] = None) -> None:
        import json
        self.execute("INSERT INTO reglages (compte_id, cle, valeur) VALUES (?, ?, ?) "
                     "ON CONFLICT (compte_id, cle) DO UPDATE SET valeur = excluded.valeur",
                     (account or self.account(), key, json.dumps(value, ensure_ascii=False)))

    # --- sauvegarde --------------------------------------------------------------------------
    def snapshot(self, target: Path) -> Optional[Path]:
        """Copie cohérente de la base SQLite (pour l'archive de sauvegarde), même pendant qu'elle sert ; None pour
        PostgreSQL (sauvegardé par son hébergeur)."""
        if self.dialect != "sqlite":
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        dest = sqlite3.connect(target)
        try:
            self.connection().backup(dest)
        finally:
            dest.close()
        return target


_DATABASES: dict[str, Database] = {}
_OPEN = threading.Lock()


def current() -> Database:
    """La base de l'application (ANALYZER_DB, sinon ~/.analyzer/analyzer.db), ouverte une fois par adresse."""
    url = default_url()
    db = _DATABASES.get(url)
    if db is None:
        with _OPEN:
            db = _DATABASES.get(url)
            if db is None:
                for other, old in list(_DATABASES.items()):  # bases d'un dossier effacé (tests) : refermées
                    if old.path is not None and not old.path.parent.exists():
                        old.close()
                        del _DATABASES[other]
                db = Database(url)
                from .legacy import import_once
                import_once(db)  # les fichiers d'avant la base, une seule fois
                _DATABASES[url] = db
    return db


def close_all() -> None:
    with _OPEN:
        dbs = list(_DATABASES.values())
        _DATABASES.clear()
    for db in dbs:
        db.close()
