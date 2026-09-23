# 回归 fixtures

这些输入从清理前 commit cb6d67dee5a65ad50959330e4c58d246ded35226 投影，来源路径、原 SHA256、
投影规则、保留字节与 SHA256 见 [provenance.json](provenance.json)。不包含可用于新服务器排名的历史指标。

| 目录 | 保留内容 | 验证目的 |
| --- | --- | --- |
| scenes/run_06_v3 | 13 CSV、633 条 QA_ID/原问题 | legacy 全部场景与配置路由 |
| scenes/legacy_regressions.csv | 4 条误分类原问题 | 防止旧分类问题回归 |
| intervention/g0 | 4 个小型 profile/fixture | 介入/stop/decoder 无模型接口 |
| parser_tail | 60 DTB 响应投影、原工具 schema、必要流式 text/token IDs（gzip） | 全 parser 链及 47 条历史失败负对照 |
| tool_call | 原有 chunks-4-fail.jsonl | tool 参数分块回归 |

parser_tail 历史数据不是正确服务器输出，47 条公开 reasoning 缺口仍应被负对照识别。
不得因缺少 fixture skip 测试。大日志可从来源 commit 查找；来源文件已不在当前树中。
需要实际发给模型的短数据使用 [validation/replay](../../validation/replay/README.md)。
