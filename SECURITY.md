# 安全说明

## 受支持版本

当前仅支持最新的公开测试版。

## 密钥处理

- API Key 使用 Windows DPAPI 加密存储在 `%LOCALAPPDATA%\CodexToolbox\providers.json`。
- 密钥不写入 Codex `config.toml`，而是由命令认证助手按需输出到 Codex 进程。
- 密钥不通过子进程命令行传递，默认不记录代理请求内容。
- DPAPI 保护不能防止同一 Windows 用户下的恶意软件读取密钥。

## 报告漏洞

请不要在公开 Issue 中粘贴真实 API Key、`providers.json`、Codex 数据库或会话文件。在仓库启用 GitHub Security Advisories 后，请使用私密漏洞报告。
