# 隐私说明

Codex 工具箱不运行任何项目自有的远程后端，不收集遥测、账户、会话或 API Key。

为实现功能，程序会在本机：

- 读取并备份 `~\.codex\config.toml`；
- 在用户明确点击归档/恢复时，读取 Codex 会话索引和文件，并调用 Codex 官方 app-server；
- 在 `%LOCALAPPDATA%\CodexToolbox` 保存供应商配置、DPAPI 密文和操作备份；
- 在使用第三方供应商时，把模型请求发往用户配置的 Base URL。

本地翻译代理默认不写日志。只有用户主动设置 `CODEX_TOOLBOX_DEBUG_PROXY=1` 时才会写调试日志，且不记录完整请求体。
