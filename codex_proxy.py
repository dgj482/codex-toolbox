# -*- coding: utf-8 -*-
"""Codex 本地翻译代理：Responses <-> Chat Completions 协议互转
Codex (Responses) -> 本代理(127.0.0.1:PORT) -> 上游 Chat Completions API（如 DeepSeek 官方）
支持：文本对话、函数调用（function calling）、流式 SSE
不支持：computer use、文件搜索、图像等多模态高级功能
"""
import json
import hmac
import hashlib
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib import error as urlerr
import secret_store

VERSION = "0.3"


def config_fingerprint(upstream_base, api_key, model, provider_id):
    """Identify a proxy's upstream configuration after caller authentication."""
    values = [upstream_base, api_key, model, provider_id]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

LOG_FILE = os.path.join(secret_store.app_data_dir(), "proxy.log")
LOG_ENABLED = os.environ.get("CODEX_TOOLBOX_DEBUG_PROXY") == "1"


def _log(msg):
    if not LOG_ENABLED:
        return
    import datetime as _dt
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n")
    except Exception:
        pass


class ProxyConfig:
    upstream_base = "https://api.deepseek.com/v1"
    api_key = ""
    model = "deepseek-chat"
    port = 18732
    provider_id = ""
    proxy_token = ""


class UnsupportedCapabilityError(ValueError):
    """请求包含本地协议翻译无法无损转换的能力。"""


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _response_input_items(body):
    """把 Responses 的单条或列表输入统一为列表，拒绝未知输入结构。"""
    raw_input = body.get("input", [])
    if isinstance(raw_input, (str, dict)):
        return [raw_input]
    if isinstance(raw_input, list):
        return raw_input
    raise UnsupportedCapabilityError("本地协议翻译不支持当前输入结构；请改用原生 Responses API。")


def _validate_supported_request(body):
    """不静默丢弃工具或媒体能力，避免生成看似成功但语义错误的请求。"""
    unsupported = []
    for tool in body.get("tools", []) or []:
        if not isinstance(tool, dict):
            unsupported.append("未知工具")
            continue
        tool_type = tool.get("type", "未知工具")
        if tool_type != "function":
            unsupported.append(str(tool_type))

    for item in _response_input_items(body):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            unsupported.append("未知消息内容")
            continue
        for block in content:
            if not isinstance(block, dict):
                unsupported.append("未知消息内容")
                continue
            block_type = block.get("type", "未知内容")
            if block_type not in ("input_text", "output_text"):
                unsupported.append(str(block_type))

    if unsupported:
        names = "、".join(dict.fromkeys(unsupported))
        raise UnsupportedCapabilityError(
            f"本地协议翻译不支持：{names}。请改用原生 Responses API 或移除该能力。"
        )


def responses_to_chat(body):
    """把 Codex 的 /v1/responses 请求体转为 Chat Completions 请求体"""
    messages = []
    _validate_supported_request(body)
    for item in _response_input_items(body):
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            role = item.get("role", "user")
            content = item.get("content", [])
            if isinstance(content, list):
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") in ("input_text", "output_text"):
                        texts.append(c.get("text", ""))
                content = "\n".join(texts)
            messages.append({"role": role, "content": content or ""})
        elif itype == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": str(item.get("output", "")),
            })
        elif itype == "function_call":
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": item.get("call_id", _new_id("call")),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }],
            })

    if body.get("instructions"):
        messages.insert(0, {"role": "system", "content": body["instructions"]})

    chat = {
        # 强制用代理配置的上游模型（Codex 会话里可能残留旧模型名 gpt-5.6-terra，
        # 直接转发会给 DashScope 等上游报 model not found）
        "model": ProxyConfig.model,
        "messages": messages,
        "stream": True,
    }
    tools = body.get("tools")
    if tools:
        converted = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            if t.get("type") == "function":
                fn = t.get("function") or t
                converted.append({
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    },
                })
        if converted:
            chat["tools"] = converted
    if body.get("tool_choice"):
        tc = body["tool_choice"]
        if isinstance(tc, str):
            chat["tool_choice"] = tc
        elif isinstance(tc, dict) and tc.get("type") == "function":
            chat["tool_choice"] = {"type": "function", "function": {"name": (tc.get("function") or {}).get("name", "")}}
    return chat


def chat_chunk_to_responses(chunk, state):
    """把一个 Chat Completions SSE chunk 转为 0..N 个 Responses SSE 事件"""
    events = []
    choices = chunk.get("choices") or []
    if not choices:
        return events
    choice = choices[0]
    delta = choice.get("delta") or {}
    finish = choice.get("finish_reason")

    content = delta.get("content")
    if content:
        events.append({
            "type": "response.output_text.delta",
            "item_id": state["item_id"],
            "output_index": 0,
            "content_index": 0,
            "delta": content,
        })

    tool_calls = delta.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in state["tool_calls"]:
                state["tool_calls"][idx] = {"id": "", "name": "", "args": ""}
            slot = state["tool_calls"][idx]
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]
            if tc.get("id"):
                events.append({
                    "type": "response.function_call_arguments.delta",
                    "item_id": state["tool_item_id"],
                    "output_index": idx,
                    "delta": f'{{"id": "{tc["id"]}", "type": "function", "name": "{slot["name"]}", "arguments": "',
                })
            if fn.get("arguments"):
                events.append({
                    "type": "response.function_call_arguments.delta",
                    "item_id": state["tool_item_id"],
                    "output_index": idx,
                    "delta": fn["arguments"],
                })

    if finish in ("stop", "tool_calls"):
        if state["tool_calls"]:
            for idx, slot in state["tool_calls"].items():
                events.append({
                    "type": "response.function_call_arguments.done",
                    "item_id": state["tool_item_id"],
                    "output_index": idx,
                    "arguments": slot["args"],
                })
            events.append({
                "type": "response.output_item.done",
                "item_id": state["tool_item_id"],
                "output_index": 0,
                "item": {
                    "id": state["tool_item_id"],
                    "type": "function_call",
                    "call_id": state["tool_calls"].get(0, {}).get("id", _new_id("call")),
                    "name": state["tool_calls"].get(0, {}).get("name", ""),
                    "arguments": state["tool_calls"].get(0, {}).get("args", "{}"),
                    "status": "completed",
                },
            })
        else:
            events.append({
                "type": "response.output_item.done",
                "item_id": state["item_id"],
                "output_index": 0,
                "item": {
                    "id": state["item_id"],
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": state.get("text", ""), "annotations": []}],
                },
            })
        events.append({
            "type": "response.completed",
            "response": {
                "id": state["response_id"],
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": ProxyConfig.model,
                "output": [],
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        })
    return events


def do_proxy_request(body, state):
    """转发到上游 Chat Completions 并流式返回转换后的 Responses SSE。
    失败重试 1 次（应对 DashScope 等偶发 SSL keep-alive 断开）"""
    _log(f"REQ model_in_body={body.get('model')} upstream={ProxyConfig.upstream_base} forced_model={ProxyConfig.model}")
    chat_body = responses_to_chat(body)
    _log(f"  chat model={chat_body.get('model')} msgs={len(chat_body.get('messages',[]))} stream={chat_body.get('stream')}")
    target = ProxyConfig.upstream_base.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ProxyConfig.api_key}",
        "Accept": "text/event-stream",
        "Connection": "close",  # 强制上游响应结束就关闭，避免 keep-alive 偶发 SSL EOF
    }
    payload = json.dumps(chat_body).encode("utf-8")
    last_err = None
    for attempt in range(2):
        try:
            req = urlreq.Request(target, data=payload, headers=headers, method="POST")
            resp = urlreq.urlopen(req, timeout=120)
            break  # 成功
        except urlerr.HTTPError as e:
            err_body = e.read().decode("utf-8", "ignore")
            _log(f"  HTTPError attempt={attempt+1} code={e.code} body={err_body[:300]}")
            raise RuntimeError(f"上游 {e.code}: {err_body[:300]}")
        except (urlerr.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = e
            _log(f"  URLError attempt={attempt+1} err={type(e).__name__}: {str(e)[:200]}")
            import time as _t
            _t.sleep(0.5)
    else:
        _log(f"  ALL RETRIES FAILED last_err={last_err}")
        raise RuntimeError(f"上游连接失败（已重试 1 次）: {last_err}")
    _log(f"  upstream OK status={resp.status} ct={resp.headers.get('Content-Type','')}")

    ct = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ct:
        return _stream_sse(resp, state)
    # 非流式兜底
    data = json.loads(resp.read().decode("utf-8"))
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    events = []
    if msg.get("content"):
        events.append({"type": "response.output_text.delta", "item_id": state["item_id"],
                       "output_index": 0, "content_index": 0, "delta": msg["content"]})
        state["text"] = msg["content"]
    tc = msg.get("tool_calls")
    if tc:
        for i, t in enumerate(tc):
            events.append({
                "type": "response.function_call_arguments.done",
                "item_id": state["tool_item_id"], "output_index": i,
                "arguments": (t.get("function") or {}).get("arguments", "{}"),
            })
    events.append({"type": "response.output_item.done", "item_id": state["item_id"], "output_index": 0,
                   "item": {"id": state["item_id"], "type": "message", "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": state.get("text", ""), "annotations": []}]}})
    events.append({"type": "response.completed", "response": {
        "id": state["response_id"], "object": "response", "created_at": int(time.time()),
        "status": "completed", "model": ProxyConfig.model, "output": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})
    return events


def _stream_sse(resp, state):
    events = []
    buffer = ""
    for raw in resp:
        text = raw.decode("utf-8", "ignore")
        buffer += text
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            events.extend(chat_chunk_to_responses(chunk, state))
    return events


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_events(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for ev in events:
            payload = json.dumps(ev, ensure_ascii=False)
            self.wfile.write(f"event: {ev['type']}\n".encode("utf-8"))
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True

    def _send_error(self, status, message, error_type):
        payload = json.dumps({"error": {"message": message, "type": error_type}}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        expected = f"Bearer {ProxyConfig.proxy_token}"
        supplied = self.headers.get("Authorization", "")
        if ProxyConfig.proxy_token and hmac.compare_digest(supplied, expected):
            return True
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", "Bearer")
        payload = b'{"error":{"message":"Unauthorized","type":"authentication_error"}}'
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return False

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if path == "/shutdown":
                expected = config_fingerprint(ProxyConfig.upstream_base, ProxyConfig.api_key,
                                              ProxyConfig.model, ProxyConfig.provider_id)
                if not hmac.compare_digest(str(body.get("config_fingerprint", "")), expected):
                    self._send_error(409, "Proxy configuration changed", "configuration_mismatch")
                    return
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
                self.server.stop_event.set()
                return
            _log(f"POST {path} len={length} model={body.get('model', '')}")
            if path.endswith("/responses"):
                state = {
                    "response_id": _new_id("resp"),
                    "item_id": _new_id("msg"),
                    "tool_item_id": _new_id("fc"),
                    "tool_calls": {},
                    "text": "",
                }
                events = do_proxy_request(body, state)
                _log(f"POST {path} -> sending {len(events)} events")
                self._send_events(events)
                return

            if path.endswith("/chat/completions"):
                # 上游直通（调试用）
                events = do_proxy_request(body, {"item_id": _new_id("msg"), "tool_item_id": _new_id("fc"),
                                                 "tool_calls": {}, "text": "", "response_id": _new_id("resp")})
                self._send_events(events)
                return
            _log(f"POST {path} -> 404 (no matching route)")
        except UnsupportedCapabilityError as e:
            _log(f"POST {path} UNSUPPORTED: {str(e)[:300]}")
            self._send_error(422, str(e), "unsupported_capability")
            return
        except Exception as e:
            _log(f"POST {path} EXCEPTION {type(e).__name__}: {str(e)[:300]}")
            self._send_error(500, str(e), "proxy_error")
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        if path.endswith("/status"):
            data = {
                "ok": True,
                "service": "codex-toolbox-proxy",
                "version": VERSION,
                "auth_required": True,
                "model": ProxyConfig.model,
                "provider_id": ProxyConfig.provider_id,
                "config_fingerprint": config_fingerprint(
                    ProxyConfig.upstream_base, ProxyConfig.api_key, ProxyConfig.model, ProxyConfig.provider_id,
                ),
            }
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path.endswith("/models"):
            data = {"object": "list", "data": [
                {"id": ProxyConfig.model, "object": "model", "owned_by": "proxy"},
                {"id": "deepseek-chat", "object": "model", "owned_by": "proxy"},
                {"id": "deepseek-reasoner", "object": "model", "owned_by": "proxy"},
            ]}
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


def start_proxy(upstream_base, api_key, model, port=18732, provider_id="", proxy_token=""):
    if not proxy_token:
        raise ValueError("本地代理缺少调用令牌")
    ProxyConfig.upstream_base = upstream_base
    ProxyConfig.api_key = api_key
    ProxyConfig.model = model
    ProxyConfig.provider_id = provider_id
    ProxyConfig.proxy_token = proxy_token
    server = ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
    server.stop_event = threading.Event()
    actual_port = server.server_address[1]
    ProxyConfig.port = actual_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, actual_port


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "https://api.deepseek.com/v1"
    model = sys.argv[2] if len(sys.argv) > 2 else "deepseek-chat"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 18732
    provider_id = sys.argv[4] if len(sys.argv) > 4 else ""
    key = secret_store.read_provider_secret(provider_id)
    proxy_token = secret_store.read_provider_proxy_secret(provider_id)
    server, p = start_proxy(base, key, model, port, provider_id=provider_id, proxy_token=proxy_token)
    print(f"Codex 翻译代理运行中: http://127.0.0.1:{p}/v1 -> {base}")
    print(f"上游模型: {model}")
    try:
        server.stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
