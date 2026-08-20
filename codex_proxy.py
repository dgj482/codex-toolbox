# -*- coding: utf-8 -*-
"""Codex 本地翻译代理：Responses <-> Chat Completions 协议互转
Codex (Responses) -> 本代理(127.0.0.1:PORT) -> 上游 Chat Completions API（如 DeepSeek 官方）
支持：文本对话、函数调用（function calling）、流式 SSE
不支持：computer use、文件搜索、图像等多模态高级功能
"""
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib import error as urlerr
import secret_store

VERSION = "0.1"

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


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def responses_to_chat(body):
    """把 Codex 的 /v1/responses 请求体转为 Chat Completions 请求体"""
    messages = []
    for item in body.get("input", []) or []:
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
            elif t.get("type") in ("custom", "web_search", "file_search"):
                pass  # 不支持的上游工具类型，跳过
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

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
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
        except Exception as e:
            _log(f"POST {path} EXCEPTION {type(e).__name__}: {str(e)[:300]}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": str(e), "type": "proxy_error"}}).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("/status"):
            import hashlib
            data = {
                "ok": True,
                "service": "codex-toolbox-proxy",
                "upstream": ProxyConfig.upstream_base,
                "model": ProxyConfig.model,
                "provider_id": ProxyConfig.provider_id,
                "key_hash": hashlib.sha256(ProxyConfig.api_key.encode()).hexdigest()[:12] if ProxyConfig.api_key else "",
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


def start_proxy(upstream_base, api_key, model, port=18732, provider_id=""):
    ProxyConfig.upstream_base = upstream_base
    ProxyConfig.api_key = api_key
    ProxyConfig.model = model
    ProxyConfig.provider_id = provider_id
    server = ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
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
    server, p = start_proxy(base, key, model, port, provider_id=provider_id)
    print(f"Codex 翻译代理运行中: http://127.0.0.1:{p}/v1 -> {base}")
    print(f"上游模型: {model}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
