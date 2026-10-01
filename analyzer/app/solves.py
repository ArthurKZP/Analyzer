"""Résolutions postflop lancées depuis l'application : une à la fois, en arrière-plan.

La dernière résolution reste en mémoire (session) pour que l'explorateur puisse naviguer dans
tout l'arbre ; elle est fermée après IDLE_TIMEOUT sans requête, ou quand une autre commence.
Sans session, l'explorateur se limite aux nœuds de la ligne jouée, enregistrés dans le cache.
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from ..theory import postflop

IDLE_TIMEOUT = 30 * 60  # secondes sans requête avant de libérer la mémoire de la session


class NeedSession(LookupError):
    """Ce nœud n'est pas dans la ligne jouée : il faut une session (re-résolution)."""


@dataclass
class Job:
    key: str
    hand_id: str
    max_iterations: int
    target: float
    state: str = "waiting"  # waiting | running | done | error | cancelled
    mode: str = "solve"  # solve (résolution) | load (étude enregistrée)
    progress: dict = field(default_factory=dict)
    result: Optional[dict] = None
    error: Optional[str] = None
    started: float = 0.0
    process: object = None
    cancelled: bool = False

    def view(self) -> dict:
        out = {"job": self.key, "hand": self.hand_id, "state": self.state, "progress": dict(self.progress),
               "max_iterations": self.max_iterations, "target": self.target, "mode": self.mode}
        if self.state == "running":
            out["elapsed"] = round(time.time() - self.started, 1)
        if self.result is not None:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        return out


class SolveQueue:
    def __init__(self, iterations: int = postflop.DEFAULT_ITERATIONS, target: float = postflop.DEFAULT_TARGET,
                 idle_timeout: float = IDLE_TIMEOUT):
        self.iterations, self.target, self.idle_timeout = iterations, target, idle_timeout
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gtopen")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._live: Optional[tuple[str, postflop.Session]] = None  # (main, session navigable)
        self._stop = threading.Event()
        threading.Thread(target=self._reap_idle, daemon=True, name="gtopen-idle").start()

    def _request(self, spot: postflop.PostflopSpot) -> dict:
        return spot.request(self.iterations, self.target)

    # --- session navigable ------------------------------------------------------
    def live_session(self, hand_id: str) -> Optional[postflop.Session]:
        with self._lock:
            live = self._live
        if live and live[0] == hand_id and live[1].alive:
            return live[1]
        return None

    def _set_live(self, hand_id: str, session: Optional[postflop.Session]) -> None:
        with self._lock:
            old, self._live = self._live, (hand_id, session) if session else None
        if old and old[1] is not session:
            old[1].close()

    def _reap_idle(self) -> None:
        while not self._stop.wait(30):
            with self._lock:
                live = self._live
            if live and time.time() - live[1].last_used > self.idle_timeout:
                self._set_live(live[0], None)

    # --- résolutions ------------------------------------------------------------
    def lookup(self, spot: postflop.PostflopSpot) -> dict:
        """Résultat en cache ou résolution en cours pour ce spot, sans rien lancer."""
        request = self._request(spot)
        key = postflop.cache_key(request)
        hand_id = spot.hand.hand_id
        live = self.live_session(hand_id) is not None
        study = postflop.study_path(request).is_file()
        with self._lock:
            job = self._jobs.get(key)
        if job and job.state in ("waiting", "running"):
            return dict(job.view(), live=False, study=study)
        raw = postflop.cached(request)
        if raw is not None:
            return {"job": key, "hand": hand_id, "state": "done", "live": live, "study": study,
                    "result": postflop.interpret(spot, raw)}
        if job and job.state == "error":
            return dict(job.view(), live=False, study=study)
        return {"job": key, "hand": hand_id, "state": "absent", "live": False, "study": study}

    def start(self, spot: postflop.PostflopSpot, force: bool = False) -> dict:
        """Lance la résolution ; force=True la relance même en cache, pour rouvrir une session."""
        view = self.lookup(spot)
        if view["state"] in ("waiting", "running") or view["state"] == "done" and (view["live"] or not force):
            return view
        request = self._request(spot)
        job = Job(view["job"], spot.hand.hand_id, self.iterations, self.target,
                  mode="load" if postflop.study_path(request).is_file() else "solve")
        with self._lock:
            self._jobs[job.key] = job
        self._executor.submit(self._run, job, spot, request)
        return dict(job.view(), live=False, study=job.mode == "load")

    def _run(self, job: Job, spot: postflop.PostflopSpot, request: dict) -> None:
        if job.cancelled:
            return
        self._set_live("", None)  # libère la mémoire de la session précédente
        job.state, job.started = "running", time.time()
        session = postflop.Session(request)
        try:
            raw = session.start(on_progress=job.progress.update, on_start=lambda proc: setattr(job, "process", proc))
            if not session.loading and session.study.is_file():
                postflop.write_study_meta(spot, request, raw)
            job.result = postflop.interpret(spot, raw)
            self._set_live(spot.hand.hand_id, session)
            job.state = "done"
        except postflop.SolverError as exc:
            session.close()
            job.state = "cancelled" if job.cancelled else "error"
            job.error = None if job.cancelled else str(exc)
        except Exception:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            session.close()
            traceback.print_exc()
            job.state, job.error = "error", "Erreur inattendue pendant la résolution (détails dans le terminal)."
        finally:
            job.process = None

    def node(self, spot: postflop.PostflopSpot, path: list) -> dict:
        """Nœud au bout du chemin : depuis la session si elle est ouverte, sinon depuis le cache."""
        session = self.live_session(spot.hand.hand_id)
        if session is not None:
            return {"node": session.node(path), "live": True}
        raw = postflop.cached(self._request(spot))
        node = postflop.cached_node(raw, path) if raw else None
        if node is None:
            raise NeedSession(spot.hand.hand_id)
        return {"node": node, "live": False}

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
        self._stop.set()
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.state in ("waiting", "running"):
                self.cancel(job.key)
        self._set_live("", None)
        self._executor.shutdown(wait=False, cancel_futures=True)
