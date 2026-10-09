"""L'avancement d'un import d'historiques (la barre de la page Importer).

Deux étapes : lire les historiques (chaque main est lue puis enregistrée dans la base), puis relire toutes les mains de
l'espace pour mettre les analyses à jour (seulement s'il y a des mains nouvelles). L'avancement se compte en mains :
celles des historiques, comptées avant de les lire, puis celles de l'espace ; lire et enregistrer une main coûte environ
READ_COST fois plus que la relire (mesuré : 0,46 ms contre 0,12 ms), et les analyses recalculées après la relecture
environ FINISH_COST fois la relecture.
"""
from __future__ import annotations

import threading
from typing import Optional

READ_COST = 4
FINISH_COST = 0.35


class ImportJob:
    def __init__(self, job_id: str, known: int):
        self.id = job_id
        self.state = "running"  # puis « done » (result) ou « error » (error)
        self.step = "lecture"   # lecture des historiques, puis « analyses » (mise à jour)
        self.files_done = self.files_total = 0
        self.hands_read = self.hands_total = 0  # mains des historiques, lues sur comptées
        self.loaded, self.to_load = 0, known    # mains de l'espace relues (estimé : celles d'avant + les nouvelles)
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self._finished = 0  # mains des historiques déjà lus
        self._lock = threading.Lock()

    def plan(self, counts: list[int]) -> None:
        """Les historiques à lire et le nombre de mains de chacun."""
        with self._lock:
            self.files_total, self.hands_total = len(counts), sum(counts)
            self.to_load += self.hands_total

    def reading(self, n: int) -> None:
        """n mains lues dans l'historique en cours."""
        with self._lock:
            self.hands_read = min(self._finished + n, self.hands_total)

    def file_done(self, hands: int) -> None:
        """Un historique de plus (ses mains, comptées comme dans plan)."""
        with self._lock:
            self.files_done += 1
            self._finished += hands
            self.hands_read = min(self._finished, self.hands_total)

    def loading(self, k: int, n: int) -> None:
        """Mise à jour des analyses : k mains relues sur n."""
        with self._lock:
            self.step, self.loaded, self.to_load = "analyses", k, n

    def finish(self, result: dict) -> None:
        with self._lock:
            self.state, self.result = "done", result

    def fail(self, message: str) -> None:
        with self._lock:
            self.state, self.error = "error", message

    def view(self) -> dict:
        """Pour la page : l'étape, l'avancement (0 à 1), les historiques et les mains, puis le résultat."""
        with self._lock:
            work = self.hands_total * READ_COST + self.to_load * (1 + FINISH_COST)
            done = self.hands_read * READ_COST + min(self.loaded, self.to_load)
            if self.state == "done":
                progress = 1.0
            else:  # jamais 100 % avant la fin
                progress = min(done / work, 0.99) if work else 0.0
            return {"id": self.id, "state": self.state, "step": self.step, "progress": round(progress, 3),
                    "files": [self.files_done, self.files_total], "hands": [self.hands_read, self.hands_total],
                    "loaded": [self.loaded, self.to_load], "result": self.result, "error": self.error}
