import json
import os
from datetime import datetime, timezone


_TRAJECTORY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'naie', 'trajectory_logs'
)


def log_trajectory(mode, dataset_name, entry):
    os.makedirs(_TRAJECTORY_DIR, exist_ok=True)
    log_path = os.path.join(_TRAJECTORY_DIR, f'trajectory_{mode}_{dataset_name}.jsonl')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def build_trajectory_entry(idx, question, mode, model_name,
                           thinking_tokens, total_tokens,
                           confidence, perplexity, score, is_correct,
                           elapsed_time, raw_content=None):
    entry = {
        'question_id': idx + 1,
        'question': str(question)[:200],
        'mode': mode,
        'model_name': model_name,
        'thinking_tokens': thinking_tokens,
        'total_tokens': total_tokens,
        'confidence': confidence,
        'perplexity': perplexity,
        'score': score,
        'is_correct': is_correct,
        'elapsed_time': round(elapsed_time, 3),
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    if raw_content is not None:
        entry['raw_content_preview'] = str(raw_content)[:500]
    return entry


def load_trajectories(mode=None, dataset_name=None):
    if not os.path.exists(_TRAJECTORY_DIR):
        return []
    trajectories = []
    for f in sorted(os.listdir(_TRAJECTORY_DIR)):
        if not f.endswith('.jsonl'):
            continue
        if mode and f'trajectory_{mode}_' not in f:
            continue
        if dataset_name and dataset_name not in f:
            continue
        with open(os.path.join(_TRAJECTORY_DIR, f), 'r', encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    trajectories.append(json.loads(line.strip()))
    return trajectories


def get_trajectory_summary(mode=None, dataset_name=None):
    trajectories = load_trajectories(mode, dataset_name)
    if not trajectories:
        return {'total': 0}
    total = len(trajectories)
    correct = sum(1 for t in trajectories if t.get('is_correct'))
    avg_thinking = sum(t.get('thinking_tokens', 0) or 0 for t in trajectories) / total
    avg_elapsed = sum(t.get('elapsed_time', 0) or 0 for t in trajectories) / total
    scores = [t.get('score', 0) or 0 for t in trajectories]
    return {
        'total': total,
        'correct': correct,
        'accuracy': correct / total * 100 if total else 0,
        'avg_thinking_tokens': avg_thinking,
        'avg_elapsed_time': avg_elapsed,
        'avg_score': sum(scores) / total,
    }
