"""Single source for PyInstaller options (P2-7).

Both build scripts and the release workflow use ``Ae_Thicc_Launcher.spec``,
which imports this module. Do not duplicate the hidden-import or collect-all
lists in shell/PowerShell files.
"""

APP_NAME = "Ae_Thicc_Launcher"

# Packages whose data/binaries PyInstaller cannot discover statically.
COLLECT_ALL = (
    "curl_cffi",
    "certifi",
    "keyring",
    "secretstorage",
)

# Imports that are only reached through Qt/WebEngine dynamic loading.
HIDDEN_IMPORTS = (
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
)

ICON = ".media/icon.ico"
