# -*- coding: utf-8 -*-
"""表层翻译回归测试；不启动或修改真实 Codex。"""
import json
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codex_app
from codex_surface_translation import (
    OpenAICompatibleTranslator,
    SurfaceTranslationController,
    _extract_output,
    _parse_translation_array,
    protect_technical_text,
    restore_technical_text,
    select_codex_page_target,
)


class SurfaceTranslationTests(unittest.TestCase):
    def test_background_and_translation_share_one_debug_port(self):
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "codex_app.py").read_text(encoding="utf-8")
        ui_source = (root / "ui.html").read_text(encoding="utf-8")
        launcher_source = (root / "experiments" / "codex_translation_launcher.ps1").read_text(encoding="utf-8")

        self.assertIn("CODEX_EXPERIMENT_PORT = 9229", app_source)
        self.assertIn('"-Port", str(CODEX_EXPERIMENT_PORT)', app_source)
        self.assertIn("port: 9229", ui_source)
        self.assertIn("[int]$Port = 9229", launcher_source)
        self.assertNotIn('"port": 9230', app_source)
        self.assertNotIn("port: 9230", ui_source)
        self.assertNotIn("[int]$Port = 9230", launcher_source)

    def test_translation_launcher_has_valid_powershell_syntax(self):
        launcher = Path(__file__).resolve().parents[1] / "experiments" / "codex_translation_launcher.ps1"
        command = (
            "$tokens=$null; $errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors); "
            "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cdp_target_list_selects_only_main_codex_page(self):
        payload = [
            {"type": "webview", "url": "codex-sandbox://task", "webSocketDebuggerUrl": "ws://sandbox"},
            {"type": "page", "url": "app://-/index.html?initialRoute=%2Favatar-overlay", "webSocketDebuggerUrl": "ws://avatar"},
            {"type": "page", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://main"},
        ]
        self.assertEqual(select_codex_page_target(payload)["webSocketDebuggerUrl"], "ws://main")
        self.assertEqual(select_codex_page_target(payload[2])["webSocketDebuggerUrl"], "ws://main")

    def test_launcher_serializes_one_target_object(self):
        source = (Path(__file__).resolve().parents[1] / "experiments" / "codex_translation_launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("foreach ($item in $response)", source)
        self.assertIn("$target = @($target)[0]", source)
        self.assertIn("ConvertTo-Json -Compress -Depth 6", source)

    def test_codex_launchers_clear_frozen_toolbox_runtime_environment(self):
        root = Path(__file__).resolve().parents[1] / "experiments"
        for name in ("codex_translation_launcher.ps1", "codex_background_injector.ps1"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn("$_.Name -like '_PYI_*'", source)
            self.assertIn("Remove-Item Env:PYINSTALLER_RESET_ENVIRONMENT", source)

    def test_protect_and_restore_technical_fragments(self):
        source = r"请检查 `git status`、https://example.test/a、D:\work\app.py 和 @codex。"
        masked, protected = protect_technical_text(source)

        self.assertNotIn("git status", masked)
        self.assertNotIn("https://example.test/a", masked)
        self.assertEqual(restore_technical_text(masked, protected), source)

    def test_model_names_and_terms_survive_translation(self):
        source = "Use GPT-6.1 sol, gpt-6-astra and qwen-mt-flash as the Model; compare models."
        masked, protected = protect_technical_text(source)
        for value in ("GPT-6.1 sol", "gpt-6-astra", "qwen-mt-flash", "Model", "models"):
            self.assertIn(value, protected)
            self.assertNotIn(value, masked)
        self.assertIn("compare", masked)
        self.assertEqual(restore_technical_text(masked, protected), source)

    def test_translation_array_accepts_fenced_json(self):
        self.assertEqual(_parse_translation_array('```json\n["设置", "发送"]\n```', 2), ["设置", "发送"])

    def test_responses_text_blocks_are_combined(self):
        self.assertEqual(
            _extract_output({"output": [{"type": "message", "content": [
                {"type": "output_text", "text": '["Hel'},
                {"type": "output_text", "text": 'lo"]'},
            ]}]}),
            '["Hello"]',
        )

    def test_missing_protected_marker_rejects_translation(self):
        translator = OpenAICompatibleTranslator({"base_url": "http://127.0.0.1:1/v1", "model": "mock"})
        with mock.patch.object(translator, "_request", return_value='["Run the command"]'):
            with self.assertRaisesRegex(RuntimeError, "受保护"):
                translator.translate(["运行 `git status`"], "en", scope="input")

    def test_proxy_provider_uses_remote_upstream_directly(self):
        self.assertEqual(json.loads(_extract_output(["设置", "发送"])), ["设置", "发送"])
        self.assertEqual(
            _extract_output({"choices": [{"message": {"content": [{"text": "ok"}]}}]}),
            "ok",
        )

        translator = OpenAICompatibleTranslator({
            "proxy": True,
            "base_url": "http://127.0.0.1:19999/v1",
            "upstream_base": "https://api.example.test/v1",
            "api_key": "secret",
            "model": "translator",
        })
        self.assertEqual(translator.base_url, "https://api.example.test/v1")

    def test_responses_request_keeps_secret_out_of_prompt_result(self):
        class Response:
            def read(self):
                return json.dumps({"output_text": '["Check [[CTB_KEEP_0]]"]'}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        translator = OpenAICompatibleTranslator({
            "base_url": "https://api.example.test/v1",
            "api_key": "secret-token",
            "model": "translator",
            "wire_api": "responses",
        })
        with mock.patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            result = translator.translate(["检查 `git status`"], "en", scope="interface")

        self.assertEqual(result, ["Check `git status`"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/responses")
        self.assertNotIn(b"secret-token", request.data)
        repeated = "检查 " + chr(96) + "git status" + chr(96)
        self.assertEqual(
            translator.translate([repeated], "en", scope="interface"),
            ["Check " + chr(96) + "git status" + chr(96)],
        )
        self.assertEqual(urlopen.call_count, 1)

    def test_dashscope_translation_prefers_faster_chat_completions_protocol(self):
        class Response:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '["中文"]'}}]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-flash",
            "wire_api": "responses",
        })
        with mock.patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            self.assertEqual(translator.translate(["English"], "zh-CN"), ["中文"])

        self.assertEqual(urlopen.call_args.args[0].full_url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    def test_qwen_mt_uses_native_single_text_contract(self):
        class Response:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "中文译文"}}]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-mt-flash",
            "wire_api": "responses",
        })
        with mock.patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            self.assertEqual(translator.translate(["English text"], "zh-CN"), ["中文译文"])

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(body["messages"], [{"role": "user", "content": "English text"}])
        self.assertEqual(body["translation_options"], {"source_lang": "auto", "target_lang": "Chinese"})
        self.assertNotIn("temperature", body)

    def test_qwen_mt_packs_multiple_items_into_one_native_request(self):
        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-mt-flash",
        })
        translator._request_qwen_mt = mock.Mock(
            return_value="[[CTB_SEG_0]]\n中:one\n[[CTB_SEG_1]]\n中:two"
        )

        self.assertEqual(translator.translate(["one", "two"], "zh-CN"), ["中:one", "中:two"])
        translator._request_qwen_mt.assert_called_once_with(
            "[[CTB_SEG_0]]\none\n[[CTB_SEG_1]]\ntwo", "zh-CN", mock.ANY
        )

    def test_qwen_mt_pack_falls_back_when_segment_markers_are_changed(self):
        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-mt-flash",
        })
        translator._request_qwen_mt = mock.Mock(side_effect=["markers lost", "中:one", "中:two"])

        self.assertEqual(translator.translate(["one", "two"], "zh-CN"), ["中:one", "中:two"])
        self.assertEqual(translator._request_qwen_mt.call_count, 3)

    def test_qwen_mt_honors_retry_after(self):
        class Response:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "中文"}}]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-mt-flash",
        })
        rate_limit = urllib.error.HTTPError(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            429,
            "Too Many Requests",
            {"Retry-After": "2"},
            io.BytesIO(b'{"error":{"message":"rate limited"}}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=[rate_limit, Response()]), \
                mock.patch("time.sleep") as sleep:
            self.assertEqual(translator.translate(["English"], "zh-CN"), ["中文"])
        sleep.assert_called_once_with(2.0)

    def test_ui_uses_non_blocking_provider_diagnosis_and_translation_logging(self):
        root = Path(__file__).resolve().parents[1]
        ui_source = (root / "ui.html").read_text(encoding="utf-8")
        app_source = (root / "codex_app.py").read_text(encoding="utf-8")
        self.assertNotIn("alert(", ui_source)
        self.assertIn("surface_translation_errors.log", app_source)
        self.assertIn("self.on_error(str(error))", (root / "codex_surface_translation.py").read_text(encoding="utf-8"))

    def test_translation_failure_is_written_to_rotating_log(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(codex_app.core, "_app_dir", return_value=td):
            api = codex_app.Api()
            api._translation_failed("HTTP 429\nToo Many Requests")
            log = Path(td) / "surface_translation_errors.log"
            content = log.read_text(encoding="utf-8")
        self.assertIn("HTTP 429 Too Many Requests", content)
        self.assertEqual(api._translation_last_error, "HTTP 429 Too Many Requests")

    def test_existing_codex_debug_port_requires_live_main_page(self):
        target = [{"type": "page", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://127.0.0.1:37266/page"}]
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "codex_debug_port").write_text("37266", encoding="utf-8")
            with mock.patch.object(codex_app.core, "_app_dir", return_value=td):
                with mock.patch("codex_app.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(target).encode())) as open_url:
                    self.assertEqual(codex_app.Api._existing_codex_debug_port(), 37266)
        self.assertIn(":37266/json/list", open_url.call_args.args[0])

    def test_saved_translation_reattaches_without_restarting_codex(self):
        api = codex_app.Api()
        settings = {"provider_id": "provider", "translate_input": True}
        with mock.patch.object(api, "_load_community_experiments", return_value={"surface_translation": True}), \
                mock.patch.object(api, "_translation_settings_file", return_value="settings.json"), \
                mock.patch("codex_app.os.path.isfile", return_value=True), \
                mock.patch.object(api, "_existing_codex_debug_port", return_value=37266), \
                mock.patch.object(api, "_load_translation_settings", return_value=settings), \
                mock.patch.object(api, "apply_surface_translation", return_value={"ok": True}) as attach:
            api.resume_saved_surface_translation()
        attach.assert_called_once_with(settings, restart=False)

    def test_transient_translation_network_failure_is_retried(self):
        class Response:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '["中文"]'}}]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-flash",
            "wire_api": "responses",
        })
        with mock.patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("SSL EOF"), Response()]) as urlopen, \
                mock.patch("time.sleep") as sleep:
            self.assertEqual(translator.translate(["English"], "zh-CN"), ["中文"])

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_only_interface_translations_persist_across_controllers(self):
        provider = {
            "base_url": "https://api.example.test/v1",
            "api_key": "secret-token",
            "model": "translator",
            "wire_api": "responses",
        }
        with tempfile.TemporaryDirectory() as td:
            cache_path = str(Path(td) / "translation-cache.sqlite")
            first = OpenAICompatibleTranslator(provider, persistent_cache_path=cache_path)
            first._request = mock.Mock(return_value='["设置"]')
            self.assertEqual(first.translate(["Settings"], "zh-CN", scope="interface"), ["设置"])

            second = OpenAICompatibleTranslator(provider, persistent_cache_path=cache_path)
            second._request = mock.Mock(side_effect=AssertionError("persistent cache was not used"))
            self.assertEqual(second.translate(["Settings"], "zh-CN", scope="interface"), ["设置"])
            second._request.assert_not_called()

            connection = sqlite3.connect(cache_path)
            try:
                count = connection.execute("SELECT COUNT(*) FROM interface_translations").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1)

    def test_body_and_input_translations_are_not_persisted(self):
        provider = {
            "base_url": "https://api.example.test/v1",
            "api_key": "secret-token",
            "model": "translator",
        }
        with tempfile.TemporaryDirectory() as td:
            translator = OpenAICompatibleTranslator(
                provider, persistent_cache_path=str(Path(td) / "translation-cache.sqlite")
            )
            translator._request = mock.Mock(side_effect=['["正文"]', '["正文"]', '["Prompt"]', '["Prompt"]'])
            self.assertEqual(translator.translate(["Body"], "zh-CN", scope="body"), ["正文"])
            self.assertEqual(translator.translate(["Body"], "zh-CN", scope="body"), ["正文"])
            self.assertEqual(translator.translate(["提示词"], "en", scope="input"), ["Prompt"])
            self.assertEqual(translator.translate(["提示词"], "en", scope="input"), ["Prompt"])
            self.assertEqual(translator._request.call_count, 4)

    def test_overlay_waits_until_page_body_exists(self):
        controller = SurfaceTranslationController(
            "ws://example.test", "window.testConfig=__CODEX_TOOLBOX_CONFIG__", {"translateInterface": True}, None
        )
        expression = controller._bootstrap_expression()
        self.assertIn("DOMContentLoaded", expression)
        self.assertIn('"translateInterface":true', expression)

    def test_surface_translation_is_reversible_and_scoped(self):
        source = (Path(__file__).resolve().parents[1] / "experiments" / "codex_translation_overlay.js").read_text(encoding="utf-8")
        for required in (
            "codex-toolbox-translation-layer",
            "showTranslations:false",
            "pointer-events:none",
            "root.remove()",
            "replacements:new Map()",
            "removeReplacement(node,entry)",
            "node.nodeValue=entry.translatedRaw",
            '[data-markdown-text-style="assistant-message"]',
            '[data-markdown-text-tone="user-message"]',
            "assistantBodyTextNodes()",
            "state.cache.get('body:zh:'+original)",
            "bodyTextIsStable(node,original)",
            "BODY_TRANSLATION_CONCURRENCY=3",
            "BATCH_MAX_ITEMS=8",
            "BATCH_MAX_CHARACTERS=800",
            "CACHE_LIMIT=4000",
            "while(state.bodyRequestsRunning<BODY_TRANSLATION_CONCURRENCY",
            "state.bodyRequestsRunning++",
            "state.bodyRequestsRunning--",
            "syncPlaceholderReplacements(generation)",
            "new Option('中→英','zh-en')",
            "new Option('英→中','en-zh')",
            "direction==='en-zh'?'zh-CN':'en'",
            "translateButton.addEventListener('click'",
            "function inputTranslationShortcut(event)",
            "document.addEventListener('keydown',inputTranslationShortcut,true)",
            "document.removeEventListener('keydown',inputTranslationShortcut,true)",
            "state.enterBypassText=original",
            "request([original],target,'input')",
            "request(batch,'zh-CN',kind)",
            "function showTranslationNotice(message,kind='error')",
        ):
            self.assertIn(required, source)
        for forbidden in ("showTranslations:true", "translateButton.addEventListener('pointerup'", "alert(", "api_key"):
            self.assertNotIn(forbidden, source)

    def test_translation_request_deadline_stays_below_page_timeout(self):
        translator = OpenAICompatibleTranslator({
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret-token",
            "model": "qwen-mt-flash",
        })
        self.assertLess(translator.REQUEST_DEADLINE_SECONDS, 50)
        with mock.patch("codex_surface_translation.time.monotonic", return_value=100.0):
            self.assertEqual(translator._attempt_timeout(145.0), translator.ATTEMPT_TIMEOUT_SECONDS)
            with self.assertRaises(TimeoutError):
                translator._attempt_timeout(99.0)

    def test_translation_bridge_rejects_long_input_without_truncating(self):
        translator = mock.Mock()
        errors = []
        controller = SurfaceTranslationController("ws://127.0.0.1:9229/devtools/page/old", "", {}, translator, errors.append)
        sent = []
        controller._send_evaluate = sent.append
        controller._handle_request({"id": "long", "texts": ["中" * 6001], "target": "en", "scope": "input"})
        self.assertFalse(translator.translate.called)
        self.assertIn("原文未被截断", errors[0])
        self.assertIn('"ok":false', sent[0])
        controller.stop()

    def test_translation_bridge_reconnects_to_new_main_page(self):
        class FakeSocket:
            def __init__(self):
                self.last_id = 0

            def send(self, payload):
                self.last_id = json.loads(payload)["id"]

            def recv(self):
                return json.dumps({"id": self.last_id, "result": {"identifier": "new-script"}})

            def settimeout(self, _value):
                pass

            def close(self):
                pass

        controller = SurfaceTranslationController("ws://127.0.0.1:9229/devtools/page/old", "", {}, None)
        payload = [{"type": "page", "url": "app://-/index.html", "webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/page/new"}]
        with mock.patch("codex_surface_translation.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            with mock.patch("websocket.create_connection", return_value=FakeSocket()):
                self.assertTrue(controller._reconnect())
        self.assertTrue(controller.is_connected)
        self.assertTrue(controller.websocket_url.endswith("/new"))
        controller.stop()


    def test_ui_has_one_central_launcher_and_hides_disabled_modules(self):
        source = (Path(__file__).resolve().parents[1] / "ui.html").read_text(encoding="utf-8")
        self.assertEqual(source.count('onclick="launchCodexHub()"'), 1)
        self.assertIn('id="tab-home"', source)
        self.assertIn('data-feature-toggle="channel_switching"', source)
        self.assertIn('data-feature-toggle="session_routing"', source)
        self.assertIn('data-feature-toggle="session_archive"', source)
        self.assertIn('data-feature-toggle="local_appearance"', source)
        self.assertIn('data-feature-toggle="surface_translation"', source)
        self.assertIn('data-feature-toggle="diagnostics"', source)
        self.assertIn("button.hidden = !communityExperiments[button.dataset.feature]", source)
        self.assertNotIn("launchCodexWithSavedBackground", source)
        self.assertNotIn("startLiveBackgroundPreview", source)
        self.assertNotIn("applySurfaceTranslation(true)", source)
        self.assertNotIn("function applyBackground(", source)


if __name__ == "__main__":
    unittest.main()
