# fixed_v4：量化增训权重候选参数构造

## 0. 结论与状态

本文基于 [`run_08_quant&post_train`](../log/perf/run_08_quant&post_train/RESULT.md) 中同一
W8A8 量化、特定场景思维链压缩语料增训权重的 Vanilla 和自动 DTB 结果，为 13 个产品
测试集构造 `fixed_v4_quant_post_train` 候选。

参数已追加到 [`fixed_param/`](fixed_param/) 下原有 13 个测试集同名 TXT 中；每个文件
使用以下分隔符，人工只复制其后的七行请求参数：

```text
===== fixed_v4_quant_post_train =====
```

状态：**待服务器 fixed 实测**。v4 是新权重的第一轮质量恢复候选，不是最终 YAML
配置。只有通过同权重 Vanilla 质量门且 E2E 为正收益的场景，才能写入量化增训权重的
独立 YAML。

## 1. 为什么不能直接复用旧公式或旧配置

run_08 的 633/633 个自动 DTB 请求均正确命中预期 scene，且 13 组预算签名与开源权重
run_07 完全一致。因此本轮失败不是分类问题，而是预算跨权重失配：

| 指标 | 量化增训 Vanilla | 复用旧预算的 DTB | 变化 |
| --- | ---: | ---: | ---: |
| 请求加权平均 E2E | 77.1998 s | 65.7576 s | 改善 14.82% |
| completion token | 834,888 | 603,704 | 减少 27.69% |
| 正确数 | 586/633 | 547/633 | **少 39 题** |
| 质量层 | — | 2 严格 / 2 少1题 / 9 失败 | 未通过 |

设计计划 6.2 的 `0.20/0.10/0.50 × T50` 是没有 fixed 证据时的首探公式。run_08 已经
提供一个更强的反证：旧配置在 9 个场景过早 probe 并显著掉点。若再次从新权重 T50
构造 `0.50 × T50`，得到的预算会比已失败配置更激进，不能作为质量优先的 v4。

v4 因此采用“通过场景原样保留、失败场景推迟第一次 probe”的局部搜索策略。

## 2. Vanilla 思考长度估计

### 2.1 方法

13 个 Vanilla 明细均能在 `</think>` 处分离思考和答案。本地没有目标 tokenizer，使用
同场景 `qp_dtb` 的 `requests.jsonl` 中 `visible_think_tokens` 和
`final_answer_tokens` 校准答案 token，再从 Vanilla completion token 中扣除估计答案
token。算法与 [`construct_fixed_candidate.py`](../../tools/construct_fixed_candidate.py)
的 run_04 构造模式相同。

本次给脚本补充了 `qp_vanilla_/qp_dtb_` variant 前缀识别；旧
`vanilla_/dtb_` 行为不变。服务器若能访问精确 tokenizer，应使用同一脚本的
`--tokenizer` 模式复算；精确分位数若跨过 50-token 取整边界，以服务器结果为准。

### 2.2 分布和校准误差

| 测试集 | N | T50 | P75 | P95 | max | 校准 MAE / P95 error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ReAct 白盒单步规划 | 79 | 753.0 | 900.8 | 1,213.8 | 1,517.0 | 4.3 / 13.3 |
| ReAct 白盒整体规划 | 16 | 1,387.3 | 1,556.1 | 1,908.5 | 2,052.2 | 17.6 / 44.6 |
| ReAct 白盒机器数据分析 | 29 | 949.7 | 1,138.6 | 1,245.6 | 1,491.7 | 5.8 / 7.8 |
| KPI 配置保障 | 9 | 1,357.5 | 1,403.8 | 1,484.9 | 1,512.3 | 181.6 / 899.4 |
| NetQAD E2E 单步规划 | 80 | 933.8 | 978.9 | 1,090.3 | 1,341.2 | 4.9 / 18.1 |
| NetQAD E2E 规划模块 | 101 | 1,786.2 | 2,099.2 | 2,561.2 | 3,310.5 | 9.0 / 27.9 |
| 事件自闭环 | 25 | 1,379.4 | 1,672.8 | 2,789.0 | 3,425.9 | 5.7 / 15.9 |
| 告警诊断建议总结 | 11 | 1,525.6 | 1,765.2 | 2,085.5 | 2,338.6 | 59.7 / 162.1 |
| 告警诊断结论总结 | 13 | 1,810.7 | 1,892.8 | 1,990.0 | 2,042.9 | 33.9 / 115.6 |
| 长文本根因推理 | 47 | 1,195.2 | 1,253.0 | 1,320.7 | 1,448.1 | 4.9 / 13.6 |
| 投诉助理工具调用 | 39 | 1,158.5 | 1,248.6 | 1,556.7 | 1,796.8 | 3.6 / 11.1 |
| 投诉分类中文 | 164 | 878.6 | 992.9 | 1,205.4 | 1,998.6 | 1.2 / 4.0 |
| 投诉分类英文 | 20 | 845.8 | 969.5 | 1,117.8 | 1,241.7 | 0.7 / 1.4 |

KPI 只有 9 条且答案形态差异使校准误差异常大，因此不使用其估计分位数调参；该场景
直接保留 run_08 已实测严格非劣的配置。其余分位数只用于候选播种，不当作精确 token
实测。

## 3. v4 构造规则

统一取整：除长文本诊断候选外，分位数和 `0.10 × T50` 均按最接近 50 token、恰好
中点 half-up；`fixed_num_thinking` 为：

```text
max(1, ceil((fixed_budget_tokens - fixed_check_length)
            / fixed_think_stride))
```

### 3.1 已通过质量门：原样保留

KPI 配置保障、事件自闭环、投诉分类中文和投诉分类英文在 run_08 分别为严格、少 1 题、
严格、少 1 题，且 E2E 改善 31.62%～41.36%。v4 原样复制其已生效配置，避免在没有
必要时同时改变多个变量。

### 3.2 小幅质量回退：T50 首检、P95 上限

ReAct 白盒单步规划、机器数据分析和 NetQAD 单步规划分别少 3/79、2/29 和 2/80 题。
首次 probe 从旧配置的 300～500 token 推迟到新权重 Vanilla T50，预算上限取 P95；
`fixed_prob_th` 提高到 0.95，只在置信度不足时继续到 P95。

### 3.3 严重质量回退：P75 首检、P95 上限

ReAct 白盒整体规划、NetQAD 规划模块、告警建议、告警结论和投诉工具调用分别少
4、10、5、3、4 题。首次 probe 推迟到 P75，预算上限取 P95，优先验证质量能否恢复。
这些候选比 3.2 更保守，若质量恢复但时延无正收益，再在 P50～P75 之间二分，而不是
提前压缩。

### 3.4 长文本根因推理：控制器适用性诊断

该场景旧配置同时多耗时 14.69%、多生成 5.64% completion token、少 7 题。v4 将首次
思考设为估计 Vanilla 最大值 1,448.1 向上取整后的 1,450，并把 hard budget 同样设为
1,450，不允许 iterative think。它用于回答“完整保留原生思考长度后，fresh final
answer/probe 状态机是否仍会损害质量”，不是一个承诺有时延收益的候选。

若该诊断候选仍不能恢复质量或仍比 Vanilla 慢，应停止该 scene 的 fixed 搜索；不能用
其他场景收益抵消它。

## 4. 完整 v4 参数

| 测试集 | 选择依据 | prob | check/min | stride | num | hard budget |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| ReAct 白盒单步规划 | T50 / P95 | 0.95 | 750 | 100 | 5 | 1,200 |
| ReAct 白盒整体规划 | P75 / P95 | 0.95 | 1,550 | 150 | 3 | 1,900 |
| ReAct 白盒机器数据分析 | T50 / P95 | 0.95 | 950 | 100 | 3 | 1,250 |
| KPI 配置保障 | run_08 carry-forward | 0.93 | 500 | 300 | 4 | 1,600 |
| NetQAD E2E 单步规划 | T50 / P95 | 0.95 | 950 | 100 | 2 | 1,100 |
| NetQAD E2E 规划模块 | P75 / P95 | 0.95 | 2,100 | 200 | 3 | 2,550 |
| 事件自闭环 | run_08 carry-forward | 0.93 | 700 | 350 | 3 | 1,700 |
| 告警诊断建议总结 | P75 / P95 | 0.95 | 1,750 | 150 | 3 | 2,100 |
| 告警诊断结论总结 | P75 / P95 | 0.95 | 1,900 | 200 | 1 | 2,000 |
| 长文本根因推理 | ceil(max) 诊断 | 0.95 | 1,450 | 100 | 1 | 1,450 |
| 投诉助理工具调用 | P75 / P95 | 0.95 | 1,250 | 100 | 3 | 1,550 |
| 投诉分类中文 | run_08 carry-forward | 0.93 | 400 | 200 | 3 | 1,000 |
| 投诉分类英文 | run_08 carry-forward | 0.93 | 400 | 200 | 4 | 1,050 |

每个 TXT 中的实际请求块均严格使用以下字段顺序：

```text
enable_think_budget = true
fixed_prob_th = ...
fixed_check_length = ...
fixed_think_stride = ...
fixed_num_thinking = ...
fixed_min_think_tokens = ...
fixed_budget_tokens = ...
```

## 5. 下一轮执行与验收

下一轮对 13 个测试集都显式复制 `fixed_v4_quant_post_train` 块，history 和 difficulty
继续关闭。不得把开源权重 Vanilla 作为质量基线。

最低验收：

1. 每个 DTB 请求 `decision_branch=fixed`，七个请求字段、有效 `BudgetPlan`、hard
   limit 和 `source_by_field` 完全一致；
2. 与 run_08 的 633 条量化增训 Vanilla 按“测试集 + QA_ID”完整配对，问题和参考答案
   不变；
3. 每场景优先 `correct(v4) >= correct(Vanilla)`；无严格候选时最多少 1 题；少 2 题及
   以上失败；
4. 不新增解析、工具调用、关键事实遗漏等 hard failure；
5. 只有通过质量层后才比较 E2E；carry-forward 场景若出现跨轮波动，先复测，不立刻
   改参数；
6. 报告 hidden probe、visible think、final answer 和 phase wall，不能只比较客户端
   completion token；
7. v4 通过后，才把入选参数写入量化增训权重的独立 YAML，并由部署人员在启动服务前
   人工选择替换当前 `budget_config.yaml`。

建议命令（本地校准审计；不会自动采用其激进的默认 `0.50 × T50` 候选）：

```powershell
python tools/construct_fixed_candidate.py "docs/log/perf/run_08_quant&post_train" `
  --vanilla-result "test_result/qp_vanilla_<测试集>_<timestamp>.csv" `
  --candidate-label audit_only `
  --dry-run
```

## 6. 证据强度与变更记录

- run_08 E2E、质量、旧配置和 DTB 停止行为为**实测**；
- Vanilla 思考分位数为同 run DTB ledger 校准的**估计**，服务器 tokenizer 复算更权威；
- v4 参数的表现为**待实验**，不能从构造公式推断已通过。

| 日期 | 变更 |
| --- | --- |
| 2026-08-12 | 初版：基于 run_08 构造 4 个 carry-forward、3 个 T50/P95、5 个 P75/P95 和 1 个完整思考诊断候选，并写入 13 个测试集同名 TXT。 |
