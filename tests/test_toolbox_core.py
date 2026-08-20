# -*- coding: utf-8 -*-
"""工具箱核心回归测试：全部使用临时目录，不接触用户真实 Codex 数据。"""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codex_toolbox as core
from codex_app import Api


class ToolboxCoreTests(unittest.TestCase):
    @staticmethod
    def _make_archive_fixture(td, extended_path=True):
        state = os.path.join(td, "state.sqlite")
        catalog = os.path.join(td, "catalog.sqlite")
        backup = os.path.join(td, "backups")
        sessions = os.path.join(td, "sessions", "2026", "08", "20")
        os.makedirs(sessions)
        rollout = os.path.join(sessions, "rollout-thread-archive.jsonl")
        with open(rollout, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_meta", "payload": {"id": "thread-archive"}}) + "\n")
        stored_path = "\\\\?\\" + rollout if extended_path and os.name == "nt" else rollout

        conn = sqlite3.connect(state)
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER, "
            "archived_at INTEGER, title TEXT, model_provider TEXT, model TEXT, reasoning_effort TEXT)"
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, 0, NULL, '归档测试', 'openai', 'gpt-old', 'medium')",
            ("thread-archive", stored_path),
        )
        conn.execute(
            "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)"
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(catalog)
        conn.execute("CREATE TABLE local_thread_catalog (thread_id TEXT, display_title TEXT)")
        conn.execute("INSERT INTO local_thread_catalog VALUES ('thread-archive', '归档测试')")
        conn.commit()
        conn.close()
        return state, catalog, backup, rollout, stored_path

    @staticmethod
    def _make_route_fixture(td, thread_ids=("thread-route",)):
        state = os.path.join(td, "state.sqlite")
        catalog = os.path.join(td, "catalog.sqlite")
        backup = os.path.join(td, "backups")
        sessions = os.path.join(td, "sessions")
        os.makedirs(sessions)

        conn = sqlite3.connect(state)
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER, "
            "model_provider TEXT, model TEXT, reasoning_effort TEXT)"
        )
        rollouts = {}
        for tid in thread_ids:
            rollout = os.path.join(sessions, f"rollout-{tid}.jsonl")
            Path(rollout).write_text(
                json.dumps({"type": "session_meta", "payload": {"id": tid}}) + "\n",
                encoding="utf-8",
            )
            conn.execute(
                "INSERT INTO threads VALUES (?, ?, 0, 'openai', 'gpt-old', 'medium')",
                (tid, rollout),
            )
            rollouts[tid] = rollout
        conn.commit()
        conn.close()

        conn = sqlite3.connect(catalog)
        conn.execute("CREATE TABLE local_thread_catalog (thread_id TEXT, display_title TEXT)")
        conn.commit()
        conn.close()
        return state, catalog, backup, rollouts

    def test_model_family_only_flags_cross_provider_residue(self):
        self.assertEqual(Api._model_family("gpt-5.6-sol"), Api._model_family("gpt-5.6-terra"))
        self.assertNotEqual(Api._model_family("qwen3.8-max"), Api._model_family("gpt-5.6-terra"))

    def test_dashscope_legacy_proxy_is_normalized_to_direct(self):
        p = core.normalize_provider({
            "id": "qwen",
            "name": "通义千问",
            "base_url": "http://127.0.0.1:18732/v1",
            "upstream_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "proxy": True,
        })
        self.assertEqual(p["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertFalse(p["proxy"])
        self.assertEqual(p["kind"], "direct")
        self.assertEqual(p["wire_api"], "responses")
        self.assertNotIn("upstream_base", p)

    def test_provider_edit_can_preserve_key_by_backend_contract(self):
        p = core.normalize_provider({"id": "relay", "api_key": "secret", "base_url": "https://example.test/v1"})
        self.assertEqual(p["api_key"], "secret")

    def test_apply_provider_writes_configured_reasoning_effort(self):
        with tempfile.TemporaryDirectory() as td:
            config = os.path.join(td, "config.toml")
            backup = os.path.join(td, "backups")
            Path(config).write_text(
                'model_provider = "openai"\nmodel = "gpt-old"\nmodel_reasoning_effort = "medium"\n',
                encoding="utf-8",
            )
            providers = [{
                "id": "qwen", "name": "千问", "model": "qwen-test",
                "reasoning_effort": "high", "base_url": "https://example.test/v1",
                "wire_api": "responses",
            }]
            with mock.patch.object(core, "CONFIG", config), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "load_providers", return_value=providers), \
                 mock.patch.object(core, "_auth_helper_command", return_value=("helper.exe", [])):
                core.apply_provider("qwen")
            updated = Path(config).read_text(encoding="utf-8")
            self.assertIn('model_provider = "qwen"', updated)
            self.assertIn('model = "qwen-test"', updated)
            self.assertIn('model_reasoning_effort = "high"', updated)

    def test_thread_route_modes_are_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            routes_file = os.path.join(td, "thread_routes.json")
            with mock.patch.object(core, "THREAD_ROUTES_FILE", routes_file), \
                 mock.patch.object(core, "read_provider", return_value="openai"):
                core.set_thread_route_mode(["a", "b"], "follow")
                core.set_thread_route_mode(["c"], "fixed", "qwen")
                routes = core.load_thread_routes()
                self.assertEqual(routes["a"]["mode"], "follow")
                self.assertEqual(routes["c"]["provider"], "qwen")
                self.assertEqual(set(core.following_thread_ids()), {"a", "b"})

    def test_thread_provider_uses_official_resume_without_editing_rollout(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, rollouts = self._make_route_fixture(td)
            original_rollout = Path(rollouts["thread-route"]).read_bytes()
            providers = [
                {"id": "openai", "name": "官方", "model": "gpt-old"},
                {"id": "qwen", "name": "千问", "model": "qwen-test",
                 "reasoning_effort": "high"},
            ]

            class FakeAppServer:
                requests = []

                def __init__(self, executable):
                    self.executable = executable

                def request(self, method, params):
                    FakeAppServer.requests.append(dict(params))
                    conn = sqlite3.connect(core.STATE_DB)
                    if method == "thread/resume":
                        with conn:
                            conn.execute(
                                "UPDATE threads SET model_provider=?, model=? WHERE id=?",
                                (params["modelProvider"], params["model"], params["threadId"]),
                            )
                    elif method == "thread/settings/update":
                        with conn:
                            conn.execute(
                                "UPDATE threads SET model=?, reasoning_effort=? WHERE id=?",
                                (params["model"], params["effort"], params["threadId"]),
                            )
                    else:
                        conn.close()
                        raise AssertionError(method)
                    conn.close()
                    return ({
                        "modelProvider": params["modelProvider"],
                        "model": params["model"],
                    } if method == "thread/resume" else {})

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "load_providers", return_value=providers), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FakeAppServer):
                stamp = core.set_thread_provider("thread-route", "qwen")

            conn = sqlite3.connect(state)
            row = conn.execute(
                "SELECT model_provider, model, reasoning_effort FROM threads WHERE id='thread-route'"
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("qwen", "qwen-test", "high"))
            self.assertEqual(Path(rollouts["thread-route"]).read_bytes(), original_rollout)
            self.assertEqual(FakeAppServer.requests[0]["excludeTurns"], True)
            self.assertEqual(FakeAppServer.requests[1]["effort"], "high")
            self.assertTrue(os.path.isfile(os.path.join(
                backup, f"thread_route_{stamp}", "state_5.sqlite"
            )))

    def test_thread_provider_failure_rolls_back_switched_threads(self):
        with tempfile.TemporaryDirectory() as td:
            tids = ("thread-a", "thread-b")
            state, catalog, backup, _ = self._make_route_fixture(td, tids)
            providers = [
                {"id": "openai", "name": "官方", "model": "gpt-old"},
                {"id": "qwen", "name": "千问", "model": "qwen-test"},
            ]

            class FailingSecondServer:
                def __init__(self, executable):
                    pass

                def request(self, method, params):
                    if (method == "thread/resume" and params["threadId"] == "thread-b"
                            and params["modelProvider"] == "qwen"):
                        raise RuntimeError("模拟第二个会话切换失败")
                    conn = sqlite3.connect(core.STATE_DB)
                    if method == "thread/resume":
                        with conn:
                            conn.execute(
                                "UPDATE threads SET model_provider=?, model=? WHERE id=?",
                                (params["modelProvider"], params["model"], params["threadId"]),
                            )
                    elif method == "thread/settings/update":
                        with conn:
                            conn.execute(
                                "UPDATE threads SET model=?, reasoning_effort=? WHERE id=?",
                                (params["model"], params["effort"], params["threadId"]),
                            )
                    else:
                        conn.close()
                        raise AssertionError(method)
                    conn.close()
                    return dict(params)

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "load_providers", return_value=providers), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FailingSecondServer):
                with self.assertRaisesRegex(RuntimeError, "模拟第二个会话切换失败"):
                    core.set_threads_provider_official(tids, "qwen")

            conn = sqlite3.connect(state)
            rows = conn.execute(
                "SELECT id, model_provider, model, reasoning_effort FROM threads ORDER BY id"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [
                ("thread-a", "openai", "gpt-old", "medium"),
                ("thread-b", "openai", "gpt-old", "medium"),
            ])

    def test_provider_block_uses_command_auth_not_plaintext_token(self):
        provider = {
            "id": "qwen", "name": "千问", "kind": "direct",
            "base_url": "https://example.test/v1", "api_key": "dummy-token",
            "model": "qwen-test", "wire_api": "responses",
        }
        with mock.patch.object(core, "_auth_helper_command", return_value=("helper.exe", [])):
            block = core._provider_block("qwen", provider)
        self.assertIn("[model_providers.qwen.auth]", block)
        self.assertIn('command = "helper.exe"', block)
        self.assertIn('args = ["qwen"]', block)
        self.assertNotIn("dummy-token", block)
        self.assertNotIn("experimental_bearer_token", block)

    def test_provider_file_contains_only_dpapi_ciphertext(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(core, "PROVIDERS_FILE", os.path.join(td, "providers.json")):
            providers = [{
                "id": "relay", "name": "Relay", "kind": "relay",
                "base_url": "https://example.test/v1", "api_key": "dummy-token",
                "model": "model-test",
            }]
            core.save_providers(providers)
            raw = Path(core.PROVIDERS_FILE).read_text(encoding="utf-8")
            self.assertNotIn("dummy-token", raw)
            self.assertIn("api_key_dpapi", raw)
            loaded = core.load_providers()
            relay = next(p for p in loaded if p["id"] == "relay")
            self.assertEqual(relay["api_key"], "dummy-token")

    def test_archive_uses_official_app_server_and_normalizes_path(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, rollout, stored_path = self._make_archive_fixture(td)

            class FakeAppServer:
                saw_normal_path = False

                def __init__(self, executable):
                    self.executable = executable

                def request(self, method, params):
                    self.assert_method(method)
                    tid = params["threadId"]
                    conn = sqlite3.connect(core.STATE_DB)
                    source = conn.execute("SELECT rollout_path FROM threads WHERE id=?", (tid,)).fetchone()[0]
                    FakeAppServer.saw_normal_path = not source.startswith("\\\\?\\")
                    destination_dir = os.path.join(os.path.dirname(core.STATE_DB), "archived_sessions")
                    os.makedirs(destination_dir, exist_ok=True)
                    destination = os.path.join(destination_dir, os.path.basename(source))
                    os.replace(source, destination)
                    with conn:
                        conn.execute(
                            "UPDATE threads SET archived=1, archived_at=1, rollout_path=? WHERE id=?",
                            (destination, tid),
                        )
                    conn.close()
                    cat = sqlite3.connect(core.CATALOG_DB)
                    with cat:
                        cat.execute("DELETE FROM local_thread_catalog WHERE thread_id=?", (tid,))
                    cat.close()

                @staticmethod
                def assert_method(method):
                    if method != "thread/archive":
                        raise AssertionError(method)

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FakeAppServer):
                stamp = core.archive_threads(["thread-archive"])

            conn = sqlite3.connect(state)
            archived, archived_path = conn.execute(
                "SELECT archived, rollout_path FROM threads WHERE id='thread-archive'"
            ).fetchone()
            conn.close()
            self.assertTrue(FakeAppServer.saw_normal_path)
            self.assertEqual(archived, 1)
            self.assertFalse(os.path.exists(rollout))
            self.assertTrue(os.path.isfile(archived_path))
            self.assertTrue(os.path.isfile(os.path.join(backup, f"archive_{stamp}", "state_5.sqlite")))
            self.assertTrue(os.path.isfile(os.path.join(
                backup, f"archive_{stamp}", "rollouts", os.path.basename(rollout)
            )))

    def test_archive_failure_restores_original_rollout_path(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, rollout, stored_path = self._make_archive_fixture(td)

            class FailingAppServer:
                def __init__(self, executable):
                    pass

                def request(self, method, params):
                    raise RuntimeError("模拟官方归档失败")

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FailingAppServer):
                with self.assertRaisesRegex(RuntimeError, "模拟官方归档失败"):
                    core.archive_threads(["thread-archive"])

            conn = sqlite3.connect(state)
            archived, restored_path = conn.execute(
                "SELECT archived, rollout_path FROM threads WHERE id='thread-archive'"
            ).fetchone()
            conn.close()
            self.assertEqual(archived, 0)
            self.assertEqual(restored_path, stored_path)
            self.assertTrue(os.path.isfile(rollout))

    def test_unarchive_uses_official_app_server_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, rollout, _ = self._make_archive_fixture(td, extended_path=False)
            archived_dir = os.path.join(td, "archived_sessions")
            os.makedirs(archived_dir)
            archived_path = os.path.join(archived_dir, os.path.basename(rollout))
            os.replace(rollout, archived_path)
            conn = sqlite3.connect(state)
            with conn:
                conn.execute(
                    "UPDATE threads SET archived=1, archived_at=1, rollout_path=? WHERE id=?",
                    (archived_path, "thread-archive"),
                )
            conn.close()

            class FakeUnarchiveServer:
                def __init__(self, executable):
                    self.executable = executable

                def request(self, method, params):
                    if method != "thread/unarchive":
                        raise AssertionError(method)
                    tid = params["threadId"]
                    conn = sqlite3.connect(core.STATE_DB)
                    source = conn.execute(
                        "SELECT rollout_path FROM threads WHERE id=?", (tid,)
                    ).fetchone()[0]
                    destination_dir = os.path.join(td, "sessions", "restored")
                    os.makedirs(destination_dir, exist_ok=True)
                    destination = os.path.join(destination_dir, os.path.basename(source))
                    os.replace(source, destination)
                    with conn:
                        conn.execute(
                            "UPDATE threads SET archived=0, archived_at=NULL, rollout_path=? WHERE id=?",
                            (destination, tid),
                        )
                    conn.close()

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FakeUnarchiveServer):
                stamp = core.unarchive_threads(["thread-archive"])

            conn = sqlite3.connect(state)
            archived, restored_path = conn.execute(
                "SELECT archived, rollout_path FROM threads WHERE id='thread-archive'"
            ).fetchone()
            conn.close()
            self.assertEqual(archived, 0)
            self.assertTrue(os.path.isfile(restored_path))
            self.assertTrue(os.path.isfile(os.path.join(
                backup, f"unarchive_{stamp}", "state_5.sqlite"
            )))
            self.assertTrue(os.path.isfile(os.path.join(
                backup, f"unarchive_{stamp}", "rollouts", os.path.basename(archived_path)
            )))


if __name__ == "__main__":
    unittest.main()
