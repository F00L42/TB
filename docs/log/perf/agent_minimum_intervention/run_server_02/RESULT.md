# run_server_02：原始输出与工具参数归因

分析日期：2026-09-08；结果提交：`b832b74e0fba7deeef551932c29735e720a8143d`。

## 1. 结论

**本轮没有复现“工具名存在、arguments为空”的协议故障。** 两个子run共120个请求，133次工具调用，全部参数JSON合法、schema审计通过。逐请求对照服务端完整输出中的工具XML、客户端SSE重建结果和summary，**133次工具调用的工具名及参数值全部一致，没有发现parser或转发丢参数。**

自动报告中的26个FAIL全部是 `expected_tool_name_missing`：测试预期下一步调用 `task` 或 `write`，而模型实际生成了带完整参数的 `bash` 或 `grep`。这是**模型动作选择与历史预期不一致**，不是缺少实际参数；“先取时间戳再写报告”等行为甚至可能是合法中间步骤，单轮回放不能将其直接判为任务失败。

**本轮可以排除“这26个告警由parser丢失实际生成的工具名/参数造成”；不能排除上轮3个空参数故障与parser有关。** 上轮故障没有在本轮重现，也没有取得上轮同一次生成的完整原始流。不能以新一次成功采样解释旧一次失败。

## 2. 实验身份与完整性

| 子run | manifest时间UTC | V / D请求数 | V / D协议失败 | V / D语义告警 | 完整输出日志 | 工具调用数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| replay_capture | 2026-09-08 03:36:06 | 30 / 30 | 0 / 0 | 6 / 6 | 60 | 68 |
| replay_capture_audit | 2026-09-08 06:10:26 | 30 / 30 | 0 / 0 | 7 / 7 | 60 | 65 |

两组均为历史 `cases.jsonl` 行2、4、10，各重复10次，两种variant按attempt交替先后，单并发。不是原计划的30次，也不是完整Agent执行。固定参数为 budget=20000、T=850、stride=500、num_thinking=5、min_think=1、prob_th=0.93。

已完成以下离线审计：

- 输入按LF规范化的SHA256为 `8667912b8a5b525c448cd8822281e7aa044df696cedbc2b16b42a46589e66e90`，与两份manifest一致；逐条重建120个request_body并与chunks记录的hash对账。
- 所有预期 `(line_no, attempt, variant)` 唯一且完整；全部HTTP 200、每请求一个有效terminal。
- 每个请求恰好匹配一条服务端 `(streaming complete)`，无缺失、重复或额外parent；原始XML工具/函数/参数开闭标记数量闭合。
- 独立读取XML参数时只剥离标签格式引入的首尾各一个换行，**不使用strip去掉代码/报告正文中的合法空白**。对本轮全部字符串参数，原始XML解析值与SSE拼接JSON及summary逐字段完全相等。
- 两个子run复用了 `request_id_run=intervention-g1r-capture`，有相同parent ID。此次用“子目录＋parent ID”隔离，未跨run串联原始输出与SSE。

两组服务器日志各有60条 `Added request`，其中30条为DTB `_native_observation_1`，未见probe/iterative/final child记录。这支持本轮DTB主要验证原生旁路；不是切换安全性或G0已通过的证明。两组都没有上传 `DTB_PHASE` 或 `DTB_STREAM_AUDIT` 事件，目录名带 `_audit` 不代表已有四层取证。

服务器原生Vanilla的 `Received request` 日志明确记录 temperature=1.0、top_p=1.0、top_k=20、seed=None、stop=[]、stop_token_ids=[]、max_tokens=32000、skip_special_tokens=True。DTB有效参数没有同样完整的独立记录，不能只由Vanilla推定全部内部child配置。仍缺完整部署commit/dirty、patch/YAML/tokenizer hash、硬件软件栈与缓存策略manifest；提交b832b是结果归档版本，不是服务器部署版本的证明。本实验开启输出日志，不做正式时延排名。

## 3. 三个有明确证据的样例

### 3.1 预期write，模型实际先取时间戳

`replay_capture`，parent=`chatcmpl-dtbtool-l10-a1-dtb-intervention-g1r-capture`。

服务端 `server_filtered_part_01:1445` 的完整输出明确表示先取时间戳：

```text
Good. Now I need to generate the JSON and Markdown reports.
Let me get the timestamp first and then create both reports.
</think>

<tool_call>
<function=bash>
<parameter=command>
date '+%Y%m%d_%H%M%S'
</parameter>
<parameter=description>
Get timestamp for report filename
</parameter>
</function>
</tool_call>
```

客户端 `chunks.jsonl:1431–1432` 最终拼接为：

```json
{
  "name": "bash",
  "arguments": {
    "command": "date '+%Y%m%d_%H%M%S'",
    "description": "Get timestamp for report filename"
  }
}
```

不是parser把write变成bash，也不是参数为空。模型本次没有生成write调用；审计器按历史预期write标记偏离。单轮回放不执行bash并继续模型调用，因此不知道下一轮是否会写报告。

### 3.2 预期task，模型实际检查测试目录

`replay_capture`，parent=`chatcmpl-dtbtool-l4-a1-vanilla-intervention-g1r-capture`。

- 服务端 `server_filtered_part_01:712`：`<function=bash>`，command为 `ls /app/code/CodeBenchRepos/service/src/test/java/com/huawei/i2000/dvaiagentservice/`，description为 `List test directory structure`，标记完整。
- 客户端 `chunks.jsonl:698–699`：同一个bash调用，两个参数逐字相等。
- 告警 `expected_tool_name_missing`：历史期望启动task，本次先检查目录；没有“实际task已生成却被parser漏掉”的证据。

### 3.3 预期task，模型实际搜索常量定义

`replay_capture_audit`，parent=`chatcmpl-dtbtool-l4-a10-vanilla-intervention-g1r-capture`。

- 服务端 `server_part_01:1115`：思考表示先检查HttpStatus导入，实际生成 `<function=grep>`。
- 参数为 `pattern=INTERNAL_SERVER_ERROR_500`、`path=/app/code/CodeBenchRepos`、`include=*.java`。
- 客户端 `chunks.jsonl:1121–1122`：相同grep及三个参数，JSON/schema合法。

所有26个告警均完成相同的原始XML→SSE→summary对照，完整索引及告警参数见 `output_evidence.json`。其中：24个是行10预期write、实际bash；另2个是行4预期task、实际bash/grep。并非26种故障。

## 4. `--enable-log-outputs` 到底记录了哪一层

依据本地固定vLLM v0.23.0源码，需区分两种日志：

| 日志 | 源码位置 | 语义与限制 |
| --- | --- | --- |
| `(streaming delta)` | `chat_completion/serving.py:769–795` | 以parser生成的delta_message拼成reasoning/content/tool_args摘要；只有有可打印内容且启用log_deltas才写。不是完整原始模型delta序列 |
| `(streaming complete)` | `chat_completion/serving.py:912–926` | 输出累计的previous_texts；本轮Qwen路径中是传入parser的detokenized全文，包含think/tool/XML结构；未保存完整token IDs或原始分片 |

在此Qwen路径，`:669–674` 用 previous_text + delta_text构造current_text，`:720–726` 送入组合parser，`:743` 保存current_text；complete日志因此可以用于本次“原始文本是否有工具/参数”的核对。它不是CPU/GPU内部原始token流；DTB请求的该全文也已经过控制器，不能单独定位controller前后的差异。

几个容易误判的字段：

- 完整日志的 `output_token_ids: None` 是 `serving.py:923` 显式传None，并不表示没有生成token，也不表示工具arguments缺失。
- `finish_reason: streaming_complete` 是日志标签，不是客户端实际finish_reason；实际结束语义应读取SSE/summary。
- 初始工具名delta带 `arguments=""` 可以是正常的增量协议。只有所有delta拼接完、请求结束后仍为空才是上轮所说的协议故障。
- `request_logger.py:80–86` 会按 `max_log_len` 截断日志。此次全部133次工具参数与SSE完全一致且结构闭合，足以支持本轮参数归因；但未记录该开关值，未来不能默认所有complete日志都无截断。

上述层级解释是固定源码推导，与本轮日志形态一致；实际部署完整hash仍待补充。本次已取得有用的全文证据，**不等于已经完成上一轮计划中的A/B/C四层逐delta取证或parser固定流版本回归**。

## 5. 根因裁决与后续动作

### 对本轮26个告警

**裁决：模型选择了不同的下一步动作；没有参数缺失或parser篡改调用的证据，且原始输出到SSE的实际参数全部保真。** 这些是期望匹配层的行为偏离，不应与工具协议损坏混计；是否属于真正模型任务错误，须看后续工具执行和最终报告，不能因没有立即调用write/task就判错。

审计工具的 `tools/dtb/replay_tool_calls.py:498` 将预期工具未出现记为 `expected_tool_name_missing`，`:506` 才是非法JSON，`:525`将前者列入语义告警。当前原自动RESULT中的Overall failure并不是协议失败数。本次保留原始报告，不为消除FAIL而修改期望或parser。

### 对上轮3个空arguments故障

**裁决：本轮未复现，根因仍未决。** 不能把本轮的bash/grep选择差异反推为上轮空JSON的原因，也不能宣布parser全局无关或已经修复。120次零协议错是正向证据，不是低概率故障不存在的证明。

建议后续将任务分开：

1. 本轮26个告警无需继续做“丢参数”排障，也不据此改parser。将其保留为语义偏离，长尾参数实验可继续准备/分析。
2. 如果继续追踪上轮空参数，保留同一输入的完整输出日志，等待捕获 **实际 `invalid_tool_arguments_json` 且最终arguments为空** 的那次请求。日志长度必须足以覆盖完整工具XML，使用独立run ID。
3. 若该次complete全文本身缺少函数闭合/参数，优先核对实际停止原因及模型/解码；若全文XML完整合法而SSE参数为空，再补实际tool parser输入与终态delta，做原版/补丁固定流对照。不要对新的正常bash调用做无限扩样。
4. 当前日志没有完整token IDs及模型原始分片，不能据此修复G0。新证据表明Vanilla使用skip_special_tokens=True，而G0脚本显式False；这支持优先对齐生产参数，但未证明该差异就是G0根因，也未验证重启。

## 6. 生产参数下的 G0 实验详细方案（待实施）

### 6.1 目标、起点与裁决边界

目标是确认：**真实生产请求经最低介入值机制取消、接纳完整排空前缀并重启后，是否保持text/token IDs及parser-facing语义连续**，而不是单纯消除日志中的特殊标记。基线为本报告分析的b832b结果与现有 `tools/validate_dtb_intervention_detokenizer.py`；不预设需要修改运行时。

当前证据：旧G0有60/768个固定序列文本不一致，支持的无string-stop子集也失败30/384；本轮原生Vanilla日志显示 `skip_special_tokens=True`，旧G0固定False。但DTB observation/probe/iterative/final的有效参数及实际decoder后端未全部捕获，**True尚不能代表所有生产child**。本节实验尚未执行，后文拟新增接口也尚未实现。

实验分三级：

1. **G0-P：生产参数对齐与真实decoder CPU-only对照。** 判断配置差异、collector或decoder初始化是否造成固定序列差异，不加载权重、不重新采样。
2. **G0-I：固定流经实际DTB适配及parser的影响验证。** 区分仅reasoning显示变化、正文/工具参数损坏、结束语义错误。
3. **G0-S：隔离服务上的真实长请求smoke。** 验证实际调用参数与前两级profile一致，确认真实切换路径可达；不能用“随机生成未碰到”代替G0-P通过。

G0-P/I通过意味着已测支持路径可进入小规模真实任务可用性试跑；不等于9场景质量非劣、历史空arguments问题解决或端到端性能验收通过。

### 6.2 第一步：冻结生产环境与有效参数profile

在**与推理服务相同的Python环境、tokenizer文件、插件环境和源码**执行；本机没有实际tokenizer/推理设备。新建独立输出目录，建议 `docs/log/perf/agent_minimum_intervention/run_server_03/g0_production/`；若已存在则使用新的run目录，不覆盖旧证据。

先保存 `manifest.json`：TB与vLLM/vLLM-Ascend commit、dirty差异、三个部署patch hash、YAML hash、模型修订、tokenizer文件hash、Python/transformers/tokenizers/fastokens（如未安装则明确记录）版本、PyTorch/torch-npu/CANN、相关插件/decoder环境开关及实验脚本hash。依赖版本对照必须使用隔离环境，不直接升级或改动生产服务。

**参数要取自实际构造完成的位置，不能仅复制HTTP request或SamplingParams默认值：**

| profile | 采集/重建位置 | 必须核对的差异 |
| --- | --- | --- |
| native | 原生serving调用generate前，以及frontend转换后的EngineCoreRequest | parser的adjust_request、服务默认generation配置、原生prompt模板是否已生效 |
| observation | `deferred_observation.py` 提交原生观察child前 | 是否与同一请求native的解码参数一致 |
| confidence_probe | `generation_states.py` 试答child提交前 | 实际TRIAL_PROMPT token后缀、logprobs、试答额度及reasoning_ended上下文 |
| iterative_think | 同文件继续思考child提交前 | 实际token-stop集合、stop处理、剩余额度、输出kind |
| final_answer | 同文件最终child提交前 | 自然boundary分支与强制收尾分支的不同prompt，cue/close/separator及剩余额度 |

每个profile保存**完整可序列化的有效SamplingParams快照**和来源；以下字段至少逐项列出：`output_kind`、`detokenize`、`skip_special_tokens`、`spaces_between_special_tokens`、`stop`、`stop_token_ids`、`include_stop_str_in_output`、`ignore_eos`、`min_tokens`、`max_tokens`、n、temperature/top_p/top_k/min_p/seed、penalties、logprobs与structured_outputs。版本不存在的字段记为absent，不自行填默认值。保存engine调用的reasoning/parser kwargs、parser类和初始reasoning状态、prompt IDs及hash；不采集认证头。

在 `IncrementalDetokenizer.from_new_request` 之后记录实际decoder类、tokenizer类、`tokenizers.decoders.DecodeStream` 所属模块/实现来源及相关开关。只记录CachedTokenizersBackend不足以判断fast/slow实际分支。若generate层参数与EngineCoreRequest层不同，保留两份并解释转换，以decoder实际接收的那份驱动G0-P。

避免为了取profile先冒险执行未验证的Unicode切换：先在目标环境用同一部署代码、请求配置和确定性engine test double驱动状态，捕获各child构造结果，标记 `profile_source=runtime_constructed`，不称为线上实测；已有真实native/observation可标为 `live_captured`。G0-P/I通过后，在G0-S补齐真实child快照并核对hash/允许变化字段。不能手工拼一份“看起来等价”的child参数冒充生产构造结果。

首轮明确保留两类候选解释：①旧脚本参数/fixture不代表生产；②真实decoder无法安全重启。此时不更改任何生产skip_special_tokens或stop设置。

### 6.3 第二步：最小变量对照，先隔离skip_special_tokens

保留原脚本四个fixture、所有切点、pending=0/1/4、forced_close两种后缀，并保留旧输出。主硬门是无string-stop；`NEVER_MATCH_THIS_STOP`只作历史buffer行为对照，不解除生产string-stop能力门禁。

| 组 | 参数与输入 | 用途 |
| --- | --- | --- |
| H0历史复现 | 原脚本原参数，skip=False、短prompt、原四fixture | 在冻结环境重新确认60个旧失败是否仍出现；环境变化时不强行要求相同失败数 |
| H1单字段对照 | 与H0完全相同，只改skip=True | 隔离skip开关对旧现象的因果影响；还不能称完整生产验证 |
| P生产profile | 实际native/observation/continuation有效参数、对应原始prompt IDs | 验证真实参数组合；profile之间不同字段逐项解释，不混进H1结论 |
| P-override（若开放） | 当前接口允许的显式skip=False等解码覆盖 | 默认True通过不等于覆盖请求也安全；若该形状仍允许介入，就仍属于待验收支持路径 |

先看sample1排空点5/7/8/10/12，再跑完整矩阵。每组统计各fixture、stop形状、pending及后缀类型的tested/failed/skipped；**不能丢弃失败切点、把string-stop对照数混入生产通过率，或只检查日志中是否还有 `<|im_start|>`。**

原脚本唯一现有输入参数是 `--tokenizer`。原样复现命令如下（目标环境执行，tokenizer路径以部署manifest为准；输出目录预先建立）：

```bash
python tools/validate_dtb_intervention_detokenizer.py \
  --tokenizer /opt/w00618058/Qwen3.6-27B \
  > docs/log/perf/agent_minimum_intervention/run_server_03/g0_production/legacy_stdout.log \
  2> docs/log/perf/agent_minimum_intervention/run_server_03/g0_production/legacy_stderr.log
```

执行器必须保存实际退出码；旧脚本发现任何不一致就返回1，这是实验失败结果，不是执行器异常。不通过管道丢失退出码，也不只保存summary而丢弃失败记录。

**待实现的最小测试入口扩展**（本次仅写方案，不提供假装可执行的新增CLI）：

- 保留 `--tokenizer` 的历史默认行为；新增profile输入、fixture输入、输出目录与显式矩阵选择能力。skip=True/False必须可分别运行，不能只把源码False改True覆盖历史对照。
- profile输入按6.2驱动真实EngineCoreRequest/decoder；解码参数不缺省覆盖，生产StopChecker路径不能偷偷绕开。固定token序列的测试不验证temperature/seed对应的采样过程，仍保存这些字段以审计身份。
- 接受原始 `prompt_token_ids`，可选择历史短prompt或真实prompt，不能decode再encode prompt。新增无模型单元测试验证profile装载、无未知字段静默忽略、布尔值/输出kind一致及矩阵计数。
- 输出结构化JSONL到独立文件，stdout/stderr另存；每个失败保留逐步证据，完成后写机器可读汇总及非零退出码。本文所称生产参数矩阵必须等扩展实现后才可执行。

### 6.4 第三步：固定token对照契约与真实fixture

定义P为原始prompt IDs，G为触发时已接纳token，R为取消后排空接纳token，H=G+R，S为固定后缀。同一个fixture的各对照共享完全一致的P/H/S IDs及解码profile，不重新调用模型，不用重新编码的可见reasoning文本冒充真实生成token。

对不注入cue且无跨child参数变化的continuation，执行三条路径：

1. **连续基准**：真实decoder以P初始化，依次消费H+S，得到对外增量文本和最终flush。
2. **abort-drain重启**：真实OutputProcessor/collector消费G并制造0/1/4个待消费token，显式abort，接纳整个R及abort terminal载荷；新decoder以P+H初始化，消费同一S。只隐藏内部abort终止语义，不丢其text或IDs。
3. **直接decoder重启**：不经过collector合并，直接比较旧decoder已输出H的文本与新decoder(P+H)消费S的文本。若此项也失败，说明问题不依赖collector；若只有第二项失败，则继续核对abort flush/聚合与前缀归属。

硬断言：H严格等于完整已接纳IDs且不重复；continuation的可见逻辑IDs等于H+S；连续文本等于重启后的组合文本（均采用相同合法终止/flush规则）；未重发P/H，未丢未完成字符，source请求表清空。输入包含literal结构标记时以连续参考为准，不通过字符串黑名单误杀合法引用。

固定注入不得在已发生EOS/token-stop/length之后继续硬喂token：测试必须遵守真实输出处理的停止位置。legacy fixture的直接decoder字符对照，与生产StopChecker/collector停止路径分别标识，不能把不可能的“stop之后继续生成”算作生产故障。

fixture分层如下：

- **历史切点层**：原四fixture，包括Unicode、emoji、组合字符和普通文本引用；保留旧样例原ID序列及tokenizer hash，tokenizer变化时另外记录重新构建后的fixture，不能假定token ID相同。
- **生产prompt层**：复用run-05短工具请求及13条历史reasoning>512请求的完整request_body，走实际serving模板得到P，而不是再次套chat模板到已tokenize的P。body由correlation的source_file/source_line精确提取。老proxy日志没有原始生成IDs时标为 `synthetic_suffix_on_real_prompt`；不得decode/encode响应后称其为原始生成轨迹。
- **真实Qwen工具层**：补充合法 `<tool_call><function=...><parameter=...>` 形状、中文文件路径/代码值、多个工具调用。原G0第四fixture的JSON风格tool示意仅保留作历史detokenizer对照，不把它作为生产Qwen XML工具协议的成功oracle。
- **生产位置层**：在合法固定前缀上安排阈值附近T−1/T/T+1及Unicode未完成位置，T首先采用850；记录实际token位置，不能把字符数当token数。加入完整delta跨阈值、空文本非空IDs和collector合并；`stream_interval`始终为1。
- **终态层**：无boundary时续写；R含think-end+正文、R含tool前缀；自然stop/length先完成时不重启。可控状态fixture标为模拟竞态，不声称CPU-only验证了scheduler时间竞态。

对真实DTB的probe/cue/final，**不能直接套用“自然连续输出=DTB全文”的断言**，因为prompt策略有意改变：

- probe使用部署代码生成的P+H+TRIAL_PROMPT及有效参数；验证prompt不回显、隐藏输出不进入外层流，不能用旧脚本的 `</think>\nanswer` 代替真实trial路径。
- iterative-think仍从P+H继续，验证stop-token与boundary交付，不能把已交付H重新计数。
- forced-final使用实际cue、forced-close IDs与separator；cue是prompt注入，不应回显，外层只出现协议规定的一次boundary。固定正文/tool后缀使用独立已知期望验证，不把cue的合法差异判成decoder失败。
- R已有自然boundary时直接final，不probe、不追加cue/额外close；保留已接纳的全部正文/tool前缀。所有这些分支最终进入G0-I判定结构与参数影响。

### 6.5 第四步：G0-I验证实际parser与端到端正确性影响

在目标部署Python中，以固定RequestOutput序列驱动实际DTB adapter、reasoning/组合parser、tool parser和serving结束处理；不加载权重、不执行模型采样。复用已有测试逻辑，但本地stub类型的通过只算开发回归，真实部署依赖下必须再跑。

对每个支持fixture保存：初始parser状态、request/tool schema、实际接收的text/IDs增量、reasoning/content、工具name/arguments、finish_reason及terminal次数。与同一固定序列的连续参考或6.4规定的forced-final期望比较：

| 观测 | 判定 |
| --- | --- |
| 仅旧False配置失败，生产有效profile全部通过 | 支持参数差异解释；修正测试覆盖和结论，不直接修改生产参数。仍检查显式False请求是否属于开放支持范围 |
| 真实profile文本重复/缺字，但暂未改变工具结果 | G0保真失败；记录显示层影响，不能说已导致Agent失败，也不能宣布安全门禁通过 |
| boundary错误分流、正文污染、工具名或参数变化、内部abort外泄、terminal重复/丢失 | 已证明对实际协议/可执行动作有影响；支持路径阻断，修复后重测 |
| 固定流工具与正文均正确，但真实任务仍失败 | 转任务轨迹/质量分析，不把失败自动归因G0 |
| H1通过但真实profile/实际child快照缺失 | 仅完成skip开关局部定位，生产结论仍是证据不足 |

`skip_special_tokens=True`下某些marker text为空、对应ID仍存在可能是原生行为，必须由实际parser协议判断；不以“文本里看不到marker”为通过标准。不需要为了证明风险让真实Agent先执行损坏的工具调用：固定流已经改变可执行参数，就足以阻止发布；实际线上发生频率另行测量。

### 6.6 第五步：归因后的修复选择与真实服务smoke

修复按证据层定位，不提前选定实现：

1. **测试构造错误/生产配置不一致**：修正profile、prompt模板或fixture，保留历史负例；不修改生产decoder来迎合错误测试。
2. **DTB child传参错误**：在相应adapter/child构造处修复，验证native与observation参数一致、后续差异均为策略明确要求；不把全局强制skip=True作为通用修复。
3. **真实decoder前缀恢复缺陷**：在实际decoder/依赖适配层恢复正确消费状态，或采用已验证的依赖修复；先固定同P/H/S的回归，再验证parser链。fast/slow或版本对照只用于定位，不未经验证直接切换部署。
4. **无法立即修复的切点**：只有能在abort之前可靠识别、保持原生继续且开销可控时，才考虑明确限制支持范围；不能取消后才发现不安全，也不能删字符串前缀、丢空delta、过滤replacement字符或重新tokenize H补洞。任意切点安全检测本身需要独立证明。

G0-P/I通过后，再在隔离服务以生产参数对少量真实长请求做G0-S，记录真实child有效参数并与profile比对；动态max_tokens/parent ID等允许变化字段明确白名单。要求确认实际发生切换，而不是只跑完旁路；需要获取实际生成IDs，`--enable-log-outputs`完整文本不足以替代。发现与profile不一致立即回到G0-P，不把该请求当通过样本。

在此smoke中检查工具参数、terminal、请求回收与completion额度，并保留失败现场；自然结束抢先导致未介入的样本照常记录，但不充当切换覆盖。该阶段不做正式E2E收益排名，也不直接执行9场景全量评测。通过后才启动2–3个真实任务可用性试跑。

### 6.7 交付与通过门槛

新增实验产物建议为：

| 文件 | 内容 |
| --- | --- |
| manifest.json / profiles.json | 环境身份、每种child有效参数与来源、脚本/依赖hash、执行命令和退出码 |
| fixtures.jsonl / prompts/ | fixture ID、来源请求/工具schema、P/H/S或实际cue后缀IDs、边界与切点、数据类型real/synthetic |
| decoder_results.jsonl / failures/ | 每对照分支的文本/IDs hash、首个差异位置、切点附近token与Unicode转义文本、完整失败P/H/S引用、collector回收断言 |
| parser_results.jsonl | 实际parser输入与输出对照、工具参数/结构/终态判定，显示差异与可执行协议损坏分开 |
| summary.json | 按profile/fixture/stop/pending/后缀分组的tested/failed/skipped、未覆盖分支、执行退出码 |
| RESULT.md | 生产参数是否复现、首个差异层、根因证据、修复或支持范围决定、能否进入真实任务试跑 |

统一UTF-8，失败上下文中的代码/工具输入按内部数据保护；日志不覆盖、不静默截断，保留可复算原始IDs。Windows执行器若启动子进程，无需输入时显式设置 `stdin=subprocess.DEVNULL`，不以PTY绕过句柄问题。

**进入真实任务试跑的最低门槛：**实际支持profile完整；无string-stop生产矩阵零保真失败；H/token计数及回收断言全部通过；真实parser正文/工具/terminal正确；smoke真实child参数与已测profile一致。False如果仍是可达且允许介入的请求形状，就不能以默认True通过豁免；未验证的形状须明确不支持并在运行时安全旁路，不能只在文档里排除。

这一门槛只关闭已测生产路径的G0风险，不要求消除所有Vanilla语义偏离，也不证明所有Unicode/未知环境永不出错。上轮空arguments与正式Agent质量/时延仍按各自实验独立裁决。

## 7. 复算与证据边界

新增 `analyze_output_logs.py` 与 `output_evidence.json`；保留两个子run的自动RESULT原文。运行：

```bash
python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_02/analyze_output_logs.py
```

脚本只做离线分析：验证配对集合、源输入/实际请求hash、日志parent覆盖、XML结构计数，以及133次调用的原始参数/SSE/summary相等。XML读取器仅适用于本归档的字符串参数，不是通用XML parser，也没有用本轮输出重新调用模型或执行工具。

本地复算通过；本轮未改parser、DTB运行时或回放期望，未进行服务器实验。未报告压缩率/时延排名，因为本次目标是协议归因且采集日志启用、部署身份不全。

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-08 | 对b832b74两个子run完成原始完整输出/SSE/summary参数对账，区分26个工具选择偏离与上轮空参数故障，澄清log-outputs层级及证据边界 | 用户要求判断实际参数缺失来自模型还是parser；本轮实际没有复现空参数，不能混淆两类告警 |
| 2026-09-08 | 新增第6节生产参数G0详细方案：profile冻结、skip单字段与完整生产矩阵、真实prompt/token切点、DTB各child及parser影响验证、分层修复与服务smoke门槛；原复算节顺延为7 | 用户要求明确真实任务试跑前的G0实验，避免把Vanilla的skip=True直接当作所有child生产配置，或将未实施脚本能力写成现有功能 |
