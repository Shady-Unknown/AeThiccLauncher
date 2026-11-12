"""Single source of truth for the application version.

Everything that needs the version reads it from here:

* ``build_win.ps1`` names the release artifacts and passes it to Inno Setup
  (``ISCC /DAppVersion=...``);
* ``installer.iss`` falls back to the same value when the define is missing;
* ``CHANGELOG.md`` must contain a ``## v<version>`` section (checked by
  ``tests/test_version.py``) because the release workflow extracts the release
  notes from it.

Bump this value *and* add the matching ``## v<version>`` changelog section in
the same commit that starts with ``Release_``.
"""

__version__ = "1.9.4"

#: Same version as a tuple, handy for comparisons (Windows file versions).
__version_info__ = tuple(int(part) for part in __version__.split("."))
