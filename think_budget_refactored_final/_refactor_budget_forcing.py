"""Script to apply budget_forcing.py refactoring — externalize hardcoded constants to YAML config."""
import re

TARGET = r'C:\Users\r50058444\Desktop\work\workspace\think_budget_refactored_final\naie\models\budget_forcing.py'

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add import after existing imports (after the from naie.optimizations.history_budget line)
import_line = "from naie.config.unified_config import (\n    get_budget_forcing_config, get_safety_gate_config, get_prompt_format_config,\n)\n"
# Insert after the last import line
anchor = "from naie.optimizations.history_budget import (\n    compute_history_budget, detect_new_task_type, register_task_type_from_history,\n)"
if anchor in content:
    content = content.replace(anchor, anchor + "\n\n" + import_line)
    print("Added import line")
else:
    print("ERROR: Could not find import anchor")

# 2. Replace the constants block at top of file
# The original has _THINK_OPEN_TAG and _THINK_CLOSE_TAG with special chars
# We need to find and replace these lines specifically

# Replace _THINK_OPEN_TAG line
content = re.sub(
    r"# 文件顶部添加\n_THINK_OPEN_TAG = '.*?'\n_THINK_CLOSE_TAG = '.*?'",
    """# ─── 从 YAML 加载配置(回退到内置默认值) ───
_BF_CFG = get_budget_forcing_config()
_SG_CFG = get_safety_gate_config()
_PF_CFG = get_prompt_format_config()

_THINK_OPEN_TAG = _PF_CFG.get('think_open_tag', 'iesa')
_THINK_CLOSE_TAG = _PF_CFG.get('think_close_tag', 'eiraot')
_USER_PREFIX = _PF_CFG.get('user_prefix', '<|im_start|>user\\n')
_USER_SUFFIX = _PF_CFG.get('user_suffix', '<|im_end|>\\n<|im_start|>assistant\\n')""",
    content,
    count=1,
)
print("Replaced think tags block")

# 3. Replace _UNLIMITED_ANSWER_TOKENS
content = content.replace(
    "# 答案永远不被截断 — 最终答案生成使用此值，远超任何实际答案长度\n# max_tokens 只控制推理 token，最终答案生成不受 max_tokens 限制\n# 65536 是大多数 OpenAI 兼容 API 支持的上限，保证任何长度的答案都能完整输出\n_UNLIMITED_ANSWER_TOKENS = 65536",
    "_UNLIMITED_ANSWER_TOKENS = _BF_CFG.get('unlimited_answer_tokens', 65536)"
)
print("Replaced _UNLIMITED_ANSWER_TOKENS")

# 4. Replace _NATURAL_BREAK_CHARS (contains special chars, use regex)
content = re.sub(
    r"_NATURAL_BREAK_CHARS = \{.*?\}",
    """_NATURAL_BREAK_CHARS = set(_BF_CFG.get('natural_break_chars', ['。', '！', '？', '.', '!', '?', '\\n', '}', '】', '」']))
_NATURAL_BREAK_SEARCH_RANGE = _BF_CFG.get('natural_break_search_range', 500)
_PROMPT_TOKENS_DIVISOR = _BF_CFG.get('prompt_tokens_divisor', 4)
_MIN_THINK_RATIO_WARM = _BF_CFG.get('min_think_ratio_warm', 0.3)
_MIN_THINK_RATIO_HOT = _BF_CFG.get('min_think_ratio_hot', 0.2)
_THINKING_WORDS = _BF_CFG.get('thinking_words', [
    '等一下', '等等', '不过', '此外', '但是',
    'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively',
    'alternatively', 'However', 'however',
])

# 安全门参数
_BUDGET_AMPLIFY_FACTOR = _SG_CFG.get('budget_amplify_factor', 0.5)
_BUDGET_AMPLIFY_CAP = _SG_CFG.get('budget_amplify_cap', 4)
_BUDGET_TIME_CAP_MULTIPLIER = _SG_CFG.get('budget_time_cap_multiplier', 3.0)
_SOFT_TRIGGER_MULT = _SG_CFG.get('soft_trigger_multiplier', 1.0)
_HARD_TRIGGER_MULT = _SG_CFG.get('hard_trigger_multiplier', 1.5)""",
    content,
    count=1,
)
print("Replaced _NATURAL_BREAK_CHARS and added new config constants")

# 5. Replace thinking_words list (appears twice — inline and async versions)
# First occurrence (inline call_model)
old_tw_inline = """thinking_words = ['等一下', '等等', '不过', '此外', '但是', 'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively',
                          'alternatively', 'However', 'however']"""
new_tw_inline = "thinking_words = _THINKING_WORDS"
content = content.replace(old_tw_inline, new_tw_inline)
print(f"Replaced thinking_words inline (count: {content.count(new_tw_inline)})")

# Second occurrence (async call_model_async) — same pattern
# The replacement above should catch both if they're identical
remaining_tw = content.count("thinking_words = ['等一下'")
if remaining_tw > 0:
    print(f"WARNING: {remaining_tw} thinking_words lists still remain")
    # Try to replace remaining ones
    content = content.replace(
        "thinking_words = ['等一下', '等等', '不过', '此外', '但是', 'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively',\n                          'alternatively', 'However', 'however']",
        "thinking_words = _THINKING_WORDS",
    )

# 6. Replace budget_amplify hardcoded values
# Inline version
content = content.replace(
    "_budget_amplify = 1.0 + 0.5 * min(num_thinking, 4)",
    "_budget_amplify = 1.0 + _BUDGET_AMPLIFY_FACTOR * min(num_thinking, _BUDGET_AMPLIFY_CAP)",
)
# There are 2 occurrences (inline + async), replace_all
content = content.replace(
    "_effective_time_limit = min(baseline_time_limit * _budget_amplify, baseline_time_limit * 3.0)",
    "_effective_time_limit = min(baseline_time_limit * _budget_amplify, baseline_time_limit * _BUDGET_TIME_CAP_MULTIPLIER)",
)
print("Replaced budget_amplify constants")

# 7. Replace natural breakpoint search range (500)
content = content.replace(
    "max(len(content) - 500, -1)",
    "max(len(content) - _NATURAL_BREAK_SEARCH_RANGE, -1)",
)
print("Replaced natural_break_search_range")

# 8. Replace prompt_tokens divisor (len(prompt) // 4)
content = content.replace("len(prompt) // 4", "len(prompt) // _PROMPT_TOKENS_DIVISOR")
print(f"Replaced prompt_tokens_divisor (count: {content.count('len(prompt) // _PROMPT_TOKENS_DIVISOR')})")

# 9. Replace prompt format string
content = content.replace(
    '"<|im_start|>user\\n" + prompt + "<|im_end|>\\n<|im_start|>assistant\\n" + _THINK_OPEN_TAG + "\\n"',
    '_USER_PREFIX + prompt + _USER_SUFFIX + _THINK_OPEN_TAG + "\\n"',
)
print("Replaced prompt format string")

# 10. Replace check_length * 1.5 adjustment
content = content.replace(
    "check_length = min(int(check_length * 1.5), difficulty_check_length_max)",
    "check_length = min(int(check_length * _HARD_TRIGGER_MULT), difficulty_check_length_max)",
)
print("Replaced check_length * 1.5")

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(content)
print("\nDone! budget_forcing.py has been updated.")
