import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from analyzer import leaks, tracking
from analyzer.app.tracking_page import build_tracking_page, sparkline
from analyzer.stats import Ratio

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome


def hand(day: date, opened: bool = False):
    return SimpleNamespace(date=datetime(day.year, day.month, day.day, 20), opened=opened)


def stat(hits: int, opps: int, ref: float = 0.4) -> leaks.Stat:
    return leaks.Stat("pf:btn:open", "Préflop", "Bouton : open", "aggr", {"reg": Ratio(hits, opps)}, ref, per100=40)


def stats_of(part: list) -> list:
    """Une seule stat : la part des mains « ouvertes », face à une théorie à 40 %."""
    return [stat(sum(h.opened for h in part), len(part))] if part else []


class PeriodsTest(unittest.TestCase):
    def test_months_weeks_blocks(self):
        months = [hand(date(2026, m, 3)) for m in (1, 2, 3)] + [hand(date(2026, 3, 20))]
        unit, found = tracking.periods(months)
        self.assertEqual((unit, [p.label for p in found], [len(p.hands) for p in found]),
                         ("mois", ["janv. 2026", "févr. 2026", "mars 2026"], [1, 1, 2]))
        weeks = [hand(date(2026, 5, 4) + timedelta(days=7 * k)) for k in range(3)]
        unit, found = tracking.periods(weeks)
        self.assertEqual((unit, found[0].label), ("semaine", "sem. du 04/05"))
        # deux semaines seulement : des blocs d'environ BLOCK_HANDS mains
        many = [hand(date(2026, 5, 4) + timedelta(days=k % 9)) for k in range(600)]
        unit, found = tracking.periods(many)
        self.assertEqual((unit, len(found), sum(len(p.hands) for p in found)), ("bloc", 2, 600))
        self.assertEqual(found[0].label, "04/05 → 08/05")
        day = [hand(date(2026, 5, 4)) for _ in range(300)]  # une seule journée : ses heures
        self.assertEqual(tracking.periods(day)[1][0].label, "04/05 20h00 → 20h00")
        self.assertEqual(tracking.periods([]), ("bloc", []))

    def test_oldest_periods_merged(self):
        hands = [hand(date(2025, 1, 1) + timedelta(days=31 * k)) for k in range(12)]
        unit, found = tracking.periods(hands)
        self.assertEqual((unit, len(found)), ("mois", tracking.MAX_PERIODS))
        self.assertEqual(len(found[0].hands), 12 - tracking.MAX_PERIODS + 1)
        self.assertTrue(found[0].label.startswith("jusqu'à "))
        self.assertEqual(found[-1].label, "déc. 2025")


class StatusTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(tracking.status(stat(80, 100), stat(5, 8)), "trop_tot")  # trop peu d'occasions depuis
        self.assertEqual(tracking.status(stat(80, 100), None), "trop_tot")
        self.assertEqual(tracking.status(stat(80, 100), stat(41, 100)), "corrige")  # dans la tolérance
        self.assertEqual(tracking.status(stat(80, 100), stat(60, 100)), "progres")  # rapproché, pas encore juste
        self.assertEqual(tracking.status(stat(60, 100), stat(80, 100)), "aggrave")
        self.assertEqual(tracking.status(stat(60, 100), stat(63, 100)), "pas_encore")  # le hasard l'explique
        self.assertEqual(tracking.status(None, stat(60, 100)), "sans_avant")  # rien avant pour comparer


class FollowTest(IsolatedHome):
    def test_follow_storage(self):
        self.assertEqual(tracking.followed("/moi", "HU"), {})
        tracking.follow("/moi", "HU", "pf:btn:open", date(2026, 3, 1), "Bouton : open trop large")
        tracking.follow("/eleve/anna", "HU", "pf:sb:limp", date(2026, 2, 1))
        self.assertEqual(tracking.followed("/moi", "HU"),
                         {"pf:btn:open": {"since": "2026-03-01", "title": "Bouton : open trop large"}})
        self.assertEqual(tracking.followed("/moi", "6max"), {})  # un suivi par format et par joueur
        self.assertEqual(list(tracking.followed("/eleve/anna", "HU")), ["pf:sb:limp"])
        self.assertEqual(tracking.follow("/moi", "HU", "pf:btn:open", None), {})


class BuildTest(unittest.TestCase):
    def setUp(self):
        # janvier et février : il ouvre 80 % ; il travaille son open dès le 1er mars ; mars et avril : 42 %
        self.hands = []
        for month, share in ((1, 0.8), (2, 0.8), (3, 0.42), (4, 0.42)):
            self.hands += [hand(date(2026, month, 1 + k % 25), k < 100 * share) for k in range(100)]

    def test_build_and_page(self):
        follow_list = {"pf:btn:open": {"since": "2026-03-01", "title": "Bouton : open trop large"}}
        found = tracking.build(self.hands, stats_of, follow_list, [("pf:btn:open", "Bouton : open", "plus"),
                                                                   ("srp:cbet", "Cbet flop", "moins")])
        self.assertEqual((found.unit, len(found.periods), found.hands), ("mois", 4, 400))
        followed, other = found.tracks
        self.assertEqual((followed.status, followed.since_index(found.periods), followed.current),
                         ("corrige", 2, "plus"))
        self.assertEqual((followed.before.ratios["reg"].opps, followed.after.ratios["reg"].hits), (200, 84))
        self.assertEqual((other.key, other.since, other.stats), ("srp:cbet", None, [None] * 4))
        page = build_tracking_page(found, "/api/suivi", today=date(2026, 5, 2))
        for text in ("Les leaks que tu travailles", "Bouton : open trop large", 'class="tr-s corrige"', "Corrigé",
                     "Avant le 01/03/2026 : 80 % (200 occasions) · depuis : 42 % (200)", 'value="2026-05-02"',
                     "Toutes tes stats, période par période", 'data-api="/api/suivi"', "janv. 2026 : 80 %"):
            self.assertIn(text, page)
        svg = sparkline(followed, found.periods)
        self.assertEqual((svg.count('class="pt out"'), svg.count('class="pt"')), (2, 2))  # écart net, puis juste
        self.assertIn('class="since"', svg)
        empty = build_tracking_page(tracking.build([], stats_of, {}, []), "/api/suivi")
        self.assertIn("Pas encore de mains datées", empty)

    def test_bad_date_ignored(self):
        found = tracking.build(self.hands, stats_of, {"pf:btn:open": {"since": "hier"}}, [])
        self.assertEqual(found.tracks, [])


if __name__ == "__main__":
    unittest.main()
