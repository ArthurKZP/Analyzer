import shutil
import tempfile
import unittest
from pathlib import Path

from analyzer.app.library import Library

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "betclic_sample.txt"


class ChosenPseudosTest(IsolatedHome):
    def test_choose_pseudos(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = Library(tmp.name)
        ident = lib.create_student("Paul")["id"]
        student = lib.student(ident)
        folder = Path(student.folder)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE, folder / "betclic.txt")  # ses mains heads-up (« Hero »)
        shutil.copy(HERE / "sites" / "ggpoker.txt", folder / "gg.txt")  # sur GGPoker, le héros s'appelle « Hero »
        # les historiques d'un autre joueur, importés avec les siens
        text = (HERE / "sites" / "winamax.txt").read_text(encoding="utf-8").replace("Hero", "Autre")
        (folder / "autre.txt").write_text(text, encoding="utf-8")
        student.reload()
        self.assertEqual(len(student.hands) + len(student.ring), 8)  # sans choix : tous les héros sont lui
        view = student.pseudos_view()
        self.assertEqual(({p["name"] for p in view["pseudos"]}, view["chosen"], view["student"]),
                         ({"Hero", "Autre"}, [], True))
        found = student.set_chosen_pseudos(["Hero"])
        self.assertEqual((len(student.hands), len(student.ring)), (4, 2))
        self.assertEqual({p["name"]: p["role"] for p in found["pseudos"]["pseudos"]}, {"Hero": "me", "Autre": "other"})
        self.assertEqual(found["pseudos"]["chosen"], ["Hero"])
        with self.assertRaises(ValueError):
            student.set_chosen_pseudos(["Inconnu"])
        with self.assertRaises(ValueError):
            lib.set_chosen_pseudos(["Hero"])  # pas un élève
        student.set_chosen_pseudos([])  # retour au choix automatique
        self.assertEqual(len(student.hands) + len(student.ring), 8)
        self.assertIn("Résultat cumulé", student.self_page("bilan"))


if __name__ == "__main__":
    unittest.main()
