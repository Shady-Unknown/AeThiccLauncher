"""Build/release inputs stay in sync and single-sourced (P2-7)."""
import json
import re
import unittest
from pathlib import Path

import build_config
import verify_release
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Private tree: workflows live in the publish overrides. Public tree: the
# overrides have already been applied to .github/workflows.
WORKFLOWS = ROOT / ".github" / "workflows"
if not WORKFLOWS.is_dir():
    WORKFLOWS = ROOT / "tools" / "public_overrides" / ".github" / "workflows"


class VersionSourceTests(unittest.TestCase):
    def test_version_sources_agree(self):
        app_version = verify_release.version()
        self.assertEqual(verify_release.check_version_sources(ROOT, app_version), [])

    def test_artifact_names(self):
        windows = verify_release.expected_artifacts("1.9.7", "windows")
        self.assertIn("Ae_Thicc_Launcher_Setup_1.9.7.exe", windows)
        self.assertIn("Ae_Thicc_Launcher_Windows_1.9.7.zip", windows)
        self.assertIn(
            "Ae_Thicc_Launcher_Linux.flatpak",
            verify_release.expected_artifacts("1.9.7", "linux"),
        )


class BuildOptionsTests(unittest.TestCase):
    def test_spec_uses_the_shared_options(self):
        spec = (ROOT / "Ae_Thicc_Launcher.spec").read_text(encoding="utf-8")
        self.assertIn("import build_config", spec)
        self.assertIn("AETHICC_BUILD_MODE", spec)
        self.assertIn("AETHICC_APP_NAME", spec)
        self.assertIn("PyQt6.QtWebEngineWidgets", build_config.HIDDEN_IMPORTS)
        self.assertIn("PyQt6.QtWebEngineCore", build_config.HIDDEN_IMPORTS)
        self.assertIn("curl_cffi", build_config.COLLECT_ALL)

    def test_scripts_do_not_duplicate_pyinstaller_flags(self):
        for name in ("build_win.ps1", "build_linux.sh"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("--collect-all", text, name)
            self.assertNotIn("--hidden-import", text, name)
            self.assertIn("Ae_Thicc_Launcher.spec", text, name)

    def test_python_is_pinned_to_312(self):
        windows = (ROOT / "build_win.ps1").read_text(encoding="utf-8")
        linux = (ROOT / "build_linux.sh").read_text(encoding="utf-8")
        self.assertIn("3.12", windows)
        self.assertIn("python:3.12", linux)
        self.assertIn("SOURCE_DATE_EPOCH", windows)
        self.assertIn("SOURCE_DATE_EPOCH", linux)

    def test_public_manifest_exports_the_build_inputs(self):
        manifest = ROOT / "tools" / "public_manifest.json"
        if not manifest.is_file():
            self.skipTest("public manifest is private-only")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for name in (
            "Ae_Thicc_Launcher.spec", "build_config.py",
            "requirements-build.txt", "verify_release.py",
        ):
            self.assertIn(name, data["include"])


class WorkflowTests(unittest.TestCase):
    def _workflow(self, name):
        return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))

    def test_release_is_gated_on_tests(self):
        release = self._workflow("release.yml")
        jobs = release["jobs"]
        self.assertIn("tests", jobs)
        self.assertIn("preflight", jobs)
        self.assertEqual(jobs["build-windows"]["needs"], ["tests", "preflight"])
        self.assertEqual(jobs["build-flatpak"]["needs"], ["tests", "preflight"])

    def test_release_uses_the_single_validator(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertNotIn("grep -ioP", text)
        self.assertIn("verify_release.py --require-release", text)
        self.assertIn('--tag "v$VERSION"', text)
        self.assertIn("--platform windows", text)
        self.assertIn("--platform linux", text)
        self.assertIn("--generate-sums", text)
        self.assertIn("--require-remote", text)

    def test_workflows_pin_python_and_actions(self):
        for name in ("release.yml", "tests.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("3.12.10", text, name)
            self.assertNotIn("windows-latest", text, name)
            self.assertNotIn("ubuntu-latest", text, name)
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("uses:"):
                    ref = stripped.split("uses:", 1)[1].strip()
                    if ref.startswith("."):
                        continue
                    self.assertRegex(
                        ref, r"^[^@]+@[0-9a-f]{40}\b",
                        f"{name}: action not pinned to a commit: {ref}",
                    )

    def test_release_uploads_checksums(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("requirements-build.txt", text)

    def test_test_workflow_runs_the_suite(self):
        workflow = self._workflow("tests.yml")
        steps = workflow["jobs"]["test"]["steps"]
        runs = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("unittest discover -s tests", runs)
        self.assertEqual(workflow["jobs"]["test"]["strategy"]["matrix"]["os"],
                         ["ubuntu-24.04", "windows-2025"])


if __name__ == "__main__":
    unittest.main()
