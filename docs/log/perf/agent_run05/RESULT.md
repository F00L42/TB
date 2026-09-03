# LightCode agent run-05 结果分析

## 1. 结论

run-05 **没有达到“正确率不变、端到端时延降低”的目标**。

- 关联完整：Vanilla 的 27/27、DTB 的 25/25 个实际启动 agent session，以及两侧全部任务请求均已关联。DTB 另有 1 条无 task anchor 的标题生成请求，不进入任务指标。
- 按要求排除已知 `stop="</think>"` 碰撞导致缺报告的 case 36 后，平台严格口径正确率从 Vanilla `6/8=75.0%` 降至 DTB `5/8=62.5%`。
- case 3 的 DTB 失败是 Maven 内部依赖不可达，不能归为模型代码错误。若将它标成 unresolved 并从敏感性口径中排除，两侧均为 `5/7=71.4%`；但这不能改写严格平台结果，也不能证明逐样本质量非劣。
- 排除 case 36 后，8 个任务的累计代理侧 LLM E2E 从 `1,991.654 s` 增至 `2,821.750 s`，增加 `830.096 s`（`+41.68%`）；`start_time → end_time` 累计评测 wall time 从 `3,956.451 s` 增至 `5,100.037 s`，增加 `1,143.587 s`（`+28.90%`）。
- 同一口径下，LLM 请求数从 272 增至 404（`+48.53%`），响应 completion token 从 197,964 增至 231,673（`+17.03%`）。响应 usage 不含 hidden probe，因此这不是 DTB 总模型工作量，真实增量只能在 phase 账本 run 中裁决。
- DTB 变快的 case 2、3、26 都没有形成“双方正确且 DTB 更快”的证据；双方均为优秀的 case 28、29、34、37 全部变慢。
- 9 场景 Vanilla 的 301 个请求中，292 个（97.01%）reasoning 不超过当前首次检查长度 850。用同固定参数的 run_server_02 natural-short paired replay 校准，当前“自然结束后停下并 fresh-final”的中心成本约为 `0.66 s/请求`；映射到 Vanilla 轨迹约增加 `192.7 s`，相当于 Vanilla LLM E2E 的 `9.0%`、评测 wall 的 `4.5%`。这是带 stream-audit 与生成随机性的估算，不是 run-05 phase 实测。

因此下一轮不应直接把当前六个 fixed 参数写入新 scene，也不应继续无边界的 fixed 网格搜索。先完成 token-stop 正确性门，再验证“短推理同请求直通、只有长推理才介入”的 deferred-intervention 策略。

## 2. 输入、身份与证据边界

输入：

- Vanilla 透传：`proxy/results/qwen/local-task29/local-apiserver/vanilla/run-05`
- Vanilla 评测：`lightcode/session_logs_0902_vanilla`
- DTB 透传：`proxy/results/qwen/local-task29/local-apiserver/dtb/run-05`
- DTB 评测：`lightcode/session_logs_0902_dtb`
- 关联产物：本目录的 `vanilla_correlation/`、`dtb_correlation/`

两侧 9 个主 agent 初始 prompt 的 SHA-256 按 case 全部相同，任务级输入可以配对。LightCode 按用户说明为单并发执行。

DTB 的 443 条原始请求均实测携带同一组参数：

```text
enable_think_budget=true
fixed_budget_tokens=20000
fixed_check_length=850
fixed_think_stride=500
fixed_num_thinking=5
fixed_min_think_tokens=1
fixed_prob_th=0.93
```

证据限制：

- run 目录没有实验级 manifest，无法在本地证明模型、服务 commit、两份 patch hash、sampling/seed、cache 和服务器负载完全一致；以下跨 variant 差异是实测观察，不能把所有波动单因果归于 DTB。
- 没有 DTB phase telemetry，不能分解 hidden probe、queue、prefill、decode 和控制器 CPU，也不能直接测量 fresh-final 的 TTFT/prefill 成本。
- Agent 轨迹在首轮输出后会分叉，任务级 LLM E2E 是产品指标，不是相同请求的控制器微基准；机制归因必须另做 recorded-prompt paired replay。

## 3. 关联与完整性

| 项目 | Vanilla | DTB |
| --- | ---: | ---: |
| LightCode 任务 | 9 | 9 |
| 实际启动 agent session | 27/27 | 25/25 |
| 已关联 proxy session | 29/29 | 28/29 |
| 已关联任务请求 | 301/301 | 442/442 |
| 无 task anchor 的辅助请求 | 0 | 1 |
| 有评测报告任务 | 9/9 | 8/9 |

DTB 未关联的是 `proxy_session_id=4a934be2e837` 的单条 “Generate a title for this conversation” 请求；它不含 case/task anchor，不计入任何任务的请求数、token 或 E2E。

## 4. 逐任务结果

时间均为秒；`LLM` 是该 task 所有关联代理请求的 `metrics.e2e_ms` 之和，`评测 wall` 是 `evaluation_results.json` 的 `end_time-start_time`。

| Case | Vanilla / DTB 等级 | 请求 V/D | LLM V/D | LLM 变化 | 评测 wall V/D | wall 变化 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 2 | 优秀 / 不合格 | 42 / 24 | 260.16 / 280.67 | +7.9% | 495.08 / 450.72 | -9.0% |
| 3 | 优秀 / 不合格（依赖不可达） | 45 / 33 | 297.31 / 278.05 | -6.5% | 503.17 / 414.78 | -17.6% |
| 26 | 不合格 / 不合格 | 24 / 17 | 203.11 / 133.68 | -34.2% | 392.58 / 285.89 | -27.2% |
| 28 | 优秀 / 优秀 | 41 / 127 | 340.50 / 796.79 | +134.0% | 641.83 / 1,430.78 | +122.9% |
| 29 | 优秀 / 优秀 | 37 / 57 | 284.30 / 381.12 | +34.1% | 538.33 / 539.41 | +0.2% |
| 34 | 优秀 / 优秀 | 29 / 32 | 137.29 / 218.34 | +59.0% | 443.80 / 484.09 | +9.1% |
| 35 | 不合格 / 优秀 | 26 / 82 | 283.89 / 479.07 | +68.8% | 416.42 / 834.05 | +100.3% |
| 36 | 不合格 / 缺报告（已知 stop 碰撞） | 29 / 38 | 151.92 / 283.33 | +86.5% | 345.81 / 590.68 | +70.8% |
| 37 | 优秀 / 优秀 | 28 / 32 | 185.10 / 254.02 | +37.2% | 525.24 / 660.32 | +25.7% |

### 4.1 全量与排除口径

| 口径 | Vanilla | DTB | DTB - Vanilla |
| --- | ---: | ---: | ---: |
| 9 任务严格优秀数 | 6/9 | 5/9（另 1 缺报告） | -1，且新增 1 个协议 hard failure |
| 9 任务 LLM E2E | 2,143.578 s | 3,105.085 s | +961.507 s（+44.85%） |
| 9 任务评测 wall 累计 | 4,302.263 s | 5,690.721 s | +1,388.458 s（+32.27%） |
| 9 任务最早 start 至最晚 end | 5,996.069 s | 7,880.398 s | +1,884.329 s（+31.43%） |
| 排除 case 36 后优秀数 | 6/8 | 5/8 | -1（-12.5 pp） |
| 排除 case 36 后 LLM E2E | 1,991.654 s | 2,821.750 s | +830.096 s（+41.68%） |
| 排除 case 36 后评测 wall | 3,956.451 s | 5,100.037 s | +1,143.587 s（+28.90%） |
| 再排除 case 3 基础设施项 | 5/7 | 5/7 | 0；质量仍有双向转移 |
| 再排除 case 3 后评测 wall | 3,453.277 s | 4,685.257 s | +1,231.980 s（+35.68%） |
| 再排除 case 3 后 LLM E2E | 1,694.346 s | 2,543.696 s | +849.350 s（+50.13%） |

最早 start 至最晚 end 包含任务之间的空档，只作为整批占用时间参考；单并发产品工作量的主要口径是逐任务 `start→end` 累计。

## 5. 正确率与输出分析

排除 case 36 后的质量转移：

| 类型 | Case | 数量 |
| --- | --- | ---: |
| 两侧优秀 | 28、29、34、37 | 4 |
| 仅 Vanilla 优秀 | 2、3 | 2 |
| 仅 DTB 优秀 | 35 | 1 |
| 两侧不合格 | 26 | 1 |

具体归因：

- **case 2：真实 DTB 代码质量回归。** DTB 代码错误调用 `template.getBasicInfo().getStatus()`，并缺少 `reportTemplateDao.selectByTemplateId()`；主代码编译失败。Vanilla 编译通过且 44/44 测试通过。
- **case 3：基础设施 unresolved。** DTB 无法访问内部 Maven 制品库，未完成编译和 DT；报告人工审查认为代码结构基本正确，但同时记录了异常处理差异。严格平台口径仍是不合格，不能静默判对；敏感性分析单独排除此项。
- **case 26：两侧均为模型代码错误。** Vanilla 写错文件路径，DTB 生成非法的 `catch (ServiceException | Exception e)`；均未执行 DT。
- **case 35：DTB 质量改善但流程更长。** Vanilla 因缺少 checked exception 声明编译失败；DTB 编译通过并通过 36/36 测试。DTB 随后完成了手工应用 test patch 等完整评测步骤，不能把相对提前失败的 Vanilla wall time 当作相同工作量基线。
- **case 28、29、34、37：两侧均通过。** 这四个真正质量可比的任务中 DTB 的 LLM E2E 和评测 wall 全部更慢。
- **case 29 的评测范围不一致。** Vanilla 报告 87/87，DTB 报告 8/8；两侧均判优秀，但下轮应固定同一 test patch/测试命令并在 manifest 记录，避免等级相同掩盖测试覆盖差异。

严格结论是：只排除已知 stop hard failure 后，run-05 质量下降；若再把 case 3 作为基础设施 unresolved 排除，聚合正确数持平，但 case 2 与 case 35 形成反向转移，仍需要重复实验确认稳定性。

## 6. 时延与模型工作量分析

排除 case 36 后：

| 指标 | Vanilla | DTB | 变化 |
| --- | ---: | ---: | ---: |
| LLM 请求数 | 272 | 404 | +132（+48.53%） |
| 代理侧 LLM E2E | 1,991.654 s | 2,821.750 s | +830.096 s（+41.68%） |
| 响应 completion token | 197,964 | 231,673 | +33,709（+17.03%） |
| proxy 本地 reasoning token | 33,146 | 42,537 | +9,391（+28.33%） |
| 评测 wall | 3,956.451 s | 5,100.037 s | +1,143.587 s（+28.90%） |

LLM E2E 增量约占评测 wall 增量的 72.6%，但这只是差额比例，不是 phase 因果分解。其余差异包含工具执行、编译/测试、agent 分支和平台等待。

主要放大项：

- case 28 单项增加 `456.29 s` LLM E2E，占 8-task 总增量的 55.0%。请求从 41 增至 127，主 agent 请求从 32 增至 118，bash 工具轮次从 15 增至 70；轨迹显示它反复修复基线编译问题、stash 后丢失修改和手工应用 test patch。这是 agent 轨迹放大，不是已观察到的 parser 故障。
- case 35 增加 `195.18 s` LLM E2E；请求从 26 增至 82，主 agent 从 18 增至 73。DTB 代码通过主编译后继续手工应用测试补丁并完成 36 个测试，而 Vanilla 提前失败。
- case 28 与 35 合计贡献 `651.47 s`，占排除 case 36 后 LLM 增量的 78.5%。这说明一次 9-task E2E 很容易被少数轨迹分叉主导。

run-05 本身不能直接测量“每个自然 reasoning boundary 后 fresh-final 的首字成本”：它的
`chunks.jsonl` 对同一请求内所有 chunk 使用同一个请求起始时间戳，不能拿来计算 boundary gap。
第 8.3 节因此使用相同完整 prompt、相同 fixed 参数的 run_server_02 交替 paired replay 做观测校准，
而没有把已分叉的 agent 任务总时间直接解释成 handoff 成本。纯 queue/prefill/decode/controller
分解仍需 telemetry-off 性能 run 与独立 phase run。

## 7. case 36 已知 hard failure

唯一满足 `completion_tokens=0` 且无 `stop_reason` 的已关联 DTB 请求是：

```text
request_id=2f3b7eed116941e3
proxy_session_id=e9ab8c8741df
turn=23
e2e_ms=1407.755
request last role=tool
tool payload contains: THINK_BLOCK = "</think>";
partial reasoning: Now I can see the actual values:\n\nOriginal (line 67): `THINK_BLOCK = "</think
```

该请求停在引用源码常量的普通文本中，没有 content、tool call、finish reason 或 usage；随后该 proxy session 结束，任务等待后无报告。它与已确认的 decoded-string collision 形状一致，属于本轮预先声明排除的 `stop="</think>"` hard failure。计划中的 `stop_token_ids=[248069,248058]` 与 streaming tool parser 原子修改针对该错误层次。

除该请求外，其余 441 个已关联 DTB 请求均有 `stop` 或 `tool_calls` terminal；这只能证明 proxy/API 层没有第二个同形状中断，不能替代 server stream audit 对 parser token 连续性的裁决。

## 8. Vanilla reasoning 分布与强制续写成本估算

### 8.1 9 场景总体分布

以下统计完整 9 场景的 301 个已关联 Vanilla 请求。token 使用 proxy 针对最终可见 reasoning
执行本地 tokenizer 得到的 `reasoning_tokens_local`；它不是 DTB hidden probe 或服务器 phase
generated token。

```text
N=301, reasoning token 合计=36,389
mean=120.9, P50=32, P75=92, P90=311, P95=439, P99=1356, max=2865
```

| reasoning token 区间 | 请求数 | 请求占比 | reasoning token | 该组 Vanilla LLM E2E |
| ---: | ---: | ---: | ---: | ---: |
| 0–32 | 156 | 51.83% | 3,026 | 897.866 s |
| 33–64 | 55 | 18.27% | 2,490 | 301.065 s |
| 65–128 | 29 | 9.63% | 2,706 | 185.836 s |
| 129–256 | 21 | 6.98% | 3,969 | 206.987 s |
| 257–512 | 27 | 8.97% | 9,269 | 285.782 s |
| 513–850 | 4 | 1.33% | 2,442 | 44.195 s |
| >850 | 9 | 2.99% | 12,487 | 221.845 s |

分布高度右偏：70.10% 的请求不超过 64 token，86.71% 不超过 256，97.01% 不超过当前
`fixed_check_length=850`。仅 9 个 `>850` 请求贡献 34.32% 的 reasoning token，但只占
Vanilla LLM E2E 的 10.35%；因此“治理长尾”有明确 token 集中度，但可优化的总 E2E 份额并不等于
token 份额。

不同延迟介入阈值的覆盖如下：

| 候选阈值 | 原生直通候选 | 需要 DTB 介入 | 长请求占 reasoning token | 长请求占 LLM E2E |
| ---: | ---: | ---: | ---: | ---: |
| 256 | 261/301（86.71%） | 40/301（13.29%） | 66.50% | 25.74% |
| 512 | 288/301（95.68%） | 13/301（4.32%） | 41.03% | 12.41% |
| 850 | 292/301（97.01%） | 9/301（2.99%） | 34.32% | 10.35% |

### 8.2 逐场景分布

百分位使用请求级线性插值。`≤850` 是按照 Vanilla 输出判断的“若行为不变，本应自然结束而不需要
DTB 介入”的请求数。

| Case | 请求 | Mean | P50 | P75 | P90 | P95 | Max | ≤850 | >850 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 42 | 71.8 | 29.5 | 58.0 | 148.9 | 239.0 | 640 | 42（100%） | 0 |
| 3 | 45 | 129.1 | 35.0 | 93.0 | 348.2 | 396.8 | 1,682 | 44（97.8%） | 1 |
| 26 | 24 | 145.6 | 32.0 | 143.2 | 365.9 | 693.7 | 927 | 23（95.8%） | 1 |
| 28 | 41 | 158.3 | 32.0 | 185.0 | 322.0 | 520.0 | 2,086 | 39（95.1%） | 2 |
| 29 | 37 | 76.7 | 21.0 | 62.0 | 176.8 | 326.2 | 857 | 36（97.3%） | 1 |
| 34 | 29 | 56.7 | 18.0 | 31.0 | 133.0 | 302.4 | 439 | 29（100%） | 0 |
| 35 | 26 | 210.8 | 29.0 | 118.8 | 333.0 | 787.8 | 2,865 | 24（92.3%） | 2 |
| 36 | 29 | 111.8 | 56.0 | 180.0 | 287.8 | 360.0 | 392 | 29（100%） | 0 |
| 37 | 28 | 156.5 | 45.0 | 115.2 | 362.4 | 770.2 | 1,356 | 26（92.9%） | 2 |

case 2、34、36 在这轮 Vanilla 中没有一个请求超过 850；对这些场景，当前 always-on DTB 理论上
只会增加停机、续写和 probe 风险，没有命中长尾压缩对象。case 35 的两个长请求构成全局最大值一侧，
是更适合验证长尾策略的定向样本。

### 8.3 当前 stop + fresh-final 的单请求成本标尺

run-05 的 chunk 时间戳不足以直接测 handoff，成本标尺改用
`agent_tool_call_run04_paired_replay/run_server_02`：

- 与 run-05 使用相同 fixed 参数：budget 20,000、check 850、stride 500、num-thinking 5、
  min-think 1、prob 0.93；Vanilla/DTB 同 prompt 交替请求，共 300 pair；
- server audit 显示 299/300 个 DTB 请求在 `initial_think` 中、850 token 前自然遇到 reasoning
  boundary，299 个随后均启动了独立 `final_answer` child；另 1 个是已知无 terminal 生命周期失败；
- 其中 255 个 pair 两侧均无协议或语义异常，DTB − Vanilla E2E 中位数为 `+552.505 ms`；
- 在 completion token 相差不超过 5% 的 38 个 clean pair 中，中位数为 `+709.3 ms`；
- 为降低输出长度差异的干扰，在 completion token 相差不超过 10% 的 68 个 clean pair 中，均值
  `+487.2 ms`、中位数 `+659.9 ms`、IQR `+141.7～+862.4 ms`，53/68 个 DTB 更慢；
- 在 completion token 绝对差不超过 25 的 45 个 clean pair 中，中位数 `+712.0 ms`、IQR
  `+556.8～+849.8 ms`，41/45 个 DTB 更慢。

校准集与 run-05 的 prompt 长度处于同一主要区间：Vanilla prompt token 的 P50/P95 分别为
60,131/127,966，对应 run-05 的 69,339/120,624；但校准集最短 prompt 为 58,867，而 run-05
最短为 6,520，因此固定单请求成本可能高估较短上下文的 restart 成本。

据此把 **`0.66 s/自然短请求`** 作为中心标尺，并用 `0.55～0.71 s` 做敏感性范围。它度量的是
“当前完整 DTB 短路径相对 Vanilla 的观测增量”，不能严格拆成纯 prefill/queue/fresh-first-token：

- run_server_02 的 DTB 开启了 `stream_audit`，Vanilla 没有相同 audit，估值可能包含观测开销；
- manifest 未固定 sampling/seed，pair 间仍有生成长度和模型选择差异；
- 0.55～0.71 秒是不同过滤口径的稳健中心范围，不是置信区间或理论上下界；
- Automatic Prefix Caching 可能复用相同 prompt block，不能把 fresh child 的全部 prompt token
  当成从零 prefill。

### 8.4 映射到 9 场景的 E2E 劣化

若 DTB 保持 Vanilla 的 301-request agent 轨迹不变，292 个 `≤850` 请求都会承担一次不必要的
stop + fresh-final：

```text
中心估计 = 292 × 0.6599 s = 192.7 s（约 3 分 13 秒）
敏感性范围 = 292 × [0.5525, 0.7093] s = 161.3～207.1 s
```

相对 Vanilla 全量基线：

| 口径 | 中心估计 | 敏感性范围 |
| --- | ---: | ---: |
| 代理侧 LLM E2E 2,143.578 s | +8.99% | +7.53%～+9.66% |
| 累计评测 wall 4,302.263 s | +4.48% | +3.75%～+4.81% |

按前文预声明排除 case 36 的主统计口径，Vanilla 剩余 272 个请求中有 263 个 `≤850`；对应中心
估计为 `173.6 s`、敏感性范围 `145.3～186.6 s`，约为该口径 Vanilla LLM E2E 的
`8.71%`（范围 7.30%～9.37%）和评测 wall 的 `4.39%`（范围 3.67%～4.71%）。这与全 9
场景估计的比例接近，说明 case 36 是否纳入不会改变“约 9% LLM E2E 税”的量级判断。

按 `0.6599 s/请求` 分摊的逐场景中心估计：

| Case | 不必要 handoff 数 | 增加 E2E | 占该场景 Vanilla LLM E2E |
| ---: | ---: | ---: | ---: |
| 2 | 42 | 27.72 s | 10.65% |
| 3 | 44 | 29.04 s | 9.77% |
| 26 | 23 | 15.18 s | 7.47% |
| 28 | 39 | 25.74 s | 7.56% |
| 29 | 36 | 23.76 s | 8.36% |
| 34 | 29 | 19.14 s | 13.94% |
| 35 | 24 | 15.84 s | 5.58% |
| 36 | 29 | 19.14 s | 12.60% |
| 37 | 26 | 17.16 s | 9.27% |

run-05 实际 DTB 轨迹有 442 个已关联请求，其中 432 个本地 reasoning `≤850`。若同样套用标尺，
实际轨迹上的不必要 handoff 约为 `238.7～306.4 s`，中心 `285.1 s`：约占本轮已观察到
`+961.507 s` LLM E2E 差额的 `24.8%～31.9%`（中心 29.7%），以及 `+1,388.458 s` 评测 wall
差额的 `17.2%～22.1%`（中心 20.5%）。

若仍按排除 case 36 的 8-task 主口径，DTB 有 396 个 `≤850` 请求，估计 handoff 为
`218.8～280.9 s`、中心 `261.3 s`；它约占该口径 `+830.096 s` LLM 差额的
`26.4%～33.8%`（中心 31.5%），以及 `+1,143.587 s` wall 差额的 `19.1%～24.6%`
（中心 22.9%）。

这也给出重要边界：即使 deferred intervention 完全消除中心估计的 285.1 秒，DTB LLM E2E
仍约为 `2,820.0 s`，比 Vanilla 高约 31.6%；评测 wall 仍约为 `5,405.6 s`，高约 25.7%。因此
短路径原生直通是必要优化，但不足以单独扭转 run-05；case 28/35 的请求轨迹放大和 hidden probe/
长路径工作量仍须分别解决。

### 8.5 单请求长 reasoning 压缩后净时延下降的样例

对 run-05 两侧请求删除且只删除 `enable_think_budget` 和六个 `fixed_*` 注入字段后，对完整
`request_body` 做精确哈希。共得到 7 个两侧均唯一的同 body pair；其中只有下面 1 个同时满足
“Vanilla reasoning 较长、DTB reasoning 变短、DTB E2E 变短”：

| 项目 | Vanilla | DTB | 变化 |
| --- | ---: | ---: | ---: |
| Case / role | 35 / auxiliary title | 35 / auxiliary title | 同一完整输入 |
| Request ID | `236bb99275fd4fde` | `e3c838f2b7aa4e60` | - |
| proxy 本地 reasoning token | 2,865 | 1,311 | -1,554（-54.2%） |
| completion token | 2,877 | 1,324 | -1,553（-54.0%） |
| E2E | 36,500.422 ms | 29,244.742 ms | -7,255.680 ms（-19.9%） |
| 最终输出 | `空指针判空与事务同步管理器修复` | `IncidentManage空指针修复与事务同步评测` | 均为有效标题 |

Vanilla reasoning 在多个候选标题之间反复自我修正，最终答案早已基本确定；DTB 删除了约一半
冗余 reasoning，并获得了单请求净时延收益。该样例直接支持“长尾冗余可以压缩”的原始假设，
但它是辅助标题生成，不是主 agent 工具动作，不能用于证明 LightCode 任务 E2E 已改善。

另一个更接近主 agent 工具路径的证据来自
`agent_tool_call_run04_paired_replay/run_server_02`。该实验对同一 request body 交替运行
Vanilla/DTB。`line=4, attempt=3` 是 fully-clean pair：

| 项目 | Vanilla | DTB | 变化 |
| --- | ---: | ---: | ---: |
| Request ID | `dtbtool-l4-a3-vanilla-run04-p0` | `dtbtool-l4-a3-dtb-run04-p0` | 同一 replay 输入 |
| reasoning 文本字符数 | 1,885 | 184 | -1,701（-90.2%） |
| completion token | 1,301 | 1,072 | -229（-17.6%） |
| first output | 545.297 ms | 640.044 ms | +94.747 ms |
| first content | 6,580.118 ms | 1,738.239 ms | -4,841.879 ms |
| first tool call | 6,651.830 ms | 1,825.384 ms | -4,826.446 ms |
| E2E | 15,177.240 ms | 11,742.237 ms | -3,435.003 ms（-22.6%） |

两侧均以 `finish_reason=tool_calls` 正确调用 `task`，tool index 连续，arguments/schema、协议和
语义异常列表均为空。DTB 的首个输出反而慢约 95 ms，但 reasoning 缩短使 tool call 提前约
4.83 s，覆盖了控制器首字成本并使最终 E2E 降低。相同筛选条件
（Vanilla reasoning 文本至少 1,000 字符、DTB 至少压缩 30%、两侧 fully clean、DTB 更快）
还命中 `line=1, attempt=25`（E2E -13.1%）和 `line=4, attempt=29`（E2E -9.9%）。

这些是“存在性证据”，不是稳定收益证明：run_server_02 的 255 个 fully-clean pair 中只有 99 个
DTB 更快，且该 replay 的 `quality_eligible=false`，只验证工具协议与预期动作，不能代替最终代码
质量。因此下一步阈值实验应优先复用上述正样例，同时加入同 prompt 的多次交替重复，检验收益
概率和均值，而不是只保存最快的一次。

## 9. Deferred intervention 设计

### 9.1 目标行为

新增一个仅用于流式路径的、按 reasoning token 数激活的状态：

```text
初始同一 engine 请求，无 controller reasoning-boundary stop
    |
    +-- canonical reasoning boundary 先到
    |      -> 解除阈值监控
    |      -> reasoning/content/tool/terminal 全部原样透传
    |      -> 0 probe、0 synthetic boundary、0 fresh-final child
    |
    +-- reasoning token 阈值先到
           -> 只透传阈值内前缀
           -> 内部关闭/abort 当前 child，不向客户端发 terminal
           -> 以该前缀进入现有 confidence probe / iterative think / final
```

短路径应称为 **native-equivalent same-child passthrough**：它仍经过 DTB wrapper 和一次预算决策，但模型生成、parser-facing token 顺序和 terminal 来自同一原生 engine child，不发生 final 重启。这正是避免短 reasoning 首字开销所需要的形态。

### 9.2 为什么现有参数做不到

- 把 `fixed_min_think_tokens` 或 vLLM `min_tokens` 设成阈值会 mask reasoning boundary，强迫本来想结束的模型继续思考，改变质量与延迟分布。
- 把首个 child 的 `max_tokens` 设成阈值，若 reasoning 很短但 final/tool 输出较长，会在 final 中途误截断；`max_tokens` 不是 reasoning-only 上限。
- 先完整原生生成，发现过长后再重跑 DTB，会重复整段模型工作，通常不可能得到净时延收益。
- 仅新增 YAML scene 仍会进入当前状态机，并在短 reasoning boundary 后启动 fresh-final；当前配置没有表达未来 token 长度条件的 native bypass。

### 9.3 实现前必须锁定的设计点

1. 阈值只累计 canonical boundary 之前的 parser-facing reasoning token，不包含 boundary、content、tool 或隐藏 probe。
2. 初始观察 child 不追加 controller 的 `248069/248058` stop；原请求的用户 stop、EOS 和 completion limit 保留。
3. boundary 在阈值前或阈值位置出现时，boundary 优先并永久解除阈值监控；同一 child 继续到原生 terminal。
4. MTP/speculative delta 一次跨越阈值时必须按 token ID 切分：boundary 位于阈值内则走短路径；boundary 位于阈值外则只透传阈值前缀，并记录被丢弃的 source overshoot token。
5. 长路径应显式 `aclose()` 初始 async generator；当前 vLLM 0.23 `AsyncLLM.generate()` 在 `GeneratorExit` 中会 `abort(..., internal=True)`。必须验证请求释放、取消不外泄、没有双 abort 和残余 terminal。
6. 长路径 continuation prompt 与已透传前缀完全一致；外部 completion 剩余额度扣除已透传 token，不能重新获得完整额度。
7. 短路径把原生 final terminal 只透传一次；不生成 answer cue、不合成 boundary，不重置 streaming reasoning/tool parser。
8. 账本新增 `activation_threshold`、`threshold_armed/disarmed/triggered`、触发 token 位置、source/emitted overshoot、initial child calls、fresh-final avoided 和 abort wall。
9. 非流式保持现状；token-stop 与 tool parser 正确性 changeset 先独立验收，deferred intervention 不与它混成一个不可归因的大 patch。

## 10. 下一轮实验计划

### P0：先完成协议正确性门

1. 按 `docs/design/dtb_reasoning_boundary_token_stop.md` 完成 token-stop + streaming tool parser 的原子部署。
2. 先跑文档中的 2-case × Vanilla/DTB × 30 次 paired replay；hard gate 为 60/60 DTB terminal contract 有效、无 XML/arguments/finish reason 回归、两个结构 boundary 均有覆盖。
3. 定向重放 run-05 case 36 的 request `2f3b7eed116941e3` 完整 prompt，要求普通拆分 token 引用 `</think>` 不再停止，并能继续产生合法 tool call/terminal。

P0 未通过前，不启动新的 LightCode 参数搜索。

### P1：同参数可用性复验

使用同一新 parser、相同模型/服务进程和单并发，重新跑 9 场景 Vanilla 与当前 fixed DTB：

- Vanilla 也必须使用同一新版 parser，避免把 parser 差异混入 DTB 对比；
- 创建实验级 manifest，记录模型与五类运行时版本、TB/vLLM/vLLM-Ascend commit、patch/YAML hash、sampling/seed、MTP、TP/DP、cache、执行顺序和固定参数；
- 正式 E2E 使用 observability off；另取固定 prompt 做 phase audit，不把 telemetry-on E2E 混入排名；
- 可用性门：9/9 任务有报告、无 parser/tool hard failure；质量门：严格结果不低于同轮 Vanilla；
- 为减小少数轨迹分叉，至少做两轮相反顺序的 task-level A/B；机制时延使用相同 prompt 的交替 paired replay。

若当前 always-on DTB 在 token 修复后仍出现类似 run-05 的请求/工作量增长，停止搜索它的六维 fixed 参数。

### P2：Deferred-intervention recorded-prompt 实验

从 run-05 Vanilla 完整日志构造两类固定 prompt：

- 短组：覆盖 reasoning→tool、reasoning→content、字面量 marker，分别落在 64/128/256 token 内；
- 长组：全部 `>512` 的 13 条和 `>850` 的 9 条真实请求。

对相同请求体、sampling/seed、cache 和单并发，交替运行：

```text
A  Vanilla
B  token-stop 修复后的 always-on DTB
C  deferred intervention, threshold=512
D  deferred intervention, threshold=850
```

短组验收：C/D 的 `child_generate_calls=1`、probe=0、fresh-final=0，parser-facing token/terminal 与 Vanilla 等价；E2E/TTFT 不劣于 B，并接近 Vanilla。

长组验收：阈值只触发一次，abort 无外泄，工具协议无回归；比较完整模型 token、phase wall 和 E2E。只有 `长尾节省 - 新控制成本 > 0` 且质量等价的阈值进入 LightCode。

建议先比较 512 与 850；256 只作为提高介入覆盖率的诊断点，不直接作为首个 E2E 候选。

### P3：LightCode 与配置收口

1. 用 P2 胜出的单一阈值跑 9-task task-level paired E2E，并与 Vanilla、always-on DTB 三方比较。
2. 预声明主指标：9/9 报告、严格正确数非劣、累计 `start→end` wall、累计代理 LLM E2E、请求数；辅指标为 P50/P95、completion token 和长尾触发率。
3. 只有在上述门通过后，才把 `activation_threshold` 纳入运行时 schema/YAML 并为 agent/LightCode scene 配置；当前 YAML 不应先写一个未实现的 `native` 或 threshold 字段。
4. 阈值策略稳定后，只在被触发的长请求子集上搜索 `check_length/stride/num_thinking/prob_th`，而不是再次让短请求参与六维网格。

## 11. 证据强度与阶段判断

- **实测：**关联覆盖、任务报告、逐任务 wall、代理 LLM E2E、请求数、响应 token、case 36 空 terminal、Vanilla reasoning 分布。
- **源码确定：**现有状态机在自然 boundary 后进入 fresh-final；vLLM async generator `GeneratorExit` 会内部 abort；现有 fixed/YAML 参数不能表达“未来长度超过阈值才介入”。
- **待验证假设：**fresh-final 是短请求重要成本；deferred intervention 能在不改变输出的前提下取得净收益。P2 的 TTFT/phase paired replay用于证伪。

阶段判断：**run-05 可用于发现系统可用性和 agent 轨迹放大问题，不能作为 DTB 时延优化成功证据，也不能进入自动 scene 配置。**

## 12. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-03 | 补充 9 场景 Vanilla 请求级/逐场景 reasoning 分布；用 run_server_02 natural-short paired replay 校准当前 stop + fresh-final 成本，并映射到全量及排除 case 36 的 E2E 劣化范围。 |
| 2026-09-02 | 首版：完成 run-05 LightCode↔proxy 关联、排除 stop 碰撞后的质量/时延复算、输出归因和 deferred-intervention 下一轮设计。 |
