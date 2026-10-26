# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
import comtypes.client
comtypes.client.GetModule("UIAutomationCore.dll")

# 公开版采用 PyWebView；旧版 Tk 界面不在启动链和发布包中。
# network_gate 由 codex_app 在函数内延迟导入，显式列入以避免打包时漏带。
hiddenimports = collect_submodules("webview") + collect_submodules("pystray") + collect_submodules("comtypes") + collect_submodules("uiautomation") + ["websocket", "network_gate"]

a = Analysis(
    ["codex_app.py"],
    pathex=[],
    binaries=collect_dynamic_libs("uiautomation"),
    datas=[
        ("ui.html", "."),
        ("assets/codex_toolbox.ico", "."),
        ("experiments/codex_background_injector.ps1", "experiments"),
        ("experiments/codex_package_activation.ps1", "experiments"),
        ("experiments/codex_translation_launcher.ps1", "experiments"),
        ("experiments/codex_translation_overlay.js", "experiments"),
    ] + collect_data_files("uiautomation"),
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
