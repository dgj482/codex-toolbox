#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Run this build on a Mac."
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install -r requirements-macos.txt
.venv-macos/bin/python -m unittest discover -s tests -p 'test_macos_compat.py'
.venv-macos/bin/python -m unittest discover -s tests -p 'test_surface_translation.py'
if command -v node >/dev/null 2>&1; then
  node scripts/check_ui_syntax.js
fi
.venv-macos/bin/python -m PyInstaller --noconfirm --clean Codex工具箱-macos.spec
.venv-macos/bin/python scripts/package_macos_app.py
echo "Built DMG for $(uname -m). Native acceptance checks: docs/macOS使用说明.md"
