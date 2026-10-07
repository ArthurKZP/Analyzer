"""Reprise, une seule fois, des données qu'Analyzer gardait dans des fichiers avant la base : type des adversaires
(joueurs.json), élèves (eleves/<élève>/eleve.json ; leurs historiques sont repris depuis leur dossier, qui reste une
boîte d'arrivée) et résumés des mains passées au solveur (revue/*.json). Les fichiers restent en place."""
from __future__ import annotations

import json

from . import Database, analyses, hands, home, now

DONE = "reprise_fichiers"


def import_once(db: Database, force: bool = False) -> dict:
    """Reprend les fichiers si ce n'est pas déjà fait (force : à nouveau, après la restauration d'une archive d'avant
    la base ; ce qui est déjà dans la base est gardé) ; renvoie ce qui a été repris."""
    if db.setting(DONE) and not force:
        return {}
    account = db.account()
    root = home()
    out = {"adversaires": 0, "eleves": 0, "analyses": 0}
    with db.transaction():
        try:
            kinds = json.loads((root / "joueurs.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kinds = {}
        for name, kind in (kinds.items() if isinstance(kinds, dict) else []):
            if isinstance(name, str) and kind in ("reg", "rec"):
                db.execute("INSERT INTO adversaires (compte_id, nom, type, maj_le) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT (compte_id, nom) DO NOTHING", (account, name, kind, now()))
                out["adversaires"] += 1
        students = root / "eleves"
        for folder in sorted(students.iterdir()) if students.is_dir() else []:
            try:
                meta = json.loads((folder / "eleve.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get("name"):
                hands.space(db, f"eleve:{folder.name}", meta["name"], meta.get("pseudo"), account,
                            created=meta.get("created"))
                out["eleves"] += 1
        reviews = root / "revue"
        for path in sorted(reviews.glob("*.json")) if reviews.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                analyses.put(db, path.stem, data, account)
                out["analyses"] += 1
        db.set_setting(DONE, {"le": now(), **out}, account)
    return out
