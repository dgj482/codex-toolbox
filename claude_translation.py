"""Windows Claude companion: native accessibility, explicit preview, no CDP."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse

DOCK_SIZE = (152, 100)
PREVIEW_SIZE = (420, 500)


def _native_init():
    import comtypes
    # comtypes' first import can initialise STA. This dedicated UIA worker must be MTA.
    comtypes.CoUninitialize()
    comtypes.CoInitializeEx(0)
    import uiautomation  # noqa: F401 — load the UIA client only after the apartment is ready.


_NATIVE_EXECUTOR = ThreadPoolExecutor(max_workers=1, initializer=_native_init, thread_name_prefix="claude-uia")


def _process_path(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ""
    finally:
        kernel.CloseHandle(handle)


class ClaudeNativeAdapter:
    """All COM controls stay on one worker; only plain data leaves it."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Claude 桌面翻译目前仅支持 Windows")
        # uiautomation caches its COM client globally; reopening must retain its owning thread.
        self._executor = _NATIVE_EXECUTOR
        self._handle = 0
        self._pid = 0
        self._closed = threading.Event()

    def call(self, method, *args):
        if self._closed.is_set():
            raise RuntimeError("Claude 接口已关闭")
        future = self._executor.submit(self._execute, method, args, time.monotonic() + 12)
        try:
            return future.result(timeout=12)
        except TimeoutError as error:
            future.cancel()
            raise RuntimeError("Claude 操作超时，结果不确定；请查看 Claude，勿重复发送") from error

    def _execute(self, method, args, deadline):
        self._deadline = deadline
        if self._closed.is_set() or time.monotonic() >= deadline:
            raise RuntimeError("Claude 接口已关闭")
        return getattr(self, "_" + method)(*args)

    def close(self):
        self._closed.set()

    @staticmethod
    def _walk(root):
        import uiautomation as auto
        deadline = time.monotonic() + 3
        for index, (node, depth) in enumerate(auto.WalkControl(root, includeTop=True, maxDepth=32)):
            if index >= 1200 or time.monotonic() > deadline:
                raise RuntimeError("Claude 页面较复杂，请收起侧栏或缩小选中范围后重试")
            yield node

    @staticmethod
    def _pattern(node, name):
        import uiautomation as auto
        return node.GetPattern(getattr(auto.PatternId, name))

    @classmethod
    def _value(cls, node):
        for name in ("ValuePattern", "LegacyIAccessiblePattern"):
            pattern = cls._pattern(node, name)
            if pattern:
                return str(pattern.Value or "")
        return ""

    @classmethod
    def _editor_text(cls, node):
        pattern = cls._pattern(node, "TextPattern")
        if pattern:
            return str(pattern.DocumentRange.GetText(-1) or "")
        return cls._value(node)

    def _window(self):
        import uiautomation as auto
        user = ctypes.WinDLL("user32")
        user.GetForegroundWindow.restype = wintypes.HWND
        foreground = user.GetForegroundWindow()
        candidates = [node for node in auto.GetRootControl().GetChildren()
                      if node.NativeWindowHandle and os.path.basename(_process_path(node.ProcessId)).lower() == "claude.exe"
                      and not node.IsOffscreen]
        chosen = next((node for node in candidates if node.NativeWindowHandle == self._handle and node.ProcessId == self._pid), None)
        if self._handle and chosen is None:
            raise RuntimeError("绑定的 Claude 窗口已关闭或隐藏，请重新打开翻译")
        if not self._handle:
            chosen = next((node for node in candidates if node.NativeWindowHandle == foreground), None)
        if chosen is None and len(candidates) == 1:
            chosen = candidates[0]
        if chosen is None:
            raise RuntimeError("请先正常打开 Claude；有多个窗口时先切到需要翻译的 Claude 窗口")
        self._handle = chosen.NativeWindowHandle
        self._pid = chosen.ProcessId
        return chosen

    def _page(self):
        window = self._window()
        for node in self._walk(window):
            if node.ControlTypeName == "DocumentControl":
                url = self._value(node)
                if urlparse(url).hostname == "claude.ai":
                    return window, node, url
        raise RuntimeError("未找到 Claude 对话页面，请打开 Chat 对话后重试")

    @staticmethod
    def _is_editor(node):
        return (node.ControlTypeName == "EditControl" and "Claude" in node.Name
                and bool(re.search(r"prompt|提示词", node.Name, re.I))
                and node.IsEnabled and not node.IsOffscreen and not node.IsPassword)

    def _input(self):
        window, page, url = self._page()
        editors = [node for node in self._walk(page) if self._is_editor(node)]
        if len(editors) != 1:
            raise RuntimeError("未找到唯一的 Claude 提示词输入框，请关闭弹窗后重试")
        editor = editors[0]
        return {"window": window.NativeWindowHandle, "url": url,
                "editor": tuple(editor.GetRuntimeId()), "text": self._editor_text(editor)}, editor, page

    def _capture(self, scope, from_enter=False):
        if scope == "input":
            snapshot, editor, _ = self._input()
            if from_enter and not editor.HasKeyboardFocus:
                raise RuntimeError("Claude 输入焦点已变化，已取消回车操作")
            return snapshot
        window, page, url = self._page()
        pattern = self._pattern(page, "TextPattern")
        selection = pattern.GetSelection() if pattern else []
        text = "\n".join(part.GetText(-1) for part in selection).strip()
        if not text:
            raise RuntimeError("请先在 Claude 回答中选中需要翻译的正文，再点击“翻译选中文本”")
        return {"window": window.NativeWindowHandle, "url": url, "text": text}

    def _placement(self):
        snapshot, editor, _ = self._input()
        user = ctypes.WinDLL("user32")
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        scale = (user.GetDpiForWindow(snapshot["window"]) or 96) / 96
        rect = editor.BoundingRectangle
        return {"x": round(rect.right / scale) - DOCK_SIZE[0] - 12,
                "y": max(12, round(rect.top / scale) - DOCK_SIZE[1] - 12),
                "owner": self._handle, "pid": self._pid}

    def _focus(self):
        if not self._handle:
            return None
        user = ctypes.WinDLL("user32")
        user.GetForegroundWindow.restype = wintypes.HWND
        if user.GetForegroundWindow() != self._handle:
            return None
        import uiautomation as auto
        node = auto.GetFocusedControl()
        if node and self._is_editor(node):
            return {"window": self._handle}
        return None

    def _commit(self, original, text, send):
        if self._closed.is_set():
            raise RuntimeError("Claude 接口已关闭")
        current, editor, page = self._input()
        if any(current[key] != original[key] for key in ("window", "url", "editor", "text")):
            raise RuntimeError("Claude 草稿或对话已变化，已取消写入；请重新翻译")
        pattern = self._pattern(editor, "ValuePattern")
        if self._closed.is_set() or time.monotonic() >= getattr(self, "_deadline", float("inf")):
            raise RuntimeError("写入操作已过期，已取消写入")
        if not pattern or not pattern.SetValue(text, waitTime=0):
            raise RuntimeError("译文未能写入 Claude；原文未被自动发送")
        # Claude's editor reconciles its accessibility value asynchronously after SetValue.
        settle_deadline = time.monotonic() + 2
        while True:
            latest, _, _ = self._input()
            if latest["url"] != original["url"] or latest["editor"] != original["editor"]:
                raise RuntimeError("写入后对话或输入框已变化，已取消发送")
            if latest["text"].strip() == text.strip():
                break
            if time.monotonic() >= settle_deadline:
                raise RuntimeError("Claude 输入框未确认译文，已取消发送")
            time.sleep(0.1)
        if not send:
            return "译文已写入 Claude，可在 Claude 中按回车发送"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            latest, _, page = self._input()
            if latest["url"] != original["url"] or latest["editor"] != original["editor"] or latest["text"].strip() != text.strip():
                raise RuntimeError("写入后草稿或对话已变化，已取消发送")
            buttons = [node for node in self._walk(page)
                       if node.ControlTypeName == "ButtonControl"
                       and node.Name.strip().casefold() in {"send message", "send", "发送消息", "发送"}
                       and node.IsEnabled and not node.IsOffscreen]
            if len(buttons) == 1:
                if self._closed.is_set() or time.monotonic() >= getattr(self, "_deadline", float("inf")):
                    raise RuntimeError("Claude 接口已关闭，已取消发送")
                invoke = self._pattern(buttons[0], "InvokePattern")
                if not invoke:
                    raise RuntimeError("译文已写入；发送按钮不支持调用，请在 Claude 手动发送")
                try:
                    invoke.Invoke(waitTime=0)
                except Exception as error:
                    raise RuntimeError("发送结果不确定，请检查 Claude；工具箱不会自动重试") from error
                return "已调用 Claude 发送按钮，请在 Claude 查看消息状态"
            time.sleep(0.1)
        raise RuntimeError("译文已写入，但发送按钮不可用；请在 Claude 手动发送")


class ClaudeEnterHook:
    """Fast low-level hook: no COM or network work inside the callback."""
    def __init__(self, focus, on_enter):
        self.focus = focus
        self.on_enter = on_enter
        self._ready = threading.Event()
        self._thread_id = 0
        self._error = ""

    def start(self):
        self._thread = threading.Thread(target=self._run, name="claude-enter", daemon=True)
        self._thread.start()
        if not self._ready.wait(3) or self._error:
            raise RuntimeError(self._error or "Claude 回车监听未能启动")

    def stop(self):
        if self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, 0x12, 0, 0)
            self._thread_id = 0

    def _run(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        imm = ctypes.WinDLL("imm32")
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        class Keyboard(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]
        class GuiInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD)] + [(name, wintypes.HWND) for name in ("active", "focus", "capture", "menu", "move", "caret")] + [("rect", wintypes.RECT)]
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetAsyncKeyState.restype = ctypes.c_short
        imm.ImmGetContext.argtypes = [wintypes.HWND]
        imm.ImmGetContext.restype = wintypes.HANDLE
        imm.ImmGetCompositionStringW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self._thread_id = kernel.GetCurrentThreadId()
        down = False
        suppressed = False
        def composing():
            info = GuiInfo()
            info.size = ctypes.sizeof(info)
            if not user.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.focus:
                return False
            context = imm.ImmGetContext(info.focus)
            if not context:
                return False
            try:
                return imm.ImmGetCompositionStringW(context, 8, None, 0) > 0
            finally:
                imm.ImmReleaseContext(info.focus, context)
        @callback_type
        def callback(code, message, pointer):
            nonlocal down, suppressed
            if code >= 0:
                event = ctypes.cast(pointer, ctypes.POINTER(Keyboard)).contents
                if event.vkCode == 13 and not event.flags & 0x10:
                    if message in (0x101, 0x105):
                        was_suppressed = suppressed
                        down = suppressed = False
                        if was_suppressed:
                            return 1
                    elif message in (0x100, 0x104):
                        if down:
                            if suppressed:
                                return 1
                        else:
                            down = True
                            item = self.focus()
                            if (item and user.GetForegroundWindow() == item["window"]
                                    and not any(user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5b, 0x5c))
                                    and not composing()):
                                try:
                                    suppressed = bool(self.on_enter())
                                except Exception:
                                    suppressed = False
                                if suppressed:
                                    return 1
            return user.CallNextHookEx(None, code, message, pointer)
        hook = user.SetWindowsHookExW(13, callback, kernel.GetModuleHandleW(None), 0)
        if not hook:
            self._error = "Windows 拒绝注册 Claude 回车监听"
            self._ready.set()
            return
        self._ready.set()
        try:
            msg = wintypes.MSG()
            while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
        finally:
            user.UnhookWindowsHookEx(hook)


class ClaudeTranslationSession:
    def __init__(self, adapter, translator, on_preview=None):
        self.adapter = adapter
        self.translator = translator
        self.on_preview = on_preview
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude-translation")
        self._closed = threading.Event()
        self._focus = None
        self._focus_time = 0
        self._hook = None
        self._original = None
        self._revision = 0
        self._state = {"phase": "idle", "scope": "input", "direction": "zh-en", "source": "", "translated": "", "token": "", "message": "等待翻译"}

    def start(self, intercept_enter=True):
        self.adapter.call("capture", "input")
        threading.Thread(target=self._watch_focus, name="claude-focus", daemon=True).start()
        if intercept_enter:
            self._hook = ClaudeEnterHook(self.focus, self.enter)
            self._hook.start()

    def _watch_focus(self):
        while not self._closed.is_set():
            try:
                item = self.adapter.call("focus")
            except Exception:
                item = None
            with self._lock:
                self._focus, self._focus_time = item, time.monotonic()
            self._closed.wait(0.15)

    def focus(self):
        with self._lock:
            busy = self._state["phase"] in {"busy", "confirming"}
            return dict(self._focus) if self._focus and (busy or time.monotonic() - self._focus_time < 0.5) else None

    def state(self):
        with self._lock:
            return {**self._state, "revision": self._revision, "running": not self._closed.is_set()}

    def _update(self, **values):
        with self._lock:
            if self._closed.is_set():
                return
            self._state.update(values)
            self._revision += 1

    def set_direction(self, direction):
        if direction not in {"zh-en", "en-zh"}:
            raise ValueError("未知翻译方向")
        with self._lock:
            if self._closed.is_set() or self._state["phase"] in {"busy", "confirming"}:
                raise RuntimeError("正在处理上一项操作，请完成后更换方向")
            if direction != self._state["direction"]:
                self._original = None
                self._update(direction=direction, phase="idle", token="", source="", translated="", message="等待翻译")

    def dismiss_preview(self):
        with self._lock:
            if self._state["phase"] in {"busy", "confirming"}:
                raise RuntimeError("正在处理上一项操作")
            self._original = None
            self._update(phase="idle", token="", message="预览已收起")

    def enter(self):
        with self._lock:
            if self._closed.is_set():
                return False
            phase = self._state["phase"]
            if phase in {"busy", "confirming"}:
                return True
            if phase == "preview" and self._state["scope"] == "input":
                token, text = self._state["token"], self._state["translated"]
                self._update(phase="confirming", message="正在写入并发送…")
                self._pool.submit(self._confirm, token, text, True)
                return True
            focus = self.focus()
            if not focus:
                return False
            # Read the actual draft after the key event, never classify a cached pre-paste value.
            self.request("input", from_enter=True)
            return True

    def request(self, scope, from_enter=False):
        if scope not in {"input", "body"}:
            raise ValueError("未知翻译范围")
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("Claude 翻译已关闭")
            if self._state["phase"] in {"busy", "confirming"}:
                raise RuntimeError("正在处理上一项操作")
            self._original = None
            self._update(phase="busy", scope=scope, token="", source="", translated="", message="正在翻译…")
            self._pool.submit(self._translate, scope, from_enter, self._state["direction"])

    def _translate(self, scope, from_enter=False, direction="zh-en"):
        try:
            original = self.adapter.call("capture", scope, from_enter)
            source = original["text"]
            if not source.strip():
                raise RuntimeError("Claude 输入框为空")
            if len(source) > 12000:
                raise RuntimeError("单次内容超过 12000 字符，请分段翻译；原文未被截断")
            has_cjk = bool(re.search(r"[\u3400-\u9fff]", source))
            matches_direction = has_cjk if direction == "zh-en" else bool(re.search(r"[A-Za-z]", source)) and not has_cjk
            if from_enter and not matches_direction:
                message = self.adapter.call("commit", original, source, True)
                self._update(phase="done", token="", message=message)
                return
            target = "en" if scope == "input" and direction == "zh-en" else "zh"
            translated = self.translator.translate([source], target, scope=scope)[0]
            if not translated.strip():
                raise RuntimeError("翻译服务返回空译文，原文已保留")
            with self._lock:
                if self._closed.is_set():
                    return
                self._original = original
                self._update(phase="preview", source=source, translated=translated, token=uuid.uuid4().hex,
                             message="核对译文后按回车确认发送；Shift+回车换行" if scope == "input" else "正文译文；不会写入输入框")
            if self.on_preview:
                self.on_preview()
        except Exception as error:
            self._update(phase="error", token="", message=str(error))

    def confirm(self, token, text, send=False):
        with self._lock:
            if self._state["phase"] != "preview" or self._state["scope"] != "input" or token != self._state["token"] or not token:
                raise RuntimeError("译文预览已过期，请重新翻译")
            if not str(text).strip():
                raise ValueError("译文不能为空")
            if len(text) > 12000:
                raise ValueError("译文超过 12000 字符")
            self._update(phase="confirming", message="正在写入并发送…" if send else "正在写入…")
            self._pool.submit(self._confirm, token, text, send)
        return "已确认，正在后台写入并发送…" if send else "已确认，正在后台写入…"

    def _confirm(self, token, text, send):
        try:
            if self._closed.is_set():
                raise RuntimeError("Claude 翻译已关闭")
            message = self.adapter.call("commit", self._original, str(text), bool(send))
            self._update(phase="done", token="", message=message)
            return message
        except Exception as error:
            self._update(phase="error", token="", message=str(error))
            raise

    def stop(self):
        self._closed.set()
        if self._hook:
            self._hook.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.adapter.close()
