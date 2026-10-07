"""Les gros fichiers d'Analyzer, hors de la base : les arbres résolus des études (<clé>.etude, de 20 Mo à quelques
centaines de Mo chacun ; leurs fiches sont dans la base, analyzer/db/studies.py).

Sur ton ordinateur, un dossier (~/.analyzer/etudes). En ligne, un stockage objet (S3 ou compatible, hébergé en UE)
prendra le relais avec la même interface, le dossier local servant alors de cache au solveur, qui ne lit et
n'écrit que des fichiers :

- path(clé) : le chemin local où le solveur écrit l'arbre (analyzer-solve --save) ;
- stored(clé) : une fois l'arbre écrit, il est confié au stockage (en ligne : envoyé) ;
- fetch(clé) : le chemin local de l'arbre, prêt à être relu par le solveur (en ligne : téléchargé au besoin) ;
- exists, size, delete, keys.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")


class LocalFiles:
    """Les fichiers dans un dossier de cet ordinateur (un par clé : <clé><suffixe>)."""

    def __init__(self, root: Path, suffix: str):
        self.root, self.suffix = Path(root), suffix

    def path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError(f"Clé de fichier invalide : {key!r}")
        return self.root / f"{key}{self.suffix}"

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def size(self, key: str) -> Optional[int]:
        try:
            return self.path(key).stat().st_size
        except OSError:
            return None

    def stored(self, key: str) -> None:
        """Le fichier vient d'être écrit à path(key) : il est déjà à sa place."""

    def fetch(self, key: str) -> Path:
        return self.path(key)

    def delete(self, key: str) -> bool:
        path = self.path(key)
        if path.is_file():
            path.unlink()
            return True
        return False

    def keys(self) -> set[str]:
        if not self.root.is_dir():
            return set()
        return {p.name[:-len(self.suffix)] for p in self.root.iterdir() if p.name.endswith(self.suffix)}

    def describe(self) -> str:
        return str(self.root)


def studies() -> LocalFiles:
    """Les arbres des études du solveur."""
    from . import db
    return LocalFiles(db.home() / "etudes", ".etude")
