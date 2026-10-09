# -*- coding: utf-8 -*-
"""测试 codex_proxy：mock 一个 Chat Completions 上游，验证 Responses 转换"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerr
from urllib import request as urlreq
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_proxy

class MockChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        model = body.get("model", "?")
        # 检查转换后的消息结构
        msgs = body.get("messages", [])
        print(f"  [mock] 收到 chat/completions: model={model} messages={len(msgs)} tools={'tools' in body}")
        for m in msgs:
            print(f"    role={m['role']} content={str(m.get('content'))[:50]}")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        # 场景1：如果 messages 最后是 tool 消息 → 返回文本
        # 场景2：如果请求带 tools 且没有 tool 结果 → 返回工具调用
        has_tools = "tools" in body
        last_is_tool = msgs and msgs[-1].get("role") == "tool"

        def sse(event_data):
            self.wfile.write(f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n".encode("utf-8"))

        if has_tools and not last_is_tool:
            # 返回工具调用
            sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": None,
                                                    "tool_calls": [{"index": 0, "id": "call_mock_1",
                                                                    "type": "function",
                                                                    "function": {"name": "get_weather", "arguments": ""}}]}}]})
            sse({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"city": "北京"}'}}]}}]})
            sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            # 返回文本
            sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "你好"}}]})
            sse({"choices": [{"index": 0, "delta": {"content": "，这是"}}]})
            sse({"choices": [{"index": 0, "delta": {"content": "转换测试结果"}}]})
            sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        sse({"choices": []})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path.endswith("/models"):
            payload = json.dumps({"object": "list", "data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


def send_responses_request(port, body, token="proxy-test-token"):
    """模拟 Codex 向代理发 /v1/responses 请求"""
    req = urlreq.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    resp = urlreq.urlopen(req, timeout=30)
    text = resp.read().decode("utf-8")
    return text


def main():
    # 启动 mock 上游
    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockChatHandler)
    mock_port = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    print(f"[test] mock 上游: http://127.0.0.1:{mock_port}")

    # 启动代理
    server, port = codex_proxy.start_proxy(
        f"http://127.0.0.1:{mock_port}/v1", "dummy-token", "deepseek-chat", port=0,
        provider_id="test", proxy_token="proxy-test-token",
    )
    print(f"[test] 代理: http://127.0.0.1:{port}/v1")

    time.sleep(0.5)

    # 未认证的本机进程不能读取状态、模型或使用上游额度。
    try:
        urlreq.urlopen(f"http://127.0.0.1:{port}/status", timeout=5)
        raise AssertionError("未认证状态请求不应成功")
    except urlerr.HTTPError as error:
        assert error.code == 401

    unauthorized = urlreq.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": "Bearer wrong-token"},
        method="POST",
    )
    try:
        urlreq.urlopen(unauthorized, timeout=5)
        raise AssertionError("错误令牌不应转发请求")
    except urlerr.HTTPError as error:
        assert error.code == 401

    status_request = urlreq.Request(
        f"http://127.0.0.1:{port}/status",
        headers={"Authorization": "Bearer proxy-test-token"},
    )
    with urlreq.urlopen(status_request, timeout=5) as response:
        status = json.load(response)
    assert status["config_fingerprint"] == codex_proxy.config_fingerprint(
        f"http://127.0.0.1:{mock_port}/v1", "dummy-token", "deepseek-chat", "test"
    )

    # 测试1：纯文本
    print("\n========== 测试1：纯文本对话 ==========")
    body1 = {
        "model": "deepseek-chat",
        "instructions": "你是测试助手",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "你好"}]},
        ],
        "stream": True,
    }
    out1 = send_responses_request(port, body1)
    types1 = []
    for line in out1.splitlines():
        if line.startswith("event:"):
            types1.append(line[6:].strip())
    print("收到事件序列:", types1)
    assert "response.output_text.delta" in types1, "缺少文本 delta"
    assert "response.completed" in types1, "缺少 completed"
    print("测试1 通过")

    # 测试2：工具调用
    print("\n========== 测试2：工具调用（function calling）==========")
    body2 = {
        "model": "deepseek-chat",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "北京天气"}]},
        ],
        "tools": [
            {"type": "function", "name": "get_weather", "description": "查天气",
             "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}},
        ],
        "stream": True,
    }
    out2 = send_responses_request(port, body2)
    types2 = []
    for line in out2.splitlines():
        if line.startswith("event:"):
            types2.append(line[6:].strip())
    print("收到事件序列:", types2)
    assert any("function_call" in t for t in types2), "缺少 function_call 事件"
    assert "response.completed" in types2, "缺少 completed"
    # 提取 arguments
    for line in out2.splitlines():
        if line.startswith("data:") and "function_call_arguments.done" in out2:
            pass
    print("测试2 通过")

    # 测试3：含历史工具结果（function_call_output）
    print("\n========== 测试3：带工具结果继续对话 ==========")
    body3 = {
        "model": "deepseek-chat",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "北京天气"}]},
            {"type": "function_call", "call_id": "call_mock_1", "name": "get_weather", "arguments": '{"city": "北京"}'},
            {"type": "function_call_output", "call_id": "call_mock_1", "output": "晴 25度"},
        ],
        "stream": True,
    }
    out3 = send_responses_request(port, body3)
    types3 = [line[6:].strip() for line in out3.splitlines() if line.startswith("event:")]
    print("收到事件序列:", types3)
    assert "response.completed" in types3
    print("测试3 通过")

    server.shutdown()
    mock.shutdown()
    print("\n全部测试通过！")


if __name__ == "__main__":
    main()
