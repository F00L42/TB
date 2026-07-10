import re
from typing import Dict

_COMPLEXITY_KEYWORDS_HIGH = {
    '根因', '根因分析', '根因推理', '诊断', '故障诊断', '告警诊断',
    '复杂逻辑', '多步推理', '链式推理', '综合分析',
    'SBC', 'PSBC', 'ABCF', 'IMSI', 'KPI',
}

_COMPLEXITY_KEYWORDS_MEDIUM = {
    '对比', '比较', '差异', '趋势', '变化',
    '排查', '定位', '分析', '原因',
    '接通率', '成功率', '利用率', '过载',
    '告警', '性能', '指标',
}

_TABLE_ROW_PATTERN = re.compile(r'\|[^|]+\|[^|]+\|', re.MULTILINE)
_NUMBER_PATTERN = re.compile(r'\d+\.?\d*')
_IP_PATTERN = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_JSON_KEY_PATTERN = re.compile(r'"[a-zA-Z_]\w*"')
def estimate_complexity(question: str, reference: str = '') -> Dict[str, float]:
    q_len = len(question)
    ref_len = len(reference) if reference else 0

    table_rows = len(_TABLE_ROW_PATTERN.findall(question))
    numbers = len(_NUMBER_PATTERN.findall(question))
    ips = len(_IP_PATTERN.findall(question))
    json_keys = len(_JSON_KEY_PATTERN.findall(reference)) if reference else 0

    q_lower = question.lower()
    high_hits = sum(1 for kw in _COMPLEXITY_KEYWORDS_HIGH if kw in q_lower or kw in question)
    medium_hits = sum(1 for kw in _COMPLEXITY_KEYWORDS_MEDIUM if kw in q_lower or kw in question)

    text_complexity = 0.0
    if q_len > 3000:
        text_complexity += 0.3
    elif q_len > 1500:
        text_complexity += 0.2
    elif q_len > 800:
        text_complexity += 0.1

    table_complexity = 0.0
    if table_rows > 20:
        table_complexity = 0.3
    elif table_rows > 10:
        table_complexity = 0.2
    elif table_rows > 3:
        table_complexity = 0.1

    keyword_complexity = min(high_hits * 0.15 + medium_hits * 0.05, 0.3)

    numeric_complexity = 0.0
    if numbers > 30:
        numeric_complexity = 0.15
    elif numbers > 15:
        numeric_complexity = 0.1
    elif numbers > 5:
        numeric_complexity = 0.05

    answer_complexity = 0.0
    if json_keys > 6:
        answer_complexity = 0.15
    elif json_keys > 3:
        answer_complexity = 0.1
    elif json_keys > 0:
        answer_complexity = 0.05

    raw_score = text_complexity + table_complexity + keyword_complexity + numeric_complexity + answer_complexity
    normalized = min(raw_score / 1.2, 1.0)

    return {
        'complexity': normalized,
        'text_len': q_len,
        'table_rows': table_rows,
        'high_keyword_hits': high_hits,
        'medium_keyword_hits': medium_hits,
        'numbers': numbers,
        'json_keys': json_keys,
    }
