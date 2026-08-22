# 安全说明

## 受支持版本

当前仅支持最新的公开测试版。

## 非官方实验边界

- “会话路由实验”和“本地外观实验”默认关闭，不是官方稳定能力，也不代表 OpenAI 认可。
- 会话路由仅在 Codex 完全退出后执行，先预检和备份；它不会直接改写单会话 SQLite/JSONL。
- 归档/恢复通过 Codex app-server 尝试执行并在前后校验；其可用性随 Codex 版本变化，不应视为已公开承诺的稳定接口。
- 本地外观只连接 `127.0.0.1` 调试接口，只注入工具箱生成的受控 CSS；MP4 仅通过固定模板的视频元素和用户选定文件生成的受控 `data:` 媒体播放，不开放额外媒体端口。CDP 中用于创建/移除样式和固定媒体元素的代码是内置固定模板，不接收用户 JavaScript；样式仅作用于当前页面，不会注册为重载脚本。它不修改 WindowsApps、app.asar、Codex 配置、账号或会话内容。
- “修复已归档会话侧栏残留”是实验兼容修复：只处理 state 已确认 `archived=1` 的 ID，先备份并在操作后验证；若失败会停止并报告备份位置。
- 用户需自行遵守 OpenAI、第三方供应商和所在地区的规则。本项目不提供绕过登录、订阅、地区限制、权限或安全机制的功能。

## 密钥处理

- API Key 使用 Windows DPAPI 加密存储在 `%LOCALAPPDATA%\CodexToolbox\providers.json`。
- 密钥不写入 Codex `config.toml`，而是由命令认证助手按需输出到 Codex 进程。
- 密钥不通过子进程命令行传递，默认不记录代理请求内容。
- DPAPI 保护不能防止同一 Windows 用户下的恶意软件读取密钥。

## 报告漏洞

请不要在公开 Issue 中粘贴真实 API Key、`providers.json`、Codex 数据库或会话文件。在仓库启用 GitHub Security Advisories 后，请使用私密漏洞报告。
