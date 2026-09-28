import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from analyzer.cli import find_player, main, slugify

FIXTURES = Path(__file__).parent / "fixtures"


class CliTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("_Bërsërk_"), "berserk")
        self.assertEqual(slugify("Peste Noire"), "peste-noire")

    def test_find_player_is_accent_and_case_insensitive(self):
        names = ["_Bërsërk_", "Peste Noire"]
        self.assertEqual(find_player("berserk", names), "_Bërsërk_")
        self.assertEqual(find_player("peste", names), "Peste Noire")
        self.assertIsNone(find_player("inconnu", names))

    def test_generates_report(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            code = main([str(FIXTURES), "--adversaire", "villain", "--sortie", tmp])
            report = Path(tmp) / "villain.html"
            self.assertEqual(code, 0)
            html = report.read_text(encoding="utf-8")
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
