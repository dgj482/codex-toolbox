"""Page lifecycle and settings regressions without controlling real windows."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_app import Api


class Bridge:
    created = []

    def __init__(self, translator, overlay, **options):
        self.options = options
        self.running = self.attached = False
        self.stopped = False
        self.created.append(self)

    def start(self):
        self.running = True

    def state(self):
        return {"running": self.running, "attached": self.attached,
                "translate_messages": self.options["translate_messages"]}

    def stop(self):
        self.stopped = True
        self.running = self.attached = False


class Loader:
    calls = []
    failure = False

    def __init__(self, bridge, window, on_stage=None):
        self.bridge, self.window = bridge, window

    def attach(self):
        self.calls.append(self.window)
        if self.failure:
            raise RuntimeError("page did not acknowledge")
        self.bridge.attached = True


@unittest.skipUnless(sys.platform == "win32", "Claude Desktop translation supports Windows only")
class ClaudePageApiTests(unittest.TestCase):
    def setUp(self):
        Bridge.created = []
        Loader.calls, Loader.failure = [], False
        self.api = Api()
        self.window = object()
        self.api.set_window(self.window)
        self.api._create_surface_translator = mock.Mock(return_value=object())
        for target, value in (("claude_surface_translation.ClaudePageTranslationBridge", Bridge),
                              ("claude_page_loader.ClaudePageLoader", Loader)):
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch("claude_page_loader.check_developer_permissions")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.api.stop_claude_translation)

    def test_start_uses_page_loader_and_selected_ranges_without_a_companion_window(self):
        with mock.patch("codex_app.webview.create_window") as create_window:
            result = self.api.open_claude_translation({"translate_interface": False, "translate_messages": False,
                                                     "translate_input": True, "intercept_enter": False})
        self.assertTrue(result["ok"])
        create_window.assert_not_called()
        self.assertEqual(Loader.calls, [self.window])
        self.assertEqual(Bridge.created[0].options, {"translate_interface": False, "translate_messages": False,
                                                   "translate_input": True, "intercept_enter": False})

    def test_repeated_start_does_not_reload_an_attached_page(self):
        self.api.open_claude_translation({})
        self.api.open_claude_translation({})
        self.assertEqual(len(Loader.calls), 1)

    def test_refreshed_page_reconnects_instead_of_reporting_false_success(self):
        self.api.open_claude_translation({})
        previous = Bridge.created[0]
        previous.attached = False
        self.assertEqual(self.api.get_claude_translation_state()["phase"], "disconnected")
        self.assertTrue(self.api.open_claude_translation({})["ok"])
        self.assertTrue(previous.stopped)
        self.assertEqual(len(Loader.calls), 2)

    def test_changed_ranges_replace_the_old_bridge(self):
        self.api.open_claude_translation({})
        previous = Bridge.created[0]
        self.api.open_claude_translation({"translate_input": False})
        self.assertTrue(previous.stopped)
        self.assertFalse(Bridge.created[-1].options["translate_input"])

    def test_failed_attachment_closes_the_server_and_is_not_published_as_running(self):
        Loader.failure = True
        self.assertFalse(self.api.open_claude_translation({})["ok"])
        self.assertTrue(Bridge.created[0].stopped)
        self.assertFalse(self.api.get_claude_translation_state()["running"])
        self.assertEqual(self.api.get_claude_translation_state()["phase"], "error")


if __name__ == "__main__":
    unittest.main()
