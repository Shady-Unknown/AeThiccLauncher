"""Uniform lifecycle tracking for background QThread workers.

`main_window.py` used to keep one ad-hoc list per worker type
(``_active_sales_workers``, ``_active_sgdb_workers``, ...). Those lists were
fragile: every new feature added another list, another cleanup method and
another chance to forget a worker at shutdown.

The registry keeps a single set of workers, drops them automatically when
their ``finished`` signal fires and offers a single ``stop_all`` hook for the
window shutdown path.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject


def _is_running(worker) -> bool:
    try:
        return bool(worker.isRunning())
    except Exception:
        return False


class TaskRegistry(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: set = set()

    def track(self, worker, cancel: str = "cancel"):
        """Register a worker and return it (convenient for ``self.tasks.track(...)``).

        ``cancel`` is the optional method name used by ``stop_all``. Pass an
        empty string for workers that only support ``wait``.
        """
        if worker is None:
            return worker
        self._workers.add(worker)
        finished = getattr(worker, "finished", None)
        if finished is not None:
            try:
                finished.connect(lambda w=worker: self.forget(w))
            except Exception:
                pass
        if cancel:
            setattr(worker, "_task_registry_cancel", cancel)
        return worker

    def forget(self, worker):
        self._workers.discard(worker)

    def running(self) -> list:
        return [w for w in list(self._workers) if _is_running(w)]

    def stop_all(self, wait_ms: int = 1500) -> None:
        """Ask every tracked worker to stop, then wait briefly for it."""
        workers = list(self._workers)
        for worker in workers:
            cancel_name = getattr(worker, "_task_registry_cancel", "cancel")
            cancel = getattr(worker, cancel_name, None) if cancel_name else None
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    print(f"[Tasks] cancel failed for {worker}: {exc}")
        for worker in workers:
            try:
                if _is_running(worker):
                    worker.wait(wait_ms)
            except Exception as exc:
                print(f"[Tasks] wait failed for {worker}: {exc}")
        self._workers.clear()

    def __len__(self) -> int:
        return len(self._workers)
