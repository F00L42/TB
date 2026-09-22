# Noemate 4c259c：长请求候选、短路径开销与预算约束

分析日期：2026-09-21。结果来源：`4c259cefd661250b7f7f47b8ef12295b6649ccb2`。

## 1. 结论与目标调整

按用户澄清，本场景的主要目标调整为：**找出值得优化的长请求；验证短请求无显著额外开销；让单次思考耗时与资源消耗可控。** 整个分层校准集的平均 E2E 仅作背景，不再作为选参或发布的主指标。

本轮尚不能证明稳定压缩或短请求不退化，原因比均值重要：

1. **实测：direct750 没有呈现明确的 750 预算约束。** 128 次 DTB 输出中 67 次可见 reasoning 超过 750，33 次超过 1000，最大 1846。本地计数不是服务器生成 token ID，流式 drain 也允许超出阈值，但如此明显的超出必须解释。当前没有账本，不能确认哪些请求真正接管、跳过 probe 或走了原生回退。
2. **实测：最值得保留的长样本是 pool line 319。** 同案例 8 次 Vanilla 均为 1829～2016 reasoning token。direct750 相对其同轮 Vanilla 平均快 8.67%（约 3.61 秒），3/4 对更快；但收益高度集中在其中一对，去掉该对仅快 0.74%。它是优先验证的候选，尚非稳定成功样例。
3. **实测：短请求的固定开销似乎较小，但没有非劣证明。** direct750 中 8 对思考、正文、工具名/参数完全相同的输出，平均 E2E 差为 −7.85 ms（负数表示 DTB 更快）；样本只有 5 个案例，且事后筛选，不能代表全部短请求。另有短请求改变生成路径而明显变慢，必须分开分析。
4. **下一步先确认接管确实发生，再开展长、短两条独立实验。** 暂停整池重复和大范围调 H，不用现有均值证明兜底有效。当前最需要的是一次很小的真实 phase 诊断。

## 2. 身份、完整性与证据边界

用户本轮确认：环境沿用前轮，APC 开启，实际 observability=off，服务已加载 fd60e41 的直接 final 门禁修复；**日志压根未开启**。因此目录名含 `phase` 不代表实际 telemetry=phase，不能把其中结果叫 phase 账本，也不能继续假定“日志只是漏上传”。

| 项目 | 审计结果 |
| --- | --- |
| off 正式客户端数据 | 32 案例 × 两策略 ×（1 warmup + 4 对 V/D）= **576 请求**；排除 64 warmup 后为 **512 请求、256 对**，每策略 128 对 |
| 子目录覆盖 | 128 个预期子目录全部存在，案例/attempt/variant/顺序闭合，请求 ID 唯一 |
| 原始 SSE | 逐条读取 **223,419** 个事件，全部 summary 与 chunks 重组完全一致，每请求一个 DONE |
| 请求身份 | 所有实际 body hash 与输入重建一致，源 pool SHA256 与 manifest 相符；保留原始 messages/tools/max_tokens/采样 |
| 参数 | seed=20260918、单并发、SSE、每案例每策略预热，V/D 四次交替；H=750，budget 分别 2048/750，其余 stride=256、num=4、min=1、prob=0.95 |
| 客户端代码 | `fd60e41e5b0b311e9cae6ef7d5ca5ee8c2a62aad`，随附 client_dtb_worktree.patch 为空；该 commit 的源码包含门禁修复 |
| 服务身份 | 环境和加载修复由用户确认；本包未附完整实际启动参数、已加载配置/patch/tokenizer 哈希及各上游 SHA，不能从客户端 commit 推导所有运行状态 |
| 名为 phase 的目录 | probe750 为计划内 8 条 × 2 次 =16；direct750 实际为 **line 52 × 2 次 =2**，与计划内 8 条集合不同；18 条 SSE 均审计通过，但不能做策略配对 |
| 内部状态 | 无 DTB_PHASE / DTB_REQUEST_SUMMARY，实际分支、probe 次数、hidden token、abort/drain、queue/prefill 和总生成量未知 |
| 正确性 | 未运行参考答案 Judge；协议/schema、空 final、length 及少量语义检查独立报告，不等同于业务通过率 |

预热后的首输出已接近：direct750 对照的 V/D 均值 676.47/673.89 ms，probe750 为 677.46/676.41 ms。上轮 V1 冷缓存首输出差约数秒的现象没有在这组均值中重现；这仍不是实际 cache hit 账本。

输入和本地 tokenizer 口径沿用 [前轮审计](../noemate_replay_20260920T012159Z/RESULT.md)。新旧 run 不交叉作为性能基线。

## 3. 背景时延：没有稳定的整体改善

加速率为 `1 − ΣDTB_E2E / Σ本轮Vanilla_E2E`，正值表示更快。下表只描述这 32 条事后分层校准集，不外推全流量。

| 请求侧策略标签 | 配对数 | Vanilla 均值 s | DTB 均值 s | 加速率 | DTB 更快 |
| --- | ---: | ---: | ---: | ---: | ---: |
| probe750 | 128 | 17.182 | 18.002 | −4.77% | 56/128 |
| direct750 | 128 | 17.569 | 17.742 | −0.99% | 62/128 |

按 case 聚类重采样的 95% 收益区间：direct750 为 [−7.59%, +4.94%]，probe750 为 [−9.84%, −0.16%]。区间只描述本样本的不确定性，不消除选样、重复轨迹相关性或服务状态因素。

direct750 看起来比 probe750 更接近 Vanilla，但不能将两者均值差直接解释为节省 probe：没有隐藏账本，两组 Vanilla 输出也不同。direct750 可见 reasoning 均值 768.70→767.41，几乎未变；probe750 为 728.32→796.19。当前更应关注接管是否生效。

## 4. 哪些长请求值得继续

采用一个可复核的探索规则：同案例在两策略下共 **8 次 Vanilla 都超过 750 reasoning token**，得到四例。此规则是本轮事后选样，下轮冻结为独立验证集合；不是运行时预测规则。

| pool line | 请求内容 | 本轮 8 次 Vanilla reasoning 范围 | direct750 V→D 均值 s | 加速率 | 价值与限制 |
| --- | --- | --- | --- | ---: | --- |
| **319** | CSCF 典型链路告警，max_tokens=16384 | **1829～2016** | **41.660→38.048** | **+8.67%** | 最稳定的长思考、最明显潜在空间；收益集中于一次，未证明预算接管 |
| **353** | CSCF 典型链路告警，另一个历史请求，max_tokens=2048 | 813～1784 | 26.881→26.729 | +0.57% | 主要用于验证能否给最终答案留出额度：本组 V 3/4、D 4/4 为 length；不能只看时延 |
| **178** | 12 条告警按级别分类 | 846～1963 | 24.408→24.549 | −0.58% | 思考均值下降 261，但正文增加约 230 token；更适合验证压缩思考与输出完整性，而非保证更快 |
| **259** | 告警含义与触发条件 | 817～1104 | 20.160→20.341 | −0.90% | 稳定的中等长度对照，检查接管开销会不会吃掉有限后缀空间 |

319 的四次 D reasoning 为 **1846、898、1817、1838**。第二对 E2E 38.647→25.146 秒、快 13.50 秒，贡献了四对净节省的约 93.5%；另外三对合计仅快 0.74%。因此保留该样本，但不能把一次显著缩短当成可重复压缩成功。

319 和 353 的业务问题相同，但 request.messages 和 max_tokens 均不同，不能当成“只改外部额度”的严格对照。

另有 line 340（+51.34%）、386（+34.83%）、26（+21.62%）看似更快，但输出路径与长短身份不稳定：340 发生 Bash/Rag 选择及参数形式变化；26 在工具调用和正文之间切换；386 在拒答和继续检索之间变化。保留为行为诊断样例，不把它们排在稳定长样本优化收益榜首。

line 399 继续保留作算术语义边界例：本轮 D 四次均有数值正文，均可见正确总和，均值为精确值或三位小数舍入；V 第三次只有思考、没有 final。旧日期/“今日”的表述问题仍在。这个观察不构成整体正确性结论，也不证明 D 在 750 真正退出，因为其 reasoning 为 769/1069/1046/862。

冻结候选：[next_cohorts.json](next_cohorts.json)、[next_long_lines.txt](next_long_lines.txt)。

## 5. 短请求有没有变慢

### 5.1 原始 E2E：不能宣称短请求已不退化

两策略下共 8 次 Vanilla 都 ≤750 的案例只有 **21、321、365、394**；这四例的所有 D 也 ≤750。仍不能仅凭可见长度确认未接管，需实际状态账本。

| pool line | direct750 的 V→D 均值 s | 加速率 | 首输出 D−V ms |
| --- | --- | ---: | ---: |
| 21 | 3.827→4.995 | −30.54% | +0.06 |
| 321 | 7.979→8.653 | −8.45% | +10.42 |
| 365 | 1.547→4.077 | −163.50% | −2.85 |
| 394 | 14.062→12.566 | +10.63% | −5.17 |

合计 16 对平均慢 10.49%，9/16 对更快，样本级差异大。不能把“多数配对更快”替代非劣验收。

其中 line 365 第 2 对是解释关键：V reasoning=48、completion=61、1.548 秒，简短说明知识不足；D reasoning=628、completion=690、11.677 秒，再次调用 Rag。其余三对正文/思考完全相同，E2E 差为 −4.44、+0.25、−6.19 ms。**10 秒的差异伴随输出路径改变，不是现有证据能归因给控制器 CPU 的固定开销。** 随机生成、运行时调用上下文差异等均待排除，不能预先断言是纯采样噪声或 DTB 代码导致。

line 21 也类似：相同轨迹的第 2/3 对差值为 +0.06/−12.85 ms；其他执行在英文短解释、中文长复述或“未找到目标网元数据”的答案之间变化。

### 5.2 固定开销的初步线索

把思考、正文、工具名和原始参数字符串完全相同的配对单列，忽略服务生成的随机 tool_call ID：

| 策略 | 对数 / 案例数 | 平均 E2E D−V | 平均首输出 D−V | 解释 |
| --- | --- | ---: | ---: | --- |
| direct750 | 8 / 5 | **−7.85 ms** | −2.58 ms | 未显示明显固定税；逐对仍有 +83.30 ms 波动 |
| probe750 | 5 / 2 | −3.28 ms | −3.50 ms | 同输出时耗时接近，样本更少 |

这些轨迹的思考均 ≤297，符合短路径候选。**这是事后选中的敏感性分析，不能用来证明所有短请求不会变慢。** 同文本不等于已确认相同内部 token 轨迹或一次 engine call；输出不同的请求也不能从主验收集删除。

后续应分别验收：一是观察/转发路径的额外开销；二是在原始采样下，短请求的行为和长度分布是否额外恶化。前者适合可重复输出与 A/A 噪声对照，后者必须保留所有生成结果。

候选：[next_short_lines.txt](next_short_lines.txt)。新场景路由由其他任务实现，本轮 fixed 参数短路径结果不覆盖未来 system 匹配器的额外 CPU 开销。

## 6. 当前预算可控性尚未闭合

| 本地可见 reasoning | direct750 D 次数 /128 |
| --- | ---: |
| >750 | 67 |
| >1000 | 33 |
| >1262 | 10 |
| >1500 | 4 |
| 最大值 | 1846 token |

**实测**：请求 body 确实发了 budget=750/check=750。**源码确定**：已部署版本包含预算耗尽直接 final 的门禁修复。**尚未实测**：服务是否对这些请求绑定了可切换协议、触发了接管，以及触发/排空各用了多少 token。

源码仍有正常的原生回退边界：[switching_rejection](../../../../dtb/reasoning/deferred_observation.py#L28) 检查协议、输出模式、采样状态等；[协议绑定异常](../../../../dtb/request_controller.py#L330) 会退回 unbound_protocol；[门禁与 drain](../../../../dtb/reasoning/deferred_observation.py#L241) 受容量和自然结束竞态约束。大幅超出也可能与 trigger 批量或 drain 尾部有关。以上都是待区分的候选解释，不据此指定根因；用户已确认部署修复，本报告不擅自改判成“没部署”。

当前只能说“客户端要求了 750”，不能说“已有 750 的硬兜底”。必须将以下边界分开：

- **思考 token 控制**：介入点 H、触发超出、drain 超出、是否转入 final。
- **总生成量控制**：可见 thinking + hidden probe + final；不是 API completion 单列。
- **耗时控制**：还受队列、prefill、decode、final 长度和负载影响，token 预算不能直接承诺秒级 SLA。

若要提供严格上界，需要明确不支持切换时的策略和可接受 overshoot；现有 fail-open 原生完成是协议安全选择，但不等价于严格硬截断保证。资源账本闭合前也不能宣称“零 probe 已省算力”。

## 7. 完整性异常不能被均值掩盖

| 组别 | API/传输错误 | 脚本 protocol flags | 工具 schema 错误 | length | 空 final 且无工具调用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct750 Vanilla | 0 | 0 | 2 | 8 | 3 |
| direct750 DTB | 0 | 0 | 2 | 7 | 1 |
| probe750 Vanilla | 0 | 0 | 1 | 5 | 1 |
| probe750 DTB | 0 | 0 | 3 | 7 | 3 |

空 final 与 length 可重叠；不是相加的独立错误数。direct750 D 的空 final 为 line371/attempt3/stop；其 Vanilla 为 line378/attempt1、2 和 line399/attempt3，均 stop。probe750 D 为 line178/attempt2/length、line353/attempt1/stop、line399/attempt1/stop；Vanilla 为 line371/attempt1/stop。

当前 replay 脚本未把这些空 final 标为 anomaly，因此“protocol 0”不能证明完成了可用回答。它们须作为独立完成性失败保留。工具名偏离/少一次 Rag 属历史动作偏离，和 schema 错误、空 final、业务答案错误分别统计；本次不把其全部判成参考答案错误。

## 8. 下一步：一次路径确认，两条效果验证

### 8.1 首先补一次真实 phase，仅 12 请求

优先级最高。将**实际加载**的配置改成 `OBSERVABILITY.mode=phase` 并重启服务，保存启动记录与 server.log；这与当前全部 off 的结果分开。选 short21/365、long178/319/353、算术399，每例两次直接预算。不要只更改输出目录名。下列命令在服务器仓库根目录执行：

```bash
set -euo pipefail
BASE=docs/log/perf/noemate_analysis_60f2d7
AUDIT=docs/log/perf/noemate_direct750_off_20260921T064359Z
python "$BASE/prepare_replay_dataset.py"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="docs/log/perf/noemate_activation_audit_${STAMP}"
mkdir "$OUT"
cp "$AUDIT/next_cohorts.json" "$OUT/selection.json"
git rev-parse HEAD > "$OUT/client_commit.txt"
git diff -- dtb tools/dtb/replay_tool_calls.py > "$OUT/client_worktree.patch"
python tools/dtb/replay_tool_calls.py \
  --file "$BASE/replay_dataset/pool.jsonl" \
  --lines "$(cat "$AUDIT/activation_diagnostic_lines.txt")" \
  --url http://127.0.0.1:9001/v1/chat/completions \
  --mode dtb --stream --repetitions 2 --seed 20260918 --timeout 1800 \
  --fixed-budget-tokens 750 --fixed-check-length 750 \
  --fixed-think-stride 256 --fixed-num-thinking 4 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.95 \
  --request-id-run "activate_${STAMP}" --output-dir "$OUT/direct750"
```

必须按实际 request ID 回传服务事件，不能仅回传 replay chunks。对长请求检查有效预算、`activation_skipped_reason`、`post_drain_branch`、`trigger_prefix_tokens`、`drained_tail_tokens`、abort 耗时与 final 原因。短请求确认原生同一 child 完成、abort=0、没有 probe/final 重启。直接预算路径的 hidden probe 必须为零。

验收/停止条件：至少有一条实际 `budget_final` 且完整 final；若全部 native_complete 或存在 rejection，先解释条件，不继续用它衡量压缩收益。若 trigger≈750 但 drain 大量超出，优先定位接管时机、输出积压及 abort/drain 行为；若 token 计数口径不一致，先用服务内部 ID/计数对齐。此 run 不排名时延。工具非零退出保留已完成产物，检查后续跑，不覆盖目录或忽略错误。

### 8.2 长请求：验证稳定收益与可控性，不扫完整池

路径闭合后切回 off，冻结 `178,259,319,353`，优先 319，另加 399 作语义边界。每 case 逐次预热、同轮 Vanilla/候选交替 **至少 10 对**，保持当前采样和 seed；使用独立运行确认，不能沿用本轮选样数据作验收。

首先仍用 H=budget=750。每例分别报告思考长度与总可见 token 的 P50/P95/max、超出 H 的分布、最终答案额度/完整率、请求 E2E 差与重复收益一致性；内部模型总生成量通过独立 phase 确认，不混入 off 时延排名。319 验证可读清单覆盖；353/178 重点验证是否减少 final 截断，允许出现“思考更可控、答案更完整、总 E2E 基本不变”的有效结果。无需为追求均值强行缩短正常答案。

如果 750 的完整性/语义代价偏大，再在同一小集合比较 direct1024；只有 750 路径确实生效且安全，才讨论更激进的 512。整体全流量约 45% 的历史 Vanilla 重放超过 750，750 并非天然的稀有异常阈值；作为兜底的最终 H 应根据允许介入比例和单请求预算选定，而非按全池均值最大加速率选择。

### 8.3 短请求：开销与行为分开验证

冻结 `21,321,365,394`，扩充真实短请求后再验收覆盖率。建议分两层：

1. **观察路径开销**：先做原生 A/A 重复确定噪声，再使用 greedy/temperature=0、相同 seed 的 V/DTB 配对，每例至少 20 对，APC/并发/节奏一致。greedy 不保证设备上逐 token 确定性，需实际检查输出；同轨迹比较只是辅助分析，所有配对仍保留。可以额外用高于这四例原始 max_tokens=2048 的 H=4096 作为“永不接管”的观察路径对照，再比较 H750；这只验证当前 fixed 观察路径，不替代新路由模块成本测试。
2. **原始采样下的行为**：恢复 temperature=0.2 等原始参数，在同一短集比较长度、重复工具调用、空 final 与 E2E 分布。重点复现 line365/attempt2 的 Rag 分支和 line21 的摘要变化；用 A/A 中相同波动衡量背景，不将所有变化归因给 DTB，也不删除变长样本。

“不会变慢”应落成事先声明的非劣容差与置信界，例如同轨迹 E2E 平均额外耗时的单侧 95% 上界低于 `max(20 ms, 1% Vanilla E2E)`、首输出上界低于 +10 ms，同时短路径无接管/额外 child。该数值是**待冻结的工程验收建议**，不是本轮已经通过的门；A/A 用来评估所需重复数，不能看完 A/B 再放宽门限。原始采样层还需检查整体短请求的高分位和行为退化，不能仅凭同轨迹子集过门。

### 8.4 兜底定位的最终验收

主指标改为“短请求无显著税 + 超预算长请求被可靠接管 + 返回可用答案 + 内部生成量有账可查”。token 上限和秒级 deadline 是不同能力；本轮先证明前者，若产品需要后者，再设计思考阶段 wall-time 限制与负载下的尾延迟验收。真实长样本不足时可以加受控压力例检验上界与取消/错误，但标为工程压力测试，不能替代原业务集的质量证明。

不优先做整池 H 网格或端到端 Agent 大跑。待上述三项闭合后，再用并发5检查资源争用和短请求被长请求拖累的情况；此时关注短请求 P95、长请求最大占用与取消清理，整体任务均值仍只作背景。

## 9. 复算与产物

运行 `python docs/log/perf/noemate_direct750_off_20260921T064359Z/analyze_4c259c.py`。脚本只读原始结果，不请求模型服务；逐目录重组 SSE、核对 body hash 与自动 paired_summary，然后生成：

- [audit_4c259c.json](audit_4c259c.json)：完整性、分层背景统计、异常及重采样结果。
- [case_metrics.json](case_metrics.json)：全部32案例的两策略均值、四次长度及逐对耗时差。
- [paired_metrics.json](paired_metrics.json)：256 对请求与 ID、可见 token、异常、同输出标记。
- [request_metrics.json](request_metrics.json)、[folder_audit.json](folder_audit.json)：请求级完成性与目录核对。
- [next_cohorts.json](next_cohorts.json)：冻结的长、短、行为诊断与激活诊断集合。

同输出标记忽略随机 tool_call ID，仍精确比较 reasoning/content 和工具名/原始参数。`first_content_ms/first_tool_call_ms=0` 表示未记录该类输出，不当成零毫秒开始；本报告不将首正文时间当作内部 think phase wall time。无服务器性能测试在本地执行。
