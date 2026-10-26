# macOS test kit / 苹果电脑测试说明

这是源码测试包，**不是已经编译完成的 `.app` 安装包**。Apple Silicon 和 Intel 均可用各自的本机 Python 运行；尚未经过 Mac 实机验证。

## 启动测试

1. 安装 Python 3.11 或更新版（建议 3.11–3.13，https://www.python.org/downloads/macos/）。安装官方 Codex 桌面版，通常在 `/Applications/Codex.app`。
2. 解压 ZIP。打开终端，输入 `cd `，把解压后的文件夹拖进终端，然后按回车。
3. 输入以下命令并回车：

```bash
bash Test-on-Mac.command
```

首次启动会联网安装依赖并运行兼容性检查，可能需要几分钟。窗口关闭后工具箱退出。再次测试仍使用同一条命令。

## 当前范围

- 测试目标：Codex 界面、供应商配置、本地代理、Keychain 密钥存储、窗口启动和退出。
- Codex 表层翻译和背景实验另外需要 PowerShell 7（https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-macos）。这些功能还需 Mac 实测。
- Claude 桌面集成目前仅支持 Windows；本包不提供 Gemini 翻译。
- 启动网络检测在 Mac 上的系统代理/TUN 识别尚不完整；提示未检测到代理不能证明网络泄漏。
- 不包含发送者的 API Key、账号、聊天记录或运行配置。需要测试供应商时，请自行填写密钥。

## 编译可双击的应用

在相同文件夹中执行：

```bash
bash build_macos.sh
```

生成 `dist/Codex工具箱.app` 和带本机架构名称的 ZIP。构建需要联网安装依赖。产物没有 Developer ID 签名或 Apple 公证；本包不会修改 Gatekeeper 或其他系统安全设置。

## 反馈

请记录：Mac 芯片型号、macOS 版本、Python 版本、失败步骤，以及终端最后一段错误或截图。先测试窗口能否打开、首页是否显示、关闭能否退出，再测试供应商和翻译。分享截图前遮住密钥、邮箱及聊天内容。

More detail: `docs/macOS使用说明.md`. Source integrity: `source-manifest.json`.
