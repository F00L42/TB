# DTB 输入去 decode 与 Token ID 权威续写设计

## 1. 设计目的与基线

状态：已完成本地实现与无模型验证，目标服务器协议/性能验收待执行。设计日期：2026-09-04；原始设计基线：
`8f71b852965db7864ccb066912c446e3458ceec3`。源码核对使用本地 vLLM `v0.23.0`
（`0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665`），不代表目标服务器身份或性能实测。

最终目标是在质量不退化的前提下，降低 Agent 多轮执行的任务级端到端时延。Agent 请求的思考
长度极端长尾，短思考请求不应反复支付与全部输入长度有关的控制器成本。本次作为
[最低介入值机制](dtb_minimum_intervention_threshold.md)的独立前置重构，先取消 DTB 完整
输入 prompt decode 和可变文本镜像，再实施同 child 原生直通。

本文承接 [token-stop 设计 §4.7](dtb_reasoning_boundary_token_stop.md#47-continuation-prompt-的-token-authoritative-设计)，
将此前的“缺文本时惰性 decode”收敛为“运行请求不做完整输入 decode”。
[性能分析文档](../debug/deer_performance_analysis.md)记录优先级和测量结果；输入、续写和
兼容性契约以本文为准，不把详细接口设计散落在历史性能报告中。

本次不改变 probe 算法、预算参数、停止参数、parser、采样、子请求次数或取消机制；不提前实施
最低介入值。输入去 decode 和续写账本同时覆盖流式、非流式共用路径，但非流式原有
stop/full parser 行为保持不变。取消的是 **DTB 对完整输入的重复 decode**，不是首次 chat
模板渲染/tokenization、输出增量 detokenization、协议校验或静态 cue 编码。

## 2. 当前行为与证据

以下描述原始设计基线的行为，均为**源码确定**，不是已测耗时；实施结果见 §6.1：

| 位置 | 当前行为 | 对设计的约束 |
| --- | --- | --- |
| [request_controller.py](../../dtb/request_controller.py)，`handle_request` | 先完整 `decode(prompt_token_ids, skip_special_tokens=True)`，再调用 `visible_user_text` | 即使最新 user 文本存在，也已支付完整 decode |
| [request_adapter.py](../../dtb/integration/vllm/request_adapter.py)，`visible_user_text` | 逆序选取最新非空 user 文本，缺失时使用 decoded fallback | 正常 chat 分类不依赖完整输入 decode；必须保留文本选取语义 |
| [dtb_serving.patch](../../patches/vllm/v0.23.0/dtb_serving.patch) | 接收原生 `conversation, engine_inputs`；将 `prompt_components.text` 放入 `_dtb_route_identity.prompt_text` | 已有分类原料和可选文本通道，无需 proxy 新增 HTTP 字段 |
| [render/serving.py](../../third_party/vllm/vllm/entrypoints/serve/render/serving.py)，`render_chat` | 返回规范化 conversation 和 engine inputs | 复用原生预处理结果，不二次解析/渲染整个消息列表 |
| [preprocess.py](../../third_party/vllm/vllm/renderers/inputs/preprocess.py)，`extract_prompt_components` | 从目标 prompt 读取可选 `prompt` 和 token IDs，不调用 decode | `prompt_components.text` 可能为 `None`，不能承诺所有输入都有全文 |
| [request_context.py](../../dtb/reasoning/request_context.py)，`append_*` / `child_prompt` | 模型输入已使用 IDs，但仍同步拼接 `prompt_text` 和字典中的 `prompt` | 只删除入口 decode 还不等于取消可变文本镜像 |
| [async_llm.py](../../third_party/vllm/vllm/v1/engine/async_llm.py)，`add_request` | 普通 prompt 字典路径重新从字典提取 `prompt_text` | 不能仅传 `prompt_text=None` 而保留已经失真的 `prompt` 键 |

## 3. 三项核心决策

### 3.1 分类文本：复用 serving 数据，只选取一次

可以从 vLLM serving 获取文本，但要区分“已有文本”与“DTB 分类输入”：原生 serving 已完成
消息规范化，并不额外提供一个具有 DTB 语义的 `classification_text` 字段，也没有替 DTB
完成 scene/difficulty 分类。仍需一次轻量的最新 user 文本选取，不需要任何完整 prompt decode。

首版继续由 `dtb/integration/vllm/request_adapter.py` 的 helper 在 controller 入口执行一次选取；
它的输入直接是 serving 已传入的 `conversation` 和可选 `prompt_components.text`。不把 scene
规则移入上游 serving，不在 serving 和 controller 各选取一遍。helper 返回的文本视图由预算
解析和 context 复用；不再自行渲染 chat template、拼接全量历史或 decode tokens 来获取分类文本。

分类来源按以下顺序确定，并明确记录来源：

| 条件 | `classification_text` | `classification_source` |
| --- | --- | --- |
| 存在非空可见 user 文本 | 与当前 helper 相同的最新非空 user 文本 | `latest_user_message` |
| 无上述文本，serving 已有非空全文 | 直接使用该全文 | `serving_prompt_fallback` |
| 两者都没有可用文本 | 空字符串 | `missing` |

第一行保持当前字符串、文本块拼接与 strip 语义，不改为最新 tool 输出或全文；无文本的图片块
不转成描述文本。第二行只是兼容兜底，不能与最新 user 等同，也不能保证与旧
`skip_special_tokens=True` 解码文本相同：角色标记、特殊 token 的文本表现可能不同。
此类请求必须单列比较 scene、decision branch 和预算，不能宣称全输入域分类完全不变。

第三行不为补文本触发 tokenize/decode，不静默改走 Vanilla，也不因缺少可选文本拒绝原生有效
输入。继续交给现有 resolver：保留 fixed/scene override 优先级；没有分类证据时按现有
`general` 及 history/default 规则处理，并记录 `classification_source=missing`。
这是相对旧 decoded fallback 的明确兼容性变化，须有单独用例，不可混入正常 chat 等价结论。

这里的“续写 prompt 历史”和“在线预算 history”不是同一概念。
[history/budget_policy.py](../../dtb/history/budget_policy.py)的预算统计按 scene 读取，当前不使用
question 内容作为预算查找键；[history/store.py](../../dtb/history/store.py)仍保存 question hash。
本次不顺带更改 history 的统计、写入策略或 schema；缺文本/来源改变对 scene 和 question hash
的影响单列审计，不为维持旧 hash 重建完整 prompt。

### 3.2 续写输入：Token ID 是唯一可变事实源

状态机已拿到原生 `prompt_token_ids`，因此维护历史 prompt、预算长度和提交下一 child 均以
Token ID 为准。初始文本、分类文本以及可见输出文本都不能反向恢复或覆盖输入 IDs。

采用轻量 `ContinuationPrompt` 账本（可封装进现有 context）：

1. 从 serving engine input 接管一个由 DTB 持有的 token list；不得原地改写 serving 的原始列表。
2. 仅追加现有状态机本来就追加的生成 IDs、协议 boundary IDs、静态 cue 编码 IDs，不改变顺序
   和次数。输出字符串不是重新 encode 的输入；现有 legacy 输出边界处理不在本次改写范围内。
3. hidden probe 从主账本 fork 新列表，加 probe suffix；探测内容不能写回主账本。
4. 每次 engine submission 持有稳定的 token 快照；不能在引擎仍可能读取时继续 mutate 同一列表。
5. 延续原生 multimodal/cache 等适用字段，以及 LoRA、priority、trace headers、DP 亲和性、
   reasoning/parser 参数。不能因 token-only 而重建一个丢失上下文的最小字典。

初始 child 如果 IDs 未改变，可以保留 serving 原有的匹配文本。**任何追加或 fork 后的 child
都必须移除旧 `prompt` 键，且不携带旧 `prompt_text` 提示。** 即使保留了原始文本用于调试，
也不能把它冒充当前 child 的全文传给 engine。

原始基线的 `engine_request_kwargs(prompt_text=None)` 会回退到 `self.prompt_text`，不具备
“显式清空”语义。实现采用 `engine_request_kwargs(child_prompt, *, reasoning_ended=...)`，
直接从实际 child input 派生可选文本，避免两个文本来源。字典的失效 `prompt` 字段同步移除，
因为 vLLM 会重新读取它。

### 3.3 可调试文本：可选、不可变、不进入模型输入决策

如果 serving 透传层已提供全文，保存一次 `initial_text` 和来源即可；优先保存字符串引用，
不复制或序列化全文。`None` 表示未提供，空字符串表示提供了空文本，不能使用连续 `or`
表达式混淆二者。最新 user 文本不能被冒充为完整 `initial_text`。

不再随着 thinking/cue/boundary 累计完整文本镜像。已有生成 delta text 仍照常进入输出 parser，
必要时由现有白名单 stream audit 保存，调试时可组合初始快照与生成事件查看；需要精确查看
任意 child 全文时，在离线工具中对保存的 token 快照 decode，而不是在请求热路径恢复完整文本。
`OBSERVABILITY.mode=off` 不新增全文日志、全量 token 序列化或 debug decode。

兼容 `return_prompt_text=true` 时，外部响应只应展示原生外部请求已有的 `initial_text`；原生
没有文本就保持 `None`，不按需完整 decode，不暴露内部 thinking、probe 或 cue。vLLM chat
stream/full 分别从 `res.prompt` / `final_res.prompt` 构造这个字段，响应适配层必须独立处理，
不能为了它给内部 child 填失真文本。必要时构造外部输出元数据视图，不修改 engine 输入或
审计保存的原始输出。这属于需要专门验证的可选响应字段行为修正，不承诺与旧内部 child 文本
泄漏行为逐字一致；普通 reasoning/content/tool 输出应保持一致。

## 4. 核心结构与接口改动面

以下为概念接口；首版将 `ContinuationPrompt` 的职责封装在现有 `DtbRequestContext`，没有新增
同名类或通用 prompt 框架。`PromptTextViews` 已实现为 frozen dataclass。

```python
@dataclass(frozen=True)
class PromptTextViews:
    classification_text: str
    classification_source: str
    initial_text: str | None       # serving 原生全文，不随续写变化
    initial_text_source: str       # serving_prompt / missing

class ContinuationPrompt:
    engine_fields: dict           # 保留原生适用字段；不含失效文本
    token_ids: list[int]           # DTB 持有；唯一可变输入账本

    def append_tokens(self, ids: Sequence[int]) -> None: ...
    def engine_input(self) -> dict: ...  # 为一次提交提供稳定快照
    def fork(self, suffix_ids: Sequence[int]) -> dict: ...

def extract_prompt_text_views(conversation, initial_text) -> PromptTextViews:
    ...  # 只选取已有文本；无 tokenizer 参数
```

| 模块 | 职责与改动边界 |
| --- | --- |
| `dtb/integration/vllm/request_adapter.py` | 将文本选择与来源表示集中到一次 helper 调用；删除 decoded fallback 语义 |
| `dtb/request_controller.py` | 删除完整 decode 和捕获异常后空文本兜底；复用 serving 数据创建文本视图、预算和 token 账本 |
| `dtb/reasoning/request_context.py` | 移除可变全文镜像；统一 append/fork/提交快照；明确 child 可选文本语义 |
| `dtb/reasoning/generation_states.py` 及调用点 | 子请求继续使用同一 IDs；probe 不再合成 child 全文；调用参数取自实际 child |
| 输出适配层 / `dtb_serving.patch` | 按需保持外部 `return_prompt_text` 契约；不新增公共请求字段，不改 parser patch |
| `tests/`、现有 telemetry | 加来源/账本/可选响应字段验证及入口到首次提交计时；关闭模式无新增重型观测 |

首版不要求扩大 serving 私有协议：当前 `conversation` 和 `_dtb_route_identity.prompt_text` 足够。
若实现中需要调整私有字段命名，必须原子更新 patch/adapter/test，不能让旧字段静默变成
`missing`。静态 cue 仍使用现有请求级编码缓存，编码结果才是追加输入。

## 5. 一致性与验收

### 5.1 本地无模型门禁

- 对正常 chat、文本块、user/tool 多轮、缺 conversation、有/无 serving 文本、空文本建立来源
  用例。正常 chat 的分类文本、scene、预算应与旧实现一致；两个 fallback 分支独立列出差异。
- 使用专门的 spy 断言入口和续写不再 decode 完整输入；不能粗暴禁止 tokenizer 所有 decode，
  因为输出 detokenization 和协议校验不属于本次删除范围。
- 固定 fake engine 返回的 IDs、输出 text 和置信度，逐 child 比较重构前后的输入 IDs、采样
  参数、调用次数、budget decision、usage 和终止语义；覆盖自然结束、预算耗尽、probe
  accept/reject、forced close、错误、客户端取消及流式/非流式。
- 测试自然 boundary、纯 token boundary、混合 terminal text 和工具参数增量：只改输入存储，
  不丢失、重复或改写 parser-facing 输出 text/IDs。沿用三个已部署 patch 的协议回归门禁。
- 验证 probe fork、提交快照和主账本无可变列表别名；serving 原始 engine input 不变；所有
  追加后的 child 无失效 `prompt` 或 `prompt_text`；原生路由/多模态/cache 字段未丢失。
- 对 `return_prompt_text` 开/关分别覆盖流式与非流式、原生文本有/无、初始/多 child。
  外部只返回初始文本或 `None`，无内部 prompt 泄漏；保存文本从不改变输入 token 序列。

### 5.2 服务器性能与质量验证

仅比较“现有 eager DTB”与“输入去 decode 后的 eager DTB”，固定部署的三份 patch 其余语义、
模型/服务 SHA、配置、sampling/seed、并发、到达节奏、prefix cache 状态；Agent 主实验保持
现有单并发。此轮不修改 `check_length`、probe 参数，不混入最低介入值，`stream_interval`
保持默认 1，不新增其他 interval 测试。

先复用真实 Agent 请求做 paired replay，包含短/长输入及短/长生成，特别保留“长输入、短思考”
组。独立检查正常分类等价组和 fallback 行为变化组；协议与质量通过后复跑完整 9 场景，报告
任务正确率、缺失/hard failure、请求级 E2E/TTFT，以及 Agent task wall。服务器随机轨迹差异
不能仅凭 seed 相同认定为输入去 decode 导致；先核对子请求输入与首分叉。

正式 E2E 排名使用 telemetry `off`；另做 `phase` 或 profiler 诊断 run，测量从 controller
入口到首次 engine 提交的总 CPU/wall、文本选取时间、token 快照复制、字符串分配及峰值内存。
原始基线的 `decision.total` 包围的是 resolver，**不包含它之前的完整 prompt decode**；context 的
`budget_start_time` 也在这之后创建，不能仅用这两个指标证明入口成本已消失。新版增加独立的
入口到首次提交 wall 区间，具体含义见 §6.1；独立 CPU、分配和内存仍需 profiler 测量。

旧版记录的 `decision.decode_prompt` 和新版无该操作的直接计时可以归因；仅仅删掉同名计时项
并不构成性能证据。诊断结果不得混入 off 性能基线，不额外串行记录全文来“测”其移除收益。

### 5.3 收益与剩余成本

**源码确定可移除的工作**：一次完整输入 decode；多次随着思考增长进行的全文字符串拼接与
镜像存储。已有 serving 文本无需再创建相同内容的 DTB 字符串。

**仍然存在的成本**：最新 user 文本选取/必要的文本块拼接、scene/difficulty/history 决策、
token list 所有权与提交快照复制、静态 cue 编码、状态机/queue、模型计算及输出 detokenize。
逆序选取最坏仍需扫描 conversation；token 快照复制仍随输入长度增长，不宣称零 CPU 或零拷贝。

**待实测假设**：长输入短生成的 TTFT/CPU 会更受益，去除文本镜像能降低多轮复制成本。新增的
稳定 token 快照也有内存/复制成本，净收益不能先填百分比。本次不减少二次提交、重复 prefill
或首字等待，不保证单独带来 Agent 任务级净加速；这些短路径结构性成本由下一步最低介入值处理。

## 6. 实施顺序与设计边界

1. 独立 changeset 完成文本选取去 decode、Token ID 账本、可选文本快照和响应兼容；不长期
   保留“分类不 decode、context 又 decode”或“token-only 输入、child 还携带旧全文”的过渡态。
2. 通过本地门禁和服务器 eager 配对验证，冻结源码/patch hash；记录正常 chat 等价范围、
   fallback 行为变化和实测增量成本。
3. 以该版本为最低介入值设计的 eager 对照，再实施 deferred 同 child 直通，分别核算两项收益。

本稿选择无文本时显式 `missing`，不提供运行时完整 decode 兜底。若某个额外插件确实要求
每个内部 child 的全文，必须先确认其兼容性要求；不能隐式恢复镜像或把 decode 移到 serving、
异步线程或日志路径就称为完成优化。未识别的插件依赖是部署前检查项，不是原生 vLLM 的必要条件。

### 6.1 本地实施结果（2026-09-04）

- adapter 一次生成 `PromptTextViews`；controller 已删除完整输入 decode 及其异常兜底。
  serving 现有私有字段足够，无需更改或重新应用三份 vLLM patch。
- context 构造时复制原生输入字典和 token list；`engine_input()` 为每次 thinking/final 提交
  建立稳定快照，`child_prompt()` 为 probe fork 新 token list。原生非文本 payload 只共享引用，
  不做 deepcopy。所有 append 都会移除失效 `prompt`，不再维护 `context.prompt_text`。
- 初始全文经 `context.prompt_text_views.initial_text` / `initial_prompt_text` 读取，不随续写
  增长。`engine_request_kwargs` 从实际 child 字典读取可选文本，不接受独立全文覆盖参数。
- `return_prompt_text=true` 在外部消费侧浅拷贝 `RequestOutput` 顶层、仅改 `prompt` 元数据；
  输出 text、IDs、finish/stop、prompt IDs 和源审计数据不变。关闭该选项不做这次浅拷贝。
- `phase` 增加 `decision.select_text`；`DTB_REQUEST_SUMMARY.modules` 增加
  `controller.entry_to_first_submit`，每个实际提交过 child 的请求只记一次。后者是重叠 wall
  区间，包含配置/协议绑定、决策、context/token 快照及首次提交前的异步调度/消费等待，
  **不是纯 CPU，也不是新模型 phase**，不得与 decision 或 initial_think wall 相加。
  `decision.total` 继续只计 resolver；`off` 不新增计时器或 writer。

本地验证入口（Windows PowerShell；Linux 可使用 `PYTHONUTF8=1 python ...`）：

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests -q -p no:cacheprovider
git -C third_party/vllm apply --check ../../patches/vllm/v0.23.0/dtb_serving.patch ../../patches/vllm/v0.23.0/qwen3_coder_parser.patch ../../patches/vllm/v0.23.0/qwen3_reasoning_parser.patch
```

新增 [test_dtb_token_prompt.py](../../tests/test_dtb_token_prompt.py) 81 个用例，覆盖 text 来源、
完整 decode 禁止、off/phase、输入所有权、流式/非流式、probe 接受/拒绝、返回全文开/关、
异常及取消；现有状态机测试从可变文本镜像断言改为精确 token 后缀断言。
尤其对 `include_stop_str_in_output=False` 的空文本/混合正文 terminal chunk，检查 parser-facing
text/IDs 与下一 child 输入 IDs 两条路径分别保真，不补写 marker 文本，也不丢弃 stop ID。

三份 patch 的合并 `git apply --check` 已通过；全量 **229 tests / 1966 subtests 通过**，包含
既有 parser 正/负对照，已同步性能分析文档变更记录。这些是无模型门禁，不能等同于真实
tokenizer、NPU 或 HTTP 测试。
Windows 首次基线测试有 3 个 reasoning-tail 子进程失败，设置 UTF-8 环境后 3/3 通过，未修改
该测试或 parser 来绕过失败。

部署时只更新 `dtb/` 代码并重启服务；已完整应用三份 patch 的服务器不重复打 patch。保留
原 eager/算法配置，按 §5.2 先进行配对验收，归档 DTB commit、三份 patch hash、服务身份和
observability mode。目标服务器验收通过之前，不实施最低介入值，也不宣称已经取得 E2E 收益。

## 7. 变更历史

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-04 | 完成本地输入去 decode、Token ID 续写、外部初始文本元数据适配及定向回归；增加重叠入口 wall 指标，三份上游 patch 不变 | 保持两条 text/token 路径独立保真，先验证 eager 前置优化；服务器性能待验收 |
| 2026-09-04 | 初稿：取消 DTB 完整输入 decode；分类复用 serving 数据，续写 Token ID 权威，文本仅保留不可变快照；明确缺文本与响应兼容契约 | 最低介入值实施前先去除短请求不必要的输入处理和双写成本，并独立验证收益 |
