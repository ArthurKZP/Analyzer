import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer import period
from analyzer.app.library import Library
from analyzer.app.server import start
from analyzer.cli import main as cli_main

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def spread_fixture(target: Path) -> None:
    """La main d'exemple, ses quatre mains jouées chacune un jour différent (du 07 au 10 janvier)."""
    text = FIXTURE.read_text(encoding="utf-8")
    for day, stamp in zip((10, 9, 8, 7), ("20:03:00", "20:02:00", "20:01:00", "20:00:00")):
        text = text.replace(f"2026-01-10 {stamp}", f"2026-01-{day:02d} {stamp}")
    target.write_text(text, encoding="utf-8")


class PeriodTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(period.clean({}), {"kind": "all"})
        self.assertEqual(period.clean({"kind": "last", "n": "500"}), {"kind": "last", "n": 500})
        self.assertEqual(period.clean({"kind": "days", "days": 30}), {"kind": "days", "days": 30})
        self.assertEqual(period.clean({"kind": "range", "from": "2026-02-01", "to": "2026-01-01"}),
                         {"kind": "range", "from": "2026-01-01", "to": "2026-02-01"})  # remises dans l'ordre
        self.assertEqual(period.clean({"kind": "range", "to": "2026-01-31"}),
                         {"kind": "range", "from": None, "to": "2026-01-31"})
        for bad in (None, [], {"kind": "x"}, {"kind": "last"}, {"kind": "last", "n": 0}, {"kind": "last", "n": True},
                    {"kind": "days", "days": -3}, {"kind": "range"}, {"kind": "range", "from": "31/01/2026"}):
            with self.assertRaises(ValueError):
                period.clean(bad)

    def test_select(self):
        hands = [SimpleNamespace(date=datetime(2026, 1, d, 20)) for d in range(1, 11)]
        self.assertIs(period.select(hands, {"kind": "all"}), hands)
        self.assertEqual(period.select(hands, {"kind": "last", "n": 3}), hands[-3:])
        self.assertEqual(period.select(hands, {"kind": "last", "n": 50}), hands)
        self.assertEqual(period.select(hands, {"kind": "days", "days": 7}, today=date(2026, 1, 10)), hands[3:])
        self.assertEqual(period.select(hands, {"kind": "days", "days": 1}, today=date(2026, 1, 12)), [])
        self.assertEqual(period.select(hands, {"kind": "range", "from": "2026-01-02", "to": "2026-01-04"}), hands[1:4])
        self.assertEqual(period.select(hands, {"kind": "range", "from": "2026-01-09", "to": None}), hands[8:])

    def test_labels(self):
        self.assertEqual(period.label({"kind": "all"}), "Toutes les mains")
        self.assertEqual(period.label({"kind": "last", "n": 1000}), "1\u202f000 dernières mains")
        self.assertEqual(period.label({"kind": "days", "days": 30}), "30 derniers jours")
        self.assertEqual(period.label({"kind": "range", "from": "2026-01-01", "to": "2026-01-31"}),
                         "Du 01/01/2026 au 31/01/2026")
        self.assertEqual(period.label({"kind": "range", "from": None, "to": "2026-01-31"}), "Jusqu'au 31/01/2026")
        self.assertEqual(period.describe({"kind": "all"}), "")
        self.assertIn("de chaque format", period.describe({"kind": "last", "n": 500}))
        self.assertEqual(period.describe({"kind": "range", "from": "2026-01-05", "to": None}),
                         "Période : depuis le 05/01/2026.")


class LibraryPeriodTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        spread_fixture(self.folder / "sample.txt")

    def test_period_in_library(self):
        lib = Library(self.folder)
        self.assertEqual(lib.summary()["period"]["kind"], "all")
        state = lib.set_period({"kind": "last", "n": 2})
        self.assertEqual((state["hands"], state["first"], state["last"]), (2, "09/01/2026", "10/01/2026"))
        self.assertEqual(state["period"]["label"], "2 dernières mains")
        self.assertEqual((state["period"]["all_hands"], state["period"]["first"], state["period"]["last"]),
                         (4, "2026-01-07", "2026-01-10"))
        self.assertEqual(state["opponents"][0]["hands"], 2)
        self.assertIn("2 dernières mains de chaque format", lib.leaks_page(standalone=True))
        self.assertEqual(len(lib.by_id), 4)  # chaque main reste ouvrable (solveur, replayer)
        self.assertEqual(Library(self.folder).summary()["hands"], 2)  # gardée dans la base
        state = lib.set_period({"kind": "range", "from": "2026-01-07", "to": "2026-01-08"})
        self.assertEqual((state["hands"], state["last"]), (2, "08/01/2026"))
        state = lib.set_period({"kind": "range", "from": "2027-01-01"})
        self.assertEqual((state["hands"], state["opponents"], state["period"]["all_hands"]), (0, [], 4))
        self.assertEqual(lib.set_kind("Villain", "rec")["hands"], 0)  # un joueur hors de la période reste réglable
        with self.assertRaises(ValueError):
            lib.set_period({"kind": "days", "days": 0})
        self.assertEqual(lib.set_period({"kind": "all"})["hands"], 4)

    def test_spaces_have_their_own_period(self):
        lib = Library(self.folder)
        lib.set_period({"kind": "last", "n": 1})
        other = Library(self.folder, space="eleve:paul", space_name="Paul")
        self.assertEqual((other.summary()["period"]["kind"], other.summary()["hands"]), ("all", 4))


class PeriodServerTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        spread_fixture(Path(tmp.name) / "sample.txt")
        self.server = start(Library(tmp.name), port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        body = None if payload is None else json.dumps(payload)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        conn.close()
        return resp.status, data

    def test_routes(self):
        status, data = self.request("GET", "/api/periode")
        self.assertEqual((status, data["kind"], data["all_hands"]), (200, "all", 4))
        status, data = self.request("POST", "/api/periode", {"kind": "days", "days": 36500})
        self.assertEqual((status, data["period"]["label"], data["hands"]), (200, "36\u202f500 derniers jours", 4))
        self.assertEqual(self.request("POST", "/api/periode", {"kind": "last", "n": "beaucoup"})[0], 400)
        self.assertEqual(self.request("POST", "/api/periode", {"kind": "last", "n": 1})[1]["hands"], 1)
        self.assertEqual(self.request("GET", "/api/state")[1]["hands"], 1)
        student = self.request("POST", "/api/eleves", {"name": "Paul"})[1]
        status, data = self.request("POST", f"/api/eleves/{student['id']}/periode", {"kind": "last", "n": 5})
        self.assertEqual((status, data["period"]["kind"]), (200, "last"))
        self.assertEqual(self.request("GET", f"/api/eleves/{student['id']}/periode")[1]["n"], 5)
        self.assertEqual(self.request("GET", "/api/periode")[1]["n"], 1)  # chacun la sienne


class CliPeriodTest(IsolatedHome):
    def test_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.txt"
            spread_fixture(sample)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli_main([str(sample), "--liste", "--depuis", "2026-01-09"]), 0)
            self.assertIn("2 mains HU (depuis le 09/01/2026)", out.getvalue())
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(cli_main([str(sample), "--liste", "--derniers", "2", "--jours", "3"]), 2)
                self.assertEqual(cli_main([str(sample), "--liste", "--depuis", "2030-01-01"]), 1)
            self.assertIn("Une seule période", err.getvalue())


if __name__ == "__main__":
    unittest.main()
