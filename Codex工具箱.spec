# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("webview") + collect_submodules("pystray")

a = Analysis(
    ["codex_app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui.html", "."),
        ("assets/codex_toolbox.ico", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Codex工具箱",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/codex_toolbox.ico",
)
