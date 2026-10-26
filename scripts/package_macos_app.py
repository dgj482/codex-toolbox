"""Build a binary-only DMG from the native PyInstaller app; run on macOS."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402 — script execution needs the project root first.

# Official PowerShell release assets, checked against GitHub's asset digests.
PWSH_VERSION = "7.6.9"
PWSH_ASSETS = {
    "arm64": ("arm64", "a1c7e98ac403da4be5ba75c1c5855af272de7787a91d478c4267466fe58fc1af"),
    "x86_64": ("x64", "a5aa2308a7e9664510a5c93d5b337fd524ff7dc14b8a637fb6144a2bbaa298ae"),
}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def verify_architecture(binary, arch):
    architectures = run("/usr/bin/lipo", "-archs", binary,
                        capture_output=True, text=True).stdout.split()
    if arch not in architectures:
        raise RuntimeError(f"Wrong architecture in {binary.name}: {architectures}")


def embed_powershell(app, work, arch):
    asset_arch, expected = PWSH_ASSETS[arch]
    name = f"powershell-{PWSH_VERSION}-osx-{asset_arch}.tar.gz"
    archive = work / name
    url = f"https://github.com/PowerShell/PowerShell/releases/download/v{PWSH_VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    if digest(archive) != expected:
        raise RuntimeError("PowerShell archive SHA-256 mismatch; packaging stopped")
    runtime = app / "Contents" / "Resources" / "powershell"
    runtime.mkdir(parents=True)
    with tarfile.open(archive) as source:
        source.extractall(runtime, filter="data")
    executable = runtime / "pwsh"
    executable.chmod(executable.stat().st_mode | 0o111)
    verify_architecture(executable, arch)
    return executable


def main():
    if sys.platform != "darwin":
        raise SystemExit("A native macOS build host is required; Windows cannot produce this installer.")
    arch = platform.machine()
    if arch not in PWSH_ASSETS:
        raise SystemExit(f"Unsupported architecture: {arch}")
    original = ROOT / "dist" / "Codex工具箱.app"
    if not (original / "Contents" / "MacOS" / "Codex工具箱").is_file():
        raise SystemExit("Build Codex工具箱.app with PyInstaller first.")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    # This staging directory holds only the application and Applications shortcut.
    # Source, settings, API keys, tests and build scripts are never copied into it.
    with tempfile.TemporaryDirectory(prefix="macos-installer-", dir=build) as temporary:
        work = Path(temporary)
        payload = work / "payload"
        payload.mkdir()
        app = payload / original.name
        run("/usr/bin/ditto", original, app)
        executable = app / "Contents" / "MacOS" / "Codex工具箱"
        verify_architecture(executable, arch)
        # Project Python modules belong in the frozen archive, not loose source files.
        if any(path.suffix in {".py", ".pyw", ".spec"} for path in app.rglob("*")):
            raise RuntimeError("Unexpected loose Python source in app bundle")
        powershell = embed_powershell(app, work, arch)
        # Refresh the outer ad-hoc signature after embedding runtime resources.
        # This is not a Developer ID signature or Apple notarization.
        run("/usr/bin/codesign", "--force", "--sign", "-", app)
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
        actual_version = run(powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-Command", "$PSVersionTable.PSVersion.ToString()",
                             capture_output=True, text=True, timeout=60).stdout.strip()
        if actual_version != PWSH_VERSION:
            raise RuntimeError(f"Bundled PowerShell failed its startup check: {actual_version!r}")
        result = work / "self-test.json"
        run(executable, "--self-test", result, timeout=60)
        report = json.loads(result.read_text(encoding="utf-8"))
        if not report.get("ok") or report.get("version") != __version__:
            raise RuntimeError("Packaged app self-test failed")
        (payload / "Applications").symlink_to("/Applications", target_is_directory=True)
        name = f"CodexToolbox-{__version__}-macOS-{arch}.dmg"
        image = work / name
        run("/usr/bin/hdiutil", "create", "-volname", "Codex Toolbox",
            "-srcfolder", payload, "-format", "UDZO", image)
        run("/usr/bin/hdiutil", "verify", image)
        output = ROOT / "dist" / name
        image.replace(output)
        output.with_suffix(".dmg.sha256").write_text(f"{digest(output)}  {name}\n", encoding="utf-8")
        print(f"Installer: {output}\nArchitecture: {arch}\nSigning: ad-hoc; not notarized")
        print("Native GUI and Codex integration acceptance tests remain separate from these build checks.")


if __name__ == "__main__":
    main()
