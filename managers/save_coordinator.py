"""In-process serialization of savegame operations.

One reentrant lock protects every operation that mutates the staging store, a
live save destination or the per-game manifest. The lock is intentionally
coarse: finer-grained locking would have to coordinate mirror snapshots,
shared destinations and retention, and is not justified by measured contention.
It is reentrant so restore can create its safety snapshot and refresh its
manifest inside the same transaction.

Shutdown uses the same lock. ``close()`` refuses new *outermost* operations;
callers already parked in ``acquire()`` are woken when the lock is released and
then refused, and ``wait_idle()`` keeps reporting busy while they are parked.
Nested calls from the thread that already owns the operation are never refused,
because they are part of the transaction that is finishing.
"""
from __future__ import annotations

import threading
import time


class OperationBusy(RuntimeError):
    """The coordinator could not be acquired within the caller's budget."""


class OperationCancelled(RuntimeError):
    """The coordinator is closing and refuses new ordinary operations."""


class SavegamesOperationCoordinator:
    def __init__(self):
        self._lock = threading.RLock()
        self._condition = threading.Condition(threading.Lock())
        self._closing = False
        self._active = 0
        # Threads parked in acquire() for their outermost acquisition.
        self._waiters = 0
        # Cooperative cancellation: operations poll this at their safe points.
        self._cancel = threading.Event()
        # Reentrancy depth per thread: only the outermost call owns the closing
        # check and the waiter accounting.
        self._local = threading.local()

    def _depth(self) -> int:
        return getattr(self._local, "depth", 0)

    def request_cancel(self):
        """Ask running operations to stop at their next safe point.

        It is not a lock refusal: the operation itself decides where stopping
        leaves the store consistent. Already-running transactions keep the
        lock until they unwind.
        """
        self._cancel.set()

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def is_nested(self) -> bool:
        """True when the calling thread already owns an operation."""
        return self._depth() > 0

    def is_closing(self):
        with self._condition:
            return self._closing

    def is_active(self):
        with self._condition:
            return self._active > 0

    def run(self, operation, timeout_ms=None):
        """Run ``operation`` under the global lock.

        ``timeout_ms=None`` waits without a deadline (worker context);
        ``timeout_ms=0`` is the non-blocking GUI path. The lock is always
        released, including on exceptions and cancellation.
        """
        depth = self._depth()
        if depth:
            # Nested coordinator call (restore -> safety snapshot, backup ->
            # push_mirror -> ensure_mirror_import, ...). It belongs to the
            # transaction already owned by this thread and must not be refused
            # by a concurrent close().
            self._local.depth = depth + 1
            try:
                return operation()
            finally:
                self._local.depth = depth

        timeout = None
        if timeout_ms is not None:
            timeout = max(0.0, float(timeout_ms) / 1000.0)

        with self._condition:
            if self._closing:
                raise OperationCancelled("savegames coordinator is closing")
            self._waiters += 1

        acquired = False
        waiting = True
        try:
            acquired = (self._lock.acquire(timeout=timeout)
                        if timeout is not None else self._lock.acquire())
            if not acquired:
                raise OperationBusy("another savegame operation is running")

            with self._condition:
                # ``_active`` is incremented in the same critical section that
                # removes the waiter, so wait_idle() never sees an idle gap.
                self._waiters -= 1
                waiting = False
                closing = self._closing
                if not closing:
                    self._active += 1
                self._condition.notify_all()

            if closing:
                # close() happened while we were parked: release the lock and
                # refuse the operation without running it.
                self._lock.release()
                acquired = False
                raise OperationCancelled("savegames coordinator is closing")

            self._local.depth = 1
            try:
                return operation()
            finally:
                self._local.depth = 0
                with self._condition:
                    self._active -= 1
                    self._condition.notify_all()
                self._lock.release()
                acquired = False
        finally:
            if waiting:
                with self._condition:
                    self._waiters -= 1
                    self._condition.notify_all()
            if acquired:
                # Defensive: every regular path releases explicitly above.
                self._lock.release()

    def close(self):
        """Refuse new outermost operations; active callers finish their work.

        Idempotent and safe to call from any thread. Wakes ``wait_idle``
        callers so they can observe the new state.
        """
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    def wait_idle(self, timeout_ms=0):
        """Wait outside the operation lock until no operation or waiter exists.

        A caller parked in ``acquire()`` still counts as busy: returning True
        while work is about to start would let the caller tear down managers
        that the operation is going to use.
        """
        deadline = None
        if timeout_ms is not None:
            deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
        with self._condition:
            while self._active or self._waiters:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True
