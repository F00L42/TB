# run_00 一级 profiling 结果

## 1. 数据范围与结论边界

本轮是 Qwen3.6-27B、TP=4、DP=1、4 并发的 16 请求 Think Budget 测试。客户端明细为 [`dtb_..._11-31-18.csv`](../../test_results/dtb_2026-02-11_ReAct白盒-整体规划_2026-07-21_11-31-18.csv)，控制器明细为 [`controller_phases.jsonl`](controller_phases.jsonl)。

- **实测：** 客户端 E2E 合计 `2964.2027 s`，174 条 phase 的 wall 合计 `2962.1618 s`，只差 `2.0409 s`，即 `127.6 ms/请求`。phase 账本可解释 99.93% 的客户端总时延。
- **实测：** 158 个 engine child call 的 prompt/cache/generated token 总量与 `metrics.after.prom` 完全一致。
- **采集缺口：** `metrics.before.prom` 与 `metrics.after.prom` 的 `process_start_time_seconds` 分别是 `1784635949.04` 与 `1784631797.28`，不是同一进程，counter 不能直接相减。下文只把 after 的绝对计数用于与 phase JSONL 交叉校验。
- **采集缺口：** 没有保存 server log、服务器 TB/vLLM/vLLM-Ascend commit SHA、CANN 版本、模型 revision、随机种子和质量评分；因此不能判断质量非劣，也不能把本轮与旧 DTB 做严格代码版本归因。
- **环境备注：** 启动脚本在服务前清空 torch compile cache；`npu-smi` 还显示 devices 4–7 上有另一组进程，虽不与本轮选择的 0–3 重叠，但后续严格复现实验仍应记录共享 CPU/主机负载。

## 2. 环境版本

| 组件 | run_00 观测值 | 状态 |
|---|---|---|
| vLLM package / API | `0.23.0+empty` / `0.23.0`，editable `/vllm-workspace/vllm` | 版本匹配；实际 SHA 未采集 |
| vLLM-Ascend | `0.23.0rc1`，editable `/vllm-workspace/vllm-ascend` | 版本匹配；实际 SHA 未采集 |
| Python | `3.12.13` | 已采集 |
| PyTorch / torch-npu | `2.10.0+cpu` / `2.10.0.post2` | 已采集 |
| transformers | `5.5.4` | 已采集 |
| triton / triton-ascend | `3.5.0` / `3.2.1` | 已采集 |
| NPU / npu-smi | 4 × Ascend 910B4 / `25.0.rc1` | 已采集 |
| CANN | 未采集 | 下轮必填 |
| 模型 / tokenizer revision | 未采集 | 下轮必填 |

完整字段和缺口标记见 [`manifest.json`](manifest.json)。[`manifest.template.json`](manifest.template.json) 已保留本轮版本基线；后续轮次不能只因 package version 相同就认定源码相同。

## 3. 端到端模型工作账本

| Phase | 调用数 | wall 合计 | 占客户端 E2E | decode 合计 | 占客户端 E2E | 生成 token |
|---|---:|---:|---:|---:|---:|---:|
| decision | 16 | 0.160 s | 0.005% | — | — | 0 |
| initial_think | 16 | 164.436 s | 5.55% | 128.169 s | 4.32% | 6066 |
| iterative_think | 63 | 211.347 s | 7.13% | 150.262 s | 5.07% | 6212 |
| confidence_probe | 63 | **1922.167 s** | **64.85%** | **1864.566 s** | **62.90%** | **74871** |
| final_answer | 16 | 664.052 s | 22.40% | 649.986 s | 21.93% | 27188 |

### 3.1 隐藏 token 已证实是主成本

- **实测：** 客户端可见 token 合计 `39466`，其中模型思考 `12278`、最终答案 `27188`；隐藏 probe 另生成 `74871` token。
- **实测：** 总模型生成量实际是 `114337` token，平均 `7146.1 token/请求`，而客户端只报告平均 `2466.6 token/请求`。
- **实测：** 隐藏 probe 是可见 token 的 `1.90×`，占真实模型生成量的 `65.48%`。
- **聚合对照：** 同日 vanilla 可见生成量为 `70096`。run_00 虽少输出 `30630` 个可见 token，但加入隐藏 probe 后，真实生成量反而多 `44241` 个，即 `+63.11%`。
- **实测：** 请求 E2E 与 hidden probe token 的 Pearson 相关系数为 `0.933`；与总模型生成 token 的相关系数为 `0.960`。

因此 H1“隐藏 probe decode 是主要回归项”得到直接支持。继续压缩 Python 对象或日志不可能补回这一数量级的模型 decode。

### 3.2 Python/controller 不是当前主矛盾

- **实测：** 已记录的 `controller_cpu_ms` 合计只有 `354.951 ms`，平均 `22.2 ms/请求`。
- **实测：** phase 与客户端之间未解释差值平均 `127.6 ms/请求`，即使全部归入 controller/HTTP，也远小于 probe 的平均 `120.1 s/请求`。

`controller_cpu_ms` 尚不包含所有响应格式化和 event-loop 开销，不能视为完整 CPU profile；但数量级已经足以否定“继续做普通 Python 微优化即可解决 E2E”的判断。

## 4. Probe 有效性

### 4.1 置信度门几乎没有工作

- **实测：** 63 次 probe 的 `decision_score` 范围为 `0.012936–0.086135`，中位数 `0.024788`，没有一次达到 classification 的 `prob_th=0.90`。
- **实测：** 只有 3 次记录为 accepted，但三次 score 分别为 `0.057075 / 0.030287 / 0.041276`，显然不是置信度阈值放行。
- **源码确定：** 当前默认配置下，低于阈值仍可能由 consecutive-same gate 放行；现有 `TB_PHASE` 没有 `accept_reason`，所以本轮不能进一步区分实际原因。
- **实测：** 26/63 个 probe 生成满 2000 token；其余 37 个以 `</think>` 停止。平均每次仍生成 `1188.4` 个隐藏 token。

### 4.2 Bottom-K 与超长 probe 的组合需要重新校准

**源码确定：** 默认 `enable_value_min_confidence=true` 时，decision score 是答案 Value token 中最低 5 个 logprob 的均值再取指数。随着 trial answer 变长，候选 token 增多，bottom-K 分数只可能保持或下降；2000-token 自然语言答案要求 bottom-5 平均概率达到 0.90，门槛非常苛刻。

**假设：** 当前 `0.90 + Bottom-5 + 最长 2000 token` 的组合使置信度早停近乎不可达。证伪方法是在下一轮同时记录 `accept_reason`、平均置信度、Value-Min 原始值、Value token 数和 candidate 完整性，并对 `64/128/256/当前长度` 分层重标定阈值。

### 4.3 第四轮 probe 在当前状态机中是死工作

**源码确定：** 当 `trial_count + 1 == trial_answers` 时，本轮 probe 无论接受还是拒绝，下一状态最终都是 `_AnswerState`；probe prompt 不追加到主思考 prompt，`answer_list` 也不用于最终答案生成。因此在当前实现中，最后一轮 probe 不改变最终生成路径。

**实测反事实上界：** 第四轮共有 15 次 probe，消耗 `17152` 隐藏 token 和 `433.541 s` wall，占本轮客户端总 E2E 的 `14.63%`，平均 `27.10 s/请求`。直接跳过最后一轮 probe 后，按本轮 wall 静态扣除估算，平均时延仍约 `158.17 s`，所以它是高收益首改，但不足以单独达到 vanilla。

## 5. 任务分类异常

- **实测 + 源码重现：** 所有请求的 initial 对齐长度、100-token stride、4 轮上限和 2000-token probe 上限都与 `classification` 预算一致。
- **本地重现：** 当前分类器把 16/16 个故障诊断/整体规划 prompt 判成 classification。
- **源码确定：** `_CHOICE_RE = r'[A-D][\.、\)）]'` 带 `IGNORECASE` 且不要求行首/选项结构，会把长英文正文中的普通 `a.`、`b.`、`c.`、`d.` 当作选项。本批每条 prompt 出现 26–44 个这种匹配；`A.`/`B.`/`C.`/`D.` 强关键词还会重复贡献 classification 分数。

不能直接“修好分类器然后复测”：正确路由到 planning/fault_diagnosis 会启用 8 轮、3000–4000 token probe，在当前策略下可能进一步恶化时延。必须先给所有 task type 加全局 hidden-work 上限或缩短 probe，再修分类器并做质量验证。

## 6. Prefix cache 与调度

- **实测：** engine prompt token 合计 `591201`，cache hit `181248`，整体命中率 `30.66%`。
- **实测：** iterative/probe 命中率分别为 `32.78% / 33.03%`；缓存 token 只出现 `0` 或 `1536`，没有随 parent 的思考轮次增长。
- **实测：** prefill 总计 `143.205 s`，占客户端 E2E `4.83%`；queue 总计 `12.217 s`，占 `0.41%`；无 preemption。

H2“增长前缀已经大部分命中”被否定。`1536` 的固定命中更像只复用了跨样本公共静态前缀，没有复用 parent 的增长推理前缀，但根因尚未确定。

建议用单请求、并发 1 做 APC 诊断：若后续 phase 仍固定命中 1536，检查 child token 序列/block hash/cache salt 与生成 block 保留语义；若单并发能随轮次增长，则检查并发下的 KV 容量和淘汰。即使完全消除本轮所有 prefill，理论上也只回收约 `8.95 s/请求`，优先级仍低于减少 probe decode。

## 7. 优化优先级与实验方案

### P0-A：跳过不能改变控制流的最后一轮 probe

在 `_ConfidenceCheckState` 发起试答前判断：

```text
trial_count + 1 >= trial_answers
或剩余 API call 只够 current probe + final、已不够下一次 think
```

若成立则直接进入 `_AnswerState`。验证要求：相同 seed 下最终 prompt、可见 token、stop reason、答案和质量一致；4 并发 paired E2E 应稳定下降。

### P0-B：给隐藏模型工作独立硬预算

新增并审计：

- `max_hidden_probe_tokens`
- `max_probe_rounds`
- `max_total_model_generated_tokens`
- `max_controller_wall_ms`

不要继续让 `max_tokens_temp` 只表示可见思考预算。达到上限后必须保留 final answer 的调用空间。

### P0-C：短 probe + 质量非劣消融

保持本轮 task route 不变，依次测试 `256 / 128 / 64`，不要同时修改分类器。按本轮真实 token 静态计算：

| Probe cap | 最多保留的实际 probe token | 可减少隐藏 token | 占当前隐藏 token |
|---:|---:|---:|---:|
| 256 | 14810 | 60061 | 80.22% |
| 128 | 7429 | 67442 | 90.08% |
| 64 | 3717 | 71154 | 95.04% |

这是 token 上限反事实，不是已测时延收益。每个变体必须报告 candidate 完整率、Value-Min 分布、最终质量、paired E2E 和 final-answer token。

### P1：避免“完整 probe 后再完整 final”重复回答

如果 probe 已生成完整、格式合格且通过质量门，可考虑直接复用 candidate；本轮 final decode 另占 `649.986 s`。实施前必须先：

1. 聚合完整 trial 文本，不能只使用最后一个流式 chunk；
2. 记录 candidate stop/completeness/accept_reason；
3. 保证工具调用、JSON 和规划格式与原生 final response parser 一致；
4. 做质量非劣检验。

### P1：修正分类器，但在 hidden-work 上限之后

选项检测应要求独立选项行、至少 3 个不同标签，例如行首 `A.`/`B)`，不能在整段正文中搜索任意 `a.`。随后用人工标注的 task-type 集合报告 confusion matrix。修复分类器和预算缩短必须拆成两个实验变量。

### P2：APC 专项与低优先级 CPU 优化

先按第 6 节区分内容不一致和容量淘汰，再决定是否研究 resumable main chain。当前不应优先继续优化 JSON 序列化、浅拷贝或 MTP；MTP 保持生产默认配置。

## 8. 下一轮必须补采字段

1. TB、vLLM、vLLM-Ascend 的实际 `git rev-parse HEAD`、remote、dirty 状态。
2. CANN、模型和 tokenizer revision，服务器 `budget_config.yaml` SHA256。
3. 完整 sampling params、seed、到达方式、warmup 和 prefix-cache 初始状态。
4. `accept_reason`、task type、effective budget、Value-Min raw、平均 confidence、candidate token/completeness。
5. 同一 `/metrics` 进程连续性检查；若 `process_start_time_seconds` 变化，立即判定本轮 counter delta 无效。
6. 同 sample ID 的 vanilla 明细和预先定义的质量评分。
