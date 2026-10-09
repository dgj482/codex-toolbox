# -*- coding: utf-8 -*-
"""Codex 表层翻译：翻译后端与临时 CDP 页面桥。

只临时替换当前 Codex 页面内存中的可见文字，不修改官方设置、安装文件或会话数据。
API 密钥始终留在本进程内，不会注入页面。
"""
from __future__ import annotations

import json
import http.client
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from typing import Callable


_PROTECTED = re.compile(
    r"```[\s\S]*?```|`[^`\r\n]+`|https?://\S+|"
    r"(?:[A-Za-z]:\\[^\r\n\t<>|\"]+)|"
    r"(?:/(?:[\w.@+-]+/)+[\w.@+-]+)|"
    r"@[\w.-]+|\b(?i:(?:gpt-\d[\w.-]*|o[134](?:-[\w.-]+)?|qwen[\w.-]*|claude[\w.-]*|gemini[\w.-]*)(?:\s+(?:sol|astra|luna|terra))?)\b|"
    r"\b(?:Codex|ChatGPT|OpenAI|Git|PowerShell|PyCharm|API|URL|MCP|(?i:models?))\b",
    re.MULTILINE,
)


def select_codex_page_target(payload) -> dict | None:
    """从 CDP 目标对象或目标列表中选出 Codex 主页面。"""
    candidates = payload if isinstance(payload, list) else [payload]
    valid = [item for item in candidates if isinstance(item, dict) and item.get("webSocketDebuggerUrl")]
    exact = next(
        (item for item in valid if item.get("type") == "page" and item.get("url") == "app://-/index.html"),
        None,
    )
    if exact:
        return exact
    return next(
        (
            item for item in valid
            if item.get("type") == "page"
            and str(item.get("url") or "").startswith("app://")
            and "avatar-overlay" not in str(item.get("url") or "")
        ),
        None,
    )


def protect_technical_text(text: str) -> tuple[str, list[str]]:
    """把代码、URL、路径和提及替换为稳定占位符，避免翻译器改写。"""
    protected: list[str] = []

    def replace(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"[[CTB_KEEP_{len(protected) - 1}]]"

    return _PROTECTED.sub(replace, text), protected


def restore_technical_text(text: str, protected: list[str]) -> str:
    for index, value in enumerate(protected):
        text = text.replace(f"[[CTB_KEEP_{index}]]", value)
    return text


def _extract_output(payload) -> str:
    # 少数 OpenAI 兼容中转会把模型输出数组直接作为 HTTP JSON 根节点返回。
    # 这不是标准 Responses/Chat Completions 结构，但内容本身仍可安全解析。
    if isinstance(payload, list):
        if all(isinstance(item, str) for item in payload):
            return json.dumps(payload, ensure_ascii=False)
        for item in payload:
            if isinstance(item, dict):
                try:
                    return _extract_output(item)
                except RuntimeError:
                    continue
        raise RuntimeError("翻译服务返回了列表，但里面没有可识别的文本")
    if not isinstance(payload, dict):
        raise RuntimeError("翻译服务返回的数据不是对象或文本列表")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {})
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces = [block.get("text") for block in content
                      if isinstance(block, dict) and isinstance(block.get("text"), str)]
            if pieces:
                return "".join(pieces)
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    pieces = []
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            continue
        for block in item.get("content", []):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                pieces.append(block["text"])
    if pieces:
        return "".join(pieces)
    raise RuntimeError("翻译服务没有返回可识别的文本")


def _parse_translation_array(content: str, expected: int) -> list[str]:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    if fenced:
        content = fenced.group(1).strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", content)
        if not match:
            raise RuntimeError("翻译服务返回的格式不是 JSON 数组")
        value = json.loads(match.group(0))
    if not isinstance(value, list) or len(value) != expected or not all(isinstance(x, str) for x in value):
        raise RuntimeError("翻译服务返回的条目数量与请求不一致")
    return value


class OpenAICompatibleTranslator:
    CACHE_VERSION = 2
    CACHEABLE_SCOPES = {"interface"}
    QWEN_MT_REQUESTS_PER_MINUTE = 55
    QWEN_MT_PACK_ITEMS = 8
    QWEN_MT_PACK_CHARACTERS = 800
    REQUEST_DEADLINE_SECONDS = 45.0
    ATTEMPT_TIMEOUT_SECONDS = 20.0

    def __init__(self, provider: dict, model_override: str = "", persistent_cache_path: str = ""):
        selected_base = provider.get("upstream_base") if provider.get("proxy") else provider.get("base_url")
        self.base_url = str(selected_base or provider.get("base_url") or provider.get("upstream_base") or "").rstrip("/")
        self.api_key = str(provider.get("api_key") or "")
        self.model = model_override.strip() or str(provider.get("model") or "").strip()
        self.wire_api = str(provider.get("wire_api") or "responses")
        hostname = (urllib.parse.urlparse(self.base_url).hostname or "").lower()
        self.translation_wire_api = "chat_completions" if hostname.endswith("dashscope.aliyuncs.com") else self.wire_api
        self.is_qwen_mt = self.model.lower().startswith("qwen-mt-")
        self._request_slots = threading.BoundedSemaphore(4)
        self._rate_lock = threading.Lock()
        self._rate_events: deque[float] = deque()
        self.persistent_cache_path = os.path.abspath(persistent_cache_path) if persistent_cache_path else ""
        self._cache: dict[tuple[str, str, str], str] = {}
        self._cache_lock = threading.Lock()
        if not self.base_url or not self.model:
            raise ValueError("翻译通道缺少 API 地址或模型")
        if not self.api_key and not self.base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("翻译通道没有可用的 API Key")
        if self.persistent_cache_path:
            self._initialize_persistent_cache()

    def _connect_cache(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.persistent_cache_path), exist_ok=True)
        connection = sqlite3.connect(self.persistent_cache_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_persistent_cache(self) -> None:
        connection = self._connect_cache()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS interface_translations ("
                "cache_version INTEGER NOT NULL, target TEXT NOT NULL, source TEXT NOT NULL, "
                "translation TEXT NOT NULL, updated_at INTEGER NOT NULL, "
                "PRIMARY KEY(cache_version, target, source))"
            )
            connection.commit()
        finally:
            connection.close()

    def _load_persistent(self, target: str, texts: list[str]) -> dict[str, str]:
        if not self.persistent_cache_path or not texts:
            return {}
        placeholders = ",".join("?" for _ in texts)
        connection = self._connect_cache()
        try:
            rows = connection.execute(
                "SELECT source, translation FROM interface_translations "
                f"WHERE cache_version=? AND target=? AND source IN ({placeholders})",
                [self.CACHE_VERSION, target, *texts],
            ).fetchall()
        finally:
            connection.close()
        return {source: translation for source, translation in rows}

    def _save_persistent(self, target: str, values: list[tuple[str, str]]) -> None:
        if not self.persistent_cache_path or not values:
            return
        now = int(time.time())
        connection = self._connect_cache()
        try:
            connection.executemany(
                "INSERT INTO interface_translations "
                "(cache_version, target, source, translation, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(cache_version, target, source) DO UPDATE SET "
                "translation=excluded.translation, updated_at=excluded.updated_at",
                [(self.CACHE_VERSION, target, source, translation, now) for source, translation in values],
            )
            connection.commit()
        finally:
            connection.close()

    def translate(self, texts: list[str], target: str, scope: str = "body") -> list[str]:
        if not texts:
            return []
        if scope not in {"interface", "body", "input"}:
            raise ValueError("翻译请求范围无效")
        source_texts = [str(text) for text in texts]
        results: list[str | None] = [None] * len(source_texts)
        missing_indexes: list[int] = []
        cacheable = scope in self.CACHEABLE_SCOPES
        persistent: dict[str, str] = {}
        if cacheable:
            with self._cache_lock:
                uncached = [text for text in source_texts if (scope, target, text) not in self._cache]
                persistent = self._load_persistent(target, list(dict.fromkeys(uncached)))
                for text, value in persistent.items():
                    self._cache[(scope, target, text)] = value
                for index, text in enumerate(source_texts):
                    cached = self._cache.get((scope, target, text))
                    if cached is None:
                        missing_indexes.append(index)
                    else:
                        results[index] = cached
        else:
            missing_indexes = list(range(len(source_texts)))
        if not missing_indexes:
            return [value or "" for value in results]
        masked: list[str] = []
        protected: list[list[str]] = []
        for index in missing_indexes:
            safe, items = protect_technical_text(source_texts[index])
            masked.append(safe)
            protected.append(items)
        deadline = time.monotonic() + self.REQUEST_DEADLINE_SECONDS
        if self.is_qwen_mt:
            packs: list[list[str]] = []
            pack: list[str] = []
            characters = 0
            for text in masked:
                if pack and (len(pack) >= self.QWEN_MT_PACK_ITEMS
                             or characters + len(text) > self.QWEN_MT_PACK_CHARACTERS):
                    packs.append(pack)
                    pack = []
                    characters = 0
                pack.append(text)
                characters += len(text)
            if pack:
                packs.append(pack)
            with ThreadPoolExecutor(max_workers=min(4, len(packs))) as executor:
                translated_packs = list(executor.map(lambda items: self._request_qwen_mt_pack(items, target, deadline), packs))
            translated = [value for values in translated_packs for value in values]
        else:
            language = "简体中文" if target == "zh-CN" else "英文"
            instruction = (
                f"你是严格的界面与正文翻译器。把输入 JSON 数组逐项翻译为{language}。"
                "保持数组长度和顺序，只输出 JSON 字符串数组。"
                "不要改写 [[CTB_KEEP_n]] 占位符，不解释，不使用 Markdown。"
            )
            prompt = instruction + "\n输入：" + json.dumps(masked, ensure_ascii=False)
            result = self._request(prompt, deadline)
            translated = _parse_translation_array(result, len(masked))
        for index, value in enumerate(translated):
            if any(value.count(f"[[CTB_KEEP_{token_index}]]") != 1
                   for token_index in range(len(protected[index]))):
                raise RuntimeError("翻译服务改写了受保护的代码、路径或链接；原文已保留")
        restored = [restore_technical_text(value, protected[index]) for index, value in enumerate(translated)]
        saved: list[tuple[str, str]] = []
        with self._cache_lock:
            for item_index, source_index in enumerate(missing_indexes):
                value = restored[item_index]
                results[source_index] = value
                if cacheable:
                    source = source_texts[source_index]
                    self._cache[(scope, target, source)] = value
                    saved.append((source, value))
            if cacheable:
                self._save_persistent(target, saved)
        return [value or "" for value in results]

    @staticmethod
    def _http_error_detail(error: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            raw = error.read().decode("utf-8", errors="replace").strip()
            if raw:
                payload = json.loads(raw)
                item = payload.get("error", payload) if isinstance(payload, dict) else payload
                if isinstance(item, dict):
                    detail = str(item.get("message") or item.get("code") or "").strip()
                elif isinstance(item, str):
                    detail = item.strip()
                if not detail:
                    detail = raw[:300]
        except (OSError, ValueError, AttributeError):
            detail = ""
        label = f"HTTP {error.code}"
        return f"{label}: {detail or error.reason}" if detail or error.reason else label

    @staticmethod
    def _retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
        value = str(error.headers.get("Retry-After") or "").strip() if error.headers else ""
        if value:
            try:
                return min(60.0, max(0.25, float(value)))
            except ValueError:
                try:
                    moment = parsedate_to_datetime(value)
                    return min(60.0, max(0.25, moment.timestamp() - time.time()))
                except (TypeError, ValueError, OverflowError):
                    pass
        return (1.0, 3.0)[min(attempt, 1)]

    @staticmethod
    def _attempt_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("翻译请求超过等待时间，请重试")
        return min(OpenAICompatibleTranslator.ATTEMPT_TIMEOUT_SECONDS, remaining)

    @staticmethod
    def _sleep_within_deadline(delay: float, deadline: float) -> bool:
        if delay >= deadline - time.monotonic():
            return False
        time.sleep(delay)
        return True

    def _wait_for_rate_slot(self, deadline: float) -> None:
        if not self.is_qwen_mt:
            return
        while True:
            with self._rate_lock:
                now = time.monotonic()
                cutoff = now - 60.0
                while self._rate_events and self._rate_events[0] <= cutoff:
                    self._rate_events.popleft()
                if len(self._rate_events) < self.QWEN_MT_REQUESTS_PER_MINUTE:
                    self._rate_events.append(now)
                    return
                delay = max(0.05, self._rate_events[0] + 60.0 - now)
            if not self._sleep_within_deadline(delay, deadline):
                raise TimeoutError("翻译通道达到速率上限，请稍后重试")

    def _request_qwen_mt(self, text: str, target: str, deadline: float | None = None) -> str:
        """Use Qwen-MT's native single-text contract; it does not support structured or batch output."""
        target_language = "Chinese" if target == "zh-CN" else "English"
        options: dict[str, object] = {"source_lang": "auto", "target_lang": target_language}
        placeholders = list(dict.fromkeys(re.findall(r"\[\[CTB_(?:KEEP|SEG)_\d+\]\]", text)))
        if placeholders:
            options["terms"] = [{"source": token, "target": token} for token in placeholders]
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": text}],
            "translation_options": options,
        }
        deadline = deadline or time.monotonic() + self.REQUEST_DEADLINE_SECONDS
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                timeout = self._attempt_timeout(deadline)
                request = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "CodexToolbox-SurfaceTranslation/0.1",
                    },
                    method="POST",
                )
                self._wait_for_rate_slot(deadline)
                if not self._request_slots.acquire(timeout=self._attempt_timeout(deadline)):
                    raise TimeoutError("翻译通道繁忙，请稍后重试")
                try:
                    with urllib.request.urlopen(request, timeout=min(timeout, self._attempt_timeout(deadline))) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                finally:
                    self._request_slots.release()
                return _extract_output(payload).strip()
            except urllib.error.HTTPError as error:
                last_error = RuntimeError(self._http_error_detail(error))
                if (error.code == 429 or 500 <= error.code < 600) and attempt < 2:
                    if self._sleep_within_deadline(self._retry_delay(error, attempt), deadline):
                        continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionResetError,
                    ssl.SSLError, http.client.RemoteDisconnected) as error:
                last_error = error
                if attempt < 2:
                    if self._sleep_within_deadline((0.25, 0.6)[attempt], deadline):
                        continue
                break
            except (ValueError, KeyError, RuntimeError) as error:
                last_error = error
                break
        raise RuntimeError(f"翻译请求失败：{last_error}")

    def _request_qwen_mt_pack(self, texts: list[str], target: str, deadline: float | None = None) -> list[str]:
        deadline = deadline or time.monotonic() + self.REQUEST_DEADLINE_SECONDS
        if len(texts) == 1:
            return [self._request_qwen_mt(texts[0], target, deadline)]
        markers = [f"[[CTB_SEG_{index}]]" for index in range(len(texts))]
        packed = "\n".join(f"{marker}\n{text}" for marker, text in zip(markers, texts))
        translated = self._request_qwen_mt(packed, target, deadline)
        positions = [translated.find(marker) for marker in markers]
        if all(position >= 0 for position in positions) and positions == sorted(positions):
            values: list[str] = []
            for index, marker in enumerate(markers):
                start = positions[index] + len(marker)
                end = positions[index + 1] if index + 1 < len(markers) else len(translated)
                value = translated[start:end].strip()
                if not value:
                    break
                values.append(value)
            if len(values) == len(texts):
                return values
        return [self._request_qwen_mt(text, target, deadline) for text in texts]

    def _request(self, prompt: str, deadline: float | None = None) -> str:
        deadline = deadline or time.monotonic() + self.REQUEST_DEADLINE_SECONDS
        order = [self.translation_wire_api, "chat_completions" if self.translation_wire_api == "responses" else "responses"]
        last_error: Exception | None = None
        for protocol in dict.fromkeys(order):
            for attempt in range(3):
                try:
                    timeout = self._attempt_timeout(deadline)
                    if protocol == "responses":
                        body = {"model": self.model, "input": prompt}
                        path = "/responses"
                    else:
                        body = {
                            "model": self.model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0,
                        }
                        path = "/chat/completions"
                    request = urllib.request.Request(
                        self.base_url + path,
                        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "User-Agent": "CodexToolbox-SurfaceTranslation/0.1",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=timeout) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    return _extract_output(payload)
                except urllib.error.HTTPError as error:
                    last_error = RuntimeError(self._http_error_detail(error))
                    retryable = error.code == 429 or 500 <= error.code < 600
                    if retryable and attempt < 2:
                        if self._sleep_within_deadline(self._retry_delay(error, attempt), deadline):
                            continue
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionResetError,
                        ssl.SSLError, http.client.RemoteDisconnected) as error:
                    last_error = error
                    if attempt < 2:
                        if self._sleep_within_deadline((0.25, 0.6)[attempt], deadline):
                            continue
                    break
                except (ValueError, KeyError, RuntimeError) as error:
                    last_error = error
                    break
        raise RuntimeError(f"翻译请求失败：{last_error}")


class SurfaceTranslationController:
    """维持 CDP WebSocket，处理页面发来的翻译请求。"""

    MAX_REQUEST_ITEMS = 40
    MAX_ITEM_CHARACTERS = 6000

    def __init__(
        self,
        websocket_url: str,
        overlay_source: str,
        config: dict,
        translator: OpenAICompatibleTranslator | None,
        on_error: Callable[[str], None] | None = None,
    ):
        self.websocket_url = websocket_url
        self.overlay_source = overlay_source
        self.config = config
        self.translator = translator
        self.on_error = on_error
        self._socket = None
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._script_id = ""
        self._closed = threading.Event()
        self._connected = threading.Event()
        self._reader = None
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="codex-translation")

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set() and not self._closed.is_set()

    def _bootstrap_expression(self) -> str:
        overlay = self.overlay_source.replace(
            "__CODEX_TOOLBOX_CONFIG__", json.dumps(self.config, ensure_ascii=False, separators=(",", ":"))
        )
        return (
            "(()=>{const start=()=>{" + overlay + "};"
            "if(document.body){return start();}"
            "addEventListener('DOMContentLoaded',start,{once:true});"
            "return '等待页面准备完成';})()"
        )

    def start(self) -> None:
        try:
            import websocket
        except ImportError as error:
            raise RuntimeError("缺少 websocket-client，无法启动表层翻译") from error
        self._socket = websocket.create_connection(self.websocket_url, timeout=10, suppress_origin=True)
        self._initialize_socket()
        self._reader = threading.Thread(target=self._read_loop, name="codex-surface-translation", daemon=True)
        self._reader.start()

    def _initialize_socket(self) -> None:
        self._command("Runtime.enable")
        self._command("Page.enable")
        self._command("Runtime.addBinding", {"name": "codexToolboxTranslate"})
        expression = self._bootstrap_expression()
        response = self._command("Page.addScriptToEvaluateOnNewDocument", {"source": expression})
        self._script_id = str(response.get("result", {}).get("identifier") or "")
        self._command("Runtime.evaluate", {"expression": expression, "awaitPromise": False})
        self._socket.settimeout(1)
        self._connected.set()

    def _command(self, method: str, params: dict | None = None) -> dict:
        with self._send_lock:
            request_id = self._next_id
            self._next_id += 1
            socket = self._socket
            socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}, ensure_ascii=False))
        while True:
            response = json.loads(socket.recv())
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", "CDP 命令失败"))
                return response

    def _send_evaluate(self, expression: str) -> None:
        if not self.is_connected:
            return
        with self._send_lock:
            request_id = self._next_id
            self._next_id += 1
            try:
                self._socket.send(json.dumps({
                    "id": request_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "awaitPromise": False},
                }, ensure_ascii=False))
            except Exception:
                self._connected.clear()
                try:
                    self._socket.close()
                except Exception:
                    pass

    def _reconnect(self) -> bool:
        import websocket
        parsed = urllib.parse.urlparse(self.websocket_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
            return False
        endpoint = f"http://127.0.0.1:{parsed.port}/json/list"
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        while not self._closed.is_set():
            try:
                with urllib.request.urlopen(endpoint, timeout=2) as response:
                    target = select_codex_page_target(json.load(response))
                if not target:
                    raise RuntimeError("Codex 主页面尚未就绪")
                socket = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
                if self._closed.is_set():
                    socket.close()
                    return False
                self._socket = socket
                self.websocket_url = target["webSocketDebuggerUrl"]
                self._initialize_socket()
                return True
            except Exception:
                if self._socket is not None:
                    try:
                        self._socket.close()
                    except Exception:
                        pass
                self._socket = None
                self._closed.wait(2)
        return False

    def _read_loop(self) -> None:
        import websocket
        while not self._closed.is_set():
            try:
                message = json.loads(self._socket.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as error:
                if not self._closed.is_set() and self.on_error:
                    self.on_error(str(error))
                self._connected.clear()
                if not self._reconnect():
                    break
                continue
            if message.get("method") != "Runtime.bindingCalled":
                continue
            params = message.get("params", {})
            if params.get("name") != "codexToolboxTranslate":
                continue
            try:
                request = json.loads(params.get("payload") or "{}")
            except json.JSONDecodeError:
                continue
            self._pool.submit(self._handle_request, request)

    def _handle_request(self, request: dict) -> None:
        request_id = str(request.get("id") or "")
        texts = request.get("texts")
        target = request.get("target")
        scope = request.get("scope") or "body"
        result: dict
        try:
            if (not request_id or not isinstance(texts, list) or target not in {"zh-CN", "en"}
                    or scope not in {"interface", "body", "input"}):
                raise ValueError("页面翻译请求格式无效")
            if not self.translator:
                raise RuntimeError("尚未配置动态翻译通道")
            if len(texts) > self.MAX_REQUEST_ITEMS:
                raise ValueError("单次翻译条目过多，请分批重试")
            clean = [str(text) for text in texts]
            if any(len(text) > self.MAX_ITEM_CHARACTERS for text in clean):
                raise ValueError("翻译内容过长，请拆分后重试；原文未被截断")
            result = {"id": request_id, "ok": True,
                      "texts": self.translator.translate(clean, target, scope=scope)}
        except Exception as error:
            if self.on_error:
                self.on_error(str(error))
            result = {"id": request_id, "ok": False, "error": str(error)[:300]}
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        self._send_evaluate(f"window.__codexToolboxTranslationResolve({encoded})")

    def stop(self) -> None:
        if self._closed.is_set():
            return
        try:
            self._send_evaluate("window.__codexToolboxSurfaceTranslation?.destroy?.()")
            if self._script_id:
                with self._send_lock:
                    request_id = self._next_id
                    self._next_id += 1
                    self._socket.send(json.dumps({
                        "id": request_id,
                        "method": "Page.removeScriptToEvaluateOnNewDocument",
                        "params": {"identifier": self._script_id},
                    }))
            # WebSocket 的 send 只保证数据写入本机缓冲区；给页面一个极短窗口执行清理。
            time.sleep(0.12)
        except Exception:
            pass
        self._closed.set()
        self._connected.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
