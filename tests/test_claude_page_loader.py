"""Permission checks only; never changes Claude's security preferences."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_page_loader import check_developer_permissions


class ClaudeLoaderPermissionsTests(unittest.TestCase):
    def test_human_enabled_permissions_are_required_and_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Developer Mode"):
                check_developer_permissions(root)
            self.assertEqual(list(root.iterdir()), [])
            developer = root / "developer_settings.json"
            developer.write_text(json.dumps({"allowDevTools": True}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "allow pasting"):
                check_developer_permissions(root)
            prefs = root / "Preferences"
            data = {"electron": {"devtools": {"preferences": {"disable-self-xss-warning": "true"}}}}
            prefs.write_text(json.dumps(data), encoding="utf-8")
            before = (developer.read_bytes(), prefs.read_bytes())
            check_developer_permissions(root)
            self.assertEqual(before, (developer.read_bytes(), prefs.read_bytes()))


class ClaudeStaleControlTests(unittest.TestCase):
    class StaleElementError(Exception):
        hresult = -2147220991

    def test_stale_reference_is_reacquired(self):
        from claude_page_loader import _retry_uia_lookup
        read = mock.Mock(side_effect=[self.StaleElementError(), "fresh-control"])
        with mock.patch("claude_page_loader.time.sleep"):
            self.assertEqual(_retry_uia_lookup(read, float("inf"), lambda: False), "fresh-control")
        self.assertEqual(read.call_count, 2)

    def test_persistent_stale_reference_stops_at_deadline(self):
        from claude_page_loader import _retry_uia_lookup
        with self.assertRaisesRegex(RuntimeError, "Claude"):
            _retry_uia_lookup(mock.Mock(side_effect=self.StaleElementError()), 0, lambda: False)

    def test_other_errors_and_cancellation_are_not_retried(self):
        from claude_page_loader import _retry_uia_lookup
        operation = mock.Mock(side_effect=ValueError("other failure"))
        with self.assertRaises(ValueError):
            _retry_uia_lookup(operation, float("inf"), lambda: False)
        self.assertEqual(operation.call_count, 1)
        operation.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "取消"):
            _retry_uia_lookup(operation, float("inf"), lambda: True)
        operation.assert_not_called()


class ClaudeFocusTests(unittest.TestCase):
    def test_actual_focus_wins_over_false_activation_return(self):
        from claude_page_loader import _focus_editor
        window = mock.Mock()
        window.SetActive.return_value = False
        editor = mock.Mock(HasKeyboardFocus=True)
        user = mock.Mock()
        user.GetForegroundWindow.return_value = 123
        _focus_editor(window, editor, user, 123)
        editor.SetFocus.assert_called_once()

    def test_editor_found_after_activation_does_not_reactivate_window(self):
        from claude_page_loader import _focus_editor
        window = mock.Mock()
        user = mock.Mock()
        user.GetForegroundWindow.return_value = 123
        editor = mock.Mock(HasKeyboardFocus=True)
        _focus_editor(window, editor, user, 123, activate=False)
        window.SetActive.assert_not_called()
        window.SetFocus.assert_not_called()
        editor.SetFocus.assert_called_once()

    def test_other_foreground_window_cancels_attachment(self):
        from claude_page_loader import _focus_editor
        user = mock.Mock()
        user.GetForegroundWindow.return_value = 999
        with self.assertRaisesRegex(RuntimeError, "Claude"):
            _focus_editor(mock.Mock(), mock.Mock(HasKeyboardFocus=True), user, 123)


class ClaudeHubLaunchTests(unittest.TestCase):
    def test_launch_then_attach_with_current_settings(self):
        import codex_app
        api = codex_app.Api()
        api._claude_channels = mock.Mock()
        api._claude_channels.list.return_value = {"current_id": "claude-official"}
        api._trace_claude_launch = mock.Mock()
        events = []
        options = {"provider_id": "test-provider", "translate_input": True}
        with mock.patch("claude_page_loader.check_developer_permissions", side_effect=lambda: events.append("permissions")), mock.patch("claude_page_loader.launch_claude", side_effect=lambda: events.append("launch")), mock.patch.object(api, "open_claude_translation", side_effect=lambda data: events.append(("attach", data)) or {"ok": True}):
            result = api.launch_claude_hub(options)
        self.assertTrue(result["ok"])
        self.assertEqual(events, ["launch", ("attach", options)])

    def test_permission_failure_still_opens_claude_and_reports_translation_error(self):
        import codex_app
        api = codex_app.Api()
        api._claude_channels = mock.Mock()
        api._claude_channels.list.return_value = {"current_id": "claude-official"}
        api._trace_claude_launch = mock.Mock()
        with mock.patch("claude_page_loader.check_developer_permissions", side_effect=RuntimeError("Developer Mode")), mock.patch("claude_page_loader.launch_claude") as launch:
            result = api.launch_claude_hub({})
        self.assertFalse(result["ok"])
        self.assertEqual(api.get_claude_translation_state()["phase"], "error")
        self.assertIn("Developer Mode", api.get_claude_translation_state()["message"])
        launch.assert_called_once()
        self.assertIn("Claude 已打开", result["error"])

    def test_attach_failure_preserves_launch_result(self):
        import codex_app
        api = codex_app.Api()
        api._claude_channels = mock.Mock()
        api._claude_channels.list.return_value = {"current_id": "claude-official"}
        api._trace_claude_launch = mock.Mock()
        with mock.patch("claude_page_loader.check_developer_permissions"), mock.patch("claude_page_loader.launch_claude"), mock.patch.object(api, "open_claude_translation", return_value={"ok": False, "error": "page timeout"}):
            result = api.launch_claude_hub({})
        self.assertFalse(result["ok"])
        self.assertIn("page timeout", result["error"])


if __name__ == "__main__":
    unittest.main()
