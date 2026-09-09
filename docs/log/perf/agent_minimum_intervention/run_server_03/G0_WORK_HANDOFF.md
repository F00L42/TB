# G0 重启问题：供外部源码诊断的材料

## 可直接复制的一段话

我们在vLLM 0.23.0 / vLLM-Ascend、transformers 5.5.4、tokenizers 0.22.2、Qwen3.6-27B上验证“取消生成、接纳完整排空token前缀、以原始prompt IDs＋已接纳生成IDs重启”的可行性，发现固定流文本不保真：CPU测试不调用模型或parser，设原始prompt为P、已接纳前缀为H、固定后缀为S，比较连续decoder(P→H+S)与旧decoder(P→H，包含abort flush)＋新decoder(P+H→S)；使用真实IncrementalDetokenizer/OutputProcessor/collector，模拟pending=0/1/4，另有不经collector的直接decoder对照。H0(skip=False)与H1(仅改True)均失败60/768，无string-stop子集均失败30/384，失败切点相同；15份真实native/observation profile在修正TokenizerPool包装后身份完全匹配，但每份仍失败70/384，全部为token IDs相等、文本不等，直接decoder重启也失败。真实profile是DELTA、skip=True、stop=[]、stop_token_ids=[248044]、EOS=248046、include_stop_str_in_output=False，实际使用FastIncrementalDetokenizer及tokenizers.decoders.DecodeStream。一个original后缀Unicode例子在H长度5（末ID=59720）重启后，预期32字符，实际204094字符，输出从“分析中文与 emoji”变成“分析中文与 emojisystem\n# Tools\n...”并回显大量prompt；用历史短P也会回显“assistant\n<think>\n...”。测试的P来自真实原始IDs，但H/S来自对完整固定文本encode后切片，并非在线模型原始生成轨迹；真实DTB还会进行probe/cue等跨child转换，本测试未覆盖。请优先审计测试方法是否准确模拟真实abort/排空/flush和停止语义，再判断问题属于不完整Unicode前缀的DecodeStream预填充/续写状态恢复，还是测试在任意token切点强制终止旧decoder、拼接两段文本所建立的等价断言不成立；不要先假定测试正确或直接归咎模型/parser，请给出能区分“重启输入/实现缺陷”和“测试方法缺陷”的最小实验及源码依据。

## 测试实际逻辑（概念代码，不是额外已验证的实现）

```python
# P: live profile的prompt IDs（历史对照则为短合成prompt）
# ids: tokenizer.encode(完整固定样本文本, add_special_tokens=False)
# 所有分支共用相同P/H/S，不对P/H decode再encode。
end = min(trigger + pending, len(ids))
H = ids[:end]
S = ids[end:]                 # original；另一组人为改成encode('</think>\nanswer')

# collector路径：创建真实OutputProcessor + DELTA collector。
# 逐token执行state.detokenizer.update([token], stop_terminated)，
# state.make_request_output(...)，queue.put(...)；
# trigger以前立即消费，之后pending部分留在queue；
# processor.abort_requests([external_id], internal=False)，
# 消费合并后的abort terminal，接纳全部text/IDs。
prefix = abort_prefix(P, H, trigger, params)
assert prefix.token_ids == H
assert request_tables_are_empty

remaining_params.max_tokens = params.max_tokens - len(H)
continuous = decode(P, H + S, params)
resumed = decode(P + H, S, remaining_params)
actual = prefix.text + resumed.text

# direct_prefix不经过collector：逐tokenupdate；最后get_next_output_text(True, True)。
direct = direct_prefix(P, H, params) + resumed.text
assert actual == direct == continuous.text
assert H + resumed.token_ids == continuous.token_ids
```

`decode()`每个token后调用`get_next_output_text(finished, delta=True)`，最后再做finished flush；模拟EOS/token-stop/string-stop/max_tokens，已自然停止的前缀记not_applicable。脚本没有经过真实scheduler/EngineCore输出时序；请核查这些模拟是否与目标版本真实输出处理等价，而不是仅检查断言结果。

当前vLLM FastIncrementalDetokenizer初始化的关键调用是：

```python
self.tokenizer = tokenizer._tokenizer
self.stream = tokenizers.decoders.DecodeStream(
    ids=request.prompt_token_ids,
    skip_special_tokens=sampling_params.skip_special_tokens,
)
# 后续生成：self.stream.step(self.tokenizer, next_token_id)
```

## 建议附带的现成文件

第一批优先附不含真实业务prompt的文件：

- `tools/dtb/g0_experiment.py`：重点看`materialize()`、`trial()`、`run_matrix()`。
- `tools/dtb/g0_runtime.py`：重点看`prepare_profile()`、`decode()`、`abort_prefix()`、`direct_prefix()`。
- `tools/dtb/g0_support.py`及`input/g0/legacy_profiles.json`、`legacy_fixtures.jsonl`（input完整前缀见RESULT.md）。
- `run_server_03/g0_production/baseline/H1/failures/000001.json`：短合成P上的original失败，不需要真实58K prompt也能研究现象。
- 对应H1的`manifest.json`和`inputs/`中Unicode样本（含短合成P与完整固定IDs）；目标服务器实际`vllm/v1/engine/detokenizer.py`，并注明其SHA256。

第二批按需要才提供真实profile/P-native失败；这些文件包含内部系统提示、工具schema和业务上下文，体积大且可能敏感，不要默认整包上传。当前文档仅整理材料，未替用户向任何外部服务发送文件。

## 请诊断方特别避免的误判

- `skip=True`已复现，不要只建议改skip；`stop=[]`不等于没有EOS/token-stop。
- 固定original后缀也失败，不能只讨论人为forced-close；但forced-close在不完整Unicode后缀处的oracle仍应单独审计。
- 工具XML内的强制切点多数在自然boundary之后，普通observer已disarm；不能直接当成生产工具故障发生率。
- T附近有失败，但这份fixture恰trigger=850、pending=0/1/4的original组通过；需要移动Unicode对齐位置，不能据此断言850恒安全或恒失败。
- 原始生成IDs一致不自动证明增量文本状态一致；也不能由CPU固定流反推实际Agent发生频率。
