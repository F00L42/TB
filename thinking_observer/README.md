# Thinking Observer SDK

Session D 的独立二次开发交付：增量观察模型思考，在合法位置停止，并用自定义引导直接续写答案。**没有实现重复检测算法**；附带的 token 阈值只用于验证接线。

交付分支：`codex/thinking-observer`。提取基线：TB `f342e127`。
目标：vLLM `v0.23.0` + vLLM-Ascend `v0.23.0rc1`。
本地验证是 CPU 协议与固定源码执行，设备运行、真实 tokenizer/HTTP/性能均为 **pending_remote**。

接手修改先读 [Agent 导航](AGENTS.md)；读取实际流过的文本/Token 并开发检测器见
[可运行的有界窗口示例](docs/detector_development.md)。

## 安装与直接集成

此目录可单独复制、安装、运行测试；不需要仓库的 `dtb/`、`deer/` 或预算 YAML。

```bash
python -m pip install -e /path/to/TB/thinking_observer
```

Python ≥3.11。SDK 本身没有第三方运行时依赖；vLLM 及设备依赖由服务器环境管理。
安装后向目标 vLLM 0.23 源码直接 `git apply` 本目录的三份 patch，命令见[根 README](../README.md)和[服务器手册](docs/server_runbook.md)。
不要求本地完整验收、初始化或检查 `third_party/`。

压缩算法可以直接侵入式修改 `src/thinking_observer/detectors.py`、`controller.py` 和 `integration.py`，
无需另建外部 detector 包。`THINKING_OBSERVER_FACTORY` 可指向包内自己的类/工厂，外部 factory 只是可选方式。
editable 安装后修改源码并重启服务即可生效；普通安装则需重新安装。下面展示的是现有 detector 接口用法。

```python
from thinking_observer import CONTINUE, StopAndAnswer

class MyDetector:
    def start(self, context):
        return MyRequestState()  # 每个请求一个新实例

class MyRequestState:
    def observe(self, delta):
        # 增量处理 delta.token_ids；自行实现算法。
        return CONTINUE
        # 算法决定接管时返回：
        # return StopAndAnswer(
        #     reason="my_detector", detected_at=delta.end,
        #     repeat_start=None,
        #     answer_cue="\n请根据已有推理直接给出完整答案。\n",
        # )

    def close(self, outcome):
        pass  # 清理状态；可读取最终接管位置和失败原因

def make_detector():
    return MyDetector()
```

`detected_at` / `repeat_start` 是从 0 开始的思考 token 位置，前缀长度采用右开区间。
`repeat_start` 仅作诊断。框架接管整个已接受 batch 和 abort 尾部；**不撤回已输出片段，也不按 repeat_start 回剪 prompt**。
`answer_cue` 仅进入内部续写 prompt，随后发出一次 parser 可见的 `</think>`；引导文本不会被塞入最终正文。

两个开关都开启才进入 SDK：

```bash
export THINKING_OBSERVER_ENABLED=1
export THINKING_OBSERVER_FACTORY=my_detector:make_detector
export THINKING_OBSERVER_TELEMETRY=off
# 请求 JSON："enable_thinking_observer": true
```

无触发保持原生同一个 engine child；触发最多再发起一次 final child，没有 hidden probe。
任一开关关闭时直接走原生 serving，不创建 detector state。
`NoopDetector` 是默认工厂。`TokenLimitDetector` 和 [custom_detector.py](examples/custom_detector.py) 是可替换的接线示例。

## 能力与交付入口

| 项目 | 首版行为 |
| --- | --- |
| stream=true / false | 内部 DELTA；对外保留原生流式 / 完整 response parser |
| 模型协议 | 绑定 Qwen3 reasoning 与可选 Qwen3Coder tool parser；ID 来自活动 tokenizer |
| 自然结束、工具边界 | 同 batch 的自然结束优先；drain 自然终止保持原 child；drain 边界之后继续原边界语义 |
| 接管位置 | 已接受完整 token 前缀；不提供任意删改历史或 holdback |
| completion | 可见源 token + 合成 closure + final ≤ 原生外部额度；内部 cue 占上下文，不计公开生成量 |
| 取消 / engine、parser 错误 | 传播并清理；不会重新启动一个请求伪装成功 |
| 普通 detector 错误 | 关闭本次检测，继续同一个原生 child；错误写入 close outcome |
| 不支持参数/模型 | 提交 engine 前原生回退，日志 `THINKING_OBSERVER_FALLBACK` 标明原因 |
| DP | 两个 child 透传同一显式 rank；多 DP 未指定 rank 时回退原生 |
| PD / KV transfer、多模态、beam、n>1、logprobs、状态相关采样 | 显式原生回退；详见 API 文档 |

- [设计与依赖提取表](docs/design.md)
- [API、位置与生命周期](docs/api.md)
- [部署、修改算法、回滚、服务器验收](docs/server_runbook.md)
- [可选检查与历史验证记录](docs/validation.md)
- [历史设计、实验与最终演示来源](docs/history.md)
- [独立补丁](patches/vllm-v0.23.0/observer_serving.patch)，不与 DTB serving patch 叠加。

可选日志 `THINKING_OBSERVER_SUMMARY` schema 1 包含接管位置、超出 token、回调/abort/接管耗时和两个 child 的 token/wall 账本；不记录正文。`off` 不写成功事件。
它不提供 scheduler queue/prefill/decode 分解，也不证明端到端净收益。
