"""Claude page translation bridge. Never modifies the installed Claude app.

The official Developer Tools console bootstraps the shared in-page engine.
Loading the script is deliberately separate from serving translations: opening
this server alone is not proof that a Claude page was attached.
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit


CLAUDE_ORIGIN = "https://claude.ai"
CLAUDE_ASSISTANT_SELECTOR = '[data-testid="assistant-message"] [data-perf-reply-text]'
CLAUDE_MESSAGE_SELECTOR = '[data-testid="assistant-message"],[data-testid="user-message"]'


class ClaudePageTranslationBridge:
    def __init__(self, translator, overlay_path, *, intercept_enter=True,
                 assistant_selector=CLAUDE_ASSISTANT_SELECTOR, message_selector=CLAUDE_MESSAGE_SELECTOR, composer_scope_selector="",
                 owner_alive=None, translate_interface=True, translate_input=True, translate_messages=True):
        self.translator = translator
        self.overlay_path = Path(overlay_path)
        self.token = secrets.token_urlsafe(32)
        self.config = {
            "appName": "Claude", "translateInterface": bool(translate_interface),
            "translateInput": bool(translate_input), "interceptEnter": bool(intercept_enter),
            # Verified in Claude Desktop's actual reply DOM, excluding actions and user text.
            "translateMessages": bool(translate_messages and assistant_selector),
            "assistantSelector": assistant_selector,
            "messageSelector": message_selector or assistant_selector,
            "composerSelector": '.ProseMirror[contenteditable="true"][role="textbox"]',
            "composerScopeSelector": composer_scope_selector or ".rounded-composer",
        }
        self.owner_alive = owner_alive
        self._closed = threading.Event()
        self._attached = threading.Event()
        self._server = None
        self._last_seen = 0.0
        self._reconnect = None
        self._reconnect_error = ''
        self._owner_watcher_started = False

    @property
    def base_url(self):
        if self._server is None:
            raise RuntimeError("Claude 页面翻译服务未启动")
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self):
        if self._server is not None:
            raise RuntimeError("Claude 页面翻译服务已经启动")
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Neither the capability token nor drafts belong in logs.

            def _respond(self, status, payload, content_type="application/json; charset=utf-8"):
                data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Access-Control-Allow-Origin", CLAUDE_ORIGIN)
                self.send_header("Vary", "Origin")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _authorized(self):
                parts = urlsplit(self.path)
                token = parse_qs(parts.query).get("token", [""])[0]
                origin = self.headers.get("Origin", "")
                return (not bridge._closed.is_set()
                        and origin in ("", CLAUDE_ORIGIN)
                        and hmac.compare_digest(token, bridge.token))

            def do_OPTIONS(self):
                if not self._authorized():
                    self._respond(403, {"ok": False})
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", CLAUDE_ORIGIN)
                self.send_header("Access-Control-Allow-Methods", "POST, GET")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self):
                if not self._authorized():
                    self._respond(403, {"ok": False})
                    return
                path = urlsplit(self.path).path
                if path == "/bootstrap":
                    self._respond(200, bridge.page_script(), "text/javascript; charset=utf-8")
                elif path == "/health":
                    bridge._last_seen = time.monotonic()
                    self._respond(200, {"ok": True})
                else:
                    self._respond(404, {"ok": False})

            def do_POST(self):
                if not self._authorized():
                    self._respond(403, {"ok": False})
                    return
                path = urlsplit(self.path).path
                if path == "/attached":
                    bridge._reconnect_error = ''
                    bridge._attached.set()
                    bridge._last_seen = time.monotonic()
                    self._respond(200, {"ok": True})
                    return
                if path == "/detached":
                    bridge._attached.clear()
                    self._respond(200, {"ok": True})
                    return
                if path != "/translate":
                    self._respond(404, {"ok": False})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        raise ValueError("翻译请求大小无效")
                    payload = json.loads(self.rfile.read(size).decode("utf-8"))
                    texts, target, scope = validate_translation_request(payload)
                except (ValueError, TypeError, KeyError, UnicodeError):
                    self._respond(400, {"ok": False, "error": "翻译请求无效"})
                    return
                try:
                    result = bridge.translator.translate(texts, target, scope)
                    if len(result) != len(texts) or not all(isinstance(value, str) for value in result):
                        raise RuntimeError("翻译服务返回的条目数量或格式不正确")
                    self._respond(200, {"ok": True, "texts": result})
                except Exception as error:
                    message = str(error)
                    key = getattr(bridge.translator, "api_key", "")
                    if key:
                        message = message.replace(key, "[密钥已隐藏]")
                    self._respond(502, {"ok": False, "error": message[:400] or "翻译失败"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="claude-page-bridge", daemon=True).start()
        if self.owner_alive:
            self.bind_owner(self.owner_alive)

    def bind_owner(self, owner_alive, reconnect=None):
        self.owner_alive = owner_alive
        self._reconnect = reconnect
        if not self._owner_watcher_started:
            self._owner_watcher_started = True
            threading.Thread(target=self._watch_owner, name="claude-page-owner", daemon=True).start()

    def _watch_owner(self):
        while not self._closed.wait(0.5):
            try:
                alive = self.owner_alive()
            except Exception:
                alive = False
            if not alive:
                self.stop()
                return
            if self._attached.is_set() and time.monotonic() - self._last_seen > 12:
                self._attached.clear()
            if not self._attached.is_set() and self._reconnect:
                try:
                    self._reconnect()
                except Exception as error:
                    self._reconnect_error = str(error)[:400]
                self._closed.wait(2)

    def wait_attached(self, timeout=8):
        return self._attached.wait(timeout)

    def state(self):
        return {"running": not self._closed.is_set() and self._server is not None,
                "attached": self._attached.is_set() and not self._closed.is_set(),
                "error": self._reconnect_error, "translate_messages": self.config["translateMessages"]}

    def bootstrap_command(self):
        # The full local program is passed to the official console as source.
        # Claude CSP refuses a page callback compiling fetched text with eval.
        return self.page_script()

    def page_script(self):
        source = self.overlay_path.read_text(encoding="utf-8")
        if source.count("__CODEX_TOOLBOX_CONFIG__") != 1:
            raise RuntimeError("页面翻译引擎格式不正确")
        source = source.replace("__CODEX_TOOLBOX_CONFIG__", json.dumps(self.config, ensure_ascii=False))
        base, token = json.dumps(self.base_url), json.dumps(self.token)
        return f"""(() => {{
  if(location.origin!=={json.dumps(CLAUDE_ORIGIN)})throw Error('不是 Claude 聊天页面');
  window.__claudeToolboxBridge?.stop?.();
  const base={base},token={token};
  let closed=false,timer=0,inflight=false;
  const endpoint=path=>base+path+'?token='+encodeURIComponent(token);
  const binding=raw=>{{
    const payload=JSON.parse(raw);
    fetch(endpoint('/translate'),{{method:'POST',credentials:'omit',headers:{{'Content-Type':'application/json'}},body:raw,signal:AbortSignal.timeout(50000)}})
      .then(r=>r.json()).then(result=>{{if(!closed)window.__codexToolboxTranslationResolve?.({{id:payload.id,...result}})}})
      .catch(error=>{{if(!closed)window.__codexToolboxTranslationResolve?.({{id:payload.id,ok:false,error:error.message}})}});
  }};
  function stop(){{
    if(closed)return;closed=true;clearInterval(timer);
    fetch(endpoint('/detached'),{{method:'POST',credentials:'omit',keepalive:true}}).catch(()=>{{}});
    window.__codexToolboxSurfaceTranslation?.destroy?.();
    if(window.codexToolboxTranslate===binding)delete window.codexToolboxTranslate;
    removeEventListener('pagehide',stop);delete window.__claudeToolboxBridge;
  }}
  window.codexToolboxTranslate=binding;
  window.__claudeToolboxBridge={{stop}};
  try{{ {source}; }}catch(error){{stop();throw error;}}
  addEventListener('pagehide',stop,{{once:true}});
  timer=setInterval(async()=>{{
    if(inflight||closed)return;inflight=true;
    try{{const r=await fetch(endpoint('/health'),{{credentials:'omit',signal:AbortSignal.timeout(4000)}});if(!r.ok)stop();}}
    catch(_){{stop();}}finally{{inflight=false;}}
  }},3000);
  fetch(endpoint('/attached'),{{method:'POST',credentials:'omit'}}).then(r=>{{if(!r.ok)stop();}}).catch(stop);
  return 'Claude 页面内翻译已启动';
}})()"""

    def stop(self):
        if self._closed.is_set():
            return
        self._closed.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def validate_translation_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须是对象")
    texts, target, scope = payload["texts"], payload["target"], payload["scope"]
    if (not isinstance(texts, list) or not 1 <= len(texts) <= 8
            or not all(isinstance(text, str) and text.strip() for text in texts)
            or sum(len(text) for text in texts) > 16000
            or target not in ("en", "zh-CN") or scope not in ("input", "body", "interface")):
        raise ValueError("翻译请求无效")
    return texts, target, scope
