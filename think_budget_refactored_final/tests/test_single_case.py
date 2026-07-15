"""Quick single-case test for BudgetForcing pipeline verification.

Usage:
    python test_single_case.py [--prob_th 0.95] [--enable_value_min_confidence on]
"""
import naie._bootstrap  # sys.path + NO_PROXY

import time
import json

import pandas as pd
from naie.inference.think_budget import call_model
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47, print_config
from naie.evaluation.runner import parse_json_string, parse_think_content
from naie.scoring.score import calculate_score
from naie.scoring.llm_judge import evaluate_answer
from transformers import AutoTokenizer

_tokenizer = AutoTokenizer.from_pretrained(NAIE_CONFIG['tokenizer_path'], trust_remote_code=True)


def count_tokens_local(text):
    if not text:
        return 0
    return len(_tokenizer.encode(text, add_special_tokens=False))


def _on_off(val):
    s = str(val).lower()
    return s in ('on', 'true', '1', 'yes')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--prob_th', type=float, default=None)
    parser.add_argument('--check_length', type=int, default=None)
    parser.add_argument('--enable_value_min_confidence', type=str, default=None)
    parser.add_argument('--enable_clean_context', type=str, default=None)
    args = parser.parse_args()

    if args.prob_th is not None:
        THINK_BUDGET_CONFIG_47['prob_th'] = args.prob_th
    if args.check_length is not None:
        THINK_BUDGET_CONFIG_47['check_length'] = args.check_length
    if args.enable_value_min_confidence is not None:
        THINK_BUDGET_CONFIG_47['enable_value_min_confidence'] = _on_off(args.enable_value_min_confidence)
    if args.enable_clean_context is not None:
        THINK_BUDGET_CONFIG_47['enable_clean_context'] = _on_off(args.enable_clean_context)

    print_config()

    dataset_dir = NAIE_CONFIG['dataset_dir']
    datasets = []
    for file in os.listdir(dataset_dir):
        if file.endswith('.xlsx'):
            datasets.append({'name': file, 'data': pd.read_excel(os.path.join(dataset_dir, file))})

    if not datasets:
        print("No datasets found!")
        return

    df = datasets[0]['data']
    idx = 0
    row = df.iloc[idx]
    question = str(row['问题'])
    reference = row['参考答案']

    print(f"\n=== Single Case Test ===")
    print(f"Dataset: {datasets[0]['name']}")
    print(f"Question: {question[:200]}")
    print(f"Reference: {reference}")

    start_time = time.time()
    try:
        response = call_model(question, THINK_BUDGET_CONFIG_47.get('max_tokens', 2000))
        elapsed = time.time() - start_time

        content = response.get('content', '')
        thinking_content, answer_content = parse_think_content(content)
        thinking_tokens = count_tokens_local(thinking_content)

        predicted = content
        score = calculate_score(reference, predicted)
        match_result = score
        if score != 10:
            match_result = evaluate_answer(question, parse_json_string(predicted), reference)
            score = match_result.get('score', 0)
        is_correct = score == 10

        vmc_raw = response.get('value_min_confidence')
        vmc_prob = round(math.exp(vmc_raw), 4) if vmc_raw is not None else None

        print(f"\n=== Results ===")
        print(f"Correct: {is_correct}")
        print(f"Score: {score}")
        print(f"Thinking tokens: {thinking_tokens}")
        print(f"Total actual_len: {response.get('actual_len', 'N/A')}")
        print(f"Confidence: {f'{response[\"confidence\"]:.4f}' if response.get('confidence') is not None else 'N/A'}")
        print(f"Perplexity: {f'{response[\"perplexity\"]:.4f}' if response.get('perplexity') is not None else 'N/A'}")
        print(f"Value Min Confidence: {f'{vmc_prob:.4f}' if vmc_prob is not None else 'OFF'}")
        print(f"Accept reason: {response.get('accept_reason', 'N/A')}")
        print(f"Elapsed: {elapsed:.2f}s")
        print(f"\nAnswer content (last 500 chars): {answer_content[:500]}")

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n=== ERROR ===")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
