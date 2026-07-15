"""单题测试 — 完整打印 BudgetForcing 管线逐轮置信度、双通道诊断、Token统计与完整模型输出。

用法:
    python run_one.py [--enable_dual_channel on] [--prob_th 0.95]

固定问题:
    Eight circles of radius $34$ ... (可通过 --question 覆盖)

输出:
    1. 逐轮打印推理/动作区置信度、平均置信度、最低K个token概率、accept决策
    2. Token 统计: 推理tokens、答案tokens、总输出tokens、actual_len
    3. 完整推理过程 (thinking_content) — 不截断
    4. 完整答案 (answer_content) — 不截断
    5. 完整模型输出 (raw_content) — 不截断
"""
import naie._bootstrap  # sys.path + NO_PROXY

import sys
import time
import math
import json
import logging
import argparse

# ─── 设置日志 — 让 budget_forcing 的 logger.info 输出到控制台 ───
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from naie.inference.think_budget import call_model
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47, print_config
from naie.evaluation.runner import parse_json_string, parse_think_content, _translate_accept_reason
from naie.scoring.score import calculate_score
from naie.scoring.llm_judge import evaluate_answer
from transformers import AutoTokenizer

# ─── Monkey-patch 双通道和决策函数, 注入逐轮诊断打印 ───
# 关键: budget_forcing.py 用了 "from ... import" 语法,
# 所以必须 patch budget_forcing 模块命名空间中的直接引用,
# 而非 confidence_gates/early_stopping 模块本身。
from naie.models import budget_forcing as _bf_module
from naie.optimizations import token_filter

_orig_dual_channel = _bf_module.compute_dual_channel_score
_orig_accept = _bf_module.make_accept_decision
_round_counter = [0]  # mutable counter for closure


def _patched_dual_channel_score(reasoning_logprobs, reasoning_tokens,
                                 ans_logprobs, ans_tokens, confidence, perplexity, **kw):
    _round_counter[0] += 1
    rnd = _round_counter[0]

    # ─── 先计算原始结果 ───
    ds, vmr, rsr, asr, vmc = _orig_dual_channel(reasoning_logprobs, reasoning_tokens,
                                            ans_logprobs, ans_tokens, confidence, perplexity, **kw)

    enable_dual = kw.get('enable_dual_channel', False)
    enable_vmc = kw.get('enable_value_min_confidence', False)
    bottom_k = kw.get('value_min_bottom_k', 3)
    prob_th = None  # 不在这里打印, 在 accept_decision 里打印

    # ─── 打印详细诊断 ───
    print(f"\n{'═'*70}")
    print(f"  [Round {rnd}] 置信度评估")
    print(f"{'═'*70}")
    print(f"  平均置信度 (cal_confidence):  {confidence:.4f}")
    print(f"  困惑度 (perplexity):          {perplexity:.4f}")

    # ─── 答案区 token 分析 ───
    if ans_logprobs and ans_tokens:
        value_lps, value_toks = [], []
        struct_lps, struct_toks = [], []
        for lp, tok in zip(ans_logprobs, ans_tokens):
            if lp is None:
                continue
            if token_filter.is_structural_token(tok):
                struct_lps.append(lp)
                struct_toks.append(tok)
            else:
                value_lps.append(lp)
                value_toks.append(tok)

        print(f"\n  ─── 答案区 Token 分析 ───")
        print(f"  总 token 数: {len([lp for lp in ans_logprobs if lp is not None])}")
        print(f"  结构 token 数: {len(struct_lps)}  (过滤, 不参与置信度)")
        print(f"  Value token 数: {len(value_lps)}  (参与置信度计算)")

        if struct_lps:
            avg_struct = sum(math.exp(lp) for lp in struct_lps) / len(struct_lps)
            print(f"  结构 token 平均概率: {avg_struct:.4f}")

        if value_lps:
            avg_value = sum(math.exp(lp) for lp in value_lps) / len(value_lps)
            print(f"  Value token 平均概率: {avg_value:.4f}")

            # Bottom-K 详列
            sorted_pairs = sorted(zip(value_lps, value_toks))
            k = min(bottom_k, len(sorted_pairs))
            print(f"\n  ─── 答案区最低 {k} 个 Value Token ───")
            for j, (lp, tok) in enumerate(sorted_pairs[:k]):
                prob = math.exp(lp)
                print(f"    #{j+1}: token='{tok}' logprob={lp:.4f} prob={prob:.4f}")

            vmc_prob = math.exp(vmr) if vmr is not None else 0.0
            print(f"\n  ★ 答案区 Value 最低概率 (Bottom-{k}均值): {vmc_prob:.4f}  (raw={vmr:.4f})")

    # ─── 推理区分析 (双通道) ───
    if enable_dual and reasoning_logprobs and reasoning_tokens:
        r_value_lps = [lp for lp, tok in zip(reasoning_logprobs, reasoning_tokens)
                       if lp is not None and not token_filter.is_structural_token(tok)]
        print(f"\n  ─── 推理区 Token 分析 (双通道) ───")
        print(f"  推理 Value token 数: {len(r_value_lps)}")
        if r_value_lps:
            r_sorted = sorted(r_value_lps)
            pct = kw.get('reasoning_min_percentile', 0.1)
            k_r = max(1, int(len(r_sorted) * pct))
            print(f"  推理区 Min-{pct*100:.0f}% = 取最低 {k_r} 个:")
            for j, lp in enumerate(r_sorted[:min(k_r, 5)]):  # 最多打印5个
                print(f"    #{j+1}: logprob={lp:.4f} prob={math.exp(lp):.4f}")
            if k_r > 5:
                print(f"    ... (共 {k_r} 个, 省略)")

        r_score = min(math.exp(rsr), 1.0)
        a_score = min(math.exp(asr), 1.0)
        print(f"\n  ★ 推理区置信度: {r_score:.4f}  (raw={rsr:.4f})")
        print(f"  ★ 动作区置信度: {a_score:.4f}  (raw={asr:.4f})")
        print(f"  ★ 决策分数 (AND门): {ds:.4f} = min({r_score:.4f}, {a_score:.4f})")
    else:
        print(f"\n  ★ 决策分数 (decision_score): {ds:.4f}")

    return ds, vmr, rsr, asr, vmc


def _patched_accept_decision(decision_score, prob_th, actual_len, min_think_tokens,
                              is_paired, consecutive_same_count, consecutive_same_stop,
                              enable_value_min_confidence, decision_score_raw,
                              enable_full_chain_confidence=False,
                              reasoning_score_raw=0.0,
                              action_score_raw=0.0,
                              enable_dual_channel=False,
                              deer_should_early_stop=False,
                              deer_stable_count=0,
                              value_min_computed=True,
                              is_final=False):
    should_accept, accept_reason = _orig_accept(
        decision_score, prob_th, actual_len, min_think_tokens,
        is_paired, consecutive_same_count, consecutive_same_stop,
        enable_value_min_confidence, decision_score_raw,
        enable_full_chain_confidence=enable_full_chain_confidence,
        reasoning_score_raw=reasoning_score_raw,
        action_score_raw=action_score_raw,
        enable_dual_channel=enable_dual_channel,
        deer_should_early_stop=deer_should_early_stop,
        deer_stable_count=deer_stable_count,
        value_min_computed=value_min_computed,
        is_final=is_final,
    )

    # ─── 打印决策结果 ───
    vmc_prob = math.exp(decision_score_raw) if decision_score_raw else 0.0
    accept_cn = _translate_accept_reason(accept_reason)
    status = "✓ ACCEPT" if should_accept else "✗ CONTINUE"
    print(f"\n  ─── 决策 ───")
    print(f"  {status}  decision_score={decision_score:.4f} vs prob_th={prob_th}")
    print(f"  采纳原因: {accept_reason} ({accept_cn})")
    print(f"  实际思考 tokens: {actual_len}  最低要求: {min_think_tokens}")
    print(f"  think标签配对: {is_paired}")
    if enable_dual_channel:
        r_score = min(math.exp(reasoning_score_raw), 1.0)
        a_score = min(math.exp(action_score_raw), 1.0)
        print(f"  双通道: 推理={r_score:.4f} 动作={a_score:.4f}")
    print(f"{'═'*70}")

    return should_accept, accept_reason


# 应用 patch — 必须修改 budget_forcing 模块命名空间中的直接引用
# (budget_forcing 用了 "from ... import" 语法, patch 原模块不会生效)
_bf_module.compute_dual_channel_score = _patched_dual_channel_score
_bf_module.make_accept_decision = _patched_accept_decision

# ─── Tokenizer ───
_tokenizer = AutoTokenizer.from_pretrained(NAIE_CONFIG['tokenizer_path'], trust_remote_code=True)


def count_tokens_local(text):
    if not text:
        return 0
    return len(_tokenizer.encode(text, add_special_tokens=False))


def _on_off(val):
    s = str(val).lower()
    return s in ('on', 'true', '1', 'yes')


def main():
    parser = argparse.ArgumentParser(description='单题测试 — 完整打印逐轮置信度')
    parser.add_argument('--model_name', type=str, default='mg2hg', help='模型名 (如 mg2hg, netmo-qwq-32b, Qwen3-32B 等)')
    parser.add_argument('--prob_th', type=float, default=None, help='置信度阈值')
    parser.add_argument('--enable_dual_channel', type=str, default=None, help='双通道 on/off')
    parser.add_argument('--enable_value_min_confidence', type=str, default=None, help='P3.1 on/off')
    parser.add_argument('--value_min_bottom_k', type=int, default=None, help='Bottom-K')
    parser.add_argument('--question', type=str, default=None, help='自定义问题 (覆盖数据集)')
    parser.add_argument('--row', type=int, default=0, help='数据集行号 (默认0)')
    parser.add_argument('--num_thinking', type=int, default=None, help='最大轮次')
    parser.add_argument('--check_length', type=int, default=None, help='初始思考长度')
    args = parser.parse_args()

    # ─── 应用命令行覆盖 ───
    if args.prob_th is not None:
        THINK_BUDGET_CONFIG_47['prob_th'] = args.prob_th
    if args.enable_dual_channel is not None:
        THINK_BUDGET_CONFIG_47['enable_dual_channel'] = _on_off(args.enable_dual_channel)
    if args.enable_value_min_confidence is not None:
        THINK_BUDGET_CONFIG_47['enable_value_min_confidence'] = _on_off(args.enable_value_min_confidence)
    if args.value_min_bottom_k is not None:
        THINK_BUDGET_CONFIG_47['value_min_bottom_k'] = args.value_min_bottom_k
    if args.num_thinking is not None:
        THINK_BUDGET_CONFIG_47['num_thinking'] = args.num_thinking
    if args.check_length is not None:
        THINK_BUDGET_CONFIG_47['check_length'] = args.check_length

    print_config()

    # ─── 固定单题问题 ───
    _DEFAULT_QUESTION = '''提参：
你是电信运营商，核心网部门，负责处理个人用户投诉。现在需要你在投诉相关内容中提取关键信息。
输入：
***********************************
【故障现象】我方联系客户13425105017得知，故障时间：11月1日，前期使用正常，用于4G车机，故障卡数量：1张，表象：使用CMIOT APN无法上网，要求我方处理，如需测试，请联系客户，烦请贵方协助，谢谢！
【故障地点】 浙江宁波
【是否首次使用】否
【是否机卡绑定/贴片卡】 未知
【客户夜间是否配合处理】 否
【终端设备，制式】 4G车机
【客户联系方式】13425105017
【客户经理联系方式】未知
【故障号码信息】经集团360视图核查，故障号码14885939663，IMSI：460138859309663，状态正常，卡类型为USIM卡，开通2、3、4G上网功能，限制2、3G接入，签约APN为CMIOTGACICV.GD,CMIOT，CS域附着时间：2025-10-31 19:31:47，PS域附着时间：2025-09-01 16:21:15，MME域附着时间：2025-11-01 11:03。
策略信息：
22200000100000000000000000005094 业务流量包内（未触发策略，不限速）
22200000100000000000000000005093 业务流量包内（未触发策略，不限速）
22200000100000000000000000005092 业务流量包内（未触发策略，不限速）
22200000100000000000000000005091 业务流量包内（未触发策略，不限速）
22210000100000000000000000000006 业务流量包内（未触发策略，不限速）
22210000100000000000000000000007 业务流量包内（未触发策略，不限速）
21000000100000000000000000000014 业务流量包内（未触发策略，不限速）
22200000100000000000000000000521 业务流量包内（未触发策略，不限速）
21000000100000000000000000000051 业务流量包内（放通）。
***********************************
指令：
需要提取的每个关键信息的含义，以及输出内容格式如下：
1.电话号码：表示用户投诉信息中实际故障的电话号码。号码格式可能是IMPU格式或MSISDN格式。输出字符串，如果没有或无法成功提取则返回空字符串。
2.故障IMSI号码：表示用户投诉信息中实际故障的IMSI格式号码，输出字符串，如果没有则返回空字符串
3.故障URL或域名：URL或域名。输出字符串，如果没有则返回空字符串
4.故障IP：表示用户投诉信息中无法访问的IP，输出字符串，如果没有则返回空字符串
5.故障APN：表示用户投诉信息中的故障APN，输出字符串，如果没有则返回空字符串
6.故障时间：用户投诉发生故障的时间，故障时间可能是某个时间点，也可能是时间段。
时间点输出格式为：yyyy-MM-dd HH:mm:ss，时间段输出格式为：yyyy-MM-dd HH:mm:ss~yyyy-MM-dd HH:mm:ss，如果没有故障时间信息则返回空字符串
注意：
>有明确的故障时间则
只取故障时间即可，没有发生故障的时间可以取投诉时间
>"附着时间"不属于故障时间
>故障时间相关描述中如果无法获取到年、月、日信息，则认为是故障时间是当前年、月、日
>故障时间相关描述中如果包含上午、中午、下午、晚上、凌晨，分别表示特定的时间段，上午认为是当天06:00:00~12:00:00，中午可认为是当天12:00:00~14:00:00，下午认为是当天12:00:00~18:00:00，晚上认为是当天18:00:00~23:59:59，凌晨认为是当天00:00:00~06:00:00
>故障时间相关描述中如果只能具体到某一天，表示一整天的时间段，以时间段格式
输出：yyyy-MM-dd 00:00:00~yyyy-MM-dd 23:59:59
>故障时间有多个时间点，则将最早故障时间和最晚故障时间当做故障起始时间和结束时间，以时间段格式输出：yyyy-MM-dd HH:mm:ss~yyyy-MM-dd HH:mm:ss
7.主被叫信息：如果投诉场景是无法主叫（即拨打电话场景存在问题）则取值为"Y"，如果是无法被叫（即接收电话场景存在问题）则取值为"N"，如果是无法主被叫（即拨打接收电话都存在问题）则取值为"ALL"，如果不存在相关信息则返回空字符串
最后请将上述请将提取出的电话号码、故障IMSI号码、故障URL或域名、故障IP、故障APN、故
障时间、主被叫信息以{“faultNumber”:电话号码,“faultIMSI”:故障IMSI号码,“faultUrl”:故障URL或域名,“faultIp”:故障IP,“faultAPN”:故障APN,“faultTimeRange”:故障时间,“isCaller”:主被叫信息}的json格式输出'''

    # ─── 获取问题 ───
    if args.question:
        question = args.question
        reference = ''
    else:
        question = _DEFAULT_QUESTION
        reference = ''

    print(f"\n{'━'*70}")
    print(f"  单题测试")
    print(f"{'━'*70}")
    print(f"  问题: {question[:300]}")
    if reference:
        print(f"  参考: {reference[:200]}")

    # ─── 重置轮次计数器 ───
    _round_counter[0] = 0

    # ─── 构建调用参数 ───
    # call_model 需要 max_tokens 作为位置参数 (即使 budget_config 中已包含)
    max_tokens_val = THINK_BUDGET_CONFIG_47.get('max_tokens', 65536)
    call_kwargs = {}
    if args.model_name:
        call_kwargs['model_name'] = args.model_name

    print(f"  模型: {args.model_name or '(默认)'}")

    start_time = time.time()
    try:
        response = call_model(question, max_tokens=max_tokens_val, budget_config=THINK_BUDGET_CONFIG_47, **call_kwargs)
        elapsed = time.time() - start_time

        content = response.get('content', '')
        thinking_content, answer_content = parse_think_content(content)
        thinking_tokens = count_tokens_local(thinking_content)
        answer_tokens = count_tokens_local(answer_content)
        total_output_tokens = thinking_tokens + answer_tokens

        predicted = content
        score = 0
        is_correct = False
        if reference:
            score = calculate_score(reference, predicted)
            if score != 10:
                eval_result = evaluate_answer(question, parse_json_string(predicted), reference)
                score = eval_result.get('score', 0)
            is_correct = score == 10

        # ─── 最终汇总 ───
        conf = response.get('confidence')
        ppl = response.get('perplexity')
        vmc_raw = response.get('value_min_confidence')
        vmc_prob = round(math.exp(vmc_raw), 4) if vmc_raw is not None else None
        accept_reason = response.get('accept_reason', '')
        accept_cn = _translate_accept_reason(accept_reason)

        print(f"\n{'━'*70}")
        print(f"  最终结果")
        print(f"{'━'*70}")
        print(f"  正确: {'✓' if is_correct else '✗'}")
        print(f"  分数: {score}")
        print(f"  采纳原因: {accept_reason} ({accept_cn})")
        print(f"  总思考 tokens: {thinking_tokens}")
        print(f"  总 actual_len: {response.get('actual_len', 'N/A')}")
        print(f"  用时: { elapsed }s")
        print(f"  API 调用次数: {response.get('api_call_count', 'N/A')}")
        print(f"  思考轮次: {response.get('thinking_rounds', 'N/A')}")
        print(f"  端到端延迟: {elapsed:.2f}s")
        print(f"\n  ─── 置信度指标 ───")
        print(f"  平均置信度 (confidence):     {f'{conf:.4f}' if conf is not None else 'N/A'}")
        print(f"  困惑度 (perplexity):          {f'{ppl:.4f}' if ppl is not None else 'N/A'}")
        print(f"  Value最低概率 (vmc_prob):     {f'{vmc_prob:.4f}' if vmc_prob is not None else 'N/A'}")
        print(f"  Value最低logprob (vmc_raw):   {f'{vmc_raw:.4f}' if vmc_raw is not None else 'N/A'}")
        print(f"\n  ─── Token 统计 ───")
        print(f"  推理 tokens (thinking):    {thinking_tokens}")
        print(f"  答案 tokens (answer):      {answer_tokens}")
        print(f"  总输出 tokens:             {total_output_tokens}")
        print(f"  actual_len (API返回):      {response.get('actual_len', 'N/A')}")
        print(f"  raw_content 总字符数:      {len(content)}")
        print(f"  thinking_content 字符数:   {len(thinking_content)}")
        print(f"  answer_content 字符数:     {len(answer_content)}")

        print(f"\n{'━'*70}")
        print(f"  完整推理过程 (thinking_content)")
        print(f"{'━'*70}")
        print(thinking_content if thinking_content else "(无推理内容)")

        print(f"\n{'━'*70}")
        print(f"  完整答案 (answer_content)")
        print(f"{'━'*70}")
        print(answer_content if answer_content else "(无答案内容)")

        print(f"\n{'━'*70}")
        print(f"  完整模型输出 (raw_content)")
        print(f"{'━'*70}")
        print(content)

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n{'━'*70}")
        print(f"  ERROR: {e}")
        print(f"{'━'*70}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
