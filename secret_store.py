# -*- coding: utf-8 -*-
"""Windows DPAPI 密钥存储。

密钥只能由保存它的 Windows 用户解密。公开版不会把明文密钥写入
providers.json、config.toml、日志或子进程命令行。
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
import sys


APP_DIR_NAME = "CodexToolbox"
PROVIDERS_FILENAME = "providers.json"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def providers_path() -> str:
    return os.path.join(app_data_dir(), PROVIDERS_FILENAME)


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt32():
    if os.name != "nt":
        raise RuntimeError("DPAPI 仅支持 Windows")
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    source, source_buffer = _blob(secret.encode("utf-8"))
    result = _DataBlob()
    crypt32 = _crypt32()
    ok = crypt32.CryptProtectData(
        ctypes.byref(source), "CodexToolbox provider key", None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
    )
    _ = source_buffer  # 保持输入缓冲区存活到 API 调用结束
    if not ok:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def unprotect_secret(encoded: str) -> str:
    if not encoded:
        return ""
    encrypted = base64.b64decode(encoded, validate=True)
    source, source_buffer = _blob(encrypted)
    result = _DataBlob()
    description = wintypes.LPWSTR()
    crypt32 = _crypt32()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source), ctypes.byref(description), None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
    )
    _ = source_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        if description:
            ctypes.windll.kernel32.LocalFree(description)
        ctypes.windll.kernel32.LocalFree(result.pbData)


def read_provider_secret(provider_id: str, path: str | None = None) -> str:
    """读取指定供应商密钥；认证助手只调用此函数并把密钥写到 stdout。"""
    target = path or providers_path()
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    for provider in data.get("providers", []):
        if provider.get("id") != provider_id:
            continue
        encrypted = provider.get("api_key_dpapi", "")
        if not encrypted:
            raise RuntimeError(f"供应商 {provider_id} 没有已保存的密钥")
        return unprotect_secret(encrypted)
    raise RuntimeError(f"找不到供应商 {provider_id}")


def read_provider_proxy_secret(provider_id: str, path: str | None = None) -> str:
    """Read the per-provider capability token used only by the loopback proxy."""
    target = path or providers_path()
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    for provider in data.get("providers", []):
        if provider.get("id") != provider_id:
            continue
        encrypted = provider.get("proxy_token_dpapi", "")
        if not encrypted:
            raise RuntimeError(f"供应商 {provider_id} 没有本地代理令牌")
        return unprotect_secret(encrypted)
    raise RuntimeError(f"找不到供应商 {provider_id}")


def write_auth_stdout(value: str) -> bool:
    # 不追加换行，避免某些上游把换行视为令牌的一部分。窗口版 EXE 没有 Python sys.stdout，
    # 但 Codex 启动命令认证时提供了标准输出管道，直接写入该 Windows 句柄即可。
    if getattr(sys, "frozen", False) and os.name == "nt":
        payload = value.encode("utf-8")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.WriteFile.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(0xFFFFFFF5)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return False
        buffer = ctypes.create_string_buffer(payload)
        written = wintypes.DWORD()
        if not kernel32.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None) or written.value != len(payload):
            return False
    else:
        sys.stdout.write(value)
        sys.stdout.flush()
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    proxy_mode = len(args) == 2 and args[0] == "--proxy" and args[1].strip()
    provider_mode = len(args) == 1 and args[0].strip()
    if not (proxy_mode or provider_mode):
        return 2
    try:
        secret = (
            read_provider_proxy_secret(args[1].strip())
            if proxy_mode else read_provider_secret(args[0].strip())
        )
    except Exception:
        return 1
    return 0 if write_auth_stdout(secret) else 1


if __name__ == "__main__":
    raise SystemExit(main())
