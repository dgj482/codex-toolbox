"""Bootstrap a local page script using Claude's explicitly enabled DevTools.

No debugging launch switches, installed-file edits, or security preference
changes are performed. Console pasting preserves the original clipboard.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit


def _native_init():
    import comtypes
    comtypes.CoUninitialize()
    comtypes.CoInitializeEx(0)
    import uiautomation  # noqa: F401 — initialize UIA after the COM apartment is ready.


_NATIVE_EXECUTOR = ThreadPoolExecutor(max_workers=1, initializer=_native_init, thread_name_prefix="claude-page-loader")


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


def _walk(root):
    import uiautomation as auto
    deadline = time.monotonic() + 3
    for index, (node, _) in enumerate(auto.WalkControl(root, includeTop=True, maxDepth=32)):
        if index >= 1200 or time.monotonic() > deadline:
            raise RuntimeError("Claude 页面较复杂，请收起侧栏后重试")
        yield node


def _retry_uia_lookup(operation, deadline, cancelled):
    """Reacquire stale UIA references; this helper must never replay key input."""
    while True:
        if cancelled():
            raise RuntimeError("Claude 翻译接入已取消")
        try:
            return operation()
        except Exception as error:
            code = getattr(error, "hresult", None)
            if code is None or code & 0xffffffff != 0x80040201:
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError("Claude 页面控件持续变化，无法完成接入") from error
            time.sleep(0.1)


def _is_editor(node):
    return (node.ControlTypeName == "EditControl" and "Claude" in node.Name
            and bool(re.search(r"prompt|提示词", node.Name, re.I))
            and node.IsEnabled and not node.IsOffscreen and not node.IsPassword)


def _focus_editor(window, editor, user, owner, *, activate=True):
    # A failed SetForegroundWindow return is not proof that focus is wrong.
    if activate:
        window.SetActive(waitTime=0.1)
        window.SetFocus()
    editor.SetFocus()
    if user.GetForegroundWindow() != owner or not editor.HasKeyboardFocus:
        raise RuntimeError("无法聚焦 Claude 输入框，请切回 Claude 后重试")


def check_developer_permissions(user_data=None):
    root = Path(user_data) if user_data else Path(os.environ.get("APPDATA", "")) / "Claude"
    try:
        settings = json.loads((root / "developer_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    if settings.get("allowDevTools") is not True:
        raise RuntimeError("请在 Claude 的 Help → Troubleshooting 中手动开启 Developer Mode")
    try:
        prefs = json.loads((root / "Preferences").read_text(encoding="utf-8"))
        allowed = prefs.get("electron", {}).get("devtools", {}).get("preferences", {}).get("disable-self-xss-warning")
    except (OSError, ValueError, AttributeError):
        allowed = False
    if allowed not in (True, "true"):
        raise RuntimeError("请先在 Claude 官方 Console 中手动确认 allow pasting；工具箱不会更改该安全设置")


def launch_claude():
    """Use Claude's installed launcher to open or restore its single instance."""
    if os.name != "nt":
        raise RuntimeError("Claude 桌面翻译目前仅支持 Windows")
    executable = Path(os.environ.get("LOCALAPPDATA", "")) / "AnthropicClaude" / "claude.exe"
    if not executable.is_file():
        raise RuntimeError("未找到 Claude 桌面版，请先安装 Claude")
    subprocess.Popen([str(executable)], cwd=str(executable.parent))


class ClaudePageLoader:
    def __init__(self, bridge, window=None, on_stage=None):
        self.bridge = bridge
        self.toolbox_window = window
        self.on_stage = on_stage or (lambda stage: None)
        self.owner = 0
        self.pid = 0

    def attach(self):
        check_developer_permissions()
        cancelled = threading.Event()
        future = _NATIVE_EXECUTOR.submit(self._attach, cancelled)
        try:
            future.result(timeout=45)
        except TimeoutError as error:
            cancelled.set()
            future.cancel()
            raise RuntimeError("Claude 页面接入超时，请检查其窗口；未自动发送任何消息") from error

    def reconnect_if_active(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetForegroundWindow.restype = wintypes.HWND
        if user.GetForegroundWindow() != self.owner:
            return
        self.attach()

    def _attach(self, cancelled):
        import uiautomation as auto
        user = ctypes.WinDLL("user32", use_last_error=True)
        for name, args, result in (
                ("IsWindow", [wintypes.HWND], wintypes.BOOL),
                ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
                ("IsIconic", [wintypes.HWND], wintypes.BOOL),
                ("GetForegroundWindow", [], wintypes.HWND),
                ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)):
            function = getattr(user, name)
            function.argtypes, function.restype = args, result
        deadline = time.monotonic() + 20

        def alive():
            current_pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(self.owner, ctypes.byref(current_pid))
            return (not cancelled.is_set() and self.bridge.state()['running']
                    and bool(user.IsWindow(self.owner)) and current_pid.value == self.pid
                    and (bool(user.IsWindowVisible(self.owner)) or bool(user.IsIconic(self.owner))))

        def stopped():
            return cancelled.is_set() or not self.bridge.state()["running"]

        def inspected_tools():
            def inspect():
                matches = []
                for node in auto.GetRootControl().GetChildren():
                    if node.ProcessId != self.pid or not node.Name.startswith("Developer Tools - "):
                        continue
                    parsed = urlsplit(node.Name[len("Developer Tools - "):])
                    if parsed.scheme == "https" and parsed.hostname == "claude.ai":
                        matches.append(node.NativeWindowHandle)
                if len(matches) > 1:
                    raise RuntimeError("请只保留一个 Claude 聊天页面的开发者工具")
                return matches[0] if matches else None
            return _retry_uia_lookup(inspect, deadline, stopped)

        def discover_page():
            windows = [node for node in auto.GetRootControl().GetChildren()
                       if node.Name == "Claude" and node.NativeWindowHandle
                       and os.path.basename(_process_path(node.ProcessId)).lower() == "claude.exe"]
            if len(windows) > 1:
                raise RuntimeError("请只保留一个 Claude 主窗口")
            if not windows:
                return None
            window = windows[0]
            handle, pid = window.NativeWindowHandle, window.ProcessId
            if self.owner and (handle != self.owner or pid != self.pid):
                raise RuntimeError("原 Claude 窗口已关闭，已取消重新接入")
            if window.IsOffscreen:
                window.SetActive(waitTime=0.1)
            editors = [node for node in _walk(window) if _is_editor(node)]
            return (handle, pid) if len(editors) == 1 else None

        while time.monotonic() < deadline:
            page = _retry_uia_lookup(discover_page, deadline, stopped)
            if page:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("等待 Claude 对话页面超时，请打开 Chat 页面并关闭弹窗后重试")
        self.owner, self.pid = page
        deadline = time.monotonic() + 20
        self.on_stage("page-ready")

        def focus_current_editor():
            if not alive():
                raise RuntimeError("Claude 窗口已关闭")
            # Activating the window can rebuild the DOM. Find the editor afterwards.
            current = auto.ControlFromHandle(self.owner)
            current.SetActive(waitTime=0.1)
            current.SetFocus()
            editors = [node for node in _walk(current) if _is_editor(node)]
            if len(editors) != 1:
                return False
            _focus_editor(current, editors[0], user, self.owner, activate=False)
            return True

        while not _retry_uia_lookup(focus_current_editor, deadline, stopped):
            if time.monotonic() >= deadline:
                raise RuntimeError("等待 Claude 输入框就绪超时")
            time.sleep(0.1)
        self.on_stage("editor-focused")
        devtools_handle = inspected_tools()
        if devtools_handle is None:
            if not alive() or user.GetForegroundWindow() != self.owner:
                raise RuntimeError("Claude 焦点已变化，已取消接入")
            auto.SendKeys("{Ctrl}{Alt}i", waitTime=0.1)
        while time.monotonic() < deadline:
            devtools_handle = inspected_tools()
            if devtools_handle is not None:
                break
            if not alive():
                raise RuntimeError("Claude 窗口已关闭")
            time.sleep(0.15)
        if devtools_handle is None:
            raise RuntimeError("Claude 未打开官方开发者工具，请检查 Developer Mode 或组织策略")

        def select_current_console():
            current = auto.ControlFromHandle(devtools_handle)
            current.SetActive(waitTime=0.1)
            current.SetFocus()
            if user.GetForegroundWindow() != devtools_handle:
                raise RuntimeError("无法聚焦 Claude 官方开发者工具")
            tab = next((node for node in _walk(current)
                        if node.ControlTypeName == "TabItemControl" and node.Name == "Console"), None)
            if tab is None:
                return False
            selection = tab.GetPattern(auto.PatternId.SelectionItemPattern)
            return bool(selection and selection.Select(waitTime=0.1))

        while not _retry_uia_lookup(select_current_console, deadline, stopped):
            if time.monotonic() >= deadline:
                raise RuntimeError("未找到 Claude 官方 Console 标签页")
            time.sleep(0.1)
        # Chromium's native UIA tree omits the Console editor. Use its documented
        # focus/paste shortcuts, checking both the empty input and pasted source.
        from claude_clipboard import ConsoleClipboard

        def keys(value):
            if (not alive() or time.monotonic() >= deadline
                    or user.GetForegroundWindow() != devtools_handle):
                raise RuntimeError("Claude Console 焦点已变化或接入已过期，已取消执行")
            # Input is sent exactly once; stale lookup recovery never replays it.
            auto.SendKeys(value, charMode=False, waitTime=0.15)

        self.on_stage('console-ready')
        with ConsoleClipboard(self.toolbox_window) as clipboard:
            sentinel = "ctb-empty-" + secrets.token_hex(12)
            clipboard.set(sentinel)
            keys("{Ctrl}`")
            keys("{Ctrl}a{Ctrl}c")
            existing = clipboard.copied()
            if existing.strip() not in ("", sentinel):
                raise RuntimeError("Claude Console 已有未执行的代码，请先处理后重试")
            source = self.bridge.bootstrap_command()
            clipboard.set(source)
            keys("{Ctrl}v")
            keys("{Ctrl}a{Ctrl}c")
            actual = clipboard.copied()
            if actual.replace("\r\n", "\n").strip() != source.replace("\r\n", "\n").strip():
                raise RuntimeError("Claude Console 未完整接收脚本，已取消执行")
            keys("{Ctrl}{End}")
            keys("{Enter}")
            self.on_stage("script-submitted")
        self.on_stage("clipboard-restored")
        if not self.bridge.wait_attached(max(0, min(5, deadline-time.monotonic()))):
            raise RuntimeError("Claude 页面未确认接入，请查看 Console；工具箱不会重复执行")
        # Only the inspected page remains; no separate translation window is created.
        if user.GetForegroundWindow() == devtools_handle:
            auto.SendKeys("{Alt}{F4}", waitTime=0.1)
        self.bridge.bind_owner(alive, self.reconnect_if_active)
        self.on_stage('owner-bound')

