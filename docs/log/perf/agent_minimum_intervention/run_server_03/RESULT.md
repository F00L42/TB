# run_server_03：010ce 的 G0/G1 结果与端到端准入判断

分析日期：2026-09-08。结果归档：`010ced2c5080a87444eac3ce87b916ca815b530c`。

## 1. 结论：暂不进入启用 DTB 的正式 Agent 端到端评测

**G0 仍未通过；本轮 G1 只有原生观察/旁路的正向证据，没有完成介入切换验收。** 不建议直接启动9场景，也不把2–3个真实任务当成已通过前置门禁的可用性试跑。可以继续隔离排障；不开启DTB的任务可作为原生功能检查，但修复若改变运行时，正式Vanilla基线仍需重新冻结。

主要依据：

1. **实测：skip=True没有消除旧G0问题。** H0与H1均失败60/768，无string-stop子集均失败30/384，失败切点完全相同。可以否定“仅把skip=False改为True就能解决”的解释，不能将它继续当作主修复方向。
2. **实测：15份真实profile均通过身份检查，但重启矩阵各失败70/384。** TokenizerPool初始化修正已解决身份误拦截；现在是真正运行到固定流文本比较后失败，不是同一个启动错误。
3. **实测：固定输入重启会把原始prompt文本带入输出。** `P-native/failures/000001.json`预期32个字符，实际204,094个字符；全部70个失败均为IDs比较通过、文本比较失败，直接decoder重启也失败。此类保真错误不是可忽略的模型动作选择偏离。
4. **实测：HTTP回放15请求、17次工具调用，协议错误0，语义偏离4。** 但15份采集快照只有8次Vanilla和7次DTB `native_observation_1`，没有probe/iterative/final快照。不能用这些成功请求证明取消排空与重启安全。

本报告不宣布已证实线上Agent工具损坏：固定流失败与真实任务发生率是两件事；但现有证据已足够阻止把“任意已排空token前缀可安全重启”视为成立。

## 2. 实验身份与完整性

### 2.1 输入和产物

归档包含：H0/H1/log-derived三个CPU矩阵；`P-native`至`P-native-r15`共15个真实profile矩阵；15份captured profile；`native_capture`、`smoke`、`smoke_more`三个HTTP回放目录。没有上传实际G0-I parser固定流结果、phase账本、服务原始日志或请求级queue/cache metrics。

离线复核已通过：

- 18个矩阵的明细行数、唯一case键、summary分组、失败文件数全部闭合；共7,680行固定流对照，不是7,680次模型请求。
- 15个P矩阵各自嵌入的profile与对应captured文件完全一致；`profile_identity_match=true`且保存的runtime identity与captured identity逐字段相等。
- 15份profile只有6种不同P、1种有效SamplingParams；其中重复的native/observation参数不能当作15种独立配置覆盖。
- prepared_check与仓库prepared的14条输入逐条相等；三个HTTP manifest的输入LF归一化hash一致：`963b7f46bb63fec04a3689bdb59d9f9d2288228cebdd61966c35d1bdef1924e7`。
- 逐请求重建实际发送body并对账request记录hash；重新组装所有SSE，reasoning/content、工具name/arguments、finish_reason、usage及异常分类均与summary一致。15请求全部HTTP 200，成功terminal各1次。
- `smoke`与`smoke_more`复用了`g0-production-smoke` run ID，行5的两种variant各有跨run同名parent。此次按子目录隔离；后续必须换新run ID，不能仅按parent串联合并日志。

### 2.2 已知和缺失的身份

P矩阵记录的环境为：vLLM `0.23.0+empty`、vLLM-Ascend `0.23.0`、transformers `5.5.4`、tokenizers `0.22.2`、fastokens未安装、PyTorch `2.10.0+cpu`、torch-npu `2.10.0.post4`。这些是包元数据，不据`+cpu`推断实际推理使用CPU。

实际decoder为`FastIncrementalDetokenizer`，DecodeStream来自`tokenizers.decoders`，`use_fast_detokenizer=true`；decoder源码SHA256为`52d43b97d5c0f84285df596b2da5f4ee32184b9f41b87c6ef99437225b5720b5`。tokenizer为`TokenizerPoolCachedTokenizersBackend`，backend指纹为`a57b5c62cd5f242a06c632b82cc0b4f8239d08cc80fd9347fc1f629be977c6ca`。

真实参数：n=1、DELTA、temperature=1、top_p=1、top_k=20、seed=null、max_tokens=32000、skip_special_tokens=True、stop=[]、stop_token_ids=[248044]、EOS=248046、include_stop_str_in_output=False。注意decoder侧有效stop IDs不是旧Received日志中可见的空列表；本轮以捕获的EngineCoreRequest为准。

baseline/capture记录TB commit `c56100572719b05a64312405e61e7449a317bb12`，P矩阵记录`961134b1b6333dfaee70d33912674dd6f41c16a0`；这两个对象在当前本地仓库中不能解析，不把归档commit `010ce`等同于部署commit。15个P矩阵的三个实验脚本hash与当前仓库文件一致，支持直接追踪当前实验实现；仍缺vLLM/Ascend完整commit/dirty、三个patch/YAML hash、模型修订、硬件/CANN、服务启动参数、observability mode及cache状态，不能据此进行正式时延归因。

## 3. G0：生产参数对齐后仍有确定性文本保真失败

| 组 | 通过 / 总数 | 失败 | 无string-stop失败 | 裁决 |
| --- | ---: | ---: | ---: | --- |
| H0：历史skip=False | 708 / 768 | 60 | 30 / 384 | 复现旧问题 |
| H1：仅改skip=True | 708 / 768 | 60 | 30 / 384 | 失败切点与H0完全一致 |
| log-derived | 314 / 384 | 70 | 70 / 384 | 日志参数预检查未通过 |
| P-native…P-native-r15，每份 | 314 / 384 | 70 | 70 / 384 | 实际native/observation profile未通过 |

15个P矩阵合计1,050/5,760失败，但它们使用同一固定fixture，且失败切点签名一致；**不能把18.23%当作线上Agent请求故障率，也不能把重复profile当作独立随机样本。** H0/H1全部失败集中在Unicode fixture，不是所有文本都失败。

每份P矩阵的失败分布相同：

| fixture | 失败 / 总数 | 含义 |
| --- | ---: | --- |
| unicode-on-captured-prompt | 30 / 150 | 真实P上合成Unicode序列的多个重启切点失败 |
| unicode-near-850 | 22 / 54 | 阈值附近同类失败，不限于极短前缀 |
| qwen-xml-unicode | 18 / 180 | 工具XML文本内强制重启可使参数文本污染；不直接等于observer可达路径 |

全部失败均为`text_equal=false`、`direct_decoder_equal=false`、`token_ids_equal=true`；P矩阵所有失败的resumed逐token记录中均含prompt的`system\n# Tools`回显。纯original后缀也失败44/282，故不能把所有故障归因于人工`forced_close`改变后缀。

### 3.1 最小例子：不是模型重新采样造成

`g0_production/P-native/failures/000001.json`：trigger=1、pending=4、restart=5，接纳H的最后ID为59720。预期：

```text
分析中文与 emoji 👩🏽‍💻🙂；组合字符 é 与换行
结束。
```

实际开头：

```text
分析中文与 emojisystem
# Tools

You have access to the following functions:
...
```

首差在字符11；预期32字符，实际204,094字符。P为真实58,626个prompt IDs，不是把解码文本再次encode构造。工具说明等prompt内容进入重启输出；本报告不再复制该大段内部prompt，完整证据保留原失败文件。

H1相同fixture仍出现`分析中文与 emojiassistant\n<think>\n分析中文与 emoji ...`。skip=True可以影响某些特殊token的显示，却不能保证消除普通角色名称、模板文本或prompt回显。

### 3.2 阈值附近与真实介入条件要分开看

`P-native/failures/000039.json`：trigger=853、pending=0、restart=853，original后缀；前缀尚未含自然reasoning boundary，直接decoder也失败。T附近original子集中，排除trigger=849以后仍有10/24失败。

但**当前fixture恰好trigger=850时，pending=0/1/4三条original对照均通过**。因此不能写成“已实测每次T=850停止都失败”。这只能说明固定文本在某些token对齐位置安全、另一些不安全；当前机制接受完整delta/完整排空前缀，没有保证实际重启永远位于这三个安全位置。stream_interval=1也不等于已经证明任何完整delta都落在可重启Unicode状态。

下一步应把同类Unicode切口平移到恰好850的位置、测试完整delta跨T及排空overshoot；保持stream_interval=1，不靠改阈值避开这一份fixture的几个坏位置。

### 3.3 工具XML例子的影响与可达性限制

`P-native/failures/000053.json`：trigger=35、pending=4、restart=39，首差位于字符117，即`write`工具`content`参数中的emoji位置：

```text
参考：<parameter=content>\nprint("👩🏽‍💻🙂")...
实际：<parameter=content>\nprint("system\n# Tools\n...
```

resumed首个非空文本delta长204,170字符。固定文本层面已经不是单纯去掉显示标记，而是潜在可执行代码参数发生变化。

不过该fixture的`</think>`早于强制切点。当前`deferred_observation.py`看到boundary会disarm，不会在普通正文中继续按T触发；**不能把这18例直接计为生产observer新增工具故障**。要验证真实影响，应构造“T前无boundary→abort排空带入boundary和工具前缀→final续写”的实际适配路径，再检查真实parser输出。当前只有decoder文本证据，没有这条链的完整实测。

### 3.4 根因定位程度

- **实测排除：**不是仅由skip=False、历史短prompt、string-stop造成；native/observation生产profile同样复现。
- **实测定位：**错误出现在decoder续写文本，直接decoder重启也失败，所以collector合并不是必要条件；固定流没有调用模型或parser，不能归因模型随机性或parser创造了这段回显。
- **源码确定：**G0 Runtime以P+H初始化新decoder；当前vLLM FastIncrementalDetokenizer把该前缀传给`DecodeStream(ids=...)`，再对S逐token执行step。保留IDs与恢复增量解码状态不是同一个条件。
- **待验证假设：**Unicode前缀状态在DecodeStream预填充/首次续写/错误恢复中没有正确恢复，导致prompt内容再次变为可输出文本。尚未取得目标tokenizers底层实现调用栈或状态dump，不能据此指定确切Rust函数缺陷，也不能声称已完成修复。

## 4. G1/HTTP：零协议错，但没有关闭切换风险

| 子run | V / D请求 | 协议失败V / D | 语义偏离V / D | 说明 |
| --- | ---: | ---: | ---: | --- |
| native_capture | 1 / 0 | 0 / 0 | 0 / 0 | 仅采native profile |
| smoke | 1 / 1 | 0 / 0 | 1 / 1 | 行5预期read，两侧实际bash |
| smoke_more | 6 / 6 | 0 / 0 | 1 / 1 | V行14实际read而非bash；D行12实际bash而非edit |

15请求共17次工具调用，SSE重组后JSON/schema审计通过，terminal各一次。4个告警都是`expected_tool_name_missing`；并非空arguments、无效JSON或工具XML泄漏。HTTP回放不执行工具和后续任务，不能据动作不同判定Agent最终错误，更不能据协议通过认定代码质量合格。

15份captured中，8次原生、7次DTB观察；**没有confidence_probe、iterative_think、final_answer**。每个已采DTB observation的P/有效SamplingParams与对应native一致，这是“不额外改动短路径输入参数”的正向证据；缺phase与原始server日志，不完整裁决全部请求的disarm原因或telemetry账本。

7个DTB响应completion分别为174、61、178、256、208、935、456。935包含工具参数，不能视为reasoning已超过850；历史输入曾有长思考也不保证本次重采样仍长。此次采集结果没有支持实际abort→probe/final切换的证据，更没有排空含boundary、自然结束抢先等分支证据。

自动RESULT显示`Protocol-clean pairs: 0`，并不表示出现了协议错误：本输入全部`latency_eligible=false`，回放汇总在时延候选筛选后计算该计数。原始请求层面是15/15协议通过，不修改原报告掩盖其口径。

## 5. 本轮较快，但不能归因DTB压缩或Agent端到端收益

`smoke_more`的6对请求实测：Vanilla总LLM E2E约73.966s，DTB约26.405s，观察差值约64.30%；响应completion为3372→2094。此处仅描述观测，不参加正式性能排名，也不把completion减少称为含hidden probe的总工作量压缩。

不能用于收益验收的原因：

1. 无已证实的probe/压缩切换，不能解释为“DTB压缩成功”。
2. first-output累计约35.138s→4.385s，该部分差异约占总E2E差值64.66%。P很长（58,626–126,478 tokens），重复输入、到达顺序、prefill/cache/排队是需要metrics证伪的候选因素；没有metrics时不直接认定cache命中为根因。
3. 只重复一次；seed=null，模型实际轨迹和输出长度不同。variant虽声明alternating，这不是完整的顺序/热缓存控制证明。
4. 开启profile采集有额外I/O，不是正式off性能环境；输入主动排除时延排名。
5. 没有LightCode工具执行、编译、DT与报告阶段。单轮LLM E2E与Agent task wall不是同一个指标。

因此，本轮不能得出Agent正确率或端到端加速率；也不能因观测变快而豁免G0保真失败。

## 6. 下一步：先关闭有明确反例的门，再做真实任务

### P0：停止重复同一矩阵扩样，定位decoder恢复

优先使用已有`P-native`的000001、000039、000053固定证据。连续、直接重启、collector重启三路已经足够形成定位起点，不再用新随机采样解释旧固定流失败。

- 在相同目标Python/tokenizer中，补最小DecodeStream级复现：相同P/H/S、初始化后首个step文本、切点附近token IDs/UTF-8字节和异常/恢复路径。不得decode再encode真实P/H。
- 将Unicode未完成前缀平移到T=850，覆盖原生original后缀及真实trial/cue后缀；真实cue分支不能套用自然连续全文完全相等的错误oracle。
- 修复应恢复正确的增量解码状态/前缀抑制，或验证目标依赖修复。不要删掉回显字符串、丢弃空delta、仅改skip或只改T避开个别切点。
- 本报告仅分析，不修改DTB/parser/tokenizers运行时。服务端源码/依赖身份先补齐，便于固定版本回归。

### P1：修复后的最小验收

1. 保留H0/H1历史对照；无string-stop生产矩阵与恰T=850补充切点零保真失败，H/输出IDs及请求回收断言通过。
2. 运行真实DTB适配＋reasoning/tool parser＋serving固定流，至少覆盖无boundary切换、排空带think-end/正文、排空带tool前缀、自然terminal抢先；工具名/参数、boundary及terminal必须正确。XML fixture的不可达强制切点不替代可达路径验收。
3. 隔离服务小样本G1记录phase账本与实际child参数，确认至少有真实triggered及后续child；T=850无触发时保留旁路结论，另作明确标记的定向分支实验，不把诊断阈值结果当生产参数收益。
4. 新run ID、独立目录；补齐服务器身份和请求级原始流/phase证据，不再依赖目录名或历史reasoning长度判断覆盖。

### P2：满足上面门槛后，再启动端到端

先2–3个真实任务做单并发可用性试跑，再扩到9场景；每个任务下载主/子agent session并与proxy逐请求关联。要求报告完整、无新增协议hard failure、编译/DT质量不退化。正式收益使用独立`OBSERVABILITY.mode=off`、关闭profile采集的同身份V/D实验，至少两轮相反variant顺序，同时比较task start/end wall和LLM E2E。质量/可用性通过后才讨论抓大放小参数收益。

## 7. 复算与边界

```bash
python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_03/analyze_results.py
```

生成`analysis_evidence.json`：18个矩阵统计、首个失败索引/截取、15个profile身份、15个HTTP请求及7对观测、重复parent提示。脚本只做离线对账，不调用模型、不执行工具；复用了现有SSE/schema审计器，不能将其视为一次独立原生parser版本回归。完整断言已在本地通过；未执行新服务器实验。

## 8. G0问题交接与一条长轨迹的G1定向实验

### 8.1 G0交接材料

[G0_WORK_HANDOFF.md](G0_WORK_HANDOFF.md)包含可直接复制的一段问题描述、P/H/S测试逻辑和建议附带的源码/证据文件。重点请诊断方同时审计“输入前缀/decoder恢复”与“测试方法/abort flush等价假设”，不预设一定是tokenizers缺陷。先提供短合成P的H1失败，不必上传含内部业务prompt的巨大P-native文件；本次未替用户向外部服务发送材料。

### 8.2 选定输入：同一请求体在两轮Vanilla均产生长思考

不再首选本轮已回放但没有介入证据的行5。选择已有输入包第**2行**：

`docs/log/perf/agent_minimum_intervention/input/g0/prepared/requests.jsonl`

| 证据 | Vanilla run-04 | Vanilla run-05（选定输入） |
| --- | --- | --- |
| request_id | `22fde80b63424547` | `236bb99275fd4fde` |
| source_file | `full_log/proxy_session_ad2b94ab060c/part-00001.jsonl`，第3行 | `full_log/proxy_session_b61034e15998/part-00001.jsonl`，第5行 |
| prompt tokens | 14,037 | 14,037 |
| reasoning_tokens_local | 1,792 | 2,865 |
| 角色 | 标题生成辅助请求 | 标题生成辅助请求 |

两个source_file的完整前缀分别是`proxy/results/qwen/local-task29/local-apiserver/vanilla/run-04/`和`.../run-05/`。本地逐字段核对两个原始request_body**完全相等**，规范化JSON SHA256均为`23836db5b9d2a9d7bba03201d22f41eb46213b4a3170098cec729ee30d8de6ec`。因此不是仅凭相似标题选择候选，也不是把不同上下文当作配对。

选择理由：两轮相同输入均超过850；P明显短于上轮58K–126K工具上下文，历史body无tools、tool_choice、stop、min_tokens、penalty覆盖，减少无关工具/上下文容量因素。max_tokens=32000、top_p=1，temperature/seed未在原body指定，实验保留这些参数，不注入额外“请多思考”文本。

**证据边界：**这是一条真实Agent辅助请求，但不是工具执行请求。它用于检验`observation→abort/drain→confidence_probe→final`基础链，不能独立证明tool parser或完整Agent任务可用。历史长轨迹并不保证重采样仍长，更不能保证排空时boundary还未出现；本机无模型，尚不能声称这条输入本次已触发probe。最终必须以服务端phase为准。

### 8.3 隔离服务准备

用户已同意在定位G0的同时做定向G1诊断。此实验**可在G0尚未修复时隔离执行，但不能据成功结果豁免G0或放行端到端任务**；回放客户端不执行模型生成的工具，不把它接到真实Agent执行器。

1. 使用当前冻结模型/DTB/三个parser-serving patch，保存完整部署manifest。只在获准实验实例修改实际加载的`dtb/config/budget_config.yaml`中已有的`OBSERVABILITY.mode`为`phase`并重启；不是在HTTP body设置mode，也不要用下面片段覆盖整个YAML。

   ```yaml
   OBSERVABILITY:
     mode: "phase"
   ```

2. 本轮不必使用`capture-server`，也无需开启`stream_audit`；保存完整`DTB_PHASE` **以及** `DTB_REQUEST_SUMMARY`。上轮仅过滤PHASE导致summary缺失的记录不能完成本次判定。日志不可截断JSON，不重复拼接同一分片。
3. 单并发、stream_interval=1。保持原生产上下文上限及MTP设置，不人为提高max_tokens/上下文或加入用户stop。确认服务的实际请求参数没有令`switching_rejection()`命中不支持路径。
4. 下列命令在服务器仓库根目录运行，URL/served-model-name按已部署实例替换；输出目录必须不存在。需要认证时沿用`OPENAI_API_KEY`，不要写进归档。

### 8.4 第一轮：原输入、生产T=850，4次配对

```bash
python -X utf8 tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_minimum_intervention/input/g0/prepared/requests.jsonl \
  --lines 2 --mode both --order alternating --repetitions 4 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model qwen3.6-27b \
  --fixed-budget-tokens 20000 --fixed-check-length 850 \
  --fixed-think-stride 500 --fixed-num-thinking 5 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.93 \
  --request-id-run g1-title-long-t850-r1 \
  --output-dir docs/log/perf/agent_minimum_intervention/run_server_04/g1_probe_t850
```

加`--list-request-ids`可先只打印8个parent ID，不发送请求。DTB parent形如`chatcmpl-dtbtool-l2-a1-dtb-g1-title-long-t850-r1`，attempt为1–4。4次的先后为V/D、D/V、V/D、D/V；不设seed覆盖，保留源请求的采样语义。本输入`latency_eligible=false`，不因观测变快就改为性能排名。

服务端日志保存完成后执行只读检查（`/actual/path/server.log`必须替换）：

```bash
python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_03/check_probe.py \
  docs/log/perf/agent_minimum_intervention/run_server_04/g1_probe_t850 \
  /actual/path/server.log
```

若回传分片，命令末尾按顺序列出`server_filtered_part_01 server_filtered_part_02 ...`；每个分片需保留两种事件。检查器根据客户端summary的parent白名单隔离，不需要把其他请求混入结论。它只读日志并输出JSON，不修改原始结果。

### 8.5 真正触发probe的判据与停止规则

不是“HTTP 200”“completion>850”或“有native_observation child”就算成功。至少一个**同一parent**应同时具备：

- 完整且唯一的`DTB_REQUEST_SUMMARY`：`intervention_status=triggered`、`post_drain_branch=confidence_probe`、`abort_count=1`、`abort_terminal_seen=true`、`abort_lost_to_native_terminal=false`。
- `DTB_PHASE`中一次`native_passthrough`（这是phase名；child后缀是`native_observation_1`），至少一次`confidence_probe`，并有正常`final_answer`。
- hidden probe generated tokens>0，phase合计与summary的`hidden_probe_tokens`一致；请求`completion_status=success`、无error、最终terminal一次。
- 客户端没有协议异常、空响应、内部abort外泄或终态重复。人工检查最终标题与reasoning无prompt回显/重复，隐藏试答没有出现在外层流；标题任务的正常无工具输出不是“缺少工具调用”。

`check_probe.py`退出0表示至少一条probe链的上述基础phase/终态检查通过且本run没有已检测的协议/账本阻断；退出3表示没有取得probe覆盖；退出1表示协议失败、缺失/重复summary或链路不一致。它输出固定`full_g1_accepted=false`，因为还未检查所有parser-facing内容和其他竞态分支；不能把退出0当完整G1验收。

若出现协议错误或prompt回显，停止扩大实验并保留该parent全量现场，转回G0/固定流定位。若只因`disarmed_boundary`/自然结束抢先而没有probe，不判机制失败，也不记通过。如果是`disarmed_no_capacity`、不支持形状或缺telemetry，先解决该原因，不盲目调低T。

### 8.6 有条件的低阈值诊断：同一输入，T=128

只有第一轮完整记录显示“正常生成但均在850前结束思考/自然结束抢先”，才执行以下**两次**配对。目的是更早进入尚未结束的思考，提高取得probe链证据的概率，不是参数寻优、不替换生产T=850，也不保证必触发；仍未触发就回传而非无限加样。除了T、重复次数和独立ID/目录，其余参数相同：

```bash
python -X utf8 tools/dtb/replay_tool_calls.py \
  --file docs/log/perf/agent_minimum_intervention/input/g0/prepared/requests.jsonl \
  --lines 2 --mode both --order alternating --repetitions 2 --stream \
  --url http://127.0.0.1:9001/v1/chat/completions --model qwen3.6-27b \
  --fixed-budget-tokens 20000 --fixed-check-length 128 \
  --fixed-think-stride 500 --fixed-num-thinking 5 \
  --fixed-min-think-tokens 1 --fixed-prob-th 0.93 \
  --request-id-run g1-title-long-t128-diag-r1 \
  --output-dir docs/log/perf/agent_minimum_intervention/run_server_04/g1_probe_t128_diag

python -X utf8 docs/log/perf/agent_minimum_intervention/run_server_03/check_probe.py \
  docs/log/perf/agent_minimum_intervention/run_server_04/g1_probe_t128_diag \
  /actual/path/server.log
```

回传两轮各自的manifest/summary/chunks/自动RESULT、phase及REQUEST_SUMMARY日志、检查器输出、部署manifest；未执行第二轮就明确标注未执行。对取得的probe分别说明接受/拒绝、后续child、排空boundary情况和隐藏token；标题样本通过后仍需工具分支固定流/真实请求覆盖，之后才考虑端到端。

本次本地已核对两轮Vanilla输入完全相等并验证命令可加载第2行；检查器以无模型fixture验证有probe、仅旁路、缺/重复summary、token账本不一致、协议失败和语义偏离的判定，真实服务器回放待执行。

## 9. Work“重启测试诊断”复核：尚不能解除端到端阻断

复核日期：2026-09-09；外部诊断：[重启测试诊断](chatgpt-conversation://6aa0b730-9250-83ea-9597-6c75a6c19314)。本节不新增服务器实测，不修改运行时，也不把外部报告中的实验声明当成本地已执行结果。

### 9.1 新诊断改变了什么

**结论仍为暂缓启用DTB的正式Agent端到端评测，但根因定位已收敛，下一步无需继续盲目扩展同一矩阵。** Work报告在tokenizers 0.22.2的ByteLevel/ByteFallback小词表上复现了native-prefill前缀回显，并报告“从原P初始化、逐步重放H再接S”可以消除其最小例子的回显；它没有运行目标Qwen tokenizer、完整矩阵或真实Ascend取消链，因此这是候选修复方向，不是生产修复验收。

本地重新核对附件H1 `000001.json`：H最后token为59720，S第一个token为102；旧流含abort输出合计是`分析中文与 emoji`，新流的第一个delta却是`assistant\n<think>\n分析中文与 emoji 👩`。连续流期望新增的只是` 👩`；collector拼接结果与直接decoder重启结果相同，且token IDs守恒。因此该反例发生在**固定token序列的文本解码层**，不是模型重新采样产生了错误，也不是tool parser才引入了角色标记；但尚不能据此排除parser的其他独立缺陷。

**源码确定：**[Python DecodeStream构造器](https://github.com/huggingface/tokenizers/blob/v0.22.2/bindings/python/src/decoders.rs)保存预填IDs，但prefix初始为空；[Rust step_decode_stream](https://github.com/huggingface/tokenizers/blob/v0.22.2/tokenizers/src/tokenizer/mod.rs)仅在预填解码结果不以U+FFFD结尾时建立prefix。未完成字符令prefix保持空，后续token使文本完整后，就可能返回包括预填在内的全文；空prefix也不会触发InvalidPrefix异常。这与本轮固定流首delta回显一致。因网页渲染行号存在差异，以函数名定位。

当前vLLM `FastIncrementalDetokenizer.__init__()`仍将`request.prompt_token_ids`传给`DecodeStream(ids=...)`（`third_party/vllm/vllm/v1/engine/detokenizer.py`）；DTB在`deferred_observation.py`接纳排空H后执行`context.append_prompt_token_ids(prefix)`，后续状态新建generate子请求。**完整delta/完整token IDs，不等于新的增量decoder恢复了旧流的未完成字符状态。** 这并不否定P+H作为模型续写输入的合法性，但否定了仅凭IDs完整就宣称parser-facing文本安全的充分性。

### 9.2 测试方法的限制不等于当前反例无效

- `AsyncLLM.abort()`先在OutputProcessor回收状态并投递本地abort终态，再通知EngineCore；这里H应定义为**前端最终接纳的前缀**，不是设备已经计算出的全部token。旧状态移除后的迟到输出不属于续写前缀。现有pending用例不能替代真实调度/批输出竞态覆盖。
- `get_next_output_text(finished=True, delta=True)`释放文本stop缓冲，并非对DecodeStream执行Unicode finalization。生产profile的`stop=[]`也不能保证P+H尾部字符完整。
- 跨切点实际命中string stop、强制close/cue改变后缀等情况需要独立oracle；不能全部要求与原始自然续写全文相等。但此处`original`后缀、无string-stop的失败仍存在，不能用这些测试边界将它注销。
- G0暴露的是确定性保真反例，不是线上发生率。工具XML fixture中思考已结束后的强制切点不直接代表observer可达路径；仍需验证真实abort前未闭合、排空后含boundary的分支。

### 9.3 端到端准入与最短后续路径

1. **先用目标环境已有P/H/S做三路对照**：旧decoder继续；新decoder原生预填P+H；新decoder从P初始化并重放H（抑制重放输出）再接S。记录P+H解码尾部、首个重启delta、完整text/IDs与stop结果。先测现成失败000001与阈值附近000039，再回归生产矩阵；不需要模型重采样即可确认该修复方向是否适用于目标tokenizer。
2. **修复必须落到实际输出链，而非仅改测试。** 候选是保留/恢复逻辑输出decoder状态，或正确重放H恢复状态；不能简单截掉回显文本、删除U+FFFD、改skip或移动阈值规避单个切点。probe/cue会改变逻辑后缀，需分别处理decoder状态、已发送offset和隐藏输出，不能把同一可变decoder不加区分地共用给所有子请求。重放的CPU成本应只在真实介入后发生，并计入telemetry。
3. **补真实OutputProcessor与DTB/parser固定流回归**：通过`process_outputs`输入实际批次形状，验证abort回收、迟到输出、自然terminal抢先、排空含think-end/tool前缀；真实probe/cue/final链要求无prompt回显、无hidden输出泄漏、工具参数和终态正确。无string-stop生产域先收口，不把当前原生拒识的任意string-stop跨界问题扩成新增准入范围。
4. **第8节隔离G1可以继续执行。** 但当前仓库尚无新的实际probe验收结果；即使标题长轨迹成功，也只能补充基础切换证据，不能抵消已知G0反例或替代工具链验收。
5. 上述保真修复及可达分支验证通过后，再进行2–3个单并发真实任务可用性试跑，之后扩9场景。正式时延实验另用同身份、关闭profile采集的`OBSERVABILITY.mode=off`配对结果。当前可继续Vanilla功能检查及不执行工具的隔离诊断；不建议直接开启完整DTB评测来寻找一个可能低频、但已有固定反例的基础缺陷。

本次仅完成外部报告、失败附件与源码复核；没有运行目标模型、没有部署修复，也没有新增G0/G1通过结论。

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-09-08 | 对010ce完成G0矩阵/profile/SSE归属复算，识别生产skip=True下prompt回显、旁路与切换覆盖差异；给出暂缓正式Agent评测及分层关闭门禁的建议 | 用户要求判断是否可开始端到端测试，避免以身份修复或零HTTP协议错误替代重启可用性验收 |
| 2026-09-08 | 补充G0外部诊断材料；选择两轮Vanilla同请求体1792/2865-token长思考，提供T850与有条件T128隔离G1命令、phase检查器和覆盖边界 | 用户要求并行调查测试方法/重启根因并获得真实probe可用性证据；不把历史长思考或诊断成功当完整端到端准入 |
| 2026-09-09 | 复核Work的DecodeStream native-prefill诊断，区分前端接纳前缀、decoder状态与测试oracle，补充三路目标tokenizer对照及端到端准入判断 | 外部诊断收敛了根因，但尚未提供生产修复及真实介入链验收，不能据此解除G0阻断 |
