# 建议的压缩参数（交付模板）

复制本模板后填写方括号内容，并将配套报告的链接改为本次项目实际位置。方法见
[从测试集得到配置](../compression_calibration.md)，报告格式见[迭代报告模板](compression_iteration_report.md)。
此文件当前是模板，没有替新数据推荐生产参数。

## 当前结论

- 状态：[pending_remote / 已测候选 / 目标范围内已验证 / 保持 native]
- 推荐版本、日期、负责人：[填写]
- 一句话建议：[哪些场景使用什么策略；哪些继续 native；为什么]
- 关联迭代报告、采纳轮次、验收集与 run ID：[填写实际位置/hash]
- 最优性的范围：[仅在什么数据、候选集合、目标指标和资源约束内比较过]

## 适用身份与数据

| 项目 | 本次值 |
| --- | --- |
| 模型、量化/后训练、tokenizer 修订 | [填写，不能只写服务别名] |
| DTB / vLLM / Ascend commit，torch / torch-npu / CANN | [填写] |
| 数据集、校准/留出版本/hash、评分器版本 | [填写，注明会话分组规则] |
| 场景/调用角色、system 身份及路由方式 | [填写，含未匹配动作] |
| stream/JSON、sampling/seed、并发/到达节奏/cache/MTP | [填写实测范围] |
| 质量与收益门、置信区间/样本量依据 | [填写预定标准及是否满足] |

## 推荐向量与部署文件

| scene/身份 | action | C/check_length | S/think_stride | R/num_thinking | M/min_think_tokens | B/max_think_tokens | p/prob_th | 策略与依据轮次 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [目标场景] | [dtb/native] | [填] | [填] | [填] | [填] | [填] | [填] | [C=B direct 或 C<B probe；轮次] |
| [未匹配] | [填] | — | — | — | — | — | — | [回退理由] |

完整候选 YAML：[路径/归档位置与 SHA256]。实际规范文件：[部署路径与 SHA256]。
附已校验的完整文件或可复现 diff；表格不直接作为 YAML。说明评分算法、Value-Min、history、
difficulty、安全门、服务硬上限、max_internal_calls 和 parser/token ID 契约；未改变的字段
通过完整配置 hash 固定。C=B 时标明 p/S/R/M 哪些实际未参与决策，避免把保留字段当作已调优。

请求 fixed 覆盖：[验证阶段使用哪些；自动路由验收时全部删除的证据]。
服务 fixed 覆盖：[应为零或注明保留原因与影响]。实际 route/budget source 审计：[run ID]。
最终答案 completion 额度：[填写，区分 B 与 max_tokens]。

## 支撑结论的结果

| 范围 | N/配对数/重复 | Vanilla→候选正确率；TF/invalid/hard failure | off E2E 与短/长层 | phase 介入/probe/隐藏成本 | 结论/证据 |
| --- | --- | --- | --- | --- | --- |
| 校准集 | [填] | [填] | [填] | [独立 run] | [填] |
| 留出集/关键场景 | [填] | [填] | [填] | [独立 run] | [填] |
| 实际 Agent 闭环 | [填或未执行] | [任务成功率] | [任务耗时与模型 HTTP 分列] | [填] | [填] |

长度统计注明 actual IDs / local_reencode / estimate。不得将候选置信度当成答案正确率，
不得将 visible token 减少当成净 E2E 收益。未测项统一 `pending_remote`；若只有调参集
结果，不将状态写成目标范围内已验证。

## 上线、回滚与重标定

1. [备份旧完整配置/patch/包的身份和路径；记录回滚目标]
2. [部署成套实现与规范 YAML；重启命令；实际加载/hash 核验]
3. [自动 scene/native 及流式/非流式/工具/多轮验收命令和预期]
4. [正式 off 验收命令、质量与短层回退触发条件]
5. [恢复旧配置并重启，或以明确开关回原生；核验 fixed 不会越过 native 路由]

重新标定触发：[模型/模板/tokenizer/parser/关键服务版本变更、调用角色与输入分布漂移、
正确率或短请求性能回退等；分别记录触发依据]。

## 推荐版本变更

| 日期/版本 | 从哪轮报告得出 | 推荐变更/回滚 | 证据与未决项 |
| --- | --- | --- | --- |
| [填写] | [填写] | [填写] | [填写] |
