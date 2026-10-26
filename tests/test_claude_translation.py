"""Claude send/preview gates using fake accessibility objects; never sends a chat."""
import sys
import ctypes
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_translation import ClaudeTranslationSession, ClaudeNativeAdapter, ClaudeEnterHook


class ClaudeTranslationTests(unittest.TestCase):
    def session(self, translation="Translated draft"):
        adapter = mock.Mock()
        original = {"window": 123, "url": "https://claude.ai/new", "editor": (1, 2), "text": "请翻译这个草稿"}
        adapter.call.side_effect = lambda method, *args: dict(original) if method == "capture" else "written"
        translator = mock.Mock()
        translator.translate.return_value = [translation]
        session = ClaudeTranslationSession(adapter, translator)
        self.addCleanup(session.stop)
        return session, adapter, translator

    def settle(self, session):
        session._pool.submit(lambda: None).result(timeout=3)

    def test_first_enter_translates_second_enter_commits_once(self):
        session, adapter, translator = self.session()
        session._focus = {"window": 123, "text": "请翻译这个草稿"}
        session._focus_time = time.monotonic()
        self.assertTrue(session.enter())
        self.settle(session)
        self.assertEqual(session.state()["phase"], "preview")
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))
        self.assertTrue(session.enter())
        self.settle(session)
        commits = [call for call in adapter.call.call_args_list if call.args[0] == "commit"]
        self.assertEqual(len(commits), 1)
        self.assertTrue(commits[0].args[3])
        self.assertEqual(session.state()["phase"], "done")

    def test_english_enter_skips_translation_and_stale_focus_is_not_intercepted(self):
        session, adapter, translator = self.session()
        adapter.call.side_effect = lambda method, *args: {"window":123,"url":"https://claude.ai/new","editor":(1,),"text":"English input"} if method == "capture" else "sent"
        session._focus = {"window": 123, "text": "English input"}
        session._focus_time = time.monotonic()
        self.assertTrue(session.enter())
        self.settle(session)
        self.assertEqual(session.state()["phase"], "done")
        session._focus["text"] = "中文草稿"
        session._focus_time = time.monotonic() - 1
        self.assertFalse(session.enter())
        translator.translate.assert_not_called()

    def test_empty_translation_preserves_original_and_cannot_send(self):
        session, adapter, _ = self.session(" ")
        session.request("input")
        self.settle(session)
        self.assertEqual(session.state()["phase"], "error")
        self.assertEqual(session.state()["token"], "")
        with self.assertRaises(RuntimeError):
            session.confirm("", "draft", True)
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))

    def test_body_preview_never_writes_to_composer(self):
        session, adapter, translator = self.session("正文译文")
        session.request("body")
        self.settle(session)
        token = session.state()["token"]
        translator.translate.assert_called_once_with(["请翻译这个草稿"], "zh", scope="body")
        with self.assertRaises(RuntimeError):
            session.confirm(token, "正文译文", True)
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))

    def test_write_only_and_duplicate_confirmation_gate(self):
        session, adapter, _ = self.session()
        session.request("input")
        self.settle(session)
        token = session.state()["token"]
        session.confirm(token, "Edited translation", False)
        self.settle(session)
        commits = [call for call in adapter.call.call_args_list if call.args[0] == "commit"]
        self.assertEqual(commits[0].args[2:], ("Edited translation", False))
        with self.assertRaises(RuntimeError):
            session.confirm(token, "Edited translation", True)
        self.assertEqual(sum(call.args[0] == "commit" for call in adapter.call.call_args_list), 1)

    def test_direction_change_invalidates_old_confirmation(self):
        session, adapter, _ = self.session()
        session.request("input")
        self.settle(session)
        token = session.state()["token"]
        session.set_direction("en-zh")
        with self.assertRaises(RuntimeError):
            session.confirm(token, "old translation", True)
        self.assertEqual(session.state()["direction"], "en-zh")
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))

    def test_reverse_direction_translates_english_enter_to_chinese(self):
        session, adapter, translator = self.session("中文译文")
        adapter.call.side_effect = lambda method, *args: {"window":123,"url":"https://claude.ai/new","editor":(1,),"text":"Review this draft"} if method == "capture" else "sent"
        session.set_direction("en-zh")
        session._focus = {"window": 123}
        session._focus_time = time.monotonic()
        self.assertTrue(session.enter())
        self.settle(session)
        translator.translate.assert_called_once_with(["Review this draft"], "zh", scope="input")
        self.assertEqual(session.state()["phase"], "preview")
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))

    def test_collapse_invalidates_pending_send_without_writing(self):
        session, adapter, _ = self.session()
        session.request("input")
        self.settle(session)
        token = session.state()["token"]
        session.dismiss_preview()
        with self.assertRaises(RuntimeError):
            session.confirm(token, "translation", True)
        self.assertEqual(session.state()["phase"], "idle")
        self.assertEqual(session.state()["token"], "")
        self.assertFalse(any(call.args[0] == "commit" for call in adapter.call.call_args_list))

    def test_native_commit_rejects_changed_draft_before_write(self):
        native = object.__new__(ClaudeNativeAdapter)
        native._closed = threading.Event()
        editor = mock.Mock()
        original = {"window": 1, "url": "https://claude.ai/new", "editor": (1,), "text": "original"}
        current = {**original, "text": "user changed it"}
        native._input = mock.Mock(return_value=(current, editor, mock.Mock()))
        native._pattern = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "已变化"):
            native._commit(original, "translated", True)
        native._pattern.assert_not_called()

    def test_native_send_invokes_only_enabled_send_button(self):
        native = object.__new__(ClaudeNativeAdapter)
        native._closed = threading.Event()
        original = {"window": 1, "url": "https://claude.ai/new", "editor": (1,), "text": "original"}
        translated = {**original, "text": "translated"}
        native._input = mock.Mock(side_effect=[(original, "editor", "page"), (translated, "editor", "page"), (translated, "editor", "page")])
        native._value = mock.Mock(return_value="translated")
        value = mock.Mock()
        value.SetValue.return_value = True
        invoke = mock.Mock()
        native._pattern = mock.Mock(side_effect=[value, invoke])
        disabled = SimpleNamespace(ControlTypeName="ButtonControl", Name="Send message", IsEnabled=False, IsOffscreen=False)
        stop = SimpleNamespace(ControlTypeName="ButtonControl", Name="Stop", IsEnabled=True, IsOffscreen=False)
        send = SimpleNamespace(ControlTypeName="ButtonControl", Name="发送消息", IsEnabled=True, IsOffscreen=False)
        native._walk = mock.Mock(return_value=[disabled, stop, send])
        native._commit(original, "translated", True)
        self.assertEqual(native._pattern.call_args_list[1].args, (send, "InvokePattern"))
        invoke.Invoke.assert_called_once_with(waitTime=0)

    def test_native_readback_mismatch_prevents_send(self):
        native = object.__new__(ClaudeNativeAdapter)
        native._closed = threading.Event()
        original = {"window": 1, "url": "https://claude.ai/new", "editor": (1,), "text": "original"}
        native._input = mock.Mock(return_value=(original, "editor", "page"))
        native._pattern = mock.Mock(return_value=mock.Mock(SetValue=mock.Mock(return_value=True)))
        native._value = mock.Mock(return_value="old draft")
        native._walk = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "未确认译文"):
            native._commit(original, "translated", True)
        native._walk.assert_not_called()

    def run_hook(self, messages, modifier=False, composing=False, foreground=1):
        from ctypes import wintypes
        class Event(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]
        user, kernel, imm = mock.Mock(), mock.Mock(), mock.Mock()
        user.GetForegroundWindow.return_value = foreground
        user.GetAsyncKeyState.side_effect = lambda key: 0x8000 if modifier and key == 0x10 else 0
        user.CallNextHookEx.return_value = 42
        user.GetGUIThreadInfo.return_value = False
        if composing:
            def fill_gui(_, pointer):
                # HWND focus is the second handle after the two DWORD fields.
                offset = (2 * ctypes.sizeof(wintypes.DWORD) + ctypes.sizeof(ctypes.c_void_p) - 1) // ctypes.sizeof(ctypes.c_void_p) * ctypes.sizeof(ctypes.c_void_p)
                ctypes.c_void_p.from_address(ctypes.addressof(pointer._obj) + offset + ctypes.sizeof(ctypes.c_void_p)).value = 1
                return True
            user.GetGUIThreadInfo.side_effect = fill_gui
            imm.ImmGetContext.return_value = 1
            imm.ImmGetCompositionStringW.return_value = 8
        callback = None
        def register(_, handler, *__):
            nonlocal callback
            callback = handler
            return 1
        user.SetWindowsHookExW.side_effect = register
        iterator = iter(messages)
        results = []
        def pump(*_):
            try:
                message, flags = next(iterator)
            except StopIteration:
                return 0
            event = Event(13, 0, flags, 0, 0)
            results.append(callback(0, message, ctypes.addressof(event)))
            return 1
        user.GetMessageW.side_effect = pump
        enter = mock.Mock(return_value=True)
        hook = ClaudeEnterHook(lambda: {"window": 1, "text": "中文"}, enter)
        with mock.patch.object(ctypes, "WinDLL", side_effect=lambda name, **_: {"user32": user, "kernel32": kernel, "imm32": imm}[name], create=True), mock.patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
            hook._run()
        user.UnhookWindowsHookEx.assert_called_once()
        return results, enter

    def test_hook_holding_enter_does_not_confirm_or_send_again(self):
        results, enter = self.run_hook([(0x100, 0), (0x100, 0), (0x101, 0)])
        self.assertEqual(results, [1, 1, 1])
        enter.assert_called_once()

    def test_hook_preserves_shift_enter_and_other_apps(self):
        results, enter = self.run_hook([(0x100, 0), (0x101, 0)], modifier=True)
        self.assertEqual(results, [42, 42])
        enter.assert_not_called()
        results, enter = self.run_hook([(0x100, 0)], foreground=2)
        self.assertEqual(results, [42])
        enter.assert_not_called()

    def test_hook_preserves_ime_confirmation_and_injected_enter(self):
        results, enter = self.run_hook([(0x100, 0)], composing=True)
        self.assertEqual(results, [42])
        enter.assert_not_called()
        results, enter = self.run_hook([(0x100, 0x10)])
        self.assertEqual(results, [42])
        enter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
