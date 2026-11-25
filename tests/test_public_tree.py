"""The public mirror must not contain private-only files (CI only)."""
import os
import unittest
from pathlib import Path

PRIVATE_PATHS = (
    "API_Keys.txt",
    "Audit",
    "plans",
    "secrets.json",
    ".env",
    "tools",
    "agent.md",
    "AGENTS.md",
)


@unittest.skipUnless(
    os.environ.get("AETHICC_PUBLIC_CI") == "1",
    "set AETHICC_PUBLIC_CI=1 to check the exported public tree",
)
class PublicTreeTests(unittest.TestCase):
    def test_private_files_are_not_exported(self):
        root = Path(__file__).resolve().parents[1]
        leaked = [name for name in PRIVATE_PATHS if (root / name).exists()]
        self.assertEqual(leaked, [], f"private paths exported: {leaked}")

    def test_version_and_changelog_are_present(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "core" / "version.py").is_file())
        self.assertTrue((root / "CHANGELOG.md").is_file())
        self.assertTrue((root / "tests").is_dir())
        self.assertTrue((root / "requirements.txt").is_file())


if __name__ == "__main__":
    unittest.main()
