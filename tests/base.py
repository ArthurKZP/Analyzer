"""Un dossier d'Analyzer propre à chaque test (sa base de données, son cache) : les tests n'écrivent jamais dans
~/.analyzer ni dans la base désignée par ANALYZER_DB."""
import os
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
