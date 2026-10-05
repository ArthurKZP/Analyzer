"""Résolutions postflop lancées depuis l'application : une à la fois, en arrière-plan (une étude
enregistrée se rouvre à part, sans attendre la fin des résolutions en file).

La dernière résolution reste en mémoire (session) pour que l'explorateur puisse naviguer dans
tout l'arbre ; elle est fermée après IDLE_TIMEOUT sans requête, ou quand une autre commence.
Sans session, l'explorateur se limite aux nœuds de la ligne jouée, enregistrés dans le cache.
"""
from __future__ import annotations

import hashlib
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

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
    mode: str = "solve"  # solve | load (étude enregistrée) | choose (choix des tailles) | analyse (main jouée)
    #                      | plan (extraction du plan de jeu d'une étude)
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
        # Rouvrir une étude (quelques secondes) ne fait pas la queue derrière une série de résolutions.
        self._loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gtopen-etude")
        self._jobs: dict[str, Job] = {}
        self._series: dict[str, str] = {}  # spot -> tâche « choisir les tailles puis résoudre »
        self._plans: dict[str, str] = {}  # spot -> tâche « extraire le plan de jeu »
        self._lock = threading.Lock()
        self._live: Optional[tuple[str, postflop.Session]] = None  # (main, session navigable)
        self._stop = threading.Event()
        self.on_done: list[Callable[[Job], None]] = []  # après chaque calcul terminé (pas une étude rouverte)
        threading.Thread(target=self._reap_idle, daemon=True, name="gtopen-idle").start()

    def _finished(self, job: Job) -> None:
        for callback in self.on_done:
            try:
                callback(job)
            except Exception:  # noqa: BLE001 — un rappel ne doit pas faire échouer la résolution
                traceback.print_exc()

    def _request(self, spot) -> dict:
        """spot : une main jouée (postflop.PostflopSpot) ou un spot d'étude (studyspots.StudySpot)."""
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
    def lookup(self, spot) -> dict:
        """Résultat en cache ou résolution en cours pour ce spot, sans rien lancer."""
        request = self._request(spot)
        key = postflop.cache_key(request)
        hand_id = spot.ident
        live = self.live_session(hand_id) is not None
        study = postflop.study_path(request).is_file()
        with self._lock:
            job = self._jobs.get(key)
            series = self._jobs.get(self._series.get(hand_id, ""))
        if series and series.state in ("waiting", "running"):
            return dict(series.view(), live=False, study=study)
        if job and job.state in ("waiting", "running"):
            return dict(job.view(), live=False, study=study)
        raw = postflop.cached(request)
        if raw is not None:
            return {"job": key, "hand": hand_id, "state": "done", "live": live, "study": study,
                    "result": spot.interpret(raw)}
        if job and job.state == "error":
            return dict(job.view(), live=False, study=study)
        return {"job": key, "hand": hand_id, "state": "absent", "live": False, "study": study}

    def start(self, spot, force: bool = False, keep_live: bool = True) -> dict:
        """Lance la résolution ; force=True la relance même en cache, pour rouvrir une session.

        keep_live=False (résolutions en lot) : la session est fermée dès l'étude enregistrée."""
        view = self.lookup(spot)
        if view["state"] in ("waiting", "running") or view["state"] == "done" and (view["live"] or not force):
            return view
        request = self._request(spot)
        job = Job(view["job"], spot.ident, self.iterations, self.target,
                  mode="load" if postflop.study_path(request).is_file() else "solve")
        with self._lock:
            self._jobs[job.key] = job
        (self._loader if job.mode == "load" else self._executor).submit(self._run, job, spot, request, keep_live)
        return dict(job.view(), live=False, study=job.mode == "load")

    def choose_and_solve(self, ident: str, choose: Callable[[Job], None], make_spot: Callable[[], object]) -> dict:
        """Choisit d'abord les tailles du spot (long), puis le résout sans garder de session.

        choose(job) fait le choix (job.progress["stage"] : l'étape en cours ; job.process : le programme
        lancé ; job.cancelled : l'arrêt demandé) ; make_spot() rend ensuite le spot avec ses tailles."""
        with self._lock:
            current = self._jobs.get(self._series.get(ident, ""))
            if current and current.state in ("waiting", "running"):
                return current.view()
            key = "choix-" + hashlib.sha256(ident.encode()).hexdigest()[:14]
            job = Job(key, ident, self.iterations, self.target, mode="choose")
            self._jobs[key] = job
            self._series[ident] = key
        self._executor.submit(self._run_series, job, choose, make_spot)
        return job.view()

    def _run_series(self, job: Job, choose: Callable[[Job], None], make_spot: Callable[[], object]) -> None:
        if job.cancelled:
            return
        job.state, job.started = "running", time.time()
        try:
            choose(job)
        except postflop.SolverError as exc:
            job.state = "cancelled" if job.cancelled else "error"
            job.error = None if job.cancelled else str(exc)
            return
        except Exception:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            job.state, job.error = "error", "Erreur inattendue pendant le choix des tailles (détails dans le terminal)."
            return
        finally:
            job.process = None
        spot = make_spot()
        job.mode = "solve"
        job.progress.clear()
        self._run(job, spot, self._request(spot), keep_live=False)

    def analyze(self, spot, on_done: Callable[[object, dict], None]) -> dict:
        """Résout une main jouée pour l'analyse : le résultat seul (cache), sans étude ni session ;
        on_done(spot, raw) le range ensuite (résumé de la main)."""
        view = self.lookup(spot)
        if view["state"] in ("waiting", "running", "done"):
            return view
        request = self._request(spot)
        job = Job(view["job"], spot.ident, self.iterations, self.target, mode="analyse")
        with self._lock:
            self._jobs[job.key] = job
        self._executor.submit(self._run_analysis, job, spot, request, on_done)
        return job.view()

    def _run_analysis(self, job: Job, spot, request: dict, on_done: Callable[[object, dict], None]) -> None:
        if job.cancelled:
            return
        job.state, job.started = "running", time.time()

        def started(proc) -> None:
            job.process = proc
            if job.cancelled:
                proc.terminate()
        try:
            raw = postflop.solve(request, on_progress=job.progress.update, on_start=started)
            on_done(spot, raw)
            job.result = spot.interpret(raw)
            job.state = "done"
            self._finished(job)
        except postflop.SolverError as exc:
            job.state = "cancelled" if job.cancelled else "error"
            job.error = None if job.cancelled else str(exc)
        except Exception:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            job.state, job.error = "error", "Erreur inattendue pendant l'analyse (détails dans le terminal)."
        finally:
            job.process = None

    def _run(self, job: Job, spot, request: dict, keep_live: bool = True) -> None:
        if job.cancelled:
            return
        if keep_live:
            self._set_live("", None)  # libère la mémoire de la session précédente
        job.state, job.started = "running", time.time()
        session = postflop.Session(request)

        def on_start(proc) -> None:
            job.process = proc
            if job.cancelled:  # arrêt demandé pendant le lancement du programme
                proc.terminate()
        try:
            raw = session.start(on_progress=job.progress.update, on_start=on_start)
            if session.study.is_file() and (not session.loading or not session.study.with_suffix(".json").is_file()):
                spot.write_meta(request, raw, session)
            after = getattr(spot, "after_solve", None)
            if after is not None and not session.loading:  # ex. le plan de jeu d'un spot d'étude
                try:
                    after(session)
                except Exception:  # noqa: BLE001 — la résolution reste bonne sans son plan
                    traceback.print_exc()
            job.result = spot.interpret(raw)
            if keep_live:
                self._set_live(spot.ident, session)
            else:
                session.close()
            job.state = "done"
            if job.mode != "load":
                self._finished(job)
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

    def plan_view(self, spot) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(self._plans.get(spot.ident, ""))
        return job.view() if job else None

    def prepare_plan(self, spot, extract: Callable[[postflop.Session, object], None]) -> dict:
        """Ouvre l'étude enregistrée d'un spot et en tire son plan de jeu (extract), sans garder la session."""
        request = self._request(spot)
        with self._lock:
            current = self._jobs.get(self._plans.get(spot.ident, ""))
            if current and current.state in ("waiting", "running"):
                return current.view()
            job = Job("plan-" + postflop.study_key(request), spot.ident, self.iterations, self.target, mode="plan")
            self._jobs[job.key] = job
            self._plans[spot.ident] = job.key
        self._executor.submit(self._run_plan, job, spot, request, extract)
        return job.view()

    def _run_plan(self, job: Job, spot, request: dict, extract: Callable[[postflop.Session, object], None]) -> None:
        if job.cancelled:
            return
        if not postflop.study_path(request).is_file():
            job.state, job.error = "error", "Étude introuvable."
            return
        self._set_live("", None)  # une étude à la fois en mémoire
        job.state, job.started = "running", time.time()
        job.progress["stage"] = "ouverture de l'étude"
        session = postflop.Session(request)

        def on_start(proc) -> None:
            job.process = proc
            if job.cancelled:
                proc.terminate()
        try:
            session.start(on_progress=job.progress.update, on_start=on_start)
            job.progress["stage"] = "lecture des stratégies"
            extract(session, spot)
            job.state = "done"
        except postflop.SolverError as exc:
            job.state = "cancelled" if job.cancelled else "error"
            job.error = None if job.cancelled else str(exc)
        except Exception:  # noqa: BLE001 — l'erreur est montrée dans l'interface
            traceback.print_exc()
            job.state, job.error = "error", "Erreur inattendue pendant la préparation du plan (détails dans le terminal)."
        finally:
            session.close()
            job.process = None

    def node(self, spot, path: list) -> dict:
        """Nœud au bout du chemin : depuis la session si elle est ouverte, sinon depuis le cache. Les mains
        que le solveur ne joue presque jamais là y prennent leur meilleure action selon l'EV (postflop.settle)."""
        session = self.live_session(spot.ident)
        if session is not None:
            return {"node": postflop.settle(session.node(path), postflop.weights_of(spot)), "live": True}
        raw = postflop.cached(self._request(spot))
        node = postflop.cached_node(raw, path) if raw else None
        if node is None:
            raise NeedSession(spot.ident)
        return {"node": postflop.settle(node, postflop.weights_of(spot)), "live": False}

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
        self._loader.shutdown(wait=False, cancel_futures=True)
