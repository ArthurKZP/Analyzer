"""Reprise, une seule fois, des données qu'Analyzer gardait dans des fichiers avant la base. Les fichiers restent en
place (ils ne servent plus : tu peux les effacer une fois la reprise faite).

1. Type des adversaires (joueurs.json), élèves (eleves/<élève>/eleve.json ; leurs historiques sont repris depuis
   leur dossier, qui reste une boîte d'arrivée) et résumés des mains passées au solveur (revue/*.json).
2. Tailles de mise choisies (tailles/), plans de jeu (plans/), résultats du solveur (resolutions/), précision des
   spots (precisions.json), réglages (reglages.json), durées des résolutions (durees.json), solutions préflop des
   tables à plusieurs et ranges ajustées (ranges/), journal de l'entraîneur (entrainement/journal.jsonl) et fiches
   des études (etudes/*.json ; les arbres .etude restent dans etudes/, voir analyzer/blobs.py).

Ce qui est déjà dans la base est gardé.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import Database, analyses, hands, home, now, training
from .documents import bump, pack

STEPS_DONE = ("reprise_fichiers", "reprise_fichiers_2")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _files(folder: Path, pattern: str = "*.json") -> list[Path]:
    return sorted(folder.glob(pattern)) if folder.is_dir() else []


def _first(db: Database, account: int, root: Path) -> dict:
    out = {"adversaires": 0, "eleves": 0, "analyses": 0}
    kinds = _json(root / "joueurs.json")
    for name, kind in (kinds.items() if isinstance(kinds, dict) else []):
        if isinstance(name, str) and kind in ("reg", "rec"):
            db.execute("INSERT INTO adversaires (compte_id, nom, type, maj_le) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT (compte_id, nom) DO NOTHING", (account, name, kind, now()))
            out["adversaires"] += 1
    students = root / "eleves"
    for folder in sorted(students.iterdir()) if students.is_dir() else []:
        meta = _json(folder / "eleve.json")
        if isinstance(meta, dict) and meta.get("name"):
            hands.space(db, f"eleve:{folder.name}", meta["name"], meta.get("pseudo"), account,
                        created=meta.get("created"))
            out["eleves"] += 1
    for path in _files(root / "revue"):
        data = _json(path)
        if isinstance(data, dict):
            analyses.put(db, path.stem, data, account)
            out["analyses"] += 1
    return out


def _stamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return now()


def _document(db: Database, account: int, kind: str, key: str, data: Any, path: Path) -> int:
    db.execute("INSERT INTO documents (compte_id, type, cle, donnees, maj_le) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT (compte_id, type, cle) DO NOTHING", (account, kind, key, pack(data), _stamp(path)))
    return 1


def _second(db: Database, account: int, root: Path) -> dict:
    out = {"tailles": 0, "plans": 0, "resolutions": 0, "precisions": 0, "ranges": 0, "entrainement": 0,
           "etudes": 0}
    for path in _files(root / "tailles"):
        family, _, board = path.stem.rpartition("-")
        data = _json(path)
        if family and isinstance(data, dict):
            out["tailles"] += _document(db, account, "tailles", f"{family}:{board}", data, path)
    for path in _files(root / "plans"):
        data = _json(path)
        if isinstance(data, dict):
            out["plans"] += _document(db, account, "plan", path.stem, data, path)
    for path in _files(root / "resolutions"):
        data = _json(path)
        if isinstance(data, dict):
            out["resolutions"] += _document(db, account, "resolution", path.stem, data, path)
    precisions = _json(root / "precisions.json")
    for key, entry in (precisions.items() if isinstance(precisions, dict) else []):
        if isinstance(entry, dict):
            out["precisions"] += _document(db, account, "precision", key, entry, root / "precisions.json")
    settings = _json(root / "reglages.json")
    if isinstance(settings, dict) and "precision" in settings and db.setting("precision", account=account) is None:
        db.set_setting("precision", settings["precision"], account)
    durations = _json(root / "durees.json")
    if isinstance(durations, list) and db.setting("durees", account=account) is None:
        db.set_setting("durees", durations, account)
    for path in _files(root / "ranges"):
        data = _json(path)
        if path.stem == "perso" and isinstance(data, dict):
            out["ranges"] += _document(db, account, "ranges-ajustees", "perso", data, path)
        elif isinstance(data, dict) and isinstance(data.get("lines"), dict):
            out["ranges"] += _document(db, account, "ranges", path.stem, data, path)
    journal = root / "entrainement" / "journal.jsonl"
    if journal.is_file():
        rows = []
        for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        out["entrainement"] = training.add(db, rows, account, skip_known=True)
    for path in _files(root / "etudes"):
        tree, meta = path.with_suffix(".etude"), _json(path)
        if tree.is_file() and isinstance(meta, dict):
            db.execute("INSERT INTO etudes (compte_id, cle, type, fiche, taille, maj_le) VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT (compte_id, cle) DO NOTHING",
                       (account, path.stem, str(meta.get("kind", "")), pack(meta), tree.stat().st_size, _stamp(tree)))
            out["etudes"] += 1
    for kind in ("tailles", "plan", "resolution", "precision", "ranges", "ranges-ajustees", "etudes", "reglages"):
        bump(db, kind, account)
    return out


STEPS: tuple[tuple[str, Callable[[Database, int, Path], dict]], ...] = tuple(zip(STEPS_DONE, (_first, _second)))


def import_once(db: Database, force: bool = False) -> dict:
    """Reprend les fichiers si ce n'est pas déjà fait (force : à nouveau, après la restauration d'une archive d'avant
    la base ; ce qui est déjà dans la base est gardé) ; renvoie ce qui a été repris."""
    account = db.account()
    root = home()
    out: dict = {}
    for flag, step in STEPS:
        if db.setting(flag, account=account) and not force:
            continue
        with db.transaction():
            found = step(db, account, root)
            db.set_setting(flag, {"le": now(), **found}, account)
        out.update(found)
    return out
