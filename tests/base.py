"""Un dossier d'Analyzer propre à chaque test (sa base de données, son cache) : les tests n'écrivent jamais dans
~/.analyzer ni dans la base désignée par ANALYZER_DB."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def close_storage() -> None:
    """Ferme la base et le cache (avant d'effacer le dossier : Windows ne supprime pas un fichier ouvert)."""
    from analyzer import db, store
    db.close_all()
    store.close()


class IsolatedHome(unittest.TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        patcher = mock.patch.dict(os.environ, {"ANALYZER_HOME": str(self.home), "ANALYZER_DB": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(close_storage)


_MODULE: list = []


def isolate_module() -> None:
    """Pour setUpModule : un dossier d'Analyzer à part pour tout le module (ses tests qui n'en ont pas à eux)."""
    tmp = tempfile.TemporaryDirectory()
    patcher = mock.patch.dict(os.environ, {"ANALYZER_HOME": str(Path(tmp.name) / "home"), "ANALYZER_DB": ""})
    patcher.start()
    _MODULE.append((tmp, patcher))


def release_module() -> None:
    """Pour tearDownModule."""
    close_storage()
    while _MODULE:
        tmp, patcher = _MODULE.pop()
        patcher.stop()
        tmp.cleanup()


def renumbered(text: str, digit: str) -> str:
    """Le même historique Betclic avec d'autres numéros de main (HAND01 -> HAND<digit>1…)."""
    return text.replace("Hand ID: HAND0", f"Hand ID: HAND{digit}")


def seen_by(text: str, player: str) -> str:
    """Le même historique Betclic vu par un autre joueur de la table : l'étiquette Hero passe à sa place."""
    out = []
    for line in text.split("\n"):
        found = re.match(r"^(Seat \d+: (.+) \(.*\)) \[([^\]]*)\]$", line)
        if found:
            tags = [t for t in found.group(3).split() if t != "Hero"] + (["Hero"] if found.group(2) == player else [])
            line = f"{found.group(1)} [{' '.join(tags)}]"
        out.append(line)
    return "\n".join(out)
