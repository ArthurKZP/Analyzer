"""Sauvegardes lancées depuis l'application : une à la fois, en arrière-plan ; automatiques après les
calculs si les réglages le demandent (au plus une toutes les AUTO_INTERVAL secondes)."""
from __future__ import annotations

import shutil
import threading
import time
import traceback
from typing import Optional

from .. import backup

AUTO_DELAY = 120          # secondes après la fin d'un calcul (d'autres suivent souvent)
AUTO_INTERVAL = 15 * 60   # entre deux sauvegardes automatiques


class Backups:
    def __init__(self):
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self.running: Optional[str] = None  # "backup" | "restore"
        self.log: list[str] = []
        self.error: Optional[str] = None
        self.result: Optional[dict] = None

    def view(self) -> dict:
        config = backup.load_config()
        essentials = sum(f.stat().st_size for f in backup.essential_files())
        studies = backup.study_files()
        return dict(config, running=self.running, log=self.log[-8:], error=self.error, result=self.result,
                    rclone=bool(shutil.which("rclone")), home=str(backup.home()), essentials_size=essentials,
                    studies_count=len(studies), studies_size=sum(f.stat().st_size for f in studies))

    def configure(self, dest: str, studies: bool, auto: bool) -> dict:
        backup.save_config(dest=dest.strip(), studies=studies, auto=auto)
        return self.view()

    def start(self, kind: str = "backup") -> dict:
        with self._lock:
            if self.running:
                return self.view()
            self.running, self.log, self.error, self.result = kind, [], None, None
        threading.Thread(target=self._run, args=(kind,), daemon=True, name="sauvegarde").start()
        return self.view()

    def _run(self, kind: str) -> None:
        try:
            if kind == "restore":
                self.result = backup.restore(log=self.log.append)
            else:
                self.result = backup.backup(log=self.log.append)
        except backup.BackupError as exc:
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            self.error = f"Erreur inattendue : {exc}"
        finally:
            self.running = None

    def schedule(self) -> None:
        """Après un calcul : sauvegarde automatique (si activée), regroupée avec les calculs qui suivent."""
        config = backup.load_config()
        if not config["auto"] or not config["dest"]:
            return
        with self._lock:
            if self._timer is not None and self._timer.is_alive():
                return
            last = (config.get("last") or {}).get("t", 0)
            delay = max(AUTO_DELAY, last + AUTO_INTERVAL - time.time())
            self._timer = threading.Timer(delay, self.start)
            self._timer.daemon = True
            self._timer.start()

    def shutdown(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
