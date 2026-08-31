# run-04 Agent 可用性故障与 paired replay 方案

## 结论

新版 Vanilla run-04 已完成精确关联：9/9 task、27/27 个实际启动 agent、29/29 proxy
session、376/376 request 全部映射且无歧义，9 个任务均生成报告。因此旧 Vanilla run-03
缺完整 request body 的证据限制已消除，可以用两侧真实历史上下文构建回放。

当前先关闭系统可用性门，不进入 DTB 时延参数排名：

- case 35、36 的报告缺失是 Agent 流程真实中断，不是评测报告抓取失败；
- case 36 还有 3 次 `task` arguments JSON 截断；
- case 26、28、3 在新版 Vanilla 均为优秀，而 DTB 均为不合格，已构成同 case 的质量退化；
- 单轮 HTTP replay 能裁决工具协议和异常终止，不能替代执行工具、编译和测试的完整 LightCode
  质量门。

## 5 个不合格或缺失任务

| Case | DTB run-04 | Vanilla run-04 | 根因与证据结论 |
| --- | --- | --- | --- |
| 26 | 不合格 | 优秀，16/16 测试通过 | DTB 生成代码有 3 个编译错误：漏 `new`、两处不存在的 `OssLog.exception()`；还多生成测试文件且缺版权头。是生成质量错误，不是 parser/tool transport 故障。 |
| 28 | 不合格 | 优秀，12/12 测试通过 | DTB 报告把编译失败归为 base commit 预存接口问题；新版 Vanilla 在同 branch/base 完成生成、编译和测试，已反证“不可避免的预存编译问题”。需在完整 LightCode A/B 中复核 DTB 生成/工作区路径。 |
| 3 | 不合格 | 优秀，15/15 测试通过 | DTB 给 `ReportTemplateMapper.xml` 写入 Java 风格版权注释，导致 MyBatis XML 解析失败和 15/15 测试报错；Vanilla 正常生成 XML。是代码生成格式错误。 |
| 35 | 无报告 | 优秀，36/36 测试通过 | DTB request `af590ee7efa14439` 在明确准备 checkout 时以 `stop` 结束且无 tool call；任务没有进入实际评测。Vanilla request `01263fedacc6427e` 在相同阶段发出 `bash git checkout`。 |
| 36 | 无报告 | 良好，3/4 测试通过且报告完整 | 3 个 DTB request 的 `task` arguments 连续截断；重试成功后，request `1e1d5c135dfb473f` 又在准备写报告时仅输出 `##` 并 `stop`。Vanilla request `58020b784c544d22` 正常委派生成任务，`a60accbd88004f5a` 正常写报告。 |

case 35/36 的两条提前 `stop` 在客户端只能证明“parser 后无工具调用并终止”。没有对应
stream-audit 时，仍不能在模型原始选择、DTB finalization 和 parser 丢失之间定责。

## 回放输入

`replay_spec.json` 定义 8 个语义 pair，构建出的 `input/cases.jsonl` 有 16 条真实请求：

- 第 1-10 行：5 个 P0 协议故障 pair；每个 pair 各含 DTB 故障上下文和 Vanilla 健康上下文；
- 第 11-16 行：case 26/28/3 的代码 mutation turn，仅作 `quality_probe`，不进入时延排名。

不同历史来源的两条上下文只用于复现边界，不直接互算时延。每一行在回放时分别切换
`enable_think_budget=false/true`，这两个请求才是输入完全相同的 A/B pair。

## 服务器执行顺序

先生成 DTB request ID 白名单并配置 `OBSERVABILITY.mode=stream_audit`：

```bash
python tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl \
  --end 10 --repetitions 30 \
  --request-id-run run04-p0 \
  --list-dtb-request-ids
```

重启并记录 manifest 后，单并发、交替顺序执行：

```bash
python tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_tool_call_run04_paired_replay/input/cases.jsonl \
  --end 10 --repetitions 30 \
  --request-id-run run04-p0 \
  --order alternating \
  --output-dir docs/log/perf/agent_tool_call_run04_paired_replay/run_server_01
```

默认 DTB 参数已与 run-04 对齐：`budget=20000`、`check=850`、`stride=500`、`num=5`、
`min=1`、`prob=0.93`。不要同时改参数，否则无法裁决故障是否仍存在。

准入标准：P0 的 JSON、schema、预期 tool name、XML、index、terminal、finish reason hard
failure 全为 0；随后相同配置完整跑 9-task LightCode smoke，必须 9/9 生成报告，case 26/28/3
质量不得低于 Vanilla。只有两道门都通过，才切换到 `OBSERVABILITY.mode=off` 做正式 paired
latency；另用独立 `phase` run 闭合 hidden probe 和各 phase 工作量。
