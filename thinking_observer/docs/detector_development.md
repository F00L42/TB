# 直接修改 observer 与开发检测器

压缩算法可以直接侵入式修改 `src/thinking_observer/`，无需另建外部 detector 包：

- `detectors.py`：增量停止判断与 cue 策略。
- `controller.py`：接管、abort/drain、压缩与答案续写流程。
- `integration.py` / `config.py`：默认工厂、配置与 vLLM 适配。

使用 `pip install -e ./thinking_observer` 后直接改源码并重启服务。使用现有 detector 接口时，
`THINKING_OBSERVER_FACTORY` 指向包内自己的类或工厂；外部 factory 只是可选方式。
直接修改仍要保留请求隔离、原生 parser、已输出 text/token 连续性、取消和额度语义。
不要求先执行完整本地验收或检查 `third_party/`。以下介绍现有回调接口，便于按需复用。

扩展点是 `Detector.start(context)` 返回的请求状态的 `observe(delta)`，不需要从 HTTP/SSE 文本重新分词。
`delta.token_ids` 已经是本次新增的 reasoning token ID；`delta.text` 是原生 detokenizer 的新增片段。
可直接运行 [有界窗口示例](../examples/observe_window.py)：

```bash
python -m pip install -e ./thinking_observer
python thinking_observer/examples/observe_window.py
```

示例仅用公开 API 构造合成事件，始终返回 `CONTINUE`。它证明观察入口、窗口和状态隔离可用，
不实现重复检测、不触发接管、不访问模型，也不证明真实 tokenizer 的文本连续性。

## 读到的是什么

| 数据 | 正确用法 | 不成立的假设 |
| --- | --- | --- |
| `delta.token_ids` | 把本次 ID 加入请求自己的 token 窗口；直接按 ID 做增量算法 | 把 `text` encode 一次就等于这组 ID |
| `delta.start/end` | 全程 reasoning token 的 `[start,end)`；`end-start == len(token_ids)` | 用字符位置或当前窗口下标直接当全局 token 偏移 |
| `delta.text` | `is not None` 时处理新增片段；空串合法 | 文本总与当前这组 ID 一一对应 |
| `text=None` | 本次混合边界无法安全切文本；记录文本不完整，继续用权威 ID | 把 None 当成已知没有文本，宣称已收齐全文 |
| `boundary/terminal` | 可观察该最后事件；自然边界/终态优先 | 检测器能覆盖原生 stop/length 或继续看到 final/tool 内容 |
| `emitted_tokens` | 框架已接受的 parser-facing completion token 数 | HTTP 客户端已收到这些字节 |

token 窗口尾部的全局起点是 `delta.end - len(window)`。文本窗口只表示已观察片段的字符后缀，
没有对应 token 起点。text-only 事件不增计 token；自然边界之后的正文/tool 与 abort drain 不再喂给检测算法。
若算法需要完整文本或精确 token/字符对齐，当前接口不提供此保证，必须另定义并验证数据契约。

## 接上自己的算法

1. 从示例复制 factory / state 形状，每请求一个状态；在 state 内保存有上限的窗口或滚动统计。
2. 在 `observe` 中先消费新增 ID/文本，再更新自己的增量检测结果。不要累计全文、每块重扫全文或在回调内 I/O。
3. 不介入返回 `CONTINUE`。要结束思考时返回 `StopAndAnswer`，其位置是全局 reasoning token 前缀长度：

```python
return StopAndAnswer(
    reason="my_detector",
    detected_at=delta.end,
    repeat_start=global_repeat_start,  # 可选；只能是 token 位置，仅诊断
    answer_cue="\n请根据已有推理给出完整答案。\n",
)
```

`0 <= repeat_start <= detected_at <= delta.end`。不能返回字符位置。框架保留完整已接受 batch 和 abort 尾部，
不会回剪到重复起点；实际接管点在 `close(outcome)` 的 `actual_handoff`，可能晚于检测点。
自然结束或工具边界优先；cue 仅进入内部续写 prompt，不能包含结构 token。详细额度与失败语义见 [API](api.md)。

4. 按改动范围选择 chunk 切分、跨 chunk 候选、空 ID/text、`text=None`、边界/终态、请求隔离或 close 清理检查。
   接管改动可使用 SDK 引擎 fake 场景；真实协议、算法质量和耗时参考[服务器手册](server_runbook.md)。

在服务器仅观察窗口时可这样绑定示例；配置后重启服务：

```bash
export SDK=/absolute/path/TB/thinking_observer
export PYTHONPATH="$SDK/examples${PYTHONPATH:+:$PYTHONPATH}"
export THINKING_OBSERVER_ENABLED=1
export THINKING_OBSERVER_FACTORY=observe_window:make_detector
# 请求中另外设置 enable_thinking_observer=true
```

示例不会向日志输出正文或原始 ID，close 会清空窗口。若后续需要采样正文排障，应另设计显式、有限的采集流程，
不要在高频同步回调中直接写文件。
