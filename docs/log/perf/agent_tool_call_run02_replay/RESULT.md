# run-02 tool-call 流式回放输入抽取

## 结论

从 `full_log.jsonl` 抽取 4 个 request-body 精确样本。每个样本都是对应分支第一次出现工具 XML 污染、且请求历史中的 assistant 消息尚未出现同类污染的轮次。

HTTP headers 未包含在代理归档中；回放脚本会为每个请求生成确定性的 `X-Request-Id`。

## 样本

| Case | Branch | Source line | Messages | Original E2E ms | Prompt tokens | Completion tokens | Reasoning tokens | Content tokens | Leaked functions | Structured tools |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| run02-tool-boundary-01 | `36_DVAIAgentService_ed9292b4` | 98 | 18 | 7734.295 | 50660 | 144 | 31 | 60 | `read` | `bash` |
| run02-tool-boundary-02 | `2_DataSmartCubeService_ece9bf60` | 206 | 22 | 17351.068000000003 | 54547 | 350 | 38 | 146 | `read` | `read` |
| run02-tool-boundary-03 | `28_ExperienceAssuranceService_42e693fa` | 300 | 4 | 66149.98 | 50581 | 1332 | 89 | 1182 | `write` | `bash` |
| run02-tool-boundary-04 | `37_DVAIAgentService_11718ad6` | 307 | 10 | 170474.295 | 50167 | 3694 | 32 | 3606 | `task` | `bash` |

## 使用

```bash
python tools/dtb/replay_tool_calls.py --file docs/log/perf/agent_tool_call_run02_replay/cases.jsonl --mode both --repetitions 10 --request-id-run agent-run02 --fixed-budget-tokens 20000 --fixed-check-length 400 --fixed-think-stride 500 --fixed-num-thinking 4 --fixed-min-think-tokens 1 --fixed-prob-th 0.93
```
