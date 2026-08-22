# -*- coding: utf-8 -*-
"""Codex 工具箱 0.1.0-beta（PyWebView 界面）
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
import shutil
import webview
import codex_toolbox as core

UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


def _resource_path(*parts):
    """返回开发版或打包版内置资源的实际路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


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
        # 会话路由在后台执行，避免 UI 请求被 app-server 的落盘校验长时间阻塞。
        self.route_operations = {}
        self.route_operations_lock = threading.Lock()
        # PyWebView 会递归扫描 js_api 的公开属性并生成前端方法列表。
        # 主窗口对象必须是私有属性，否则扫描 Window 的内部对象图会中断导出，
        # 页面便会出现“window.pywebview.api[method] is not a function”。
        self._window = None
        # 启动时只读取状态，绝不因“检测到旧配置”自动改写用户的 Codex 配置。
        # 历史迁移函数保留给维护者手动处理，不作为公开版启动路径的一部分。
        self.migrated_dashscope = False

    def set_window(self, window):
        """保存主窗口，用于调用系统文件选择器。"""
        self._window = window

    # ---------- 社区实验开关 ----------
    @staticmethod
    def _community_experiments_file():
        return os.path.join(core._app_dir(), "community_experiments.json")

    @staticmethod
    def _community_experiment_defaults():
        # 公开版默认关闭；用户需在界面中明确确认后才可执行。
        return {"session_routing": False, "local_appearance": False}

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
                file_types=("Windows 程序 (*.exe)",),
            )
            return {"ok": True, "path": result[0] if result else ""}
        except Exception as e:
            return {"ok": False, "error": f"无法打开程序选择器：{e}"}

    @staticmethod
    def _find_pwsh():
        # 优先使用用户已安装并在 PATH 中可用的 PowerShell 7，避免把外观实验
        # 错误地绑定到某一台电脑的 Codex 运行时缓存目录。
        for command in ("pwsh.exe", "pwsh"):
            found = shutil.which(command)
            if found:
                return found
        candidates = []
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

    def _normalized_background_settings(self, data):
        defaults = self._background_defaults()
        result = {"image_path": os.path.abspath(str(data.get("image_path") or ""))}
        launch_path = str(data.get("codex_launch_path") or "").strip()
        if launch_path:
            launch_path = os.path.abspath(os.path.expandvars(launch_path))
            if not launch_path.lower().endswith(".exe") or not os.path.isfile(launch_path):
                raise ValueError("指定的 Codex 启动程序不存在或不是 .exe 文件")
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
            "compact", "normal", "hidden"
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

    def apply_background(self, data, restart=True):
        if not self._load_community_experiments()["local_appearance"]:
            return {"ok": False, "error": "请先在“本地外观实验”中确认并启用此非官方功能"}
        try:
            settings = self._normalized_background_settings(data)
        except (TypeError, ValueError):
            return {"ok": False, "error": "外观参数格式不正确"}
        image_path = settings["image_path"]
        if not os.path.isfile(image_path):
            return {"ok": False, "error": "请先选择有效的本地背景图片"}
        extension = os.path.splitext(image_path)[1].lower()
        if extension not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"):
            return {"ok": False, "error": "仅支持 PNG、JPG、JPEG、WEBP、GIF 或 MP4 背景"}
        if extension == ".gif":
            if not settings["animated_gif"]:
                return {"ok": False, "error": "GIF 动态背景默认关闭，请勾选“启用动态 GIF 背景”后再应用"}
            if os.path.getsize(image_path) > 3 * 1024 * 1024:
                return {"ok": False, "error": "动态 GIF 请控制在 3 MB 以内，避免占用过高或调试注入失败"}
        if extension == ".mp4":
            if not settings["animated_mp4"]:
                return {"ok": False, "error": "MP4 动态背景默认关闭，请勾选“启用本地 MP4 背景”后再应用"}
            if os.path.getsize(image_path) > 30 * 1024 * 1024:
                return {"ok": False, "error": "MP4 请控制在 30 MB 以内；为兼容 Codex 的本地安全策略，视频会临时载入当前窗口内存"}
        try:
            script = _resource_path("experiments", "codex_background_injector.ps1")
            if not os.path.isfile(script):
                raise RuntimeError("工具箱内置的背景实验脚本缺失，请重新安装工具箱")
            command = [self._find_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
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

    # ---------- 状态 ----------
    def get_models(self, base_url, api_key):
        """从 OpenAI 兼容 API 的 /models 拉取真实模型列表"""
        import urllib.request
        url = (base_url or "").rstrip("/") + "/models"
        if not url.startswith("http"):
            return {"ok": False, "error": "Base URL 无效"}
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode("utf-8"))
            models = [
                {"id": item.get("id"), "created": item.get("created")}
                for item in data.get("data", []) if isinstance(item, dict) and item.get("id")
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

    def detect_protocol(self, base_url, api_key, model):
        """主动探测上游是否接受 Responses 请求，不保存也不切换供应商。"""
        import urllib.error
        import urllib.request

        base = (base_url or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            return {"ok": False, "error": "Base URL 无效"}
        if not api_key:
            return {"ok": False, "error": "请先填写 API Key"}
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
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "CodexToolbox/0.1.0-beta",
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
        return {
            "provider": prov,
            "provider_name": names.get(prov, prov),
            "model": core.read_model(),
            "reasoning_effort": core.read_reasoning_effort(),
            "codex_running": core.codex_running(),
            "proxy_running": self.proxy_server is not None,
            "proxy_url": self.proxy_url,
            "migrated_dashscope": self.migrated_dashscope,
        }

    def get_diagnostics(self):
        """仅返回本机状态摘要，不返回 API Key、会话内容或数据库记录。"""
        data_dir = core._app_dir()
        try:
            backup_count = len([
                item for item in os.listdir(core.BAK_DIR)
                if os.path.isdir(os.path.join(core.BAK_DIR, item))
            ]) if os.path.isdir(core.BAK_DIR) else 0
        except OSError:
            backup_count = 0
        return {
            "data_dir": data_dir,
            "backup_dir": core.BAK_DIR,
            "provider_count": len(core.load_providers()),
            "route_count": len(core.load_thread_routes()),
            "backup_count": backup_count,
            "codex_running": core.codex_running(),
            "community_experiments": self._load_community_experiments(),
            "appearance_saved": os.path.isfile(self._background_settings_file()),
        }

    # ---------- 供应商 ----------
    def get_providers(self):
        provs = core.load_providers()
        current = core.read_provider()
        out = []
        for p in provs:
            item = {
                "id": p["id"],
                "name": p["name"],
                "kind": p.get("kind", "relay"),
                "model": p.get("model", ""),
                "reasoning_effort": p.get("reasoning_effort", ""),
                "base_url": p.get("base_url", ""),
                "proxy": bool(p.get("proxy")),
                "wire_api": p.get("wire_api", "responses"),
                "upstream_base": p.get("upstream_base", ""),
                "has_api_key": bool(p.get("api_key")),
                "current": p["id"] == current,
            }
            out.append(item)
        return out

    def save_provider(self, data):
        provs = core.load_providers()
        pid = data.get("id") or ""
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
        }
        if data.get("proxy"):
            p["proxy"] = True
            p["upstream_base"] = data.get("upstream_base", "https://api.deepseek.com/v1")
        p = core.normalize_provider(p)
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
        if pid == "openai":
            return {"ok": False, "error": "官方供应商不能删除"}
        if core.read_provider() == pid:
            return {
                "ok": False,
                "error": "不能删除当前全局供应商。请先切换到其他 API 或官方登录，再删除。",
            }
        provs = core.load_providers()
        provs = [p for p in provs if p["id"] != pid]
        core.save_providers(provs)
        return {"ok": True}

    def switch_provider(self, pid):
        provs = core.load_providers()
        p = next((x for x in provs if x["id"] == pid), None)
        if not p:
            return {"ok": False, "error": f"供应商 {pid} 不存在"}
        try:
            proxy_url = self._ensure_proxy(p) if p.get("proxy") else None
            # 全局默认只决定之后新建的会话。不在这里恢复或改写已有会话，
            # 避免不同版本的官方 app-server 无法持久化会话设置时，反过来
            # 阻止正常的全局切换。
            core.ensure_provider_config(pid, proxy_url)
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
        if pid != core.OFFICIAL_ID and not p.get("api_key"):
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

    def test_provider_connection(self, pid):
        """用户主动触发的轻量认证测试：调用 /models，不产生对话用量。"""
        import urllib.error
        import urllib.request
        p = next((x for x in core.load_providers() if x.get("id") == pid), None)
        if not p:
            return {"ok": False, "error": "供应商不存在"}
        if pid == core.OFFICIAL_ID:
            return {"ok": True, "message": "该通道使用当前 Codex 登录状态，请直接在 Codex 中验证可用性。"}
        base = (p.get("upstream_base") if p.get("proxy") else p.get("base_url")) or ""
        if not base.startswith(("http://", "https://")):
            return {"ok": False, "error": "Base URL 无效"}
        req = urllib.request.Request(
            base.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {p.get('api_key', '')}", "User-Agent": "CodexToolbox/0.1.0-beta"},
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
        import hashlib
        import time
        upstream = p.get("upstream_base") or "https://api.deepseek.com/v1"
        key = p.get("api_key") or ""
        model = p.get("model") or "deepseek-chat"
        provider_id = p.get("id") or ""
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:12]
        selected_port = core.PROXY_PORT

        # 端口是否空闲
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", core.PROXY_PORT))
            s.close()
            port_free = True
        except OSError:
            port_free = False

        if not port_free:
            # 已有代理：查 /status 对比配置
            same = False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{core.PROXY_PORT}/status", timeout=3) as r:
                    st = json.loads(r.read().decode("utf-8"))
                same = (st.get("service") == "codex-toolbox-proxy"
                        and st.get("upstream") == upstream and st.get("model") == model
                        and st.get("provider_id") == provider_id
                        and st.get("key_hash") == key_hash)
            except Exception:
                same = False
            if same:
                self.proxy_server = object()
                self.proxy_url = f"http://127.0.0.1:{core.PROXY_PORT}/v1"
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
        self.proxy_process = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        status_url = f"http://127.0.0.1:{selected_port}/status"
        last_error = ""
        for _ in range(30):
            if self.proxy_process.poll() is not None:
                last_error = f"代理进程已退出（代码 {self.proxy_process.returncode}）"
                break
            try:
                with urllib.request.urlopen(status_url, timeout=0.5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                if status.get("service") == "codex-toolbox-proxy":
                    self.proxy_server = object()
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
                "route_label": {"follow": "已同步默认", "fixed": "固定通道", "same": "与默认一致"}[mode],
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

    def switch_sessions(self, tids, pid, model_override=None, effort_override=None):
        if not self._load_community_experiments()["session_routing"]:
            return {"ok": False, "error": "请先在“会话路由实验”中确认并启用此非官方功能"}
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not provider:
            return {"ok": False, "error": "供应商不存在"}
        try:
            model, effort = core.resolve_thread_route_settings(
                provider, model_override, effort_override,
            )
            proxy_url = self._ensure_proxy(provider) if provider.get("proxy") else None
            core.ensure_provider_config(pid, proxy_url)
            stamp = core.set_threads_provider_official(tids, pid, model, effort)
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
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
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
        return {"ok": True, "operation_id": operation_id, "message": "已开始后台处理"}

    def _run_route_operation(self, operation_id, tids, pid, mode, model_override=None, effort_override=None):
        with self.route_operations_lock:
            operation = self.route_operations.get(operation_id)
            if not operation:
                return
            operation["state"] = "running"
            operation["message"] = "正在请求 Codex 保存通道，并确认落盘状态"
        try:
            result = (
                self.switch_sessions(tids, pid, model_override, effort_override)
                if mode == "fixed" else self.follow_sessions(tids)
            )
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

    def follow_sessions(self, tids):
        if not self._load_community_experiments()["session_routing"]:
            return {"ok": False, "error": "请先在“会话路由实验”中确认并启用此非官方功能"}
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
        pid = core.read_provider()
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not provider:
            return {"ok": False, "error": f"当前全局供应商 {pid} 不存在"}
        try:
            proxy_url = self._ensure_proxy(provider) if provider.get("proxy") else None
            core.ensure_provider_config(pid, proxy_url)
            stamp = core.set_threads_provider_official(tids, pid)
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
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
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
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
        try:
            stamp = core.unarchive_threads(tids)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "count": len(tids), "backup": stamp}

    def repair_archived_session_index(self):
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
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
        if self.proxy_process is not None and self.proxy_process.poll() is None:
            try:
                self.proxy_process.terminate()
            except Exception:
                pass
        self.proxy_process = None
        self.proxy_server = None
        self.proxy_url = ""

    def stop_background_media(self):
        """兼容旧托盘退出调用；MP4 已不再启动本机媒体服务。"""
        return None

def _acquire_single_instance():
    """Windows 命名互斥锁：返回 False 表示当前用户已有实例在运行。"""
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
    # 代理独立进程模式：命令行只传供应商 ID，不传明文密钥。
    if "--proxy" in sys.argv:
        i = sys.argv.index("--proxy")
        args = sys.argv[i + 1:i + 5]
        if len(args) >= 4:
            import codex_proxy
            import secret_store
            import time
            upstream, model, port_text, provider_id = args
            key = secret_store.read_provider_secret(provider_id)
            codex_proxy.start_proxy(upstream, key, model, int(port_text), provider_id=provider_id)
            while True:
                time.sleep(10)
        return

    # 单实例锁：已有实例在跑则直接退出（避免多个窗口/托盘图标）
    if not _acquire_single_instance():
        return

    api = Api()
    html = _load_ui()
    window = webview.create_window(
        "Codex 工具箱",
        html=html,
        js_api=api,
        width=1060,
        height=820,
        min_size=(960, 720),
        background_color="#0e1116",
    )
    api.set_window(window)

    # ---- 托盘：关闭窗口 → 隐藏到后台，托盘驻留 ----
    tray = None
    exiting = {"flag": False}

    def tray_show():
        window.show()
        window.restore()

    def tray_quit():
        exiting["flag"] = True
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
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codex_toolbox.ico")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CodexToolbox", "codex_toolbox.ico")
            img = Image.open(icon_path) if os.path.exists(icon_path) else Image.new("RGBA", (64, 64), (79, 140, 255, 255))
            menu = pystray.Menu(
                pystray.MenuItem("显示工具箱", tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("停止代理", lambda: api.stop_proxy()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", tray_quit),
            )
            tray = pystray.Icon("codex_toolbox", img, "Codex 工具箱", menu)
            tray.run_detached()
        except Exception:
            pass  # 托盘失败不影响主程序

    threading.Thread(target=tray_setup, daemon=True).start()

    def on_closing():
        if exiting["flag"]:
            return True  # 真正退出
        try:
            window.hide()  # 隐藏到托盘
        except Exception:
            pass
        return False  # 取消关闭

    window.events.closing += on_closing
    def stop_local_services():
        api.stop_proxy()
        api.stop_background_media()

    window.events.closed += stop_local_services
    webview.start()


if __name__ == "__main__":
    main()
