# -*- coding: utf-8 -*-
"""Codex 工具箱公开测试版：多供应商管理 + 本地翻译代理 + 会话归档辅助。
供应商配置存于当前用户的 %LOCALAPPDATA%\\CodexToolbox，可增删改，一键切换。
支持"本地翻译"供应商（proxy=true）：Codex -> 本地代理 -> 上游 Chat Completions（如 DeepSeek 官方）。
运行：python codex_app.py（开发模式需安装 requirements.txt 中的依赖）。
"""
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


# 公开版界面已迁移至 PyWebView（codex_app.py）。下方旧版 Tk 类仅保留历史源码，
# 不能在导入核心逻辑时拉入 ttkbootstrap / tkinter，否则发布 EXE 会携带无用的 Tk 依赖。
class _LegacyTtkPlaceholder:
    class Toplevel:
        pass


ttk = _LegacyTtkPlaceholder()
PRIMARY = SUCCESS = INFO = WARNING = DANGER = OUTLINE = "legacy-unused"
messagebox = None


def _app_dir():
    """开发版和打包版都使用系统数据目录，避免把密钥写入源码仓库。"""
    return secret_store.app_data_dir()


CONFIG = os.path.expanduser(r"~\.codex\config.toml")
STATE_DB = os.path.expanduser(r"~\.codex\state_5.sqlite")
CATALOG_DB = os.path.expanduser(r"~\.codex\sqlite\codex-dev.db")
SESSION_INDEX_FILE = os.path.expanduser(r"~\.codex\session_index.jsonl")
BAK_DIR = os.path.join(_app_dir(), "archive_backups")
PROVIDERS_FILE = os.path.join(_app_dir(), "providers.json")
THREAD_ROUTES_FILE = os.path.join(_app_dir(), "thread_routes.json")
PROXY_PORT = 18732
AUTH_HELPER_NAME = "Codex工具箱认证助手.exe"

OFFICIAL_ID = "openai"
OFFICIAL_NAME = "OpenAI 登录（当前 Codex 账户）"
PROVIDER_NAMES = {}
# Codex app-server 在部分机器上会先返回请求成功，再异步落盘会话设置。
# 6 秒会造成“实际随后成功、工具箱却已报未确认”的误报；15 秒仍能避免无限等待。
THREAD_ROUTE_VERIFY_TIMEOUT = 15.0
THREAD_ROUTE_VERIFY_INTERVAL = 0.25


class SessionRouteUnsupported(RuntimeError):
    """当前 Codex 版本未确认保存已有会话的路由设置。"""


def _wait_for_thread_route(tid, provider_id, model, effort):
    """等待单个会话异步写入；保留单会话测试和旧调用的兼容入口。"""
    return _wait_for_thread_routes([tid], provider_id, model, effort)


def _wait_for_thread_routes(target_ids, provider_id, model, effort):
    """一次轮询全部会话，避免多个会话依次等待完整超时。"""
    target_ids = list(dict.fromkeys(target_ids))
    if not target_ids:
        return True
    deadline = time.monotonic() + THREAD_ROUTE_VERIFY_TIMEOUT
    placeholders = ",".join("?" for _ in target_ids)
    while True:
        verify = sqlite3.connect(STATE_DB, timeout=15)
        try:
            rows = verify.execute(
                f"SELECT id, model_provider, model, reasoning_effort FROM threads WHERE id IN ({placeholders})",
                target_ids,
            ).fetchall()
        finally:
            verify.close()

        row_map = {row[0]: row[1:] for row in rows}
        if all(
            tid in row_map
            and row_map[tid][0] == provider_id
            and row_map[tid][1] == model
            and (not effort or row_map[tid][2] == effort)
            for tid in target_ids
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(THREAD_ROUTE_VERIFY_INTERVAL)


MODEL_PRESETS = [
    "deepseek-chat", "deepseek-reasoner",
    "qwen-plus", "qwen-max", "qwen-turbo", "qwen-long",
    "glm-4-plus", "glm-4-air", "glm-4-flash",
    "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
    "abab6.5s-chat", "abab6.5g-chat",
    "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
    "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "grok-4", "grok-3",
    "mistral-large-latest", "codestral-latest",
    "llama-3.3-70b-versatile", "qwen/qwen3-32b",
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
    # 以下预设只提供公开兼容接口的起始配置。模型可用性、区域、额度和工具调用能力
    # 以各供应商账户实际返回的 /models 与其文档为准，保存前可在界面中测试连接。
    {"label": "百度智能云千帆", "name": "千帆", "kind": "direct", "base_url": "https://qianfan.baidubce.com/v2",
     "model": "deepseek-v3.2", "models": ["deepseek-v3.2", "deepseek-r1", "ernie-4.5-turbo-vl"],
     "wire_api": "responses", "proxy": True},
    {"label": "腾讯混元", "name": "腾讯混元", "kind": "direct", "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
     "model": "hunyuan-turbos-latest", "models": ["hunyuan-turbos-latest", "hunyuan-a13b", "hunyuan-vision"],
     "wire_api": "responses", "proxy": True},
    {"label": "阶跃星辰 StepFun", "name": "阶跃星辰", "kind": "direct", "base_url": "https://api.stepfun.com/v1",
     "model": "step-3.5-flash", "models": ["step-3.5-flash", "step-2-16k", "step-1-8k"],
     "wire_api": "responses", "proxy": True},
    {"label": "零一万物 Yi", "name": "零一万物", "kind": "direct", "base_url": "https://api.lingyiwanwu.com/v1",
     "model": "yi-lightning", "models": ["yi-lightning", "yi-large", "yi-medium"],
     "wire_api": "responses", "proxy": True},
    {"label": "百川智能", "name": "百川智能", "kind": "direct", "base_url": "https://api.baichuan-ai.com/v1",
     "model": "Baichuan4", "models": ["Baichuan4", "Baichuan3-Turbo", "Baichuan2-Turbo"],
     "wire_api": "responses", "proxy": True},
    {"label": "小米 MiMo", "name": "小米 MiMo", "kind": "direct", "base_url": "https://api.xiaomimimo.com/v1",
     "model": "mimo-v2-flash", "models": ["mimo-v2-flash", "mimo-v2-pro", "mimo-v1"],
     "wire_api": "responses", "proxy": True},
    {"label": "魔搭 ModelScope", "name": "魔搭 ModelScope", "kind": "relay", "base_url": "https://api-inference.modelscope.cn/v1",
     "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct", "models": ["Qwen/Qwen3-Coder-480B-A35B-Instruct", "deepseek-ai/DeepSeek-V3.2", "ZhipuAI/GLM-4.7"],
     "wire_api": "responses", "proxy": True},
    {"label": "Google Gemini（OpenAI 兼容）", "name": "Google Gemini", "kind": "direct", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-2.5-flash", "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
     "wire_api": "responses", "proxy": True},
    {"label": "xAI Grok", "name": "xAI Grok", "kind": "direct", "base_url": "https://api.x.ai/v1",
     "model": "grok-4", "models": ["grok-4", "grok-3", "grok-3-mini"],
     "wire_api": "responses", "proxy": True},
    {"label": "Mistral AI", "name": "Mistral AI", "kind": "direct", "base_url": "https://api.mistral.ai/v1",
     "model": "mistral-large-latest", "models": ["mistral-large-latest", "codestral-latest", "ministral-8b-latest"],
     "wire_api": "responses", "proxy": True},
    {"label": "Groq", "name": "Groq", "kind": "direct", "base_url": "https://api.groq.com/openai/v1",
     "model": "llama-3.3-70b-versatile", "models": ["llama-3.3-70b-versatile", "qwen/qwen3-32b", "openai/gpt-oss-120b"],
     "wire_api": "responses", "proxy": True},
    {"label": "Together AI", "name": "Together AI", "kind": "relay", "base_url": "https://api.together.xyz/v1",
     "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
     "wire_api": "responses", "proxy": True},
    {"label": "Fireworks AI", "name": "Fireworks AI", "kind": "relay", "base_url": "https://api.fireworks.ai/inference/v1",
     "model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "models": ["accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/deepseek-r1", "accounts/fireworks/models/qwen3-235b-a22b"],
     "wire_api": "responses", "proxy": True},
    {"label": "NVIDIA NIM", "name": "NVIDIA NIM", "kind": "relay", "base_url": "https://integrate.api.nvidia.com/v1",
     "model": "meta/llama-3.3-70b-instruct", "models": ["meta/llama-3.3-70b-instruct", "deepseek-ai/deepseek-r1", "qwen/qwen3-235b-a22b"],
     "wire_api": "responses", "proxy": True},
    {"label": "本地 Ollama（OpenAI 兼容）", "name": "本地 Ollama", "kind": "local", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen3:latest", "models": ["qwen3:latest", "deepseek-r1:latest", "llama3.3:latest"],
     "wire_api": "responses", "proxy": True},
    {"label": "本地 LM Studio（OpenAI 兼容）", "name": "本地 LM Studio", "kind": "local", "base_url": "http://127.0.0.1:1234/v1",
     "model": "local-model", "models": ["local-model"],
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


def read_reasoning_effort():
    value = read_config_field("model_reasoning_effort")
    return "" if value == "?" else value


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
    p.setdefault("reasoning_effort", "")
    if p.get("id") == OFFICIAL_ID:
        # 登录状态由用户当前 Codex 账户决定，不能假定具体套餐或组织类型。
        p["name"] = OFFICIAL_NAME
        p["kind"] = "official"
        p["base_url"] = ""
        p["api_key"] = ""
        p.pop("proxy", None)
        p.pop("upstream_base", None)
    elif p.get("kind") == "official":
        # 只有内置 openai 条目能使用 Codex 的现有登录；其他条目必须显式配置 API。
        p["kind"] = "relay"
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
    official = {"id": OFFICIAL_ID, "name": OFFICIAL_NAME, "kind": "official", "model": read_model()}
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
    """记录工具箱管理语义；follow 表示曾手动同步到当时的全局默认。"""
    if mode not in ("follow", "fixed"):
        raise ValueError("会话模式必须是 follow 或 fixed")
    routes = load_thread_routes()
    for tid in tids:
        routes[tid] = {"mode": mode, "provider": provider or read_provider()}
    save_thread_routes(routes)


def following_thread_ids(active_only=False):
    """返回历史上标记为 follow 的会话，供兼容旧配置和诊断使用。

    自 0.1.0-beta 起，全局切换不会据此修改已有会话。
    """
    following = [tid for tid, item in load_thread_routes().items()
                 if isinstance(item, dict) and item.get("mode") == "follow"]
    if not active_only or not following:
        return following

    placeholders = ",".join("?" for _ in following)
    try:
        conn = sqlite3.connect(STATE_DB, timeout=15)
        try:
            rows = conn.execute(
                f"SELECT id FROM threads WHERE archived = 0 AND id IN ({placeholders})",
                following,
            ).fetchall()
        finally:
            conn.close()
        active_ids = {row[0] for row in rows}
        return [tid for tid in following if tid in active_ids]
    except sqlite3.Error:
        return following


def migrate_active_dashscope():
    """兼容旧调用的空操作。

    公开版不再因检测到历史千问配置而自动修改用户的 Codex 配置；用户需要
    在界面中核对供应商后，明确点击切换才会写入配置。
    """
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
    # 留空表示保持用户当前的 Codex 思考强度；设置后作为该通道的默认值。
    if p.get("reasoning_effort"):
        effort_line = f'model_reasoning_effort = {_toml_string(p["reasoning_effort"])}'
        if re.search(r'^model_reasoning_effort\s*=', content, re.M):
            content = re.sub(
                r'^model_reasoning_effort\s*=.*$', effort_line, content,
                count=1, flags=re.M,
            )
        else:
            content = effort_line + "\n" + content
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


def ensure_provider_config(pid, proxy_url=None):
    """确保指定供应商已写入 Codex 配置，但不改变全局默认通道。"""
    if pid == OFFICIAL_ID:
        return
    provider = next((item for item in load_providers() if item.get("id") == pid), None)
    if not provider:
        raise ValueError(f"供应商 {pid} 不存在")
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            content = handle.read()
    except FileNotFoundError:
        raise RuntimeError("未找到 Codex 配置。请先安装、登录并至少启动一次 Codex。")
    provider_copy = dict(provider)
    if provider.get("proxy"):
        if not proxy_url:
            raise RuntimeError("该供应商需要本地翻译代理，但代理尚未启动。")
        provider_copy["base_url"] = proxy_url
        provider_copy.pop("upstream_base", None)
    updated = upsert_section(content, pid, provider_copy)
    if updated == content:
        return
    os.makedirs(BAK_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(CONFIG, os.path.join(BAK_DIR, f"config.toml.bak_{stamp}"))
    tmp = CONFIG + ".toolbox.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    os.replace(tmp, CONFIG)


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
    cur.execute(
        "SELECT id, title, model_provider, model, reasoning_effort "
        "FROM threads WHERE archived = 0 ORDER BY created_at"
    )
    rows = cur.fetchall()
    conn.close()
    return rows, _friendly_titles()


def list_archived():
    """列出已归档会话。标题优先使用 Codex 状态库，不依赖已被官方移除的 catalog 记录。"""
    conn = sqlite3.connect(STATE_DB)
    try:
        rows = conn.execute(
            "SELECT id, title, model_provider, model, reasoning_effort FROM threads "
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


def _backup_catalog_if_present(destination):
    """catalog 是侧栏缓存，缺失时不阻断会话路由、归档或恢复。"""
    if os.path.isfile(CATALOG_DB):
        _backup_sqlite(CATALOG_DB, destination)


def _archived_state_map(thread_ids):
    """读取指定会话是否已归档，用于失败回滚时只恢复本次可能影响的会话。"""
    ids = list(dict.fromkeys(thread_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        return {
            row[0]: bool(row[1])
            for row in conn.execute(
                f"SELECT id, archived FROM threads WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
    finally:
        conn.close()


def _find_codex_executable():
    """定位可运行 Codex app-server 的入口。

    Microsoft Store 包内的 ``resources/codex.exe`` 在部分 Windows 版本中
    不允许被外部程序直接执行。若检测到用户安装的官方 ``@openai/codex``
    CLI，优先使用其 ``codex.cmd``，它和桌面端使用同一 ``~/.codex`` 数据目录。
    """
    app_data = os.environ.get("APPDATA", "")
    cli_candidates = []
    if app_data:
        cli_candidates.append(os.path.join(app_data, "npm", "codex.cmd"))
    cli_from_path = shutil.which("codex.cmd")
    if cli_from_path:
        cli_candidates.append(cli_from_path)
    for cli_path in cli_candidates:
        cli_path = os.path.abspath(os.path.expandvars(cli_path))
        cli_package = os.path.join(
            os.path.dirname(cli_path), "node_modules", "@openai", "codex", "bin", "codex.js"
        )
        if os.path.isfile(cli_path) and os.path.isfile(cli_package):
            return cli_path

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

    # 部分 PyInstaller 打包环境下，WindowsApps 的通配枚举会异常。
    # Store 的已注册包位置是稳定的系统信息，用它补充候选路径，避免写死用户目录。
    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-AppxPackage -Name OpenAI.Codex | "
                    "Sort-Object Version -Descending | Select-Object -ExpandProperty InstallLocation",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                for install_location in result.stdout.splitlines():
                    install_location = install_location.strip()
                    if install_location:
                        candidates.append(os.path.join(
                            install_location, "app", "resources", "codex.exe"
                        ))
        except (OSError, subprocess.SubprocessError):
            pass

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
    """最小 JSON-RPC 客户端：只通过 Codex app-server 执行会话归档/恢复。"""

    def __init__(self, executable, timeout=20):
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.timeout = timeout
        self.next_id = 1
        self.lines = queue.Queue()
        self.stderr_lines = []
        if executable.lower().endswith((".cmd", ".bat")):
            command = ["cmd.exe", "/d", "/s", "/c", executable, "app-server", "--stdio"]
        else:
            command = [executable, "app-server", "--stdio"]
        self.process = subprocess.Popen(
            command,
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
            # thread/resume.excludeTurns 是 app-server 标注的实验字段，
            # 必须在初始化时显式协商，否则当前 app-server 会拒绝该请求。
            "capabilities": {
                "experimentalApi": True,
                "optOutNotificationMethods": ["thread/started"],
            },
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
                raise RuntimeError(f"Codex app-server 请求失败：{detail}")
            return message.get("result")
        detail = "\n".join(self.stderr_lines[-5:])
        raise RuntimeError(f"等待 Codex app-server 响应超时。{detail}")

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
    """Codex 归档通常会连同子会话一起处理；预检和备份也覆盖同一范围。"""
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


def _sync_session_index(removed_ids=(), restored_rows=()):
    """同步 Codex 启动时读取的最近会话索引，避免归档会话在重启后短暂出现。"""
    removed_ids = set(removed_ids)
    restored = {row[0]: row for row in restored_rows}
    if not removed_ids and not restored:
        return

    lines = []
    if os.path.isfile(SESSION_INDEX_FILE):
        with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    # 不属于会话索引格式的历史行原样保留。
                    lines.append(line.rstrip("\r\n"))
                    continue
                if not isinstance(item, dict) or item.get("id") not in (removed_ids | set(restored)):
                    lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for tid, row in restored.items():
        title = row[1] or "未命名会话"
        lines.append(json.dumps({"id": tid, "thread_name": title, "updated_at": now},
                                ensure_ascii=False, separators=(",", ":")))

    directory = os.path.dirname(SESSION_INDEX_FILE)
    os.makedirs(directory, exist_ok=True)
    temporary = f"{SESSION_INDEX_FILE}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as f:
            if lines:
                f.write("\n".join(lines) + "\n")
        os.replace(temporary, SESSION_INDEX_FILE)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _remove_archived_catalog_rows(thread_ids):
    """移除已归档会话残留的侧栏 catalog 记录。

    当前 Windows 版 Codex 的归档有时不会删除这些记录，重启后会使
    已归档任务短暂回到“最近”。只清理 state 数据库已确认 archived=1 的 ID。
    """
    thread_ids = list(dict.fromkeys(thread_ids))
    if not thread_ids:
        return 0
    if not os.path.isfile(CATALOG_DB):
        return 0
    placeholders = ",".join("?" for _ in thread_ids)
    state = sqlite3.connect(STATE_DB, timeout=15)
    try:
        archived_ids = [row[0] for row in state.execute(
            f"SELECT id FROM threads WHERE archived = 1 AND id IN ({placeholders})",
            thread_ids,
        ).fetchall()]
    finally:
        state.close()
    if not archived_ids:
        return 0

    placeholders = ",".join("?" for _ in archived_ids)
    catalog = sqlite3.connect(CATALOG_DB, timeout=15)
    try:
        with catalog:
            cursor = catalog.execute(
                f"DELETE FROM local_thread_catalog WHERE thread_id IN ({placeholders})",
                archived_ids,
            )
        return cursor.rowcount
    finally:
        catalog.close()


def _archived_ids_from_state(candidate_ids=None):
    """只从 state 中确认 archived=1 的会话 ID，绝不猜测其他记录。"""
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        if candidate_ids:
            candidate_ids = list(dict.fromkeys(candidate_ids))
            placeholders = ",".join("?" for _ in candidate_ids)
            rows = conn.execute(
                f"SELECT id FROM threads WHERE archived = 1 AND id IN ({placeholders})",
                candidate_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM threads WHERE archived = 1").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def _archived_residue(archived_ids):
    """读取修复后的残留；只用于验证，不对未知数据做任何处理。"""
    ids = set(archived_ids)
    catalog_ids = set()
    index_ids = set()
    if ids and os.path.isfile(CATALOG_DB):
        catalog = sqlite3.connect(CATALOG_DB, timeout=15)
        try:
            placeholders = ",".join("?" for _ in ids)
            catalog_ids = {row[0] for row in catalog.execute(
                f"SELECT thread_id FROM local_thread_catalog WHERE thread_id IN ({placeholders})",
                list(ids),
            ).fetchall()}
        finally:
            catalog.close()
    if ids and os.path.isfile(SESSION_INDEX_FILE):
        with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("id") in ids:
                    index_ids.add(item["id"])
    return catalog_ids, index_ids


def _backup_sidebar_repair_inputs(destination):
    """备份兼容修复会读取/写入的三份本地数据。"""
    os.makedirs(destination, exist_ok=False)
    _backup_sqlite(STATE_DB, os.path.join(destination, "state_5.sqlite"))
    if os.path.isfile(CATALOG_DB):
        _backup_sqlite(CATALOG_DB, os.path.join(destination, "codex-dev.db"))
    if os.path.isfile(SESSION_INDEX_FILE):
        shutil.copy2(SESSION_INDEX_FILE, os.path.join(destination, "session_index.jsonl"))


def repair_archived_session_index(candidate_ids=None, backup_dir=None):
    """实验兼容修复：只移除已归档会话的侧栏残留，并完成验证。

    该逻辑用于部分 Codex 版本已归档但 ``local_thread_catalog`` / ``session_index``
    仍残留的情况。它不会删除未归档会话，也不会批量猜测未知记录。
    """
    archived_ids = _archived_ids_from_state(candidate_ids)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    repair_backup = backup_dir or os.path.join(BAK_DIR, f"archived_sidebar_repair_{stamp}")
    if backup_dir is None:
        _backup_sidebar_repair_inputs(repair_backup)
    try:
        _sync_session_index(removed_ids=archived_ids)
        removed_catalog_rows = _remove_archived_catalog_rows(archived_ids)
        catalog_left, index_left = _archived_residue(archived_ids)
        if catalog_left or index_left:
            details = []
            if catalog_left:
                details.append(f"catalog 仍残留 {len(catalog_left)} 条")
            if index_left:
                details.append(f"session_index 仍残留 {len(index_left)} 条")
            raise RuntimeError(
                "修复后验证失败，已停止；未处理未知数据。"
                f"备份位置：{repair_backup}；" + "，".join(details)
            )
    except Exception:
        # 归档后自动修复失败时，只还原本函数碰过的侧栏文件；
        # 归档状态本身随后仍由 archive_threads 的 app-server 回滚处理。
        if backup_dir is not None:
            catalog_backup = os.path.join(repair_backup, "codex-dev.db")
            index_backup = os.path.join(repair_backup, "session_index.jsonl")
            try:
                if os.path.isfile(catalog_backup):
                    shutil.copy2(catalog_backup, CATALOG_DB)
                if os.path.isfile(index_backup):
                    shutil.copy2(index_backup, SESSION_INDEX_FILE)
                elif os.path.isfile(SESSION_INDEX_FILE):
                    os.remove(SESSION_INDEX_FILE)
            except OSError:
                # 原始错误会携带备份位置，避免二次异常掩盖它。
                pass
        raise
    return stamp, len(archived_ids), removed_catalog_rows, repair_backup


def archive_threads(target_ids):
    """按当前 Codex 行为归档：预检/备份后调用 app-server 的 thread/archive。"""
    target_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
    if not target_ids:
        raise ValueError("没有可归档的会话。")

    executable = _find_codex_executable()
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        affected_ids = _thread_ids_with_descendants(conn, target_ids)
        placeholders = ",".join("?" for _ in affected_ids)
        rows = conn.execute(
            f"SELECT id, rollout_path, archived, title FROM threads WHERE id IN ({placeholders})",
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
    _backup_catalog_if_present(os.path.join(operation_backup, "codex-dev.db"))
    if os.path.isfile(SESSION_INDEX_FILE):
        shutil.copy2(SESSION_INDEX_FILE, os.path.join(operation_backup, "session_index.jsonl"))
    for source in files_to_backup:
        shutil.copy2(source, os.path.join(rollout_backup, os.path.basename(source)))

    # 当前 Codex Windows 版对部分 \\?\ 扩展路径归档会误报文件不存在；
    # 仅规范化路径表示，真正的移动、状态更新和 catalog 维护仍交给 Codex app-server。
    # 仅对 ``\\?\`` 扩展路径做临时兼容处理。普通路径无需写 SQLite；
    # app-server 若未把临时路径替换为归档后的真实路径，后续校验会失败并走回滚。
    normalized_for_archive = {
        tid: _normal_windows_path(original_path)
        for tid, original_path in originals.items()
        if _normal_windows_path(original_path) != original_path
    }
    if normalized_for_archive:
        conn = sqlite3.connect(STATE_DB, timeout=15)
        try:
            with conn:
                for tid, normal_path in normalized_for_archive.items():
                    conn.execute("UPDATE threads SET rollout_path=? WHERE id=?", (normal_path, tid))
        finally:
            conn.close()

    client = None
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

        verify = sqlite3.connect(STATE_DB)
        try:
            # 子会话由 app-server 负责级联处理；必须验证整个预检范围，不能只验证父会话。
            for tid in originals:
                row = verify.execute(
                    "SELECT archived, rollout_path FROM threads WHERE id=?", (tid,)
                ).fetchone()
                if not row or not row[0] or not os.path.isfile(_normal_windows_path(row[1])):
                    raise RuntimeError(f"Codex app-server 返回后校验失败：{tid}")
                if tid in normalized_for_archive and row[1] == normalized_for_archive[tid]:
                    raise RuntimeError(f"Codex app-server 未更新归档后的会话路径：{tid}")
        finally:
            verify.close()
        # 已归档后的侧栏残留只按 state 已确认 archived=1 的 ID 做受限修复。
        # 本次归档的完整备份已在 operation_backup 中，失败会走 app-server 回滚流程。
        repair_archived_session_index(affected_ids, backup_dir=operation_backup)
    except Exception as operation_error:
        rollback_errors = []
        if client:
            # app-server 可能已级联归档子会话；按真实 state 回滚全部受影响的活跃会话。
            for tid, archived in reversed(list(_archived_state_map(originals).items())):
                if not archived:
                    continue
                try:
                    client.request("thread/unarchive", {"threadId": tid})
                except Exception as rollback_error:
                    rollback_errors.append(f"{tid}: {rollback_error}")
        # 只恢复本函数为扩展路径做的临时表示；仍处于归档状态的会话不能伪造原路径。
        current_states = _archived_state_map(normalized_for_archive)
        restore_ids = [tid for tid, archived in current_states.items() if not archived]
        if restore_ids:
            restore = sqlite3.connect(STATE_DB, timeout=15)
            try:
                with restore:
                    for tid in restore_ids:
                        restore.execute(
                            "UPDATE threads SET rollout_path=? WHERE id=?",
                            (originals[tid], tid),
                        )
            finally:
                restore.close()
        if rollback_errors:
            raise RuntimeError(
                f"{operation_error}\n部分归档回滚未确认：{'；'.join(rollback_errors)}\n"
                f"请保留并检查备份：{operation_backup}"
            ) from operation_error
        raise
    finally:
        if client:
            client.close()

    return stamp


def unarchive_threads(target_ids):
    """通过 Codex app-server 恢复已归档会话，操作前保留一致性备份。"""
    target_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
    if not target_ids:
        raise ValueError("没有可恢复的会话。")

    executable = _find_codex_executable()
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        affected_ids = _thread_ids_with_descendants(conn, target_ids)
        placeholders = ",".join("?" for _ in affected_ids)
        rows = conn.execute(
            f"SELECT id, rollout_path, archived, title FROM threads WHERE id IN ({placeholders})",
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
        _, path, archived, _ = row_map[tid]
        if not archived:
            continue
        normal_path = _normal_windows_path(path)
        if not normal_path or not os.path.isfile(normal_path):
            raise RuntimeError(f"已归档会话文件不存在，已取消恢复：{tid}")
        originals[tid] = True
        files_to_backup.append(normal_path)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    operation_backup = os.path.join(BAK_DIR, f"unarchive_{stamp}")
    rollout_backup = os.path.join(operation_backup, "rollouts")
    os.makedirs(rollout_backup, exist_ok=False)
    _backup_sqlite(STATE_DB, os.path.join(operation_backup, "state_5.sqlite"))
    _backup_catalog_if_present(os.path.join(operation_backup, "codex-dev.db"))
    if os.path.isfile(SESSION_INDEX_FILE):
        shutil.copy2(SESSION_INDEX_FILE, os.path.join(operation_backup, "session_index.jsonl"))
    for source in files_to_backup:
        shutil.copy2(source, os.path.join(rollout_backup, os.path.basename(source)))

    client = None
    try:
        client = _CodexAppServer(executable)
        for tid in target_ids:
            if not row_map[tid][2]:
                continue
            client.request("thread/unarchive", {"threadId": tid})

        verify = sqlite3.connect(STATE_DB)
        try:
            for tid in originals:
                row = verify.execute(
                    "SELECT archived, rollout_path FROM threads WHERE id=?", (tid,)
                ).fetchone()
                if not row or row[0] or not os.path.isfile(_normal_windows_path(row[1])):
                    raise RuntimeError(f"Codex app-server 返回后恢复校验失败：{tid}")
        finally:
            verify.close()
        restored_rows = [(tid, row_map[tid][3]) for tid in originals if not _archived_state_map([tid]).get(tid)]
        _sync_session_index(restored_rows=restored_rows)
    except Exception as operation_error:
        rollback_errors = []
        if client:
            # app-server 可能已级联恢复子会话；按当前 state 回滚全部本次涉及的已恢复会话。
            for tid, archived in reversed(list(_archived_state_map(originals).items())):
                if archived:
                    continue
                try:
                    client.request("thread/archive", {"threadId": tid})
                except Exception as rollback_error:
                    rollback_errors.append(f"{tid}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"{operation_error}\n部分恢复回滚未确认：{'；'.join(rollback_errors)}\n"
                f"请保留并检查备份：{operation_backup}"
            ) from operation_error
        raise
    finally:
        if client:
            client.close()
    return stamp


def resolve_thread_route_settings(provider, model_override=None, effort_override=None):
    """合并供应商默认值与仅用于本次会话路由的覆盖值。"""
    model = (model_override or "").strip() if isinstance(model_override, str) else ""
    effort = (effort_override or "").strip().lower() if isinstance(effort_override, str) else ""
    model = model or (provider.get("model") or "").strip()
    effort = effort or (provider.get("reasoning_effort") or "").strip().lower()
    if not model:
        raise ValueError(f"供应商 {provider.get('name', provider.get('id', ''))} 未设置模型。")
    allowed_efforts = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
    if effort and effort not in allowed_efforts:
        raise ValueError("思考强度仅支持 minimal、low、medium、high、xhigh、max 或 ultra。")
    return model, effort


def set_threads_provider_official(target_ids, provider_id, model_override=None, effort_override=None):
    """
    通过 Codex app-server 的 thread/resume 为已有会话尝试设置通道和模型。

    本函数只读取 SQLite 用于预检和结果校验，不直接写入 SQLite/JSONL。
    """
    target_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
    if not target_ids:
        raise ValueError("没有选中会话。")
    provider = next((item for item in load_providers() if item.get("id") == provider_id), None)
    if not provider:
        raise ValueError(f"供应商 {provider_id} 不存在")
    model, effort = resolve_thread_route_settings(provider, model_override, effort_override)

    placeholders = ",".join("?" for _ in target_ids)
    conn = sqlite3.connect(STATE_DB, timeout=15)
    try:
        rows = conn.execute(
            f"SELECT id, rollout_path, archived, model_provider, model, reasoning_effort "
            f"FROM threads WHERE id IN ({placeholders})",
            target_ids,
        ).fetchall()
    finally:
        conn.close()
    row_map = {row[0]: row for row in rows}
    missing = [tid for tid in target_ids if tid not in row_map]
    if missing:
        raise RuntimeError(f"数据库中找不到会话：{', '.join(missing)}")

    rollout_files = []
    originals = {}
    for tid in target_ids:
        (_, rollout_path, archived, original_provider, original_model,
         original_effort) = row_map[tid]
        if archived:
            raise RuntimeError(f"会话已归档，请先恢复：{tid}")
        normal_path = _normal_windows_path(rollout_path)
        if not normal_path or not os.path.isfile(normal_path):
            raise RuntimeError(f"会话文件不存在，已取消切换：{tid}")
        rollout_files.append(normal_path)
        originals[tid] = (original_provider, original_model, original_effort)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    operation_backup = os.path.join(BAK_DIR, f"thread_route_{stamp}")
    rollout_backup = os.path.join(operation_backup, "rollouts")
    os.makedirs(rollout_backup, exist_ok=False)
    _backup_sqlite(STATE_DB, os.path.join(operation_backup, "state_5.sqlite"))
    _backup_catalog_if_present(os.path.join(operation_backup, "codex-dev.db"))
    for source in rollout_files:
        shutil.copy2(source, os.path.join(rollout_backup, os.path.basename(source)))

    executable = _find_codex_executable()
    client = None
    switched = []
    try:
        client = _CodexAppServer(executable)
        for tid in target_ids:
            result = client.request("thread/resume", {
                "threadId": tid,
                "modelProvider": provider_id,
                "model": model,
                "excludeTurns": True,
            }) or {}
            # resume 成功后已经可能写入会话设置。即使随后的结果校验或思考强度设置失败，
            # 也必须把该会话纳入回滚范围。
            switched.append(tid)
            if isinstance(result, dict):
                returned_provider = result.get("modelProvider")
                returned_model = result.get("model")
                if returned_provider and returned_provider != provider_id:
                    raise RuntimeError(f"Codex app-server 返回的通道不一致：{tid}")
                if returned_model and returned_model != model:
                    raise RuntimeError(f"Codex app-server 返回的模型不一致：{tid}")
            if effort:
                client.request("thread/settings/update", {
                    "threadId": tid,
                    "model": model,
                    "effort": effort,
                })
        if not _wait_for_thread_routes(target_ids, provider_id, model, effort):
            raise SessionRouteUnsupported(
                f"等待 Codex 保存已有会话的通道设置超过 {int(THREAD_ROUTE_VERIFY_TIMEOUT)} 秒，未确认成功。"
                "本次没有标记为已切换；全局通道和会话内容未受影响。"
            )
    except Exception as operation_error:
        rollback_errors = []
        if client:
            for tid in reversed(switched):
                original_provider, original_model, original_effort = originals[tid]
                try:
                    client.request("thread/resume", {
                        "threadId": tid,
                        "modelProvider": original_provider,
                        "model": original_model,
                        "excludeTurns": True,
                    })
                    client.request("thread/settings/update", {
                        "threadId": tid,
                        "model": original_model,
                        "effort": original_effort,
                    })
                except Exception as rollback_error:
                    rollback_errors.append(f"{tid}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"{operation_error}\n部分会话路由回滚未确认：{'；'.join(rollback_errors)}\n"
                f"请保留并检查备份：{operation_backup}"
            ) from operation_error
        raise
    finally:
        if client:
            client.close()
    return stamp


def set_thread_provider(tid, provider, model_override=None, effort_override=None):
    """向后兼容的单会话入口，内部使用 Codex app-server。"""
    return set_threads_provider_official([tid], provider, model_override, effort_override)


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

        note = ("· 供应商配置存于 codex_tools\\providers.json，可自由添加/编辑/删除 API 接入配置。\n"
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
    raise SystemExit("旧版 Tk 界面已停止维护，请运行 codex_app.py。")


if __name__ == "__main__":
    main()
