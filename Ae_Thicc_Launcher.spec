# -*- mode: python ; coding: utf-8 -*-
"""Parameterised PyInstaller spec (P2-7).

Mode and name come from the environment so Windows (onedir + installer) and
Linux (onefile inside Flatpak) share one option source:

    AETHICC_BUILD_MODE=onedir|onefile
    AETHICC_APP_NAME=<file name>
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from PyInstaller.utils.hooks import collect_all

import build_config

mode = os.environ.get("AETHICC_BUILD_MODE", "onedir").strip().lower()
app_name = os.environ.get("AETHICC_APP_NAME", build_config.APP_NAME).strip()
icon = build_config.ICON if os.path.exists(build_config.ICON) else None

datas = []
binaries = []
hiddenimports = list(build_config.HIDDEN_IMPORTS)
for package in build_config.COLLECT_ALL:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if mode == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=app_name,
    )
