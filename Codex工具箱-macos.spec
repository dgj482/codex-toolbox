# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PIL import Image
from PyInstaller.utils.hooks import collect_submodules
from version import __version__, __version_tuple__

if sys.platform != "darwin":
    raise SystemExit("Build this app on macOS.")
icon = Path("build/macos-icon.icns")
icon.parent.mkdir(parents=True, exist_ok=True)
with Image.open("assets/codex_toolbox_master.png") as source:
    source.convert("RGBA").save(icon, format="ICNS")
a = Analysis(
    ["codex_app.py"], pathex=[], binaries=[],
    datas=[("ui.html", "."), ("assets/codex_toolbox.ico", "."),
           ("experiments/codex_background_injector.ps1", "experiments"),
           ("experiments/codex_package_activation.ps1", "experiments"),
           ("experiments/codex_translation_launcher.ps1", "experiments"),
           ("experiments/codex_translation_overlay.js", "experiments")],
    hiddenimports=collect_submodules("webview") + ["websocket", "keyring.backends.macOS", "network_gate"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["tkinter"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Codex工具箱",
          debug=False, strip=False, upx=False, console=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Codex工具箱-macos")
app = BUNDLE(collection, name="Codex工具箱.app", icon=str(icon),
             bundle_identifier="org.codex-toolbox.desktop",
             version=".".join(map(str, __version_tuple__[:3])),
             info_plist={"NSHighResolutionCapable": True,
                         "CFBundleVersion": ".".join(map(str, __version_tuple__[:3])),
                         "CodexToolboxVersion": __version__,
                         "LSMinimumSystemVersion": "15.0"})
