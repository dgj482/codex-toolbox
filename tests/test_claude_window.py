"""Window lifecycle tests without manipulating the user's desktop."""
import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_window import AttachedLayout, ClaudeWindowAttachment, NativeWindows
from claude_translation import ClaudeNativeAdapter


class ClaudeWindowTests(unittest.TestCase):
    def attachment(self, **changes):
        native = mock.Mock()
        native.owner_state.return_value = {"alive":True,"visible":True,"minimized":False,
            "active":True,"bounds":(100,100,900,700), **changes}
        native.exists.return_value = True
        native.rect.return_value = (600,400,152,100)
        closed = mock.Mock()
        return ClaudeWindowAttachment(1, 10, 2, closed, native), native, closed

    def test_drag_is_clamped_to_claude_client_area(self):
        layout = AttachedLayout()
        self.assertEqual(layout.place((100,100,900,700), (-500,1000,152,100)), (108,592,152,100))

    def test_owner_move_and_resize_preserve_relative_position(self):
        layout = AttachedLayout()
        original = layout.place((100,100,900,700), (600,400,152,100))
        moved = layout.place((160,180,960,780), original)
        self.assertEqual(moved, (660,480,152,100))
        resized = layout.place((160,180,560,480), moved)
        self.assertGreaterEqual(resized[0],168)
        self.assertLessEqual(resized[0]+resized[2],552)
        self.assertLessEqual(resized[1]+resized[3],472)

    def test_preview_fits_owner_without_spilling_onto_desktop(self):
        rect = AttachedLayout().place((0,0,350,400), (290,300,420,500))
        self.assertEqual(rect, (8,8,334,384))

    def test_switching_apps_hides_without_stopping_and_return_shows(self):
        attachment, native, closed = self.attachment(active=False)
        self.assertTrue(attachment.sync())
        native.show.assert_called_with(2,False)
        native.owner_state.return_value["active"] = True
        self.assertTrue(attachment.sync())
        native.show.assert_called_with(2,True)
        closed.assert_not_called()

    def test_minimizing_hides_without_closing_and_restore_shows(self):
        attachment, native, closed = self.attachment(minimized=True,bounds=None)
        self.assertTrue(attachment.sync())
        native.rect.assert_not_called()
        native.show.assert_called_with(2,False)
        native.owner_state.return_value.update(minimized=False,bounds=(100,100,900,700))
        self.assertTrue(attachment.sync())
        native.show.assert_called_with(2,True)
        closed.assert_not_called()

    def test_close_to_tray_and_destroy_stop_the_attachment(self):
        for change in ({"visible":False},{"alive":False}):
            attachment, native, closed = self.attachment(**change)
            attachment._watch()
            native.show.assert_called_with(2,False)
            closed.assert_called_once_with()
            self.assertTrue(attachment._closed.is_set())

    def test_reused_owner_handle_with_a_different_pid_is_closed(self):
        native = object.__new__(NativeWindows)
        native.user = mock.Mock()
        native.user.IsWindow.return_value = True
        native.user.GetWindowThreadProcessId.side_effect = lambda handle, pointer: setattr(pointer._obj,"value",11)
        self.assertEqual(native.owner_state(1,10,2), {"alive":False})

    def test_closed_owner_cannot_retarget_another_claude_window(self):
        adapter = object.__new__(ClaudeNativeAdapter)
        adapter._handle, adapter._pid = 1, 10
        other = SimpleNamespace(NativeWindowHandle=2,ProcessId=11,IsOffscreen=False)
        auto = SimpleNamespace(GetRootControl=lambda:SimpleNamespace(GetChildren=lambda:[other]))
        user = mock.Mock()
        user.GetForegroundWindow.return_value = 2
        with mock.patch.dict(sys.modules,{"uiautomation":auto}), mock.patch.object(ctypes,"WinDLL",return_value=user,create=True), mock.patch("claude_translation._process_path",return_value="claude.exe"):
            with self.assertRaisesRegex(RuntimeError,"绑定的 Claude 窗口"):
                adapter._window()



if __name__ == "__main__":
    unittest.main()
