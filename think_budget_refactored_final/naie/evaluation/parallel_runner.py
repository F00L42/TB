"""NAIE 并行评估运行器 — asyncio 版。

基于 asyncio + httpx.AsyncClient 实现异步并发:
  - asyncio + httpx.AsyncClient 实现异步并发
  - asyncio.Semaphore 控制并发度
  - asyncio.Lock 保护共享状态
  - asyncio.gather() 扇出所有任务

与 runner.py 的关系:
  - 复用 runner.py 中所有纯函数 (parse_think_content, calculate_score 等)
  - evaluate_baseline_parallel / evaluate_budget_parallel 接口与顺序版完全一致
  - 额外参数: concurrency (并发数, 默认4)
  - runner.py 保留不变, 此文件为增量扩展

线程/协程安全保障:
  - all_results.append + trajectory 写入: asyncio.Lock 保护 (单线程事件循环, 实际无竞态)
  - record_inference: history_module 内部已有 _HISTORY_LOCK, asyncio 环境下仍安全
  - model_client: 异步版使用 httpx.AsyncClient 连接池
  - tqdm: 使用 asyncio 兼容方式更新进度条
"""
import asyncio
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
from naie.models.budget_forcing import call_model_async as bf_call_model_async
from naie.models.model_client import async_model_call, async_close_client, set_api_concurrency
from naie.inference.think_budget import call_model_async as naie_call_model_async
from naie.evaluation.runner import (
    _translate_accept_reason,
    _get_tokenizer,
    compute_thinking_tokens,
    parse_think_content,
    parse_json_string,
    _save_result_file,
    _print_case_table,
    _API_MAX_RETRIES,
    _API_RETRY_DELAY,
    _get_history_stats,
    load_datasets,
    calculate_metrics,
)
from naie.scoring.score import calculate_score
from naie.scoring.llm_judge import evaluate_answer
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47
from naie.evaluation.trajectory_logger import log_trajectory, build_trajectory_entry
from naie.optimizations.history_module import record_inference

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 异步版 _evaluate_one
# ─────────────────────────────────────────────────────

async def _evaluate_one_async(question, reference, model_name, response_fn, max_retries=_API_MAX_RETRIES):
    """_evaluate_one 的异步版 — 内部调用 await response_fn()。

    逻辑与 runner.py _evaluate_one 完全一致, 仅将 response_fn() 改为 await response_fn()。
    llm_judge.evaluate_answer 仍然是同步调用 (短耗时, 不会阻塞事件循环)。
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            response = await response_fn(question, reference)
            raw_content = response.get('content', '')

            if not raw_content or len(raw_content.strip()) < 5:
                if attempt < max_retries - 1:
                    logger.warning(f"Empty API response for question (attempt {attempt+1}/{max_retries}), retrying...")
                    await asyncio.sleep(_API_RETRY_DELAY)
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
            }
            break
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                logger.warning(f"API call failed for question (attempt {attempt+1}/{max_retries}): {e}, retrying in {_API_RETRY_DELAY}s...")
                await asyncio.sleep(_API_RETRY_DELAY)
            else:
                logger.error(f"API call failed after {max_retries} attempts: {e}")
    else:
        elapsed_time = 0
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
        }

    conf = extras['confidence']
    ppl = extras['perplexity']
    vmc_raw = extras['value_min_confidence']
    vmc_prob = round(math.exp(vmc_raw), 4) if vmc_raw is not None else None
    accept_cn = _translate_accept_reason(extras['accept_reason'])

    record_inference(
        question=question, reference=reference,
        difficulty_score=extras.get('complexity', {}).get('difficulty', 0) if isinstance(extras.get('complexity'), dict) else 0,
        budget_params=None,
        result={'thinking_tokens': thinking_tokens, 'total_tokens': total_tokens,
                'score': score, 'elapsed_time': elapsed_time},
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
    }
    return result


# ─────────────────────────────────────────────────────
# 基线并行评估 (asyncio)
# ─────────────────────────────────────────────────────

async def _run_baseline_async(datasets, result_dir, hyperparams, max_concurrent_requests):
    """基线并行评估的 async 核心。

    并发控制为 API 请求级别: max_concurrent_requests 限制同时进行的 API 请求数，
    而非题目数。基线模式每题 1 次 API 调用，所以题目并发数 ≈ max_concurrent_requests。
    """
    model_name = NAIE_CONFIG['model_name']
    max_token = NAIE_CONFIG['max_token']

    # 设置全局 API 请求级信号量
    set_api_concurrency(max_concurrent_requests)

    async def _baseline_fn(question, reference):
        return await bf_call_model_async(question, max_token, enable_confidence=False, model_name='mg2hg')

    # 展平所有用例
    tasks = []
    for ds in datasets:
        df = ds['data']
        dataset_name = ds['name']
        for idx, row in df.iterrows():
            question = row.get('问题', row.get('question', ''))
            reference = row.get('参考答案', row.get('reference', ''))
            if pd.isna(question):
                continue
            tasks.append((dataset_name, idx, question, reference))

    total_cases = len(tasks)
    all_results = []
    lock = asyncio.Lock()

    pbar = tqdm(total=total_cases, desc="Baseline∥", unit="case",
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')

    async def run_one(dataset_name, idx, question, reference):
        result = await _evaluate_one_async(question, reference, model_name, _baseline_fn)
        result['id'] = idx + 1

        async with lock:
            all_results.append(result)
            trajectory_entry = build_trajectory_entry(
                idx=idx, question=question, mode='think', model_name=model_name,
                thinking_tokens=result['thinking_tokens'], total_tokens=result['total_tokens'],
                confidence=result['confidence'], perplexity=result['perplexity'],
                score=result['score'], is_correct=result['correct'],
                elapsed_time=result['elapsed_time'], raw_content=result['raw_content'],
            )
        # 文件 I/O (log_trajectory) 在锁外执行，避免阻塞其他协程
        log_trajectory('think', dataset_name, trajectory_entry)

        pbar.set_postfix_str(
            f"ok={'Y' if result['correct'] else 'N'} score={result['score']} "
            f"think={result['thinking_tokens'] or 0}tok latency={result['elapsed_time']:.1f}s"
        )
        pbar.update(1)
        return result

    coros = [run_one(ds_name, idx, q, ref) for ds_name, idx, q, ref in tasks]
    await asyncio.gather(*coros, return_exceptions=True)

    pbar.close()
    all_results.sort(key=lambda r: r['id'])
    return all_results


def evaluate_baseline_parallel(datasets, result_dir, hyperparams=None, concurrency=4):
    """基线并行评估: asyncio 版。

    Args:
        datasets: load_datasets() 返回的数据集列表
        result_dir: 结果保存目录
        hyperparams: 运行超参数 (与顺序版一致)
        concurrency: 并发数 (默认4)
    """
    model_name = NAIE_CONFIG['model_name']
    max_token = NAIE_CONFIG['max_token']
    run_hyperparams = {"mode": "think_baseline_async_parallel", "model_name": model_name,
                       "max_token": max_token, "concurrency": concurrency}
    if hyperparams:
        run_hyperparams.update(hyperparams)

    print(f"\n[基线并行评估] model={model_name}, max_token={max_token}, concurrency={concurrency}")

    async def _run_with_cleanup():
        """在同一事件循环中运行评估并清理客户端，避免跨循环访问。"""
        try:
            return await _run_baseline_async(datasets, result_dir, run_hyperparams, concurrency)
        finally:
            await async_close_client()

    run_start_time = time.time()
    all_results = asyncio.run(_run_with_cleanup())
    run_total_time = time.time() - run_start_time
    filepath = _save_result_file(result_dir, 'result_baseline', run_hyperparams, all_results, 'think')
    _print_case_table(all_results, 'baseline')
    print(f"\n[基线∥] 保存到 {filepath}")
    print(f"  端到端总延迟: {run_total_time:.1f}s  (concurrency={concurrency})")
    return all_results


# ─────────────────────────────────────────────────────
# BudgetForcing 并行评估 (asyncio)
# ─────────────────────────────────────────────────────

async def _run_budget_async(datasets, result_dir, budget_config, concurrency):
    """BudgetForcing 并行评估的 async 核心。

    并发控制说明:
      - 题目级: asyncio.Semaphore(concurrency) 限制同时运行的题目数
      - API 请求级: set_api_concurrency() 限制同时进行的 API 请求数
      - BudgetForcing 每题发起 num_thinking+1 次 API 调用，因此
        API 并发上限 = concurrency × (num_thinking + 1)，否则双层
        信号量会导致实际并发远低于预期
    """
    model_name = NAIE_CONFIG['model_name']
    cfg = dict(THINK_BUDGET_CONFIG_47)
    if budget_config:
        cfg.update(budget_config)
    max_tokens = cfg.get('max_tokens', 2000)

    # 自动设置 API 请求级并发上限 = 题目并发数 × 每题平均 API 调用数
    # 避免双层信号量叠加导致实际并发远低于预期
    avg_api_calls_per_task = cfg.get('num_thinking', 3) + 1  # thinking rounds + 1 final
    max_concurrent_requests = concurrency * avg_api_calls_per_task
    set_api_concurrency(max_concurrent_requests)

    async def _budget_fn(question, reference):
        ref = str(reference) if not pd.isna(reference) else ''
        cat_stats = _get_history_stats(question)
        cfg_local = dict(cfg)
        cfg_local['history_avg_tokens'] = cat_stats.get('avg_tokens', 0)
        cfg_local['history_failure_rate'] = cat_stats.get('failure_rate', 0)
        cfg_local['history_category_avg_tokens'] = cat_stats.get('avg_tokens', 0)
        cfg_local['history_category_failure_rate'] = cat_stats.get('failure_rate', 0)
        cfg_local['history_count'] = cat_stats.get('count', 0)
        return await naie_call_model_async(question, max_tokens, budget_config=cfg_local, reference=ref)

    # 展平所有用例
    tasks = []
    for ds in datasets:
        df = ds['data']
        dataset_name = ds['name']
        for idx, row in df.iterrows():
            question = row.get('问题', row.get('question', ''))
            reference = row.get('参考答案', row.get('reference', ''))
            if pd.isna(question):
                continue
            tasks.append((dataset_name, idx, question, reference))

    total_cases = len(tasks)
    all_results = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)

    pbar = tqdm(total=total_cases, desc="Budget∥", unit="case",
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')

    async def run_one(dataset_name, idx, question, reference):
        async with semaphore:
            result = await _evaluate_one_async(question, reference, model_name, _budget_fn)
            result['id'] = idx + 1

            vmc_str = (f"{result['value_min_probability']:.2f}"
                       if result.get('value_min_probability') is not None else '-')

            async with lock:
                all_results.append(result)
                trajectory_entry = build_trajectory_entry(
                    idx=idx, question=question, mode='think_budget', model_name=model_name,
                    thinking_tokens=result['thinking_tokens'], total_tokens=result['total_tokens'],
                    confidence=result['confidence'], perplexity=result['perplexity'],
                    score=result['score'], is_correct=result['correct'],
                    elapsed_time=result['elapsed_time'], raw_content=result['raw_content'],
                )
            # 文件 I/O (log_trajectory) 在锁外执行，避免阻塞其他协程
            log_trajectory('think_budget', dataset_name, trajectory_entry)

            pbar.set_postfix_str(
                f"ok={'Y' if result['correct'] else 'N'} score={result['score']} "
                f"think={result['thinking_tokens'] or 0}tok accept={result['accept_reason_cn'][:12]} "
                f"vmc={vmc_str} latency={result['elapsed_time']:.1f}s"
            )
            pbar.update(1)
            return result

    coros = [run_one(ds_name, idx, q, ref) for ds_name, idx, q, ref in tasks]
    await asyncio.gather(*coros, return_exceptions=True)

    pbar.close()
    all_results.sort(key=lambda r: r['id'])
    return all_results


def evaluate_budget_parallel(datasets, result_dir, budget_config=None, concurrency=4):
    """BudgetForcing 并行评估: asyncio 版。

    Args:
        datasets: load_datasets() 返回的数据集列表
        result_dir: 结果保存目录
        budget_config: BudgetForcing 配置 (与顺序版一致)
        concurrency: 并发数 (默认4)
    """
    model_name = NAIE_CONFIG['model_name']
    cfg = dict(THINK_BUDGET_CONFIG_47)
    if budget_config:
        cfg.update(budget_config)

    run_hyperparams = {"mode": "think_budget_async_parallel", "model_name": model_name,
                       "concurrency": concurrency}
    run_hyperparams.update(cfg)

    print(f"\n[BudgetForcing并行评估] model={model_name}, concurrency={concurrency}")
    print(f"  BudgetForcing config: {json.dumps(cfg, indent=2, default=str)}")

    async def _run_with_cleanup():
        """在同一事件循环中运行评估并清理客户端，避免跨循环访问。"""
        try:
            return await _run_budget_async(datasets, result_dir, budget_config, concurrency)
        finally:
            await async_close_client()

    run_start_time = time.time()
    all_results = asyncio.run(_run_with_cleanup())
    run_total_time = time.time() - run_start_time
    filepath = _save_result_file(result_dir, 'result_budget', run_hyperparams, all_results, 'think_budget')
    _print_case_table(all_results, 'budget')
    print(f"\n[Budget∥] 保存到 {filepath}")
    print(f"  端到端总延迟: {run_total_time:.1f}s  (concurrency={concurrency})")
    return all_results
