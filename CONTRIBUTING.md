# 贡献指南

1. Fork 仓库并从 `main` 创建分支。
2. 不要提交 API Key、用户绝对路径、Codex 数据库、会话文件、日志或打包产物。
3. 修改核心逻辑时补充测试，然后运行单元测试和 `scripts/check_secrets.py`。
4. 与 Codex 内部数据相关的功能优先使用官方 app-server；不接受没有备份、校验和回滚的直接数据库写入。
