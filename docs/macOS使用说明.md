# macOS 测试版

本版复用现有浅色界面，适配 Keychain、macOS 配置目录、进程检测和 Codex.app 启动。
目前只完成 Windows 回归与模拟 macOS 分支测试，尚未在 Mac 实机验收。

Claude 桌面集成仍仅支持 Windows。启动网络检测在 Mac 上尚未完整识别系统代理和 TUN，提示结果不可作为泄漏判定。

## 使用安装包（需要先完成原生构建）

1. macOS 15 或更新版本：Apple Silicon 选择 `arm64.dmg`，Intel 选择 `x86_64.dmg`。
2. 打开 DMG，把 `Codex工具箱.app` 拖到 `Applications`，再从应用程序打开。
3. 安装包内置 Python 和 PowerShell 运行时，无须朋友安装开发环境。DMG 仅包含应用程序和 Applications 快捷入口，不附带源码工程。
4. 测试构建没有 Developer ID 签名和 Apple 公证，首次启动可能需要通过 macOS 的“隐私与安全性”确认；不关闭 Gatekeeper。

Codex 仍需单独安装。程序内部的 HTML/JS 与翻译脚本是运行资源，Python 业务模块由 PyInstaller 打包；这不是不可逆加密或防反编译保护。

## 开发者从源码运行

1. 安装 Codex 桌面版，通常位于 `/Applications/Codex.app`。
2. 安装 Python 3.11 或更新版本。解压源码包，在终端进入该目录。
3. 执行：

```bash
python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install -r requirements-macos.txt
.venv-macos/bin/python codex_app.py
```

界面使用系统 Cocoa/WebKit，无须安装 WebView2。macOS 关闭窗口会退出工具箱并停止其本地代理和翻译服务。
从源码运行背景与表层翻译实验时仍需安装 PowerShell 7；DMG 内的应用优先使用自带运行时。
若 Codex 安装位置不同，在实验设置中选择 `Codex.app`；若系统选择器无法选择应用包，可直接填写路径。
会话操作使用 Codex app-server；找不到它时，请安装 Codex CLI，或用 `CODEX_EXECUTABLE` 指定支持 app-server 的 CLI 可执行文件。

## 构建桌面应用

在 Mac 终端运行：

```bash
bash build_macos.sh
```

产物包括 `dist/Codex工具箱.app` 与 `dist/CodexToolbox-版本-macOS-架构.dmg` 及 SHA-256 校验文件；对外只分发 DMG 和校验文件。
安装包脚本从微软官方 GitHub Release 下载固定版本 PowerShell，校验固定 SHA-256 后嵌入应用，不从构建机器复制个人配置。
Apple Silicon 与 Intel 应分别在对应架构环境构建；本脚本不生成通用二进制。
GitHub Actions 的 `Build macOS installers` 使用独立的 macOS 15 ARM 与 Intel 主机，产物不包含源码归档。上传源码到远端仓库需要单独确认其可见性与授权。
构建产物尚未经过 Developer ID 签名或 Apple 公证，不作为已验收的公开安装包。
Windows 无法直接生成可运行的 Mac 应用包。

## 数据与密钥

- 工具箱设置：`~/Library/Application Support/CodexToolbox/`。
- Codex 数据：`~/.codex/`；尊重 `CODEX_HOME` 环境变量。
- API Key 和代理令牌：当前用户 Keychain，设置文件仅保存随机引用。
- 初次访问可能出现系统 Keychain 授权提示。拒绝访问会导致认证不可用，但已有密钥引用会保留。
- Windows DPAPI 密钥无法在 Mac 解密；迁移时需要重新输入密钥。

## Mac 实机验收清单

1. 窗口启动、文件选择器、关闭退出、重复启动限制。
2. 新建供应商保存密钥，重启后检测连接；拒绝 Keychain 访问后确认旧引用仍在。
3. 本地代理认证与 Codex 外部认证助手调用。
4. Codex 退出后进行会话操作，Codex 运行时确认操作被阻止。
5. `.app` 重启附加、菜单翻译、输入翻译与发送、静态背景/GIF/MP4。
6. 在 Apple Silicon 与 Intel 的实际目标系统分别验证。

这些检查未通过前，不应声称 macOS 全功能已经验证。
