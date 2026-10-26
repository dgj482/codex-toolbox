"""Batch import regressions; fixtures never use installed Claude data or keys."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

import claude_channels as channels
from codex_app import Api


class ProviderImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = channels.ClaudeChannels(self.root / 'data', self.root / 'local')
        self.entries = []
        for method, action in [('protect_secret', lambda key: 'protected:' + key),
                               ('unprotect_secret', lambda key: key.removeprefix('protected:'))]:
            patch = mock.patch.object(channels.secret_store, method, side_effect=action)
            patch.start()
            self.addCleanup(patch.stop)

    def profile(self, name='Example', key='test-key-a', base='https://example.invalid', **extra):
        entry_id = str(uuid.uuid4())
        self.entries.append({'id': entry_id, 'name': name})
        channels.write_object(self.store.meta, {'entries': self.entries})
        path = self.store.library / (entry_id + '.json')
        channels.write_object(path, {'inferenceProvider': 'gateway', 'inferenceGatewayApiKey': key,
                                    'inferenceGatewayBaseUrl': base, **extra})
        return path

    def test_bad_profile_does_not_hide_valid_imports_and_batch_writes_once(self):
        self.profile('First')
        broken = self.profile('Broken')
        broken.read_bytes()
        broken.write_text('{', encoding='utf-8')
        self.profile('Last', key='test-key-b')
        native_before = {p: p.read_bytes() for p in self.store.library.glob('*.json')}
        with mock.patch.object(channels, 'write_object', wraps=channels.write_object) as write, \
                mock.patch.object(self.store, '_load', wraps=self.store._load) as load:
            report = self.store.import_native()
        self.assertEqual((report['imported'], report['failed']), (2, 1))
        self.assertEqual(write.call_count, 1)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(native_before, {p: p.read_bytes() for p in self.store.library.glob('*.json')})
        self.assertEqual(self.store.list()['current_id'], channels.OFFICIAL)
        self.assertNotIn('test-key', json.dumps(report))

    def test_dedupe_normalizes_urls_but_keeps_different_accounts_and_models(self):
        self.profile('Account A', base='HTTPS://EXAMPLE.invalid:443/')
        self.profile('Duplicate A')
        self.profile('Account B', key='test-key-b')
        self.profile('Different model', inferenceModels=['claude-sonnet-test'])
        report = self.store.import_native()
        self.assertEqual(report['imported'], 3)
        self.assertEqual(report['duplicates'], 1)
        again = self.store.import_native()
        self.assertEqual(again['imported'], 0)
        self.assertEqual(again['duplicates'], 4)

    def test_previously_imported_source_does_not_overwrite_user_edits(self):
        source = self.profile()
        report = self.store.import_native()
        provider_id = report['imported_ids'][0]
        self.store.save({'id': provider_id, 'name': 'Edited', 'base_url': 'https://new.invalid', 'card_css': '.provider-name {color:blue;}'})
        source.read_bytes()
        channels.write_object(source, {'inferenceProvider': 'gateway', 'inferenceGatewayBaseUrl': 'https://example.invalid', 'inferenceGatewayApiKey': 'changed-test-key'})
        before = self.store.file.read_bytes()
        self.assertEqual(self.store.import_native()['duplicates'], 1)
        self.assertEqual(self.store.file.read_bytes(), before)

    def test_models_accept_ids_in_objects_and_newline_text(self):
        self.profile(inferenceModels=[{'id': 'claude-sonnet-test', 'name': 'Friendly label'}])
        self.profile(key='test-key-b', inferenceModels='claude-opus-test\nclaude-haiku-test')
        self.assertEqual(self.store.import_native()['imported'], 2)
        self.assertEqual(self.store._load()['providers'][0]['models'], 'claude-sonnet-test')

    def test_unsupported_and_missing_profiles_have_reasons(self):
        self.profile('Helper', inferenceCredentialKind='helper-script', inferenceCredentialHelperWindows='never-execute.exe')
        self.profile('No key', key='')
        self.profile('Invalid models', inferenceModels={})
        missing = self.profile('Missing')
        missing.read_bytes()
        missing.unlink()
        self.entries += [None, {'id': '../outside'}]
        channels.write_object(self.store.meta, {'entries': self.entries})
        with mock.patch.object(channels.subprocess, 'run', side_effect=AssertionError('must not execute')):
            report = self.store.import_native()
        self.assertEqual(report['imported'], 0)
        self.assertEqual(report['skipped'], 2)
        self.assertEqual(report['failed'], 4)
        self.assertFalse(self.store.file.exists())

    def test_failed_secret_storage_aborts_entire_batch(self):
        self.profile('A')
        self.profile('B', key='test-key-b')
        with mock.patch.object(channels.secret_store, 'protect_secret', side_effect=['encrypted-test-a', RuntimeError('sensitive detail')]):
            with self.assertRaisesRegex(ValueError, '未保存') as caught:
                self.store.import_native()
        self.assertNotIn('sensitive', str(caught.exception))
        self.assertFalse(self.store.file.exists())

    def test_replace_failure_preserves_store_and_removes_temporary_file(self):
        self.store.save({'name': 'Existing', 'base_url': 'https://other.invalid', 'api_key': 'existing-test-key'})
        self.profile()
        before = self.store.file.read_bytes()
        with mock.patch.object(channels.os, 'replace', side_effect=OSError('simulated disk failure')):
            with self.assertRaisesRegex(ValueError, '未写入'):
                self.store.import_native()
        self.assertEqual(self.store.file.read_bytes(), before)
        self.assertEqual(list(self.store.data_dir.glob('*.tmp')), [])

    def test_concurrent_imports_do_not_duplicate_and_empty_index_is_reported(self):
        self.assertEqual(self.store.import_native()['items'], [])
        self.profile()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.import_native(), range(2)))
        self.assertEqual(sorted(r['imported'] for r in results), [0, 1])
        self.assertEqual(len(self.store._load()['providers']), 1)

    def test_bad_index_is_rejected_before_any_write(self):
        channels.write_object(self.store.meta, {'entries': {}})
        with self.assertRaisesRegex(ValueError, 'entries'):
            self.store.import_native()
        self.assertFalse(self.store.file.exists())

    def test_bad_url_error_does_not_echo_embedded_credentials(self):
        self.profile(base='https://test-secret@bad\uff03hostname.invalid')
        report = self.store.import_native()
        self.assertEqual(report['failed'], 1)
        self.assertNotIn('test-secret', json.dumps(report))

    def test_api_import_is_blocked_during_switch_and_returns_summary(self):
        self.profile()
        api = Api()
        api._claude_channels = self.store
        with api._claude_switch_lock:
            self.assertFalse(api.import_claude_providers()['ok'])
        result = api.import_claude_providers()
        self.assertTrue(result['ok'])
        self.assertEqual(result['imported'], 1)
        self.assertIn('当前通道未改变', result['message'])


if __name__ == '__main__':
    unittest.main()
