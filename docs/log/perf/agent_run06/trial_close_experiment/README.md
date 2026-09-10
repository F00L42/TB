# Trial 显式闭合：单变量实验

2026-09-10。用户决定先给 trial prompt 添加 `</think>`，观察能否改善停止决策与 probe 成本。

状态：**实验补丁和回放输入已准备，本仓库生产源码未应用该补丁；未执行目标服务器实验。** 本地基线和实验版各通过 148 项测试、4 项子测试，详见 [local_validation.json](local_validation.json)。

## 1. 改哪里，不改什么

原 trial 后缀 A：

```text
I have to give the answer based on the thinking directly without explanation now.
**Final Answer**
```

实验后缀 B（精确追加 `</think>\n\n`）：

```text
I have to give the answer based on the thinking directly without explanation now.
**Final Answer**
</think>

```

选择放在**现有诱导语之后**，使模型从明确的思考闭合状态开始 trial。既不把整段诱导语改成 guided-final，也不移动 `**Final Answer**`，避免同时测试多个 prompt 变化。

- trial 仍为 **50 token**，平均概率 gate 仍为 **.93**，T850/stride500/N5/min1/budget20000 不变。
- 只改变隐藏 child 的 prompt；主 P/H、reasoning 状态和外部 parser 不提前闭合。reject 后从原 H 继续思考，accept 后仍采用原有 fresh-final；不复用 trial。
- **不把 `</think>` 加入 trial 的 stop 参数**。输入中闭合思考与让输出遇到边界时停止是两回事。
- 不修改 abort/drain、短旁路、parser patch、final cue、默认 YAML 或采样参数。
- `_room()` 和 trial 的实际 context limit 都按同一个 `TRIAL_PROMPT` 编码长度计算，追加后的容量成本自动纳入；近上下文上限的请求可能因此不再有 probe 容量，应单列而不是判为性能优化。
- 实验政策版本为 `dtb-algorithm-v4-trial-close-exp1`，A 为 `dtb-algorithm-v4`。不增加请求级开关，避免把一次性实验做成新的生产分支。

旧 [finalization 设计](../../../../design/think_budget_finalization_compatibility.md) 中“不修改 probe”的决策是为了隔离当时的 final 修复，并非已证明 trial 不应该闭合。本次是独立重开该问题，不改写历史 run 的含义。

## 2. 输入与复现边界

[requests.jsonl](requests.jsonl) 包含 run-06 所有 13 条发生过 probe 的**完整 HTTP 请求体**：8 个标题、5 个主/子 Agent；约 1.56 MiB。每行原始来源、request-body hash 见 [input_manifest.json](input_manifest.json)。

- 这不是捕获的 P/H token checkpoint；重新采样后可能不再触发 probe。没有触发时只能算旁路样本，不能证明 trial 闭合有效。
- A/B 必须使用同一文件、相同行与完整上下文。源是 DTB 当时的上下文，不应与另一条 Vanilla 历史上下文直接比较。
- 回放器不会执行任何模型返回的工具。业务质量需另行编译/DT；期望工具名未硬编码为旧样本的工具名，避免把正常轨迹变化判为协议故障。
- 标题的 E2E 收益纳入统计；标题质量以单行、非空、≤50 字符、无解释/结构泄漏、主题准确判定，不要求与旧标题完全同字。

重新生成与验证输入：

```bash
python -X utf8 docs/log/perf/agent_run06/trial_close_experiment/prepare.py
```

建议先 smoke 行 `8,9,11`：case 28 标题、case 28 JSON 提取子 Agent、case 34 标题，覆盖低于 gate 的自然短 trial 与高分接受 trial。

## 3. 服务器执行

### 3.1 冻结环境，先跑 A

在独占测试服务中执行；记录 TB/上游 SHA、模型与 tokenizer 身份、patch/YAML hash、采样默认值、并发、cache/预热方式。保留 `stream_interval=1`，不测其他值。

第一轮设置 `OBSERVABILITY.mode=phase` 并重启，保存 **DTB_PHASE + DTB_REQUEST_SUMMARY**。A/B 两侧模式、服务/代理日志开关一致。确认实际被服务加载的是本工作树/安装路径的 DTB，不只检查当前 SSH 窗口 cwd。

先确认 A 身份：

```bash
python -c "from dtb.config.schema import POLICY_VERSION; from dtb.reasoning.generation_states import TRIAL_PROMPT; print(POLICY_VERSION); print(repr(TRIAL_PROMPT)); assert POLICY_VERSION == 'dtb-algorithm-v4'; assert '</think>' not in TRIAL_PROMPT"
```

服务器根目录运行（URL/model 按实际端点替换；需要认证使用 `OPENAI_API_KEY`，不写入归档）：

```bash
python -X utf8 tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_run06/trial_close_experiment/requests.jsonl \
  --mode both --order alternating --repetitions 4 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model qwen3.6-27b \
  --fixed-budget-tokens 20000 --fixed-check-length 850 \
  --fixed-think-stride 500 --fixed-num-thinking 5 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.93 \
  --request-id-run trial-open-a \
  --output-dir docs/log/perf/agent_trial_close/run_server_01/open
```

smoke 可额外加 `--lines 8,9,11 --repetitions 1` 并换独立 run ID/输出目录。先用 `--list-request-ids` 可验证输入并打印 ID，不发请求。

上述完整 A 一共 104 个请求：13 行×4 次×V/A。Vanilla 是环境对照，**不是旧 trial 的替代对照**；真正的单变量比较是下面 B 的 DTB 与本步骤 A 的 DTB。`--order alternating` 只交替当前部署的 V/DTB，不会替你交替两个算法部署。

### 3.2 应用实验补丁并确认 B

先停服务，不在运行中热改模块。以下补丁只修改 DTB trial 后缀、policy 身份和相应测试，不修改 vLLM 上游 checkout：

```bash
git apply --check docs/log/perf/agent_run06/trial_close_experiment/closed_trial.patch
git apply docs/log/perf/agent_run06/trial_close_experiment/closed_trial.patch
python -m pytest -q tests/test_dtb_config.py tests/test_dtb_state_machine.py tests/test_dtb_token_prompt.py tests/test_dtb_telemetry.py tests/test_dtb_intervention.py
python -c "from dtb.config.schema import POLICY_VERSION; from dtb.reasoning.generation_states import TRIAL_PROMPT; print(POLICY_VERSION); print(repr(TRIAL_PROMPT)); assert POLICY_VERSION == 'dtb-algorithm-v4-trial-close-exp1'; assert TRIAL_PROMPT.count('</think>') == 1; assert TRIAL_PROMPT.endswith('</think>\n\n')"
```

若 patch check 不通过，说明目标源版本/改动不一致，应检查 diff，不用强行覆盖。按原有安装/同步方式使服务加载新 DTB，再重启。

**模型侧检查**：使用服务实际 tokenizer 确认 `encode('</think>', add_special_tokens=False)` 为一个配置的 think-end ID（本模型为 248069），并确认完整 trial suffix 中确实只有一个该 ID；普通字符拼写不能冒充结构 token。这里仍用原有 `child_prompt → encode_prompt_suffix`，不执行 P/H 全量 decode/re-encode。本地 FakeTokenizer 单测只验证程序契约，不能替代此服务器检查。

### 3.3 B 使用相同输入和参数

```bash
python -X utf8 tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_run06/trial_close_experiment/requests.jsonl \
  --mode both --order alternating --repetitions 4 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model qwen3.6-27b \
  --fixed-budget-tokens 20000 --fixed-check-length 850 \
  --fixed-think-stride 500 --fixed-num-thinking 5 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.93 \
  --request-id-run trial-closed-b \
  --output-dir docs/log/perf/agent_trial_close/run_server_01/closed
```

A/B 合计 208 请求，单请求并发。未显式覆盖 temperature/seed，保持原请求采样方式并冻结服务默认值；因此不是相同 P/H/S 的确定性实验。首轮有积极信号后做反向顺序 B→A 的第二块以减少时序/缓存偏差；不能只凭一次部署顺序宣布稳定收益。

### 3.4 回滚

停止测试服务后，只反向应用本实验补丁，再按原安装方式部署和重启：

```bash
git apply --reverse --check docs/log/perf/agent_run06/trial_close_experiment/closed_trial.patch
git apply --reverse docs/log/perf/agent_run06/trial_close_experiment/closed_trial.patch
```

若文件另有修改而 check 失败，人工核对，不用 `git reset/checkout` 丢弃其他改动。保留全部实验结果与 manifest。

## 4. 如何判效果

按输入 case_id 配对 A/B 的 DTB 请求，Vanilla 单列检查环境漂移。相同 attempt 只是重复编号，不表示采样轨迹相同。

1. **必须先取得 B 真 probe**：summary 新 policy、`triggered`、hidden>0；日志中 trial 仍最多 50 token，没有外部 abort、重复 terminal 或 XML/工具参数错误。
2. **停止是否更有效**：首 probe 得分、首 probe 接受率、自然 stop/length 比、实际 probe 次数、到达 final 前的可见思考长度。不把“更容易接受”当成唯一目标。
3. **是否更省成本**：hidden token、probe wall/prefill/decode、每请求 E2E/TTFT、响应 completion；标题与业务分列。平均 probe 得分只做辅助，不能忽略减少或新增的 probe 轮次。
4. **质量**：逐个复核标题；业务代码/JSON/工具动作单独检查，必要时做真实编译/DT。新出现的空 final、结构泄漏、错误动作或已知功能回退不因省 token 而忽略。
5. **归因边界**：phase 不保存 trial 原文。服务普通 `--enable-log-outputs` 是 serving 输出日志，不能保证包含直接 `engine_client.generate()` 的隐藏 trial。若没有具体 child 对应的 raw trial，不声称已证明“原版一直在思考、B 都在回答”；首轮仍可判断 gate/成本/对外质量是否改善。原文取证作为后续独立诊断，不为此扩张本次单变量补丁。
6. **正式 E2E**：本轮 phase-on 用于算法机制审计。候选有收益后，用相同输入、同一修订环境补 off 配对及真实 Agent 任务；不把两个模式的时间拼接。

回传 `open/closed` 的完整 `summary.jsonl`、`chunks.jsonl`、`paired_summary.json`、回放 manifest（沿用工具实际文件名）和包含两种 DTB 事件的服务器分片；另保存实验部署 manifest。不要只回传自动 RESULT 的均值，它当前只比较每组 V/DTB，尚未做跨组 A/B。

## 5. 本地验证与边界

```bash
python -X utf8 docs/log/perf/agent_run06/trial_close_experiment/validate_patch.py
```

脚本复制相关代码/测试到新建临时目录，在副本分别运行基线与补丁测试；不改当前生产源码，不启动目标模型。已有测试覆盖接受/拒绝后续写、trial 隐藏、主 prompt 不混入 trial、外部额度/单终态、短旁路与取消交接。该实验补丁不宣称修复 G0，也不实现 trial 复用。

| 日期 | 变更 |
| --- | --- |
| 2026-09-10 | 准备只追加 trial think-end 的独立 patch、13 条真实输入及 A/B 执行步骤；旧版/实验版各 148 tests + 4 subtests 本地通过。 |
