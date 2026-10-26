"""Package only explicitly selected public source files, never local runtime data."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402 — script execution needs the project root first.


def main():
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    names = ["codex_app.py", "codex_toolbox.py", "codex_proxy.py",
             "codex_surface_translation.py", "network_gate.py", "claude_channels.py", "claude_translation.py", "claude_window.py",
             "claude_surface_translation.py", "claude_page_loader.py", "claude_clipboard.py", "secret_store.py", "version.py",
             "ui.html", "provider_style.py", "requirements.txt", "requirements-macos.txt",
             "Codex工具箱.spec", "Codex工具箱-macos.spec", "build.ps1", "build_macos.sh", "Test-on-Mac.command", "START-HERE-macOS.md",
             "README.md", "CHANGELOG.md", "PRIVACY.md", "SECURITY.md", "LICENSE",
             "assets/codex_toolbox.ico", "assets/codex_toolbox_master.png",
             "experiments/codex_package_activation.ps1",
             "experiments/codex_background_injector.ps1",
             "experiments/codex_translation_launcher.ps1",
             "experiments/codex_translation_overlay.js",
             "docs/macOS使用说明.md", "docs/使用手册.md", "docs/Claude桌面翻译.md"]
    names += [str(path.relative_to(ROOT)).replace("\\", "/") for path in (ROOT / "scripts").iterdir()
              if path.is_file() and path.suffix in {".py", ".js"}]
    names += [str(path.relative_to(ROOT)).replace("\\", "/") for path in (ROOT / "tests").glob("test_*.py")]
    prefix = f"codex-toolbox-{__version__}-macos-source"
    target = destination / f"Codex工具箱-{__version__}-macos-source.zip"
    manifest = {"version": __version__, "kind": "macos-source", "native_mac_verified": False, "files": {}}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(names)):
            data = (ROOT / name).read_bytes()
            info = zipfile.ZipInfo(prefix + "/" + name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith((".sh", ".command")) else 0o100644) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
        archive.writestr(prefix + "/source-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(names)) + 1
    print(f"Source package: {target}\nFiles: {len(set(names))}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
