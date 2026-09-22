#!/usr/bin/env bash
# Run from the TB repository root, after restarting the same server with mode=off.
set -euo pipefail
STAGE=${1:?Usage: bash path/to/run_off_validation.sh smoke|main}
case "$STAGE" in smoke|main) ;; *) exit 2 ;; esac
python - <<'PY'
from pathlib import Path
import yaml
cfg = yaml.safe_load(Path('dtb/config/budget_config.yaml').read_text())
assert cfg['OBSERVABILITY']['mode'] == 'off', 'Set mode=off AND restart the server first'
PY
BASE=docs/log/perf/noemate_analysis_60f2d7
AUDIT=docs/log/perf/noemate_activation_audit_20260922T031321Z
python "$BASE/prepare_replay_dataset.py"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="docs/log/perf/noemate_guardrail_${STAGE}_${STAMP}"
mkdir "$OUT"
cp "$AUDIT/selection.json" "$OUT/selection.json"
cp dtb/config/budget_config.yaml "$OUT/budget_config.yaml"
cp "$0" "$OUT/executed_plan.sh"
git rev-parse HEAD > "$OUT/client_commit.txt"
git diff -- dtb tools/dtb/replay_tool_calls.py patches > "$OUT/client_worktree.patch"
COMMON=(--file "$BASE/replay_dataset/pool.jsonl"
  --url http://127.0.0.1:9001/v1/chat/completions --stream --order alternating
  --seed 20260918 --timeout 1800 --fixed-budget-tokens 750 --fixed-check-length 750
  --fixed-think-stride 256 --fixed-num-thinking 4 --fixed-min-think-tokens 1 --fixed-prob-th 0.95)

run_case() {
  local group=$1 line=$2 reps=$3 mode=$4
  shift 4
  python tools/dtb/replay_tool_calls.py "${COMMON[@]}" --lines "$line" \
    --mode "$mode" --repetitions 1 "$@" \
    --request-id-run "${STAGE}_${STAMP}_${group}_l${line}_warm" \
    --output-dir "$OUT/${group}_l${line}_warm"
  python tools/dtb/replay_tool_calls.py "${COMMON[@]}" --lines "$line" \
    --mode "$mode" --repetitions "$reps" "$@" \
    --request-id-run "${STAGE}_${STAMP}_${group}_l${line}_measured" \
    --output-dir "$OUT/${group}_l${line}_measured"
}

if [[ "$STAGE" == smoke ]]; then
  for line in 21 319 365; do run_case smoke "$line" 2 both; done
else
  # Freeze these sets; do not drop slow, changed-output or truncated observations.
  for line in 319 178 259 353 399; do run_case long "$line" 10 both; done
  # Consecutive attempts (1,2), (3,4), ... form the native A/A noise pairs.
  for line in 21 321 365 394; do run_case short_aa "$line" 40 vanilla --temperature 0; done
  for line in 21 321 365 394; do run_case short_greedy "$line" 20 both --temperature 0; done
  for line in 21 321 365 394; do run_case short_sampling "$line" 20 both; done
fi
printf 'Saved evidence: %s\n' "$OUT"
