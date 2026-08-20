# 更新日志

## 0.1.0-beta

- 初始公开测试版。
- 全局供应商切换与本地协议翻译。
- Windows DPAPI 密钥存储与 Codex command auth。
- 通过官方 app-server 归档和恢复会话，操作前自动备份。
- 通过官方 `thread/resume` 为单个会话固定通道或设置跟随全局，并在失败时尝试恢复原通道。
- 不直接改写单会话 SQLite/JSONL，不替换 Codex 界面资源。
