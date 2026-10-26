"""Provider persistence, native profile transactions and loopback compatibility checks."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_channels as channels


@unittest.skipUnless(os.name == "nt", "Claude Desktop channels currently support Windows")
class ClaudeChannelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = channels.ClaudeChannels(self.root / "toolbox", self.root / "local")
        patch = mock.patch("claude_channels.secret_store.protect_secret", side_effect=lambda key: "protected:" + key[::-1])
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch("claude_channels.secret_store.unprotect_secret", side_effect=lambda key: key.removeprefix("protected:")[::-1])
        patch.start()
        self.addCleanup(patch.stop)
        self.provider = self.store.save({"name": "Gateway", "base_url": "https://gateway.example/v1", "api_key": "unit-key",
            "auth_scheme": "x-api-key", "models": "claude-sonnet-test"})

    def test_save_redacts_secret_and_edit_preserves_key(self):
        self.assertNotIn("unit-key", self.store.file.read_text(encoding="utf-8"))
        public = self.store.list()
        self.assertNotIn("api_key_dpapi", json.dumps(public))
        self.assertNotIn("unit-key", json.dumps(public))
        self.store.save({"id": self.provider, "name": "Renamed", "base_url": "https://gateway.example/v1", "models": "claude-sonnet-test"})
        self.assertEqual(self.store.secret(self.provider), "unit-key")

    def test_native_profile_keeps_secret_in_helper_not_json(self):
        with mock.patch("claude_channels.helper_command", return_value=("C:/Toolbox.exe", ["--claude-secret", self.provider])):
            backup = self.store.apply(self.provider)
        profile = channels.read_object(self.store.profile)
        self.assertEqual(profile["inferenceCredentialKind"], "helper-script")
        self.assertNotIn("inferenceGatewayApiKey", profile)
        self.assertNotIn("unit-key", self.store.profile.read_text(encoding="utf-8"))
        self.assertEqual(profile["inferenceCredentialHelperArgs"], ["--claude-secret", self.provider])
        self.assertTrue((Path(backup) / "manifest.json").exists())
        self.assertEqual(self.store.list()["current_id"], self.provider)

    def test_switch_and_restore_preserve_unrelated_config_and_profiles(self):
        for config in self.store.configs:
            channels.write_object(config, {"deploymentMode": "1p", "preferences": {"language": "en-US"}, "mcpServers": {"keep": {}}})
        channels.write_object(self.store.meta, {"appliedId": "external", "entries": [{"id": "external", "name": "Existing"}], "unknown": 1})
        self.store.apply(self.provider)
        for config in self.store.configs:
            value = channels.read_object(config)
            self.assertEqual(value["deploymentMode"], "3p")
            self.assertEqual(value["preferences"], {"language": "en-US"})
            self.assertIn("keep", value["mcpServers"])
        self.store.apply(channels.OFFICIAL)
        self.assertEqual(self.store.list()["current_id"], channels.OFFICIAL)
        meta = channels.read_object(self.store.meta)
        self.assertEqual(meta["entries"], [{"id": "external", "name": "Existing"}])
        self.assertEqual(meta["unknown"], 1)
        self.assertEqual(self.store.secret(self.provider), "unit-key")

    def test_bad_native_json_is_not_replaced(self):
        path = self.store.configs[0]
        path.parent.mkdir(parents=True)
        path.write_text("broken", encoding="utf-8")
        before = self.store.file.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply(self.provider)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")
        self.assertEqual(self.store.file.read_bytes(), before)
        self.assertFalse(self.store.profile.exists())

    def test_partial_write_failure_rolls_back_every_native_file(self):
        channels.write_object(self.store.configs[0], {"deploymentMode": "1p", "keep": True})
        paths = [*self.store.configs, self.store.profile, self.store.meta, self.store.file]
        before = {p: p.read_bytes() if p.exists() else None for p in paths}
        original = channels.write_object
        def fail(path, value):
            if path == self.store.configs[1]:
                raise OSError("simulated write failure")
            return original(path, value)
        with mock.patch("claude_channels.write_object", side_effect=fail):
            with self.assertRaises(OSError):
                self.store.apply(self.provider)
        self.assertEqual(before, {p: p.read_bytes() if p.exists() else None for p in paths})

    def test_import_existing_static_gateway_does_not_apply_or_duplicate(self):
        native_id = "11111111-1111-4111-8111-111111111111"
        channels.write_object(self.store.meta, {"appliedId": native_id, "entries": [{"id": native_id, "name": "Imported"}]})
        channels.write_object(self.store.library / (native_id + ".json"), {"inferenceProvider": "gateway",
            "inferenceGatewayBaseUrl": "https://import.example", "inferenceGatewayApiKey": "import-key"})
        before = self.store.meta.read_bytes()
        self.assertEqual(self.store.import_native()["imported"], 1)
        self.assertEqual(self.store.import_native()["duplicates"], 1)
        self.assertEqual(self.store.meta.read_bytes(), before)
        self.assertEqual(self.store.list()["current_id"], channels.OFFICIAL)

    def test_active_provider_cannot_be_deleted(self):
        self.store.apply(self.provider)
        with self.assertRaisesRegex(ValueError, "先切换"):
            self.store.delete(self.provider)
        self.store.apply(channels.OFFICIAL)
        self.store.delete(self.provider)
        self.assertEqual(len(self.store.list()["providers"]), 1)

    def test_native_anthropic_preflight_uses_correct_endpoint_and_key_header(self):
        response = io.BytesIO(json.dumps({"type": "message", "content": []}).encode())
        with mock.patch("claude_channels.urllib.request.urlopen", return_value=response) as opened:
            self.assertIn("连接正常", self.store.check(self.provider))
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway.example/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "unit-key")
        self.assertEqual(json.loads(request.data)["max_tokens"], 1)

    def test_openai_response_is_not_falsely_marked_compatible(self):
        with mock.patch("claude_channels.urllib.request.urlopen", return_value=io.BytesIO(b'{"choices":[]}')):
            with self.assertRaisesRegex(ValueError, "Anthropic"):
                self.store.check(self.provider)

    def test_invalid_urls_models_and_profile_ids_are_rejected(self):
        for value in ("https://gateway.example?key=secret", "https://user:pass@gateway.example", "https://gateway.example/v1/messages"):
            with self.assertRaises(ValueError):
                channels.normalize_url(value)
        with self.assertRaises(ValueError):
            self.store.save({"name": "Other", "base_url": "https://gateway.example", "api_key": "key", "models": "gpt-model"})
        with self.assertRaises(ValueError):
            self.store.save({"id": "../escape", "name": "Bad"})


class ClaudeSwitchApiTests(unittest.TestCase):
    def test_switch_failure_reopens_previous_configuration(self):
        from codex_app import Api
        api = Api()
        api._claude_channels = mock.Mock()
        api._claude_channels.apply.side_effect = ValueError("invalid config")
        with mock.patch.object(api, "stop_claude_translation"), mock.patch("claude_channels.stop_claude_app"), mock.patch("claude_page_loader.launch_claude") as launch:
            result = api.switch_claude_provider(channels.OFFICIAL)
        self.assertFalse(result["ok"])
        launch.assert_called_once()

    def test_switch_failure_before_shutdown_does_not_launch_or_write(self):
        from codex_app import Api
        api = Api()
        api._claude_channels = mock.Mock()
        with mock.patch.object(api, "stop_claude_translation"), mock.patch("claude_channels.stop_claude_app", side_effect=RuntimeError("still running")), mock.patch("claude_page_loader.launch_claude") as launch:
            result = api.switch_claude_provider(channels.OFFICIAL)
        self.assertFalse(result["ok"])
        api._claude_channels.apply.assert_not_called()
        launch.assert_not_called()
