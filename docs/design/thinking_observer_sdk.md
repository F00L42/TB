# Session D 交付入口

独立分支 `codex/thinking-observer`，基于 `f342e127`。

- [独立 SDK 安装入口](../../thinking_observer/README.md)
- [模块设计与提取依赖](../../thinking_observer/docs/design.md)
- [Public API](../../thinking_observer/docs/api.md)
- [服务器部署/验证手册](../../thinking_observer/docs/server_runbook.md)
- [本地验证证据](../../thinking_observer/docs/validation.md)

用户确认范围为思维链增量观察、合法位置停止、自定义引导续写；不实现重复检测算法，不撤回已流出内容。没有改动生产 DTB、Session A 或只读上游 checkout。目标设备运行与性能验证为 pending_remote。
