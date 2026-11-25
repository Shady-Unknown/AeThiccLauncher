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

# Portable tokens that only make sense on the machine that owns the files.
STEAM_FOLDER_TOKENS = ("<steam-folder>", "<steamfolder>", "<steam>")
STEAM_ACCOUNT_TOKENS = (
    "<storeuserid>",
    "<your-steam-userid>",
    "<steam-userid>",
    "<steamuserid>",
    "<userid>",
    "<accountid>",
    "<steamid>",
)
_ACCOUNT_TOKEN_RE = re.compile(
    r"(?i)<(?:storeuserid|your-steam-userid|steam-userid|steamuserid|userid|accountid|steamid)>"
)


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
            # Join only what is still missing: the parts already resolved are
            # part of ``current`` and must not be appended again (that used to
            # turn ``Documents`` + a missing ``My Games`` into
            # ``Documents/Documents/My Games``).
            return os.path.join(current, *raw_parts[index:])

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


# ------------------------------------------------------ steam userdata paths


def _dedupe_paths(paths) -> list[str]:
    seen = set()
    result = []
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def steam_client_roots(extra_paths=()) -> list[str]:
    """Steam client install folders (the parent of ``steamapps``)."""
    return _dedupe_paths(
        os.path.dirname(os.path.normpath(root)) for root in steamapps_roots(extra_paths)
    )


def steam_userdata_roots(extra_paths=()) -> list[str]:
    """Existing ``userdata`` folders of the local Steam clients (native/Flatpak)."""
    return _dedupe_paths(
        candidate
        for candidate in (
            os.path.join(client, "userdata") for client in steam_client_roots(extra_paths)
        )
        if os.path.isdir(candidate)
    )


def _steam_account_dirs(roots):
    """``(userdata_root, account_id, account_path)`` for numeric account folders."""
    accounts = []
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not name.isdigit() or name == "0":
                continue
            path = os.path.join(root, name)
            if os.path.isdir(path):
                accounts.append((root, name, path))
    return accounts


def _account_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def default_steam_account_id(roots=None) -> str:
    """Numeric id of the most recently used local Steam account, or ``""``."""
    accounts = _steam_account_dirs(
        roots if roots is not None else steam_userdata_roots()
    )
    if not accounts:
        return ""
    _root, name, path = max(accounts, key=lambda item: _account_mtime(item[2]))
    return name


def find_steam_userdata_path(relative, roots=None, account_id="") -> str:
    """Map ``userdata\\<account>\\<appid>\\...`` to the local Steam userdata.

    ``relative`` is the part after ``userdata``. The account may be a literal
    id (valid on every OS), a placeholder or omitted entirely (``""``); a
    placeholder resolves to ``account_id`` when given, then to the account
    folder that owns the appid, then to the most recently used one.
    """
    parts = [p for p in re.split(r"[\\/]+", str(relative or "")) if p]
    if not parts:
        return ""
    if roots is None:
        roots = steam_userdata_roots()
    roots = list(roots)
    if not roots:
        return ""

    account_token, rest = parts[0], parts[1:]
    appid = rest[0] if rest else ""
    accounts = _steam_account_dirs(roots)

    if account_token.casefold() in STEAM_ACCOUNT_TOKENS:
        chosen = None
        preferred = str(account_id or "").strip()
        if preferred:
            for root, name, _path in accounts:
                if name == preferred:
                    chosen = (root, name)
                    break
        if chosen is None and appid:
            for root, name, path in accounts:
                if os.path.isdir(os.path.join(path, appid)):
                    chosen = (root, name)
                    break
        if chosen is None and accounts:
            root, name, path = max(accounts, key=lambda item: _account_mtime(item[2]))
            chosen = (root, name)
        if chosen is None:
            return ""
        root, account = chosen
    else:
        if "<" in account_token or ">" in account_token:
            return ""
        root = ""
        for account_root, name, _path in accounts:
            if name.casefold() == account_token.casefold():
                root = account_root
                break
        if not root:
            root = roots[0]
        account = account_token

    return resolve_case_insensitive(os.path.join(root, account, *rest))


def steam_userdata_tail(path):
    """Return the part after ``userdata``, or ``None`` when not that path.

    Accepts ``<steam-folder>\\userdata\\...``, expanded Windows paths
    (``C:\\Program Files (x86)\\Steam\\userdata\\...``) and the manifest form
    ``<root>\\userdata\\<storeUserId>\\<appid>\\...``, which is the Steam client
    folder even though it does not name ``Steam``.
    """
    text = str(path or "").replace("/", "\\")
    lower = text.casefold()
    for token in STEAM_FOLDER_TOKENS:
        if lower.startswith(token + "\\"):
            tail = text[len(token) + 1:]
            tail_lower = tail.casefold()
            if tail_lower == "userdata" or tail_lower.startswith("userdata\\"):
                return tail[len("userdata"):].lstrip("\\")
            return None
    parts = [p for p in text.split("\\") if p]
    for index in range(len(parts) - 2):
        if parts[index].casefold() != "userdata":
            continue
        account = parts[index + 1]
        appid = parts[index + 2]
        if (account.casefold() in STEAM_ACCOUNT_TOKENS or account.isdigit()) and appid.isdigit():
            return "\\".join(parts[index + 1:])
    return None


def steam_folder_tail(path):
    """Return the part after ``<steam-folder>``, or ``None``."""
    text = str(path or "").replace("/", "\\")
    lower = text.casefold()
    for token in STEAM_FOLDER_TOKENS:
        if lower.startswith(token + "\\"):
            return text[len(token) + 1:]
    return None


def find_steam_folder_path(tail) -> str:
    """Resolve a path relative to the Linux Steam client folder."""
    candidates = [
        os.path.join(client, str(tail or "")) for client in steam_client_roots()
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return resolve_case_insensitive(candidate)
    return resolve_case_insensitive(candidates[0]) if candidates else ""


def replace_steam_account_token(path, account_id) -> str:
    """Replace ``<storeUserId>`` and friends with a concrete account id."""
    return _ACCOUNT_TOKEN_RE.sub(lambda _match: str(account_id), str(path or ""))


# ----------------------------------------------------------------- translation


def translate_win_path_details(win_path, compatdata_base_path, steam_roots=None,
                              account_id="") -> dict:
    """Explain how a portable save path maps on this machine.

    ``kind`` is ``prefix`` (inside the Proton/Wine prefix), ``steam`` (the
    Linux Steam client) or ``native`` (a POSIX path used as-is). ``reason`` is
    empty on success, otherwise a stable code the UI can explain.
    """
    result = {"path": "", "kind": "", "reason": "", "token": ""}
    original = str(win_path or "").strip().strip('"')
    if not original:
        result["reason"] = "empty"
        return result

    if is_posix_path(original):
        text = original
        if text.startswith("~") or text.startswith("/"):
            text = text.replace("\\", "/")
        result["path"] = resolve_absolute(text)
        result["kind"] = "native"
        return result

    path = original.replace("/", "\\")
    lower_path = path.casefold()

    # Steam userdata lives in the Linux client, never inside the game prefix.
    tail = steam_userdata_tail(path)
    if tail is not None:
        result["kind"] = "steam"
        mapped = find_steam_userdata_path(tail, steam_roots, account_id)
        if not mapped:
            result["reason"] = "steam_userdata_not_found"
            return result
        result["path"] = mapped
        return result

    tail = steam_folder_tail(path)
    if tail is not None:
        result["kind"] = "steam"
        mapped = find_steam_folder_path(tail)
        if not mapped:
            result["reason"] = "steam_folder_not_found"
            return result
        result["path"] = mapped
        return result

    # ``<storeUserId>`` also appears outside Steam userdata (for example
    # ``%appdata%\Game\<storeUserId>\slot.sav``): it is this machine's
    # Steam account id there too.
    if _ACCOUNT_TOKEN_RE.search(path):
        account = str(account_id or "").strip() or default_steam_account_id(steam_roots)
        if not account:
            result["reason"] = "account_not_found"
            return result
        path = replace_steam_account_token(path, account)
        lower_path = path.casefold()

    unknown = re.search(r"<[^<>]+>", path)
    if unknown:
        result["reason"] = "unknown_placeholder"
        result["token"] = unknown.group(0)
        return result

    if not compatdata_base_path:
        result["kind"] = "prefix"
        result["reason"] = "no_compatdata"
        return result

    result["kind"] = "prefix"

    def from_rel(relative, sub_path):
        base = os.path.join(compatdata_base_path, sub_path)
        rel = map_legacy_shell_folder(relative)
        return resolve_case_insensitive(resolve_relative(base, rel))

    if lower_path.startswith("%userprofile%\\"):
        result["path"] = from_rel(path[len("%userprofile%\\"):], "pfx/drive_c/users/steamuser")
        return result

    if lower_path.startswith("%username%\\"):
        result["path"] = from_rel(path[len("%username%\\"):], "pfx/drive_c/users/steamuser")
        return result

    if lower_path.startswith("%programdata%"):
        result["path"] = from_rel(path[len("%programdata%"):].lstrip("\\"), "pfx/drive_c/ProgramData")
        return result

    if lower_path.startswith("%public%"):
        result["path"] = from_rel(path[len("%public%"):].lstrip("\\"), "pfx/drive_c/users/Public")
        return result

    if lower_path.startswith("%windir%"):
        result["path"] = from_rel(path[len("%windir%"):].lstrip("\\"), "pfx/drive_c/windows")
        return result

    if lower_path.startswith("%localappdata%"):
        result["path"] = from_rel(
            path[len("%localappdata%"):].lstrip("\\"),
            "pfx/drive_c/users/steamuser/AppData/Local",
        )
        return result

    if lower_path.startswith("%appdata%"):
        result["path"] = from_rel(
            path[len("%appdata%"):].lstrip("\\"),
            "pfx/drive_c/users/steamuser/AppData/Roaming",
        )
        return result

    if lower_path.startswith("c:\\users\\steamuser\\"):
        result["path"] = from_rel(path[len("c:\\users\\steamuser\\"):], "pfx/drive_c/users/steamuser")
        return result

    if lower_path.startswith("c:\\users\\"):
        parts = path.split("\\")
        if len(parts) > 2:
            result["path"] = from_rel("\\".join(parts[3:]), "pfx/drive_c/users/steamuser")
            return result
        result["reason"] = "unsupported"
        return result

    if lower_path.startswith("c:\\"):
        result["path"] = from_rel(path[len("c:\\"):], "pfx/drive_c")
        return result

    result["reason"] = "unsupported"
    return result


def translate_win_path(win_path, compatdata_base_path, account_id="") -> str:
    """Map a portable Windows save path to its Proton prefix location.

    Returns ``""`` when the path uses a token we do not know. The caller must
    pass the compatdata folder of the game (``.../compatdata/<app_id>``), which
    Steam userdata paths do not need.
    """
    return translate_win_path_details(
        win_path, compatdata_base_path, account_id=account_id
    )["path"]


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
