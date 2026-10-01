"""Résolutions postflop lancées depuis l'application : une à la fois, en arrière-plan."""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from ..theory import postflop


@dataclass
class Job:
    key: str
    hand_id: str
    max_iterations: int
    target: float
    state: str = "waiting"  # waiting | running | done | error | cancelled
    progress: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    started: float = 0.0
    process: object = None
    cancelled: bool = False

    def view(self) -> dict:
        out = {"job": self.key, "hand": self.hand_id, "state": self.state, "progress": dict(self.progress),
               "max_iterations": self.max_iterations, "target": self.target}
        if self.state == "running":
            out["elapsed"] = round(time.time() - self.started, 1)
        if self.result is not None:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        return out


class SolveQueue:
    def __init__(self, iterations: int = postflop.DEFAULT_ITERATIONS, target: float = postflop.DEFAULT_TARGET):
        self.iterations, self.target = iterations, target
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gtopen")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _request(self, spot: postflop.PostflopSpot) -> dict:
        return spot.request(self.iterations, self.target)

    def lookup(self, spot: postflop.PostflopSpot) -> dict:
        """Résultat en cache ou résolution en cours pour ce spot, sans rien lancer."""
        request = self._request(spot)
        key = postflop.cache_key(request)
        with self._lock:
            job = self._jobs.get(key)
        if job and job.state in ("waiting", "running"):
            return job.view()
        raw = postflop.cached(request)
        if raw is not None:
            return {"job": key, "hand": spot.hand.hand_id, "state": "done", "result": postflop.interpret(spot, raw)}
        if job and job.state == "error":
            return job.view()
        return {"job": key, "hand": spot.hand.hand_id, "state": "absent"}

    def start(self, spot: postflop.PostflopSpot) -> dict:
        view = self.lookup(spot)
        if view["state"] in ("waiting", "running", "done"):
            return view
        request = self._request(spot)
        job = Job(view["job"], spot.hand.hand_id, self.iterations, self.target)
        with self._lock:
            self._jobs[job.key] = job
        self._executor.submit(self._run, job, spot, request)
        return job.view()

    def _run(self, job: Job, spot: postflop.PostflopSpot, request: dict) -> None:
        if job.cancelled:
            return
        job.state, job.started = "running", time.time()
        try:
            raw = postflop.solve(request, on_progress=job.progress.update,
                                 on_start=lambda proc: setattr(job, "process", proc))
            job.result = postflop.interpret(spot, raw)
            job.state = "done"
        except postflop.SolverError as exc:
            job.state = "cancelled" if job.cancelled else "error"
            job.error = None if job.cancelled else str(exc)
        except Exception:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            job.state, job.error = "error", "Erreur inattendue pendant la résolution (détails dans le terminal)."
        finally:
            job.process = None

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(key)
        return job.view() if job else None

    def cancel(self, key: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(key)
        if job is None:
            return None
        job.cancelled = True
        if job.state == "waiting":
            job.state = "cancelled"
        proc = job.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return job.view()

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.state in ("waiting", "running"):
                self.cancel(job.key)
        self._executor.shutdown(wait=False, cancel_futures=True)
