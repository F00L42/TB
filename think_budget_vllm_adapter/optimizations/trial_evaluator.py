"""Trial Answer Evaluator — 连续稳定答案检测与置信度加成。

从 think_budget/optimizations/trial_evaluator.py 迁移，
import 路径调整为 think_budget_vllm_adapter 内部模块。
"""
from typing import Dict

from think_budget_vllm_adapter.optimizations.early_stopping_compat import (
    extract_answer_for_sc, _normalize_for_sc
)


class TrialAnswerEvaluator:
    def __init__(self, config: Dict):
        self.enabled = config.get('enable_deer_trial', False)
        self.stable_rounds = config.get('deer_trial_stable_rounds', 2)
        self.confidence_boost_on_stable = config.get('deer_confidence_boost', 0.05)
        self.close_tag = config.get('think_close_tag', '</think>')
        self._prev_trial = None
        self._stable_count = 0

    def reset(self):
        self._prev_trial = None
        self._stable_count = 0

    def evaluate_trial(self, content: str, confidence: float) -> Dict:
        if not self.enabled:
            return {'is_stable': False, 'stable_count': 0, 'should_early_stop': False}

        trial_text = extract_answer_for_sc(content, self.close_tag)
        if not trial_text:
            return {'is_stable': False, 'stable_count': self._stable_count, 'should_early_stop': False}

        is_same = False
        if self._prev_trial is not None:
            norm_cur = _normalize_for_sc(trial_text)
            norm_prev = _normalize_for_sc(self._prev_trial)
            is_same = norm_cur == norm_prev and len(norm_cur) > 0

        if is_same:
            self._stable_count += 1
        else:
            self._stable_count = 0

        self._prev_trial = trial_text

        should_stop = self._stable_count >= self.stable_rounds and confidence > 0.0
        boosted_conf = min(confidence + self.confidence_boost_on_stable, 1.0) if is_same and self._stable_count >= 1 else confidence

        return {
            'is_stable': is_same,
            'stable_count': self._stable_count,
            'should_early_stop': should_stop,
            'trial_answer': trial_text[:80],
            'boosted_confidence': round(boosted_conf, 4),
        }
