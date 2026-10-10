"""Les mains dans la base : espaces (toi, tes élèves), historiques importés et mains lues.

- import_text : un historique (texte) dans un espace ; il est gardé tel quel (compressé), ses mains nouvelles sont
  enregistrées lues (JSON) avec leurs participants ; un historique déjà importé (même contenu) ne l'est pas deux fois,
  une main déjà connue (même site et même numéro) non plus.
- sync_folder : le dossier des mains sert de boîte d'arrivée : ses fichiers nouveaux ou modifiés sont importés
  (un fichier inchangé, même taille et même date, n'est pas relu).
- load : les mains d'un espace, dans l'ordre chronologique. Si le code de lecture a changé depuis l'import, les
  historiques concernés sont relus depuis le texte gardé. Les mains lues sont aussi gardées dans le cache des calculs,
  par blocs de numéros de ligne (CACHE_BLOCK) : relire un bloc gardé est cinq fois plus rapide que le JSON de la base,
  et un import ne refait que le dernier bloc.
- files, remove_file, restore_file : les historiques importés ; en retirer un efface ses mains (celles qu'un autre
  historique contient aussi restent) et le garde, marqué retiré : le dossier des mains ne le réimporte pas, et il se
  rétablit d'un clic (ou en l'important à nouveau).
- removed_pseudos, remove_pseudos, restore_pseudo : supprimer les mains d'un pseudo, celles dont l'historique le marque
  comme héros (les mains importées de son compte ; pas celles où il est un joueur de la table) ; il reste noté, avec
  les historiques qui les contiennent : les imports suivants écartent ses mains, et il se rétablit d'un clic.
"""
from __future__ import annotations

import hashlib
import json
import zlib
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .. import store
from ..models import Hand, hand_from_dict, hand_to_dict
from . import Database, now

CHUNK = 500  # numéros de main par requête « IN (…) »
CACHE_BLOCK = 5000  # lignes de la table des mains par bloc du cache des mains lues (selon leur numéro de ligne)


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
                size: Optional[int] = None, mtime: Optional[int] = None,
                on_hands: Optional[Callable[[int], None]] = None) -> tuple[list[Hand], int, bool]:
    """Importe un historique : (ses mains, combien sont nouvelles, déjà importé ?). ValueError : format inconnu.
    on_hands(n) : n mains lues (l'avancement d'un import)."""
    from ..parsers import parse_text
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    existing = db.one("SELECT id, retire_le FROM fichiers WHERE espace_id = ? AND empreinte = ?", (space_id, digest))
    hands = parse_text(text, on_hands)  # ValueError : format non reconnu
    if existing is not None and existing[1] is not None and path is None:  # retiré, puis importé à nouveau
        return hands, restore_file(db, space_id, existing[0]), False
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
        new = _set_aside(db, space_id, file_id, new)  # (gardé quand même : de quoi rétablir un pseudo supprimé)
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


# --- Historiques importés : liste, retrait, rétablissement --------------------------------------

def files(db: Database, space_id: int) -> list[dict]:
    """Les historiques de l'espace, du plus récent au plus ancien : nom, date d'import, mains dans la base (celles
    qu'il a apportées), période, sites et formats, retiré ou non, venu du dossier des mains ou importé."""
    rows = db.all("SELECT id, nom, chemin, mains, importe_le, retire_le FROM fichiers WHERE espace_id = ? "
                  "ORDER BY id DESC", (space_id,))
    stats: dict[int, dict] = {}
    for file_id, site, fmt, n, first, last in db.all(
            "SELECT fichier_id, site, format, COUNT(*), MIN(joue_le), MAX(joue_le) FROM mains WHERE espace_id = ? "
            "AND fichier_id IS NOT NULL GROUP BY fichier_id, site, format", (space_id,)):
        st = stats.setdefault(file_id, {"hands": 0, "sites": set(), "formats": {}, "first": first, "last": last})
        st["hands"] += n
        st["sites"].add(site)
        st["formats"][fmt] = st["formats"].get(fmt, 0) + n
        st["first"], st["last"] = min(st["first"], first), max(st["last"], last)
    out = []
    for file_id, name, path, total, imported, removed in rows:
        st = stats.get(file_id, {"hands": 0, "sites": set(), "formats": {}, "first": None, "last": None})
        out.append({"id": file_id, "name": name, "inbox": path is not None, "total": total, "hands": st["hands"],
                    "sites": sorted(st["sites"]), "formats": st["formats"], "first": st["first"], "last": st["last"],
                    "imported": imported, "removed": removed})
    return out


def _hands_of(text: str) -> list[Hand]:
    from ..parsers import parse_text
    try:
        return parse_text(text)
    except ValueError:
        return []


def _text(db: Database, file_id: int) -> str:
    blob = db.value("SELECT contenu FROM fichiers WHERE id = ?", (file_id,))
    return zlib.decompress(bytes(blob)).decode("utf-8", "replace") if blob is not None else ""


def remove_file(db: Database, space_id: int, file_id: int) -> dict:
    """Retire un historique : ses mains quittent la base, sauf celles qu'un autre historique (importé après lui, et
    pas retiré) contient aussi ; il reste, marqué retiré. KeyError s'il n'est pas de cet espace."""
    if db.value("SELECT 1 FROM fichiers WHERE id = ? AND espace_id = ?", (file_id, space_id)) is None:
        raise KeyError(file_id)
    version = reader_version()
    with db.transaction():
        gone = {(site, numero) for site, numero in
                db.all("SELECT site, numero FROM mains WHERE fichier_id = ?", (file_id,))}
        total = len(gone)
        db.execute("DELETE FROM mains WHERE fichier_id = ?", (file_id,))
        db.execute("UPDATE fichiers SET retire_le = ? WHERE id = ?", (now(), file_id))
        kept = 0
        later = [r[0] for r in db.all("SELECT id FROM fichiers WHERE espace_id = ? AND id > ? AND retire_le IS NULL "
                                      "ORDER BY id", (space_id, file_id))]
        removed = removed_pseudos(db, space_id)
        for other in later:
            if not gone:
                break
            again = [h for h in _hands_of(_text(db, other)) if (h.site, h.hand_id) in gone and h.hero not in removed]
            kept += _insert_hands(db, space_id, other, again, version)
            gone -= {(h.site, h.hand_id) for h in again}
    return {"removed": total - kept, "kept": kept}


def restore_file(db: Database, space_id: int, file_id: int) -> int:
    """Rétablit un historique retiré : ses mains qui manquent reviennent ; renvoie leur nombre."""
    if db.value("SELECT 1 FROM fichiers WHERE id = ? AND espace_id = ?", (file_id, space_id)) is None:
        raise KeyError(file_id)
    hands = _hands_of(_text(db, file_id))
    version = reader_version()
    with db.transaction():
        known = _known(db, space_id, hands)
        new = _set_aside(db, space_id, file_id, [h for h in hands if (h.site, h.hand_id) not in known])
        added = _insert_hands(db, space_id, file_id, new, version)
        db.execute("UPDATE fichiers SET retire_le = NULL WHERE id = ?", (file_id,))
    return added


# --- Pseudos supprimés --------------------------------------------------------------------------

def hero_of(data: dict) -> Optional[str]:
    """Le héros marqué d'une main gardée (models.hand_to_dict), sans la reconstruire."""
    for name, _seat, _stack, _button, hero, _position in data["seats"]:
        if hero:
            return name
    return None


def removed_pseudos(db: Database, space_id: int) -> dict[str, dict]:
    """Les pseudos supprimés de l'espace : {pseudo: {"hands": mains effacées ou écartées, "files": les historiques
    gardés qui les contiennent, "removed": date}}."""
    return {name: {"hands": n, "files": json.loads(files), "removed": when} for name, n, files, when in db.all(
        "SELECT pseudo, mains, fichiers, retire_le FROM pseudos_retires WHERE espace_id = ? ORDER BY pseudo",
        (space_id,))}


def _note(db: Database, space_id: int, name: str, hands: int, files: set[int], when: Optional[str] = None) -> None:
    """Note un pseudo supprimé (ou complète sa note) : ses mains et les historiques qui les contiennent."""
    found = db.one("SELECT mains, fichiers FROM pseudos_retires WHERE espace_id = ? AND pseudo = ?", (space_id, name))
    if found is None:
        db.execute("INSERT INTO pseudos_retires (espace_id, pseudo, mains, fichiers, retire_le) VALUES (?, ?, ?, ?, ?)",
                   (space_id, name, hands, json.dumps(sorted(files)), when or now()))
    else:
        db.execute("UPDATE pseudos_retires SET mains = ?, fichiers = ? WHERE espace_id = ? AND pseudo = ?",
                   (found[0] + hands, json.dumps(sorted(set(json.loads(found[1])) | files)), space_id, name))


def _set_aside(db: Database, space_id: int, file_id: int, hands: list[Hand]) -> list[Hand]:
    """Écarte les mains des pseudos supprimés (l'historique est noté avec eux, pour les rétablir) ; renvoie les
    autres."""
    removed = removed_pseudos(db, space_id)
    if not removed:
        return hands
    kept, aside = [], Counter()
    for h in hands:
        hero = h.hero
        if hero in removed:
            aside[hero] += 1
        else:
            kept.append(h)
    for name, n in aside.items():
        if file_id not in removed[name]["files"]:  # (un historique déjà noté : ses mains déjà comptées)
            _note(db, space_id, name, n, {file_id})
    return kept


def _marked(db: Database, space_id: int, names: set[str]) -> list[tuple]:
    """Les mains de l'espace dont le héros marqué est un de ces pseudos : (ligne, historique, site, numéro, date, héros,
    joueurs) ; seules celles où l'un d'eux joue sont relues (table des participants)."""
    out = []
    marks = ",".join("?" * len(names))
    for main_id, file_id, site, numero, played, blob in db.all(
            f"SELECT id, fichier_id, site, numero, joue_le, donnees FROM mains WHERE espace_id = ? AND id IN "
            f"(SELECT main_id FROM participants WHERE joueur IN ({marks}))", [space_id, *sorted(names)]):
        data = _unpack(blob)
        hero = hero_of(data)
        if hero in names:
            out.append((main_id, file_id, site, numero, played, hero, {seat[0] for seat in data["seats"]}))
    return out


def remove_pseudos(db: Database, space_id: int, names: set[str], keep: set[str] = frozenset()) -> dict:
    """Supprime les mains dont l'historique marque un de ces pseudos comme héros (pas celles où il n'est qu'un joueur
    de la table). Une de ces mains qu'un autre historique gardé contient aussi, du point de vue d'un autre héros (keep :
    les pseudos héros de l'espace qu'on garde, assis à cette main), revient sous ce point de vue. Les pseudos restent
    notés : les imports suivants écartent leurs mains, et restore_pseudo les remet. Renvoie {"removed": mains sorties
    de la base, "kept": revenues sous un autre point de vue, "pseudos": {pseudo: ses mains}} ; ValueError si aucune
    main n'est à eux."""
    names = set(names)
    found = _marked(db, space_id, names) if names else []
    if not found:
        raise ValueError("Aucune main importée de ce pseudo : rien à supprimer.")
    counts: Counter = Counter()
    files: dict[str, set[int]] = {}
    gone: dict[tuple[str, str], str] = {}  # (site, numéro) -> date, des mains qu'un autre point de vue peut rendre
    for _, file_id, site, numero, played, hero, seated in found:
        counts[hero] += 1
        files.setdefault(hero, set()).update({file_id} if file_id is not None else set())
        if (seated - names) & keep:
            gone[(site, numero)] = played
    ids = [row[0] for row in found]
    when = now()
    with db.transaction():
        for k in range(0, len(ids), CHUNK):
            part = ids[k:k + CHUNK]
            db.execute(f"DELETE FROM mains WHERE id IN ({','.join('?' * len(part))})", part)
        for name, n in counts.items():
            _note(db, space_id, name, n, files[name], when)
        kept = _other_views(db, space_id, gone)
    return {"removed": len(ids) - kept, "kept": kept, "pseudos": dict(counts)}


VIEW_MARGIN = timedelta(hours=12)  # autour des dates d'un historique : ses mains déjà là chez quelqu'un d'autre


def _other_views(db: Database, space_id: int, gone: dict[tuple[str, str], str]) -> int:
    """Remet les mains effacées que contient un autre historique gardé (pas retiré), du point de vue d'un héros qui
    n'est pas supprimé ; seuls les historiques joués autour de leurs dates sont relus. Renvoie leur nombre."""
    if not gone:
        return 0
    def when(iso: str) -> datetime:  # (sans fuseau : des sites différents peuvent en donner un ou non)
        return datetime.fromisoformat(iso).replace(tzinfo=None)

    dates = sorted({when(d) for d in gone.values()})
    candidates = []
    for file_id, first, last in db.all(
            "SELECT m.fichier_id, MIN(m.joue_le), MAX(m.joue_le) FROM mains m JOIN fichiers f ON f.id = m.fichier_id "
            "WHERE m.espace_id = ? AND f.retire_le IS NULL GROUP BY m.fichier_id", (space_id,)):
        k = bisect_left(dates, when(first) - VIEW_MARGIN)
        if k < len(dates) and dates[k] <= when(last) + VIEW_MARGIN:
            candidates.append(file_id)
    removed = removed_pseudos(db, space_id)
    version = reader_version()
    wanted, added = set(gone), 0
    for file_id in candidates:
        if not wanted:
            break
        again = [h for h in _hands_of(_text(db, file_id)) if (h.site, h.hand_id) in wanted and h.hero not in removed]
        added += _insert_hands(db, space_id, file_id, again, version)
        wanted -= {(h.site, h.hand_id) for h in again}
    return added


def stored_hero(blob) -> Optional[str]:
    """Le héros marqué d'une main telle que la table des mains la garde (donnees)."""
    return hero_of(_unpack(blob))


def adopt_removed(db: Database, space_id: int, records: dict[str, dict], files: dict) -> set[str]:
    """Fusion d'une autre base : ses pseudos supprimés (records, removed_pseudos de son espace ; files : ses numéros
    d'historiques -> ceux d'ici) rejoignent ceux d'ici, sauf un pseudo dont cet espace a des mains (rien n'est effacé
    par une fusion). Renvoie les pseudos supprimés ici : les mains de la source qui sont à eux ne viennent pas."""
    here = removed_pseudos(db, space_id)
    for name, record in records.items():
        mapped = {files[f] for f in record["files"] if files.get(f) is not None}
        if name in here:
            _note(db, space_id, name, 0, mapped)
        elif not _marked(db, space_id, {name}):
            _note(db, space_id, name, record["hands"], mapped, record["removed"])
            here[name] = record
    return set(here)


def restore_pseudo(db: Database, space_id: int, name: str) -> int:
    """Rétablit un pseudo supprimé : ses mains qui manquent reviennent depuis les historiques gardés (pas retirés), et
    les imports suivants les gardent ; renvoie leur nombre. KeyError s'il n'est pas supprimé dans cet espace."""
    record = removed_pseudos(db, space_id).get(name)
    if record is None:
        raise KeyError(name)
    version = reader_version()
    added = 0
    with db.transaction():
        db.execute("DELETE FROM pseudos_retires WHERE espace_id = ? AND pseudo = ?", (space_id, name))
        for file_id in record["files"]:
            if db.value("SELECT 1 FROM fichiers WHERE id = ? AND espace_id = ? AND retire_le IS NULL",
                        (file_id, space_id)) is None:
                continue  # retiré depuis (ou d'un autre espace) : ses mains restent dehors
            hands = [h for h in _hands_of(_text(db, file_id)) if h.hero == name]
            known = _known(db, space_id, hands)
            added += _insert_hands(db, space_id, file_id, [h for h in hands if (h.site, h.hand_id) not in known],
                                   version)
    return added


# --- Lecture -----------------------------------------------------------------------------------

def count(db: Database, space_id: int) -> int:
    return db.value("SELECT COUNT(*) FROM mains WHERE espace_id = ?", (space_id,), 0)


def outdated(db: Database, key: str = "moi") -> int:
    """Les mains de l'espace lues par un autre code de lecture : relues à son prochain chargement."""
    found = db.value("SELECT id FROM espaces WHERE compte_id = ? AND cle = ?", (db.account(), key))
    if found is None:
        return 0
    return db.value("SELECT COUNT(*) FROM mains WHERE espace_id = ? AND lecture != ?", (found, reader_version()), 0)


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


def rows(db: Database, space_id: int) -> tuple[int, int]:
    """(nombre de mains de l'espace, dernier numéro de ligne) : de quoi relire ensuite seulement les mains ajoutées."""
    found = db.one("SELECT COUNT(*), MAX(id) FROM mains WHERE espace_id = ?", (space_id,))
    return int(found[0] or 0), int(found[1] or 0)


def load_after(db: Database, space_id: int, seen: tuple[int, int]) -> Optional[tuple[list[Hand], tuple[int, int]]]:
    """Les mains ajoutées depuis un chargement (seen : rows à ce moment), dans l'ordre d'ajout, et le nouveau rows ;
    None quand il faut tout relire (des mains retirées depuis, ou lues par un autre code de lecture)."""
    count, last = seen
    if db.value("SELECT 1 FROM mains WHERE espace_id = ? AND lecture != ? AND fichier_id IS NOT NULL LIMIT 1",
                (space_id, reader_version())):  # à relire depuis leur historique (comme le fait load)
        return None
    if db.value("SELECT COUNT(*) FROM mains WHERE espace_id = ? AND id <= ?", (space_id, last), 0) != count:
        return None
    found = db.all("SELECT id, donnees FROM mains WHERE espace_id = ? AND id > ? ORDER BY id", (space_id, last))
    hands = [hand_from_dict(_unpack(blob)) for _, blob in found]
    return hands, (count + len(found), found[-1][0] if found else last)


def load(db: Database, space_id: int, progress: Optional[Callable[[int, int], None]] = None) -> list[Hand]:
    """Les mains de l'espace, dans l'ordre chronologique ; progress(k, n) : k mains relues sur n, à chaque bloc.

    Bloc par bloc (CACHE_BLOCK lignes de la table, selon leur numéro) : celui que le cache des calculs garde, pour ce
    contenu (nombre de mains, premier et dernier numéro) et ce code de lecture, se relit de là ; les autres, depuis la
    base, puis sont gardés. Les blocs d'avant (mains retirées, ajoutées) sont effacés du cache."""
    version = reader_version()
    if db.value("SELECT 1 FROM mains WHERE espace_id = ? AND lecture != ? LIMIT 1", (space_id, version)):
        _reread_outdated(db, space_id, version)
    blocks = db.all(f"SELECT id / {CACHE_BLOCK} AS bloc, COUNT(*), MIN(id), MAX(id) FROM mains WHERE espace_id = ? "
                    "GROUP BY bloc ORDER BY bloc", (space_id,))
    prefix = f"{db.url}|{space_id}|"
    total = sum(n for _, n, _, _ in blocks)
    hands: list[Hand] = []
    keys: set[str] = set()
    for block, n, first, last in blocks:
        key = f"{prefix}{block}|{n}|{first}|{last}|{version}|{_MODEL}"
        keys.add(key)
        part = store.get("mains", key)
        if part is None:
            rows = db.all("SELECT donnees FROM mains WHERE espace_id = ? AND id BETWEEN ? AND ?",
                          (space_id, first, last))
            part = [hand_from_dict(_unpack(blob)) for (blob,) in rows]
            store.put("mains", key, part)
        hands.extend(part)
        if progress is not None:
            progress(len(hands), total)
    store.keep_only("mains", prefix, keys)  # les blocs d'un contenu d'avant
    hands.sort(key=lambda h: (h.date, h.hand_id))
    return hands


_MODEL = store.fingerprint(Path(__file__).parent.parent / "models.py")  # la forme des objets gardés dans le cache
