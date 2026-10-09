"""Generate Windows version resources and a reproducible release manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__, __version_tuple__  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_version_info(destination: Path) -> None:
    numeric = repr(tuple(__version_tuple__))
    destination.write_text(
        f"""VSVersionInfo(ffi=FixedFileInfo(filevers={numeric}, prodvers={numeric}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('080404B0', [StringStruct('CompanyName', 'Codex Toolbox Community'), StringStruct('FileDescription', 'Codex Toolbox'), StringStruct('FileVersion', '{__version__}'), StringStruct('InternalName', 'CodexToolbox'), StringStruct('OriginalFilename', 'Codex工具箱.exe'), StringStruct('ProductName', 'Codex Toolbox'), StringStruct('ProductVersion', '{__version__}')])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])])\n""",
        encoding="utf-8",
    )


def source_fingerprint() -> tuple[str, list[dict[str, str]]]:
    included = []
    excluded_parts = {".venv", "build", "dist", "__pycache__", ".pytest_cache", "test-release"}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name.startswith("work-") or any(
            part in excluded_parts or part.startswith("edge-preview-profile") for part in path.parts
        ):
            continue
        if path.suffix.lower() not in {".py", ".html", ".js", ".ps1", ".md", ".toml", ".json", ".spec"}:
            continue
        relative = path.relative_to(ROOT).as_posix()
        included.append({"path": relative, "sha256": sha256(path)})
    canonical = "".join(f"{item['path']}\0{item['sha256']}\n" for item in included).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), included


def write_manifest(package_dir: Path, destination: Path) -> None:
    artifacts = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.resolve() != destination.resolve():
            artifacts.append({
                "path": path.relative_to(package_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            })
    source_hash, source_files = source_fingerprint()
    manifest = {
        "schema": 1,
        "version": __version__,
        "python": platform.python_version(),
        "source_sha256": source_hash,
        "source_files": source_files,
        "artifacts": artifacts,
    }
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    command, target = sys.argv[1], Path(sys.argv[2]).resolve()
    if command == "version-info":
        write_version_info(target)
    elif command == "manifest":
        write_manifest(target.parent, target)
    else:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

