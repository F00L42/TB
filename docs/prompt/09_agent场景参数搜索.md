# agent场景参数搜索
## 背景
AGENTS.md
docs\design\agent_multiturn_dtb_experiment_plan.md
代理透传proxy\AGENTS.md
黑盒评测lightcode\AGENTS.md
## 目标
开启dtbauto后，lightcode任务执行正确率不变，端到端时延降低
### 现状
针对9个vanilla一定能完整执行的场景进行两轮实验，结果为
* vanilla 9任务都有完整结果
  * 透传日志proxy\results\qwen\local-task29\local-apiserver\vanilla\run-03
  * 评测结果lightcode\session_logs_0827_vanilla
* dtb 4任务无评测结果
  * 透传日志proxy\results\qwen\local-task29\local-apiserver\dtb\run-02
  * 评测结果lightcode\session_logs_0828_dtb
### 思路
当前固定附加的dtb参数在proxy\agent_dtb_proxy.py: 74
先分析任务失败的错误根因，给出从执行记录到源码的具体证据
再设计下一步实验和验证方案

---

1. dtb固定参数的实验原因是因为分析vanilla思维链分布（docs\debug\vanilla_baseline_analysis.md）后，发现长思维链的分布极度倾斜，短的极短，只有少数长思考，经过分析，长思考中确实存在已得到最终答案但冗余思考的情况，因此将优化策略确定为短程自由思考，专注优化长思维链来得到时延优化，目前通过固定参数接口进行实验，确认可行后配置对应的yaml配置文件即可自动启用
2. 基于1，短思维链也加入answer cue（docs\design\think_budget_finalization_compatibility.md）的行为可能造成时延的不必要消耗
3. tool_call故障的排查是第一优先级，已确认服务器环境已完整应用现有的修复方案（dtb+parser patch），可以先抽取一些run-02中存在故障的prompt进行配对流失回放时延，提取结果和测试脚本

---

docs\log\perf\agent_tool_call_run02_replay\run_server_01是runbook执行结果，没有发现问题
回顾目标和思维连分布特征，我认为answer cue的修改对tool_call成功率和时延的影响嫌疑大，分析：对于短思维链（十几个token）开启dtb的场景，模型在输出</think>或者<tool_call>后，要先看一遍十几个token的诱导prompt再续写，带来额外的首字时延消耗和语义偏移
找到并展示源码里相关的证据，分析这一猜想的证据是否准确，评估实际影响会有多大

---

已执行回放实验，共196个异常，结果位于docs\log\perf\agent_tool_call_run04_paired_replay\run_server_01
其中有测试结果，服务器日志提取结果、metrics等，manifest未提及修改则一律复用模板
1. 测试结果分析
   1. 当前回放是否复现了端到端测试中的报错
   2. 一共有多少种报错，有多少vanilla和dtb都存在的报错
2. 解决报错
   1. 问题原因定位
   2. 参考社区PR（docs\debug\tool_call_streaming_debug_plan.md: 1358 上游缺陷族审计与修订方案），制作023版本的parser完整修复patch
   3. 验证dtb和vanilla同一表现的完整paired replay方案

---

run-05端到端实验结果
* vanilla
  * 透传日志proxy\results\qwen\local-task29\local-apiserver\vanilla\run-05
  * 评测结果lightcode\session_logs_0902_vanilla
* dtb
  * 透传日志proxy\results\qwen\local-task29\local-apiserver\dtb\run-05
  * 评测结果lightcode\session_logs_0902_dtb
1. 分析实验结果：正确率，端到端时延，总评测时延（如session_logs_0902_dtb\evaluation_results.json中每个任务的"start_time"-"end_time"）
   1. 对已知由于stop="</think>"停止参数导致的任务执行失败，后续的stop_token_ids重构计划可以解决，暂时不计入统计
   2. 输出结果分析
2. 规划下一步计划，如继续搜索参数或配置新场景或实验
   1. 考虑到每次在思维链结束后stop会造成额外的首字开销，是否有方法在较短的思维链场景中不触发停止？（即原生输出，只有当思维链长度超过一定阈值才停下来检验）

---

新一轮run-06全量测试结果
dtb
透传
proxy\results\qwen\local-task29\local-apiserver\dtb\run-06 附带phase日志
评测
lightcode\session_logs_dtb_06
vanilla
透传
proxy\results\qwen\local-task29\local-apiserver\vanilla\run-06
评测
lightcode\session_logs_vanilla_06
1. 当触发abort&probe路径时，是否存在异常回显现象
2. 分析任务执行结果和时延
   1. 比对分析评测都正确的情况下dtb能否达成时延优化
3. 规划如何进一步达成最终目标