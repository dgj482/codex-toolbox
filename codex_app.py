# -*- coding: utf-8 -*-
"""Codex 工具箱 0.1.0-beta（PyWebView 界面）
前端：ui.html（HTML/CSS/JS），后端：本文件 Api 类 + codex_toolbox 逻辑
运行：python codex_app.py （需 pywebview + WebView2）
"""
import os
import sys
import json
import threading
import webview
import codex_toolbox as core

UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


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
        self.migrated_dashscope = core.migrate_active_dashscope()

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
            ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
            # 过滤非对话模型（语音/图像/视频/嵌入等）
            skip = ("tts", "asr", "image", "video", "embedding", "speech",
                    "audio", "ocr", "realtime", "transcribe", "embed")
            ids = [i for i in ids if not any(k in i.lower() for k in skip)]
            return {"ok": True, "models": sorted(ids)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

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
        provs = core.load_providers()
        provs = [p for p in provs if p["id"] != pid]
        core.save_providers(provs)
        return {"ok": True}

    def switch_provider(self, pid):
        provs = core.load_providers()
        p = next((x for x in provs if x["id"] == pid), None)
        if not p:
            return {"ok": False, "error": f"供应商 {pid} 不存在"}
        following = core.following_thread_ids()
        if following and core.codex_running():
            return {
                "ok": False,
                "error": f"有 {len(following)} 个会话设为跟随全局。"
                         "请先完全退出 Codex（含托盘）再切换。",
            }
        previous_pid = core.read_provider()
        sessions_switched = False
        try:
            proxy_url = self._ensure_proxy(p) if p.get("proxy") else None
            # 先准备供应商配置，再同步跟随会话，最后才修改全局默认值。
            # 这样全局写入失败时，仍可通过官方接口把会话恢复到原通道。
            core.ensure_provider_config(pid, proxy_url)
            followed = 0
            if following:
                core.set_threads_provider_official(following, pid)
                sessions_switched = True
                followed = len(following)
            name = core.apply_provider(pid, proxy_url)
            if following:
                core.set_thread_route_mode(following, "follow", pid)
        except Exception as e:
            rollback_error = ""
            if sessions_switched and previous_pid != pid:
                try:
                    core.set_threads_provider_official(following, previous_pid)
                except Exception as rollback_exc:
                    rollback_error = f"；跟随会话自动恢复失败：{rollback_exc}"
            return {"ok": False, "error": str(e) + rollback_error}
        return {"ok": True, "name": name, "proxy": bool(p.get("proxy")),
                "followed": followed}

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
            advice = "配置正确，不需要本地 18732 代理，也不需要为千问设置 NO_PROXY。"
        elif p.get("proxy"):
            route = "本地翻译代理 · 127.0.0.1:18732"
            advice = "该通道依赖本地翻译。若开启 Clash 等系统代理，需确保 127.0.0.1/localhost 不走系统代理。"
        elif pid == core.OFFICIAL_ID:
            route = "Codex 官方登录通道"
            advice = "使用 Codex 自身登录状态。"
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
            return {"ok": True, "message": "官方通道使用 Codex 登录状态，请直接在 Codex 中验证。"}
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
                "route_label": {"follow": "跟随全局", "fixed": "固定通道", "same": "与默认一致"}[mode],
            })
        return out

    def repair_mismatched_sessions(self):
        return {"ok": False, "error": "公开测试版已暂停直接修改单会话数据。"}

    def switch_sessions(self, tids, pid):
        if core.codex_running():
            return {"ok": False, "error": "请先完全退出 Codex（含托盘）"}
        provider = next((item for item in core.load_providers() if item.get("id") == pid), None)
        if not provider:
            return {"ok": False, "error": "供应商不存在"}
        try:
            proxy_url = self._ensure_proxy(provider) if provider.get("proxy") else None
            core.ensure_provider_config(pid, proxy_url)
            stamp = core.set_threads_provider_official(tids, pid)
            core.set_thread_route_mode(tids, "fixed", pid)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "count": len(tids), "backup": stamp}

    def follow_sessions(self, tids):
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

def _acquire_single_instance():
    """Windows 命名互斥锁：返回 False 表示已有实例在运行"""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Global\\CodexToolbox_SingleInstance")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
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

    # ---- 托盘：关闭窗口 → 隐藏到后台，托盘驻留 ----
    tray = None
    exiting = {"flag": False}

    def tray_show():
        window.show()
        window.restore()

    def tray_quit():
        exiting["flag"] = True
        api.stop_proxy()
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
    window.events.closed += api.stop_proxy
    webview.start()


if __name__ == "__main__":
    main()
