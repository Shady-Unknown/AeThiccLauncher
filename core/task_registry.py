"""Uniform lifecycle tracking for background workers and threads.

`main_window.py` used to keep one ad-hoc list per worker type
(``_active_sales_workers``, ``_active_sgdb_workers``, ...). Those lists were
fragile: every new feature added another list, another cleanup method and
another chance to forget a worker at shutdown.

The registry keeps a single set of tasks, drops QThread workers automatically
when their ``finished`` signal fires, and offers one bounded ``stop_all`` hook
for the window shutdown path. Python daemon threads are tracked separately
because they cannot be cancelled, only joined.
"""
from __future__ import annotations

import logging

import time

from PyQt6.QtCore import QObject

log = logging.getLogger(__name__)

DEFAULT_STOP_BUDGET_MS = 5000
DEFAULT_THREAD_BUDGET_MS = 2000


def _is_running(worker) -> bool:
    try:
        return bool(worker.isRunning())
    except Exception:
        return False


def _is_alive(thread) -> bool:
    try:
        return bool(thread.is_alive())
    except Exception:
        return False


class TaskRegistry(QObject):
    #: The application-wide registry, set by :class:`core.app_context.AppContext`.
    _instance = None

    @classmethod
    def instance(cls):
        return cls._instance

    @classmethod
    def set_instance(cls, registry):
        cls._instance = registry

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: set = set()
        self._threads: set = set()

    # ------------------------------------------------------------------ tracking

    def track(self, worker, cancel=None):
        """Register a QThread-like worker and return it.

        ``cancel`` optionally names the method used by ``stop_all``; when it is
        not given both ``cancel`` and ``stop`` are tried, so every existing
        worker works without changes.
        """
        if worker is None:
            return worker
        self._workers.add(worker)
        finished = getattr(worker, "finished", None)
        if finished is not None:
            try:
                finished.connect(lambda w=worker: self.forget(w))
            except Exception:
                # best-effort: the worker may already be gone during shutdown
                pass
        if cancel:
            setattr(worker, "_task_registry_cancel", cancel)
        return worker

    def track_thread(self, thread):
        """Register a Python thread that must be joined at shutdown."""
        if thread is None:
            return thread
        self._threads.add(thread)
        return thread

    def forget(self, worker):
        self._workers.discard(worker)

    def forget_thread(self, thread):
        self._threads.discard(thread)

    def running(self) -> list:
        return [w for w in list(self._workers) if _is_running(w)]

    def running_workers(self) -> list:
        """QThread workers that are still running (public shutdown poll hook)."""
        return self.running()

    def threads_running(self) -> list:
        return [t for t in list(self._threads) if _is_alive(t)]

    # ------------------------------------------------------------------ stopping

    @staticmethod
    def _cancel(worker) -> None:
        names = []
        named = getattr(worker, "_task_registry_cancel", None)
        if named:
            names.append(named)
        names.extend(("cancel", "stop"))
        for name in names:
            cancel = getattr(worker, name, None)
            if not callable(cancel):
                continue
            try:
                cancel()
                return
            except TypeError:
                # Some cancellers take arguments (e.g. CoverWorker.cancel(titles)).
                continue
            except Exception as exc:
                log.exception(f"[Tasks] {name} failed for {worker}: {exc}")
                return
        interrupt = getattr(worker, "requestInterruption", None)
        if callable(interrupt):
            try:
                interrupt()
            except Exception:
                # best-effort: the worker may already be gone during shutdown
                pass

    def stop_all(self, wait_ms: int = DEFAULT_STOP_BUDGET_MS) -> list:
        """Ask every tracked worker to stop, then wait within a shared budget.

        Returns the workers still running when the budget expires so the caller
        can log or report them instead of terminating them unsafely.
        """
        workers = list(self._workers)
        deadline = time.monotonic() + max(0, int(wait_ms)) / 1000.0

        for worker in workers:
            self._cancel(worker)

        leftovers = []
        for worker in workers:
            if not _is_running(worker):
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                leftovers.append(worker)
                continue
            try:
                worker.wait(int(remaining * 1000))
            except Exception as exc:
                log.exception(f"[Tasks] wait failed for {worker}: {exc}")
            if _is_running(worker):
                leftovers.append(worker)

        # Keep still-running workers registered. Dropping the only Python
        # reference to a live QThread lets it be garbage-collected, and Qt
        # aborts the process ("QThread: Destroyed while thread is still
        # running"). The owner decides when a leftover is safe to forget.
        self._workers = {w for w in self._workers if _is_running(w)}
        if leftovers:
            log.info(f"[Tasks] {len(leftovers)} worker(s) still running after shutdown: "
                  + ", ".join(type(w).__name__ for w in leftovers))
        return leftovers

    def join_threads(self, timeout_ms: int = DEFAULT_THREAD_BUDGET_MS) -> list:
        """Join tracked Python threads within a shared budget; return leftovers."""
        threads = [t for t in list(self._threads) if _is_alive(t)]
        deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
        leftovers = []
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                leftovers.append(thread)
                continue
            try:
                thread.join(remaining)
            except Exception as exc:
                log.exception(f"[Tasks] join failed for {thread}: {exc}")
            if _is_alive(thread):
                leftovers.append(thread)

        self._threads.clear()
        if leftovers:
            log.info(f"[Tasks] {len(leftovers)} background thread(s) still alive: "
                  + ", ".join(getattr(t, "name", type(t).__name__) for t in leftovers))
        return leftovers

    def __len__(self) -> int:
        return len(self._workers) + len(self._threads)
