# -*- coding: utf-8 -*-
"""防止把真实密钥、开发者绝对路径或运行数据提交到公开仓库。"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".html", ".md", ".toml", ".json", ".yml", ".yaml", ".ps1", ".txt", ".spec"}
FORBIDDEN_NAMES = {"providers.json", "thread_routes.json"}
RULES = {
    "疑似 OpenAI 格式密钥": re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    "开发者绝对路径": re.compile(r"(?:C:\\\\Users\\\\|D:\\\\WorkBuddy\\\\)", re.I),
    "非示例的 bearer token": re.compile(r"Bearer\s+[A-Za-z0-9_-]{24,}", re.I),
}


def main() -> int:
    failures = []
    for path in ROOT.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.name in FORBIDDEN_NAMES:
            failures.append(f"{path.relative_to(ROOT)}: 禁止提交运行数据文件")
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in RULES.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    if failures:
        print("\n".join(failures))
        return 1
    print("敏感信息扫描通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
