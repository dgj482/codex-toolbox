# -*- coding: utf-8 -*-
"""Codex 工具箱（PyWebView 界面）
前端：ui.html（HTML/CSS/JS），后端：本文件 Api 类 + codex_toolbox 逻辑
运行：python codex_app.py （需 pywebview + WebView2）
"""
import os
import sys
import json
import threading
import glob
import subprocess
import re
import secrets
import shutil
import plistlib
import tempfile
import time
import urllib.request
import webview
import codex_toolbox as core
import secret_store
from provider_style import stored_css
from codex_surface_translation import OpenAICompatibleTranslator, SurfaceTranslationController, select_codex_page_target
from version import __version__

UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
CODEX_EXPERIMENT_PORT = 9229


def _resource_path(*parts):
    """返回开发版或打包版内置资源的实际路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def _toolbox_icon_path():
    """返回主程序、窗口和托盘统一使用的图标路径。"""
    candidates = (
        _resource_path("codex_toolbox.ico"),
        _resource_path("assets", "codex_toolbox.ico"),
        os.path.join(core._app_dir(), "codex_toolbox.ico"),
    )
    return next((path for path in candidates if os.path.isfile(path)), "")


def _load_ui():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, "ui.html")
    with open(p, encoding="utf-8") as f:
        return f.read()


class Api:
    """JS 通过 window.pywebview.api.<method> 调用"""

    def __init__(self):
        self.proxy_server = None
        self.proxy_process = None
        self.proxy_url = ""
        self._proxy_sessions = {}
        self._provider_lock = threading.Lock()
        # 会话路由在后台执行，避免 UI 请求被 app-server 的落盘校验长时间阻塞。
        self.route_operations = {}
        self.route_operations_lock = threading.Lock()
        # PyWebView 会递归扫描 js_api 的公开属性并生成前端方法列表。
        # 主窗口对象必须是私有属性，否则扫描 Window 的内部对象图会中断导出，
        # 页面便会出现“window.pywebview.api[method] is not a function”。
        self._window = None
        self._translation_controller = None
        self._translation_last_error = ""
        self._translation_log_lock = threading.Lock()
        self._claude_page_bridge = None
        self._claude_page_options = None
        self._claude_last_error = ""
        self._claude_lock = threading.RLock()
        self._claude_generation = 0
        self._claude_starting = False
        from claude_channels import ClaudeChannels
        self._claude_channels = ClaudeChannels()
        self._claude_switch_lock = threading.Lock()
        # 启动时只读取状态，绝不因“检测到旧配置”自动改写用户的 Codex 配置。
        # 历史迁移函数保留给维护者手动处理，不作为公开版启动路径的一部分。
        self.migrated_dashscope = False

    def set_window(self, window):
        """保存主窗口，用于调用系统文件选择器。"""
        self._window = window

    def get_app_info(self):
        return {"version": __version__, "claude_supported": os.name == "nt", "secret_storage": "macOS Keychain" if sys.platform == "darwin" else "Windows DPAPI", "auth_migration_error": getattr(self, "_auth_migration_error", "")}

    # ---------- Claude Desktop saved providers ----------
    def get_claude_providers(self):
        try:
            return self._claude_channels.list()
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def save_claude_provider(self, data):
        try:
            provider_id = self._claude_channels.save(data)
            return {"ok": True, "id": provider_id, "message": "供应商已保存；切换后生效。"}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def delete_claude_provider(self, provider_id):
        try:
            self._claude_channels.delete(provider_id)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def import_claude_providers(self):
        if not self._claude_switch_lock.acquire(blocking=False):
            return {"ok": False, "error": "Claude 通道正在更新，请稍后导入"}
        try:
            report = self._claude_channels.import_native()
            message = (f"已导入 {report['imported']} 个；重复 {report['duplicates']} 个；"
                       f"跳过 {report['skipped']} 个；无效 {report['failed']} 个。当前通道未改变。")
            if not report["items"]:
                message = "未发现 Claude 本地保存的供应商配置。可通过“添加供应商”手动配置。"
            return {"ok": True, "message": message, **report}
        except Exception as error:
            return {"ok": False, "error": str(error)}
        finally:
            self._claude_switch_lock.release()

    def test_claude_provider(self, provider_id):
        try:
            return {"ok": True, "message": self._claude_channels.check(provider_id)}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def switch_claude_provider(self, provider_id):
        if not self._claude_switch_lock.acquire(blocking=False):
            return {"ok": False, "error": "Claude 正在切换通道，请稍候"}
        stopped = False
        try:
            from claude_channels import OFFICIAL, stop_claude_app
            from claude_page_loader import launch_claude
            # Validate provider existence before disrupting the current app.
            if provider_id != OFFICIAL:
                self._claude_channels._provider(provider_id)
            self.stop_claude_translation()
            stop_claude_app()
            stopped = True
            backup = self._claude_channels.apply(provider_id)
            launch_claude()
            return {"ok": True, "message": "Claude 通道已切换并重启。", "backup_dir": backup}
        except Exception as error:
            if stopped:
                try:
                    launch_claude()
                except Exception:
                    pass
            return {"ok": False, "error": str(error)}
        finally:
            self._claude_switch_lock.release()

    # ---------- Windows Claude 页面翻译 ----------
    def open_claude_translation(self, data):
        """Attach the existing Codex page engine to Claude's Chat page."""
        if os.name != "nt":
            return {"ok": False, "error": "Claude 桌面翻译目前仅支持 Windows"}
        from claude_channels import OFFICIAL
        if self._claude_channels.list()["current_id"] != OFFICIAL:
            return {"ok": False, "error": "第三方供应商模式暂不支持页面翻译；请切换到官方连接"}
        options = {
            "provider_id": str(data.get("provider_id") or ""),
            "model": str(data.get("model") or ""),
            "translate_interface": bool(data.get("translate_interface", True)),
            "translate_messages": bool(data.get("translate_messages", True)),
            "translate_input": bool(data.get("translate_input", True)),
            "intercept_enter": bool(data.get("intercept_enter", self._load_translation_settings().get("intercept_enter", True))),
        }
        with self._claude_lock:
            if self._claude_starting:
                return {"ok": False, "error": "Claude 翻译正在接入，请稍候"}
            current = self._claude_page_bridge
            if current and self._claude_page_options == options and current.state()["attached"]:
                return {"ok": True, "message": "Claude 页面翻译已开启"}
            self._claude_generation += 1
            generation = self._claude_generation
            self._claude_page_bridge = None
            self._claude_page_options = options
            self._claude_last_error = ""
            self._claude_starting = True
        if current:
            current.stop()
        bridge = None
        try:
            from claude_page_loader import ClaudePageLoader, check_developer_permissions
            from claude_surface_translation import ClaudePageTranslationBridge
            check_developer_permissions()
            translator = self._create_surface_translator(options)
            bridge = ClaudePageTranslationBridge(translator,
                _resource_path("experiments", "codex_translation_overlay.js"),
                intercept_enter=options["intercept_enter"],
                translate_interface=options["translate_interface"],
                translate_input=options["translate_input"],
                translate_messages=options["translate_messages"])
            bridge.start()
            with self._claude_lock:
                if generation != self._claude_generation:
                    raise RuntimeError("Claude 翻译接入已取消")
                self._claude_page_bridge = bridge
            ClaudePageLoader(bridge, self._window, on_stage=self._trace_claude_launch).attach()
            with self._claude_lock:
                if generation != self._claude_generation:
                    raise RuntimeError("Claude 翻译接入已取消")
                self._claude_last_error = ""
            return {"ok": True, "message": "Claude 页面翻译已开启"}
        except Exception as error:
            if bridge:
                bridge.stop()
            with self._claude_lock:
                if generation == self._claude_generation:
                    self._claude_last_error = str(error)
            return {"ok": False, "error": str(error)}
        finally:
            with self._claude_lock:
                if generation == self._claude_generation:
                    self._claude_starting = False

    def _trace_claude_launch(self, stage):
        # Fixed stage names only: no prompts, provider configuration or capability tokens.
        try:
            path = os.path.join(core._app_dir(), "claude_launch.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + stage + "\n")
        except OSError:
            pass

    def launch_claude_hub(self, data):
        """Open Claude and attach the existing in-page translator."""
        self._trace_claude_launch("home-request")
        try:
            from claude_page_loader import launch_claude
            from claude_channels import OFFICIAL
            if self._claude_channels.list()["current_id"] != OFFICIAL:
                launch_claude()
                return {"ok": True, "message": "Claude 已打开当前供应商；第三方模式暂不支持页面翻译。"}
            launch_claude()
            self._trace_claude_launch("launcher-returned")
        except Exception as error:
            self._trace_claude_launch("launch-error-" + type(error).__name__)
            with self._claude_lock:
                self._claude_last_error = str(error)
            return {"ok": False, "error": str(error)}
        result = self.open_claude_translation(data)
        if result.get("ok"):
            self._trace_claude_launch("launch-attached")
            return {**result, "message": "Claude 已打开；页面翻译已开启。"}
        self._trace_claude_launch("launch-attach-failed")
        with self._claude_lock:
            self._claude_last_error = str(result.get("error", ""))
        return {**result, "error": "Claude 已打开，但翻译接入失败：" + str(result.get("error", ""))}

    def open_claude_page_translation(self, data):
        return self.open_claude_translation(data)

    def get_claude_translation_state(self):
        with self._claude_lock:
            state = self._claude_page_bridge.state() if self._claude_page_bridge else {"running": False, "attached": False}
            phase = "starting" if self._claude_starting else "attached" if state["attached"] else "disconnected" if state["running"] else "error" if self._claude_last_error else "stopped"
            message = {"starting": "正在接入 Claude 页面翻译…",
                       "attached": "Claude 页面翻译已开启",
                       "disconnected": state.get("error") or "翻译正在重连；返回 Claude 对话页面后自动恢复。",
                       "stopped": "Claude 翻译已关闭", "error": self._claude_last_error}[phase]
            return {**state, "phase": phase, "message": message}

    def stop_claude_translation(self):
        with self._claude_lock:
            self._claude_generation += 1
            self._claude_starting = False
            bridge, self._claude_page_bridge = self._claude_page_bridge, None
            self._claude_page_options = None
            self._claude_last_error = ""
        if bridge:
            bridge.stop()
        return {"ok": True}

    def stop_claude_page_translation(self):
        return self.stop_claude_translation()

    @staticmethod
    def _mutation_block_reason():
        state = core.codex_process_state()
        if state["state"] == "stopped":
            return ""
        if state["state"] == "unknown":
            return "无法确认 Codex 是否已退出；为保护会话数据，本次操作已停止。"
        labels = [f"{item['name']} (PID {item['pid']})" for item in state["processes"]]
        return "请先退出以下进程后再操作：" + "、".join(labels)

    # ---------- 社区实验开关 ----------
    @staticmethod
    def _community_experiments_file():
        return os.path.join(core._app_dir(), "community_experiments.json")

    @staticmethod
    def _community_experiment_defaults():
        # 首页是永久控制中心；普通功能默认可见，实验功能继续默认关闭。
        return {
            "channel_switching": True,
            "session_routing": False,
            "session_archive": True,
            "local_appearance": False,
            "surface_translation": False,
            "diagnostics": True,
        }

    def _load_community_experiments(self):
        settings = self._community_experiment_defaults()
        try:
            with open(self._community_experiments_file(), encoding="utf-8") as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                settings.update({key: bool(saved[key]) for key in settings if key in saved})
        except (OSError, json.JSONDecodeError):
            pass
        return settings

    def get_community_experiments(self):
        return self._load_community_experiments()

    def set_community_experiment(self, feature, enabled):
        defaults = self._community_experiment_defaults()
        if feature not in defaults:
            return {"ok": False, "error": "未知实验功能"}
        settings = self._load_community_experiments()
        if settings[feature] and not enabled:
            try:
                if feature == "surface_translation":
                    self.stop_surface_translation()
                elif feature == "local_appearance":
                    removed = self.remove_background()
                    if not removed.get("ok"):
                        return {"ok": False, "error": "无法恢复当前外观：" + removed.get("error", "未知错误")}
            except Exception as error:
                return {"ok": False, "error": f"无法关闭功能：{error}"}
        settings[feature] = bool(enabled)
        path = self._community_experiments_file()
        temporary = f"{path}.tmp-{os.getpid()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "features": settings}

    def get_feature_hub(self):
        features = self._load_community_experiments()
        return {
            "features": features,
            "configured": {
                "local_appearance": os.path.isfile(self._background_settings_file()),
                "surface_translation": os.path.isfile(self._translation_settings_file()),
            },
        }

    # ---------- 实验外观 ----------
    @staticmethod
    def _background_settings_file():
        return os.path.join(core._app_dir(), "background_experiment.json")

    @staticmethod
    def _background_defaults():
        return {
            "image_path": "", "codex_launch_path": "",
            "background_mask": 42, "panel_opacity": 58,
            "sidebar_opacity": 42, "toolbar_opacity": 28, "thread_header_opacity": 28,
            "card_opacity": 24,
            "blur": 6, "background_scale": 100, "background_position_x": 50,
            "background_position_y": 50, "background_height": 100,
            "media_fit": "cover", "background_scope": "whole",
            "projection_shape": "none", "projection_points": [],
            "projection_layers": [], "projection_mode": "scale",
            "gradient_enabled": False, "gradient_type": "linear",
            "gradient_color_start": "#5868df", "gradient_color_end": "#2bb984",
            "gradient_opacity": 28, "gradient_angle": 135,
            "border_opacity": 22, "corner_radius": 12,
            "titlebar_mode": "normal", "compact_diff": False,
            "animated_gif": False, "animated_mp4": False,
        }

    def _load_background_settings(self):
        defaults = self._background_defaults()
        try:
            with open(self._background_settings_file(), encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                return defaults
            # 兼容旧版的 {current, themes} 结构；主题数据不再参与公开版界面。
            current = saved.get("current", saved)
            if isinstance(current, dict):
                defaults.update({key: current[key] for key in defaults if key in current})
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def get_background_settings(self):
        result = self._load_background_settings()
        result["script_available"] = os.path.isfile(
            _resource_path("experiments", "codex_background_injector.ps1")
        )
        return result

    def choose_background_image(self):
        if not self._window:
            return {"ok": False, "error": "工具箱窗口尚未初始化"}
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("背景文件 (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.mp4)",),
            )
            return {"ok": True, "path": result[0] if result else ""}
        except Exception as e:
            return {"ok": False, "error": f"无法打开图片选择器：{e}"}

    def choose_codex_executable(self):
        """让用户显式选择用于背景实验的 Codex 桌面程序。"""
        if not self._window:
            return {"ok": False, "error": "工具箱窗口尚未初始化"}
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Codex 应用 (*.app)",) if sys.platform == "darwin" else ("Windows 程序 (*.exe)",),
            )
            return {"ok": True, "path": result[0] if result else ""}
        except Exception as e:
            return {"ok": False, "error": f"无法打开程序选择器：{e}"}

    @staticmethod
    def _find_pwsh():
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            bundled = os.path.abspath(os.path.join(os.path.dirname(sys.executable),
                                                   "..", "Resources", "powershell", "pwsh"))
            if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
                return bundled
        # 优先使用用户已安装并在 PATH 中可用的 PowerShell 7，避免把外观实验
        # 错误地绑定到某一台电脑的 Codex 运行时缓存目录。
        for command in ("pwsh.exe", "pwsh"):
            found = shutil.which(command)
            if found:
                return found
        candidates = []
        if sys.platform == "darwin":
            for path in ("/opt/homebrew/bin/pwsh", "/usr/local/bin/pwsh", "/usr/local/microsoft/powershell/7/pwsh"):
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
        for path in glob.glob(os.path.join(os.path.expanduser("~"), ".cache", "codex-runtimes", "**", "pwsh.exe"), recursive=True):
            if os.path.isfile(path):
                candidates.append(path)
        if candidates:
            return max(candidates, key=os.path.getmtime)
        raise RuntimeError("未找到 PowerShell 7（pwsh）。请安装 PowerShell 7 后再使用本地外观实验。")

    def _save_background_settings(self, settings):
        path = self._background_settings_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump({"version": 3, "current": settings},
                      f, ensure_ascii=False, indent=2)
        os.replace(temporary, path)

    @staticmethod
    def _validated_launch_path(value):
        path = str(value or "").strip()
        if not path:
            return ""
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
        if sys.platform == "darwin":
            if path.endswith(".app"):
                try:
                    with open(os.path.join(path, "Contents", "Info.plist"), "rb") as handle:
                        executable = plistlib.load(handle)["CFBundleExecutable"]
                    binary = os.path.join(path, "Contents", "MacOS", executable)
                except (OSError, KeyError, plistlib.InvalidFileException):
                    raise ValueError("指定的 Codex .app 无效")
            else:
                binary = path
                if ".app/Contents/MacOS/" not in path:
                    raise ValueError("请选择 Codex.app 或其中的桌面可执行文件")
            if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
                raise ValueError("Codex 桌面可执行文件不存在或不可执行")
        elif not path.lower().endswith(".exe") or not os.path.isfile(path):
            raise ValueError("指定的 Codex 启动程序不存在或不是 .exe 文件")
        return path

    @staticmethod
    def _prepare_mac_background(path):
        """Avoid Windows System.Drawing in the shared PowerShell injector."""
        if sys.platform != "darwin" or os.path.splitext(path)[1].lower() in {".gif", ".mp4"} or os.path.getsize(path) <= 500000:
            return path, False
        from PIL import Image, ImageOps
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1280, 1280))
        descriptor, target = tempfile.mkstemp(suffix=".jpg", prefix="codex-background-")
        os.close(descriptor)
        try:
            image.save(target, "JPEG", quality=75, optimize=True)
            while os.path.getsize(target) > 500000:
                image.thumbnail((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)))
                image.save(target, "JPEG", quality=65, optimize=True)
            return target, True
        except Exception:
            os.unlink(target)
            raise

    def _normalized_background_settings(self, data):
        defaults = self._background_defaults()
        result = {"image_path": os.path.abspath(str(data.get("image_path") or ""))}
        launch_path = self._validated_launch_path(data.get("codex_launch_path"))
        result["codex_launch_path"] = launch_path
        limits = {
            "background_mask": (0, 90), "panel_opacity": (0, 100),
            "sidebar_opacity": (0, 100), "toolbar_opacity": (0, 100),
            "thread_header_opacity": (0, 100),
            "card_opacity": (0, 100), "blur": (0, 20),
            "background_scale": (10, 160), "background_height": (10, 160),
            "background_position_x": (0, 100),
            "background_position_y": (0, 100), "border_opacity": (0, 100),
            "corner_radius": (0, 28), "gradient_opacity": (0, 80),
            "gradient_angle": (0, 360),
        }
        for key, (minimum, maximum) in limits.items():
            result[key] = max(minimum, min(maximum, int(data.get(key, defaults[key]))))
        result["compact_diff"] = bool(data.get("compact_diff", defaults["compact_diff"]))
        result["animated_gif"] = bool(data.get("animated_gif", defaults["animated_gif"]))
        result["animated_mp4"] = bool(data.get("animated_mp4", defaults["animated_mp4"]))
        result["titlebar_mode"] = data.get("titlebar_mode") if data.get("titlebar_mode") in {
            "compact", "normal", "topbar", "hidden"
        } else defaults["titlebar_mode"]
        result["media_fit"] = data.get("media_fit") if data.get("media_fit") in {
            "cover", "contain"
        } else defaults["media_fit"]
        result["background_scope"] = data.get("background_scope") if data.get("background_scope") in {
            "whole", "content", "sidebar"
        } else defaults["background_scope"]
        result["projection_mode"] = data.get("projection_mode") if data.get("projection_mode") in {
            "scale", "crop"
        } else defaults["projection_mode"]
        result["gradient_enabled"] = bool(data.get("gradient_enabled", defaults["gradient_enabled"]))
        result["gradient_type"] = data.get("gradient_type") if data.get("gradient_type") in {
            "linear", "radial"
        } else defaults["gradient_type"]
        for key in ("gradient_color_start", "gradient_color_end"):
            color = str(data.get(key, defaults[key]))
            result[key] = color.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else defaults[key]
        result["projection_shape"] = data.get("projection_shape") if data.get("projection_shape") in {
            "none", "rect", "circle", "line", "freehand"
        } else defaults["projection_shape"]
        raw_points = data.get("projection_points")
        points = []
        if isinstance(raw_points, list):
            for point in raw_points[:80]:
                if not isinstance(point, dict):
                    continue
                try:
                    x, y = float(point.get("x")), float(point.get("y"))
                except (TypeError, ValueError):
                    continue
                points.append({"x": max(0.0, min(1.0, x)), "y": max(0.0, min(1.0, y))})
        minimum_points = 2
        if result["projection_shape"] != "none" and len(points) < minimum_points:
            result["projection_shape"] = "none"
            points = []
        result["projection_points"] = points
        layers = []
        raw_layers = data.get("projection_layers")
        if isinstance(raw_layers, list):
            for raw_layer in raw_layers[:20]:
                if not isinstance(raw_layer, dict):
                    continue
                shape = raw_layer.get("type")
                if shape not in {"rect", "circle", "line", "freehand"}:
                    continue
                raw_layer_points = raw_layer.get("points")
                normalized_points = []
                if isinstance(raw_layer_points, list):
                    for point in raw_layer_points[:80]:
                        if not isinstance(point, dict):
                            continue
                        try:
                            x, y = float(point.get("x")), float(point.get("y"))
                        except (TypeError, ValueError):
                            continue
                        normalized_points.append({"x": max(0.0, min(1.0, x)), "y": max(0.0, min(1.0, y))})
                if len(normalized_points) >= 2:
                    # 没有 enabled 字段的旧设置视为已选，保持旧版背景显示效果。
                    try:
                        width = max(0.02, min(0.24, float(raw_layer.get("width", 0.09))))
                    except (TypeError, ValueError):
                        width = 0.09
                    layers.append({
                        "type": shape,
                        "enabled": bool(raw_layer.get("enabled", True)),
                        "width": width,
                        "points": normalized_points,
                    })
        # 兼容此前保存的单形状设置，下一次应用后会以图层格式保存。
        if not layers and result["projection_shape"] != "none":
            layers = [{
                "type": result["projection_shape"], "enabled": True,
                "width": 0.09, "points": result["projection_points"],
            }]
        result["projection_layers"] = layers
        return result

    @staticmethod
    def _format_background_error(detail):
        """将 PowerShell 错误转换为适合界面展示的简短中文提示。"""
        clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", detail or "")
        clean = " ".join(clean.replace("\r", " ").replace("\n", " ").split())
        if "没有开放调试端口" in clean or "拒绝了 Chromium 调试参数" in clean:
            return (
                "Codex 在等待时间内未开放本机调试端口，背景没有被注入，也没有修改任何文件或数据。"
                "工具箱已自动尝试重新附加一次；请确认 Codex 未被托盘残留进程占用后再启动。"
            )
        if "等待 Codex 调试接口响应超时" in clean:
            return "Codex 已开启调试端口，但没有及时响应背景注入。请等待窗口完全加载后重试。"
        if "没有找到可注入的 Codex 页面" in clean:
            return "Codex 调试端口已开启，但页面尚未完全加载。请稍等片刻后重试。"
        return clean[-300:] or "背景实验脚本执行失败，请稍后重试。"

    @staticmethod
    def _should_retry_background_attach(detail):
        """首次启动后端口或主页面较晚就绪时，只附加重试一次。"""
        return any(marker in (detail or "") for marker in (
            "没有开放调试端口", "没有找到可注入的 Codex 页面",
        ))

    @staticmethod
    def _validate_background_media(settings):
        image_path = settings["image_path"]
        if not os.path.isfile(image_path):
            return "请先选择有效的本地背景图片"
        extension = os.path.splitext(image_path)[1].lower()
        if extension not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"):
            return "仅支持 PNG、JPG、JPEG、WEBP、GIF 或 MP4 背景"
        if extension == ".gif":
            if not settings["animated_gif"]:
                return "GIF 动态背景默认关闭，请勾选“启用动态 GIF 背景”后再应用"
            if os.path.getsize(image_path) > 3 * 1024 * 1024:
                return "动态 GIF 请控制在 3 MB 以内，避免占用过高或调试注入失败"
        if extension == ".mp4":
            if not settings["animated_mp4"]:
                return "MP4 动态背景默认关闭，请勾选“启用本地 MP4 背景”后再应用"
            if os.path.getsize(image_path) > 30 * 1024 * 1024:
                return "MP4 请控制在 30 MB 以内；为兼容 Codex 的本地安全策略，视频会临时载入当前窗口内存"
        return ""

    def save_background_settings(self, data):
        try:
            settings = self._normalized_background_settings(data)
        except (TypeError, ValueError):
            return {"ok": False, "error": "外观参数格式不正确"}
        error = self._validate_background_media(settings)
        if error:
            return {"ok": False, "error": error}
        self._save_background_settings(settings)
        return {"ok": True, "message": "背景设置已保存；下次从首页启动 Codex 时生效。"}

    def apply_background(self, data, restart=True):
        if not self._load_community_experiments()["local_appearance"]:
            return {"ok": False, "error": "请先在“本地外观实验”中确认并启用此非官方功能"}
        try:
            settings = self._normalized_background_settings(data)
        except (TypeError, ValueError):
            return {"ok": False, "error": "外观参数格式不正确"}
        image_path = settings["image_path"]
        media_error = self._validate_background_media(settings)
        if media_error:
            return {"ok": False, "error": media_error}
        extension = os.path.splitext(image_path)[1].lower()
        temporary_image = ""
        try:
            image_path, prepared = self._prepare_mac_background(image_path)
            if prepared:
                temporary_image = image_path
            script = _resource_path("experiments", "codex_background_injector.ps1")
            if not os.path.isfile(script):
                raise RuntimeError("工具箱内置的背景实验脚本缺失，请重新安装工具箱")
            command = [self._find_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                       "-Port", str(CODEX_EXPERIMENT_PORT)]
            if settings["codex_launch_path"]:
                command += ["-CodexExecutable", settings["codex_launch_path"]]
            if extension == ".mp4":
                command += ["-VideoPath", image_path, "-AllowVideo:$true"]
            else:
                command += ["-ImagePath", image_path, "-AllowVideo:$false"]
            command += ["-BackgroundMask", str(settings["background_mask"]),
                       "-PanelOpacity", str(settings["panel_opacity"]),
                       "-SidebarOpacity", str(settings["sidebar_opacity"]),
                       "-ToolbarOpacity", str(settings["toolbar_opacity"]),
                       "-ThreadHeaderOpacity", str(settings["thread_header_opacity"]),
                       "-CardOpacity", str(settings["card_opacity"]),
                       "-Blur", str(settings["blur"]),
                       "-BackgroundScale", str(settings["background_scale"]),
                       "-BackgroundHeight", str(settings["background_height"]),
                       "-BackgroundPositionX", str(settings["background_position_x"]),
                       "-BackgroundPositionY", str(settings["background_position_y"]),
                       "-MediaFit", settings["media_fit"],
                       "-BackgroundScope", settings["background_scope"],
                       "-ProjectionShape", settings["projection_shape"],
                       "-ProjectionPointsJson", json.dumps(settings["projection_points"], separators=(",", ":")),
                       "-ProjectionLayersJson", json.dumps(settings["projection_layers"], separators=(",", ":")),
                       "-ProjectionMode", settings["projection_mode"],
                       "-GradientEnabled:$" + str(settings["gradient_enabled"]).lower(),
                       "-GradientType", settings["gradient_type"],
                       "-GradientColorStart", settings["gradient_color_start"],
                       "-GradientColorEnd", settings["gradient_color_end"],
                       "-GradientOpacity", str(settings["gradient_opacity"]),
                       "-GradientAngle", str(settings["gradient_angle"]),
                       "-BorderOpacity", str(settings["border_opacity"]),
                       "-CornerRadius", str(settings["corner_radius"]),
                       "-TitlebarMode", settings["titlebar_mode"],
                       "-CompactDiff:$" + str(settings["compact_diff"]).lower(),
                       "-AllowAnimatedGif:$" + str(settings["animated_gif"]).lower()]
            if restart:
                command.append("-Restart")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=120, creationflags=flags)
            detail = (result.stderr or result.stdout or "背景脚本执行失败").strip()
            if result.returncode and restart and self._should_retry_background_attach(detail):
                # 第一次调用已负责唤起 Codex；这里不再重启，只等待主页面就绪后附加。
                retry_command = [part for part in command if part != "-Restart"]
                retry_command += ["-AttachWaitSeconds", "30"]
                result = subprocess.run(retry_command, capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=40, creationflags=flags)
                if not result.returncode:
                    detail = "Codex 首次启动较慢，已自动重试并完成背景应用。\n" + (
                        result.stdout or "背景已应用"
                    ).strip()
                else:
                    detail = (result.stderr or result.stdout or "背景脚本执行失败").strip()
            if result.returncode:
                return {"ok": False, "error": self._format_background_error(detail)}
            self._save_background_settings(settings)
            return {"ok": True, "message": detail or "背景已应用"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "等待 Codex 重启超时，请稍后再试"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        finally:
            if temporary_image:
                os.unlink(temporary_image)

    def remove_background(self):
        try:
            script = _resource_path("experiments", "codex_background_injector.ps1")
            result = subprocess.run(
                [self._find_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Remove"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode:
                return {"ok": False, "error": (result.stderr or result.stdout or "恢复失败").strip()[-300:]}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Codex 表层翻译 ----------
    @staticmethod
    def _translation_settings_file():
        return os.path.join(core._app_dir(), "surface_translation.json")

    @staticmethod
    def _translation_defaults():
        return {
            "provider_id": "", "model": "", "codex_launch_path": "",
            "translate_interface": True, "translate_messages": True,
            "translate_input": True, "intercept_enter": True, "port": CODEX_EXPERIMENT_PORT,
        }

    def _load_translation_settings(self):
        settings = self._translation_defaults()
        try:
            with open(self._translation_settings_file(), encoding="utf-8") as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                settings.update({key: saved[key] for key in settings if key in saved})
                if int(saved.get("version", 1) or 1) < 2:
                    settings["translate_messages"] = True
        except (OSError, json.JSONDecodeError):
            pass
        return settings

    def _normalized_translation_settings(self, data):
        data = data if isinstance(data, dict) else {}
        result = {
            "provider_id": str(data.get("provider_id") or "").strip(),
            "model": str(data.get("model") or "").strip(),
            "translate_interface": bool(data.get("translate_interface", True)),
            "translate_messages": bool(data.get("translate_messages", True)),
            "translate_input": bool(data.get("translate_input", True)),
            "intercept_enter": bool(data.get("intercept_enter", self._load_translation_settings().get("intercept_enter", True))),
        }
        launch_path = self._validated_launch_path(data.get("codex_launch_path"))
        result["codex_launch_path"] = launch_path
        # 背景和翻译必须附加到同一个 Codex 调试会话。端口不是用户设置，
        # 忽略旧配置中的其他端口，避免两个功能互相重启并让覆盖层掉线。
        result["port"] = CODEX_EXPERIMENT_PORT
        if not any((result["translate_interface"], result["translate_messages"], result["translate_input"])):
            raise ValueError("请至少勾选一项需要翻译的内容")
        return result

    def _save_translation_settings(self, settings):
        path = self._translation_settings_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.tmp-{os.getpid()}"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"version": 2, **settings}, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)

    def get_translation_settings(self):
        settings = self._load_translation_settings()
        providers = []
        for provider in core.load_providers():
            if not isinstance(provider, dict):
                continue
            if provider.get("id") == core.OFFICIAL_ID:
                continue
            base_url = provider.get("upstream_base") if provider.get("proxy") else provider.get("base_url")
            ready = bool(base_url and provider.get("model") and (
                provider.get("api_key") or str(base_url).startswith(("http://127.0.0.1", "http://localhost"))
            ))
            providers.append({
                "id": provider.get("id", ""), "name": provider.get("name", provider.get("id", "")),
                "model": provider.get("model", ""), "ready": ready,
            })
        controller = self._translation_controller
        return {**settings, "providers": providers, "running": controller is not None,
                "connected": bool(controller and controller.is_connected),
                "last_error": self._translation_last_error}

    def _create_surface_translator(self, settings):
        if not (settings["translate_messages"] or settings["translate_input"]):
            return None
        provider = next((item for item in core.load_providers()
                         if isinstance(item, dict) and item.get("id") == settings["provider_id"]), None)
        if not provider or provider.get("id") == core.OFFICIAL_ID:
            raise ValueError(
                "聊天正文或输入框翻译需要使用 AI。请先到“通道切换”添加一个可用的 AI 服务，"
                "然后回到这里选择它。只翻译固定界面则不需要选择。"
            )
        return OpenAICompatibleTranslator(
            provider,
            settings["model"],
            os.path.join(core._app_dir(), "surface_translation_cache.sqlite"),
        )

    def save_surface_translation_settings(self, data):
        try:
            settings = self._normalized_translation_settings(data)
            self._create_surface_translator(settings)
            self._save_translation_settings(settings)
            return {"ok": True, "message": "翻译设置已保存；下次从首页启动 Codex 时生效。"}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    @staticmethod
    def _translation_launcher_error(detail):
        clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", detail or "")
        if "ParserError" in clean or "意外的标记" in clean:
            return "工具箱里的启动文件有语法错误，请安装最新修正版"
        if "Codex 正在普通模式运行" in clean:
            return "当前 Codex 是普通模式运行，无法附加功能。请回到首页使用统一的“启动 Codex”按钮。"
        if "未开放临时调试端口" in clean:
            return "Codex 已经打开，但工具箱没有连接成功。请完全退出 Codex 后再试一次。"
        return " ".join(clean.replace("\r", " ").replace("\n", " ").split())[-400:] or "无法启动表层翻译"

    def _translation_failed(self, detail):
        clean = " ".join(str(detail or "翻译失败").replace("\r", " ").replace("\n", " ").split())[:1000]
        self._translation_last_error = clean[:400]
        path = os.path.join(core._app_dir(), "surface_translation_errors.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._translation_log_lock:
            if os.path.isfile(path) and os.path.getsize(path) >= 1024 * 1024:
                archived = path + ".previous"
                if os.path.exists(archived):
                    os.remove(archived)
                os.replace(path, archived)
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {clean}\n")

    @staticmethod
    def _existing_codex_debug_port():
        """Return a live Codex CDP port, never an unverified stale marker."""
        candidates = []
        try:
            with open(os.path.join(core._app_dir(), "codex_debug_port"), encoding="utf-8") as handle:
                candidates.append(int(handle.read().strip()))
        except (OSError, ValueError):
            pass
        candidates.append(CODEX_EXPERIMENT_PORT)
        for port in dict.fromkeys(candidates):
            if not 1024 <= port <= 65535:
                continue
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                    if select_codex_page_target(json.load(response)):
                        return port
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    def resume_saved_surface_translation(self):
        """Reattach an enabled overlay to an already-running Codex without restarting it."""
        if not self._load_community_experiments()["surface_translation"]:
            return
        if not os.path.isfile(self._translation_settings_file()) or self._existing_codex_debug_port() is None:
            return
        result = self.apply_surface_translation(self._load_translation_settings(), restart=False)
        if not result.get("ok"):
            self._translation_failed(result.get("error", "翻译重连失败"))

    def apply_surface_translation(self, data, restart=True):
        if not self._load_community_experiments()["surface_translation"]:
            return {"ok": False, "error": "请先勾选“允许工具箱显示中文翻译”"}
        try:
            settings = self._normalized_translation_settings(data)
            translator = self._create_surface_translator(settings)
            launcher = _resource_path("experiments", "codex_translation_launcher.ps1")
            overlay = _resource_path("experiments", "codex_translation_overlay.js")
            if not os.path.isfile(launcher) or not os.path.isfile(overlay):
                return {"ok": False, "error": "翻译功能需要的文件不完整，请重新安装工具箱"}
            port = settings["port"] if restart else (self._existing_codex_debug_port() or settings["port"])
            command = [self._find_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launcher,
                       "-Port", str(port)]
            if settings["codex_launch_path"]:
                command += ["-CodexExecutable", settings["codex_launch_path"]]
            if restart:
                command.append("-Restart")
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=100, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            detail = (result.stdout or "") + "\n" + (result.stderr or "")
            if result.returncode:
                return {"ok": False, "error": self._translation_launcher_error(detail)}
            marker = next((line for line in detail.splitlines() if line.startswith("CODEX_TOOLBOX_TARGET=")), "")
            if not marker:
                return {"ok": False, "error": "Codex 已经打开，但工具箱暂时无法把中文显示到页面上"}
            target_payload = json.loads(marker.split("=", 1)[1])
            target = select_codex_page_target(target_payload)
            websocket_url = target.get("webSocketDebuggerUrl") if target else ""
            if not websocket_url:
                return {"ok": False, "error": "工具箱没有连接上 Codex 页面，请关闭 Codex 后重新点击第一个启动按钮"}
            with open(overlay, encoding="utf-8") as handle:
                overlay_source = handle.read()
            self.stop_surface_translation()
            controller = SurfaceTranslationController(
                websocket_url, overlay_source,
                {"translateInterface": settings["translate_interface"],
                 "translateMessages": settings["translate_messages"],
                 "translateInput": settings["translate_input"]},
                translator, self._translation_failed,
            )
            controller.start()
            self._translation_controller = controller
            self._translation_last_error = ""
            self._save_translation_settings(settings)
            return {"ok": True, "message": "翻译浮窗已经显示，可拖动顶部把手调整位置。当前保持原文，点击“翻译页面”后开始翻译。"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "等待 Codex 打开超时，请确认 Codex 能正常启动后再试"}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def launch_codex_hub(self):
        """Restart Codex once, then attach every enabled launch-time feature."""
        features = self._load_community_experiments()
        appearance_enabled = bool(features.get("local_appearance"))
        translation_enabled = bool(features.get("surface_translation"))
        background_settings = self._load_background_settings()
        translation_settings = self._load_translation_settings()

        if appearance_enabled:
            try:
                background_settings = self._normalized_background_settings(background_settings)
            except (TypeError, ValueError):
                return {"ok": False, "error": "已启用背景功能，但保存的外观参数无效。请先到“实验外观”重新保存。"}
            media_error = self._validate_background_media(background_settings)
            if media_error:
                return {"ok": False, "error": "已启用背景功能：" + media_error}
        try:
            translator = None
            if translation_enabled:
                translation_settings = self._normalized_translation_settings(translation_settings)
                translator = self._create_surface_translator(translation_settings)
        except Exception as error:
            return {"ok": False, "error": "已启用翻译功能：" + str(error)}

        launch_paths = {
            path for path in (
                background_settings.get("codex_launch_path", "") if appearance_enabled else "",
                translation_settings.get("codex_launch_path", "") if translation_enabled else "",
            ) if path
        }
        if len(launch_paths) > 1:
            return {"ok": False, "error": "背景和翻译保存了不同的 Codex 程序路径，请统一后再从首页启动。"}
        launch_path = next(iter(launch_paths), "")
        try:
            launcher = _resource_path("experiments", "codex_translation_launcher.ps1")
            command = [self._find_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launcher,
                       "-Port", str(CODEX_EXPERIMENT_PORT), "-Restart"]
            if launch_path:
                command += ["-CodexExecutable", launch_path]
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=100, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            detail = (result.stdout or "") + "\n" + (result.stderr or "")
            if result.returncode:
                return {"ok": False, "error": self._translation_launcher_error(detail)}
            marker = next((line for line in detail.splitlines() if line.startswith("CODEX_TOOLBOX_TARGET=")), "")
            if not marker:
                return {"ok": False, "error": "Codex 已启动，但工具箱没有找到主页面。"}
            target_payload = json.loads(marker.split("=", 1)[1])
            target = select_codex_page_target(target_payload)
            if not target or not target.get("webSocketDebuggerUrl"):
                return {"ok": False, "error": "Codex 已启动，但本机调试页面尚未就绪。"}

            enabled = []
            if appearance_enabled:
                applied = self.apply_background(background_settings, restart=False)
                if not applied.get("ok"):
                    return {"ok": False, "error": "Codex 已启动，但背景应用失败：" + applied.get("error", "未知错误")}
                enabled.append("背景")
            if translation_enabled:
                with open(_resource_path("experiments", "codex_translation_overlay.js"), encoding="utf-8") as handle:
                    overlay_source = handle.read()
                self.stop_surface_translation()
                controller = SurfaceTranslationController(
                    target["webSocketDebuggerUrl"], overlay_source,
                    {"translateInterface": translation_settings["translate_interface"],
                     "translateMessages": translation_settings["translate_messages"],
                     "translateInput": translation_settings["translate_input"]},
                    translator, self._translation_failed,
                )
                controller.start()
                self._translation_controller = controller
                self._translation_last_error = ""
                enabled.append("翻译")
            else:
                self.stop_surface_translation()
            feature_text = "、".join(enabled) if enabled else "基础模式"
            return {"ok": True, "message": f"Codex 已启动；已加载：{feature_text}。"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "等待 Codex 启动超时，请确认 Codex 能正常打开后重试。"}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def stop_surface_translation(self):
        controller = self._translation_controller
        self._translation_controller = None
        if controller:
            controller.stop()
        return {"ok": True, "message": "翻译已经关闭，现在显示的是 Codex 原来的英文界面。"}

    # ---------- 状态 ----------
    def _saved_request_key(self, base_url, api_key, provider_id):
        if api_key or not provider_id:
            return api_key or ""
        provider = next((p for p in core.load_providers() if p.get("id") == provider_id), {})
        if provider.get("api_key") and (base_url or "").rstrip("/") != provider.get("base_url", "").rstrip("/"):
            raise ValueError("地址已修改，请重新填写 API Key；已保存的密钥不会发送到新地址。")
        return provider.get("api_key", "")

    def get_models(self, base_url, api_key, provider_id=None):
        """从 OpenAI 兼容 API 的 /models 拉取真实模型列表"""
        import urllib.request
        try:
            if provider_id == core.OFFICIAL_ID:
                client = core._CodexAppServer(core._find_codex_executable(), timeout=15)
                try:
                    models, cursor = [], None
                    while True:
                        result = client.request("model/list", {"cursor": cursor} if cursor else {}) or {}
                        models.extend({"id": item.get("model") or item.get("id")} for item in result.get("data", []) if item.get("model") or item.get("id"))
                        cursor = result.get("nextCursor")
                        if not cursor:
                            break
                    return {"ok": True, "models": models}
                finally:
                    client.close()
            url = (base_url or "").rstrip("/") + "/models"
            if not url.startswith(("http://", "https://")):
                return {"ok": False, "error": "Base URL 无效"}
            api_key = self._saved_request_key(base_url, api_key, provider_id)
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [
                {"id": item["id"][:256], "created": item.get("created")}
                for item in data.get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
            ]
            # 过滤非对话模型（语音/图像/视频/嵌入等）
            skip = ("tts", "asr", "image", "video", "embedding", "speech",
                    "audio", "ocr", "realtime", "transcribe", "embed")
            models = [item for item in models if not any(k in item["id"].lower() for k in skip)]
            # 服务端若提供创建时间，按新到旧展示；否则保留服务端顺序，避免错误猜测型号新旧。
            if any(isinstance(item.get("created"), (int, float)) for item in models):
                models.sort(key=lambda item: item.get("created") or 0, reverse=True)
            return {"ok": True, "models": models}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def detect_protocol(self, base_url, api_key, model, provider_id=None):
        """主动探测上游是否接受 Responses 请求，不保存也不切换供应商。"""
        import urllib.error
        import urllib.request

        base = (base_url or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            return {"ok": False, "error": "Base URL 无效"}
        try:
            api_key = self._saved_request_key(base_url, api_key, provider_id)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        if not model:
            return {"ok": False, "error": "请先填写或获取一个模型"}

        # 只有用户在界面中主动点击才会执行。max_output_tokens 尽量降低探测成本。
        payload = json.dumps({
            "model": model,
            "input": "OK",
            "max_output_tokens": 1,
        }).encode("utf-8")
        request = urllib.request.Request(
            base + "/responses", data=payload, method="POST",
            headers={
                **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
                "Content-Type": "application/json",
                "User-Agent": f"CodexToolbox/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = getattr(response, "status", 200)
                raw_body = response.read(256 * 1024)
            try:
                response_body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {
                    "ok": False,
                    "error": f"HTTP {status} 但响应不是 Responses JSON；不会自动修改设置。",
                }
            if (not isinstance(response_body, dict)
                    or response_body.get("object") != "response"
                    or not isinstance(response_body.get("id"), str)):
                return {
                    "ok": False,
                    "error": f"HTTP {status} 但响应结构不是原生 Responses；不会自动修改设置。",
                }
            return {
                "ok": True,
                "proxy": False,
                "message": f"服务端接受 /responses（HTTP {status}），建议直接连接，不需要本地翻译。",
            }
        except urllib.error.HTTPError as error:
            if error.code in (404, 405, 501):
                return {
                    "ok": True,
                    "proxy": True,
                    "message": f"服务端不提供 /responses（HTTP {error.code}），建议启用本地翻译。",
                }
            tips = {
                400: "请求被拒绝，可能是模型不支持或参数要求不同；不会自动修改设置。",
                401: "API Key 无效或已过期；不会自动修改设置。",
                403: "账号、地域或权限受限；不会自动修改设置。",
                429: "请求过多或额度不足；不会自动修改设置。",
            }
            return {"ok": False, "error": f"HTTP {error.code}：{tips.get(error.code, '服务端拒绝探测；不会自动修改设置。')}"}
        except Exception as error:
            return {"ok": False, "error": f"协议检测失败：{str(error)[:160]}"}

    def get_presets(self):
        return {
            "presets": [
                {**p, "models": p.get("models") or core.MODEL_PRESETS} for p in core.PRESETS
            ]
        }

    def get_status(self):
        prov = core.read_provider()
        names = {p["id"]: p["name"] for p in core.load_providers()}
        process_state = core.codex_process_state()
        return {
            "provider": prov,
            "provider_name": names.get(prov, prov),
            "model": core.read_model(),
            "reasoning_effort": core.read_reasoning_effort(),
            "codex_running": process_state["state"] != "stopped",
            "process_state": process_state,
            "proxy_running": self.proxy_server is not None,
            "proxy_url": self.proxy_url,
            "migrated_dashscope": self.migrated_dashscope,
        }

    def get_diagnostics(self):
        """仅返回本机状态摘要，不返回 API Key、会话内容或数据库记录。"""
        data_dir = core._app_dir()
        try:
            backups = core.backup_inventory()
        except OSError:
            backups = {"count": 0, "bytes": 0}
        process_state = core.codex_process_state()
        legacy_artifacts = self._legacy_artifacts()
        return {
            "data_dir": data_dir,
            "backup_dir": core.BAK_DIR,
            "provider_count": len(core.load_providers()),
            "route_count": len(core.load_thread_routes()),
            "backup_count": backups["count"],
            "backup_bytes": backups["bytes"],
            "backup_policy": f"{core.BACKUP_MAX_AGE_DAYS} 天 / {core.BACKUP_MAX_ENTRIES} 项 / {core.BACKUP_MAX_BYTES // (1024 * 1024)} MB",
            "codex_running": process_state["state"] != "stopped",
            "process_state": process_state,
            "legacy_artifact_count": len(legacy_artifacts),
            "community_experiments": self._load_community_experiments(),
            "appearance_saved": os.path.isfile(self._background_settings_file()),
        }

    @staticmethod
    def _legacy_artifacts():
        skin_dir = os.path.join(core._app_dir(), "skin")
        candidates = [os.path.join(skin_dir, "install_codex_skin.ps1"), os.path.join(skin_dir, "skin.log")]
        candidates.extend(glob.glob(os.path.join(skin_dir, "app.asar.bak_*")))
        return [path for path in candidates if os.path.isfile(path)]

    def quarantine_legacy_artifacts(self):
        """Move obsolete app.asar patching files to a recoverable quarantine directory."""
        artifacts = self._legacy_artifacts()
        if not artifacts:
            return {"ok": True, "count": 0, "message": "没有发现旧版改包文件。"}
        destination = os.path.join(core._app_dir(), "legacy_quarantine", time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(destination, exist_ok=False)
        moved = []
        try:
            for source in artifacts:
                target = os.path.join(destination, os.path.basename(source))
                shutil.move(source, target)
                moved.append((source, target))
        except Exception as error:
            for source, target in reversed(moved):
                if os.path.isfile(target) and not os.path.exists(source):
                    shutil.move(target, source)
            return {"ok": False, "error": f"隔离失败，已回滚：{str(error)[:160]}"}
        return {
            "ok": True, "count": len(moved), "quarantine_dir": destination,
            "message": f"已隔离 {len(moved)} 个旧版文件；需要时可从隔离目录恢复。",
        }

    # ---------- 供应商 ----------
    def export_provider_css_example(self):
        if not self._window:
            return {"ok": False, "error": "工具箱窗口尚未初始化"}
        try:
            from provider_style import EXAMPLE_CSS
            paths = self._window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename="provider-card.css",
                file_types=("CSS 样式 (*.css)",),
            )
            if not paths:
                return {"ok": True, "cancelled": True}
            path = paths if isinstance(paths, str) else paths[0]
            if os.path.exists(path):
                with open(path, "rb") as handle:
                    handle.read()
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(EXAMPLE_CSS)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": f"无法保存 CSS 示例：{error}"}

    def get_providers(self):
        provs = core.load_providers()
        current = core.read_provider()
        active_official_model = core.read_model() if current == core.OFFICIAL_ID else ""
        out = []
        for p in provs:
            item = {
                "id": p["id"],
                "name": p["name"],
                "kind": p.get("kind", "relay"),
                "model": active_official_model if p["id"] == core.OFFICIAL_ID and active_official_model and not p.get("model_explicit") else p.get("model", ""),
                "reasoning_effort": p.get("reasoning_effort", ""),
                "base_url": p.get("base_url", ""),
                "proxy": bool(p.get("proxy")),
                "wire_api": p.get("wire_api", "responses"),
                "upstream_base": p.get("upstream_base", ""),
                "has_api_key": bool(p.get("api_key")),
                "card_css": p.get("card_css", ""),
                "secret_unavailable": bool(p.get("_unreadable_secrets")),
                "current": p["id"] == current,
            }
            out.append(item)
        return out

    def save_provider(self, data):
        with self._provider_lock:
            return self._save_provider(data)

    def _save_provider(self, data):
        provs = core.load_providers()
        pid = data.get("id") or "provider_" + secrets.token_hex(8)
        old = next((x for x in provs if x.get("id") == pid), {})
        p = {
            "id": pid,
            "name": data.get("name", ""),
            "kind": "official" if pid == "openai" else (data.get("kind") or "relay"),
            "base_url": data.get("base_url", ""),
            # 编辑时留空表示保留旧 Key，后端永不把明文 Key 回传给页面。
            "api_key": data.get("api_key") or old.get("api_key", ""),
            "model": data.get("model", ""),
            "reasoning_effort": data.get("reasoning_effort", ""),
            "wire_api": data.get("wire_api", "responses"),
            "requires_openai_auth": False,
            "card_css": stored_css(data.get("card_css", old.get("card_css", ""))),
        }
        if old.get("_unreadable_secrets"):
            p["_unreadable_secrets"] = dict(old["_unreadable_secrets"])
        if data.get("proxy"):
            p["proxy"] = True
            p["upstream_base"] = data.get("upstream_base", "https://api.deepseek.com/v1")
            p["proxy_token"] = old.get("proxy_token") or ("" if any(key.startswith("proxy_token_") for key in old.get("_unreadable_secrets", {})) else secrets.token_urlsafe(32))
        p = core.normalize_provider(p)
        if pid == core.OFFICIAL_ID:
            p["model_explicit"] = bool(old.get("model_explicit") or p.get("model") != old.get("model"))
        if p["kind"] == "official":
            p["base_url"] = ""
            p["api_key"] = ""
            p.pop("proxy", None)
            p.pop("upstream_base", None)
        if pid == "openai":
            for i, x in enumerate(provs):
                if x["id"] == "openai":
                    provs[i] = p
                    break
        else:
            found = False
            for i, x in enumerate(provs):
                if x["id"] == pid:
                    provs[i] = p
                    found = True
                    break
            if not found:
                provs.append(p)
        core.save_providers(provs)
        return {"ok": True, "id": pid}

    def delete_provider(self, pid):
        with self._provider_lock:
            return self._delete_provider(pid)

    def _delete_provider(self, pid):
        if pid == "openai":
            return {"ok": False, "error": "官方供应商不能删除"}
        if core.read_provider() == pid:
            return {
                "ok": False,
                "error": "不能删除当前全局供应商。请先切换到其他 API 或官方登录，再删除。",
            }
        dependencies = core.provider_dependencies(pid)
        if dependencies["unknown"]:
            return {"ok": False, "error": "无法确认会话依赖关系，已取消删除以避免损坏路由。"}
        affected = sorted(set(dependencies["route_ids"] + dependencies["state_ids"]))
        if affected:
            return {
                "ok": False,
                "error": f"仍有 {len(affected)} 个会话使用该供应商。请先把这些会话切换到其他通道。",
                "dependency_count": len(affected),
            }
        provs = core.load_providers()
        if not any(p.get("id") == pid for p in provs):
            return {"ok": False, "error": "供应商不存在"}
        provs = [p for p in provs if p["id"] != pid]
        core.save_providers(provs)
        return {"ok": True}

    def switch_provider(self, pid):
        if not self._provider_lock.acquire(blocking=False):
            return {"ok": False, "error": "通道配置正在更新，请稍候"}
        try:
            return self._switch_provider(pid)
        finally:
            self._provider_lock.release()

    def _switch_provider(self, pid):
        provs = core.load_providers()
        p = next((x for x in provs if x["id"] == pid), None)
        if not p:
            return {"ok": False, "error": f"供应商 {pid} 不存在"}
        try:
            proxy_url = self._ensure_proxy(p) if p.get("proxy") else None
            # 全局默认只决定之后新建的会话。不在这里恢复或改写已有会话，
            # 避免不同版本的官方 app-server 无法持久化会话设置时，反过来
            # 阻止正常的全局切换。
            # apply_provider writes the provider block and default together, once.
            name = core.apply_provider(pid, proxy_url)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": name, "proxy": bool(p.get("proxy")),
                "existing_sessions_unchanged": True}

    def diagnose_provider(self, pid):
        p = next((x for x in core.load_providers() if x.get("id") == pid), None)
        if not p:
            return {"ok": False, "error": "供应商不存在"}
        issues = []
        if pid != core.OFFICIAL_ID and not p.get("base_url"):
            issues.append("缺少 Base URL")
        from urllib.parse import urlparse
        local_service = urlparse(p.get("base_url", "")).hostname in {"localhost", "127.0.0.1", "::1"}
        if pid != core.OFFICIAL_ID and not p.get("api_key") and not local_service:
            issues.append("缺少 API Key")
        if core._is_dashscope_provider(p):
            if p.get("proxy") or "127.0.0.1" in p.get("base_url", ""):
                issues.append("千问被错误配置为本地代理")
            route = "DashScope 官方直连 · 原生 Responses"
            advice = "配置正确，不需要本地翻译代理；若使用系统代理，请按你的代理软件规则处理本机回环地址。"
        elif p.get("proxy"):
            route = "本地翻译代理 · " + (self.proxy_url or "启动时动态分配本机回环端口")
            advice = "该通道依赖本地翻译。若开启 Clash 等系统代理，需确保 127.0.0.1/localhost 不走系统代理。"
        elif pid == core.OFFICIAL_ID:
            route = "Codex 当前登录状态"
            advice = "使用当前 Codex 已登录账户；工具箱不会识别、推断或承诺账户套餐、组织权限和可用额度。"
        else:
            route = "远程 API 直连 · Responses"
            advice = "服务端必须原生支持 /responses；若只支持 /chat/completions，请开启本地翻译。"
        return {"ok": not issues, "name": p.get("name", pid), "route": route,
                "base_url": p.get("base_url", ""), "model": p.get("model", ""),
                "reasoning_effort": p.get("reasoning_effort", ""),
                "issues": issues, "advice": advice}

    def network_precheck(self, force=False):
        """Startup Clash check: warn on disabled/unknown state without blocking use."""
        started = time.monotonic()
        try:
            import network_gate
            result = network_gate.check(force=bool(force))
            text = network_gate.warning_text(result)
            summary = network_gate.status_line(result)
        except Exception:
            return {"ok": False, "text": "暂时无法检查 Clash 状态，请手动确认 Clash 已开启。", "summary": "Clash 状态未确认",
                    "detail": {}, "cached": False,
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
        return {
            "ok": bool(result.get("ok")),
            "text": text,
            "summary": summary,
            "detail": result,
            "cached": bool(result.get("cached")),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    def test_provider_connection(self, pid):
        """用户主动触发的连接测试，不发送对话内容。"""
        import urllib.error
        import urllib.request
        p = next((x for x in core.load_providers() if x.get("id") == pid), None)
        if not p:
            return {"ok": False, "error": "供应商不存在"}
        if pid == core.OFFICIAL_ID:
            # 官方通道没有 API Key 或可安全调用的 /models 地址。旧逻辑只看
            # auth.json 是否存在，容易把“已登录但官方流式链路超时”误报为成功。
            # app-server 的 model/list 只读取当前登录账户可用模型，不发送用户
            # 对话，也不会产生模型调用用量；用它验证登录、配置和官方网络链路。
            client = None
            started = time.monotonic()
            try:
                executable = core._find_codex_executable()
                client = core._CodexAppServer(executable, timeout=12)
                result = client.request("model/list", {}) or {}
                models = result.get("data") if isinstance(result, dict) else None
                count = len(models) if isinstance(models, list) else 0
                elapsed = time.monotonic() - started
                return {
                    "ok": True,
                    "message": f"官方登录、app-server 和模型列表预检正常（{count} 个模型，耗时 {elapsed:.1f} 秒）。首次对话仍需官方流式连接可用。",
                    "models_count": count,
                    "elapsed_seconds": round(elapsed, 1),
                }
            except Exception as error:
                detail = str(error).strip().replace("\r", " ").replace("\n", " ")
                detail = " ".join(detail.split())[:260]
                return {
                    "ok": False,
                    "error": (
                        "官方登录状态存在，但官方连接预检失败：" + detail +
                        "。请检查 chatgpt.com 的 HTTPS/WebSocket 是否经过可用代理，"
                        "并完全重启 Codex 后再试。"
                    ),
                }
            finally:
                if client:
                    client.close()
        base = (p.get("upstream_base") if p.get("proxy") else p.get("base_url")) or ""
        if not base.startswith(("http://", "https://")):
            return {"ok": False, "error": "Base URL 无效"}
        req = urllib.request.Request(
            base.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {p.get('api_key', '')}", "User-Agent": f"CodexToolbox/{__version__}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = getattr(resp, "status", 200)
            return {"ok": True, "message": f"连接和 API Key 验证成功（HTTP {status}）"}
        except urllib.error.HTTPError as e:
            tips = {401: "API Key 无效或已过期", 403: "账号、地域或权限受限",
                    404: "Base URL 不正确，未找到 /models", 429: "请求过多或额度不足"}
            return {"ok": False, "error": f"HTTP {e.code}：{tips.get(e.code, '服务端拒绝请求')}"}
        except Exception as e:
            return {"ok": False, "error": f"网络连接失败：{str(e)[:160]}"}

    def test_all_provider_connections(self):
        """逐个复用连接测试，返回不含密钥和请求内容的汇总结果。"""
        results = []
        for provider in core.load_providers():
            pid = provider.get("id")
            result = self.test_provider_connection(pid)
            results.append({
                "id": pid,
                "name": provider.get("name") or pid,
                "ok": bool(result.get("ok")),
                "message": result.get("message", "") if result.get("ok") else "",
                "error": result.get("error", "") if not result.get("ok") else "",
            })
        return {"ok": True, "results": results}

    def _ensure_proxy(self, p):
        """启动独立代理进程；绝不终止无法确认归属的端口占用进程。"""
        import socket
        import subprocess
        import urllib.request
        import time
        upstream = p.get("upstream_base") or "https://api.deepseek.com/v1"
        key = p.get("api_key") or ""
        model = p.get("model") or "deepseek-chat"
        provider_id = p.get("id") or ""
        proxy_token = p.get("proxy_token") or ""
        if not proxy_token:
            raise RuntimeError("本地代理令牌缺失，请重新保存该供应商配置。")
        import codex_proxy
        expected_fingerprint = codex_proxy.config_fingerprint(upstream, key, model, provider_id)
        selected_port = core.PROXY_PORT

        def read_status(url, timeout):
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {proxy_token}"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        # 端口是否空闲
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", core.PROXY_PORT))
            s.close()
            port_free = True
        except OSError:
            s.close()
            port_free = False

        if not port_free:
            # 已有代理：查 /status 对比配置
            same = False
            try:
                st = read_status(f"http://127.0.0.1:{core.PROXY_PORT}/status", 3)
                same = (st.get("service") == "codex-toolbox-proxy"
                        and st.get("auth_required") is True and st.get("model") == model
                        and st.get("provider_id") == provider_id
                        and st.get("version") == codex_proxy.VERSION
                        and st.get("config_fingerprint") == expected_fingerprint)
            except Exception:
                same = False
            if same:
                self.proxy_server = object()
                self.proxy_url = f"http://127.0.0.1:{core.PROXY_PORT}/v1"
                self._proxy_sessions.setdefault(self.proxy_url, (proxy_token, expected_fingerprint, None))
                return self.proxy_url
            # 端口属于其他程序或旧配置时，选择空闲端口，不误杀用户进程。
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1", 0))
                selected_port = probe.getsockname()[1]
            finally:
                probe.close()

        # 启动独立代理进程：frozen 时用 exe --proxy；py 模式用 codex_proxy.py
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--proxy"]
        else:
            cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "codex_proxy.py")]
        cmd += [upstream, model, str(selected_port), provider_id]
        self.proxy_url = f"http://127.0.0.1:{selected_port}/v1"
        self.proxy_process = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        status_url = f"http://127.0.0.1:{selected_port}/status"
        last_error = ""
        for _ in range(30):
            if self.proxy_process.poll() is not None:
                last_error = f"代理进程已退出（代码 {self.proxy_process.returncode}）"
                break
            try:
                status = read_status(status_url, 0.5)
                if (status.get("service") == "codex-toolbox-proxy"
                        and status.get("auth_required") is True
                        and status.get("config_fingerprint") == expected_fingerprint):
                    self.proxy_server = object()
                    self._proxy_sessions[self.proxy_url] = (proxy_token, expected_fingerprint, self.proxy_process)
                    return self.proxy_url
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.1)
        try:
            self.proxy_process.terminate()
        except Exception:
            pass
        self.proxy_process = None
        self.proxy_url = ""
        raise RuntimeError(f"本地翻译代理启动失败：{last_error[:160]}")

    # ---------- 会话 ----------
    @staticmethod
    def _model_family(model):
        """用于发现明显串线；同一家族内的不同型号不是错误。"""
        value = (model or "").lower()
        families = (
            ("qwen", ("qwen",)),
            ("openai", ("gpt-", "o1", "o3", "o4")),
            ("deepseek", ("deepseek",)),
            ("glm", ("glm-",)),
            ("kimi", ("kimi", "moonshot")),
            ("minimax", ("minimax", "abab")),
            ("doubao", ("doubao",)),
        )
        for family, prefixes in families:
            if any(value.startswith(prefix) for prefix in prefixes):
                return family
        return ""

    def get_sessions(self):
        rows, titles = core.list_unarchived()
        provider_items = {p["id"]: p for p in core.load_providers()}
        names = {pid: p["name"] for pid, p in provider_items.items()}
        out = []
        routes = core.load_thread_routes()
        current = core.read_provider()
        for tid, title, prov, model, effort in rows:
            saved = routes.get(tid, {}) if isinstance(routes.get(tid, {}), dict) else {}
            mode = saved.get("mode")
            if mode not in ("follow", "fixed"):
                mode = "same" if prov == current else "fixed"
            expected_model = (provider_items.get(prov) or {}).get("model") or ""
            expected_family = self._model_family(expected_model)
            actual_family = self._model_family(model)
            mismatch = bool(expected_family and actual_family and expected_family != actual_family)
            out.append({
                "id": tid,
                "title": (titles.get(tid) or title or "").replace("\n", " ")[:60],
                "provider": prov,
                "provider_name": names.get(prov, prov),
                "model": model or "",
                "reasoning_effort": effort or "",
                "expected_model": expected_model,
                "model_mismatch": mismatch,
                "route_mode": mode,
                "route_label": {"follow": "上次手动同步", "fixed": "固定通道", "same": "与默认一致"}[mode],
            })
        return out

    def repair_mismatched_sessions(self):
        return {"ok": False, "error": "公开测试版已暂停直接修改单会话数据。"}

    def preview_session_route(self, tids, pid, model_override=None, effort_override=None):
        """只读预检，用于在用户确认前展示会话、目标模型和思考强度。"""
        target_ids = list(dict.fromkeys(tid for tid in tids if tid))
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not target_ids:
            return {"ok": False, "error": "请先选择会话"}
        if not provider:
            return {"ok": False, "error": "目标供应商不存在"}
        try:
            model, effort = core.resolve_thread_route_settings(
                provider, model_override, effort_override,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        sessions = {item["id"]: item for item in self.get_sessions()}
        missing = [tid for tid in target_ids if tid not in sessions]
        if missing:
            return {"ok": False, "error": "存在已归档或不存在的会话，已取消预检"}
        return {
            "ok": True,
            "codex_running": core.codex_running(),
            "provider": provider["name"],
            "model": model,
            "reasoning_effort": effort or "保持当前",
            "targets": [{"id": tid, "title": sessions[tid]["title"]} for tid in target_ids],
        }

    def switch_sessions(
        self, tids, pid, model_override=None, effort_override=None, progress_callback=None,
    ):
        if not self._load_community_experiments()["session_routing"]:
            return {"ok": False, "error": "请先在“会话路由实验”中确认并启用此非官方功能"}
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not provider:
            return {"ok": False, "error": "供应商不存在"}
        try:
            model, effort = core.resolve_thread_route_settings(
                provider, model_override, effort_override,
            )
            proxy_url = self._ensure_proxy(provider) if provider.get("proxy") else None
            core.ensure_provider_config(pid, proxy_url)
            stamp = core.set_threads_provider_official(
                tids, pid, model, effort, progress_callback=progress_callback,
            )
            core.set_thread_route_mode(tids, "fixed", pid)
        except core.SessionRouteUnsupported as e:
            return {
                "ok": False,
                "status": "unsupported",
                "message": str(e),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "count": len(tids),
            "backup": stamp,
            "model": model,
            "reasoning_effort": effort,
        }

    def start_route_operation(self, tids, pid, mode="fixed", model_override=None, effort_override=None):
        """提交一次会话路由操作，实际写入和校验在后台线程完成。"""
        if mode not in ("fixed", "follow"):
            return {"ok": False, "error": "未知的会话路由模式"}
        target_ids = list(dict.fromkeys(tid for tid in (tids or []) if tid))
        if not target_ids:
            return {"ok": False, "error": "请先选择会话"}
        if not self._load_community_experiments()["session_routing"]:
            return {"ok": False, "error": "请先在“会话路由实验”中确认并启用此非官方功能"}
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        # 不允许同一时间启动多个批量操作，避免相互覆盖备份或路由状态。
        with self.route_operations_lock:
            for operation in self.route_operations.values():
                if operation.get("state") in ("queued", "running"):
                    return {"ok": False, "error": "已有会话路由操作正在后台执行，请等待其完成"}
            operation_id = f"route-{os.getpid()}-{threading.get_ident()}-{len(self.route_operations) + 1}"
            self.route_operations[operation_id] = {
                "state": "queued",
                "message": "已提交后台操作，等待执行",
                "count": len(target_ids),
                "provider": pid if mode == "fixed" else None,
                "mode": mode,
                "model": model_override if mode == "fixed" else None,
                "reasoning_effort": effort_override if mode == "fixed" else None,
            }
        worker = threading.Thread(
            target=self._run_route_operation,
            args=(operation_id, target_ids, pid, mode, model_override, effort_override),
            name="codex-route-operation",
            daemon=True,
        )
        worker.start()
        # 单个会话包含 app-server 启动、写入和异步校验；批量会话按顺序处理。
        # 返回估算上限让 UI 不会在固定 60 秒时误报超时。
        return {
            "ok": True,
            "operation_id": operation_id,
            "count": len(target_ids),
            "estimated_timeout_ms": max(120000, len(target_ids) * 60000 + 60000),
            "message": "已开始后台处理，请保持 Codex 关闭直到结果显示",
        }

    def _run_route_operation(self, operation_id, tids, pid, mode, model_override=None, effort_override=None):
        with self.route_operations_lock:
            operation = self.route_operations.get(operation_id)
            if not operation:
                return
            operation["state"] = "running"
            operation["message"] = "正在请求 Codex 保存通道，并确认落盘状态"
        try:
            # 在后台线程真正开始前再次检查，防止用户在确认后重新打开 Codex。
            block_reason = self._mutation_block_reason()
            if block_reason:
                result = {"ok": False, "error": block_reason}
            elif mode == "fixed":
                # 保持旧调用签名兼容；核心函数仍支持维护者传入进度回调。
                result = self.switch_sessions(tids, pid, model_override, effort_override)
            else:
                result = self.follow_sessions(tids)
            with self.route_operations_lock:
                operation = self.route_operations.get(operation_id)
                if not operation:
                    return
                operation["result"] = result
                operation["provider"] = result.get("provider", pid) if isinstance(result, dict) else pid
                if isinstance(result, dict):
                    operation["model"] = result.get("model", operation.get("model"))
                    operation["reasoning_effort"] = result.get(
                        "reasoning_effort", operation.get("reasoning_effort"),
                    )
                if isinstance(result, dict) and result.get("ok"):
                    operation["state"] = "success"
                    operation["message"] = "Codex 已保存通道并完成落盘校验"
                elif isinstance(result, dict) and result.get("status") == "unsupported":
                    operation["state"] = "unsupported"
                    operation["message"] = result.get("message", "Codex 未返回可验证的落盘状态")
                else:
                    operation["state"] = "error"
                    operation["message"] = (result or {}).get("error", "会话通道切换失败") if isinstance(result, dict) else "会话通道切换失败"
        except Exception:
            # 不向 UI 暴露 traceback、API Key 或会话内容。
            with self.route_operations_lock:
                operation = self.route_operations.get(operation_id)
                if operation:
                    operation["state"] = "error"
                    operation["message"] = "后台会话通道切换失败，请查看备份并刷新会话后重试"

    def get_route_operation(self, operation_id):
        """读取后台路由操作状态；只返回 UI 所需的安全摘要。"""
        with self.route_operations_lock:
            operation = self.route_operations.get(operation_id)
            if not operation:
                return {"ok": False, "error": "后台操作不存在或已过期"}
            result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
            return {
                "ok": True,
                "operation_id": operation_id,
                "state": operation.get("state"),
                "message": operation.get("message", ""),
                "count": operation.get("count", 0),
                "provider": operation.get("provider"),
                "backup": result.get("backup"),
                "error": result.get("error") if operation.get("state") == "error" else None,
            }

    def follow_sessions(self, tids, progress_callback=None):
        if not self._load_community_experiments()["session_routing"]:
            return {"ok": False, "error": "请先在“会话路由实验”中确认并启用此非官方功能"}
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        pid = core.read_provider()
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not provider:
            return {"ok": False, "error": f"当前全局供应商 {pid} 不存在"}
        try:
            proxy_url = self._ensure_proxy(provider) if provider.get("proxy") else None
            core.ensure_provider_config(pid, proxy_url)
            stamp = core.set_threads_provider_official(
                tids, pid, progress_callback=progress_callback,
            )
            core.set_thread_route_mode(tids, "follow", pid)
        except core.SessionRouteUnsupported as e:
            return {
                "ok": False,
                "status": "unsupported",
                "message": str(e),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "count": len(tids), "backup": stamp, "provider": pid}

    def archive_sessions(self, tids):
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        try:
            stamp = core.archive_threads(tids)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "count": len(tids), "backup": stamp}

    def get_archived_sessions(self):
        names = {p["id"]: p["name"] for p in core.load_providers()}
        return [{
            "id": tid,
            "title": (title or "").replace("\n", " ")[:60],
            "provider": provider,
            "provider_name": names.get(provider, provider),
            "model": model or "",
            "reasoning_effort": effort or "",
        } for tid, title, provider, model, effort in core.list_archived()]

    def unarchive_sessions(self, tids):
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        try:
            stamp = core.unarchive_threads(tids)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "count": len(tids), "backup": stamp}

    def repair_archived_session_index(self):
        block_reason = self._mutation_block_reason()
        if block_reason:
            return {"ok": False, "error": block_reason}
        try:
            stamp, count, catalog_count, backup = core.repair_archived_session_index()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True, "count": count, "catalog_count": catalog_count,
            "backup": stamp, "backup_path": backup,
        }

    def fix_thread(self, tid, target="openai"):
        return {"ok": False, "error": "公开测试版不直接修复 Codex 内部数据库。"}

    def stop_proxy(self):
        processes = [session[2] for session in self._proxy_sessions.values() if session[2] is not None]
        if self.proxy_process is not None and self.proxy_process not in processes:
            processes.append(self.proxy_process)
        for url, (token, fingerprint, _process) in list(self._proxy_sessions.items()):
            try:
                request = urllib.request.Request(url.removesuffix("/v1") + "/shutdown",
                    data=json.dumps({"config_fingerprint": fingerprint}).encode("utf-8"),
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=1) as response:
                    response.read(1024)
            except Exception:
                pass
        for process in processes:
            try:
                if process.poll() is None:
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._proxy_sessions.clear()
        self.proxy_process = None
        self.proxy_server = None
        self.proxy_url = ""

    def stop_background_media(self):
        """兼容旧托盘退出调用；MP4 已不再启动本机媒体服务。"""
        return None

def _acquire_single_instance():
    """Windows 命名互斥锁：返回 False 表示当前用户已有实例在运行。"""
    if sys.platform == "darwin":
        import fcntl
        handle = open(os.path.join(core._app_dir(), "toolbox.lock"), "a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        globals()["_singleton_lock"] = handle
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Global 命名空间在部分普通用户环境需要额外权限，失败会导致重复启动。
        # 工具箱数据本来就按当前 Windows 用户隔离，因此 Local 范围更合适。
        mutex = kernel32.CreateMutexW(None, False, "Local\\CodexToolbox_SingleInstance")
        if not mutex:
            return False
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            return False
        # 保持 mutex 句柄存活（进程退出时自动释放）
        globals()["_singleton_mutex"] = mutex
        return True
    except Exception:
        return True  # 非 Windows 或失败时放行


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--claude-secret":
        from claude_channels import ClaudeChannels
        try:
            key = ClaudeChannels().secret(sys.argv[2])
            result = secret_store.write_auth_stdout(key)
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0 if result else 1)

    if len(sys.argv) >= 2 and sys.argv[1] == "--auth-secret":
        raise SystemExit(secret_store.main(sys.argv[2:]))

    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        output_path = sys.argv[index + 1] if len(sys.argv) > index + 1 else ""
        html = _load_ui()
        checks = {
            "version": __version__,
            "ui_loaded": "Codex 工具箱" in html,
            "safe_model_rendering": "chip.textContent = String(model)" in html and "m.replace(/\"/g" not in html,
            "non_overlapping_refresh": "sessionRefreshInFlight" in html and "setInterval(loadSessions" not in html,
            "proxy_auth": hasattr(secret_store, "read_provider_proxy_secret"),
        }
        checks["ok"] = all(value for key, value in checks.items() if key != "version")
        payload = json.dumps(checks, ensure_ascii=False, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        else:
            print(payload)
        if not checks["ok"]:
            raise SystemExit(1)
        return

    # 代理独立进程模式：命令行只传供应商 ID，不传明文密钥。
    if "--proxy" in sys.argv:
        i = sys.argv.index("--proxy")
        args = sys.argv[i + 1:i + 5]
        if len(args) >= 4:
            import codex_proxy
            upstream, model, port_text, provider_id = args
            key = secret_store.read_provider_secret(provider_id)
            proxy_token = secret_store.read_provider_proxy_secret(provider_id)
            server, _ = codex_proxy.start_proxy(
                upstream, key, model, int(port_text), provider_id=provider_id, proxy_token=proxy_token,
            )
            try:
                server.stop_event.wait()
            finally:
                server.shutdown()
                server.server_close()
        return

    # 单实例锁：已有实例在跑则直接退出（避免多个窗口/托盘图标）
    if not _acquire_single_instance():
        return

    if sys.platform == "win32":
        # Give Explorer a stable taskbar identity instead of inferring one
        # from the executable path and its previously cached artwork.
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id("CodexToolbox.Desktop")
        if result < 0:
            raise OSError(result, "Cannot set toolbox taskbar identity")

    auth_migration_error = ""
    if os.path.isfile(core.CONFIG):
        try:
            core.migrate_legacy_auth_commands()
            core.remove_unused_legacy_auth_helpers()
        except Exception as exc:
            auth_migration_error = str(exc)[:300]
    api = Api()
    api._auth_migration_error = auth_migration_error
    html = _load_ui()
    window = webview.create_window(
        "Codex 工具箱",
        html=html,
        js_api=api,
        width=1060,
        height=820,
        min_size=(960, 720),
        background_color="#f4f7fc",
    )
    api.set_window(window)
    threading.Thread(target=api.resume_saved_surface_translation, name="codex-translation-resume", daemon=True).start()

    # ---- 托盘：关闭窗口 → 隐藏到后台，托盘驻留 ----
    tray = None
    exiting = {"flag": False}

    def tray_show():
        window.show()
        window.restore()

    def tray_quit():
        exiting["flag"] = True
        api.stop_claude_translation()
        api.stop_surface_translation()
        api.stop_proxy()
        api.stop_background_media()
        if tray:
            tray.stop()
        try:
            window.destroy()
        except Exception:
            pass

    def tray_setup():
        nonlocal tray
        try:
            import pystray
            from PIL import Image
            # 与 PyInstaller EXE 和桌面快捷方式共用同一份主图标，避免托盘显示旧图标。
            icon_path = _toolbox_icon_path()
            img = Image.open(icon_path) if os.path.exists(icon_path) else Image.new("RGBA", (64, 64), (79, 140, 255, 255))
            menu = pystray.Menu(
                pystray.MenuItem("显示工具箱", tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", tray_quit),
            )
            tray = pystray.Icon("codex_toolbox", img, "Codex 工具箱", menu)
            tray.run_detached()
        except Exception:
            pass  # 托盘失败不影响主程序

    if sys.platform != "darwin":
        threading.Thread(target=tray_setup, daemon=True).start()

    def on_closing():
        if sys.platform == "darwin":
            return True
        if exiting["flag"]:
            return True  # 真正退出
        try:
            window.hide()  # 隐藏到托盘
        except Exception:
            pass
        return False  # 取消关闭

    window.events.closing += on_closing
    def stop_local_services():
        api.stop_claude_translation()
        api.stop_surface_translation()
        api.stop_proxy()
        api.stop_background_media()

    window.events.closed += stop_local_services
    # Load the bundled artwork directly instead of shell-cached EXE extraction.
    webview.start(icon=_toolbox_icon_path() or None)


if __name__ == "__main__":
    main()
