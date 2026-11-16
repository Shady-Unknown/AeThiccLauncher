"""Save path resolution shared by the savegames manager and the UI.

Everything that turns a portable Windows save path into a real location on the
running machine lives here: Steam library discovery, Proton/compatdata mapping,
case-insensitive resolution and the legacy Windows shell-folder aliases.

The translation itself is OS-agnostic: callers decide *when* it makes sense
(the manager only translates while running on Linux, because the compatdata
prefix only exists on the machine that runs the game).
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# Legacy Windows shell folders live inside the Proton prefix under their modern
# names (Wine maps "My Documents" to "Documents", and so on).
LEGACY_SHELL_FOLDERS = {
    "my documents": "Documents",
    "my music": "Music",
    "my pictures": "Pictures",
    "my videos": "Videos",
}

_VARIABLE_SEGMENT = re.compile(r"[<\[\(\{].+[>\]\)\}]")


# ---------------------------------------------------------------- predicates


def is_posix_path(value) -> bool:
    """True when ``value`` can only be meaningful on a POSIX filesystem.

    Saved metadata may hold paths generated on another OS (for example
    ``C:Users\\me\\.local\\share\\...`` produced by expanding ``~`` on
    Windows); those must never be treated as a Linux save location.
    """
    text = str(value or "").strip().strip('"')
    if not text:
        return False
    return text.startswith("/") or text.startswith("~")


def is_variable_folder_segment(segment) -> bool:
    return bool(_VARIABLE_SEGMENT.search(str(segment)))


# ------------------------------------------------------------------ helpers


def map_legacy_shell_folder(relative_path) -> str:
    """Replace legacy Windows folder aliases with the real prefix folders.

    Inside a Wine/Proton prefix ``C:\\users\\<user>\\My Documents`` is the same
    folder as ``Documents``, but only the latter is guaranteed to exist.
    """
    parts = [p for p in re.split(r"[\\/]+", str(relative_path or "")) if p]
    if parts:
        mapped = LEGACY_SHELL_FOLDERS.get(parts[0].lower())
        if mapped:
            parts[0] = mapped
    return "\\".join(parts)


def canonicalize_shell_folders(path) -> str:
    """Rewrite legacy aliases that follow the user profile folder.

    ``%userprofile%\\My Documents`` and ``C:\\Users\\<name>\\My Documents`` are
    the same folder as ``Documents``; normalizing keeps Windows and Linux
    metadata consistent.
    """
    text = str(path or "")
    if not text:
        return text

    parts = text.split("\\")
    for index, part in enumerate(parts):
        key = part.casefold()
        if key == "%userprofile%":
            target = index + 1
        elif key == "users":
            target = index + 2
        else:
            continue
        if target < len(parts):
            mapped = LEGACY_SHELL_FOLDERS.get(parts[target].casefold())
            if mapped:
                parts[target] = mapped
    return "\\".join(parts)


def find_matching_child(current_dir, segment):
    """Best case-insensitive/wildcard match for ``segment`` in ``current_dir``."""
    if not os.path.isdir(current_dir):
        return None

    try:
        entries = [
            entry for entry in os.listdir(current_dir)
            if os.path.isdir(os.path.join(current_dir, entry))
        ]
    except OSError:
        return None
    if not entries:
        return None

    if is_variable_folder_segment(segment):
        return sorted(entries, key=str.lower)[0]

    if "*" in segment or "?" in segment:
        matches = [entry for entry in entries if fnmatch.fnmatchcase(entry, segment)]
        if matches:
            return sorted(matches, key=str.lower)[0]

    if os.path.isdir(os.path.join(current_dir, segment)):
        return segment

    lower = segment.lower()
    for entry in entries:
        if entry.lower() == lower:
            return entry
    return None


def resolve_relative(base, relative_path):
    """Resolve ``relative_path`` under ``base``, fixing the case per segment."""
    if not relative_path:
        return base

    raw_parts = [p for p in re.split(r"[\\/]+", relative_path) if p]
    current = base
    output_parts = []
    for index, part in enumerate(raw_parts):
        match = find_matching_child(current, part)
        if match is None:
            output_parts.extend(raw_parts[index:])
            return os.path.join(current, *output_parts)

        if is_variable_folder_segment(part) or "*" in part or "?" in part:
            output_parts.append(match)
        else:
            output_parts.append(part)
        current = os.path.join(current, match)
    return os.path.join(base, *output_parts)


def resolve_case_insensitive(path):
    """Walk ``path`` fixing the case; keep the tail when it does not exist."""
    if not path:
        return path

    clean_path = str(path).strip().strip('"').replace("\\", "/")
    candidate = Path(clean_path).expanduser()
    if candidate.exists():
        return str(candidate)

    drive, rest = os.path.splitdrive(clean_path)
    parts = [p for p in rest.split("/") if p]

    if drive:
        # Keep the drive anchor: Path('C:') / 'Users' would collapse the
        # separator into the invalid "C:Users".
        current = Path(drive + os.sep)
    elif clean_path.startswith("/"):
        current = Path("/")
    else:
        current = Path("")

    for part in parts:
        exact = current / part
        if exact.exists():
            current = exact
            continue
        try:
            found = False
            if current.exists() and current.is_dir():
                for entry in current.iterdir():
                    if entry.name.lower() == part.lower():
                        current = entry
                        found = True
                        break
            if not found:
                current = exact
        except PermissionError:
            current = exact

    return str(current)


def resolve_absolute(path):
    """Expand ``~`` and resolve case-insensitively from the filesystem root."""
    if not path:
        return path
    candidate = os.path.expanduser(str(path).strip().strip('"'))
    candidate = os.path.normpath(candidate)
    if os.path.exists(candidate):
        return candidate
    if not os.path.isabs(candidate):
        return candidate
    return resolve_case_insensitive(candidate)


def resolve_save_root(path):
    """Strip wildcards/file names so the result is the folder holding saves."""
    if not path:
        return path
    candidate = str(path).strip().strip('"')
    candidate = candidate.replace("/", os.sep).replace("\\", os.sep)

    last_part = os.path.basename(candidate)
    if not last_part:
        return candidate

    if is_variable_folder_segment(last_part) or "*" in last_part or "?" in last_part:
        return os.path.dirname(candidate)

    if "." in last_part and not last_part.startswith("*"):
        parent = os.path.dirname(candidate)
        return parent if parent else candidate

    return candidate


# ------------------------------------------------------------ steam libraries


def _vdf_unescape(value: str) -> str:
    return value.replace("\\\\", "\\")


def parse_library_folders(vdf_path) -> list[str]:
    """Read Steam library roots from a ``libraryfolders.vdf`` file.

    Supports both the current schema (``"path" "<dir>"``) and the legacy one
    (``"1" "<dir>"``). Unreadable or malformed files return an empty list.
    """
    try:
        text = Path(vdf_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    paths = []
    for raw in re.findall(r'"path"\s*"((?:[^"\\]|\\.)*)"', text):
        paths.append(_vdf_unescape(raw))
    for raw in re.findall(r'^\s*"\d+"\s*"((?:[^"\\]|\\.)*)"', text, re.MULTILINE):
        paths.append(_vdf_unescape(raw))

    seen = set()
    result = []
    for path in paths:
        path = path.strip()
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def steamapps_roots(extra_paths=()) -> list[str]:
    """Candidate ``steamapps`` directories, native and Flatpak (deduplicated)."""
    home = os.path.expanduser("~")
    roots = []

    compat_client = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", "").strip()
    if compat_client:
        roots.append(os.path.join(compat_client, "steamapps"))

    roots.extend([
        os.path.join(home, ".local", "share", "Steam", "steamapps"),
        os.path.join(home, ".steam", "steam", "steamapps"),
        os.path.join(home, ".steam", "root", "steamapps"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", "data", "Steam", "steamapps"),
    ])
    roots.extend(extra_paths)

    # Every library registered by Steam may hold a compatdata folder.
    for root in list(roots):
        candidates = (
            os.path.join(root, "libraryfolders.vdf"),
            os.path.join(os.path.dirname(root), "config", "libraryfolders.vdf"),
        )
        for vdf in candidates:
            for library in parse_library_folders(vdf):
                roots.append(os.path.join(library, "steamapps"))

    seen = set()
    result = []
    for root in roots:
        key = os.path.normcase(os.path.normpath(root))
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return result


def compatdata_base(app_id, roots=None):
    """Existing ``compatdata/<app_id>`` folder, or ``None`` when not found."""
    app_id = str(app_id or "").strip()
    if not app_id:
        return None
    for root in (roots if roots is not None else steamapps_roots()):
        candidate = os.path.join(root, "compatdata", app_id)
        if os.path.isdir(candidate):
            return candidate
    return None


def default_compatdata_base(app_id, roots=None) -> str:
    """Expected ``compatdata/<app_id>`` location (for messages), even if missing."""
    app_id = str(app_id or "").strip()
    if not app_id:
        return ""
    candidates = list(roots if roots is not None else steamapps_roots())
    if not candidates:
        return ""
    return os.path.join(candidates[0], "compatdata", app_id)


# ----------------------------------------------------------------- translation


def translate_win_path(win_path, compatdata_base_path) -> str:
    """Map a portable Windows save path to its Proton prefix location.

    Returns ``""`` when the path uses a token we do not know. The caller must
    pass the compatdata folder of the game (``.../compatdata/<app_id>``).
    """
    if not win_path or not compatdata_base_path:
        return ""

    original = str(win_path).strip().strip('"')
    path = original.replace("/", "\\")
    lower_path = path.lower()

    def from_rel(relative, sub_path):
        base = os.path.join(compatdata_base_path, sub_path)
        rel = map_legacy_shell_folder(relative)
        return resolve_case_insensitive(resolve_relative(base, rel))

    if lower_path.startswith("%userprofile%\\"):
        return from_rel(path[len("%userprofile%\\"):], "pfx/drive_c/users/steamuser")

    if lower_path.startswith("%programdata%"):
        return from_rel(path[len("%programdata%"):].lstrip("\\"), "pfx/drive_c/ProgramData")

    if lower_path.startswith("%public%"):
        return from_rel(path[len("%public%"):].lstrip("\\"), "pfx/drive_c/users/Public")

    if lower_path.startswith("%windir%"):
        return from_rel(path[len("%windir%"):].lstrip("\\"), "pfx/drive_c/windows")

    if lower_path.startswith("%localappdata%"):
        return from_rel(
            path[len("%localappdata%"):].lstrip("\\"),
            "pfx/drive_c/users/steamuser/AppData/Local",
        )

    if lower_path.startswith("%appdata%"):
        return from_rel(
            path[len("%appdata%"):].lstrip("\\"),
            "pfx/drive_c/users/steamuser/AppData/Roaming",
        )

    if lower_path.startswith("c:\\users\\steamuser\\"):
        return from_rel(path[len("c:\\users\\steamuser\\"):], "pfx/drive_c/users/steamuser")

    if lower_path.startswith("c:\\users\\"):
        parts = path.split("\\")
        if len(parts) > 2:
            return from_rel("\\".join(parts[3:]), "pfx/drive_c/users/steamuser")
        return ""

    if lower_path.startswith("c:\\"):
        return from_rel(path[len("c:\\"):], "pfx/drive_c")

    return ""


# ------------------------------------------------------------ restore safety


def normalize_for_compare(path) -> str:
    """Absolute, normcased path for lexical comparisons (no symlink lookup)."""
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))


def is_inside(path, root) -> bool:
    """True when ``path`` is strictly inside ``root`` (lexical)."""
    child = normalize_for_compare(path)
    parent = normalize_for_compare(root)
    if not child or not parent:
        return False
    try:
        return os.path.commonpath([child, parent]) == parent and child != parent
    except ValueError:
        # Different drives on Windows.
        return False


def check_restore_target(path, allowed_roots=(), exact_allowed=(), protected=()):
    """Validate a restore destination.

    ``allowed_roots`` are folders that may contain the destination (strictly
    inside, never equal); ``exact_allowed`` are paths that may be the
    destination itself (user-chosen save folders); ``protected`` are paths that
    must never be targeted or be an ancestor of the target.

    Returns ``(ok, reason)`` with a stable reason code for the UI.
    """
    raw = str(path or "").strip().strip('"')
    if not raw:
        return False, "empty_path"
    if not os.path.isabs(raw):
        return False, "not_absolute"

    dest = normalize_for_compare(raw)
    if dest == os.path.dirname(dest):
        return False, "filesystem_root"

    for item in protected:
        protected_path = normalize_for_compare(item)
        if not protected_path:
            continue
        if dest == protected_path:
            return False, "protected_path"
        if is_inside(protected_path, dest):
            return False, "protected_ancestor"

    for item in exact_allowed:
        if item and dest == normalize_for_compare(item):
            return True, ""

    for root in allowed_roots:
        if root and is_inside(dest, root):
            return True, ""

    return False, "outside_allowed_roots"
