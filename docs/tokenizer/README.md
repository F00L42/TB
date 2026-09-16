# Agent 模型 tokenizer 快照

2026-09-15 在本机 **CPython 3.14.7** 验证。只下载每个模型的
`tokenizer.json`、`tokenizer_config.json`、`chat_template.jinja`，总计
33,061,401 bytes（约 31.53 MiB）；无权重、模型索引、模型 Python 代码或 Torch。
`tokenizer.json` 已包含词表和 merges，本次 fast AutoTokenizer 加载不需要单独下载
`vocab.json` / `merges.txt`。上游许可见
[Qwen LICENSE](https://huggingface.co/Qwen/Qwen3.6-27B/blob/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/LICENSE) 与
[GLM-5.3 LICENSE](https://huggingface.co/zai-org/GLM-5.3/blob/aca966e4e02791568aa6a4ced368624b3d897f42/LICENSE)。

## 1. 身份与复现

| 本地目录 | 官方仓库 | 固定 revision |
| --- | --- | --- |
| `Qwen3.6-27B/` | [Qwen/Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) | `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9` |
| `GLM-5.3/` | [zai-org/GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) | `aca966e4e02791568aa6a4ced368624b3d897f42` |

Qwen 选择沿用本仓库部署文档的 27B，不将结果外推到所有 Qwen3.6 变体。服务器的量化、
增训或定制模板版本必须另外比对 tokenizer/template SHA256。文件 URL、大小和 SHA256
在各目录 `manifest.json`；实际加载、编码、解码、模板渲染结果在
[`tokenizer_audit.json`](tokenizer_audit.json)。

在仓库根目录执行：

```powershell
python -m pip install --only-binary=:all: -r docs/tokenizer/requirements.txt
python tools/prepare_agent_tokenizers.py
# 如需重新获取相同 revision 的六个文件：
python tools/prepare_agent_tokenizers.py --download
```

```python
from transformers import AutoTokenizer
t = AutoTokenizer.from_pretrained(
    "docs/tokenizer/GLM-5.3", local_files_only=True, trust_remote_code=False
)
ids = t.encode("先检查实际状态。", add_special_tokens=False)
```

两者均为 fast backend；本机加载类分别为 `Qwen2Tokenizer`、`TokenizersBackend`。
类名与模型品牌无需相同。`vocab_size` 分别为 248044、154820；含 added tokens 的
`len(tokenizer)` 分别为 248077、154856，不能将它们混作模型 padded embedding size。

## 2. 结构 token 对照（本机实测）

表中都是完整 marker 编码为一个 token 的结果；`—` 表示不是同名 singleton marker。

| 语义 / marker | Qwen3.6-27B | GLM-5.3 |
| --- | ---: | ---: |
| `<think>` | 248068 | 154841 |
| `</think>` | 248069 | 154842 |
| `<tool_call>` | 248058 | 154843 |
| `</tool_call>` | 248059 | 154844 |
| `<tool_response>` | 248066 | 154845 |
| `</tool_response>` | 248067 | 154846 |
| `<arg_key>` / `</arg_key>` | — | 154847 / 154848 |
| `<arg_value>` / `</arg_value>` | — | 154849 / 154850 |
| `<|endoftext|>` | 248044 | 154820 |
| `<|im_start|>` / `<|im_end|>` | 248045 / 248046 | — |
| `[gMASK]` / `<sop>` | — | 154822 / 154824 |
| `<|system|>` / `<|user|>` | — | 154826 / 154827 |
| `<|assistant|>` / `<|observation|>` | — | 154828 / 154829 |

**结构 marker 不等于 tokenizer 的 special 标记。** 两个快照的 `<think>`、`</think>`、
`<tool_call>`、`</tool_call>` 都在 added-token 表中标记为 `special=false`，但仍为
独立 token；GLM 的 arg markers 也一样。`skip_special_tokens=True` 不会自动删除它们。
不能通过 `all_special_ids` 来寻找 reasoning/tool 边界。

审计分别保存 artifact 的 `added_tokens.special`、加载 backend 的 special 属性、
`all_special_ids` 成员关系和实际 decode 结果。本机 Qwen 的 `<|im_start|>` 在前两者
为 true，但不在 `all_special_ids`，实际 skip decode 仍删除它；因此也不能仅凭高层
special 列表断言最终解码行为。服务器依赖版本需单独复验。

`tokenizer.eos_token_id` 仅分别是 248046 和 154820。只读核对固定 revision 的上游
generation config 得到：Qwen EOS 集为 `[248046, 248044]`，GLM 为
`[154820, 154827, 154829]`，后两者对应 user/observation role 边界。
这些 JSON 未下载到快照，因为 AutoTokenizer 不需要它们；来源是
[Qwen generation config](https://huggingface.co/Qwen/Qwen3.6-27B/blob/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/generation_config.json) 和
[GLM generation config](https://huggingface.co/zai-org/GLM-5.3/blob/aca966e4e02791568aa6a4ced368624b3d897f42/generation_config.json)。
线上仍应记录实际加载的 generation config / 请求 stop 集；不能用单个 tokenizer EOS
替代服务的结束契约，也不能把 `<|observation|>` 从 EOS 集中随意删除。

## 3. Chat template 差异

| 项目 | Qwen 快照 | GLM 快照 |
| --- | --- | --- |
| assistant 开始 | `<|im_start|>assistant\n` | `<|assistant|>` |
| 工具参数 | `<function=...><parameter=...>...` | 函数名 + `<arg_key>...<arg_value>...` |
| 工具结果 role | user 块内 `<tool_response>` | `<|observation|>` 内 `<tool_response>` |
| 历史思考 | 默认清除较早 user 轮次的 reasoning；`preserve_thinking=true` 保留 | 默认 `clear_thinking=false` 保留；true 清理较早轮次 |
| 当前 user 后的工具链思考 | 两种历史策略均仍保留 | 两种历史策略均仍保留 |
| `enable_thinking=false` | generation prompt 补闭合 `</think>` | 此快照模板忽略该参数，仍以 `<think>` 开始 |
| `reasoning_effort` | 此模板不使用 | `low` / `high`，其他值或未传落到 `max` |

合成对话包含一个已结束的历史 user 轮次，以及当前 assistant→tool 往返。
Qwen 默认/保留历史分别 332/341 prompt tokens，GLM 默认/清除历史分别 189/185。
**这是模板 smoke fixture，不是用户轨迹，也不是跨模型效率排名。**

GLM 使用 `enable_thinking=false` 时，tokenizer 模板仍打开思考，而 vLLM `glm45`
parser 可以切到 identity 模式，两者存在配置不一致风险。不能把该参数直接当作 GLM
的禁思考方案。`/nothink` 虽在词表中，单凭有这个 token 也不能证明它控制生成的效果。

审计验证九组 kwargs × 两个 tokenizer 的模板渲染与 token 编码一致。它不验证模型生成、
vLLM 实际 detokenizer/parser、NPU 或服务时延。完整迁移计划见
[`dtb_glm53_agent_acceleration_plan.md`](../design/dtb_glm53_agent_acceleration_plan.md)。
