#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This test launcher must run on macOS."
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.11 or newer from python.org, then run this again."
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
echo "Preparing Codex Toolbox on $(uname -m). First run needs internet access."
if [[ ! -x .venv-macos/bin/python ]]; then
  python3 -m venv .venv-macos
fi
.venv-macos/bin/python -m pip install -r requirements-macos.txt
.venv-macos/bin/python -m unittest discover -s tests -p 'test_macos_compat.py'
.venv-macos/bin/python codex_app.py --self-test
echo "Starting Codex Toolbox. Closing its window exits the toolbox."
exec .venv-macos/bin/python codex_app.py
