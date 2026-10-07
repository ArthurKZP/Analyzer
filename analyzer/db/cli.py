"""python -m analyzer base : l'état de la base, l'import d'un dossier d'historiques, la copie vers une autre base
(passage de SQLite à PostgreSQL, ou l'inverse), la fusion d'une autre base dans celle-ci."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from . import Database, DatabaseError, current, now
from . import hands as db_hands
from .schema import IDENTITY, TABLES, version

BATCH = 1000


def masked(url: str) -> str:
    """L'adresse sans son mot de passe."""
    return re.sub(r"(://[^:/@]+):[^@]+@", r"\1:***@", url)


def status(db: Database) -> dict:
    spaces = []
    for account_id, key, name in db.all("SELECT id, cle, nom FROM comptes ORDER BY id"):
        for space in db_hands.spaces(db, account=account_id):
            spaces.append(dict(space, account=key, hands=db_hands.count(db, space["id"]),
                               files=db.value("SELECT COUNT(*) FROM fichiers WHERE espace_id = ?", (space["id"],), 0)))
    return {"url": masked(db.url), "dialect": db.dialect, "schema": version(db), "spaces": spaces,
            "opponents": db.value("SELECT COUNT(*) FROM adversaires", default=0),
            "analyses": db.value("SELECT COUNT(*) FROM analyses", default=0),
            "documents": dict(db.all("SELECT type, COUNT(*) FROM documents GROUP BY type ORDER BY type")),
            "studies": db.value("SELECT COUNT(*) FROM etudes", default=0),
            "training": db.value("SELECT COUNT(*) FROM entrainement", default=0)}


def copy(source: Database, target: Database, log=print) -> dict[str, int]:
    """Recopie toutes les tables dans une base vide (même schéma : les migrations s'appliquent à l'ouverture)."""
    if target.value("SELECT COUNT(*) FROM comptes", default=0):
        raise DatabaseError("La base de destination n'est pas vide : la copie ne remplace rien.")
    if version(target) != version(source):
        raise DatabaseError("Les deux bases n'ont pas la même version du schéma.")
    counts = {}
    with target.transaction():
        for table in TABLES:
            cur = source.execute(f"SELECT * FROM {table}")
            columns = [d[0] for d in cur.description]
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
            n = 0
            while True:
                rows = cur.fetchmany(BATCH)
                if not rows:
                    break
                target.executemany(sql, [tuple(bytes(v) if isinstance(v, memoryview) else v for v in r) for r in rows])
                n += len(rows)
            counts[table] = n
            log(f"{table} : {n} ligne(s)")
        if target.dialect == "postgres":  # les clés automatiques reprennent après les lignes copiées
            for table in IDENTITY:
                target.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                               f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
        target.set_setting("copie_depuis", {"base": masked(source.url), "le": now()},
                           target.value("SELECT id FROM comptes WHERE cle = 'local'"))
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer base",
        description="La base de données d'Analyzer (ANALYZER_DB, sinon ~/.analyzer/analyzer.db) : son état, "
                    "l'import d'un dossier d'historiques, la copie vers une autre base.")
    parser.add_argument("--importer", metavar="DOSSIER", help="importe les historiques d'un dossier (et de ses zip)")
    parser.add_argument("--eleve", metavar="IDENTIFIANT", help="…dans l'espace de cet élève (sinon le tien)")
    parser.add_argument("--copier-vers", metavar="ADRESSE",
                        help="recopie toute la base vers une base vide (postgresql://… ou sqlite:///chemin)")
    parser.add_argument("--fusionner-depuis", metavar="ADRESSE",
                        help="ajoute à cette base ce qui lui manque d'une autre (rien n'est effacé ni remplacé par "
                             "plus ancien)")
    parser.add_argument("--compte", metavar="CLE", help="avec --fusionner-depuis : le compte qui reçoit tout")
    args = parser.parse_args(argv)
    try:
        db = current()
        if args.importer:
            folder = Path(args.importer).expanduser()
            if not folder.is_dir():
                print(f"Dossier introuvable : {folder}", file=sys.stderr)
                return 1
            key = f"eleve:{args.eleve}" if args.eleve else "moi"
            if args.eleve and db.value("SELECT id FROM espaces WHERE cle = ?", (key,)) is None:
                print(f"Élève inconnu : {args.eleve}", file=sys.stderr)
                return 1
            space = db_hands.space(db, key, "Moi")
            added = db_hands.sync_folder(db, space, folder)
            print(f"{added} main(s) nouvelle(s) ; {db_hands.count(db, space)} dans l'espace « {key} ».")
        if args.copier_vers:
            target = Database(args.copier_vers)
            counts = copy(db, target)
            print(f"Copié vers {masked(target.url)} : {counts.get('mains', 0)} main(s). Pour t'en servir : "
                  f"ANALYZER_DB={masked(target.url)}")
        if args.fusionner_depuis:
            from .merge import merge
            source = Database(args.fusionner_depuis)
            merge(source, db, log=lambda m: print("Ajouté ou mis à jour — " + m), account=args.compte)
            source.close()
        if not args.importer and not args.copier_vers and not args.fusionner_depuis:
            info = status(db)
            print(f"Base : {info['url']} ({info['dialect']}, schéma v{info['schema']})")
            for s in info["spaces"]:
                print(f"  {s['key']:<24} {s['name']:<20} {s['hands']:>7} main(s)  {s['files']:>5} historique(s)")
            print(f"  {info['opponents']} adversaire(s) classé(s) à la main, {info['analyses']} main(s) analysée(s) "
                  "par le solveur")
            documents = ", ".join(f"{n} {kind}" for kind, n in info["documents"].items()) or "aucun"
            print(f"  documents du solveur : {documents} ; {info['studies']} étude(s) ; "
                  f"{info['training']} décision(s) d'entraînement")
    except DatabaseError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
