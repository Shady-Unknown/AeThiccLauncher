"""Release validator: version sources, commit, tag and platform artifacts."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verify_release

VERSION = "1.9.8"


def _root(tmp: Path) -> Path:
    (tmp / "core").mkdir(parents=True)
    (tmp / "core" / "version.py").write_text(
        f'__version__ = "{VERSION}"\n', encoding="utf-8")
    (tmp / "installer.iss").write_text(
        '[Setup]\n#define AppVersion "1.9.8"\n'
        'AppVersion={#AppVersion}\n'
        'VersionInfoVersion={#AppVersion}.0\n'
        'OutputBaseFilename=Ae_Thicc_Launcher_Setup_{#AppVersion}\n',
        encoding="utf-8",
    )
    (tmp / "CHANGELOG.md").write_text(
        "## Unreleased\n\n### Fixed\n\n"
        f"## v{VERSION} — 2026-11-25\n\n### Fixed\n",
        encoding="utf-8",
    )
    (tmp / "README.md").write_text(f"**v{VERSION} (2026-11-25):** note\n",
                                   encoding="utf-8")
    return tmp


class VersionSourceTests(unittest.TestCase):
    def test_matching_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            self.assertEqual(
                verify_release.check_version_sources(root, VERSION), [])

    def test_installer_and_changelog_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            text = (root / "installer.iss").read_text()
            (root / "installer.iss").write_text(
                text.replace('"1.9.8"', '"1.9.7"'), encoding="utf-8")
            self.assertTrue(
                verify_release.check_version_sources(root, VERSION))

    def test_release_requires_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            text = (root / "CHANGELOG.md").read_text()
            (root / "CHANGELOG.md").write_text(
                text.replace("— 2026-11-25", ""), encoding="utf-8")
            self.assertTrue(
                verify_release.check_version_sources(
                    root, VERSION, require_release=True))

    def test_readme_stale_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            (root / "README.md").write_text("**v1.9.7:** old\n", encoding="utf-8")
            self.assertEqual(
                verify_release.check_version_sources(root, VERSION), [])

    def test_exact_release_commit(self):
        self.assertEqual(
            verify_release.check_commit_message("Release_1.9.8", VERSION), [])
        self.assertTrue(
            verify_release.check_commit_message("Release_1.9.8: notes", VERSION))
        self.assertTrue(
            verify_release.check_commit_message("Release_1.9.7", VERSION))


class ArtifactTests(unittest.TestCase):
    def _dir(self, tmp):
        root = Path(tmp)
        for name in verify_release.expected_artifacts(VERSION, "all"):
            (root / name).write_bytes(b"artifact")
        return root

    def test_names_per_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._dir(tmp)
            self.assertEqual(
                verify_release.check_artifacts(VERSION, root, "windows"), [])
            self.assertEqual(
                verify_release.check_artifacts(VERSION, root, "linux"), [])
            (root / "Ae_Thicc_Launcher_Linux.flatpak").unlink()
            self.assertTrue(
                verify_release.check_artifacts(VERSION, root, "linux"))

    def test_generate_and_verify_sums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._dir(tmp)
            verify_release.write_sums(root, VERSION, "all")
            self.assertEqual(
                verify_release.check_artifacts(
                    VERSION, root, "all", require_sums=True), [])
            (root / "Ae_Thicc_Launcher_Setup_1.9.8.exe").write_bytes(b"changed")
            self.assertTrue(
                verify_release.check_artifacts(
                    VERSION, root, "all", require_sums=True))

    def test_missing_sums_when_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._dir(tmp)
            self.assertTrue(
                verify_release.check_artifacts(
                    VERSION, root, "all", require_sums=True))


class TagTests(unittest.TestCase):
    def _result(self, stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_missing_tag_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                verify_release, "_git", return_value=self._result("")
            ):
                self.assertEqual(
                    verify_release.check_tag(Path(tmp), "v1.9.8", "origin", "a"),
                    [])

    def test_lightweight_and_annotated_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                verify_release, "_git",
                return_value=self._result("abc refs/tags/v1.9.8\n"),
            ):
                self.assertEqual(
                    verify_release.check_tag(Path(tmp), "v1.9.8", "origin", "abc"),
                    [])
            with mock.patch.object(
                verify_release, "_git",
                return_value=self._result(
                    "tagobj refs/tags/v1.9.8\nabc refs/tags/v1.9.8^{}\n"),
            ):
                self.assertEqual(
                    verify_release.check_tag(Path(tmp), "v1.9.8", "origin", "abc"),
                    [])

    def test_tag_on_another_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                verify_release, "_git",
                return_value=self._result("def refs/tags/v1.9.8\n"),
            ):
                problems = verify_release.check_tag(
                    Path(tmp), "v1.9.8", "origin", "abc")
        self.assertTrue(problems)

    def test_unreachable_remote_requires_ci_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                verify_release, "_git",
                return_value=self._result("", returncode=1, stderr="network"),
            ):
                self.assertEqual(
                    verify_release.check_tag(Path(tmp), "v1.9.8", "origin", "abc"),
                    [])
                self.assertTrue(
                    verify_release.check_tag(
                        Path(tmp), "v1.9.8", "origin", "abc", require_remote=True))


if __name__ == "__main__":
    unittest.main()
