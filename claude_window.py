"""Keep the native translation companion attached to one Claude window."""
import ctypes
from ctypes import wintypes
import threading


class AttachedLayout:
    """Remember a position within the owner's client area, not the desktop."""
    def __init__(self):
        self.bounds = None
        self.relative = (1.0, 1.0)

    def place(self, bounds, actual):
        left, top, right, bottom = bounds
        x, y, width, height = actual
        width, height = min(width, right-left-16), min(height, bottom-top-16)
        room_x, room_y = max(0, right-left-width-16), max(0, bottom-top-height-16)
        if self.bounds is not None and bounds != self.bounds:
            x, y = left+8+self.relative[0]*room_x, top+8+self.relative[1]*room_y
        x, y = round(max(left+8, min(x, left+8+room_x))), round(max(top+8, min(y, top+8+room_y)))
        self.relative = ((x-left-8)/room_x if room_x else 0, (y-top-8)/room_y if room_y else 0)
        self.bounds = bounds
        return x, y, width, height


class NativeWindows:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            "SetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result

    def exists(self, handle):
        return bool(self.user.IsWindow(handle))

    def owner_state(self, owner, pid, overlay):
        current_pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(owner, ctypes.byref(current_pid))
        alive = self.exists(owner) and current_pid.value == pid
        if not alive:
            return {"alive": False}
        minimized = bool(self.user.IsIconic(owner))
        visible = bool(self.user.IsWindowVisible(owner))
        foreground = self.user.GetForegroundWindow()
        active = foreground in (owner, overlay) or self.user.GetAncestor(foreground, 3) == owner
        bounds = None
        if visible and not minimized:
            rect, origin = wintypes.RECT(), wintypes.POINT()
            if not self.user.GetClientRect(owner, ctypes.byref(rect)) or not self.user.ClientToScreen(owner, ctypes.byref(origin)):
                raise RuntimeError("无法读取 Claude 窗口范围")
            bounds = (origin.x, origin.y, origin.x+rect.right, origin.y+rect.bottom)
        return {"alive": True, "visible": visible, "minimized": minimized, "active": active, "bounds": bounds}

    def bind(self, overlay, owner):
        def set_long(index, value):
            ctypes.set_last_error(0)
            previous = self.user.SetWindowLongPtrW(overlay, index, value)
            if not previous and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
        # Owned top-level tool window: no cross-process SetParent or global topmost flag.
        set_long(-8, owner)
        style = self.user.GetWindowLongPtrW(overlay, -20)
        set_long(-20, (style & ~(0x8 | 0x40000 | 0x8000000)) | 0x80)
        if not self.user.SetWindowPos(overlay, -2, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x20):
            raise ctypes.WinError(ctypes.get_last_error())

    def rect(self, handle):
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(handle, ctypes.byref(rect)):
            raise RuntimeError("翻译浮框已经关闭")
        return rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top

    def place(self, handle, rect):
        if not self.user.SetWindowPos(handle, None, *rect, 0x4 | 0x10):
            raise RuntimeError("无法调整 Claude 翻译浮框位置")

    def show(self, handle, visible):
        if bool(self.user.IsWindowVisible(handle)) != visible:
            self.user.ShowWindow(handle, 4 if visible else 0)  # never activate another application


class ClaudeWindowAttachment:
    def __init__(self, owner, pid, overlay, on_closed, native=None):
        self.owner, self.pid, self.overlay = owner, pid, overlay
        self.on_closed = on_closed
        self.native = native or NativeWindows()
        self.layout = AttachedLayout()
        self._closed = threading.Event()
        self._lock = threading.RLock()

    def start(self):
        self.native.bind(self.overlay, self.owner)
        if not self.sync():
            raise RuntimeError("Claude 窗口已经关闭")
        threading.Thread(target=self._watch, name="claude-window", daemon=True).start()

    def sync(self):
        with self._lock:
            if self._closed.is_set():
                return False
            state = self.native.owner_state(self.owner, self.pid, self.overlay)
            # Claude's Close can hide its window in the tray without exiting the process.
            if not state["alive"] or (not state["visible"] and not state["minimized"]):
                self.native.show(self.overlay, False)
                return False
            if not self.native.exists(self.overlay):
                return False
            if state["minimized"]:
                self.native.show(self.overlay, False)
                return True
            actual = self.native.rect(self.overlay)
            target = self.layout.place(state["bounds"], actual)
            if target != actual:
                self.native.place(self.overlay, target)
            self.native.show(self.overlay, state["active"])
            return True

    def _watch(self):
        while not self._closed.wait(0.06):
            try:
                alive = self.sync()
            except Exception:
                # A failed owner query must never leave an unrestricted desktop overlay.
                self.native.show(self.overlay, False)
                alive = False
            if not alive:
                self._closed.set()
                self.on_closed()
                return

    def stop(self):
        self._closed.set()

