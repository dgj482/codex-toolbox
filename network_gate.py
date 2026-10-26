"""Silent startup check for an active proxy/TUN and working IP connectivity."""
import copy
import csv
import io
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

CACHE_TTL = 5.0
TOTAL_BUDGET = 3.5
_lock = threading.Lock()
_cache = {"at": 0.0, "result": None}
_flight = None


def _system_proxy():
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            try:
                enabled = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
            except FileNotFoundError:
                enabled = False
            try:
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
            except FileNotFoundError:
                server = ""
        return enabled, server
    server = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    return bool(server), server


def _parse_endpoint(server):
    entries = [item.strip() for item in server.split(";") if item.strip()]
    protocols = dict(item.split("=", 1) for item in entries if "=" in item)
    value = protocols.get("https") or protocols.get("http") or next(
        (item for item in entries if "=" not in item), "")
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value if "://" in value else "http://" + value)
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or 80, value if "://" in value else "http://" + value


def _tcp_ok(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _tun_active():
    """Require a running Clash core and an active Clash/Mihomo adapter."""
    if sys.platform != "win32":
        return False
    flags = subprocess.CREATE_NO_WINDOW
    listing = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=1, creationflags=flags)
    names = [row[0].lower() for row in csv.reader(io.StringIO(
        listing.stdout.decode("utf-8", "replace"))) if row]
    if not any(name.startswith(("mihomo", "clash")) and name.endswith(".exe") for name in names):
        return False
    shell = shutil.which("pwsh.exe")
    if not shell:
        return False
    script = ('[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '
              'if(Get-NetAdapter -ErrorAction Stop | Where-Object '
              '{$_.Status -eq "Up" -and ($_.Name -match "Clash|Mihomo" -or '
              '$_.InterfaceDescription -match "Clash|Mihomo|Meta Tunnel")}) '
              '{[Console]::Write("active")}')
    result = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                            capture_output=True, timeout=2, creationflags=flags)
    return result.returncode == 0 and result.stdout.decode("utf-8", "replace").strip() == "active"


def _normalize_exit(data):
    ip = str(ipaddress.ip_address(data.get("ip", "")))
    timezone = data.get("timezone") or ""
    return {"exit_ip": ip,
            "country": str(data.get("country_code") or data.get("country") or ""),
            "country_name": str(data.get("country") or ""),
            "ip_timezone": str(timezone.get("id") or "") if isinstance(timezone, dict) else str(timezone),
            "ip_utc_offset": str(timezone.get("utc") or "") if isinstance(timezone, dict) else ""}


def _probe_ip(proxy_url):
    # Explicit routing avoids a silent direct fallback when the proxy fails.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"https": proxy_url} if proxy_url else {}))
    for url in ("https://ipwho.is/", "https://ipinfo.io/json"):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CodexToolbox/startup-check"})
            with opener.open(request, timeout=1.2) as response:
                data = json.loads(response.read(16384).decode("utf-8"))
            if data.get("success") is False:
                continue
            return _normalize_exit(data)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return {}


def _warning(code, text):
    return {"ok": False, "exit_ip": "", "mode": "unknown",
            "issues": [{"code": code, "level": "warn", "text": text}]}


def _inspect():
    enabled, server = _system_proxy()
    endpoint = _parse_endpoint(server) if enabled else None
    if endpoint and _tcp_ok(endpoint[0], endpoint[1]):
        mode, proxy_url = "system_proxy", endpoint[2]
    elif _tun_active():
        mode, proxy_url = "tun", ""
    elif enabled:
        return _warning("proxy_unreachable", "系统代理已开启，但代理端口无法连接。请检查 Clash 是否正在运行。")
    else:
        return _warning("clash_off", "未检测到已启用的 Clash 系统代理或 TUN。请打开 Clash 并启用系统代理或 TUN 模式。")
    info = _probe_ip(proxy_url)
    if not info:
        return _warning("network_unknown", "已检测到代理或 TUN，但未能完成网络连通性检测。请检查 Clash 节点及网络连接。")
    return {"ok": True, **info, "mode": mode, "issues": []}


def check(force=False):
    """Bound caller wait and share overlapping checks without blocking the GUI."""
    global _flight
    started = time.monotonic()
    with _lock:
        if not force and _cache["result"] is not None and started - _cache["at"] < CACHE_TTL:
            result = copy.deepcopy(_cache["result"])
            result["cached"] = True
            return result
        if _flight is None or _flight[0].is_set():
            flight = (threading.Event(), {})
            _flight = flight

            def worker():
                try:
                    result = _inspect()
                except Exception:
                    result = _warning("check_unknown", "暂时无法检查 Clash 状态，请手动确认 Clash 已开启。")
                result.update(cached=False, checked_at=time.time())
                flight[1].update(result)
                flight[0].set()

            threading.Thread(target=worker, name="clash-startup-check", daemon=True).start()
        flight = _flight
    if not flight[0].wait(max(0, TOTAL_BUDGET - (time.monotonic() - started))):
        result = _warning("check_timeout", "Clash 网络检查超时，请确认 Clash 已开启且节点可用。")
        result.update(cached=False, checked_at=time.time())
    else:
        result = copy.deepcopy(flight[1])
        with _lock:
            _cache.update(at=time.monotonic(), result=copy.deepcopy(result))
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def reset_cache():
    with _lock:
        _cache.update(at=0.0, result=None)


def status_line(result):
    return "Clash 网络正常" if result.get("ok") else "请检查 Clash 网络连接"


def warning_text(result):
    return "\n".join(issue["text"] for issue in result.get("issues", []))
