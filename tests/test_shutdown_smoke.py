import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("AETHICC_RUN_GUI_SMOKE") == "1",
    "set AETHICC_RUN_GUI_SMOKE=1 to run the offscreen GUI shutdown smoke test",
)
class ShutdownSmokeTests(unittest.TestCase):
    def test_offscreen_close_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "lib").mkdir()
            (root / "config" / "config.json").write_text(
                json.dumps({
                    "watched_folders": [str(root / "lib")],
                    "onboarding": {"completed": True},
                }),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update({
                "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu",
                "QTWEBENGINE_DISABLE_SANDBOX": "1",
                "AETHICC_DATA_DIR": str(root),
                "AETHICC_SMOKE_QUIT_MS": "4000",
            })
            proc = subprocess.run(
                [sys.executable, "main.py"],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output[-4000:])
            self.assertIn("Shutdown sequence completed", output)
            self.assertNotIn("QThread: Destroyed", output)


if __name__ == "__main__":
    unittest.main()
