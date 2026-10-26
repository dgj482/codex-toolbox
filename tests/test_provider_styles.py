"""Provider edits and switches use temporary files, never the user's channels."""
from pathlib import Path
import tempfile
import threading
import tomllib
import unittest
from unittest import mock

from codex_app import Api
import codex_toolbox as core
from claude_channels import ClaudeChannels
from provider_style import stored_css

CSS = '.provider-card { background: #eff4ff; border-radius: 16px; }'


class ProviderStyleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'config.toml'
        self.config.write_text('model = "original"\nmodel_provider = "openai"\n', encoding='utf-8')
        for name, value in [('CONFIG', self.config), ('PROVIDERS_FILE', self.root / 'providers.json'),
                            ('BAK_DIR', self.root / 'backups')]:
            patch = mock.patch.object(core, name, str(value))
            patch.start()
            self.addCleanup(patch.stop)
        self.api = Api()

    def test_same_name_creates_distinct_providers_and_preserves_css_on_edit(self):
        first = self.api.save_provider({'name': 'Same', 'card_css': CSS})['id']
        second = self.api.save_provider({'name': 'Same'})['id']
        self.assertNotEqual(first, second)
        self.api.save_provider({'id': first, 'name': 'Edited'})
        rows = self.api.get_providers()
        self.assertEqual(next(p for p in rows if p['id'] == first)['card_css'], CSS)
        self.assertEqual(next(p for p in rows if p['id'] == second)['name'], 'Same')
        self.api.save_provider({'id': first, 'name': 'Edited', 'card_css': ''})
        self.assertEqual(next(p for p in self.api.get_providers() if p['id'] == first)['card_css'], '')

    def test_css_example_uses_save_dialog_and_handles_cancellation(self):
        from provider_style import EXAMPLE_CSS
        self.api._window = mock.Mock()
        self.api._window.create_file_dialog.return_value = None
        self.assertTrue(self.api.export_provider_css_example()['cancelled'])
        path = self.root / 'sample.css'
        for result in [(str(path),), str(path)]:
            self.api._window.create_file_dialog.return_value = result
            self.assertTrue(self.api.export_provider_css_example()['ok'])
            self.assertEqual(path.read_text(encoding='utf-8'), EXAMPLE_CSS)

    def test_switch_writes_once_and_never_exports_css_to_codex(self):
        provider = self.api.save_provider({'name': 'Test', 'model': 'test-model', 'card_css': CSS,
                                           'base_url': 'https://example.invalid/v1'})['id']
        result = self.api.switch_provider(provider)
        self.assertTrue(result['ok'], result)
        data = tomllib.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(data['model_provider'], provider)
        self.assertEqual(data['model'], 'test-model')
        self.assertNotIn('card_css', data['model_providers'][provider])
        self.assertEqual(len(list((self.root / 'backups').glob('*'))), 1)

    def test_overlapping_switch_is_rejected_and_lock_recovers_after_failure(self):
        entered, release = threading.Event(), threading.Event()
        def failing_switch(*args):
            entered.set()
            release.wait(5)
            raise OSError('simulated write failure')
        result = []
        provider = self.api.save_provider({'name': 'Test'})['id']
        with mock.patch.object(core, 'apply_provider', side_effect=failing_switch) as apply:
            thread = threading.Thread(target=lambda: result.append(self.api.switch_provider(provider)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(self.api.switch_provider(provider)['ok'])
            finally:
                release.set()
                thread.join(5)
            apply.assert_called_once()
        self.assertFalse(result[0]['ok'])
        self.assertTrue(self.api.switch_provider(provider)['ok'])

    def test_invalid_style_payload_leaves_saved_data_unchanged(self):
        self.api.save_provider({'name': 'Valid'})
        path = self.root / 'providers.json'
        before = path.read_bytes()
        for css in [None, {}, '汉' * 12000]:
            with self.assertRaises(ValueError):
                self.api.save_provider({'name': 'Invalid', 'card_css': css})
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(stored_css('  '), '')

    def test_claude_css_survives_reload_and_blank_key_edit(self):
        store = ClaudeChannels(self.root / 'claude-toolbox', self.root / 'local')
        data = {'name': 'Gateway', 'base_url': 'https://example.invalid', 'api_key': 'test-only', 'card_css': CSS}
        with mock.patch('claude_channels.secret_store.protect_secret', return_value='protected-test'):
            provider = store.save(data)
            data.update(id=provider, api_key='')
            data.pop('card_css')
            store.save(data)
        reloaded = ClaudeChannels(store.data_dir, store.local_dir)
        row = next(p for p in reloaded.list()['providers'] if p['id'] == provider)
        self.assertEqual(row['card_css'], CSS)
        self.assertNotIn('api_key_dpapi', row)
        self.assertEqual(reloaded._provider(provider)['api_key_dpapi'], 'protected-test')
        store.save(dict(data, card_css=''))
        self.assertEqual(store._provider(provider)['card_css'], '')


if __name__ == '__main__':
    unittest.main()
