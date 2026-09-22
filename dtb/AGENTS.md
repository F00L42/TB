# DTB 修改导航

本文件补充根 `AGENTS.md`，只描述生产包的入口和边界。实现/设计状态索引见
[`../docs/design/README.md`](../docs/design/README.md)；配置/部署见 [`README.md`](README.md)。

## 按问题定位

| 问题 | 首查符号 | 对应测试 |
| --- | --- | --- |
| 请求覆盖、路由身份、任务清理 | `request_controller.py::DtbRequestController` | `test_dtb_token_prompt.py`、`test_dtb_prompt_copy.py` |
| YAML 生命周期和校验 | `config/loader.py`、`config/schema.py` | `test_dtb_config.py` |
| 场景拒识、分数、优先级 | `budgeting/scene_router.py::classify_scene` | `test_dtb_scene_router.py`、`test_dtb_static_cpu.py` |
| 阈值/预算分支/来源 | `budgeting/budget_resolver.py::DtbBudgetResolver` | `test_dtb_budget_resolution.py`、`test_dtb_scene_config.py` |
| 原生观察、介入、abort/drain | `reasoning/deferred_observation.py::_DeferredInterventionState` | `test_dtb_intervention.py` |
| thinking / probe / final | `reasoning/generation_states.py` | `test_dtb_state_machine.py`、`test_dtb_final_answer.py` |
| prompt 快照、引擎上下文、输出额度 | `reasoning/request_context.py::DtbRequestContext` | `test_dtb_prompt_copy.py`、`test_dtb_token_prompt.py` |
| 边界识别、闭合、terminal | `reasoning/parser_boundary.py`、`reasoning/request_state.py` | `test_dtb_reasoning_protocol.py`、`test_dtb_final_answer.py` |
| 置信度及缺失值 | `reasoning/confidence_gates.py`、`reasoning/token_filter.py` | `test_dtb_confidence_and_trial.py`、`test_dtb_collection_optimization.py` |
| off / phase / stream audit | `observability/config.py` 与 recorder/writer | `test_dtb_telemetry.py` |
| 非流式合并与原生 parser 调用 | `integration/vllm/response_adapter.py` | `test_dtb_static_cpu.py`、`test_dtb_prompt_copy.py` |
| 可选在线历史 | `history/budget_policy.py`、`history/store.py` | `test_dtb_history.py` |

测试文件均在根 `tests/`。使用 `python tools/dtb/check_static.py` 跑交付门禁；
单模块迭代可用 `python -m pytest -q tests/<测试文件>`，遇到共享临时目录权限问题时改用统一脚本。

## 生命周期与并发所有权

- controller / 不可变 service config / budget resolver / telemetry writer 是服务级对象。
  `DtbRequestContext`、`DtbRequestState`、队列、可变 prompt、预算结果和 suffix cache 属于单请求。
- 流式先运行原生 observation；满足既有协议/阈值/容量条件后才触发完整预算决策和接管。
  非流式直接走三状态路径。不能把“每个请求都先 think→probe→final”当作当前行为。
- producer 与 parser consumer 独立运行，让观察者及时消费引擎输出。不要合并 DELTA、改成
  直通生成器、修改队列容量或加入并行 child 来消除表面上的 await/队列成本。
- observation 短路径可借用原始 prompt；开始修改前 `own_prompt()`，每个续写 child 用
  `engine_input()` / `child_prompt()` 得到稳定 token 快照。保留所有权复制。
- `reasoning_state` 表示逻辑推理状态，`reasoning_boundary_emitted` 表示 parser 是否已收到边界。
  final 可在没有历史输出模板时先闭合 prompt，再于首块输出补发边界；额度必须预留。
- `_FinalAnswerState` 内的 `_FinalLimits` 仅保存提交前额度供 sampling/telemetry 使用；
  `DtbRequestState` 仍是唯一请求账本。正常 final terminal 必须等生成器耗尽后才能发送，
  error terminal、取消和异常不能包装为成功。
- 关键词 LRU 最多保存 2048 条不可变规则，只缓存配置字符串，不缓存请求文本、分类结果或
  tokenizer。请求的 casefold 文本和匹配列表保持局部。缓存冷启动可重复构造等价规则。
- history store 和 token-filter 的兼容 setter 有进程级状态。生产路径不会按请求调用 setter；
  不要将它们用于不同并发请求的动态配置。改变隔离语义需另做行为设计。

## 改动时的证据要求

先跑原行为测试，再修改；预算算法、cue、parser 增量与取消路径的变更不能混称静态优化。
本地 mock/冻结回放可验证给定输入下的契约；不能证明服务器质量、吞吐或性能。
固定版本的 serving/parser patch 与包内 Python 各有单一来源，不增设镜像。
