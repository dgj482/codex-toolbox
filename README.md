# Codex 工具箱

Windows 上的 Codex 桌面版辅助工具，用于管理全局模型通道、本地 Responses ↔ Chat Completions 翻译代理，以及通过 Codex 官方 app-server 归档/恢复会话。

> 当前为 `0.1.0-beta`，是社区项目，不是 OpenAI 官方产品。发布前请阅读 [SECURITY.md](SECURITY.md) 和 [PRIVACY.md](PRIVACY.md)。

## 功能

- 管理 Codex 全局默认通道，支持 OpenAI 官方登录、原生 Responses 直连和本地协议翻译。
- API Key 使用 Windows DPAPI 按当前用户加密，不写入 `config.toml`、日志或子进程命令行。
- 端口冲突时不终止来路不明的进程，改用随机空闲本地端口。
- 归档和恢复使用 `thread/archive` / `thread/unarchive` 官方接口，每次操作前备份。
- 公开测试版不直接改写单个会话的 SQLite/JSONL，不替换 Codex 界面资源。

## 系统要求

- Windows 10/11 x64；
- 已安装 Codex 桌面版和 Microsoft Edge WebView2 Runtime；
- 归档/恢复前必须完全退出 Codex（含托盘）。

## 使用发布包

GitHub Actions 生成的公开测试包尚未使用付费代码签名证书签名，Windows SmartScreen 可能显示“未知发布者”。请从仓库 Releases 下载并核对同名 `.sha256` 文件；不要运行第三方重打包版。

1. 解压到任意目录，保持两个 EXE 在同一目录：`Codex工具箱.exe` 和 `Codex工具箱认证助手.exe`。
2. 运行 `Codex工具箱.exe`。
3. 添加供应商并保存 API Key；可先点“测试连接”。
4. 选中供应商后点“切换到选中”，完全重启 Codex 生效。

运行数据位于 `%LOCALAPPDATA%\CodexToolbox`，不依赖开发者电脑路径。密钥由 Windows 当前用户绑定，复制 `providers.json` 到其他账户后无法解密，需重新输入。

## 从源码运行

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe codex_app.py
```

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe scripts\check_secrets.py
.\build.ps1
```

`build.ps1` 会在 `dist\Codex工具箱-0.1.0-beta-windows-x64.zip` 产生包和 SHA-256 校验文件。

## 支持边界

- 供应商的地域、额度、账号和模型可用性由供应商决定。
- 开启本地翻译时，请求内容会发送到用户配置的上游 API；工具箱本身没有远程服务器。
- Codex 更新后如官方 app-server 协议变化，归档/恢复可能需要更新工具箱。
- 退出工具箱会停止由它启动的本地翻译代理；使用该类通道时请保持工具箱在托盘运行。

## 许可证

[MIT](LICENSE)
