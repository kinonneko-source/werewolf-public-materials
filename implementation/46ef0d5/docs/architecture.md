# 实现决议

任务 7e211566-e89a-4594-9d79-8db7fe61208c；附件指南全篇读取，版本口径 2026-10-23。

采用标准库 Python + SQLite + 宿主可信 CLI，不将 SDK Agent、模型记忆或平台收件箱作为状态主库。SDK wheel 和上游文档继续引用准备任务唯一原件，不复制 SDK，不安装额外运行依赖。

事件、状态、outbox 在同一事务；规则封闭在 Engine；宿主通过认证收件箱元数据驱动 InboxAdapter。未接入真实游戏或发送；TEST-only 是这一版本的硬限制，不可通过 test=false 绕过。

本地 skill 为已部署引擎提供调用流程。社区包单独命名为 werewolf-notes，只提供规则和人工记录说明；不能等价自动主持。未伪造远程 MCP、partner 权限或社区安装成功。插件市场服务端 dry-run 需授权接口；缺少接口时只执行本地非权威包检查。

准备任务的 architecture-pending.md 保留为历史证据，不再阻塞开发。本次代码与脱敏补证 a35ec3d2 分开交付。
