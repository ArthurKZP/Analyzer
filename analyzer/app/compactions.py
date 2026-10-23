"""La compression des études en arrière-plan : au lancement de l'application et après chaque calcul, les études
encore sous leur forme rapide passent une à une sous leur forme compacte, sans perte (postflop.compact_study :
35 à 45 % de place en moins)."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable, Optional

from .. import blobs
from ..theory import postflop

STALE = 3600  # secondes : une copie en cours (<clé>.etude.compact) plus vieille est un reste d'arrêt brutal


class Compactions:
    def __init__(self):
        self._lock = threading.Lock()
        self._again = False
        self._stop = threading.Event()
        self.running: Optional[str] = None  # la clé de l'étude en cours de compression
        self.done = 0                       # études compressées depuis le lancement
        self.saved = 0                      # octets gagnés depuis le lancement
        self.error: Optional[str] = None
        self.on_done: list[Callable[[], None]] = []  # après un passage qui a compressé au moins une étude

    def ready(self) -> bool:
        """Le pont sait compresser (installé et à jour)."""
        state = postflop.status()
        return state["ready"] and not state["outdated"]

    def start(self) -> None:
        """Lance la compression des études en attente (ou la relance après celle en cours)."""
        if not self.ready():
            return
        with self._lock:
            if self.running is not None:
                self._again = True
                return
            self.running = ""
        threading.Thread(target=self._run, daemon=True, name="etudes-compression").start()

    def _run(self) -> None:
        failed: set[str] = set()
        before = self.done
        try:
            self._clean()
            while not self._stop.is_set():
                todo = [key for key in postflop.to_compact() if key not in failed]
                if not todo:
                    with self._lock:
                        if not self._again:
                            break
                        self._again = False
                    continue
                key = self.running = todo[0]
                try:
                    result = postflop.compact_study(key)
                    self.done += 1
                    self.saved += max(0, result.get("avant", 0) - result.get("apres", 0))
                except (postflop.SolverError, OSError, ValueError) as exc:
                    failed.add(key)  # (réessayée au prochain lancement)
                    self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            self.error = f"Erreur inattendue : {exc}"
        finally:
            with self._lock:
                self.running, self._again = None, False
        if self.done > before:
            for callback in self.on_done:
                try:
                    callback()
                except Exception:  # noqa: BLE001 — un rappel ne doit pas arrêter la compression
                    traceback.print_exc()

    @staticmethod
    def _clean() -> None:
        """Les copies laissées par une compression interrompue (arrêt brutal de l'application)."""
        root = blobs.studies().root
        if not root.is_dir():
            return
        for path in root.glob("*.etude.compact"):
            try:
                if time.time() - path.stat().st_mtime > STALE:
                    path.unlink()
            except OSError:
                pass

    def busy(self) -> bool:
        """Une compression est en cours."""
        return self.running is not None

    def view(self) -> dict:
        return {"ready": self.ready(), "running": self.running is not None, "pending": len(postflop.to_compact()),
                "done": self.done, "saved": self.saved, "error": self.error}

    def shutdown(self) -> None:
        self._stop.set()
