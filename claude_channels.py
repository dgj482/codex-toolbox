"""Saved Claude Desktop providers and reversible native 1P/3P configuration.

Uses the documented Claude-3p configLibrary. Keys remain DPAPI protected;
Claude retrieves them through its documented credential-helper interface.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import uuid

import secret_store
from provider_style import stored_css

OFFICIAL = "claude-official"
PROFILE_ID = "c0de0000-0000-4000-8000-000000000031"


def read_object(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"配置不是 JSON 对象：{path.name}")
    return value


def write_object(path, value):
    # Reread immediately before replacing an existing file.
    if path.exists():
        path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_url(value):
    value = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        # urllib errors can include the input URL, including embedded secrets.
        raise ValueError("网关地址或端口无效") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("请填写不含密钥的 HTTP(S) 网关地址")
    if parts.path.endswith(("/messages", "/chat/completions", "/responses")):
        raise ValueError("请填写网关根地址或 /v1 地址，不要填写完整请求路径")
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and (parts.scheme, port) not in (("http", 80), ("https", 443)):
        host += ":" + str(port)
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def api_url(base, endpoint):
    return base.rstrip("/") + ("/" if base.rstrip("/").endswith("/v1") else "/v1/") + endpoint


def helper_command(provider_id):
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), ["--claude-secret", provider_id]
    return str(Path(sys.executable).resolve()), [str(Path(__file__).with_name("codex_app.py").resolve()), "--claude-secret", provider_id]


class ClaudeChannels:
    def __init__(self, data_dir=None, local_dir=None):
        self.data_dir = Path(data_dir or secret_store.app_data_dir())
        self.local_dir = Path(local_dir or os.environ.get("LOCALAPPDATA", ""))
        self.file = self.data_dir / "claude_providers.json"
        self.library = self.local_dir / "Claude-3p" / "configLibrary"
        self.meta = self.library / "_meta.json"
        self.profile = self.library / (PROFILE_ID + ".json")
        self.configs = [self.local_dir / name / "claude_desktop_config.json" for name in ("Claude", "Claude-3p")]
        self.lock = threading.RLock()

    def _load(self):
        value = read_object(self.file)
        value.setdefault("providers", [])
        if not isinstance(value["providers"], list):
            raise ValueError("Claude 供应商列表损坏")
        return value

    def _provider(self, provider_id):
        for provider in self._load()["providers"]:
            if provider["id"] == provider_id:
                return provider
        raise ValueError("找不到 Claude 供应商")

    def secret(self, provider_id):
        provider = self._provider(provider_id)
        return secret_store.unprotect_secret(provider.get("api_key_dpapi", ""))

    def list(self):
        with self.lock:
            store = self._load()
            native = read_object(self.configs[1])
            meta = read_object(self.meta)
            current = OFFICIAL if native.get("deploymentMode", "1p") != "3p" else (
                store.get("selected_id", "") if meta.get("appliedId") == PROFILE_ID else "external")
            rows = [{"id": OFFICIAL, "name": "Claude 官方连接", "official": True, "current": current == OFFICIAL}]
            for provider in store["providers"]:
                rows.append({key: provider.get(key, "") for key in ("id", "name", "base_url", "auth_scheme", "models", "card_css")}
                    | {"official": False, "current": provider["id"] == current, "has_key": bool(provider.get("api_key_dpapi"))})
            return {"providers": rows, "current_id": current, "supported": os.name == "nt"}

    def _validated_provider(self, data, old=None):
        old = old or {}
        provider_id = str(data.get("id") or "claude-" + uuid.uuid4().hex)
        if not re.fullmatch(r"claude-[0-9a-f]{32}", provider_id):
            raise ValueError("无效供应商 ID")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("请填写供应商名称")
        scheme = data.get("auth_scheme", "x-api-key")
        if scheme not in ("x-api-key", "bearer"):
            raise ValueError("无效认证方式")
        models = list(dict.fromkeys(x.strip() for x in str(data.get("models") or "").splitlines() if x.strip()))
        if any(not re.fullmatch(r"(?:anthropic/)?claude-(?:sonnet|opus|haiku|fable)-[\w.-]+", model) for model in models):
            raise ValueError("原生通道需要 Claude Sonnet/Opus/Haiku 模型 ID；其他模型需要格式转换网关")
        provider = {"id": provider_id, "name": name, "base_url": normalize_url(data.get("base_url")),
                    "auth_scheme": scheme, "models": "\n".join(models),
                    "card_css": stored_css(data.get("card_css", old.get("card_css", "")))}
        if old.get("import_source_id"):
            provider["import_source_id"] = old["import_source_id"]
        return provider

    def save(self, data):
        with self.lock:
            store = self._load()
            old = next((p for p in store["providers"] if p["id"] == data.get("id")), {})
            provider = self._validated_provider(data, old)
            key = str(data.get("api_key") or "").strip()
            encrypted = secret_store.protect_secret(key) if key else old.get("api_key_dpapi", "")
            if not encrypted:
                raise ValueError("请填写 API Key")
            provider["api_key_dpapi"] = encrypted
            provider_id = provider["id"]
            store["providers"] = [provider if p["id"] == provider_id else p for p in store["providers"]]
            if not old:
                store["providers"].append(provider)
            write_object(self.file, store)
            return provider_id

    def delete(self, provider_id):
        with self.lock:
            if self.list()["current_id"] == provider_id:
                raise ValueError("请先切换到另一个通道，再删除当前供应商")
            self._provider(provider_id)
            store = self._load()
            store["providers"] = [p for p in store["providers"] if p["id"] != provider_id]
            write_object(self.file, store)

    def import_native(self):
        """Import supported local profiles once, without applying or executing them."""
        with self.lock:
            store = self._load()
            try:
                meta = read_object(self.meta)
            except (OSError, ValueError):
                raise ValueError("Claude 本地配置索引无法读取或 JSON 损坏；未导入任何供应商") from None
            entries = meta.get("entries", [])
            if not isinstance(entries, list):
                raise ValueError("Claude 本地配置索引 entries 必须是列表；未导入任何供应商")
            report = {"imported": 0, "duplicates": 0, "skipped": 0, "failed": 0, "items": [], "imported_ids": []}
            def record(name, status, message):
                report[status] += 1
                report["items"].append({"name": name, "status": status, "message": message})

            # Compare URL + authentication + models + key, not address alone.
            # Plaintext keys stay local and never appear in the report or logs.
            def route(provider):
                return (normalize_url(provider["base_url"]), provider.get("auth_scheme", "x-api-key"),
                        tuple(sorted(set(provider.get("models", "").splitlines()))))
            known = {}
            unknown_keys = set()
            sources = {p.get("import_source_id") for p in store["providers"] if p.get("import_source_id")}
            for provider in store["providers"]:
                signature = route(provider)
                try:
                    key = secret_store.unprotect_secret(provider.get("api_key_dpapi", ""))
                    if key:
                        known.setdefault(signature, set()).add(key)
                    else:
                        unknown_keys.add(signature)
                except Exception:
                    unknown_keys.add(signature)

            for index, entry in enumerate(entries, 1):
                name = entry.get("name") if isinstance(entry, dict) else None
                name = name.strip()[:120] if isinstance(name, str) and name.strip() else f"配置 {index}"
                try:
                    entry_id = str(uuid.UUID(str(entry.get("id", "")))) if isinstance(entry, dict) else ""
                except ValueError:
                    entry_id = ""
                if not entry_id:
                    record(name, "failed", "配置 ID 无效")
                    continue
                if entry_id == PROFILE_ID:
                    record(name, "skipped", "工具箱自身生成的配置，无需再次导入")
                    continue
                if entry_id in sources:
                    record(name, "duplicates", "此配置已导入；保留工具箱中的现有条目")
                    continue
                path = self.library / (entry_id + ".json")
                try:
                    if not path.is_file():
                        record(name, "failed", "配置文件不存在")
                        continue
                    profile = read_object(path)
                except (OSError, ValueError):
                    record(name, "failed", "配置文件无法读取或 JSON 损坏")
                    continue
                if profile.get("inferenceProvider") != "gateway":
                    record(name, "skipped", "不是可导入的网关配置")
                    continue
                if profile.get("inferenceCredentialKind") not in (None, "", "static", "api-key"):
                    record(name, "skipped", "使用动态凭据；请手动添加供应商，导入不会执行凭据脚本")
                    continue
                key = profile.get("inferenceGatewayApiKey")
                if not isinstance(key, str) or not key.strip():
                    record(name, "skipped", "没有可导入的静态 API Key，请手动添加")
                    continue
                key = key.strip()
                models = profile.get("inferenceModels")
                if models is None:
                    models = []
                if isinstance(models, str):
                    models = models.splitlines()
                if not isinstance(models, list) or any(not isinstance(m, (str, dict)) for m in models):
                    record(name, "failed", "模型列表格式无效")
                    continue
                model_ids = [m if isinstance(m, str) else m.get("id") or m.get("name") for m in models]
                if any(not isinstance(m, str) or not m.strip() for m in model_ids):
                    record(name, "failed", "模型条目缺少有效 ID")
                    continue
                try:
                    provider = self._validated_provider({"name": name,
                        "base_url": profile.get("inferenceGatewayBaseUrl"),
                        "auth_scheme": profile.get("inferenceGatewayAuthScheme", "bearer"),
                        "models": "\n".join(model_ids)})
                except ValueError as error:
                    record(name, "failed", str(error))
                    continue
                signature = route(provider)
                if key in known.get(signature, set()):
                    record(name, "duplicates", "地址、认证、模型及密钥与现有供应商相同")
                    continue
                try:
                    provider["api_key_dpapi"] = secret_store.protect_secret(key)
                    if not provider["api_key_dpapi"]:
                        raise ValueError("empty protected key")
                except Exception:
                    raise ValueError("密钥安全存储失败；本次导入未保存，原供应商保持不变") from None
                provider["import_source_id"] = entry_id
                store["providers"].append(provider)
                sources.add(entry_id)
                known.setdefault(signature, set()).add(key)
                report["imported_ids"].append(provider["id"])
                record(name, "imported", "已导入；同地址已有密钥无法比较，请检查是否重复" if signature in unknown_keys else "已导入，尚未切换通道")

            if report["imported"]:
                try:
                    write_object(self.file, store)
                except OSError:
                    raise ValueError("无法保存供应商；本次导入未写入，原供应商保持不变") from None
            return report

    def check(self, provider_id):
        provider = self._provider(provider_id)
        key = self.secret(provider_id)
        headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        headers["x-api-key" if provider["auth_scheme"] == "x-api-key" else "Authorization"] = (
            key if provider["auth_scheme"] == "x-api-key" else "Bearer " + key)
        models = provider["models"].splitlines()
        if not models:
            with urllib.request.urlopen(urllib.request.Request(api_url(provider["base_url"], "models"), headers=headers), timeout=15) as response:
                value = json.load(response)
            models = [str(item.get("id", "")) for item in value.get("data", []) if isinstance(item, dict)]
            models = [m for m in models if re.fullmatch(r"(?:anthropic/)?claude-(?:sonnet|opus|haiku|fable)-[\w.-]+", m)]
            if not models:
                raise ValueError("未发现 Claude 模型，请手动填写模型 ID；OpenAI 专用网关不能原生接入")
        body = json.dumps({"model": models[0], "max_tokens": 1, "messages": [{"role": "user", "content": "."}]}).encode()
        request = urllib.request.Request(api_url(provider["base_url"], "messages"), data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                value = json.load(response)
            if value.get("type") != "message" or not isinstance(value.get("content"), list):
                raise ValueError("网关未返回 Anthropic Messages 格式")
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise ValueError(f"网关预检失败（HTTP {status}）；请检查地址、Key 和 Anthropic API 支持") from None
        return "Anthropic Messages API 连接正常"

    def apply(self, provider_id):
        """Apply only after the caller has fully stopped Claude. Preserve unrelated keys."""
        with self.lock:
            if os.name != "nt":
                raise ValueError("Claude 桌面通道切换目前仅支持 Windows")
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Policies\Claude") as key:
                        values = [winreg.EnumValue(key, i)[0] for i in range(winreg.QueryInfoKey(key)[1])]
                except FileNotFoundError:
                    values = []
                if any(name.startswith("inference") or name == "disableDeploymentModeChooser" for name in values):
                    raise ValueError("Claude 通道由组织策略管理，不能从工具箱覆盖")
            store = self._load()
            provider = None if provider_id == OFFICIAL else self._provider(provider_id)
            snapshots = {p: p.read_bytes() if p.exists() else None for p in [*self.configs, self.meta, self.profile, self.file]}
            docs = {p: read_object(p) for p in snapshots}
            meta = docs[self.meta]
            entries = meta.setdefault("entries", [])
            if not isinstance(entries, list):
                raise ValueError("Claude 配置目录索引损坏，未执行切换")
            mode = "1p" if provider is None else "3p"
            for config in self.configs:
                docs[config]["deploymentMode"] = mode
            if provider:
                executable, args = helper_command(provider_id)
                profile = docs[self.profile]
                for name in list(profile):
                    if name.startswith("inference"):
                        profile.pop(name)
                profile.update({"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": provider["base_url"],
                    "inferenceGatewayAuthScheme": provider["auth_scheme"], "inferenceCredentialKind": "helper-script",
                    "inferenceCredentialHelperWindows": executable, "inferenceCredentialHelperArgs": args,
                    "disableDeploymentModeChooser": True, "modelDiscoveryEnabled": not bool(provider["models"])})
                if provider["models"]:
                    profile["inferenceModels"] = provider["models"].splitlines()
                entries[:] = [e for e in entries if e.get("id") != PROFILE_ID]
                entries.append({"id": PROFILE_ID, "name": "Codex 工具箱 · " + provider["name"]})
                meta["appliedId"] = PROFILE_ID
            else:
                entries[:] = [e for e in entries if e.get("id") != PROFILE_ID]
                if meta.get("appliedId") == PROFILE_ID:
                    meta.pop("appliedId", None)
                for name in list(docs[self.profile]):
                    if name.startswith("inference") or name in ("disableDeploymentModeChooser", "modelDiscoveryEnabled"):
                        docs[self.profile].pop(name)
            store["selected_id"] = provider_id
            docs[self.file] = store
            backup = self.data_dir / "claude_channel_backups" / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
            backup.mkdir(parents=True)
            manifest = []
            for index, (path, original) in enumerate(snapshots.items()):
                filename = f"{index}.json"
                if original is not None:
                    (backup / filename).write_bytes(original)
                manifest.append({"path": str(path), "backup": filename, "existed": original is not None})
            write_object(backup / "manifest.json", {"files": manifest})
            changed = []
            try:
                for path, value in docs.items():
                    latest = path.read_bytes() if path.exists() else None
                    if latest != snapshots[path]:
                        raise ValueError("Claude 配置在切换期间发生变化，已撤销本次切换")
                    changed.append(path)
                    write_object(path, value)
            except Exception:
                for path in reversed(changed):
                    if snapshots[path] is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.read_bytes() if path.exists() else None
                        path.write_bytes(snapshots[path])
                raise
            return str(backup)


def stop_claude_app():
    """Stop only installed AnthropicClaude processes, including its tray instance."""
    from claude_page_loader import _process_path
    root = str((Path(os.environ["LOCALAPPDATA"]) / "AnthropicClaude").resolve()).lower() + os.sep
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法读取 Claude 进程，未修改通道")
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) < 2 or row[0].lower() != "claude.exe":
            continue
        pid = int(row[1])
        if _process_path(pid).lower().startswith(root):
            killed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if killed.returncode and _process_path(pid).lower().startswith(root):
                raise RuntimeError("Claude 未完全退出，未修改通道")
