import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer.theory import postflop, studyspots
from analyzer.theory.studyspots import StudySpot, canonical, cards_of, flop_distance, suit_pattern


def write_selection(home: Path, family: str, board: str, plan: dict) -> None:
    path = home / "tailles" / f"{family}-{board}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"plan": plan, "family": family, "board": board}), encoding="utf-8")


def write_study(home: Path, spot: StudySpot) -> None:
    """Fiche d'étude « résolue » pour ce spot (sans arbre réel : un fichier vide suffit à la liste)."""
    request = spot.request()
    path = postflop.study_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    meta = {"kind": "spot", "key": postflop.study_key(request), "id": spot.ident, "family": spot.family,
            "board": spot.board, "texture": spot.texture}
    path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")


class FlopHelpersTest(unittest.TestCase):
    def test_patterns_and_canonical(self):
        self.assertEqual([suit_pattern(cards_of(b)) for b in ("KsKd4c", "As8s3h", "Jh9h5h")],
                         ["rainbow", "deux couleurs", "monotone"])
        self.assertEqual(canonical(cards_of("KsKd4c")), canonical(cards_of("KhKc4d")))
        self.assertEqual(canonical(cards_of("As8s3h")), canonical(cards_of("Ah8h3d")))
        self.assertNotEqual(canonical(cards_of("As8s3h")), canonical(cards_of("As8h3s")))  # couleur sur d'autres cartes
        self.assertLess(flop_distance(cards_of("Ks8d3h"), cards_of("Kh9c2d")),
                        flop_distance(cards_of("Ks8d3h"), cards_of("Ks8s3h")))  # structure de couleurs avant la hauteur


class FlopOptionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"ANALYZER_DB": "", "ANALYZER_HOME": str(self.home)})
        self.env.start()
        self.shipped = mock.patch.object(studyspots, "shipped_path", lambda family: self.home / "absent.json")
        self.shipped.start()

    def tearDown(self):
        self.shipped.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_borrowed_sizes_and_suggestions(self):
        write_selection(self.home, "4bet", "KsKd4c", {"bet:fi:": [25]})
        write_selection(self.home, "4bet", "Ah6h4c", {"bet:fi:": ["geo2"]})
        # hors série : les tailles du flop le plus proche ; un flop de la série garde son propre choix
        self.assertEqual(StudySpot("4bet", cards_of("Ah8h3d")).sizes_from, "Ah6h4c")
        self.assertEqual(StudySpot("4bet", cards_of("Ah8h3d")).plan, {"bet:fi:": ["geo2"]})
        self.assertIn("tailles de A♥6♥4♣", StudySpot("4bet", cards_of("Ah8h3d")).menu_text())
        series = StudySpot("4bet", cards_of("As7h2d"))
        self.assertEqual((series.plan, series.sizes_from), ({}, None))
        self.assertEqual(studyspots.normalize_board("4bet", ["4c", "Kd", "Ks"]), ["Ks", "Kd", "4c"])
        self.assertEqual(studyspots.normalize_board("4bet", ["3d", "Ah", "8h"]), ["Ah", "8h", "3d"])

        write_study(self.home, StudySpot("4bet", cards_of("KsKd4c")))
        write_study(self.home, StudySpot("4bet", cards_of("Ah6h4c")))
        options = studyspots.flop_options("4bet", ["4d", "Kh", "Kc"])
        self.assertEqual((options["id"], options["solved"], options["sizes_from"]), ("spot:4bet:KhKc4d", False, "KsKd4c"))
        self.assertEqual(options["suggestions"][0]["id"], "spot:4bet:KsKd4c")
        self.assertIn("aux couleurs près", options["suggestions"][0]["relation"])
        same = studyspots.flop_options("4bet", ["Kd", "Ks", "4c"])
        self.assertTrue(same["solved"])
        flush = studyspots.flop_options("4bet", ["Ah", "8h", "3d"])
        self.assertEqual(flush["suggestions"][0]["id"], "spot:4bet:Ah6h4c")  # même texture, deux couleurs
        self.assertEqual(flush["cost"], "moins d'une minute sur 4 cœurs")
        unsolved_series = studyspots.flop_options("4bet", ["As", "7h", "2d"])
        self.assertIn("choix des tailles d'abord", unsolved_series["cost"])


if __name__ == "__main__":
    unittest.main()
