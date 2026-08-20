# -*- coding: utf-8 -*-
"""Codex 工具箱公开测试版：多供应商管理 + 本地翻译代理 + 官方会话归档。
供应商配置存于当前用户的 %LOCALAPPDATA%\\CodexToolbox，可增删改，一键切换。
支持"本地翻译"供应商（proxy=true）：Codex -> 本地代理 -> 上游 Chat Completions（如 DeepSeek 官方）。
运行：python codex_app.py（开发模式需安装 requirements.txt 中的依赖）。
"""
import ttkbootstrap as ttk
from ttkbootstrap.constants import PRIMARY, SUCCESS, INFO, WARNING, DANGER, OUTLINE
from tkinter import messagebox
import sqlite3
import os
import re
import json
import shutil
import time
import datetime
import subprocess
import glob
import threading
import queue
import sys
import codex_proxy
import secret_store


def _app_dir():
    """开发版和打包版都使用系统数据目录，避免把密钥写入源码仓库。"""
    return secret_store.app_data_dir()


CONFIG = os.path.expanduser(r"~\.codex\config.toml")
STATE_DB = os.path.expanduser(r"~\.codex\state_5.sqlite")
CATALOG_DB = os.path.expanduser(r"~\.codex\sqlite\codex-dev.db")
BAK_DIR = os.path.join(_app_dir(), "archive_backups")
PROVIDERS_FILE = os.path.join(_app_dir(), "providers.json")
THREAD_ROUTES_FILE = os.path.join(_app_dir(), "thread_routes.json")
PROXY_PORT = 18732
AUTH_HELPER_NAME = "Codex工具箱认证助手.exe"

OFFICIAL_ID = "openai"
PROVIDER_NAMES = {}

MODEL_PRESETS = [
    "deepseek-chat", "deepseek-reasoner",
    "qwen-plus", "qwen-max", "qwen-turbo", "qwen-long",
    "glm-4-plus", "glm-4-air", "glm-4-flash",
    "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
    "abab6.5s-chat", "abab6.5g-chat",
    "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
    "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna",
]

# 预设供应商模板。proxy=True 表示上游只支持 Chat Completions，需本地翻译代理（Responses→Chat 互转）；
# proxy=False 表示上游原生支持 Responses API，Codex 直连（如通义千问 DashScope compatible-mode，2026-08-19 实测支持 /v1/responses）。
# 注意：proxy 通道走 127.0.0.1 本地代理，若用户系统代理(Clash)劫持本地请求会导致 502——能直连的厂商优先直连。
PRESETS = [
    {"label": "DeepSeek 官方", "name": "DeepSeek 官方", "kind": "direct", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-v4-pro",
     "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"],
     "wire_api": "responses", "proxy": True},
    {"label": "通义千问（阿里 DashScope）", "name": "通义千问", "kind": "direct", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen3.7-max",
     "models": ["qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.7-flash", "qwen-max"],
     "wire_api": "responses", "proxy": False},
    {"label": "智谱 GLM", "name": "智谱 GLM", "kind": "direct", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-5.3", "models": ["glm-5.3", "glm-5.2", "glm-5.1"],
     "wire_api": "responses", "proxy": True},
    {"label": "Kimi（月之暗面）", "name": "Kimi", "kind": "direct", "base_url": "https://api.moonshot.cn/v1",
     "model": "kimi-k3", "models": ["kimi-k3", "kimi-k2.6", "kimi-k2.5"],
     "wire_api": "responses", "proxy": True},
    {"label": "豆包（火山方舟）", "name": "豆包", "kind": "direct", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "model": "ep-你的推理接入点", "models": ["ep-你的推理接入点ID"],
     "wire_api": "responses", "proxy": True},
    {"label": "硅基流动 SiliconFlow", "name": "硅基流动", "kind": "relay", "base_url": "https://api.siliconflow.cn/v1",
     "model": "deepseek-ai/DeepSeek-V4",
     "models": ["deepseek-ai/DeepSeek-V4", "deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-R1"],
     "wire_api": "responses", "proxy": True},
    {"label": "MiniMax", "name": "MiniMax", "kind": "direct", "base_url": "https://api.minimax.chat/v1",
     "model": "MiniMax-M2.5", "models": ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5"],
     "wire_api": "responses", "proxy": True},
    {"label": "OpenRouter", "name": "OpenRouter", "kind": "relay", "base_url": "https://openrouter.ai/api/v1",
     "model": "anthropic/claude-opus-5",
     "models": ["anthropic/claude-opus-5", "openai/gpt-5.6-sol", "deepseek/deepseek-v4-pro"],
     "wire_api": "responses", "proxy": True},
]


def codex_running():
    try:
        for image_name in ("Codex.exe", "ChatGPT.exe"):
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if image_name.encode("ascii") in out.stdout:
                return True
        return False
    except Exception:
        return False


def read_config_field(key):
    try:
        with open(CONFIG, encoding="utf-8") as f:
            content = f.read()
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', content, re.M)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def read_provider():
    return read_config_field("model_provider")


def read_model():
    return read_config_field("model")


def parse_sections(content):
    sections = {}
    current = None
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            current = m.group(1)
            sections.setdefault(current, {})
        elif current and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            sections[current][k.strip()] = v.strip().strip('"')
    return sections


def _is_dashscope_provider(p):
    """仅按官方 DashScope 域名识别，避免把第三方千问中转误改成直连。"""
    urls = " ".join(str(p.get(k, "")) for k in ("base_url", "upstream_base")).lower()
    return "dashscope.aliyuncs.com" in urls


def normalize_provider(p):
    """把供应商配置归一化，并修复历史版本生成的千问本地代理配置。"""
    p = dict(p or {})
    p.setdefault("kind", "official" if p.get("id") == OFFICIAL_ID else "relay")
    p.setdefault("wire_api", "responses")
    if _is_dashscope_provider(p):
        p["kind"] = "direct"
        p["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        p["wire_api"] = "responses"
        p["model"] = p.get("model") or "qwen3.8-max"
        p["proxy"] = False
        p.pop("upstream_base", None)
    return p


def _providers_from_config():
    """从 Codex 配置中发现工具箱尚未登记的供应商；不输出或记录密钥。"""
    found = []
    try:
        with open(CONFIG, encoding="utf-8") as f:
            sections = parse_sections(f.read())
        for key, sec in sections.items():
            if not key.startswith("model_providers."):
                continue
            pid = key.split(".", 1)[1]
            if "." in pid:
                continue
            found.append(normalize_provider({
                "id": pid,
                "name": sec.get("name", pid),
                "kind": "relay",
                "base_url": sec.get("base_url", ""),
                "api_key": "",
                "model": "",
                "wire_api": sec.get("wire_api", "responses"),
            }))
    except Exception:
        pass
    return found


def load_providers():
    if os.path.exists(PROVIDERS_FILE):
        try:
            with open(PROVIDERS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            provs = []
            plaintext_migration = False
            needs_rewrite = data.get("version") != 2
            for raw in data.get("providers", []):
                item = dict(raw)
                encrypted = item.pop("api_key_dpapi", "")
                plaintext = item.get("api_key", "")
                if encrypted:
                    try:
                        item["api_key"] = secret_store.unprotect_secret(encrypted)
                    except Exception:
                        item["api_key"] = ""
                elif plaintext:
                    plaintext_migration = True
                provs.append(normalize_provider(item))
            # 早期版本迁移：只自动补回官方 DashScope 配置，避免已删除的普通供应商反复出现。
            known = {p.get("id") for p in provs}
            for p in _providers_from_config():
                if p.get("id") not in known and _is_dashscope_provider(p):
                    provs.append(p)
                    known.add(p.get("id"))
                    needs_rewrite = True
            if plaintext_migration or needs_rewrite:
                save_providers(provs)
            return provs
        except Exception:
            pass
    provs = []
    try:
        provs.extend(_providers_from_config())
    except Exception:
        pass
    official = {"id": OFFICIAL_ID, "name": "官方 ChatGPT Plus", "kind": "official", "model": read_model()}
    if not any(p["id"] == OFFICIAL_ID for p in provs):
        provs.insert(0, official)
    save_providers(provs)
    return provs


def save_providers(provs):
    provs = [normalize_provider(p) for p in provs]
    serialized = []
    for provider in provs:
        item = dict(provider)
        secret = item.pop("api_key", "")
        item.pop("api_key_dpapi", None)
        if secret:
            item["api_key_dpapi"] = secret_store.protect_secret(secret)
        serialized.append(item)
    tmp = PROVIDERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "providers": serialized}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROVIDERS_FILE)


def load_thread_routes():
    try:
        with open(THREAD_ROUTES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("routes", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_thread_routes(routes):
    with open(THREAD_ROUTES_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "routes": routes}, f, ensure_ascii=False, indent=2)


def set_thread_route_mode(tids, mode, provider=None):
    """记录工具箱管理语义。follow 才会在以后切换全局默认时同步。"""
    if mode not in ("follow", "fixed"):
        raise ValueError("会话模式必须是 follow 或 fixed")
    routes = load_thread_routes()
    for tid in tids:
        routes[tid] = {"mode": mode, "provider": provider or read_provider()}
    save_thread_routes(routes)


def following_thread_ids():
    return [tid for tid, item in load_thread_routes().items()
            if isinstance(item, dict) and item.get("mode") == "follow"]


def migrate_active_dashscope():
    """若当前正使用旧版千问本地代理配置，自动备份并改为 DashScope 官方直连。"""
    pid = read_provider()
    p = next((x for x in load_providers() if x.get("id") == pid), None)
    if not p or not _is_dashscope_provider(p):
        return False
    try:
        with open(CONFIG, encoding="utf-8") as f:
            content = f.read()
        sections = parse_sections(content)
        current_url = sections.get(f"model_providers.{pid}", {}).get("base_url", "")
        if "dashscope.aliyuncs.com" in current_url.lower():
            return False
        os.makedirs(BAK_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CONFIG, os.path.join(BAK_DIR, f"config.toml.bak_{stamp}"))
        apply_provider(pid)
        return True
    except Exception:
        return False


def _toml_string(value):
    """JSON 双引号字符串兼容 TOML 基本字符串，可安全转义 Windows 路径。"""
    return json.dumps(str(value), ensure_ascii=False)


def _auth_helper_command():
    """返回认证助手命令；打包版把助手复制到稳定的当前用户目录。"""
    if not getattr(sys, "frozen", False):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_helper.py")
        return sys.executable, [script]

    sibling = os.path.join(os.path.dirname(sys.executable), AUTH_HELPER_NAME)
    if not os.path.isfile(sibling):
        raise RuntimeError(f"缺少认证助手：{AUTH_HELPER_NAME}。请重新解压完整发布包。")
    bin_dir = os.path.join(_app_dir(), "bin")
    os.makedirs(bin_dir, exist_ok=True)
    installed = os.path.join(bin_dir, AUTH_HELPER_NAME)
    if not os.path.isfile(installed) or os.path.getsize(installed) != os.path.getsize(sibling):
        tmp = installed + ".tmp"
        shutil.copy2(sibling, tmp)
        os.replace(tmp, installed)
    return installed, []


def _provider_block(pid, p):
    block = [f"[model_providers.{pid}]"]
    if p.get("name"):
        block.append(f'name = {_toml_string(p["name"])}')
    if p.get("base_url"):
        block.append(f'base_url = {_toml_string(p["base_url"])}')
    block.append(f'wire_api = {_toml_string(p.get("wire_api", "responses"))}')
    if p.get("api_key") and not p.get("proxy"):
        command, prefix_args = _auth_helper_command()
        args = prefix_args + [pid]
        block.extend([
            "",
            f"[model_providers.{pid}.auth]",
            f"command = {_toml_string(command)}",
            "args = [" + ", ".join(_toml_string(item) for item in args) + "]",
            "timeout_ms = 5000",
            "refresh_interval_ms = 0",
        ])
    else:
        block.append("requires_openai_auth = false")
    return block


def upsert_section(content, pid, p):
    headers = {f"[model_providers.{pid}]", f"[model_providers.{pid}.auth]"}
    lines = content.split("\n")
    kept = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped in headers
        if not skipping:
            kept.append(line)
    block = _provider_block(pid, p)
    prefix = "\n".join(kept).rstrip("\n")
    return prefix + ("\n\n" if prefix else "") + "\n".join(block) + "\n"


def apply_provider(pid, proxy_url=None):
    provs = load_providers()
    p = next((x for x in provs if x["id"] == pid), None)
    if not p:
        raise ValueError(f"供应商 {pid} 不存在")
    try:
        with open(CONFIG, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise RuntimeError("未找到 Codex 配置。请先安装、登录并至少启动一次 Codex。")
    provider_line = f"model_provider = {_toml_string(pid)}"
    if re.search(r'^model_provider\s*=', content, re.M):
        content = re.sub(r'^model_provider\s*=.*$', provider_line, content, count=1, flags=re.M)
    else:
        content = provider_line + "\n" + content
    if p.get("model"):
        model_line = f'model = {_toml_string(p["model"])}'
        if re.search(r'^model\s*=', content, re.M):
            content = re.sub(r'^model\s*=.*$', model_line, content, count=1, flags=re.M)
        else:
            content = model_line + "\n" + content
    if pid != OFFICIAL_ID:
        prov_copy = dict(p)
        if p.get("proxy"):
            # 本地翻译：config 指向本地代理
            prov_copy["base_url"] = proxy_url or f"http://127.0.0.1:{PROXY_PORT}/v1"
            prov_copy.pop("upstream_base", None)
        content = upsert_section(content, pid, prov_copy)
    os.makedirs(BAK_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(CONFIG, os.path.join(BAK_DIR, f"config.toml.bak_{stamp}"))
    tmp = CONFIG + ".toolbox.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp, CONFIG)
    return p["name"]


def _friendly_titles():
    titles = {}
    try:
        conn2 = sqlite3.connect(CATALOG_DB)
        cur2 = conn2.cursor()
        cur2.execute("SELECT thread_id, display_title FROM local_thread_catalog")
        for tid, dt in cur2.fetchall():
            if dt:
                titles[tid] = dt
        conn2.close()
    except Exception:
        pass
    return titles


def list_unarchived():
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute("SELECT id, title, model_provider, model FROM threads WHERE archived = 0 ORDER BY created_at")
    rows = cur.fetchall()
    conn.close()
    return rows, _friendly_titles()


def list_archived():
    """列出已归档会话。标题优先使用 Codex 状态库，不依赖已被官方移除的 catalog 记录。"""
    conn = sqlite3.connect(STATE_DB)
    try:
        rows = conn.execute(
            "SELECT id, title, model_provider, model FROM threads "
            "WHERE archived = 1 ORDER BY archived_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return rows


def _normal_windows_path(path):
    """把 Windows 扩展路径还原成普通路径，规避部分 Codex 版本归档时的 os error 2。"""
    if not path:
        return path
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _backup_sqlite(source, destination):
    """使用 SQLite 在线备份，确保 WAL 模式下也得到一致的数据库副本。"""
    src = sqlite3.connect(source, timeout=15)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _find_codex_executable():
    """定位桌面版自带的 codex.exe；不把 npm 的 .cmd 包装器当作桌面后端。"""
    candidates = []
    configured = os.environ.get("CODEX_EXECUTABLE")
    if configured:
        candidates.append(configured)
    found = shutil.which("codex.exe")
    if found:
        candidates.append(found)

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.extend(glob.glob(os.path.join(
        program_files, "WindowsApps", "OpenAI.Codex_*", "app", "resources", "codex.exe"
    )))
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.extend([
            os.path.join(local_app_data, "Microsoft", "WindowsApps", "codex.exe"),
            os.path.join(local_app_data, "Programs", "Codex", "resources", "codex.exe"),
        ])

    existing = []
    seen = set()
    for path in candidates:
        path = os.path.abspath(os.path.expandvars(path))
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            existing.append(path)
    if not existing:
        raise RuntimeError("未找到 Codex 桌面版的 codex.exe，请先安装或更新 Codex 桌面版。")
    return max(existing, key=os.path.getmtime)


class _CodexAppServer:
    """最小 JSON-RPC 客户端：只通过官方 app-server 执行会话归档/恢复。"""

    def __init__(self, executable, timeout=20):
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.timeout = timeout
        self.next_id = 1
        self.lines = queue.Queue()
        self.stderr_lines = []
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.request("initialize", {
            "clientInfo": {"name": "codex-toolbox", "version": "0.1.0-beta"},
            "capabilities": {"optOutNotificationMethods": ["thread/started"]},
        })
        self._send({"method": "initialized"})

    def _read_stdout(self):
        for line in self.process.stdout:
            self.lines.put(line)

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def _send(self, message):
        if self.process.poll() is not None:
            detail = "\n".join(self.stderr_lines[-5:])
            raise RuntimeError(f"Codex app-server 已退出。{detail}")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"Codex 官方接口失败：{detail}")
            return message.get("result")
        detail = "\n".join(self.stderr_lines[-5:])
        raise RuntimeError(f"等待 Codex 官方接口响应超时。{detail}")

    def close(self):
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except Exception:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _thread_ids_with_descendants(conn, target_ids):
    """官方归档会连同子会话一起处理；预检和备份也覆盖同一范围。"""
    result = []
    pending = list(dict.fromkeys(target_ids))
    has_edges = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_spawn_edges'"
    ).fetchone()
    while pending:
        tid = pending.pop(0)
        if tid in result:
            continue
        result.append(tid)
        if has_edges:
            children = conn.execute(
                "SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=?", (tid,)
            ).fetchall()
            pending.extend(row[0] for row in children)
    return result


def archive_threads(target_ids):
    """按 Codex 官方语义归档：预检/备份后调用 app-server 的 thread/archive。"""
    target_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
    if not target_ids:
        raise ValueError("没有可归档的会话。")

    executable = _find_codex_executable()
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        affected_ids = _thread_ids_with_descendants(conn, target_ids)
        placeholders = ",".join("?" for _ in affected_ids)
        rows = conn.execute(
            f"SELECT id, rollout_path, archived FROM threads WHERE id IN ({placeholders})",
            affected_ids,
        ).fetchall()
    finally:
        conn.close()

    row_map = {row[0]: row for row in rows}
    missing = [tid for tid in target_ids if tid not in row_map]
    if missing:
        raise RuntimeError(f"数据库中找不到会话：{', '.join(missing)}")

    originals = {}
    files_to_backup = []
    for tid in affected_ids:
        row = row_map.get(tid)
        if not row or row[2]:
            continue
        original_path = row[1]
        normal_path = _normal_windows_path(original_path)
        if not normal_path or not os.path.isfile(normal_path):
            raise RuntimeError(f"会话文件不存在，已取消归档：{tid}\n{normal_path or original_path}")
        originals[tid] = original_path
        files_to_backup.append(normal_path)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    operation_backup = os.path.join(BAK_DIR, f"archive_{stamp}")
    rollout_backup = os.path.join(operation_backup, "rollouts")
    os.makedirs(rollout_backup, exist_ok=False)
    _backup_sqlite(STATE_DB, os.path.join(operation_backup, "state_5.sqlite"))
    _backup_sqlite(CATALOG_DB, os.path.join(operation_backup, "codex-dev.db"))
    for source in files_to_backup:
        shutil.copy2(source, os.path.join(rollout_backup, os.path.basename(source)))

    # 当前 Codex Windows 版对部分 \\?\ 扩展路径归档会误报文件不存在；
    # 仅规范化路径表示，真正的移动、状态更新和 catalog 维护仍交给官方 app-server。
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        with conn:
            for tid, original_path in originals.items():
                conn.execute(
                    "UPDATE threads SET rollout_path=? WHERE id=?",
                    (_normal_windows_path(original_path), tid),
                )
    finally:
        conn.close()

    client = None
    archived_by_this_run = []
    try:
        client = _CodexAppServer(executable)
        for tid in target_ids:
            check = sqlite3.connect(STATE_DB)
            try:
                archived = check.execute("SELECT archived FROM threads WHERE id=?", (tid,)).fetchone()
            finally:
                check.close()
            if archived and archived[0]:
                continue
            client.request("thread/archive", {"threadId": tid})
            archived_by_this_run.append(tid)

        verify = sqlite3.connect(STATE_DB)
        try:
            for tid in target_ids:
                row = verify.execute(
                    "SELECT archived, rollout_path FROM threads WHERE id=?", (tid,)
                ).fetchone()
                if not row or not row[0] or not os.path.isfile(_normal_windows_path(row[1])):
                    raise RuntimeError(f"官方接口返回后校验失败：{tid}")
        finally:
            verify.close()
    except Exception:
        if client:
            for tid in reversed(archived_by_this_run):
                try:
                    client.request("thread/unarchive", {"threadId": tid})
                except Exception:
                    pass
        restore = sqlite3.connect(STATE_DB, timeout=15)
        try:
            with restore:
                for tid, original_path in originals.items():
                    restore.execute("UPDATE threads SET rollout_path=? WHERE id=?", (original_path, tid))
        finally:
            restore.close()
        raise
    finally:
        if client:
            client.close()

    return stamp


def unarchive_threads(target_ids):
    """通过 Codex 官方 app-server 恢复已归档会话，操作前保留一致性备份。"""
    target_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
    if not target_ids:
        raise ValueError("没有可恢复的会话。")

    executable = _find_codex_executable()
    placeholders = ",".join("?" for _ in target_ids)
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        rows = conn.execute(
            f"SELECT id, rollout_path, archived FROM threads WHERE id IN ({placeholders})",
            target_ids,
        ).fetchall()
    finally:
        conn.close()
    row_map = {row[0]: row for row in rows}
    missing = [tid for tid in target_ids if tid not in row_map]
    if missing:
        raise RuntimeError(f"数据库中找不到会话：{', '.join(missing)}")

    files_to_backup = []
    for tid in target_ids:
        _, path, archived = row_map[tid]
        if not archived:
            continue
        normal_path = _normal_windows_path(path)
        if not normal_path or not os.path.isfile(normal_path):
            raise RuntimeError(f"已归档会话文件不存在，已取消恢复：{tid}")
        files_to_backup.append(normal_path)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    operation_backup = os.path.join(BAK_DIR, f"unarchive_{stamp}")
    rollout_backup = os.path.join(operation_backup, "rollouts")
    os.makedirs(rollout_backup, exist_ok=False)
    _backup_sqlite(STATE_DB, os.path.join(operation_backup, "state_5.sqlite"))
    _backup_sqlite(CATALOG_DB, os.path.join(operation_backup, "codex-dev.db"))
    for source in files_to_backup:
        shutil.copy2(source, os.path.join(rollout_backup, os.path.basename(source)))

    client = None
    restored_by_this_run = []
    try:
        client = _CodexAppServer(executable)
        for tid in target_ids:
            if not row_map[tid][2]:
                continue
            client.request("thread/unarchive", {"threadId": tid})
            restored_by_this_run.append(tid)

        verify = sqlite3.connect(STATE_DB)
        try:
            for tid in target_ids:
                row = verify.execute(
                    "SELECT archived, rollout_path FROM threads WHERE id=?", (tid,)
                ).fetchone()
                if not row or row[0] or not os.path.isfile(_normal_windows_path(row[1])):
                    raise RuntimeError(f"官方接口返回后恢复校验失败：{tid}")
        finally:
            verify.close()
    except Exception:
        if client:
            for tid in reversed(restored_by_this_run):
                try:
                    client.request("thread/archive", {"threadId": tid})
                except Exception:
                    pass
        raise
    finally:
        if client:
            client.close()
    return stamp


def set_thread_provider(tid, provider):
    """公开测试版不直接修改 Codex 内部数据库或会话 JSONL。"""
    raise RuntimeError(
        "公开测试版已暂停单会话通道切换。请使用全局通道；"
        "该功能将在迁移到官方 App Server 后恢复。"
    )


class ProviderDialog(ttk.Toplevel):
    def __init__(self, parent, app, provider=None):
        super().__init__(parent)
        self.app = app
        self.provider = provider
        is_official = provider is not None and provider.get("id") == OFFICIAL_ID
        self.title("编辑供应商" if provider else "添加供应商")
        self.geometry("640x560")
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        # 预设模板（仅添加时显示）
        self._preset_row = 0
        if not provider:
            ttk.Label(frm, text="预设模板").grid(row=0, column=0, sticky="w", pady=4)
            self.cb_preset = ttk.Combobox(frm, values=["（手动填写）"] + [p["label"] for p in PRESETS],
                                          width=38, state="readonly")
            self.cb_preset.grid(row=0, column=1, pady=4, padx=(8, 0))
            self.cb_preset.current(0)
            self.cb_preset.bind("<<ComboboxSelected>>", self.on_preset)
            row0 = 1
        else:
            row0 = 0

        ttk.Label(frm, text="名称 *").grid(row=row0 + 0, column=0, sticky="w", pady=4)
        self.e_name = ttk.Entry(frm, width=40)
        self.e_name.grid(row=row0 + 0, column=1, pady=4, padx=(8, 0))
        self.e_name.insert(0, (provider or {}).get("name", ""))

        ttk.Label(frm, text="Base URL").grid(row=row0 + 1, column=0, sticky="w", pady=4)
        self.e_url = ttk.Entry(frm, width=40)
        self.e_url.grid(row=row0 + 1, column=1, pady=4, padx=(8, 0))
        self.e_url.insert(0, (provider or {}).get("base_url", ""))

        ttk.Label(frm, text="API Key").grid(row=row0 + 2, column=0, sticky="w", pady=4)
        self.e_key = ttk.Entry(frm, width=40)
        self.e_key.grid(row=row0 + 2, column=1, pady=4, padx=(8, 0))
        self.e_key.insert(0, (provider or {}).get("api_key", ""))

        ttk.Label(frm, text="模型").grid(row=row0 + 3, column=0, sticky="w", pady=4)
        self.e_model = ttk.Combobox(frm, values=MODEL_PRESETS, width=38)
        self.e_model.grid(row=row0 + 3, column=1, pady=4, padx=(8, 0))
        self.e_model.set((provider or {}).get("model", ""))

        ttk.Label(frm, text="协议").grid(row=row0 + 4, column=0, sticky="w", pady=4)
        self.cb_wire = ttk.Combobox(frm, values=["responses", "chat"], width=38, state="readonly")
        self.cb_wire.grid(row=row0 + 4, column=1, pady=4, padx=(8, 0))
        self.cb_wire.set((provider or {}).get("wire_api", "responses"))

        self.var_proxy = ttk.BooleanVar(value=(provider or {}).get("proxy", False))
        self.cb_proxy = ttk.Checkbutton(frm, text="需要本地翻译（上游是 Chat Completions，如 DeepSeek 官方）",
                                        variable=self.var_proxy, command=self._toggle_proxy)
        self.cb_proxy.grid(row=row0 + 5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(frm, text="上游地址").grid(row=row0 + 6, column=0, sticky="w", pady=4)
        self.e_upstream = ttk.Entry(frm, width=40)
        self.e_upstream.grid(row=row0 + 6, column=1, pady=4, padx=(8, 0))
        self.e_upstream.insert(0, (provider or {}).get("upstream_base", "https://api.deepseek.com/v1"))
        ttk.Label(frm, text="（勾选本地翻译后，Codex 请求经本地代理转发到此地址）",
                  bootstyle="secondary", font=("Microsoft YaHei UI", 8)).grid(row=row0 + 7, column=0, columnspan=2, sticky="w")

        if is_official:
            ttk.Label(frm, text="官方为内置供应商，只需确认模型名称。", bootstyle=INFO).grid(row=row0 + 8, column=0, columnspan=2, pady=6)

        row_btn = ttk.Frame(frm)
        row_btn.grid(row=row0 + 9, column=0, columnspan=2, pady=(14, 0))
        ttk.Button(row_btn, text="保存", bootstyle=SUCCESS, command=self.on_save).pack(side="left", padx=(0, 10))
        ttk.Button(row_btn, text="取消", bootstyle=OUTLINE, command=self.destroy).pack(side="left")

        self._toggle_proxy()

    def on_preset(self, event=None):
        label = self.cb_preset.get()
        preset = next((p for p in PRESETS if p["label"] == label), None)
        if not preset:
            return
        # 关键：先解除 disabled（ttkbootstrap Entry 在 disabled 状态下 delete/insert 会被静默丢弃）
        self.e_upstream.configure(state="normal")
        self.e_name.delete(0, "end")
        self.e_name.insert(0, preset["name"])
        self.e_url.delete(0, "end")
        self.e_url.insert(0, preset["base_url"])
        # 联动：模型下拉换成该厂商的模型列表
        self.e_model.configure(values=preset.get("models") or MODEL_PRESETS)
        self.e_model.set(preset["model"])
        self.e_upstream.delete(0, "end")
        self.e_upstream.insert(0, preset["base_url"])
        self.cb_wire.set(preset["wire_api"])
        self.var_proxy.set(preset["proxy"])
        self._toggle_proxy()

    def _toggle_proxy(self):
        state = "normal" if self.var_proxy.get() else "disabled"
        self.e_upstream.configure(state=state)

    def on_save(self):
        name = self.e_name.get().strip()
        if not name:
            messagebox.showwarning("提示", "名称不能为空。", parent=self)
            return
        if self.provider:
            pid = self.provider["id"]
            kind = self.provider.get("kind", "relay")
        else:
            pid = re.sub(r"[^a-zA-Z0-9_]", "", name.lower())
            if not pid:
                pid = f"relay{int(time.time())}"
            kind = "relay"
            if any(p["id"] == pid for p in self.app.providers):
                pid = f"{pid}{int(time.time()) % 1000}"
        prov = {
            "id": pid,
            "name": name,
            "kind": kind,
            "base_url": self.e_url.get().strip(),
            "api_key": self.e_key.get().strip(),
            "model": self.e_model.get().strip(),
            "wire_api": self.cb_wire.get() or "responses",
            "requires_openai_auth": False,
        }
        if self.var_proxy.get():
            prov["proxy"] = True
            prov["upstream_base"] = self.e_upstream.get().strip() or "https://api.deepseek.com/v1"
        else:
            prov.pop("proxy", None)
            prov.pop("upstream_base", None)
        if kind == "official":
            prov["base_url"] = ""
            prov["api_key"] = ""
            prov.pop("proxy", None)
            prov.pop("upstream_base", None)
        if self.provider:
            for i, p in enumerate(self.app.providers):
                if p["id"] == self.provider["id"]:
                    self.app.providers[i] = prov
                    break
        else:
            self.app.providers.append(prov)
        save_providers(self.app.providers)
        self.app.refresh_provider_list()
        self.app.refresh_status()
        self.destroy()


class ToolboxApp:
    def __init__(self, root):
        self.root = root
        root.title("Codex 工具箱")
        root.geometry("1040x820")
        root.minsize(960, 740)

        self.var_provider = ttk.StringVar()
        self.var_model = ttk.StringVar()
        self.var_proc = ttk.StringVar()
        self.var_proxy_state = ttk.StringVar(value="代理：未启动")
        self.proxy_server = None
        self.providers = load_providers()
        self.thread_rows = []
        self.chan_rows = []

        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        root = self.root
        pad = {"padx": 14, "pady": 8}

        header = ttk.Frame(root)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="Codex 工具箱", font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        self.var_status_badge = ttk.StringVar(value="● 加载中")
        ttk.Label(header, textvariable=self.var_status_badge, bootstyle=INFO, font=("Microsoft YaHei UI", 10)).pack(side="right")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, **pad)

        # ===== Tab1 通道切换 =====
        tab_switch = ttk.Frame(nb, padding=12)
        nb.add(tab_switch, text=" 通道切换 ")
        ttk.Label(tab_switch, text="当前状态：", font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        row1 = ttk.Frame(tab_switch)
        row1.pack(fill="x", pady=(6, 10))
        ttk.Label(row1, text="通道：").pack(side="left")
        self.lbl_provider = ttk.Label(row1, textvariable=self.var_provider, font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_provider.pack(side="left", padx=(4, 14))
        ttk.Label(row1, text="模型：").pack(side="left")
        ttk.Label(row1, textvariable=self.var_model).pack(side="left", padx=(4, 14))
        ttk.Label(row1, text="进程：").pack(side="left")
        ttk.Label(row1, textvariable=self.var_proc).pack(side="left", padx=(4, 0))
        ttk.Label(tab_switch, textvariable=self.var_proxy_state, bootstyle=WARNING,
                  font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))

        ttk.Label(tab_switch, text="供应商列表（选中 → 切换。切换后需完全退出 Codex 并重开生效）",
                  font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(0, 4))
        self.tree_prov = ttk.Treeview(tab_switch, height=7, show="tree", selectmode="browse")
        self.tree_prov.pack(fill="both", expand=True)
        row_pbtn = ttk.Frame(tab_switch)
        row_pbtn.pack(fill="x", pady=(8, 0))
        ttk.Button(row_pbtn, text="切换到选中", bootstyle=PRIMARY, width=14, command=self.switch_selected).pack(side="left", padx=(0, 8))
        ttk.Button(row_pbtn, text="添加供应商", bootstyle=SUCCESS, width=12, command=lambda: ProviderDialog(self.root, self)).pack(side="left", padx=(0, 8))
        ttk.Button(row_pbtn, text="编辑选中", bootstyle=WARNING, width=10, command=self.edit_selected).pack(side="left", padx=(0, 8))
        ttk.Button(row_pbtn, text="删除选中", bootstyle=DANGER, width=10, command=self.delete_selected).pack(side="left")

        # ===== Tab2 单会话通道 =====
        tab_chan = ttk.Frame(nb, padding=12)
        nb.add(tab_chan, text=" 单会话通道 ")
        ttk.Label(tab_chan, text="选中会话 → 选择目标通道 → 应用（只改该会话，不影响全局）。改后重启生效。",
                  font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        self.list_chan = ttk.Treeview(tab_chan, height=10, show="tree", selectmode="extended")
        self.list_chan.pack(fill="both", expand=True, pady=(8, 8))
        row_c = ttk.Frame(tab_chan)
        row_c.pack(fill="x")
        ttk.Label(row_c, text="目标通道：").pack(side="left")
        self.cb_target = ttk.Combobox(row_c, width=26, state="readonly")
        self.cb_target.pack(side="left", padx=(6, 10))
        ttk.Button(row_c, text="应用", bootstyle=PRIMARY, width=10, command=self.set_chan).pack(side="left", padx=(0, 8))
        ttk.Button(row_c, text="刷新列表", bootstyle="outline-info", command=self.refresh_chan_list).pack(side="left")

        # ===== Tab3 会话归档 =====
        tab_arch = ttk.Frame(nb, padding=12)
        nb.add(tab_arch, text=" 会话归档 ")
        ttk.Label(tab_arch, text="勾选要归档的会话 → 点「归档选中」。操作前自动备份，可恢复。",
                  font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        self.listbox = ttk.Treeview(tab_arch, height=12, show="tree", selectmode="extended")
        self.listbox.pack(fill="both", expand=True, pady=(8, 8))
        row_a = ttk.Frame(tab_arch)
        row_a.pack(fill="x")
        ttk.Button(row_a, text="归档选中会话", bootstyle=DANGER, width=14, command=self.archive_selected).pack(side="left", padx=(0, 8))
        ttk.Button(row_a, text="刷新列表", bootstyle="outline-info", command=self.refresh_list).pack(side="left")
        self.var_arch_msg = ttk.StringVar(value="")
        ttk.Label(row_a, textvariable=self.var_arch_msg, bootstyle=SUCCESS).pack(side="left", padx=(12, 0))

        note = ("· 供应商配置存于 codex_tools\\providers.json，可自由添加/编辑/删除中转站。\n"
                "· 「本地翻译」供应商（如 DeepSeek 官方）：Codex 请求经本地代理 (127.0.0.1:18732) 自动做 Responses↔Chat 协议转换。\n"
                "· 单会话切换会同步数据库 + rollout 元数据；切换过通道的会话 UI 归档会失效（用本工具归档即可）。\n"
                "· 所有操作前自动备份到 codex_tools\\archive_backups\\。")
        ttk.Label(root, text=note, bootstyle="secondary", justify="left", font=("Microsoft YaHei UI", 9)).pack(fill="x", **pad)

    def refresh_all(self):
        self.refresh_status()
        self.refresh_provider_list()
        self.refresh_list()
        self.refresh_chan_list()

    def refresh_status(self):
        prov = read_provider()
        names = {p["id"]: p["name"] for p in self.providers}
        self.var_provider.set(names.get(prov, prov))
        self.var_model.set(read_model())
        running = codex_running()
        self.var_proc.set("运行中" if running else "未运行")
        if self.proxy_server:
            self.var_proxy_state.set(f"代理：运行中 (127.0.0.1:{PROXY_PORT})")
        else:
            self.var_proxy_state.set("代理：未启动")
        self.var_status_badge.set("● Codex 运行中" if running else "● 已就绪")

    def refresh_provider_list(self):
        self.tree_prov.delete(*self.tree_prov.get_children())
        cur_id = read_provider()
        for i, p in enumerate(self.providers):
            mark = "●" if p["id"] == cur_id else " "
            kind = "官方" if p["kind"] == "official" else "中转"
            self.tree_prov.insert("", "end", iid=str(i), text=f"{mark} [{kind}] {p['name']}  ({p['id']})")
        self.cb_target.configure(values=[f"{p['name']} ({p['id']})" for p in self.providers])
        if self.providers:
            self.cb_target.current(0)

    def switch_selected(self):
        sel = self.tree_prov.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个供应商。")
            return
        p = self.providers[int(sel[0])]
        proxy_url = None
        if p.get("proxy"):
            proxy_url = self.ensure_proxy(p)
        try:
            name = apply_provider(p["id"], proxy_url)
        except Exception as e:
            messagebox.showerror("错误", f"切换失败：\n{e}")
            return
        msg = f"已切换到【{name}】。\n\n请完全退出 Codex（含右下角托盘图标），再重新打开生效。"
        if p.get("proxy"):
            msg += f"\n本地翻译代理已启动 (127.0.0.1:{PROXY_PORT})，Codex 请求将转发到 {p.get('upstream_base', '')}"
        messagebox.showinfo("切换完成", msg)
        self.refresh_status()
        self.refresh_provider_list()

    def ensure_proxy(self, p):
        """启动/复用本地翻译代理；返回 config 应写入的本地 base_url"""
        upstream = p.get("upstream_base") or "https://api.deepseek.com/v1"
        key = p.get("api_key") or ""
        model = p.get("model") or "deepseek-chat"
        if self.proxy_server is not None:
            # 已启动：若配置变了则重启
            cfg = codex_proxy.ProxyConfig
            if cfg.upstream_base == upstream and cfg.api_key == key and cfg.model == model:
                return f"http://127.0.0.1:{PROXY_PORT}/v1"
            try:
                self.proxy_server.shutdown()
            except Exception:
                pass
        try:
            server, port = codex_proxy.start_proxy(upstream, key, model, PROXY_PORT)
            self.proxy_server = server
        except OSError as e:
            # 端口被占用：可能是上次的代理还在，尝试复用
            if "address already in use" in str(e).lower() or "WinError 10048" in str(e):
                self.proxy_server = object()
                return f"http://127.0.0.1:{PROXY_PORT}/v1"
            raise
        self.refresh_status()
        return f"http://127.0.0.1:{port}/v1"

    def edit_selected(self):
        sel = self.tree_prov.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个供应商。")
            return
        ProviderDialog(self.root, self, provider=self.providers[int(sel[0])])

    def delete_selected(self):
        sel = self.tree_prov.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个供应商。")
            return
        p = self.providers[int(sel[0])]
        if p["id"] == OFFICIAL_ID:
            messagebox.showwarning("提示", "官方供应商不能删除。")
            return
        if not messagebox.askyesno("确认删除", f"删除供应商「{p['name']}」？\n（config.toml 中对应配置段会保留，只是不再出现在列表）"):
            return
        self.providers.pop(int(sel[0]))
        save_providers(self.providers)
        self.refresh_provider_list()

    def refresh_list(self):
        self.listbox.delete(*self.listbox.get_children())
        rows, titles = list_unarchived()
        self.thread_rows = rows
        names = {p["id"]: p["name"] for p in self.providers}
        for i, r in enumerate(rows, 1):
            tid, title, prov = r
            t = (titles.get(tid) or title or "").replace("\n", " ")[:40]
            self.listbox.insert("", "end", iid=str(i - 1), text=f"{i}. [{names.get(prov, prov)}] {t}")
        if not rows:
            self.listbox.insert("", "end", iid="0", text="（没有未归档的会话）")

    def refresh_chan_list(self):
        self.list_chan.delete(*self.list_chan.get_children())
        rows, titles = list_unarchived()
        self.chan_rows = rows
        names = {p["id"]: p["name"] for p in self.providers}
        for i, r in enumerate(rows, 1):
            tid, title, prov = r
            t = (titles.get(tid) or title or "").replace("\n", " ")[:36]
            self.list_chan.insert("", "end", iid=str(i - 1), text=f"{i}. [{names.get(prov, prov)}] {t}")
        if not rows:
            self.list_chan.insert("", "end", iid="0", text="（没有未归档的会话）")

    def set_chan(self):
        sel = self.list_chan.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选择一个或多个会话。")
            return
        if codex_running():
            messagebox.showwarning("Codex 正在运行", "请先完全退出 Codex（含托盘），再执行切换。")
            return
        target = self.cb_target.get()
        if not target:
            messagebox.showwarning("提示", "请选择目标通道。")
            return
        pid = target.rsplit("(", 1)[-1].rstrip(")")
        names = {p["id"]: p["name"] for p in self.providers}
        target_name = names.get(pid, pid)
        targets = []
        for iid in sel:
            idx = int(iid)
            if idx < len(self.chan_rows):
                tid, title, prov = self.chan_rows[idx]
                if prov != pid:
                    targets.append((tid, title))
        if not targets:
            messagebox.showinfo("无需操作", "所选会话都已经是【%s】通道。" % target_name)
            return
        shown = "\n".join(f"· {(t[1] or '')[:26]}" for t in targets[:8])
        if len(targets) > 8:
            shown += f"\n  ... 共 {len(targets)} 个"
        if not messagebox.askyesno("确认切换", f"把以下 {len(targets)} 个会话切换为【{target_name}】？\n\n{shown}\n\n只影响这些会话，全局和其他会话不变。"):
            return
        try:
            last_stamp = None
            total_rl = 0
            for tid, title in targets:
                stamp, rl_count = set_thread_provider(tid, pid)
                last_stamp = stamp
                total_rl += rl_count
        except Exception as e:
            messagebox.showerror("错误", f"切换失败：\n{e}")
            return
        self.refresh_chan_list()
        messagebox.showinfo("完成",
                            f"已切换 {len(targets)} 个会话为【{target_name}】。\n\n重启 Codex 后打开生效。\n已同步数据库 + rollout 元数据（{total_rl} 个文件）。\n备份：archive_backups\\*{last_stamp}")

    def archive_selected(self):
        sel = self.listbox.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中勾选要归档的会话。")
            return
        if codex_running():
            messagebox.showwarning("Codex 正在运行", "请先完全退出 Codex（含托盘），再执行归档。")
            return
        targets = []
        for iid in sel:
            idx = int(iid)
            if idx < len(self.thread_rows):
                targets.append(self.thread_rows[idx])
        if not targets:
            return
        names = "\n".join(f"· {(r[1] or '')[:30]}" for r in targets)
        if not messagebox.askyesno("确认归档", f"即将归档以下会话（会话内容不删除，有备份可恢复）：\n\n{names}\n\n继续吗？"):
            return
        try:
            stamp = archive_threads([r[0] for r in targets])
        except Exception as e:
            messagebox.showerror("错误", f"归档失败：\n{e}")
            return
        self.var_arch_msg.set(f"已归档 {len(targets)} 个（备份 {stamp}）")
        self.refresh_list()
        self.refresh_chan_list()
        messagebox.showinfo("完成", f"归档成功！\n\n请重启 Codex 查看效果。\n备份：archive_backups\\*{stamp}")


def main():
    root = ttk.Window(themename="flatly", title="Codex 工具箱", size=(1040, 820), minsize=(960, 740))
    ToolboxApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
