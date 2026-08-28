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