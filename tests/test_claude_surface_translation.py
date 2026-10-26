"""Real loopback protocol checks, without Claude automation or upstream calls."""
import json
from pathlib import Path
import sys
import shutil
import subprocess
import unittest
import threading
import time
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_surface_translation import ClaudePageTranslationBridge


class Translator:
    api_key = "test-upstream-secret"

    def __init__(self):
        self.calls = []

    def translate(self, texts, target, scope):
        self.calls.append((texts, target, scope))
        return ["translated:" + text for text in texts]


class ClaudePageBridgeTests(unittest.TestCase):
    def setUp(self):
        self.translator = Translator()
        self.bridge = ClaudePageTranslationBridge(self.translator,
            Path(__file__).resolve().parents[1] / "experiments" / "codex_translation_overlay.js")
        self.bridge.start()
        self.addCleanup(self.bridge.stop)

    def request(self, path, payload=None, token=None, origin="https://claude.ai"):
        token = self.bridge.token if token is None else token
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.bridge.base_url + path + "?token=" + token,
            data=data, headers={"Origin": origin, "Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=3)

    def test_wrong_token_and_origin_cannot_translate(self):
        payload = {"texts": ["draft"], "target": "en", "scope": "input"}
        for kwargs in ({"token": "wrong"}, {"origin": "https://other.example"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/translate", payload, **kwargs)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        self.assertEqual(self.translator.calls, [])

    def test_valid_request_reaches_translator_with_exact_scope(self):
        with self.request("/translate", {"texts": ["测试草稿"], "target": "en", "scope": "input"}) as response:
            self.assertEqual(json.load(response), {"ok": True, "texts": ["translated:测试草稿"]})
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://claude.ai")
        self.assertEqual(self.translator.calls, [(["测试草稿"], "en", "input")])

    def test_invalid_requests_never_reach_translator(self):
        for payload in ([], {"texts": ["x"]}, {"texts": ["x"] * 9, "target": "en", "scope": "input"},
                        {"texts": ["x"], "target": "en", "scope": "execute"},
                        {"texts": ["x" * 16001], "target": "en", "scope": "input"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/translate", payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(self.translator.calls, [])

    def test_error_response_does_not_expose_upstream_secret(self):
        def fail(*_):
            raise RuntimeError("failure " + self.translator.api_key)
        self.translator.translate = fail
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/translate", {"texts": ["x"], "target": "en", "scope": "input"})
        data = caught.exception.read().decode()
        self.assertNotIn(self.translator.api_key, data)
        caught.exception.close()

    def test_service_is_not_marked_attached_before_page_acknowledges(self):
        self.assertFalse(self.bridge.state()["attached"])
        with self.request("/attached", {}) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertTrue(self.bridge.wait_attached(0))

    def test_page_unload_clears_attachment_and_can_acknowledge_a_new_page(self):
        with self.request("/attached", {}) as response:
            json.load(response)
        with self.request("/detached", {}) as response:
            json.load(response)
        self.assertFalse(self.bridge.state()["attached"])
        self.assertTrue(self.bridge.state()["running"])
        with self.request("/attached", {}) as response:
            json.load(response)
        self.assertTrue(self.bridge.state()["attached"])

    def test_detached_page_reconnects_and_closed_owner_stops_service(self):
        alive = threading.Event()
        alive.set()
        reconnected = threading.Event()
        def reconnect():
            with self.request("/attached", {}) as response:
                json.load(response)
            reconnected.set()
        self.bridge.bind_owner(alive.is_set, reconnect)
        self.assertTrue(reconnected.wait(2))
        self.assertTrue(self.bridge.state()["attached"])
        reconnected.clear()
        with self.request("/detached", {}) as response:
            json.load(response)
        self.assertTrue(reconnected.wait(4))
        alive.clear()
        deadline = time.monotonic() + 3
        while self.bridge.state()["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.bridge.state()["running"])
        self.assertFalse(self.bridge.state()["attached"])

    def test_stop_during_attach_remains_stopped_and_state_does_not_block(self):
        import codex_app
        api = codex_app.Api()
        entered, release = threading.Event(), threading.Event()
        result = []
        def attach():
            entered.set()
            release.wait(3)
        with mock.patch("claude_page_loader.check_developer_permissions"), mock.patch("claude_page_loader.ClaudePageLoader.attach", side_effect=attach), mock.patch.object(api, "_create_surface_translator", return_value=self.translator):
            thread = threading.Thread(target=lambda: result.append(api.open_claude_translation({})))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(api.get_claude_translation_state()["phase"], "starting")
                api.stop_claude_translation()
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0]["ok"])
        self.assertEqual(api.get_claude_translation_state()["phase"], "stopped")

    def test_bootstrap_reuses_shared_engine_without_upstream_secret(self):
        with self.request("/bootstrap") as response:
            source = response.read().decode()
        self.assertNotIn(self.translator.api_key, source)
        self.assertNotIn("__CODEX_TOOLBOX_CONFIG__", source)
        self.assertTrue(self.bridge.config["translateMessages"])
        self.assertIn('[data-testid="assistant-message"] [data-perf-reply-text]', self.bridge.config["assistantSelector"])
        self.assertIn('[data-testid="user-message"]', self.bridge.config["messageSelector"])
        self.assertIn("pagehide", source)
        self.assertIn("window.__codexToolboxSurfaceTranslation?.destroy?.()", source)
        self.assertIn('"appName": "Claude"', source)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for JavaScript syntax validation")
    def test_generated_script_and_console_command_have_valid_javascript(self):
        for source in (self.bridge.page_script(), self.bridge.bootstrap_command()):
            result = subprocess.run(["node", "--check", "-"], input=source,
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
