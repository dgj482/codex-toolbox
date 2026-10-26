"""Portable tests of macOS branches; no real Keychain or Codex is accessed."""
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_app
import codex_toolbox as core
import secret_store


class MacCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_keychain_roundtrip_reuses_reference_and_propagates_denial(self):
        backend = mock.Mock()
        backend.get_password.return_value = "test-only-secret"
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(secret_store, "_mac_keychain", return_value=backend), mock.patch.object(secret_store, "_keychain_refs", {}):
            reference = secret_store.protect_secret("test-only-secret")
            self.assertTrue(reference.startswith("keychain:"))
            self.assertNotIn("test-only-secret", reference)
            self.assertEqual(secret_store.unprotect_secret(reference), "test-only-secret")
            self.assertEqual(secret_store.protect_secret("test-only-secret"), reference)
            backend.set_password.assert_called_once()
            backend.get_password.side_effect = RuntimeError("denied")
            with self.assertRaisesRegex(RuntimeError, "denied"):
                secret_store.unprotect_secret(reference)

    def test_denied_secrets_survive_provider_edit_without_token_regeneration(self):
        path = self.root / "providers.json"
        raw = {"id": "relay", "name": "Relay", "proxy": True,
               "api_key_keychain": "keychain:11111111-1111-4111-8111-111111111111",
               "proxy_token_keychain": "keychain:22222222-2222-4222-8222-222222222222"}
        path.write_text(json.dumps({"version": 2, "providers": [raw]}), encoding="utf-8")
        with mock.patch.object(core, "PROVIDERS_FILE", str(path)), mock.patch.object(core, "_providers_from_config", return_value=[]), mock.patch.object(secret_store, "unprotect_secret", side_effect=RuntimeError("denied")), mock.patch.object(secret_store, "protect_secret", side_effect=AssertionError("must preserve references")):
            providers = core.load_providers()
            self.assertEqual(providers[0]["proxy_token"], "")
            core.save_providers(providers)
            api = codex_app.Api()
            api.save_provider({"id": "relay", "name": "Edited", "proxy": True})
            saved = json.loads(path.read_text(encoding="utf-8"))["providers"][0]
            self.assertEqual(saved["api_key_keychain"], raw["api_key_keychain"])
            self.assertEqual(saved["proxy_token_keychain"], raw["proxy_token_keychain"])
            self.assertNotIn("_unreadable_secrets", saved)
            self.assertNotIn("api_key", saved)

    def test_process_detection_covers_helpers_and_paths_with_spaces(self):
        result = mock.Mock(returncode=0, stdout="1 /sbin/launchd\n22 /Applications/Codex.app/Contents/MacOS/Codex\n23 /Users/Test User/Applications/Codex.app/Contents/Frameworks/Codex Helper\n24 /usr/local/bin/codex\n25 /Applications/Other.app/Contents/MacOS/Other\n")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(core.subprocess, "run", return_value=result):
            state = core.codex_process_state()
            self.assertEqual(state["state"], "running")
            self.assertEqual([p["pid"] for p in state["processes"]], [22, 23, 24])
            result.returncode = 1
            self.assertEqual(core.codex_process_state()["state"], "unknown")

    def test_failed_plaintext_migration_preserves_existing_file(self):
        path = self.root / "providers.json"
        original = json.dumps({"version": 1, "providers": [{"id": "relay", "api_key": "test-only-secret"}]})
        path.write_text(original, encoding="utf-8")
        with mock.patch.object(core, "PROVIDERS_FILE", str(path)), mock.patch.object(core, "_providers_from_config", return_value=[]), mock.patch.object(secret_store, "protect_secret", side_effect=RuntimeError("denied")):
            with self.assertRaisesRegex(RuntimeError, "原文件已保留"):
                core.load_providers()
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_bundle_validation_reads_actual_executable(self):
        bundle = self.root / "Codex.app"
        binary = bundle / "Contents" / "MacOS" / "Desktop"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"test")
        binary.chmod(0o755)
        with (bundle / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleExecutable": "Desktop"}, handle)
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(codex_app.Api._validated_launch_path(str(bundle)), str(bundle))
            binary.unlink()
            with self.assertRaises(ValueError):
                codex_app.Api._validated_launch_path(str(bundle))

    def test_large_image_preparation_preserves_source(self):
        from PIL import Image
        source = self.root / "large.bmp"
        Image.new("RGB", (1600, 1200), "blue").save(source)
        original = source.read_bytes()
        with mock.patch.object(sys, "platform", "darwin"):
            prepared, temporary = codex_app.Api._prepare_mac_background(str(source))
        self.addCleanup(lambda: os.path.exists(prepared) and os.unlink(prepared))
        self.assertTrue(temporary)
        self.assertLessEqual(os.path.getsize(prepared), 500000)
        self.assertEqual(source.read_bytes(), original)

    def test_mac_app_data_location(self):
        base = str(self.root / "Library" / "Application Support")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(secret_store.os.path, "expanduser", return_value=base):
            self.assertEqual(secret_store.app_data_dir(), os.path.join(base, "CodexToolbox"))

    def test_installed_mac_uses_its_bundled_runtime_without_path_dependency(self):
        executable = self.root / "工具箱.app" / "Contents" / "MacOS" / "Codex工具箱"
        runtime = executable.parent.parent / "Resources" / "powershell" / "pwsh"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"test runtime")
        runtime.chmod(0o755)
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(executable)), mock.patch.object(codex_app.shutil, "which", side_effect=AssertionError("bundled runtime must take precedence")):
            self.assertEqual(codex_app.Api._find_pwsh(), str(runtime))

    def test_mac_source_launch_keeps_system_runtime_lookup(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(codex_app.shutil, "which", return_value="/opt/homebrew/bin/pwsh"):
            self.assertEqual(codex_app.Api._find_pwsh(), "/opt/homebrew/bin/pwsh")


if __name__ == "__main__":
    unittest.main()
