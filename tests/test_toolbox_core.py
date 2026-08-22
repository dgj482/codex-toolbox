# -*- coding: utf-8 -*-
"""工具箱核心回归测试：全部使用临时目录，不接触用户真实 Codex 数据。"""
import json
import os
import sqlite3
import tempfile
import unittest
import inspect
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codex_toolbox as core
import codex_proxy
from codex_app import Api


class ToolboxCoreTests(unittest.TestCase):
    def test_get_models_prefers_api_created_order_and_hides_non_chat_models(self):
        class Response:
            def read(self):
                return json.dumps({"data": [
                    {"id": "old-chat", "created": 10},
                    {"id": "new-chat", "created": 30},
                    {"id": "embedding-v1", "created": 99},
                    {"id": "middle-chat", "created": 20},
                ]}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            result = Api().get_models("https://example.test/v1", "test-key")

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["models"]], [
            "new-chat", "middle-chat", "old-chat",
        ])

    def test_background_settings_keep_saved_image_and_optional_codex_path(self):
        api = Api()
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "wallpaper.png")
            executable = os.path.join(td, "Codex.exe")
            Path(image).touch()
            Path(executable).touch()
            settings = api._normalized_background_settings({
                "image_path": image,
                "codex_launch_path": executable,
            })

        self.assertEqual(settings["image_path"], os.path.abspath(image))
        self.assertEqual(settings["codex_launch_path"], os.path.abspath(executable))

    def test_protocol_detection_recommends_direct_when_responses_is_accepted(self):
        class Response:
            status = 200

            def read(self, *_):
                return b'{"id":"resp_test","object":"response","output":[]}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            result = Api().detect_protocol("https://example.test/v1", "test-key", "test-model")

        self.assertTrue(result["ok"])
        self.assertFalse(result["proxy"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/responses")
        self.assertEqual(request.get_method(), "POST")

    def test_protocol_detection_rejects_non_responses_200(self):
        class Response:
            status = 200

            def read(self, *_):
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            result = Api().detect_protocol("https://example.test/v1", "test-key", "test-model")

        self.assertFalse(result["ok"])
        self.assertIn("不是原生 Responses", result["error"])

    def test_delete_current_provider_is_rejected(self):
        provider = {"id": "custom", "name": "自定义 API"}
        with mock.patch.object(core, "read_provider", return_value="custom"), \
             mock.patch.object(core, "load_providers", return_value=[provider]), \
             mock.patch.object(core, "save_providers") as save:
            result = Api().delete_provider("custom")

        self.assertFalse(result["ok"])
        self.assertIn("当前全局供应商", result["error"])
        save.assert_not_called()

    def test_proxy_rejects_unsupported_tools_and_media(self):
        with self.assertRaisesRegex(codex_proxy.UnsupportedCapabilityError, "web_search"):
            codex_proxy.responses_to_chat({
                "input": "测试",
                "tools": [{"type": "web_search"}],
            })
        with self.assertRaisesRegex(codex_proxy.UnsupportedCapabilityError, "input_image"):
            codex_proxy.responses_to_chat({
                "input": [{
                    "type": "message", "role": "user",
                    "content": [{"type": "input_image", "image_url": "https://example.test/a.png"}],
                }],
            })

    def test_proxy_treats_string_input_as_one_message(self):
        chat = codex_proxy.responses_to_chat({"input": "你好"})
        self.assertEqual(chat["messages"], [{"role": "user", "content": "你好"}])

    def test_common_provider_presets_are_complete_and_distinct(self):
        """预设仅是起始模板，但必须能让用户完整填入供应商配置。"""
        labels = [item["label"] for item in core.PRESETS]
        self.assertGreaterEqual(len(labels), 24)
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(all(item.get("name") and item.get("base_url") and item.get("model")
                            and item.get("models") for item in core.PRESETS))
        self.assertTrue(all(item.get("wire_api") == "responses" for item in core.PRESETS))

    def test_official_provider_does_not_assume_a_specific_account_plan(self):
        provider = core.normalize_provider({
            "id": "openai", "name": "官方 ChatGPT Plus", "kind": "official",
            "base_url": "https://example.test/v1", "api_key": "should-not-survive", "proxy": True,
        })
        self.assertEqual(provider["name"], core.OFFICIAL_NAME)
        self.assertEqual(provider["kind"], "official")
        self.assertEqual(provider["base_url"], "")
        self.assertEqual(provider["api_key"], "")
        self.assertNotIn("proxy", provider)

    def test_non_openai_provider_cannot_claim_builtin_login(self):
        provider = core.normalize_provider({"id": "custom", "kind": "official"})
        self.assertEqual(provider["kind"], "relay")

    def test_background_prefers_user_installed_powershell(self):
        with mock.patch("codex_app.shutil.which", side_effect=lambda name: "C:/pwsh/pwsh.exe" if name == "pwsh.exe" else None):
            self.assertEqual(Api._find_pwsh(), "C:/pwsh/pwsh.exe")

    def test_api_startup_does_not_automatically_migrate_user_config(self):
        """公开版启动只读本地状态，不能悄悄重写 Codex 配置。"""
        with mock.patch.object(core, "migrate_active_dashscope") as migrate:
            api = Api()
        migrate.assert_not_called()
        self.assertFalse(api.migrated_dashscope)

    def test_background_error_is_sanitized_for_ui(self):
        raw = "\x1b[31;1mthrow\x1b[0m\nCodex 已启动，但没有开放调试端口。Chromium 参数不可用。"
        message = Api._format_background_error(raw)
        self.assertNotIn("\x1b", message)
        self.assertIn("没有修改任何文件或数据", message)

    def test_background_projection_only_keeps_bounded_known_shapes(self):
        api = Api()
        settings = api._normalized_background_settings({
            "image_path": "background.png",
            "projection_shape": "freehand",
            "projection_points": [
                {"x": -1, "y": 0.4}, {"x": 0.5, "y": 2}, {"x": 0.8, "y": 0.7},
                "not-a-point",
            ],
        })
        self.assertEqual(settings["projection_shape"], "freehand")
        self.assertEqual(settings["projection_points"], [
            {"x": 0.0, "y": 0.4}, {"x": 0.5, "y": 1.0}, {"x": 0.8, "y": 0.7},
        ])
        selected = api._normalized_background_settings({
            "image_path": "background.png",
            "projection_mode": "crop",
            "gradient_enabled": True,
            "gradient_type": "radial",
            "gradient_color_start": "#AABBCC",
            "gradient_color_end": "not-a-color",
            "gradient_opacity": 99,
            "gradient_angle": 999,
            "projection_layers": [
                {"type": "circle", "enabled": False, "width": 9, "points": [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.4}]},
                {"type": "line", "points": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.8}]},
            ],
        })
        self.assertFalse(selected["projection_layers"][0]["enabled"])
        self.assertTrue(selected["projection_layers"][1]["enabled"])
        self.assertEqual(selected["projection_layers"][0]["width"], 0.24)
        self.assertEqual(selected["projection_mode"], "crop")
        self.assertEqual(selected["gradient_type"], "radial")
        self.assertEqual(selected["gradient_color_start"], "#aabbcc")
        self.assertEqual(selected["gradient_color_end"], "#2bb984")
        self.assertEqual(selected["gradient_opacity"], 80)
        self.assertEqual(selected["gradient_angle"], 360)
        invalid = api._normalized_background_settings({
            "image_path": "background.png", "projection_shape": "unexpected", "projection_points": []
        })
        self.assertEqual(invalid["projection_shape"], "none")

    def test_background_projection_accepts_single_bounded_rectangle(self):
        api = Api()
        settings = api._normalized_background_settings({
            "image_path": "background.png",
            "projection_shape": "rect",
            "projection_points": [{"x": -0.2, "y": 0.2}, {"x": 1.5, "y": 0.8}],
            "projection_layers": [{
                "type": "rect", "enabled": True,
                "points": [{"x": 0.2, "y": 0.3}, {"x": 0.7, "y": 0.9}],
            }],
        })
        self.assertEqual(settings["projection_shape"], "rect")
        self.assertEqual(settings["projection_points"], [{"x": 0.0, "y": 0.2}, {"x": 1.0, "y": 0.8}])
        self.assertEqual(settings["projection_layers"], [{
            "type": "rect", "enabled": True, "width": 0.09,
            "points": [{"x": 0.2, "y": 0.3}, {"x": 0.7, "y": 0.9}],
        }])

    def test_pywebview_api_does_not_expose_main_window_object(self):
        """避免 PyWebView 递归扫描 Window，导致前端 API 方法列表为空。"""
        api = Api()
        api.set_window(object())
        public_attributes = [name for name in dir(api) if not name.startswith("_")]
        self.assertNotIn("window", public_attributes)
        self.assertIn("get_providers", public_attributes)
        self.assertTrue(inspect.ismethod(getattr(api, "get_providers")))

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

    def test_sync_session_index_removes_archived_and_restores_active_entry(self):
        with tempfile.TemporaryDirectory() as td:
            index = os.path.join(td, "session_index.jsonl")
            Path(index).write_text(
                '{"id":"active","thread_name":"保留"}\n'
                '{"id":"archived","thread_name":"应移除"}\n',
                encoding="utf-8",
            )
            with mock.patch.object(core, "SESSION_INDEX_FILE", index):
                core._sync_session_index(removed_ids=["archived"])
                lines = [json.loads(line) for line in Path(index).read_text(encoding="utf-8").splitlines()]
                self.assertEqual([item["id"] for item in lines], ["active"])

                core._sync_session_index(restored_rows=[("archived", "已恢复")])
            lines = [json.loads(line) for line in Path(index).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["id"] for item in lines], ["active", "archived"])
            self.assertEqual(lines[-1]["thread_name"], "已恢复")

    def test_remove_archived_catalog_rows_only_removes_archived_threads(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, _, _, _ = self._make_archive_fixture(td)
            conn = sqlite3.connect(state)
            with conn:
                conn.execute("INSERT INTO threads VALUES (?, ?, 1, 1, '已归档', 'openai', 'gpt', 'medium')",
                             ("archived-thread", "archived.jsonl"))
            conn.close()
            conn = sqlite3.connect(catalog)
            with conn:
                conn.execute("INSERT INTO local_thread_catalog VALUES (?, ?)", ("archived-thread", "应清理"))
            conn.close()

            with mock.patch.object(core, "STATE_DB", state), \
                mock.patch.object(core, "CATALOG_DB", catalog):
                self.assertEqual(core._remove_archived_catalog_rows(["thread-archive", "archived-thread"]), 1)

            conn = sqlite3.connect(catalog)
            rows = conn.execute("SELECT thread_id FROM local_thread_catalog ORDER BY thread_id").fetchall()
            conn.close()
            self.assertEqual(rows, [("thread-archive",)])

    def test_background_apply_uses_selected_image_and_persists_only_local_settings(self):
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "wallpaper.png")
            script = os.path.join(td, "injector.ps1")
            settings = os.path.join(td, "background.json")
            Path(image).write_bytes(b"image")
            Path(script).write_text("# test", encoding="utf-8")

            class Completed:
                returncode = 0
                stdout = "完成：背景已注入"
                stderr = ""

            with mock.patch.object(core, "migrate_active_dashscope", return_value=False), \
                 mock.patch.object(Api, "_background_settings_file", return_value=settings), \
                 mock.patch.object(Api, "_load_community_experiments", return_value={"local_appearance": True, "session_routing": False}), \
                 mock.patch("codex_app._resource_path", return_value=script), \
                 mock.patch.object(Api, "_find_pwsh", return_value="pwsh.exe"), \
                 mock.patch("codex_app.subprocess.run", return_value=Completed()) as run:
                result = Api().apply_background({
                    "image_path": image,
                    "background_mask": 40,
                    "panel_opacity": 60,
                    "blur": 5,
                    "projection_mode": "crop",
                    "gradient_enabled": True,
                }, restart=True)

            self.assertTrue(result["ok"])
            command = run.call_args.args[0]
            self.assertIn("-Restart", command)
            self.assertIn("-ThreadHeaderOpacity", command)
            self.assertIn("-CompactDiff:$false", command)
            self.assertIn("normal", command)
            self.assertIn("-AllowAnimatedGif:$false", command)
            self.assertIn("-ProjectionMode", command)
            self.assertIn("crop", command)
            self.assertIn("-GradientEnabled:$true", command)
            self.assertIn(image, command)
            saved = json.loads(Path(settings).read_text(encoding="utf-8"))
            self.assertEqual(saved["current"]["blur"], 5)
            self.assertEqual(saved["current"]["sidebar_opacity"], 42)
            self.assertEqual(saved["current"]["background_scale"], 100)

    def test_background_start_retries_attach_once_when_first_launch_is_slow(self):
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "wallpaper.png")
            script = os.path.join(td, "injector.ps1")
            settings = os.path.join(td, "background.json")
            Path(image).write_bytes(b"image")
            Path(script).write_text("# test", encoding="utf-8")

            class Delayed:
                returncode = 1
                stdout = "Codex 已启动，但没有开放调试端口。"
                stderr = ""

            class Completed:
                returncode = 0
                stdout = "完成：背景已注入"
                stderr = ""

            with mock.patch.object(core, "migrate_active_dashscope", return_value=False), \
                 mock.patch.object(Api, "_background_settings_file", return_value=settings), \
                 mock.patch.object(Api, "_load_community_experiments", return_value={"local_appearance": True, "session_routing": False}), \
                 mock.patch("codex_app._resource_path", return_value=script), \
                 mock.patch.object(Api, "_find_pwsh", return_value="pwsh.exe"), \
                 mock.patch("codex_app.subprocess.run", side_effect=[Delayed(), Completed()]) as run:
                result = Api().apply_background({"image_path": image}, restart=True)

            self.assertTrue(result["ok"])
            self.assertEqual(run.call_count, 2)
            first, second = (call.args[0] for call in run.call_args_list)
            self.assertIn("-Restart", first)
            self.assertNotIn("-Restart", second)
            self.assertIn("-AttachWaitSeconds", second)
            self.assertIn("30", second)

    def test_animated_gif_requires_explicit_enable_and_has_size_limit(self):
        with tempfile.TemporaryDirectory() as td:
            gif = os.path.join(td, "wallpaper.gif")
            settings = os.path.join(td, "background.json")
            script = os.path.join(td, "injector.ps1")
            Path(gif).write_bytes(b"GIF89a")
            Path(script).write_text("# test", encoding="utf-8")

            class Completed:
                returncode = 0
                stdout = "完成：背景已注入"
                stderr = ""

            with mock.patch.object(core, "migrate_active_dashscope", return_value=False), \
                 mock.patch.object(Api, "_background_settings_file", return_value=settings), \
                 mock.patch.object(Api, "_load_community_experiments", return_value={"local_appearance": True, "session_routing": False}), \
                 mock.patch("codex_app._resource_path", return_value=script), \
                 mock.patch.object(Api, "_find_pwsh", return_value="pwsh.exe"), \
                 mock.patch("codex_app.subprocess.run", return_value=Completed()) as run:
                api = Api()
                denied = api.apply_background({"image_path": gif}, restart=False)
                self.assertFalse(denied["ok"])
                self.assertIn("默认关闭", denied["error"])
                enabled = api.apply_background({
                    "image_path": gif, "animated_gif": True, "background_scale": 1,
                }, restart=False)
            self.assertTrue(enabled["ok"])
            command = run.call_args.args[0]
            self.assertIn("-AllowAnimatedGif:$true", command)
            self.assertIn("-BackgroundScale", command)
            self.assertIn("10", command)
            self.assertIn("-BackgroundHeight", command)
            self.assertIn("-BackgroundScope", command)

    def test_mp4_background_requires_opt_in_and_passes_selected_path(self):
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "wallpaper.mp4")
            settings = os.path.join(td, "background.json")
            script = os.path.join(td, "injector.ps1")
            Path(video).write_bytes(b"not-a-real-video")
            Path(script).write_text("# test", encoding="utf-8")

            class Completed:
                returncode = 0
                stdout = "完成：背景已注入"
                stderr = ""

            with mock.patch.object(core, "migrate_active_dashscope", return_value=False), \
                 mock.patch.object(Api, "_background_settings_file", return_value=settings), \
                 mock.patch.object(Api, "_load_community_experiments", return_value={"local_appearance": True, "session_routing": False}), \
                 mock.patch("codex_app._resource_path", return_value=script), \
                 mock.patch.object(Api, "_find_pwsh", return_value="pwsh.exe"), \
                 mock.patch("codex_app.subprocess.run", return_value=Completed()) as run:
                api = Api()
                denied = api.apply_background({"image_path": video}, restart=False)
                self.assertFalse(denied["ok"])
                self.assertIn("默认关闭", denied["error"])
                enabled = api.apply_background({"image_path": video, "animated_mp4": True}, restart=False)

            self.assertTrue(enabled["ok"])
            command = run.call_args.args[0]
            self.assertIn("-VideoPath", command)
            self.assertIn(video, command)
            self.assertIn("-AllowVideo:$true", command)
            self.assertNotIn("-ImagePath", command)

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

    def test_following_active_thread_ids_skips_archived_threads(self):
        with tempfile.TemporaryDirectory() as td:
            routes_file = os.path.join(td, "thread_routes.json")
            state = os.path.join(td, "state.sqlite")
            conn = sqlite3.connect(state)
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, archived INTEGER)")
            conn.executemany(
                "INSERT INTO threads VALUES (?, ?)",
                [("active", 0), ("archived", 1)],
            )
            conn.commit()
            conn.close()
            with mock.patch.object(core, "THREAD_ROUTES_FILE", routes_file), \
                 mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "read_provider", return_value="openai"):
                core.set_thread_route_mode(["active", "archived"], "follow")
                self.assertEqual(
                    set(core.following_thread_ids(active_only=True)), {"active"}
                )

    def test_switch_provider_never_rewrites_existing_sessions(self):
        """全局切换只能改默认值，不能因旧会话路由失败而失败。"""
        api = object.__new__(Api)
        provider = {
            "id": "relay", "name": "测试中转", "kind": "relay",
            "proxy": False,
        }
        with mock.patch.object(core, "load_providers", return_value=[provider]), \
             mock.patch.object(core, "ensure_provider_config") as ensure, \
             mock.patch.object(core, "apply_provider", return_value="测试中转") as apply, \
             mock.patch.object(core, "following_thread_ids", return_value=["old-thread"]), \
             mock.patch.object(core, "set_threads_provider_official") as set_threads:
            result = api.switch_provider("relay")

        self.assertTrue(result["ok"])
        self.assertTrue(result["existing_sessions_unchanged"])
        ensure.assert_called_once_with("relay", None)
        apply.assert_called_once_with("relay", None)
        set_threads.assert_not_called()

    def test_test_all_provider_connections_reuses_single_provider_check(self):
        api = object.__new__(Api)
        providers = [
            {"id": "openai", "name": "官方"},
            {"id": "qwen", "name": "千问"},
        ]
        with mock.patch.object(core, "load_providers", return_value=providers), \
             mock.patch.object(
                 Api,
                 "test_provider_connection",
                 side_effect=[
                     {"ok": True, "message": "官方登录状态"},
                     {"ok": False, "error": "HTTP 401：API Key 无效或已过期"},
                 ],
             ) as check:
            result = api.test_all_provider_connections()

        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["results"]], ["官方", "千问"])
        self.assertEqual([item["ok"] for item in result["results"]], [True, False])
        self.assertEqual(check.call_args_list, [mock.call("openai"), mock.call("qwen")])

    def test_session_route_unsupported_is_a_notice_not_a_failure(self):
        api = object.__new__(Api)
        provider = {"id": "qwen", "name": "千问", "model": "qwen-test", "proxy": False}
        with mock.patch.object(core, "codex_running", return_value=False), \
             mock.patch.object(core, "load_providers", return_value=[provider]), \
             mock.patch.object(Api, "_load_community_experiments", return_value={"local_appearance": False, "session_routing": True}), \
             mock.patch.object(core, "ensure_provider_config"), \
             mock.patch.object(
                 core,
                 "set_threads_provider_official",
                 side_effect=core.SessionRouteUnsupported("未确认保存"),
             ):
            result = api.switch_sessions(["thread-a"], "qwen")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["message"], "未确认保存")

    def test_session_route_is_denied_until_user_enables_community_experiment(self):
        api = object.__new__(Api)
        with mock.patch.object(
            Api, "_load_community_experiments",
            return_value={"local_appearance": False, "session_routing": False},
        ):
            result = api.switch_sessions(["thread-a"], "qwen")
        self.assertFalse(result["ok"])
        self.assertIn("确认并启用", result["error"])

    def test_start_route_operation_returns_operation_and_finishes_in_background(self):
        api = Api()
        with mock.patch.object(
            Api, "_load_community_experiments",
            return_value={"local_appearance": False, "session_routing": True},
        ), mock.patch.object(core, "codex_running", return_value=False), mock.patch.object(
            api, "switch_sessions", return_value={"ok": True, "count": 1, "backup": "backup-1"}
        ) as switch:
            submitted = api.start_route_operation(["thread-a"], "qwen", "fixed")
            self.assertTrue(submitted["ok"])
            self.assertIn("operation_id", submitted)
            operation_id = submitted["operation_id"]
            for _ in range(40):
                status = api.get_route_operation(operation_id)
                if status["state"] == "success":
                    break
                import time
                time.sleep(0.01)
            self.assertEqual(status["state"], "success")
            self.assertEqual(status["count"], 1)
            self.assertEqual(status["backup"], "backup-1")
            switch.assert_called_once_with(["thread-a"], "qwen", None, None)

    def test_start_route_operation_rejects_duplicate_running_operation(self):
        api = Api()
        api.route_operations["existing"] = {"state": "running"}
        with mock.patch.object(
            Api, "_load_community_experiments",
            return_value={"local_appearance": False, "session_routing": True},
        ), mock.patch.object(core, "codex_running", return_value=False):
            result = api.start_route_operation(["thread-a"], "qwen", "fixed")
        self.assertFalse(result["ok"])
        self.assertIn("已有会话路由操作", result["error"])

    def test_archived_sidebar_repair_only_removes_confirmed_ids_and_keeps_backups(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, _, _ = self._make_archive_fixture(td)
            index = os.path.join(td, "session_index.jsonl")
            Path(index).write_text(
                '{"id":"thread-archive","thread_name":"活跃会话"}\n'
                '{"id":"archived-thread","thread_name":"已归档会话"}\n',
                encoding="utf-8",
            )
            conn = sqlite3.connect(state)
            with conn:
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, 1, 1, '已归档', 'openai', 'gpt', 'medium')",
                    ("archived-thread", "archived.jsonl"),
                )
            conn.close()
            conn = sqlite3.connect(catalog)
            with conn:
                conn.execute("INSERT INTO local_thread_catalog VALUES (?, ?)", ("archived-thread", "已归档"))
            conn.close()

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "SESSION_INDEX_FILE", index), \
                 mock.patch.object(core, "BAK_DIR", backup):
                stamp, count, catalog_count, repair_backup = core.repair_archived_session_index()

            self.assertEqual(count, 1)
            self.assertEqual(catalog_count, 1)
            self.assertTrue(os.path.isfile(os.path.join(repair_backup, "state_5.sqlite")))
            self.assertTrue(os.path.isfile(os.path.join(repair_backup, "codex-dev.db")))
            self.assertTrue(os.path.isfile(os.path.join(repair_backup, "session_index.jsonl")))
            self.assertIn("archived_sidebar_repair_", os.path.basename(repair_backup))
            catalog_conn = sqlite3.connect(catalog)
            try:
                catalog_rows = catalog_conn.execute(
                    "SELECT thread_id FROM local_thread_catalog ORDER BY thread_id"
                ).fetchall()
            finally:
                catalog_conn.close()
            self.assertEqual(catalog_rows, [("thread-archive",)])
            index_ids = [json.loads(line)["id"] for line in Path(index).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(index_ids, ["thread-archive"])

    def test_thread_route_waits_for_async_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            state = os.path.join(td, "state.sqlite")
            conn = sqlite3.connect(state)
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, model TEXT, reasoning_effort TEXT)"
            )
            conn.execute("INSERT INTO threads VALUES ('thread-a', 'openai', 'gpt-old', 'medium')")
            conn.commit()
            conn.close()

            slept = []

            def persist_after_first_wait(_seconds):
                slept.append(_seconds)
                conn = sqlite3.connect(state)
                with conn:
                    conn.execute(
                        "UPDATE threads SET model_provider='qwen', model='qwen-test', reasoning_effort='high' "
                        "WHERE id='thread-a'"
                    )
                conn.close()

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core.time, "sleep", side_effect=persist_after_first_wait):
                self.assertTrue(core._wait_for_thread_route("thread-a", "qwen", "qwen-test", "high"))
            self.assertEqual(slept, [core.THREAD_ROUTE_VERIFY_INTERVAL])

    def test_find_codex_executable_uses_registered_store_package(self):
        with tempfile.TemporaryDirectory() as td:
            executable = os.path.join(td, "app", "resources", "codex.exe")
            os.makedirs(os.path.dirname(executable))
            Path(executable).touch()

            class Completed:
                returncode = 0
                stdout = td + "\n"

            with mock.patch.dict(os.environ, {
                "ProgramFiles": os.path.join(td, "missing"),
                "APPDATA": os.path.join(td, "missing-appdata"),
            }, clear=False), \
                 mock.patch.object(core.shutil, "which", return_value=None), \
                 mock.patch.object(core.glob, "glob", return_value=[]), \
                 mock.patch.object(core.subprocess, "run", return_value=Completed()):
                self.assertEqual(core._find_codex_executable(), executable)

    def test_find_codex_executable_prefers_official_cli(self):
        with tempfile.TemporaryDirectory() as td:
            cli = os.path.join(td, "npm", "codex.cmd")
            package = os.path.join(td, "npm", "node_modules", "@openai", "codex", "bin", "codex.js")
            os.makedirs(os.path.dirname(package))
            Path(cli).touch()
            Path(package).touch()
            with mock.patch.dict(os.environ, {"APPDATA": td}, clear=False), \
                 mock.patch.object(core.shutil, "which", return_value=None):
                self.assertEqual(core._find_codex_executable(), cli)

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
                mock.patch.object(core, "SESSION_INDEX_FILE", os.path.join(td, "session_index.jsonl")), \
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

    def test_thread_route_override_is_limited_to_selected_threads(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, rollouts = self._make_route_fixture(td)
            providers = [
                {"id": "openai", "name": "官方", "model": "gpt-old"},
                {"id": "qwen", "name": "千问", "model": "qwen-default",
                 "reasoning_effort": "high"},
            ]

            class FakeAppServer:
                requests = []

                def __init__(self, executable):
                    pass

                def request(self, method, params):
                    FakeAppServer.requests.append((method, dict(params)))
                    conn = sqlite3.connect(core.STATE_DB)
                    try:
                        with conn:
                            if method == "thread/resume":
                                conn.execute(
                                    "UPDATE threads SET model_provider=?, model=? WHERE id=?",
                                    (params["modelProvider"], params["model"], params["threadId"]),
                                )
                            elif method == "thread/settings/update":
                                conn.execute(
                                    "UPDATE threads SET model=?, reasoning_effort=? WHERE id=?",
                                    (params["model"], params["effort"], params["threadId"]),
                                )
                            else:
                                raise AssertionError(method)
                    finally:
                        conn.close()
                    return dict(params)

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "load_providers", return_value=providers), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FakeAppServer):
                core.set_threads_provider_official(
                    ["thread-route"], "qwen", "qwen-session", "low",
                )

            conn = sqlite3.connect(state)
            row = conn.execute(
                "SELECT model_provider, model, reasoning_effort FROM threads WHERE id='thread-route'"
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("qwen", "qwen-session", "low"))
            self.assertEqual(providers[1]["model"], "qwen-default")
            self.assertEqual(providers[1]["reasoning_effort"], "high")
            self.assertEqual(FakeAppServer.requests[0][1]["model"], "qwen-session")
            self.assertEqual(FakeAppServer.requests[1][1]["effort"], "low")
            self.assertTrue(os.path.isfile(rollouts["thread-route"]))

    def test_thread_route_rejects_unknown_effort_override(self):
        provider = {"id": "qwen", "name": "千问", "model": "qwen-test"}
        with self.assertRaisesRegex(ValueError, "思考强度"):
            core.resolve_thread_route_settings(provider, "", "invalid")

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
                mock.patch.object(core, "SESSION_INDEX_FILE", os.path.join(td, "session_index.jsonl")), \
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

    def test_thread_route_rolls_back_when_effort_update_fails(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, _ = self._make_route_fixture(td)
            providers = [
                {"id": "openai", "name": "官方", "model": "gpt-old"},
                {"id": "qwen", "name": "千问", "model": "qwen-test", "reasoning_effort": "high"},
            ]

            class SettingsFailServer:
                def __init__(self, executable):
                    pass

                def request(self, method, params):
                    if method == "thread/settings/update" and params["effort"] == "high":
                        raise RuntimeError("模拟思考强度写入失败")
                    conn = sqlite3.connect(core.STATE_DB)
                    try:
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
                            raise AssertionError(method)
                    finally:
                        conn.close()
                    return dict(params)

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "load_providers", return_value=providers), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", SettingsFailServer):
                with self.assertRaisesRegex(RuntimeError, "模拟思考强度写入失败"):
                    core.set_thread_provider("thread-route", "qwen")

            conn = sqlite3.connect(state)
            row = conn.execute(
                "SELECT model_provider, model, reasoning_effort FROM threads WHERE id='thread-route'"
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("openai", "gpt-old", "medium"))

    def test_archive_rejects_missing_child_archive_and_rolls_back_parent(self):
        with tempfile.TemporaryDirectory() as td:
            state, catalog, backup, _, _ = self._make_archive_fixture(td, extended_path=False)
            child_rollout = os.path.join(td, "sessions", "rollout-thread-child.jsonl")
            Path(child_rollout).write_text('{"type":"session_meta"}\n', encoding="utf-8")
            conn = sqlite3.connect(state)
            with conn:
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, 0, NULL, '子会话', 'openai', 'gpt-old', 'medium')",
                    ("thread-child", child_rollout),
                )
                conn.execute(
                    "INSERT INTO thread_spawn_edges VALUES (?, ?, 'active')",
                    ("thread-archive", "thread-child"),
                )
            conn.close()

            class NonCascadingArchiveServer:
                requests = []

                def __init__(self, executable):
                    pass

                def request(self, method, params):
                    NonCascadingArchiveServer.requests.append((method, params["threadId"]))
                    conn = sqlite3.connect(core.STATE_DB)
                    try:
                        with conn:
                            if method == "thread/archive":
                                conn.execute("UPDATE threads SET archived=1 WHERE id=?", (params["threadId"],))
                            elif method == "thread/unarchive":
                                conn.execute("UPDATE threads SET archived=0 WHERE id=?", (params["threadId"],))
                            else:
                                raise AssertionError(method)
                    finally:
                        conn.close()

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", NonCascadingArchiveServer):
                with self.assertRaisesRegex(RuntimeError, "thread-child"):
                    core.archive_threads(["thread-archive"])

            conn = sqlite3.connect(state)
            archived = conn.execute("SELECT id, archived FROM threads ORDER BY id").fetchall()
            conn.close()
            self.assertEqual(archived, [("thread-archive", 0), ("thread-child", 0)])
            self.assertIn(("thread/unarchive", "thread-archive"), NonCascadingArchiveServer.requests)

    def test_missing_catalog_backup_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            missing_catalog = os.path.join(td, "missing-catalog.sqlite")
            destination = os.path.join(td, "backup.sqlite")
            with mock.patch.object(core, "CATALOG_DB", missing_catalog):
                core._backup_catalog_if_present(destination)
            self.assertFalse(os.path.exists(destination))

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
                mock.patch.object(core, "SESSION_INDEX_FILE", os.path.join(td, "session_index.jsonl")), \
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
                    raise RuntimeError("模拟 app-server 归档失败")

                def close(self):
                    pass

            with mock.patch.object(core, "STATE_DB", state), \
                 mock.patch.object(core, "CATALOG_DB", catalog), \
                 mock.patch.object(core, "BAK_DIR", backup), \
                 mock.patch.object(core, "_find_codex_executable", return_value="codex.exe"), \
                 mock.patch.object(core, "_CodexAppServer", FailingAppServer):
                with self.assertRaisesRegex(RuntimeError, "模拟 app-server 归档失败"):
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
