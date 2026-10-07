"""Les mains dans la base : espaces (toi, tes élèves), historiques importés et mains lues.

- import_text : un historique (texte) dans un espace ; il est gardé tel quel (compressé), ses mains nouvelles sont
  enregistrées lues (JSON) avec leurs participants ; un historique déjà importé (même contenu) ne l'est pas deux fois,
  une main déjà connue (même site et même numéro) non plus.
- sync_folder : le dossier des mains sert de boîte d'arrivée : ses fichiers nouveaux ou modifiés sont importés
  (un fichier inchangé, même taille et même date, n'est pas relu).
- load : les mains d'un espace, dans l'ordre chronologique. Si le code de lecture a changé depuis l'import, les
  historiques concernés sont relus depuis le texte gardé.
"""
from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path
from typing import Optional

from .. import store
from ..models import Hand, hand_from_dict, hand_to_dict
from . import Database, now

CHUNK = 500  # numéros de main par requête « IN (…) »


def _pack(data) -> bytes:
    return zlib.compress(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(), 6)


def _unpack(blob) -> object:
    return json.loads(zlib.decompress(bytes(blob)))


def reader_version() -> str:
    """Empreinte du code de lecture des historiques (relire quand il change)."""
    from ..parsers import _code_version
    return _code_version()


# --- Espaces -----------------------------------------------------------------------------------

def space(db: Database, key: str, name: Optional[str] = None, pseudo: Optional[str] = None,
          account: Optional[int] = None, created: Optional[str] = None) -> int:
    """L'espace (créé au besoin) : « moi », ou « eleve:<identifiant> »."""
    account = account or db.account()
    found = db.value("SELECT id FROM espaces WHERE compte_id = ? AND cle = ?", (account, key))
    if found is None:
        db.execute("INSERT INTO espaces (compte_id, cle, nom, pseudo, cree_le) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT (compte_id, cle) DO NOTHING", (account, key, name or key, pseudo, created or now()))
        found = db.value("SELECT id FROM espaces WHERE compte_id = ? AND cle = ?", (account, key))
    return found


def spaces(db: Database, prefix: str = "", account: Optional[int] = None) -> list[dict]:
    rows = db.all("SELECT id, cle, nom, pseudo, cree_le FROM espaces WHERE compte_id = ? ORDER BY cle",
                  (account or db.account(),))
    return [{"id": r[0], "key": r[1], "name": r[2], "pseudo": r[3], "created": r[4]} for r in rows
            if r[1].startswith(prefix)]


# --- Import -----------------------------------------------------------------------------------

def _known(db: Database, space_id: int, hands: list[Hand]) -> set[tuple[str, str]]:
    """Les mains (site, numéro) déjà dans l'espace, parmi celles-ci."""
    found: set[tuple[str, str]] = set()
    ids = sorted({h.hand_id for h in hands})
    for k in range(0, len(ids), CHUNK):
        part = ids[k:k + CHUNK]
        marks = ",".join("?" * len(part))
        found |= set(db.all(f"SELECT site, numero FROM mains WHERE espace_id = ? AND numero IN ({marks})",
                            [space_id] + part))
    return found


def _participants(main_id: int, h: Hand) -> list[tuple]:
    return [(main_id, name, seat.position or "", round(h.net(name), 4),
             "".join(h.hole_cards[name]) if len(h.hole_cards.get(name, [])) == 2 else None)
            for name, seat in h.seats.items()]


def _insert_hands(db: Database, space_id: int, file_id: Optional[int], hands: list[Hand], version: str) -> int:
    added = 0
    for h in hands:
        row = db.one("INSERT INTO mains (espace_id, fichier_id, site, numero, joue_le, format, sb, bb, lecture, donnees) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (espace_id, site, numero) DO NOTHING RETURNING id",
                     (space_id, file_id, h.site, h.hand_id, h.date.isoformat(), h.table_format, h.sb, h.bb, version,
                      _pack(hand_to_dict(h))))
        if row is None:
            continue
        db.executemany("INSERT INTO participants (main_id, joueur, position, net, cartes) VALUES (?, ?, ?, ?, ?)",
                       _participants(row[0], h))
        added += 1
    return added


def import_text(db: Database, space_id: int, name: str, text: str, path: Optional[str] = None,
                size: Optional[int] = None, mtime: Optional[int] = None) -> tuple[list[Hand], int, bool]:
    """Importe un historique : (ses mains, combien sont nouvelles, déjà importé ?). ValueError : format inconnu."""
    from ..parsers import parse_text
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    existing = db.one("SELECT id FROM fichiers WHERE espace_id = ? AND empreinte = ?", (space_id, digest))
    hands = parse_text(text)  # ValueError : format non reconnu
    if existing is not None:
        if path is not None:  # le même historique, retrouvé dans le dossier : on note où, pour ne plus le relire
            db.execute("UPDATE fichiers SET chemin = ?, taille = ?, date_fichier = ? WHERE id = ?",
                       (path, size, mtime, existing[0]))
        return hands, 0, True
    version = reader_version()
    with db.transaction():
        known = _known(db, space_id, hands)
        new = [h for h in hands if (h.site, h.hand_id) not in known]
        if not new and path is None:
            return hands, 0, True  # toutes ses mains sont déjà là : rien à garder
        file_id = db.one("INSERT INTO fichiers (espace_id, nom, chemin, taille, date_fichier, empreinte, contenu, mains, "
                         "importe_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                         (space_id, name[:300], path, size, mtime, digest, zlib.compress(text.encode("utf-8"), 6),
                          len(hands), now()))[0]
        added = _insert_hands(db, space_id, file_id, new, version)
    return hands, added, False


def sync_folder(db: Database, space_id: int, folder: Path) -> int:
    """Importe les historiques nouveaux ou modifiés du dossier (et de ses archives zip) ; renvoie les mains ajoutées."""
    from ..parsers import _iter_files, _texts
    if not folder.is_dir():
        return 0
    seen = {r[0]: (r[1], r[2]) for r in db.all("SELECT chemin, taille, date_fichier FROM fichiers "
                                                 "WHERE espace_id = ? AND chemin IS NOT NULL", (space_id,))}
    added = 0
    for file in _iter_files([folder]):
        try:
            stat = file.stat()
        except OSError:
            continue
        key = str(file.resolve())
        stamp = (stat.st_size, stat.st_mtime_ns)
        inner = key + "!" if file.suffix.lower() == ".zip" else None
        if seen.get(key) == stamp or (inner and any(p.startswith(inner) and s == stamp for p, s in seen.items())):
            continue
        for k, text in enumerate(_texts(file)):
            path = f"{inner}{k}" if inner else key
            try:
                _, new, _ = import_text(db, space_id, file.name, text, path, *stamp)
            except ValueError:
                continue
            added += new
    return added


# --- Lecture -----------------------------------------------------------------------------------

def count(db: Database, space_id: int) -> int:
    return db.value("SELECT COUNT(*) FROM mains WHERE espace_id = ?", (space_id,), 0)


def _reread_outdated(db: Database, space_id: int, version: str) -> None:
    """Relit les historiques dont les mains ont été lues par un autre code (parseur corrigé…)."""
    from ..parsers import parse_text
    files = [r[0] for r in db.all("SELECT DISTINCT fichier_id FROM mains WHERE espace_id = ? AND lecture != ? "
                                  "AND fichier_id IS NOT NULL", (space_id, version))]
    for file_id in files:
        blob = db.value("SELECT contenu FROM fichiers WHERE id = ?", (file_id,))
        try:
            hands = parse_text(zlib.decompress(bytes(blob)).decode("utf-8", "replace"))
        except (ValueError, zlib.error):
            continue
        with db.transaction():
            for h in hands:
                row = db.one("SELECT id FROM mains WHERE espace_id = ? AND site = ? AND numero = ?",
                             (space_id, h.site, h.hand_id))
                if row is None:
                    continue
                db.execute("UPDATE mains SET lecture = ?, donnees = ?, joue_le = ?, format = ?, sb = ?, bb = ? "
                           "WHERE id = ?", (version, _pack(hand_to_dict(h)), h.date.isoformat(), h.table_format,
                                            h.sb, h.bb, row[0]))
                db.execute("DELETE FROM participants WHERE main_id = ?", (row[0],))
                db.executemany("INSERT INTO participants (main_id, joueur, position, net, cartes) VALUES (?, ?, ?, ?, ?)",
                               _participants(row[0], h))


def load(db: Database, space_id: int) -> list[Hand]:
    """Les mains de l'espace, dans l'ordre chronologique (gardées aussi dans le cache des calculs, tant que l'espace
    ne change pas)."""
    version = reader_version()
    if db.value("SELECT 1 FROM mains WHERE espace_id = ? AND lecture != ? LIMIT 1", (space_id, version)):
        _reread_outdated(db, space_id, version)
    n, last = db.one("SELECT COUNT(*), MAX(id) FROM mains WHERE espace_id = ?", (space_id,))
    if not n:
        return []
    key = f"{db.url}|{space_id}|{n}|{last}|{version}"
    cached = store.get("mains", key)
    if cached is not None:
        return cached[1]
    hands = [hand_from_dict(_unpack(blob)) for (blob,) in
             db.all("SELECT donnees FROM mains WHERE espace_id = ?", (space_id,))]
    hands.sort(key=lambda h: (h.date, h.hand_id))
    store.forget_prefix("mains", f"{db.url}|{space_id}|")  # l'ancienne version de l'espace
    store.put("mains", key, (key, hands))
    return hands
