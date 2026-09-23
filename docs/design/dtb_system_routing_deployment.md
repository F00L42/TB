# System 路由部署与回滚

当前包与 serving patch 必须成对升级，不能把新 controller 搭配旧 patch。
固定目标 vLLM 0.23.0 / vLLM-Ascend 0.23.0rc1；真实环境另外记录两个仓库 SHA、软件版本、模型修订。
唯一 patch 制品与应用顺序见 [patch README](../../patches/vllm/v0.23.0/README.md)。

## 配置选择

| YAML | 用途 | 状态 |
| --- | --- | --- |
| budget_config.yaml | 既有 legacy_text 默认策略 | 本次不改默认算法/参数 |
| budget_config_quant.yaml | 量化增训 13 场景候选 | 必须在对应权重上验收 |
| budget_config_noemate_750.yaml | main 身份 DTB 750，followup/compaction/拒识 native | 自动路由服务器验收 pending_remote |
| budget_config_noemate_native.yaml | Noemate 保守全原生 | fixed 总预算仍优先，可用于显式探测 |

YAML 只在启动读取。停服后备份原规范文件，选择候选复制为 `dtb/config/budget_config.yaml`，
记录原/新 SHA，然后按现有生产命令重启。保留候选文件名，不运行中覆盖，不让客户端替服务选择配置。
在独立部署 checkout 操作，仓库的 third_party 只读。

```bash
mkdir -p artifacts/deploy-backup
cp dtb/config/budget_config.yaml artifacts/deploy-backup/budget_config.before.yaml
cp dtb/config/budget_config_noemate_750.yaml dtb/config/budget_config.yaml
sha256sum dtb/config/budget_config.yaml patches/vllm/v0.23.0/*.patch
```

备份目录应为本次部署新建，防止覆盖以前备份。应用补丁前 git apply --check；任何失败立即停止，
不使用 --reject。已有修改过的上游应对齐完整交付版本，不盲目重复应用 patch。

优先级与身份边界见[路由设计](dtb_system_scene_native_routing.md)。服务关闭/请求 enable_think_budget=false
原生；有效 fixed_budget_tokens 强制 DTB；其他 fixed 单字段只覆盖预算，不改变 native action。

## 验收和回滚

先跑 [本地门禁](../../README.md#本地验证)，再用 [7 条合成 replay](../../validation/replay/README.md)
测试自动/固定预算/probe、流式/非流式、工具与 tool 历史。合成 system 前缀仅测试路由接口，不代表实际 Agent 质量。
自动路由语义必须在单独 phase run 审计；正式性能使用 off。
错误、截断、XML 泄漏、质量退化、路由缺失/重复、隐藏 probe 账本不闭合时停止候选晋级。
性能不满足时保留现有生产策略，不能把短样本 smoke PASS 视作性能通过。

回滚时停服，恢复备份 YAML 及部署前成套 controller/patch，重启并做原生 smoke。
不要只改 YAML 来掩盖 controller 与 serving 不兼容。记录回滚 SHA 和本次失败样本。
