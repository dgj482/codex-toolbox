"""Smoke-test the frozen windowed EXE's command-auth stdout pipe with dummy secrets."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secret_store


def main() -> None:
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Missing executable: {executable}")
    with tempfile.TemporaryDirectory(prefix="auth-smoke-", dir=executable.parent) as temp:
        data_dir = Path(temp) / "CodexToolbox"
        data_dir.mkdir()
        (data_dir / "providers.json").write_text(json.dumps({"providers": [{
            "id": "single-file-smoke",
            "api_key_dpapi": secret_store.protect_secret("dummy-provider-token"),
            "proxy_token_dpapi": secret_store.protect_secret("dummy-proxy-token"),
        }]}), encoding="utf-8")
        claude_id = "claude-" + "0" * 32
        (data_dir / "claude_providers.json").write_text(json.dumps({"providers": [{
            "id": claude_id,
            "api_key_dpapi": secret_store.protect_secret("dummy-claude-token"),
        }]}), encoding="utf-8")
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = temp
        for name in list(environment):
            if name.startswith("_PYI_"):
                environment.pop(name)
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for args, expected in (
            (["--auth-secret", "single-file-smoke"], b"dummy-provider-token"),
            (["--auth-secret", "--proxy", "single-file-smoke"], b"dummy-proxy-token"),
            (["--claude-secret", claude_id], b"dummy-claude-token"),
        ):
            result = subprocess.run(
                [str(executable), *args],
                capture_output=True, env=environment, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0 or result.stdout != expected:
                raise SystemExit(f"Frozen command-auth pipe failed: exit={result.returncode}, bytes={len(result.stdout)}")
        print("Single-file Codex, proxy and Claude credential pipes OK")


if __name__ == "__main__":
    main()
