import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import codex_toolbox as core
from codex_app import Api


class ModelManagementTests(unittest.TestCase):
    def test_active_official_display_uses_actual_model(self):
        with mock.patch.object(core,'load_providers',return_value=[{'id':'openai','name':'Official','model':'gpt-5.6-terra'}]),mock.patch.object(core,'read_provider',return_value='openai'),mock.patch.object(core,'read_model',return_value='gpt-6.1-sol'):
            self.assertEqual(Api().get_providers()[0]['model'],'gpt-6.1-sol')

    def test_saved_key_stays_in_backend_and_is_bound_to_saved_address(self):
        api = Api()
        with mock.patch.object(core, 'load_providers', return_value=[{'id':'saved','base_url':'https://example.test/v1','api_key':'secret'}]):
            self.assertEqual(api._saved_request_key('https://example.test/v1/', '', 'saved'), 'secret')
            with self.assertRaises(ValueError):
                api._saved_request_key('https://other.test/v1', '', 'saved')

    def test_local_model_fetch_needs_no_key_and_keeps_full_list(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'data':[{'id':f'model-{i}'} for i in range(30)]}).encode()
        with mock.patch('urllib.request.urlopen', return_value=response) as request:
            result = Api().get_models('http://localhost:1234/v1', '')
        self.assertEqual(len(result['models']),30)
        self.assertNotIn('Authorization', request.call_args.args[0].headers)

    def test_official_model_list_pagination_and_cleanup(self):
        client = mock.MagicMock()
        client.request.side_effect = [{'data':[{'model':'gpt-6.1-sol'}],'nextCursor':'next'}, {'data':[{'id':'gpt-6-luna'}]}]
        with mock.patch.object(core, '_find_codex_executable', return_value='codex'), mock.patch.object(core, '_CodexAppServer', return_value=client):
            result = Api().get_models('', '', 'openai')
        self.assertEqual([m['id'] for m in result['models']],['gpt-6.1-sol','gpt-6-luna'])
        client.close.assert_called_once()

    def test_switch_remembers_active_official_model_but_honors_explicit_edit(self):
        for explicit, expected in [(False,'gpt-6.1-sol'),(True,'gpt-6-luna')]:
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as td:
                config = Path(td)/'config.toml'
                config.write_text('model = "gpt-6.1-sol"\nmodel_provider = "openai"\n',encoding='utf-8')
                providers=[{'id':'openai','name':'Official','model':'gpt-6-luna','model_explicit':explicit}]
                with mock.patch.object(core,'CONFIG',str(config)),mock.patch.object(core,'BAK_DIR',str(Path(td)/'backups')),mock.patch.object(core,'load_providers',return_value=providers),mock.patch.object(core,'save_providers'),mock.patch.object(core,'prune_backups'):
                    core.apply_provider('openai')
                self.assertEqual(core.tomllib.loads(config.read_text(encoding='utf-8'))['model'],expected)
