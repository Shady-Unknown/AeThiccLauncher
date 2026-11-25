#!/usr/bin/env python3
"""Single release validator (P2-15).

``core/version.py`` is the only version source. This script verifies every
derived metadata item instead of duplicating the version elsewhere: installer
fallback and product fields, CHANGELOG release section, exact ``Release_x.y.z``
commit subject, artifact names/hashes per platform, and the Git tag target
(lightweight and annotated). It never moves a tag or forces a push.

Usage:
    python verify_release.py                 # inputs only
    python verify_release.py --require-release --commit-message "Release_1.9.8"
    python verify_release.py --tag v1.9.8 --remote origin --require-remote
    python verify_release.py --platform all --artifacts-dir dist --generate-sums
    python verify_release.py --print-version
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS_ARTIFACTS = (
    "Ae_Thicc_Launcher_Setup_{version}.exe",
    "Ae_Thicc_Launcher_Windows_{version}.zip",
)
LINUX_ARTIFACTS = ("Ae_Thicc_Launcher_Linux.flatpak",)
SHA_FILE = "SHA256SUMS.txt"


def version(root: Path = ROOT) -> str:
    text = (root / "core" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("core/version.py has no __version__")
    return match.group(1)


def check_version_sources(root: Path, app_version: str,
                          require_release: bool = False) -> list[str]:
    problems = []
    installer = (root / "installer.iss").read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', installer)
    if not match:
        problems.append("installer.iss has no AppVersion fallback")
    elif match.group(1) != app_version:
        problems.append(
            f"installer.iss fallback {match.group(1)} != {app_version}")
    if "VersionInfoVersion={#AppVersion}.0" not in installer:
        problems.append("installer.iss VersionInfoVersion is not derived")
    if "OutputBaseFilename=Ae_Thicc_Launcher_Setup_{#AppVersion}" not in installer:
        problems.append("installer.iss output name is not versioned")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        rf"^##\s+v?{re.escape(app_version)}\b(.*)$", changelog,
        re.MULTILINE,
    )
    if section is None:
        problems.append(f"CHANGELOG.md has no '## v{app_version}' section")
    elif require_release:
        heading = section.group(0)
        if not re.search(r"\d{4}-\d{2}-\d{2}", heading):
            problems.append(
                f"CHANGELOG release heading has no YYYY-MM-DD date: {heading!r}")
    readme = root / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        latest = re.search(r"\*\*v(\d+\.\d+\.\d+)", text)
        if latest and latest.group(1) != app_version:
            print(
                f"warning: README latest note mentions v{latest.group(1)}, "
                f"version is {app_version}")
    return problems


def check_commit_message(message: str, app_version: str) -> list[str]:
    expected = f"Release_{app_version}"
    if (message or "").strip() != expected:
        return [f"commit subject must be exactly {expected!r}"]
    return []


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True)


def check_tag(root: Path, tag: str, remote: str, expected_commit: str,
              require_remote: bool = False) -> list[str]:
    result = _git(root, "ls-remote", "--tags", remote)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if require_remote:
            return [f"cannot reach remote {remote!r} for tag preflight: {detail}"]
        print(f"warning: tag preflight skipped (remote {remote!r}: {detail})")
        return []
    refs = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[1]] = parts[0]
    plain = refs.get(f"refs/tags/{tag}")
    peeled = refs.get(f"refs/tags/{tag}^{{}}")
    if plain is None:
        return []
    target = peeled or plain
    if target != expected_commit:
        return [
            f"tag {tag!r} already exists on {target[:12]}, expected "
            f"{expected_commit[:12]}; refusing to move it"]
    return []


def expected_artifacts(app_version: str, platform: str) -> list[str]:
    platform = (platform or "all").lower()
    windows = [name.format(version=app_version) for name in WINDOWS_ARTIFACTS]
    linux = list(LINUX_ARTIFACTS)
    if platform == "windows":
        return windows
    if platform == "linux":
        return linux
    if platform == "all":
        return windows + linux
    raise SystemExit(f"unknown platform: {platform}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_artifacts(app_version: str, directory: Path, platform: str,
                    require_sums: bool = False) -> list[str]:
    problems = []
    expected = expected_artifacts(app_version, platform)
    for name in expected:
        if not (directory / name).is_file():
            problems.append(f"missing artifact: {name}")
    sums = directory / SHA_FILE
    if sums.is_file():
        listed = {}
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                listed[parts[1].lstrip("*")] = parts[0]
        for name in expected:
            if name not in listed:
                problems.append(f"{SHA_FILE} has no hash for {name}")
            elif (directory / name).is_file() and sha256(directory / name) != listed[name]:
                problems.append(f"hash mismatch for {name}")
    elif require_sums:
        problems.append(f"missing {SHA_FILE}")
    return problems


def write_sums(directory: Path, app_version: str, platform: str) -> Path:
    names = expected_artifacts(app_version, platform)
    lines = []
    for name in names:
        path = directory / name
        if path.is_file():
            lines.append(f"{sha256(path)}  {name}")
    target = directory / SHA_FILE
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-release", action="store_true")
    parser.add_argument("--commit-message", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--require-remote", action="store_true")
    parser.add_argument("--expected-commit", default="")
    parser.add_argument("--platform", default="", choices=("", "windows", "linux", "all"))
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--generate-sums", action="store_true")
    parser.add_argument("--require-sums", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    app_version = version(root)
    if args.print_version:
        print(app_version)
        return 0

    problems = check_version_sources(root, app_version, args.require_release)
    if args.require_release and args.commit_message:
        problems += check_commit_message(args.commit_message, app_version)
    if args.tag:
        expected_commit = args.expected_commit or _git(
            root, "rev-parse", "HEAD").stdout.strip()
        problems += check_tag(
            root, args.tag, args.remote, expected_commit, args.require_remote)

    if args.artifacts_dir is not None and args.platform:
        directory = args.artifacts_dir.resolve()
        if args.generate_sums:
            write_sums(directory, app_version, args.platform)
        problems += check_artifacts(
            app_version, directory, args.platform, args.require_sums)

    payload = {
        "version": app_version,
        "ok": not problems,
        "problems": problems,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    elif problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
    else:
        print(f"Release inputs OK for {app_version}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
