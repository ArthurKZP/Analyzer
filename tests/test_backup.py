import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from analyzer import backup
from analyzer.app import backups as app_backups

POSIX = os.name != "nt"

# Faux rclone : « nom:chemin » est un dossier sous $FAKE_REMOTE (copyto, copy, lsf, deletefile).
FAKE_RCLONE = r'''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
root = Path(os.environ["FAKE_REMOTE"])
def p(x):
    return root / x.split(":", 1)[1] if ":" in x and not x.startswith("/") else Path(x)
cmd, *args = sys.argv[1:]
flags = [a for a in args if a.startswith("-")]
args = [a for a in args if not a.startswith("-") and a != "*.etude"]
if cmd == "copyto":
    p(args[1]).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p(args[0]), p(args[1]))
elif cmd == "copy":
    src, dst = p(args[0]), p(args[1]); dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.etude")):
        if "--ignore-existing" in flags and (dst / f.name).exists():
            continue
        shutil.copy2(f, dst / f.name); print(f"INFO  : {f.name}: Copied (new)", file=sys.stderr)
elif cmd == "lsf":
    d = p(args[0])
    if not d.is_dir():
        print("directory not found", file=sys.stderr); sys.exit(3)
    print("\n".join(sorted(f.name for f in d.iterdir())))
elif cmd == "deletefile":
    p(args[0]).unlink()
'''


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.env = mock.patch.dict(os.environ, {"ANALYZER_HOME": str(self.home)})
        self.env.start()
        self.write("tailles/srp-KsKd4c.json", '{"choix": 1}')
        self.write("revue/abc.json", '{"hand": "H1"}')
        self.write("resolutions/r.json", "{}")
        self.write("entrainement/journal.jsonl", '{"n": 1}\n{"n": 2}\n')
        self.write("etudes/k1.json", '{"kind": "spot"}')
        self.write("etudes/k1.etude", "x" * 1000)
        self.write("solveur/main.rs", "// pas sauvegardé")
        self.write("joueurs.json", '{"Lui": "rec"}')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, rel, text, home=None):
        path = (home or self.home) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_folder_backup_and_restore(self):
        dest = self.root / "OneDrive" / "Analyzer"
        with self.assertRaises(backup.BackupError):
            backup.backup()  # aucune destination choisie
        with self.assertRaises(backup.BackupError):
            backup.backup(str(self.home / "dedans"))  # dans le dossier d'Analyzer lui-même
        backup.save_config(dest=str(dest), studies=True)
        last = backup.backup(log=lambda m: None)
        self.assertEqual((last["studies"], last["with_studies"]), (1, True))
        archives = list((dest / "archives").glob("analyzer-*.zip"))
        self.assertEqual(len(archives), 1)
        names = set(zipfile.ZipFile(archives[0]).namelist())
        self.assertEqual(names, {"tailles/srp-KsKd4c.json", "revue/abc.json", "resolutions/r.json",
                                 "entrainement/journal.jsonl", "etudes/k1.json", "joueurs.json"})
        self.assertEqual((dest / "etudes" / "k1.etude").stat().st_size, 1000)
        self.assertEqual(backup.load_config()["last"]["archive"], archives[0].name)
        self.assertEqual(backup.backup(log=lambda m: None)["studies"], 0)  # rien de neuf à copier

        # Autre ordinateur : rien en local, puis restauration.
        other = self.root / "autre"
        with mock.patch.dict(os.environ, {"ANALYZER_HOME": str(other)}):
            self.write("tailles/srp-KsKd4c.json", '{"choix": "local plus récent"}', home=other)
            future = time.time() + 3600
            os.utime(other / "tailles/srp-KsKd4c.json", (future, future))
            self.write("entrainement/journal.jsonl", '{"n": 2}\n{"n": 3}\n', home=other)
            result = backup.restore(str(dest), log=lambda m: None)
            self.assertEqual((result["kept"], result["studies"]), (1, 1))
            self.assertIn("local plus récent", (other / "tailles/srp-KsKd4c.json").read_text())
            self.assertEqual((other / "revue/abc.json").read_text(), '{"hand": "H1"}')
            journal = (other / "entrainement/journal.jsonl").read_text().splitlines()
            self.assertEqual(journal, ['{"n": 2}', '{"n": 3}', '{"n": 1}'])  # fusionné, sans doublon
            self.assertTrue((other / "etudes/k1.etude").is_file())

    def test_keeps_last_archives_and_ignores_unsafe_members(self):
        dest = self.root / "bk"
        (dest / "archives").mkdir(parents=True)
        for k in range(12):
            (dest / "archives" / f"analyzer-2026010{k:02d}-000000.zip").write_bytes(b"")
        backup.backup(str(dest), studies=False, log=lambda m: None)
        self.assertEqual(len(list((dest / "archives").glob("*.zip"))), backup.KEEP)
        self.assertFalse((dest / "etudes").exists())
        evil = dest / "archives" / "analyzer-99999999-000000.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../evade.txt", "non")
            z.writestr("tailles/neuf.json", "{}")
        backup.restore(str(dest), studies=False, log=lambda m: None)
        self.assertFalse((self.root / "evade.txt").exists())
        self.assertTrue((self.home / "tailles/neuf.json").is_file())

    def test_remote_detection(self):
        for dest in ("gdrive:Analyzer", "s3:bucket/analyzer", "mon serveur:"):
            self.assertTrue(backup.is_remote(dest), dest)
        for dest in ("C:\\Users\\toi\\OneDrive", "D:/sauvegardes", "/home/toi/Dropbox", "~/Dropbox", "sauvegardes"):
            self.assertFalse(backup.is_remote(dest), dest)

    @unittest.skipUnless(POSIX, "faux rclone : script exécutable POSIX")
    def test_rclone_remote(self):
        bin_dir, remote = self.root / "bin", self.root / "remote"
        bin_dir.mkdir()
        exe = bin_dir / "rclone"
        exe.write_text(FAKE_RCLONE.replace("#!/usr/bin/env python3", "#!" + sys.executable))
        exe.chmod(0o755)
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_REMOTE": str(remote)}
        with mock.patch.dict(os.environ, env):
            last = backup.backup("gdrive:Analyzer", studies=True, log=lambda m: None)
            self.assertEqual(last["studies"], 1)
            self.assertEqual(len(list((remote / "Analyzer" / "archives").glob("*.zip"))), 1)
            self.assertTrue((remote / "Analyzer" / "etudes" / "k1.etude").is_file())
            (self.home / "revue/abc.json").unlink()
            (self.home / "etudes/k1.etude").unlink()
            result = backup.restore("gdrive:Analyzer", log=lambda m: None)
            self.assertEqual(result["studies"], 1)
            self.assertTrue((self.home / "revue/abc.json").is_file())
            with self.assertRaises(backup.BackupError):
                backup.restore("vide:rien", log=lambda m: None)
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "nulle-part")}):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.backup("gdrive:Analyzer", log=lambda m: None)
            self.assertIn("rclone", str(ctx.exception))

    def test_cli(self):
        dest = self.root / "cli"
        self.assertEqual(backup.main([str(dest), "--etudes", "--auto"]), 0)
        config = backup.load_config()
        self.assertEqual((config["dest"], config["studies"], config["auto"]), (str(dest), True, True))
        self.assertTrue((dest / "etudes" / "k1.etude").is_file())
        self.assertEqual(backup.main(["--sans-auto"]), 0)
        self.assertFalse(backup.load_config()["auto"])
        self.assertEqual(backup.main(["--restaurer"]), 0)
        self.assertEqual(backup.main([str(self.root / "vide"), "--restaurer"]), 1)

    def test_app_manager_and_auto(self):
        manager = app_backups.Backups()
        view = manager.view()
        self.assertEqual((view["dest"], view["studies_count"], view["studies_size"]), ("", 1, 1000))
        manager.configure(str(self.root / "auto"), False, True)
        with mock.patch.object(app_backups, "AUTO_DELAY", 0.05), mock.patch.object(app_backups, "AUTO_INTERVAL", 0):
            manager.schedule()
            manager.schedule()  # regroupée avec la précédente
            deadline = time.time() + 10
            while not backup.load_config()["last"] and time.time() < deadline:
                time.sleep(0.05)
        while manager.running and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(list((self.root / "auto" / "archives").glob("*.zip"))), 1)
        manager.configure("", False, False)
        manager.start()
        while manager.running:
            time.sleep(0.05)
        self.assertIn("Choisis d'abord", manager.view()["error"])
        manager.shutdown()
        json.dumps(manager.view())


if __name__ == "__main__":
    unittest.main()
