"""NAIE 评估运行器 — 基线与 BudgetForcing 模式统一入口。

核心数据流:
  question → call_model() → response(content, actual_len, confidence, ...)
         → parse_think_content() → (think_text, answer_text)
         → parse_json_string(predicted) → calculate_score / evaluate_answer
         → result dict

统一使用标准 XML 标签 <think> 和 </think> 标识思考内容。
"""
import json
import os
import time
import math
import logging
from datetime import datetime

import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer

from naie.models.budget_forcing import call_model as bf_call_model
from naie.inference.think_budget import call_model
from naie.scoring.score import calculate_score
from naie.scoring.llm_judge import evaluate_answer
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47
from naie.evaluation.trajectory_logger import log_trajectory, build_trajectory_entry
from naie.optimizations.history_module import record_inference, get_category_stats, get_scene_stats, clear_history, load_history
from naie.optimizations.difficulty_estimator import estimate_difficulty
from naie.optimizations.scene_classifier import classify_scene
from naie.optimizations.task_type_budget import classify_task_type

logger = logging.getLogger(__name__)

_tokenizer = None

_ACCEPT_REASON_CN = {
    'confidence_above_threshold': '平均置信度达标',
    'joint_conf_above_threshold': '联合置信度达标',
    'think_tag_paired': '思考标签自然闭合',
    'think_tag_paired_natural': '思考标签自然闭合',
    'think_tag_paired_final': '思考标签自然闭合(最终轮)',
    'consecutive_same_2': '连续2次答案相同',
    'consecutive_same_3': '连续3次答案相同',
    'final_fallback': '达到最大轮次(兜底)',
    'safety_gate_fallback': '安全门超时回退',
    'safety_gate_fallback_no_answers': '安全门超时回退(无候选)',
    'continue': '继续思考',
}


def _translate_accept_reason(reason):
    """将 accept_reason 机器码翻译为中文描述。"""
    if not reason:
        return reason
    if reason.startswith('sc_majority_'):
        agree = reason.split('_')[-1]
        return f'SC多数投票({agree}一致)'
    if reason.startswith('value_min_conf_'):
        val_part = reason[len('value_min_conf_'):]
        val = val_part.split('_elevated_th_')[0] if '_elevated_th_' in val_part else val_part
        try:
            prob = f'{math.exp(float(val)):.2f}'
        except Exception:
            prob = val
        return f'Value最低概率={prob}'
    if reason.startswith('dual_ch_r'):
        # 双通道: dual_ch_r0.85_a0.97 → 推理=0.85, 动作=0.97
        return f'双通道通过(R={reason[9:13]},A={reason[15:19]})'
    if reason.startswith('reasoning_zone_low_'):
        # 双通道: reasoning_zone_low_0.35_a0.97 → 推理区不通过
        return f'推理区低置信(R={reason[19:23]},A={reason[25:29]})'
    if reason.startswith('action_zone_low_'):
        # 双通道: action_zone_low_r0.85_a0.42 → 动作区不通过
        return f'动作区低置信(R={reason[17:21]},A={reason[23:27]})'
    if reason.startswith('conf_ok_think_insufficient_'):
        parts = reason.split('_')
        actual = parts[-2] if len(parts) >= 4 else '?'
        needed = parts[-1] if len(parts) >= 4 else '?'
        return f'置信度达标但思考不足({actual}<{needed})'
    if reason.startswith('error:'):
        return f'异常:{reason[6:50]}'
    return _ACCEPT_REASON_CN.get(reason, reason)


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(NAIE_CONFIG['tokenizer_path'], trust_remote_code=True)
    return _tokenizer


def compute_thinking_tokens(text):
    """用 Qwen tokenizer 计算思考文本的 token 数。"""
    if not text:
        return 0
    return len(_get_tokenizer().encode(text, add_special_tokens=False))


def parse_think_content(full_content):
    """
    从模型返回的 full_content 中提取思考内容和答案内容。
    只支持标准 XML 格式: <think>思考内容</think>答案内容
    如果未找到 </think>，则认为整个内容就是答案。
    """
    if not full_content:
        return "", ""

    # 标准 XML 标签格式：<think>...</think>
    if "</think>" in full_content:
        parts = full_content.split("</think>", 1)
        think_part = parts[0]
        answer_part = parts[1].strip() if len(parts) > 1 else ""

        # 提取 <think> 和 </think> 之间的内容
        if "<think>" in think_part:
            think_content = think_part.split("<think>", 1)[1].strip()
        else:
            # 没有 <think> 开头，但有可能以其他方式开头，这里保守处理
            think_content = think_part.strip()
        return think_content, answer_part

    # 没有找到思考标签，全部视为答案
    return "", full_content


def load_datasets(dataset_dir=None):
    """加载目录下所有 .xlsx 测试数据集。"""
    if dataset_dir is None:
        dataset_dir = NAIE_CONFIG['dataset_dir']
    datasets = []
    for file in os.listdir(dataset_dir):
        if file.endswith('.xlsx'):
            path = os.path.join(dataset_dir, file)
            df = pd.read_excel(path)
            datasets.append({'name': file, 'data': df})
    return datasets


def parse_json_string(text):
    """解析 JSON 字符串，自动移除代码块标记和可能残留的思考标签。"""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)

    # 如果存在 </think>，移除标签及其之前的内容（保留标签后部分）
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]

    # 兼容可能出现的 <think> 开头（但一般不会有）
    if text.startswith("<think>"):
        text = text[len("<think>"):]

    try:
        if pd.isna(text):
            return None
    except (ValueError, TypeError):
        pass

    text = text.strip()
    if not text:
        return None

    # 移除 Markdown 代码块
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 如果解析失败，返回原字符串
        return text


def _save_result_file(save_dir, prefix, hyperparams, results, mode):
    os.makedirs(save_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(save_dir, f"{prefix}_{ts}.json")
    output = {
        "hyperparams": hyperparams,
        "mode": mode,
        "timestamp": datetime.now().isoformat(),
        "total_cases": len(results),
        "correct": sum(1 for r in results if r['correct']),
        "accuracy": sum(1 for r in results if r['correct']) / len(results) if results else 0,
        "cases": results,
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    return filepath


def _print_case_table(all_results, mode):
    if mode == 'budget':
        print(f"\n  {'Case':>4s}  {'结果':>4s}  {'分数':>4s}  {'思考tok':>8s}  {'端到端延迟':>10s}  "
              f"{'采纳原因':<20s}  {'Value最低概率':>14s}")
        print(f"  {'----':>4s}  {'----':>4s}  {'----':>4s}  {'--------':>8s}  {'----------':>10s}  "
              f"{'--------------------':<20s}  {'--------------':>14s}")
        for r in all_results:
            mark = 'O' if r['correct'] else 'X'
            vmc = f"{r['value_min_probability']:.2f}" if r.get('value_min_probability') is not None else '-'
            accept_cn = r.get('accept_reason_cn', r.get('accept_reason', ''))
            print(f"  {r['id']:>4d}  {mark:>4s}  {r['score']:>4d}  "
                  f"{r['thinking_tokens'] or 0:>8d}  {r['elapsed_time']:>9.1f}s  "
                  f"{accept_cn:<20s}  {vmc:>14s}")
    else:
        print(f"\n  {'Case':>4s}  {'结果':>4s}  {'分数':>4s}  {'思考tok':>8s}  {'端到端延迟':>10s}")
        print(f"  {'----':>4s}  {'----':>4s}  {'----':>4s}  {'--------':>8s}  {'----------':>10s}")
        for r in all_results:
            mark = 'O' if r['correct'] else 'X'
            print(f"  {r['id']:>4d}  {mark:>4s}  {r['score']:>4d}  "
                  f"{r['thinking_tokens'] or 0:>8d}  {r['elapsed_time']:>9.1f}s")


_API_MAX_RETRIES = 3
_API_RETRY_DELAY = 5.0


def _get_history_stats(question, task_type='general', scene_type=''):
    try:
        return get_category_stats(question[:100], task_type=task_type, scene_type=scene_type)
    except Exception:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}


def _evaluate_one(question, reference, model_name, response_fn, max_retries=_API_MAX_RETRIES):
    last_exc = None
    start_time = time.time()
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            response = response_fn(question, reference)
            raw_content = response.get('content', '')

            if not raw_content or len(raw_content.strip()) < 5:
                if attempt < max_retries - 1:
                    logger.warning(f"Empty API response for question (attempt {attempt+1}/{max_retries}), retrying...")
                    time.sleep(_API_RETRY_DELAY)
                    continue
                raw_content = raw_content or ''

            thinking_content, _ = parse_think_content(raw_content)
            thinking_tokens = compute_thinking_tokens(thinking_content)
            total_tokens = response.get('actual_len', 0)
            elapsed_time = time.time() - start_time
            predicted = raw_content
            score = calculate_score(reference, predicted)
            if score != 10:
                eval_result = evaluate_answer(question, parse_json_string(predicted), reference)
                score = eval_result.get('score', 0)
            else:
                eval_result = score
            is_correct = score == 10
            extras = {
                'confidence': response.get('confidence'),
                'perplexity': response.get('perplexity'),
                'accept_reason': response.get('accept_reason', ''),
                'value_min_confidence': response.get('value_min_confidence'),
                'api_call_count': response.get('api_call_count', 0),
                'thinking_rounds': response.get('thinking_rounds', 0),
                'sc_rounds': response.get('sc_rounds', 0),
                'complexity': response.get('complexity'),
                # budget_forcing 返回的推断类型(可能为 None 若 enable_task_type_budget=False)
                'response_task_type': response.get('task_type'),
                'response_scene_type': response.get('scene_type'),
            }
            break
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                logger.warning(f"API call failed for question (attempt {attempt+1}/{max_retries}): {e}, retrying in {_API_RETRY_DELAY}s...")
                time.sleep(_API_RETRY_DELAY)
            else:
                logger.error(f"API call failed after {max_retries} attempts: {e}")
    else:
        elapsed_time = time.time() - start_time if 'start_time' in locals() else 0
        is_correct = False
        score = 0
        eval_result = str(last_exc) if last_exc else 'max retries exceeded'
        thinking_tokens = None
        total_tokens = None
        raw_content = None
        predicted = None
        extras = {
            'confidence': None, 'perplexity': None,
            'accept_reason': f'error: {str(last_exc)[:100]}' if last_exc else 'error: max_retries',
            'value_min_confidence': None,
            'api_call_count': 0, 'thinking_rounds': 0, 'sc_rounds': 0,
            'complexity': None,
            'response_task_type': None, 'response_scene_type': None,
        }

    conf = extras['confidence']
    ppl = extras['perplexity']
    vmc_raw = extras['value_min_confidence']
    vmc_prob = round(math.exp(vmc_raw), 4) if vmc_raw is not None else None
    accept_cn = _translate_accept_reason(extras['accept_reason'])

    # 推断场景类型和任务类型并记录
    # 优先使用 budget_forcing 返回的推断类型(预算循环内已分类), 回退到本地分类
    scene_type = extras.get('response_scene_type')
    classified_task_type = extras.get('response_task_type')

    # 只在两者都为 None 时调用一次 classify_scene
    if scene_type is None or classified_task_type is None:
        scene_info = classify_scene(question)
        if scene_type is None:
            scene_type = scene_info.get('scene_type', 'general')
        if classified_task_type is None:
            task_info = classify_task_type(question, scene_info)
            classified_task_type = task_info.get('task_type', 'general')
            extras['task_confidence'] = task_info.get('task_confidence', 0.0)

    # 将推断的任务类型存入 extras，供 budget 模式记录历史使用
    extras['task_type'] = classified_task_type

    # 构建实际使用的预算参数记录(用于历史审计和预算回溯)
    budget_params_record = {
        'task_type': classified_task_type,
        'scene_type': scene_type,
        'accept_reason': extras.get('accept_reason', ''),
        'thinking_rounds': extras.get('thinking_rounds', 0),
        'api_call_count': extras.get('api_call_count', 0),
        'confidence': extras.get('confidence'),
    }

    record_inference(
        question=question, reference=reference,
        task_type=classified_task_type,  # 使用推断的任务类型(非硬编码'naie')
        difficulty_score=extras.get('complexity', {}).get('difficulty', 0) if isinstance(extras.get('complexity'), dict) else 0,
        budget_params=budget_params_record,
        result={'thinking_tokens': thinking_tokens, 'total_tokens': total_tokens,
                'score': score, 'elapsed_time': elapsed_time},
        scene_type=scene_type,
    )

    result = {
        'id': 0,
        'question': str(question),
        'raw_content': raw_content,
        'predicted': parse_json_string(predicted) if predicted else None,
        'reference': parse_json_string(reference) if not pd.isna(reference) else '',
        'correct': is_correct,
        'score': score,
        'match_detail': str(eval_result),
        'elapsed_time': round(elapsed_time, 2),
        'thinking_tokens': thinking_tokens,
        'total_tokens': total_tokens,
        'confidence': round(conf, 4) if conf is not None else None,
        'perplexity': round(ppl, 4) if ppl is not None else None,
        'accept_reason': extras['accept_reason'],
        'accept_reason_cn': accept_cn,
        'value_min_confidence': round(vmc_raw, 4) if vmc_raw is not None else None,
        'value_min_probability': vmc_prob,
        'api_call_count': extras['api_call_count'],
        'thinking_rounds': extras['thinking_rounds'],
        'sc_rounds': extras['sc_rounds'],
        'complexity': extras['complexity'],
        'task_type': extras.get('task_type', classified_task_type),
        'task_confidence': extras.get('task_confidence', 0.0),
        'scene_type': scene_type,
    }
    return result


def evaluate_baseline(datasets, result_dir, hyperparams=None):
    """基线评估: enable_confidence=False, 模型自由生成不截断。

    与参考代码 evaluate_datasets_39_think.py 的 think 模式一致:
      模型一次性生成全部内容(思考+答案), 不进入budget循环。
    """
    model_name = NAIE_CONFIG['model_name']
    max_token = NAIE_CONFIG['max_token']
    run_hyperparams = {"mode": "think_baseline", "model_name": model_name, "max_token": max_token}
    if hyperparams:
        run_hyperparams.update(hyperparams)

    print(f"\n[基线评估] model={model_name}, max_token={max_token}")

    def _baseline_fn(question, reference):
        return bf_call_model(question, max_token, enable_confidence=False, model_name='mg2hg')

    all_results = []
    run_start_time = time.time()
    total_cases = sum(len(ds['data']) for ds in datasets)
    pbar = tqdm(total=total_cases, desc="Baseline", unit="case",
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')

    for ds in datasets:
        df = ds['data']
        dataset_name = ds['name']
        for idx, row in df.iterrows():
            question = row.get('问题', row.get('question', ''))
            reference = row.get('参考答案', row.get('reference', ''))
            if pd.isna(question):
                pbar.update(1)
                continue

            result = _evaluate_one(question, reference, model_name, _baseline_fn)
            result['id'] = idx + 1
            all_results.append(result)

            pbar.set_postfix_str(
                f"ok={'Y' if result['correct'] else 'N'} score={result['score']} "
                f"think={result['thinking_tokens'] or 0}tok latency={result['elapsed_time']:.1f}s"
            )
            pbar.update(1)

            trajectory_entry = build_trajectory_entry(
                idx=idx, question=question, mode='think', model_name=model_name,
                thinking_tokens=result['thinking_tokens'], total_tokens=result['total_tokens'],
                confidence=result['confidence'], perplexity=result['perplexity'],
                score=result['score'], is_correct=result['correct'],
                elapsed_time=result['elapsed_time'], raw_content=result['raw_content']
            )
            log_trajectory('think', dataset_name, trajectory_entry)

    pbar.close()
    run_total_time = time.time() - run_start_time
    filepath = _save_result_file(result_dir, 'result_baseline', run_hyperparams, all_results, 'think')
    _print_case_table(all_results, 'baseline')
    print(f"\n[基线] 保存到 {filepath}")
    print(f"  端到端总延迟: {run_total_time:.1f}s")
    return all_results


def evaluate_budget(datasets, result_dir, budget_config=None):
    """BudgetForcing 评估: 使用 THINK_BUDGET_CONFIG_47 + 可选 CLI 覆盖。"""
    model_name = NAIE_CONFIG['model_name']
    cfg = dict(THINK_BUDGET_CONFIG_47)
    if budget_config:
        cfg.update(budget_config)

    run_hyperparams = {"mode": "think_budget", "model_name": model_name}
    run_hyperparams.update(cfg)

    print(f"\n[BudgetForcing评估] model={model_name}")
    print(f"  BudgetForcing config: {json.dumps(cfg, indent=2, default=str)}")

    max_tokens = cfg.get('max_tokens', 2000)

    def _budget_fn(question, reference):
        ref = str(reference) if not pd.isna(reference) else ''
        # v3: 使用 scene_classifier 推断场景, 用 task_type_budget 推断任务类型, 按双重过滤历史
        scene_info = classify_scene(question)
        scene_type = scene_info.get('scene_type', 'general')
        task_info = classify_task_type(question, scene_info)
        classified_task_type = task_info.get('task_type', 'general')
        # 按推断的任务类型过滤历史(而非硬编码'naie')
        cat_stats = _get_history_stats(question, task_type=classified_task_type, scene_type=scene_type)
        scene_stats = get_scene_stats(scene_type, task_type=classified_task_type)
        cfg_local = dict(cfg)
        # 优先 category 级统计, 回退 scene 级统计
        cfg_local['history_avg_tokens'] = cat_stats.get('avg_tokens', 0) or scene_stats.get('avg_tokens', 0)
        cfg_local['history_failure_rate'] = cat_stats.get('failure_rate', 0) or scene_stats.get('failure_rate', 0)
        cfg_local['history_category_avg_tokens'] = cat_stats.get('avg_tokens', 0)
        cfg_local['history_category_failure_rate'] = cat_stats.get('failure_rate', 0)
        cfg_local['history_count'] = cat_stats.get('count', 0) or scene_stats.get('count', 0)
        return call_model(question, max_tokens, budget_config=cfg_local, reference=ref)

    all_results = []
    run_start_time = time.time()
    total_cases = sum(len(ds['data']) for ds in datasets)
    pbar = tqdm(total=total_cases, desc="Budget", unit="case",
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')

    for ds in datasets:
        df = ds['data']
        dataset_name = ds['name']
        for idx, row in df.iterrows():
            question = row.get('问题', row.get('question', ''))
            reference = row.get('参考答案', row.get('reference', ''))
            if pd.isna(question):
                pbar.update(1)
                continue

            result = _evaluate_one(question, reference, model_name, _budget_fn)
            result['id'] = idx + 1
            all_results.append(result)

            vmc_str = f"{result['value_min_probability']:.2f}" if result.get('value_min_probability') is not None else '-'
            pbar.set_postfix_str(
                f"ok={'Y' if result['correct'] else 'N'} score={result['score']} "
                f"think={result['thinking_tokens'] or 0}tok accept={result['accept_reason_cn'][:12]} "
                f"vmc={vmc_str} latency={result['elapsed_time']:.1f}s"
            )
            pbar.update(1)

            trajectory_entry = build_trajectory_entry(
                idx=idx, question=question, mode='think_budget', model_name=model_name,
                thinking_tokens=result['thinking_tokens'], total_tokens=result['total_tokens'],
                confidence=result['confidence'], perplexity=result['perplexity'],
                score=result['score'], is_correct=result['correct'],
                elapsed_time=result['elapsed_time'], raw_content=result['raw_content']
            )
            log_trajectory('think_budget', dataset_name, trajectory_entry)

    pbar.close()
    run_total_time = time.time() - run_start_time
    filepath = _save_result_file(result_dir, 'result_budget', run_hyperparams, all_results, 'think_budget')
    _print_case_table(all_results, 'budget')
    print(f"\n[Budget] 保存到 {filepath}")
    print(f"  端到端总延迟: {run_total_time:.1f}s")
    return all_results


def calculate_metrics(results):
    """汇总评估指标。"""
    total = len(results)
    correct = sum(1 for r in results if r['correct'])
    accuracy = correct / total if total > 0 else 0
    times = [r['elapsed_time'] for r in results]
    avg_time = sum(times) / len(times) if times else 0
    think_vals = [r['thinking_tokens'] for r in results if r.get('thinking_tokens')]
    avg_think = sum(think_vals) / len(think_vals) if think_vals else 0
    api_counts = [r.get('api_call_count', 0) for r in results if r.get('api_call_count')]
    avg_api = sum(api_counts) / len(api_counts) if api_counts else 0
    return {
        'total': total,
        'correct': correct,
        'accuracy': accuracy,
        'avg_elapsed_time': round(avg_time, 2),
        'total_elapsed_time': round(sum(times), 2),
        'avg_thinking_tokens': round(avg_think, 1),
        'avg_api_calls': round(avg_api, 1),
    }