"""Regressions for saved preferences, independent app launch and proxy ownership."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from codex_app import Api
import codex_toolbox as core


class AuditFixTests(unittest.TestCase):
    def test_enter_preference_survives_save_reload_and_older_callers(self):
        api = Api()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                api, "_translation_settings_file", return_value=str(Path(directory) / "translation.json")):
            settings = api._normalized_translation_settings({"intercept_enter": False})
            api._save_translation_settings(settings)
            self.assertIs(api._load_translation_settings()["intercept_enter"], False)
            self.assertIs(api._normalized_translation_settings({})["intercept_enter"], False)

    def test_claude_opens_even_when_translation_permission_is_missing(self):
        api = Api()
        with mock.patch.object(api._claude_channels, "list", return_value={"current_id":"claude-official"}), \
                mock.patch.object(api, "_trace_claude_launch"), \
                mock.patch("claude_page_loader.launch_claude") as launch, \
                mock.patch("claude_page_loader.check_developer_permissions", side_effect=RuntimeError("permission missing")):
            result = api.launch_claude_hub({})
        launch.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertIn("Claude 已打开", result["error"])
        self.assertIn("permission missing", result["error"])

    def test_reused_proxy_shutdown_requires_auth_and_matching_configuration(self):
        code = ('import codex_proxy as p; '
                's,port=p.start_proxy("https://example.invalid/v1","dummy-key","test-model",0,'
                'provider_id="audit-provider",proxy_token="dummy-token"); '
                'print(port,flush=True);s.stop_event.wait();s.shutdown();s.server_close()')
        process = subprocess.Popen([sys.executable,"-u","-c",code], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=str(Path(__file__).resolve().parents[1]),
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            port = int(process.stdout.readline().strip())
            url = f"http://127.0.0.1:{port}"
            for token, fingerprint, expected in [("wrong", "wrong",401), ("dummy-token","wrong",409)]:
                request = urllib.request.Request(url+"/shutdown", data=json.dumps(
                    {"config_fingerprint":fingerprint}).encode(), headers={"Authorization":"Bearer "+token})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request,timeout=2)
                self.assertEqual(caught.exception.code,expected)
                caught.exception.close()
                self.assertIsNone(process.poll())
            api = Api()
            provider = {"id":"audit-provider","model":"test-model","api_key":"dummy-key",
                        "upstream_base":"https://example.invalid/v1","proxy_token":"dummy-token"}
            with mock.patch.object(core,"PROXY_PORT",port):
                self.assertEqual(api._ensure_proxy(provider),url+"/v1")
            self.assertIsNone(api.proxy_process, "This instance did not start the process")
            api.stop_proxy()
            self.assertEqual(process.wait(timeout=5),0)
            self.assertFalse(api._proxy_sessions)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
