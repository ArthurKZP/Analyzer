import json
import re
import unittest
from pathlib import Path

from analyzer import spots
from analyzer.parsers import load_hands
from analyzer.report import chart_sample
from analyzer.spots import line_options, line_tag, spot_records
from analyzer.viewer import _json_for_script, build_viewer

FIXTURES = Path(__file__).parent / "fixtures"


class SpotRecordsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = {r["id"]: r for r in spot_records(load_hands([FIXTURES]), "Hero", "Villain")}

    def test_three_bet_pot_played_to_showdown(self):
        r = self.records["HAND02"]
        self.assertEqual((r["pt"], r["pfa"], r["hp"]), ("3bp", "H", "BB"))
        self.assertEqual(r["sa"]["f"], {"H": ["bet"], "V": ["call"]})
        self.assertEqual(r["sa"]["t"], {"H": ["xx"], "V": ["xx"]})
        self.assertEqual(r["cb"], {"f": 1, "t": 0})
        self.assertIn(line_tag("H", "flop", "C-bet", "petite (≤ 55 %)"), r["tags"])
        self.assertIn(line_tag("V", "river", "Mise après check au turn", "petite (≤ 55 %)"), r["tags"])
        self.assertIn(line_tag("V", "river", "Toutes ses mises", "petite (≤ 55 %)"), r["tags"])
        self.assertEqual(r["end"], "sd")
        self.assertAlmostEqual(r["net"], 77 / 5)
        self.assertEqual(r["vc"], ["Ts", "9s"])
        self.assertEqual(r["eq"]["r"], 1.0)  # dame servie contre hauteur
        self.assertEqual(r["hd"]["f"], "Top paire")

    def test_check_raise_and_fold(self):
        r = self.records["HAND01"]
        self.assertEqual(r["end"], "hff")
        self.assertIn("xr", r["sa"]["f"]["V"])
        self.assertIn(line_tag("V", "flop", "Check-raise", ""), r["tags"])
        self.assertEqual(r["vc"], [])  # pas d'abattage : sa main reste cachée
        self.assertEqual(r["eq"], {})

    def test_walk_limp_and_allin(self):
        self.assertEqual(self.records["HAND04"]["pt"], "srp")  # limp puis iso-raise
        self.assertEqual(self.records["HAND04"]["end"], "vfp")
        allin = self.records["HAND03"]
        self.assertTrue(allin["ai"])
        self.assertEqual(allin["pt"], "4bp")
        self.assertEqual(allin["ret"], {"V": 60.0})  # 300 € non payés, soit 60 bb
        self.assertEqual(allin["reach"], 3)

    def test_line_options_count_hands(self):
        options = dict(line_options(list(self.records.values())))
        self.assertEqual(options[line_tag("V", "flop", "Check-raise", "")], 1)
        self.assertEqual(options[line_tag("H", "flop", "Toutes tes mises", "petite (≤ 55 %)")], 2)


class ViewerTest(unittest.TestCase):
    def test_json_cannot_close_script(self):
        text = _json_for_script({"name": "</script><img src=x>&"})
        self.assertNotIn("<", text)
        self.assertEqual(json.loads(text)["name"], "</script><img src=x>&")

    def test_page_embeds_data(self):
        html = build_viewer(load_hands([FIXTURES]), "Hero", "Villain", "villain.html")
        self.assertIn("<title>Spots — Villain</title>", html)
        self.assertIn('href="villain.html"', html)
        self.assertNotRegex(html, r"__(TITLE|BACK|DATA|SCRIPT)__")
        payload = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
        data = json.loads(payload)
        self.assertEqual(len(data["records"]), 4)
        self.assertEqual(data["hero"], "Hero")
        self.assertFalse(data["solver"])  # fichier autonome : pas de serveur pour résoudre

    def test_page_without_hands(self):
        html = build_viewer(load_hands([FIXTURES]), "Hero", None, embed=True, solver=True, api="/api/coups")
        data = json.loads(re.search(r'id="data">(.*?)</script>', html, re.S).group(1))
        self.assertNotIn("records", data)  # application : les mains restent sur le serveur
        self.assertEqual((data["api"], data["count"], data["opponents"]), ("/api/coups", 4, ["Villain"]))
        self.assertTrue(data["lines"])


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = [spots.list_record(r) for r in spot_records(load_hands([FIXTURES]), "Hero", "Villain")]

    def ids(self, **filters):
        return [r["id"] for r in spots.search(self.records, filters)["rows"]]

    def test_filters_and_sorts(self):
        self.assertNotIn("x", self.records[0])  # sans le détail pour rejouer
        self.assertEqual(self.ids(), ["HAND04", "HAND03", "HAND02", "HAND01"])  # plus récentes d'abord
        self.assertEqual(self.ids(sort="old"), ["HAND01", "HAND02", "HAND03", "HAND04"])
        self.assertEqual(self.ids(pot="3bp"), ["HAND02"])
        self.assertEqual(self.ids(end="sd"), ["HAND03", "HAND02"])
        self.assertEqual(self.ids(res="l", sort="loss")[0], "HAND03")
        self.assertEqual(self.ids(q="qjs"), ["HAND02"])
        self.assertEqual(self.ids(cb="1"), ["HAND02", "HAND01"])  # c-bet faite au flop
        self.assertEqual(self.ids(v="xr"), ["HAND01"])
        self.assertEqual(self.ids(l=line_tag("V", "flop", "Check-raise", "")), ["HAND01"])
        page = spots.search(self.records, {}, offset=1, limit=2)
        self.assertEqual((page["total"], [r["id"] for r in page["rows"]]), (4, ["HAND03", "HAND02"]))
        self.assertEqual(set(page["rows"][0]), set(spots.ROW_FIELDS))
        summary = spots.search(self.records, {"st": "f"})["summary"]
        self.assertEqual((summary["n"], summary["sd"], summary["st"]), (4, 2, "f"))
        self.assertEqual(summary["reached"], 2)


class ChartTest(unittest.TestCase):
    def test_sample_keeps_shape(self):
        curve = [(float(i % 97 - (i // 500) * 3), float(-i), 0.0, 0.0) for i in range(8000)]
        kept = chart_sample(curve)
        self.assertLess(len(kept), 1501)
        self.assertEqual((kept[0], kept[-1]), (0, 7999))
        self.assertEqual(kept, sorted(set(kept)))
        lowest = min(range(8000), key=lambda i: curve[i][0])
        self.assertIn(curve[lowest][0], [curve[i][0] for i in kept])  # le creux du résultat reste
        self.assertEqual(chart_sample(curve[:100]), list(range(100)))  # courte série : tout


if __name__ == "__main__":
    unittest.main()
