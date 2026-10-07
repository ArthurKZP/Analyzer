"""Sauvegarde de ce qu'Analyzer a calculé (~/.analyzer) et restauration.

Destination : un dossier (de préférence synchronisé en ligne : OneDrive, Google Drive, Dropbox…) ou un
stockage en ligne configuré avec rclone (« gdrive:Analyzer », « s3:mon-bucket/analyzer »… voir rclone.org).

- L'essentiel : la base de données, copie cohérente même pendant que l'application tourne (tes mains et celles de
  tes élèves avec leurs historiques d'origine, type des adversaires, résumés des mains analysées, tailles de mise
  choisies, plans de jeu, résultats du solveur, ranges, réglages, journal de l'entraîneur et fiches des études).
  Une archive datée par sauvegarde (archives/), les KEEP dernières sont gardées. Une base PostgreSQL (ANALYZER_DB)
  est sauvegardée par son hébergeur.
- Les arbres des études (.etude : de 20 Mo à quelques centaines de Mo chacun), en option : copiés dans etudes/,
  puis seuls les nouveaux ou les modifiés passent.

La restauration fusionne la base de la dernière archive dans celle d'ici (analyzer/db/merge.py : rien n'est effacé
ni remplacé par plus ancien ; une archive d'avant la base, faite de fichiers, est reprise de la même façon) puis
copie les arbres d'études absents.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import blobs, db
from .theory import postflop

DB_ENTRY = "analyzer.db"  # la base SQLite, dans l'archive
# Archives d'avant la base : leurs fichiers sont remis dans le dossier d'Analyzer puis repris dans la base.
LEGACY = ("joueurs.json", "revue/", "eleves/", "tailles/", "resolutions/", "entrainement/", "plans/", "ranges/",
          "etudes/", "precisions.json", "reglages.json", "durees.json")
JOURNAL = "entrainement/journal.jsonl"
KEEP = 10      # archives gardées à destination
KEEP_LOCAL = 2
PREFIX = "analyzer-"


class BackupError(RuntimeError):
    pass


def home() -> Path:
    return postflop.home()


def config_path() -> Path:
    return home() / "sauvegarde.json"


def load_config() -> dict:
    out = {"dest": "", "studies": False, "auto": False, "last": None}
    try:
        saved = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            out.update({k: saved[k] for k in out if k in saved})
    except (OSError, ValueError):
        pass
    return out


def save_config(**changes) -> dict:
    config = dict(load_config(), **changes)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return config


def is_remote(dest: str) -> bool:
    """« gdrive:Analyzer » (rclone) ; « C:\\…» ou « /home/… » sont des dossiers."""
    return bool(re.match(r"^[A-Za-z0-9_\-. ]{2,}:", dest))


def _join(dest: str, *parts: str) -> str:
    return dest.rstrip("/") + ("" if dest.endswith(":") else "/") + "/".join(parts)


def _rclone(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("rclone")
    if not exe:
        raise BackupError("rclone est introuvable : installe-le (https://rclone.org) puis configure ton stockage "
                          "avec « rclone config ».")
    proc = subprocess.run([exe, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise BackupError("rclone : " + (proc.stderr.strip().splitlines() or ["échec"])[-1])
    return proc


def _archives(dest: str) -> list[str]:
    try:
        out = _rclone("lsf", _join(dest, "archives")).stdout
    except BackupError:
        return []  # pas encore de dossier archives/
    return sorted(n for n in out.split() if n.startswith(PREFIX) and n.endswith(".zip"))


def _copied(proc: subprocess.CompletedProcess) -> int:
    return sum("Copied" in line for line in (proc.stdout + proc.stderr).splitlines())


# --- Contenu -------------------------------------------------------------------------------------

def essentials_size() -> int:
    """La taille de la base SQLite (ce que pèse l'archive de l'essentiel, avant compression) ; 0 pour PostgreSQL."""
    path = db.current().path
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += path.with_name(path.name + suffix).stat().st_size if path else 0
        except OSError:
            pass
    return total


def study_files() -> list[Path]:
    folder = blobs.studies().root
    return sorted(folder.glob("*.etude")) if folder.is_dir() else []


def make_archive() -> Path:
    """Archive datée de l'essentiel (la base), dans ~/.analyzer/sauvegardes."""
    root = home()
    staging = root / "sauvegardes"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.zip"
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        snapshot = db.current().snapshot(staging / "analyzer.db.copie")  # None : base PostgreSQL
        if snapshot is not None:
            z.write(snapshot, DB_ENTRY)
            snapshot.unlink()
    tmp.replace(path)
    for old in sorted(staging.glob(PREFIX + "*.zip"))[:-KEEP_LOCAL]:
        old.unlink()
    return path


def _restore_db(data: bytes, log: Callable[[str], None]) -> dict:
    """Fusionne la base de l'archive dans celle d'ici (rien n'est effacé ni remplacé par plus ancien) ; renvoie ce
    qui a été ajouté ou mis à jour."""
    from .db.merge import merge
    staging = home() / "sauvegardes"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / "analyzer.db.restauree"
    tmp.write_bytes(data)
    archived = db.Database(f"sqlite:///{tmp}")  # (son schéma est mis à jour si l'archive est plus ancienne)
    try:
        counts = merge(archived, db.current(), log=lambda message: None)
    finally:
        archived.close()
        for suffix in ("", "-wal", "-shm"):
            side = tmp.with_name(tmp.name + suffix)
            if side.exists():
                side.unlink()
    added = {k: v for k, v in counts.items() if v}
    log("Base : " + (", ".join(f"{v} {k}" for k, v in added.items()) + " repris de l'archive" if added
                     else "déjà à jour") + ".")
    return counts


def _newer(src: Path, dst: Path) -> bool:
    if not dst.is_file():
        return True
    a, b = src.stat(), dst.stat()
    return a.st_size != b.st_size or a.st_mtime > b.st_mtime + 2


def _copy(src: Path, dst: Path) -> None:
    """Copie complète puis renommage : une copie interrompue ne laisse pas un fichier tronqué."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partiel")
    shutil.copy2(src, tmp)
    tmp.replace(dst)


def _size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit in ("o", "Ko") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


# --- Sauvegarde ----------------------------------------------------------------------------------

def backup(dest: Optional[str] = None, studies: Optional[bool] = None, log: Callable[[str], None] = print) -> dict:
    """Sauvegarde vers dest (par défaut celle des réglages) ; renvoie le bilan, aussi gardé dans les réglages."""
    config = load_config()
    dest = (dest if dest is not None else config["dest"]).strip()
    studies = config["studies"] if studies is None else studies
    if not dest:
        raise BackupError("Choisis d'abord où sauvegarder (un dossier synchronisé ou un stockage rclone).")
    start = time.time()
    archive = make_archive()
    log(f"Archive de l'essentiel : {archive.name} ({_size(archive.stat().st_size)})")
    copied, copied_size = 0, 0
    if is_remote(dest):
        _rclone("copyto", str(archive), _join(dest, "archives", archive.name))
        for old in _archives(dest)[:-KEEP]:
            _rclone("deletefile", _join(dest, "archives", old))
        if studies and study_files():
            log("Études : copie des nouvelles avec rclone…")
            copied = _copied(_rclone("copy", str(blobs.studies().root), _join(dest, "etudes"), "--include", "*.etude",
                                     "-v"))
    else:
        target = Path(dest).expanduser()
        if target.resolve() == home().resolve() or home().resolve() in target.resolve().parents:
            raise BackupError("Choisis un dossier en dehors de celui d'Analyzer (" + str(home()) + ").")
        try:
            (target / "archives").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"Dossier de sauvegarde inaccessible : {target} ({exc.strerror or exc}).") from exc
        _copy(archive, target / "archives" / archive.name)
        for old in sorted((target / "archives").glob(PREFIX + "*.zip"))[:-KEEP]:
            old.unlink()
        if studies:
            files = [f for f in study_files() if _newer(f, target / "etudes" / f.name)]
            for k, f in enumerate(files, 1):
                log(f"Étude {k}/{len(files)} : {f.name} ({_size(f.stat().st_size)})")
                _copy(f, target / "etudes" / f.name)
                copied += 1
                copied_size += f.stat().st_size
    last = {"t": int(time.time()), "dest": dest, "archive": archive.name, "archive_size": archive.stat().st_size,
            "studies": copied, "studies_size": copied_size, "with_studies": bool(studies),
            "seconds": round(time.time() - start, 1)}
    save_config(last=last)
    log(f"Sauvegarde terminée vers {dest}" + (f" ; {copied} étude(s) copiée(s)" if studies else "") + ".")
    return last


# --- Restauration --------------------------------------------------------------------------------

def _latest_archive(source: str, log: Callable[[str], None]) -> Optional[Path]:
    if is_remote(source):
        names = _archives(source)
        if not names:
            return None
        local = home() / "sauvegardes" / names[-1]
        local.parent.mkdir(parents=True, exist_ok=True)
        log(f"Téléchargement de {names[-1]}…")
        _rclone("copyto", _join(source, "archives", names[-1]), str(local))
        return local
    found = sorted((Path(source).expanduser() / "archives").glob(PREFIX + "*.zip"))
    return found[-1] if found else None


def _merge_journal(current: Path, incoming: bytes) -> int:
    """Ajoute au journal de l'entraîneur les lignes de l'archive qu'il n'a pas."""
    have = current.read_text(encoding="utf-8").splitlines() if current.is_file() else []
    known = set(have)
    new = [line for line in incoming.decode("utf-8").splitlines() if line.strip() and line not in known]
    if new:
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text("\n".join(have + new) + "\n", encoding="utf-8")
    return len(new)


def restore(source: Optional[str] = None, studies: bool = True, log: Callable[[str], None] = print) -> dict:
    """Reprend la dernière archive de source (par défaut la destination des réglages) et les études absentes."""
    source = (source if source is not None else load_config()["dest"]).strip()
    if not source:
        raise BackupError("Indique d'où restaurer (le dossier ou le stockage de la sauvegarde).")
    archive = _latest_archive(source, log)
    if archive is None:
        raise BackupError(f"Aucune sauvegarde Analyzer dans {source}.")
    root = home().resolve()
    restored = kept = 0
    legacy = False
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename
            target = (root / name).resolve()
            if info.is_dir() or name.startswith("/") or ".." in Path(name).parts or root not in target.parents:
                continue  # chemin hors du dossier d'Analyzer : ignoré
            data = z.read(info)
            if name == DB_ENTRY:
                if any(_restore_db(data, log).values()):
                    restored += 1
                else:
                    kept += 1
                continue
            if not name.startswith(LEGACY):
                continue  # (ni la base, ni un fichier d'avant la base : ignoré)
            legacy = True
            if name == JOURNAL:
                restored += bool(_merge_journal(target, data))
                continue
            stamp = time.mktime(info.date_time + (0, 0, -1))
            if target.is_file() and target.stat().st_mtime >= stamp - 2:
                kept += 1  # le fichier local est aussi récent : on le garde
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            restored += 1
    if legacy:  # archive d'avant la base : ses fichiers rejoignent la base
        from .db import legacy as db_legacy
        db_legacy.import_once(db.current(), force=True)
    log(f"{archive.name} : {restored} élément(s) restauré(s), {kept} déjà à jour.")
    copied = 0
    if studies:
        folder = blobs.studies().root
        if is_remote(source):
            copied = _copied(_rclone("copy", _join(source, "etudes"), str(folder), "--include", "*.etude",
                                     "--ignore-existing", "-v"))
        else:
            src = Path(source).expanduser() / "etudes"
            files = [f for f in sorted(src.glob("*.etude")) if not (folder / f.name).is_file()] if src.is_dir() else []
            for k, f in enumerate(files, 1):
                log(f"Étude {k}/{len(files)} : {f.name} ({_size(f.stat().st_size)})")
                _copy(f, folder / f.name)
                copied += 1
    return {"archive": archive.name, "restored": restored, "kept": kept, "studies": copied}


# --- Ligne de commande ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer sauvegarde",
        description="Sauvegarde (ou restaure) la base d'Analyzer (mains, tailles, résolutions, mains analysées, "
                    "entraînement…) et, en option, les arbres des études.")
    parser.add_argument("destination", nargs="?",
                        help="dossier (synchronisé en ligne de préférence) ou stockage rclone « nom:dossier » ; "
                             "retenu pour les fois suivantes")
    parser.add_argument("--etudes", dest="etudes", action="store_const", const=True,
                        help="copier aussi les études (volumineux) ; retenu")
    parser.add_argument("--sans-etudes", dest="etudes", action="store_const", const=False,
                        help="seulement l'essentiel (quelques Mo) ; retenu")
    parser.add_argument("--auto", dest="auto", action="store_const", const=True,
                        help="sauvegarde automatique après chaque calcul dans l'application ; retenu")
    parser.add_argument("--sans-auto", dest="auto", action="store_const", const=False,
                        help="plus de sauvegarde automatique ; retenu")
    parser.add_argument("--restaurer", action="store_true", help="restaure la dernière sauvegarde de la destination")
    args = parser.parse_args(argv)
    changes = {k: v for k, v in (("dest", args.destination), ("studies", args.etudes), ("auto", args.auto))
               if v is not None}
    config = save_config(**changes) if changes else load_config()
    try:
        if args.restaurer:
            result = restore()
            print(f"Restauré : {result['restored']} élément(s), {result['studies']} étude(s).")
        else:
            backup()
            if config["auto"]:
                print("Sauvegarde automatique activée dans l'application.")
    except BackupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
