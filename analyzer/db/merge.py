"""Fusion d'une base dans une autre (restauration d'une sauvegarde, reprise de tes données locales dans un compte en
ligne) : rien n'est effacé ni remplacé par plus ancien.

- espaces, historiques et mains : ajoutés s'ils manquent (même clé d'espace, même empreinte d'historique, même
  site et même numéro de main) ;
- type des adversaires, résumés du solveur, documents (tailles, plans, résultats…) et fiches d'études : ajoutés,
  ou remplacés par ceux de la source s'ils sont plus récents ;
- réglages : ajoutés s'ils manquent ; journal de l'entraîneur : les décisions qui manquent.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import Database, training
from .documents import bump
from .hands import space as make_space

BATCH = 500


def _blob(value):
    return bytes(value) if isinstance(value, memoryview) else value


def _newer(target: Database, table: str, keys: tuple[str, ...], columns: tuple[str, ...], rows: list[tuple]) -> int:
    """Insère les lignes, ou remplace celles de la cible plus anciennes (colonne maj_le) ; renvoie leur nombre."""
    names = keys + columns + ("maj_le",)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns + ("maj_le",))
    sql = (f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))}) "
           f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates} WHERE {table}.maj_le < excluded.maj_le "
           f"RETURNING 1")
    changed = 0
    for row in rows:
        changed += target.one(sql, tuple(_blob(v) for v in row)) is not None
    return changed


def merge(source: Database, target: Database, log: Callable[[str], None] = print,
          account: Optional[str] = None) -> dict[str, int]:
    """Fusionne source dans target ; account : la clé du compte de target qui reçoit tout (sinon chaque compte de la
    source va dans le compte de même clé)."""
    out = {"mains": 0, "historiques": 0, "adversaires": 0, "analyses": 0, "documents": 0, "etudes": 0,
           "entrainement": 0, "reglages": 0}
    with target.transaction():
        for src_account, key in source.all("SELECT id, cle FROM comptes ORDER BY id"):
            dst_account = target.account(account or key)
            _hands(source, target, src_account, dst_account, out)
            out["adversaires"] += _newer(target, "adversaires", ("compte_id", "nom"), ("type",), [
                (dst_account, n, t, m) for n, t, m in
                source.all("SELECT nom, type, maj_le FROM adversaires WHERE compte_id = ?", (src_account,))])
            out["analyses"] += _newer(target, "analyses", ("compte_id", "cle"), ("main", "donnees"), [
                (dst_account, *r) for r in
                source.all("SELECT cle, main, donnees, maj_le FROM analyses WHERE compte_id = ?", (src_account,))])
            kinds = set()
            for kind, key_, data, when in source.all("SELECT type, cle, donnees, maj_le FROM documents "
                                                     "WHERE compte_id = ?", (src_account,)):
                if _newer(target, "documents", ("compte_id", "type", "cle"), ("donnees",),
                          [(dst_account, kind, key_, data, when)]):
                    out["documents"] += 1
                    kinds.add(kind)
            studies = _newer(target, "etudes", ("compte_id", "cle"), ("type", "fiche", "taille"), [
                (dst_account, *r) for r in
                source.all("SELECT cle, type, fiche, taille, maj_le FROM etudes WHERE compte_id = ?", (src_account,))])
            out["etudes"] += studies
            for kind in kinds | ({"etudes"} if studies else set()):
                bump(target, kind, dst_account)
            for key_, value in source.all("SELECT cle, valeur FROM reglages WHERE compte_id = ?", (src_account,)):
                if target.one("INSERT INTO reglages (compte_id, cle, valeur) VALUES (?, ?, ?) "
                              "ON CONFLICT (compte_id, cle) DO NOTHING RETURNING 1", (dst_account, key_, value)):
                    out["reglages"] += 1
            out["entrainement"] += training.add(target, training.every(source, src_account), dst_account,
                                                skip_known=True)
    log(", ".join(f"{k} : {v}" for k, v in out.items()))
    return out


def _hands(source: Database, target: Database, src_account: int, dst_account: int, out: dict) -> None:
    for src_space, key, name, pseudo, created in source.all(
            "SELECT id, cle, nom, pseudo, cree_le FROM espaces WHERE compte_id = ? ORDER BY id", (src_account,)):
        dst_space = make_space(target, key, name, pseudo, dst_account, created)
        files = {None: None}
        retired = set()  # historiques retirés ici (ou dans la source) : leurs mains ne reviennent pas
        for row in source.all("SELECT id, nom, chemin, taille, date_fichier, empreinte, contenu, mains, importe_le, "
                              "retire_le FROM fichiers WHERE espace_id = ?", (src_space,)):
            found = target.one("SELECT id, retire_le FROM fichiers WHERE espace_id = ? AND empreinte = ?",
                               (dst_space, row[5]))
            if found is None:
                found = target.one("INSERT INTO fichiers (espace_id, nom, chemin, taille, date_fichier, empreinte, "
                                   "contenu, mains, importe_le, retire_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                                   "RETURNING id, retire_le", (dst_space, *(_blob(v) for v in row[1:])))
                out["historiques"] += 1
            files[row[0]] = found[0]
            if found[1] is not None:
                retired.add(row[0])
        cur = source.execute("SELECT id, fichier_id, site, numero, joue_le, format, sb, bb, lecture, donnees "
                             "FROM mains WHERE espace_id = ? ORDER BY id", (src_space,))
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            for row in rows:
                if row[1] in retired:
                    continue
                new = target.value("INSERT INTO mains (espace_id, fichier_id, site, numero, joue_le, format, sb, bb, "
                                   "lecture, donnees) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                                   "ON CONFLICT (espace_id, site, numero) DO NOTHING RETURNING id",
                                   (dst_space, files.get(row[1]), *(_blob(v) for v in row[2:])))
                if new is None:
                    continue
                target.executemany("INSERT INTO participants (main_id, joueur, position, net, cartes) "
                                   "VALUES (?, ?, ?, ?, ?)",
                                   [(new, *r) for r in source.all("SELECT joueur, position, net, cartes FROM "
                                                                  "participants WHERE main_id = ?", (row[0],))])
                out["mains"] += 1
