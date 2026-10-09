# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# 公开版采用 PyWebView；旧版 Tk 界面不在启动链和发布包中。
hiddenimports = collect_submodules("webview") + collect_submodules("pystray") + ["websocket"]

a = Analysis(
    ["codex_app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui.html", "."),
        ("assets/codex_toolbox.ico", "."),
        ("experiments/codex_background_injector.ps1", "experiments"),
        ("experiments/codex_package_activation.ps1", "experiments"),
        ("experiments/codex_translation_launcher.ps1", "experiments"),
        ("experiments/codex_translation_overlay.js", "experiments"),
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
    version="version_info.txt",
)
