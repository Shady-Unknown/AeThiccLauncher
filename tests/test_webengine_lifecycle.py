"""External WebEngine lifecycle smoke (P2-6).

Opt-in: set ``AETHICC_RUN_WEBENGINE_TESTS=1``. Starts the real launcher in a
subprocess, samples its renderer children while it runs, lets it quit, and then
checks that every sampled renderer is gone. The check is external on purpose:
counting renderers while QApplication is still alive proves nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[1]


def _is_renderer(process):
    try:
        name = (process.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return "webengineprocess" in name


def _alive_renderers(pids):
    alive = set()
    for pid in pids:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            if _is_renderer(process):
                alive.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return alive


@unittest.skipUnless(
    os.environ.get("AETHICC_RUN_WEBENGINE_TESTS") == "1",
    "set AETHICC_RUN_WEBENGINE_TESTS=1 to run the WebEngine lifecycle smoke",
)
class WebEngineLifecycleTests(unittest.TestCase):
    def test_no_renderer_survives_the_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            (data_root / "config").mkdir(parents=True)
            (data_root / "config" / "config.json").write_text(
                json.dumps({
                    "onboarding": {"completed": True, "version": 1},
                    "watched_folders": [],
                }),
                encoding="utf-8",
            )
            log_path = Path(tmp) / "launcher.log"

            env = os.environ.copy()
            env.update({
                "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_DISABLE_SANDBOX": "1",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu",
                "AETHICC_DATA_DIR": str(data_root),
                "AETHICC_SMOKE_QUIT_MS": "4000",
            })

            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    [sys.executable, str(REPO / "main.py")],
                    cwd=str(REPO),
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                sampled = set()
                deadline = time.time() + 45
                try:
                    parent = psutil.Process(process.pid)
                    while process.poll() is None and time.time() < deadline:
                        try:
                            for child in parent.children(recursive=True):
                                if _is_renderer(child):
                                    sampled.add(child.pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        time.sleep(0.1)
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    self.fail("launcher did not quit within the timeout")
                finally:
                    if process.poll() is None:
                        process.kill()

            output = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertTrue(
                sampled,
                "no QtWebEngineProcess child was observed; the smoke did not "
                f"exercise WebEngine.\n{output[-2000:]}",
            )
            self.assertEqual(
                process.returncode, 0,
                f"launcher exited with {process.returncode}\n{output[-2000:]}",
            )

            alive = _alive_renderers(sampled)
            give_up = time.time() + 10
            while alive and time.time() < give_up:
                time.sleep(0.2)
                alive = _alive_renderers(alive)
            self.assertEqual(
                alive, set(),
                f"renderer processes survived the launcher: {sorted(alive)}\n"
                f"{output[-2000:]}",
            )


if __name__ == "__main__":
    unittest.main()
