import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from analyzer.cli import find_player, main, slugify

try:
    from .base import isolate_module, release_module
except ImportError:  # lancé par « unittest discover -s tests »
    from base import isolate_module, release_module


def setUpModule():
    isolate_module()


def tearDownModule():
    release_module()

FIXTURES = Path(__file__).parent / "fixtures"


class CliTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("_Vïllâïn_"), "villain")
        self.assertEqual(slugify("Joueur Exemple"), "joueur-exemple")

    def test_find_player_is_accent_and_case_insensitive(self):
        names = ["_Vïllâïn_", "Joueur Exemple"]
        self.assertEqual(find_player("villain", names), "_Vïllâïn_")
        self.assertEqual(find_player("exemple", names), "Joueur Exemple")
        self.assertIsNone(find_player("inconnu", names))

    def test_generates_report(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            code = main([str(FIXTURES), "--adversaire", "villain", "--sortie", tmp])
            report = Path(tmp) / "villain.html"
            self.assertEqual(code, 0)
            html = report.read_text(encoding="utf-8")
            self.assertTrue((Path(tmp) / "villain-spots.html").exists())
            self.assertIn('href="villain-spots.html"', html)
            self.assertIn('href="villain-preflop.html"', html)
            preflop = (Path(tmp) / "villain-preflop.html").read_text(encoding="utf-8")
            self.assertIn('href="villain.html">Rapport</a>', preflop)
            self.assertIn('href="villain-spots.html#hand=HAND03"', preflop)
        self.assertIn("<title>Profil HU — Villain</title>", html)
        self.assertIn("Ses mains à l'abattage (2)", html)
        self.assertIn("== Villain — 4 mains ==", out.getvalue())

    def test_list(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([str(FIXTURES), "--liste"]), 0)
        self.assertIn("Villain", out.getvalue())
        self.assertIn("Toi : Hero", out.getvalue())


if __name__ == "__main__":
    unittest.main()
