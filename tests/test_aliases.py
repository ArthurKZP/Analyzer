import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from analyzer import aliases, players
from analyzer.app.library import Library
from analyzer.app.server import start
from analyzer.parsers import parse_text

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


class AliasTest(IsolatedHome):
    def test_group_and_ungroup(self):
        self.assertEqual(aliases.groups(), {})
        self.assertEqual(aliases.group(" Paul  B ", ["joueur1", "Joueur2"]), {"Paul B": ["joueur1", "Joueur2"]})
        self.assertEqual(aliases.load(), {"joueur1": "Paul B", "Joueur2": "Paul B"})
        # un alias regroupé dans un autre : tous ses pseudos suivent, l'ancien nom aussi
        self.assertEqual(aliases.group("Pierre", ["Paul B", "joueur3"]),
                         {"Pierre": ["joueur1", "Joueur2", "joueur3", "Paul B"]})
        self.assertEqual(aliases.group("Jean", ["Pierre"])["Jean"], ["joueur1", "Joueur2", "joueur3", "Paul B", "Pierre"])
        # l'alias peut garder le nom d'un de ses pseudos
        self.assertEqual(aliases.group("joueur1", ["Jean"]),
                         {"joueur1": ["Jean", "Joueur2", "joueur3", "Paul B", "Pierre"]})
        self.assertEqual(aliases.ungroup("joueur1"), {})
        self.assertEqual(aliases.load(), {})

    def test_invalid(self):
        for alias, pseudos in (("", ["a"]), ("x" * 61, ["a"]), ("Paul", []), ("Paul", ["Paul"]), ("Paul", [3]),
                               ("Paul", [""])):
            with self.assertRaises(ValueError):
                aliases.group(alias, pseudos)
        self.assertEqual(aliases.load(), {})

    def test_kind_follows(self):
        players.set_kind("joueur2", "rec")
        aliases.group("Paul", ["joueur1", "joueur2"])
        self.assertEqual(players.load().get("Paul"), "rec")
        players.set_kind("Pierre", "reg")  # un type déjà choisi pour l'alias reste
        aliases.group("Pierre", ["joueur2"])
        self.assertEqual(players.load().get("Pierre"), "reg")

    def test_apply(self):
        hands = parse_text(FIXTURE.read_text(encoding="utf-8"))
        aliases.apply(hands, {"Villain": "Le Vilain", "Hero": "Moi"})
        for h in hands:
            self.assertEqual(set(h.seats), {"Moi", "Le Vilain"})
            self.assertEqual(h.hero, "Moi")
            self.assertTrue(all(a.player in ("Moi", "Le Vilain") for a in h.actions))


class LibraryAliasTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        shutil.copy(FIXTURE, self.folder / "sample.txt")

    def test_alias_in_library(self):
        lib = Library(self.folder)
        result = lib.set_alias("Le Vilain", ["Villain"])
        self.assertEqual(result["aliases"], {"Le Vilain": ["Villain"]})
        self.assertEqual([o["name"] for o in result["state"]["opponents"]], ["Le Vilain"])
        self.assertEqual(result["state"]["aliases"], {"Le Vilain": ["Villain"]})
        self.assertIn("Plan de jeu", lib.player_page("Le Vilain", "plan"))
        page = lib.field_page("joueurs")
        self.assertIn('class="al-pick" value="Le Vilain"', page)
        self.assertIn('title="Pseudos : Villain"', page)
        self.assertIn('data-alias="Le Vilain"', page)  # de quoi le défaire
        self.assertEqual(Library(self.folder).summary()["opponents"][0]["name"], "Le Vilain")  # gardé dans la base
        state = lib.remove_alias("Le Vilain")["state"]
        self.assertEqual([o["name"] for o in state["opponents"]], ["Villain"])
        self.assertNotIn("data-alias=", lib.field_page("joueurs"))

    def test_hero_alias(self):
        lib = Library(self.folder, hero="Hero")
        lib.set_alias("Moi", ["Hero"])
        self.assertEqual(lib.summary()["hero"], "Moi")
        self.assertEqual(lib.summary()["hands"], 4)


class AliasServerTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
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
        self.assertEqual(self.request("GET", "/api/alias"), (200, {}))
        status, data = self.request("POST", "/api/alias", {"alias": "Le Vilain", "pseudos": ["Villain"]})
        self.assertEqual((status, data["aliases"]), (200, {"Le Vilain": ["Villain"]}))
        self.assertEqual(data["state"]["opponents"][0]["name"], "Le Vilain")
        self.assertEqual(self.request("GET", "/api/alias"), (200, {"Le Vilain": ["Villain"]}))
        self.assertEqual(self.request("POST", "/api/alias", {"alias": "", "pseudos": ["Villain"]})[0], 400)
        self.assertEqual(self.request("POST", "/api/alias", {"alias": "X", "pseudos": "Villain"})[0], 400)
        self.assertEqual(self.request("POST", "/api/alias", {"alias": "X", "pseudos": ["p"] * 51})[0], 400)
        status, data = self.request("POST", "/api/alias/defaire", {"alias": "Le Vilain"})
        self.assertEqual((status, data["aliases"]), (200, {}))
        self.assertEqual(data["state"]["opponents"][0]["name"], "Villain")
        self.assertEqual(self.request("POST", "/api/alias/defaire", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
