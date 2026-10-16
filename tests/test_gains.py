import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from analyzer import gains
from analyzer.app.gains_page import breakdown_html, filter_bar
from analyzer.app.library import Library

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"
SIX_MAX = Path(__file__).parent / "sites" / "betclic_6max.txt"


def hand(when: datetime, bb: float = 0.5, site: str = "Winamax", net: float = 1.0, ident: str = "1"):
    """Une main de Hero : son résultat net, sans all-in."""
    return SimpleNamespace(date=when, bb=bb, site=site, seats={"Hero": None}, hand_id=ident, showdown=False,
                           actions=[], net=lambda player: net)


class KeysTest(unittest.TestCase):
    def test_keys_and_labels(self):
        h = hand(datetime(2026, 9, 21, 23, 59), bb=5)
        self.assertEqual([gains.key(h, d) for d, _ in gains.DIMENSIONS], ["NL500", "Winamax", "2026-09", "fin", "soir"])
        self.assertEqual(gains.key(hand(datetime(2026, 9, 10, 0, 0), bb=0.02), "limite"), "NL2")
        self.assertEqual(gains.key(hand(datetime(2026, 9, 10, 0, 0)), "jour"), "debut")
        self.assertEqual(gains.key(hand(datetime(2026, 9, 11, 6, 0)), "jour"), "milieu")
        self.assertEqual(gains.key(hand(datetime(2026, 9, 11, 5, 59)), "heure"), "nuit")
        self.assertEqual(gains.key(hand(datetime(2026, 9, 11, 12, 0)), "heure"), "apres-midi")
        self.assertEqual(gains.label("mois", "2026-09"), "sept. 2026")
        self.assertEqual(gains.label("heure", "matin"), "Matin (6h–12h)")

    def test_clean(self):
        query = {"limite": "NL500", "room": "Winamax", "mois": "2026-13", "jour": "milieu", "heure": "aube",
                 "format": "HU", "x": "y"}
        self.assertEqual(gains.clean(query), {"limite": "NL500", "room": "Winamax", "jour": "milieu"})
        self.assertEqual(gains.clean({"limite": "500", "mois": "2026-09", "room": ""}), {"mois": "2026-09"})
        self.assertEqual(gains.query({"heure": "soir", "limite": "NL5"}), "limite=NL5&heure=soir")
        self.assertEqual(gains.describe({"heure": "soir", "limite": "NL5"}), "NL5 · Soir (18h–24h)")


class BreakdownTest(unittest.TestCase):
    def setUp(self):
        self.hands = ([hand(datetime(2026, 9, 2, 20), bb=0.5, net=2.0, ident=f"a{k}") for k in range(3)]
                      + [hand(datetime(2026, 10, 15, 9), bb=1.0, site="Betclic", net=-1.0, ident=f"b{k}")
                         for k in range(2)])

    def test_rows_and_filters(self):
        rows = gains.breakdown(self.hands, "Hero", "limite", {})
        self.assertEqual([(r.value, r.hands, r.net_bb, r.net) for r in rows],
                         [("NL50", 3, 12.0, 6.0), ("NL100", 2, -2.0, -2.0)])
        self.assertEqual(rows[0].bb100, 400.0)
        self.assertEqual(rows[0].margin, 0.0)  # trois résultats identiques : pas de hasard
        # le filtre de limite ne s'applique pas à sa propre répartition, celui de room oui
        rows = gains.breakdown(self.hands, "Hero", "limite", {"limite": "NL50", "room": "Betclic"})
        self.assertEqual([r.value for r in rows], ["NL100"])
        self.assertEqual(len(gains.apply(self.hands, {"heure": "soir"})), 3)
        self.assertEqual(gains.options(self.hands, "mois", {"room": "Winamax"}),
                         [("2026-09", "sept. 2026", 3)])
        self.assertEqual(gains.options(self.hands, "jour", {"jour": "fin"})[-1], ("fin", "Fin du mois (21–31)", 0))

    def test_html(self):
        bar = filter_bar(self.hands, {"room": "Betclic"}, "HU")
        for text in ('name="limite"', 'name="room"', "NL100 (2)", '<option value="Betclic" selected>Betclic (2)',
                     "Tout afficher", 'value="HU"', "Toute la journée"):
            self.assertIn(text, bar)
        self.assertNotIn("NL50", bar)  # les valeurs que les autres filtres gardent
        self.assertNotIn('name="limite"', filter_bar(self.hands[:3], {}, "HU"))  # une seule limite : rien à choisir
        page = breakdown_html(self.hands, "Hero", {"room": "Betclic"}, "HU")
        self.assertIn("Tes gains en détail", page)
        self.assertIn('href="?format=HU&amp;room=Betclic&amp;mois=2026-10"', page)
        self.assertIn('<tr class="on"><td><a href="?format=HU&amp;room=Betclic">Betclic</a>', page)
        self.assertEqual(filter_bar([], {}, "HU"), "")


class LibraryTest(IsolatedHome):
    def test_filtered_bilan(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "hu.txt")
        shutil.copy(SIX_MAX, Path(tmp.name) / "six.txt")
        lib = Library(tmp.name)
        page = lib.self_page("bilan")
        self.assertIn("Filtrer tes gains", page)  # les limites du heads-up et des tables à plusieurs
        self.assertNotIn("Tes gains en détail", page)  # le heads-up : une seule limite, room, date et heure
        self.assertNotIn('class="gf-on"', page)
        hu = lib.self_page("bilan", filters={"limite": "NL500", "x": "y"})
        self.assertIn("Filtre : <b>NL500</b> — 4 mains sur 5.", hu)
        self.assertIn('href="?format=ring&amp;limite=NL500"', hu)  # le choix du format garde le filtre
        ring = lib.self_page("bilan", table_format="ring", filters={"limite": "NL500"})
        self.assertIn("Filtre : <b>NL500</b>", ring)
        self.assertIn('<div class="value">+0,0 bb</div>', ring)  # aucune main 6-max à NL500
        self.assertIs(hu, lib.self_page("bilan", filters={"limite": "NL500"}))  # en cache, filtre compris


if __name__ == "__main__":
    unittest.main()
