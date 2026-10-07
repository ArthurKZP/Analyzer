"""La base de données : schéma, import des historiques, lecture des mains, élèves, types d'adversaires, résumés du
solveur, reprise des anciens fichiers, copie d'une base à l'autre.

Les mêmes tests tournent sur PostgreSQL quand ANALYZER_TEST_PG désigne une base de test (effacée à chaque test !),
par exemple ANALYZER_TEST_PG=postgresql://analyzer@127.0.0.1:5432/analyzer_test."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analyzer import db, players, students
from analyzer.db import analyses, cli, hands, legacy, schema
from analyzer.parsers import load_hands

try:
    from .base import close_storage
except ImportError:  # lancé par « unittest discover -s tests »
    from base import close_storage

FIXTURES = Path(__file__).parent / "fixtures"
SITES = Path(__file__).parent / "sites"
SAMPLE = (FIXTURES / "betclic_sample.txt").read_text(encoding="utf-8")
PG = os.environ.get("ANALYZER_TEST_PG", "")


def reset_postgres(url: str) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


class DatabaseCase(unittest.TestCase):
    url = ""  # vide : SQLite dans le dossier du test

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        if self.url:
            reset_postgres(self.url)
        env = mock.patch.dict(os.environ, {"ANALYZER_HOME": str(self.home), "ANALYZER_DB": self.url})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(close_storage)

    @property
    def db(self):
        return db.current()


class SchemaAndHandsTest(DatabaseCase):
    def test_schema(self):
        self.assertEqual(schema.version(self.db), len(schema.MIGRATIONS))
        self.assertEqual(schema.migrate(self.db), len(schema.MIGRATIONS))  # rien à refaire
        self.assertEqual(self.db.account(), self.db.account())
        self.db.set_setting("essai", {"a": [1, "é"]})
        self.assertEqual(self.db.setting("essai"), {"a": [1, "é"]})
        with self.assertRaises(ZeroDivisionError):
            with self.db.transaction():
                self.db.set_setting("essai", 2)
                1 / 0
        self.assertEqual(self.db.setting("essai"), {"a": [1, "é"]})  # annulé

    def test_import_and_load(self):
        me = hands.space(self.db, "moi", "Moi")
        parsed, new, again = hands.import_text(self.db, me, "hu.txt", SAMPLE)
        self.assertEqual((len(parsed), new, again), (4, 4, False))
        self.assertEqual(hands.import_text(self.db, me, "copie.txt", SAMPLE)[1:], (0, True))  # même historique
        loaded = hands.load(self.db, me)
        self.assertEqual([h.__getstate__() for h in loaded], [h.__getstate__() for h in load_hands([FIXTURES])])
        self.assertEqual(self.db.all("SELECT joueur, position, cartes FROM participants p JOIN mains m ON m.id = "
                                     "p.main_id WHERE m.numero = 'HAND02' ORDER BY joueur"),
                         [("Hero", "BB", "QhJh"), ("Villain", "BTN", "Ts9s")])
        other = hands.space(self.db, "eleve:paul", "Paul")
        self.assertEqual(hands.import_text(self.db, other, "hu.txt", SAMPLE)[1], 4)  # chaque espace a ses mains
        self.assertEqual([s["key"] for s in hands.spaces(self.db)], ["eleve:paul", "moi"])
        with self.assertRaises(ValueError):
            hands.import_text(self.db, me, "x.txt", "PokerStars Hand #1")

    def test_folder_inbox_and_new_reader(self):
        me = hands.space(self.db, "moi")
        folder = self.root / "mains"
        folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", folder / "a.txt")
        self.assertEqual(hands.sync_folder(self.db, me, folder), 4)
        with mock.patch.object(hands, "import_text", side_effect=AssertionError("relu")):
            self.assertEqual(hands.sync_folder(self.db, me, folder), 0)  # inchangé : pas relu
        shutil.copy(SITES / "winamax.txt", folder / "b.txt")
        self.assertEqual(hands.sync_folder(self.db, me, folder), 2)
        self.assertEqual(hands.count(self.db, me), 6)
        import io
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Unibet/table.txt", (SITES / "unibet.txt").read_text(encoding="utf-8"))
        (folder / "lot.zip").write_bytes(archive.getvalue())
        self.assertEqual(hands.sync_folder(self.db, me, folder), 1)  # une archive déposée dans le dossier
        self.assertEqual(hands.sync_folder(self.db, me, folder), 0)
        self.assertEqual(hands.count(self.db, me), 7)
        partial = SAMPLE.split("*** HEADER ***")
        self.assertEqual(hands.import_text(self.db, me, "extrait.txt", "*** HEADER ***" + partial[1])[1:], (0, True))
        self.assertEqual(self.db.value("SELECT COUNT(*) FROM fichiers WHERE nom = 'extrait.txt'"), 0)  # rien de neuf
        with mock.patch.object(hands, "reader_version", return_value="nouveau-code"):
            self.assertEqual(len(hands.load(self.db, me)), 7)  # relues depuis le texte gardé
            self.assertEqual(self.db.value("SELECT COUNT(*) FROM mains WHERE lecture != 'nouveau-code'"), 0)


class AccountDataTest(DatabaseCase):
    def test_students_kinds_and_analyses(self):
        paul = students.create("Paul Élève", "PaulPoker")
        self.assertEqual((paul["id"], paul["pseudo"]), ("paul-eleve", "PaulPoker"))
        self.assertEqual(students.create("Paul Élève")["created"], paul["created"])
        self.assertEqual([s["name"] for s in students.all_students()], ["Paul Élève"])
        self.assertIsNone(students.get("../etudes"))
        players.set_kind("Lui", "rec")
        players.set_kind("Lui", "reg")
        self.assertEqual(players.load(), {"Lui": "reg"})
        analyses.put(self.db, "k1", {"hand": "H1", "decisions": [{"ev_loss": 0.5}]})
        analyses.put(self.db, "k1", {"hand": "H1", "decisions": []})  # remplacé
        self.assertEqual((analyses.count(self.db), analyses.keys(self.db)), (1, {"k1"}))
        self.assertEqual(analyses.get(self.db, "k1")["decisions"], [])
        self.assertIsNone(analyses.get(self.db, "absent"))

    def test_files_from_before_the_database(self):
        self.home.mkdir(parents=True)
        (self.home / "joueurs.json").write_text('{"Ancien": "rec", "Bizarre": "poisson"}', encoding="utf-8")
        (self.home / "revue").mkdir()
        (self.home / "revue" / "abc.json").write_text('{"hand": "H7"}', encoding="utf-8")
        (self.home / "eleves" / "anna").mkdir(parents=True)
        (self.home / "eleves" / "anna" / "eleve.json").write_text(
            json.dumps({"name": "Anna", "pseudo": None, "created": "2026-01-02T10:00:00"}), encoding="utf-8")
        self.assertEqual(players.load(), {"Ancien": "rec"})  # repris à la première ouverture
        self.assertEqual(analyses.get(self.db, "abc"), {"hand": "H7"})
        self.assertEqual(students.get("anna")["created"], "2026-01-02T10:00:00")
        self.assertEqual(legacy.import_once(self.db), {})  # une seule fois

    def test_copy_and_status(self):
        me = hands.space(self.db, "moi", "Moi")
        hands.import_text(self.db, me, "hu.txt", SAMPLE)
        analyses.put(self.db, "k1", {"hand": "HAND01"})
        target = db.Database(f"sqlite:///{self.root / 'copie.db'}")
        counts = cli.copy(self.db, target, log=lambda m: None)
        self.assertEqual((counts["mains"], counts["participants"], counts["analyses"]), (4, 8, 1))
        self.assertEqual(len(hands.load(target, me)), 4)
        new_space = hands.space(target, "eleve:neuf", "Neuf")  # les clés automatiques continuent après la copie
        self.assertGreater(new_space, me)
        with self.assertRaises(db.DatabaseError):
            cli.copy(self.db, target, log=lambda m: None)  # jamais par-dessus des données
        target.close()
        info = cli.status(self.db)
        self.assertEqual([(s["key"], s["hands"], s["files"]) for s in info["spaces"]], [("moi", 4, 1)])
        self.assertEqual(cli.masked("postgresql://moi:secret@hote:5432/b"), "postgresql://moi:***@hote:5432/b")


@unittest.skipUnless(PG, "ANALYZER_TEST_PG non défini : pas de base PostgreSQL de test")
class PostgresSchemaAndHandsTest(SchemaAndHandsTest):
    url = PG


@unittest.skipUnless(PG, "ANALYZER_TEST_PG non défini : pas de base PostgreSQL de test")
class PostgresAccountDataTest(AccountDataTest):
    url = PG

    def test_application_on_postgres(self):
        from analyzer.app.library import Library
        folder = self.root / "mains"
        folder.mkdir()
        shutil.copy(FIXTURES / "betclic_sample.txt", folder / "hu.txt")
        lib = Library(folder)
        try:
            self.assertEqual((lib.hero, len(lib.hands)), ("Hero", 4))
            for page in ("bilan", "preflop", "spots", "mains", "leaks"):
                self.assertIn("<", lib.self_page(page))
            self.assertEqual(lib.import_files([{"name": "w.txt", "content": (SITES / "winamax.txt").read_text()}])
                             ["added"], 2)
        finally:
            lib.solves.shutdown()


if __name__ == "__main__":
    unittest.main()
