"""Unit tests for the reentrant savegames operation coordinator."""
import threading
import unittest

from managers.save_coordinator import (
    OperationBusy, OperationCancelled, SavegamesOperationCoordinator,
)


def _wait_for_condition(condition, predicate, timeout=2.0):
    """Wait on a real condition instead of sleeping."""
    with condition:
        return condition.wait_for(predicate, timeout)


class CoordinatorTests(unittest.TestCase):
    def test_reentrant_operation_runs(self):
        coordinator = SavegamesOperationCoordinator()
        values = []

        def outer():
            values.append("outer")
            return coordinator.run(lambda: values.append("inner"))

        coordinator.run(outer)
        self.assertEqual(values, ["outer", "inner"])

    def test_try_run_returns_busy_without_blocking(self):
        coordinator = SavegamesOperationCoordinator()
        entered = threading.Event()
        release = threading.Event()

        def holding():
            def operation():
                entered.set()
                release.wait(2)
            coordinator.run(operation)

        thread = threading.Thread(target=holding)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(OperationBusy):
                coordinator.run(lambda: None, timeout_ms=0)
        finally:
            release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        # The timed-out caller left no waiter behind.
        self.assertTrue(coordinator.wait_idle(2000))
        self.assertFalse(coordinator.is_active())

    def test_exception_releases_the_lock(self):
        coordinator = SavegamesOperationCoordinator()
        with self.assertRaises(ValueError):
            coordinator.run(lambda: (_ for _ in ()).throw(ValueError("boom")))
        self.assertEqual(coordinator.run(lambda: 7), 7)

    def test_closing_refuses_new_operations(self):
        coordinator = SavegamesOperationCoordinator()
        coordinator.close()
        with self.assertRaises(OperationCancelled):
            coordinator.run(lambda: None)

    def test_close_is_idempotent(self):
        coordinator = SavegamesOperationCoordinator()
        coordinator.close()
        coordinator.close()
        self.assertTrue(coordinator.is_closing())

    def test_wait_idle_does_not_hold_the_operation_lock(self):
        coordinator = SavegamesOperationCoordinator()
        entered = threading.Event()
        release = threading.Event()

        def holding():
            def operation():
                entered.set()
                release.wait(2)
            coordinator.run(operation)

        thread = threading.Thread(target=holding)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertFalse(coordinator.wait_idle(0))
        release.set()
        self.assertTrue(coordinator.wait_idle(2000))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(coordinator.wait_idle(0))

    def test_parked_caller_is_cancelled_after_close(self):
        coordinator = SavegamesOperationCoordinator()
        a_entered = threading.Event()
        release_a = threading.Event()
        executed = []
        outcome = []
        completed = []

        def hold_a():
            def operation():
                a_entered.set()
                release_a.wait(5)
                completed.append("a")
            coordinator.run(operation)

        def park_b():
            try:
                coordinator.run(lambda: executed.append("b"))
            except OperationCancelled:
                outcome.append("cancelled")

        a = threading.Thread(target=hold_a)
        b = threading.Thread(target=park_b)
        a.start()
        self.assertTrue(a_entered.wait(2))
        b.start()
        self.assertTrue(
            _wait_for_condition(
                coordinator._condition, lambda: coordinator._waiters >= 1),
            "thread B never parked in acquire()",
        )
        # A parked caller counts as busy even before it runs.
        self.assertFalse(coordinator.wait_idle(0))

        coordinator.close()
        release_a.set()

        b.join(2)
        a.join(2)
        self.assertFalse(b.is_alive())
        self.assertFalse(a.is_alive())
        self.assertEqual(outcome, ["cancelled"])
        self.assertEqual(executed, [])
        self.assertEqual(completed, ["a"])
        self.assertTrue(coordinator.wait_idle(2000))

    def test_wait_idle_reports_busy_for_a_parked_caller_only(self):
        coordinator = SavegamesOperationCoordinator()
        # Hold the operation lock directly: no operation is active, so only
        # the parked caller can keep wait_idle() busy.
        self.assertTrue(coordinator._lock.acquire())
        results = []

        def park():
            results.append(coordinator.run(lambda: "ran"))

        thread = threading.Thread(target=park)
        try:
            thread.start()
            with coordinator._condition:
                self.assertTrue(
                    coordinator._condition.wait_for(
                        lambda: coordinator._waiters == 1, 2),
                    "the caller never parked",
                )
                # White-box: _active == 0 and _waiters == 1.
                self.assertEqual(coordinator._active, 0)
                self.assertEqual(coordinator._waiters, 1)
            self.assertFalse(coordinator.wait_idle(0))
        finally:
            coordinator._lock.release()

        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["ran"])
        # Once the waiter is cleared, idle is reported again.
        self.assertTrue(coordinator.wait_idle(2000))

    def test_nested_operation_survives_close(self):
        coordinator = SavegamesOperationCoordinator()

        def outer():
            # Simulates restore creating its safety snapshot while shutdown
            # starts: the nested call belongs to the running transaction.
            coordinator.close()
            return coordinator.run(lambda: "inner")

        self.assertEqual(coordinator.run(outer), "inner")

        with self.assertRaises(OperationCancelled):
            coordinator.run(lambda: None)


class SnapshotListStatusTests(unittest.TestCase):
    def _manager(self):
        from managers.save_manager import SavegamesManager
        return object.__new__(SavegamesManager)

    def test_busy_is_not_an_empty_snapshot_list(self):
        manager = self._manager()
        entered = threading.Event()
        release = threading.Event()

        def listing(title):
            entered.set()
            release.wait(2)
            return [{"id": "snap"}]

        manager._list_snapshots_impl = listing
        thread = threading.Thread(target=lambda: manager.list_snapshots("Game"))
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            result = manager.try_list_snapshots("Game", timeout_ms=0)
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["snapshots"], [])
        finally:
            release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        result = manager.try_list_snapshots("Game", timeout_ms=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["snapshots"], [{"id": "snap"}])

    def test_closed_coordinator_reports_cancelled(self):
        manager = self._manager()

        def listing(title):
            return [{"id": "snap"}]

        manager._list_snapshots_impl = listing
        manager._coordinator().close()

        result = manager.try_list_snapshots("Game", timeout_ms=0)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["snapshots"], [])

    def test_read_failure_reports_failed(self):
        manager = self._manager()

        def listing(title):
            raise OSError("manifest unreadable")

        manager._list_snapshots_impl = listing

        result = manager.try_list_snapshots("Game", timeout_ms=0)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["detail"], "manifest unreadable")
        self.assertEqual(result["snapshots"], [])


if __name__ == "__main__":
    unittest.main()
