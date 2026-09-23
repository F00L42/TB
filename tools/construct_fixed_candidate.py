"""Construct a section-6.2 fixed Think Budget candidate from Vanilla output.

Exact usage on the model server::

    python tools/construct_fixed_candidate.py artifacts/perf/run_<id> \
      --vanilla-result test_result/vanilla_<scene>_<timestamp>.csv \
      --tokenizer /path/to/model \
      --candidate-label fixed_v1 \
      --output-dir artifacts/fixed_candidates

When the exact tokenizer is unavailable, omit ``--tokenizer``.  The fallback
uses the matching DTB result and its extracted ``requests.jsonl`` to calibrate
the token count of the answer segment.  It is intended for candidate seeding;
the server-side exact-tokenizer result remains authoritative.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence


RESPONSE_COLUMN = "模型回答"
REQUEST_ID_COLUMN = "request_id"
COMPLETION_TOKENS_COLUMN = "Token数"
THINK_END_MARKER = "</think>"
TIMESTAMP_SUFFIX = re.compile(
    r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
VARIANT_PREFIX = re.compile(r"^(?:qp_)?(?:vanilla|dtb)_")
CANDIDATE_HEADER = re.compile(r"(?m)^===== ([^=\r\n]+) =====\r?$")


@dataclass(frozen=True)
class ResultRow:
    request_id: str
    response: str
    completion_tokens: int


@dataclass(frozen=True)
class CalibrationRow:
    result: ResultRow
    thought: str
    answer: str
    visible_think_tokens: int
    final_answer_tokens: int


@dataclass(frozen=True)
class LinearModel:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]

    def predict(self, values: Sequence[float]) -> float:
        normalized = [
            (value - mean) / scale
            for value, mean, scale in zip(values, self.means, self.scales)
        ]
        return self.coefficients[0] + sum(
            coefficient * value
            for coefficient, value in zip(
                self.coefficients[1:], normalized)
        )


@dataclass(frozen=True)
class FixedCandidate:
    enable_think_budget: bool
    fixed_prob_th: float
    fixed_check_length: int
    fixed_think_stride: int
    fixed_num_thinking: int
    fixed_min_think_tokens: int
    fixed_budget_tokens: int

    def to_text(self) -> str:
        enabled = str(self.enable_think_budget).lower()
        return (
            f"enable_think_budget = {enabled}\n"
            f"fixed_prob_th = {self.fixed_prob_th:.2f}\n"
            f"fixed_check_length = {self.fixed_check_length}\n"
            f"fixed_think_stride = {self.fixed_think_stride}\n"
            f"fixed_num_thinking = {self.fixed_num_thinking}\n"
            f"fixed_min_think_tokens = {self.fixed_min_think_tokens}\n"
            f"fixed_budget_tokens = {self.fixed_budget_tokens}\n"
        )


def candidate_block(label: str, candidate: FixedCandidate) -> str:
    clean_label = label.strip()
    if not clean_label or "\n" in clean_label or "\r" in clean_label:
        raise ValueError("candidate label must be one non-empty line")
    if "=" in clean_label:
        raise ValueError("candidate label must not contain '='")
    return f"===== {clean_label} =====\n{candidate.to_text()}"


def upsert_candidate(
    path: Path, label: str, candidate: FixedCandidate
) -> None:
    block = candidate_block(label, candidate)
    if not path.exists():
        content = block
    else:
        existing = path.read_text(encoding="utf-8")
        headers = list(CANDIDATE_HEADER.finditer(existing))
        target_index = next(
            (
                index
                for index, header in enumerate(headers)
                if header.group(1).strip() == label.strip()
            ),
            None,
        )
        if target_index is None:
            content = existing.rstrip() + "\n\n" + block
        else:
            start = headers[target_index].start()
            end = (
                headers[target_index + 1].start()
                if target_index + 1 < len(headers)
                else len(existing)
            )
            content = existing[:start].rstrip() + "\n\n" + block
            if end < len(existing):
                content += "\n" + existing[end:].lstrip("\r\n")
            content = content.lstrip("\r\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def round_to_quantum(value: float, quantum: int = 50) -> int:
    units = (Decimal(str(value)) / Decimal(quantum)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    return int(units * quantum)


def construct_candidate(t50: float) -> FixedCandidate:
    if t50 <= 0:
        raise ValueError("T50 must be positive")
    check_length = min(2000, max(100, round_to_quantum(0.20 * t50)))
    think_stride = min(1000, max(50, round_to_quantum(0.10 * t50)))
    budget_tokens = max(check_length, round_to_quantum(0.50 * t50))
    num_thinking = max(
        1, math.ceil((budget_tokens - check_length) / think_stride))
    return FixedCandidate(
        enable_think_budget=True,
        fixed_prob_th=0.93,
        fixed_check_length=check_length,
        fixed_think_stride=think_stride,
        fixed_num_thinking=num_thinking,
        fixed_min_think_tokens=check_length,
        fixed_budget_tokens=budget_tokens,
    )


def percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def split_response(response: str) -> tuple[str, str]:
    if THINK_END_MARKER not in response:
        raise ValueError(f"response does not contain {THINK_END_MARKER!r}")
    thought, answer = response.split(THINK_END_MARKER, 1)
    if "<think>" in thought:
        thought = thought.rsplit("<think>", 1)[1]
    return thought, answer


def read_result_rows(path: Path) -> list[ResultRow]:
    rows: list[ResultRow] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            REQUEST_ID_COLUMN,
            RESPONSE_COLUMN,
            COMPLETION_TOKENS_COLUMN,
        }
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{path} is missing required columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                completion_tokens = int(float(row[COMPLETION_TOKENS_COLUMN]))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{path}:{row_number} has invalid completion tokens") from error
            rows.append(ResultRow(
                request_id=(row[REQUEST_ID_COLUMN] or "").strip(),
                response=row[RESPONSE_COLUMN] or "",
                completion_tokens=completion_tokens,
            ))
    if not rows:
        raise ValueError(f"{path} has no result rows")
    return rows


def text_features(text: str) -> tuple[float, ...]:
    # Mutually exclusive character classes reduce dependence on a specific
    # language mix.  The final two run counts improve English/code estimation.
    counts = [0.0] * 7
    for char in text:
        if "\u3400" <= char <= "\u9fff":
            index = 0
        elif char.isascii() and char.isalpha():
            index = 1
        elif char.isdigit():
            index = 2
        elif char.isspace():
            index = 3
        elif unicodedata.category(char).startswith("P"):
            index = 4
        elif char.isascii():
            index = 5
        else:
            index = 6
        counts[index] += 1
    english_word_runs = len(re.findall(r"[A-Za-z]+", text))
    nonspace_runs = len(re.findall(r"[^\s]+", text))
    return (*counts, float(english_word_runs), float(nonspace_runs))


def solve_linear_system(
    matrix: list[list[float]], vector: list[float]
) -> list[float]:
    size = len(vector)
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(
            augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("calibration matrix is singular")
        augmented[column], augmented[pivot] = (
            augmented[pivot], augmented[column])
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(
                    augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def fit_linear_model(
    examples: Sequence[tuple[Sequence[float], float]],
    ridge: float = 1e-8,
) -> LinearModel:
    if len(examples) < 2:
        raise ValueError("at least two calibration examples are required")
    feature_count = len(examples[0][0])
    means = tuple(
        statistics.mean(example[0][column] for example in examples)
        for column in range(feature_count)
    )
    scales = tuple(
        statistics.pstdev(example[0][column] for example in examples) or 1.0
        for column in range(feature_count)
    )
    design = [
        [1.0] + [
            (value - mean) / scale
            for value, mean, scale in zip(values, means, scales)
        ]
        for values, _ in examples
    ]
    targets = [target for _, target in examples]
    width = feature_count + 1
    normal = [[0.0] * width for _ in range(width)]
    rhs = [0.0] * width
    for row, target in zip(design, targets):
        for left in range(width):
            rhs[left] += row[left] * target
            for right in range(width):
                normal[left][right] += row[left] * row[right]
    for diagonal in range(1, width):
        normal[diagonal][diagonal] += ridge
    coefficients = solve_linear_system(normal, rhs)
    return LinearModel(means, scales, tuple(coefficients))


def scene_key(path: Path) -> str:
    stem = VARIANT_PREFIX.sub("", path.stem)
    return TIMESTAMP_SUFFIX.sub("", stem)


def resolve_result(run_dir: Path, value: str) -> Path:
    supplied = Path(value)
    candidates = [supplied, run_dir / supplied]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    matches = [
        path for path in run_dir.rglob(supplied.name)
        if path.is_file() and "extracted" not in path.parts
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one result matching {value!r} below {run_dir}, "
            f"found {len(matches)}")
    return matches[0].resolve()


def matching_dtb_result(run_dir: Path, vanilla_result: Path) -> Path:
    key = scene_key(vanilla_result)
    dtb_prefix = (
        "qp_dtb_" if vanilla_result.name.startswith("qp_vanilla_")
        else "dtb_"
    )
    matches = [
        path for path in run_dir.rglob("*.csv")
        if (
            "extracted" not in path.parts
            and path.name.startswith(dtb_prefix)
            and scene_key(path) == key
        )
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one DTB calibration result for scene {key!r}, "
            f"found {len(matches)}")
    return matches[0]


def request_ledger(run_dir: Path, result: Path) -> Path:
    preferred = run_dir / "extracted" / result.stem / "requests.jsonl"
    if preferred.is_file():
        return preferred
    matches = [
        path for path in run_dir.rglob("requests.jsonl")
        if path.parent.name == result.stem
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one requests.jsonl for {result.name}, "
            f"found {len(matches)}")
    return matches[0]


def load_calibration_rows(
    result_path: Path, ledger_path: Path
) -> list[CalibrationRow]:
    ledger: dict[str, dict[str, object]] = {}
    with ledger_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            ledger[str(record["parent_request_id"])] = record
    rows: list[CalibrationRow] = []
    for result in read_result_rows(result_path):
        if result.request_id not in ledger:
            raise ValueError(
                f"request {result.request_id!r} is absent from {ledger_path}")
        thought, answer = split_response(result.response)
        record = ledger[result.request_id]
        rows.append(CalibrationRow(
            result=result,
            thought=thought,
            answer=answer,
            visible_think_tokens=int(record["visible_think_tokens"]),
            final_answer_tokens=int(record["final_answer_tokens"]),
        ))
    return rows


def validation_summary(rows: Sequence[CalibrationRow]) -> dict[str, float]:
    folds = min(5, len(rows))
    errors: list[float] = []
    predictions: list[float] = []
    actuals: list[float] = []
    for fold in range(folds):
        training = [row for index, row in enumerate(rows) if index % folds != fold]
        testing = [row for index, row in enumerate(rows) if index % folds == fold]
        model = fit_linear_model([
            (text_features(row.answer), row.final_answer_tokens)
            for row in training
        ])
        residual = statistics.median(
            row.result.completion_tokens
            - row.visible_think_tokens
            - row.final_answer_tokens
            for row in training
        )
        for row in testing:
            predicted_answer = max(0.0, model.predict(
                text_features(row.answer)))
            predicted_thought = (
                row.result.completion_tokens - predicted_answer - residual)
            predictions.append(predicted_thought)
            actuals.append(float(row.visible_think_tokens))
            errors.append(predicted_thought - row.visible_think_tokens)
    absolute_errors = [abs(error) for error in errors]
    return {
        "mae_tokens": statistics.mean(absolute_errors),
        "median_absolute_error_tokens": statistics.median(absolute_errors),
        "p95_absolute_error_tokens": percentile(absolute_errors, 0.95),
        "bias_tokens": statistics.mean(errors),
        "predicted_t50_tokens": statistics.median(predictions),
        "actual_t50_tokens": statistics.median(actuals),
    }


def calibrated_thought_counts(
    vanilla_rows: Sequence[ResultRow],
    calibration_rows: Sequence[CalibrationRow],
) -> tuple[list[float], dict[str, float]]:
    model = fit_linear_model([
        (text_features(row.answer), row.final_answer_tokens)
        for row in calibration_rows
    ])
    residual = statistics.median(
        row.result.completion_tokens
        - row.visible_think_tokens
        - row.final_answer_tokens
        for row in calibration_rows
    )
    counts: list[float] = []
    for row in vanilla_rows:
        _, answer = split_response(row.response)
        predicted_answer = max(0.0, model.predict(text_features(answer)))
        counts.append(max(
            0.0, row.completion_tokens - predicted_answer - residual))
    return counts, {
        "ledger_residual_median_tokens": float(residual),
        **validation_summary(calibration_rows),
    }


def exact_thought_counts(
    rows: Sequence[ResultRow], tokenizer_path: str, trust_remote_code: bool
) -> list[float]:
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError(
            "exact mode requires transformers in the server environment") from error
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path, trust_remote_code=trust_remote_code)
    return [
        float(len(tokenizer.encode(
            split_response(row.response)[0], add_special_tokens=False)))
        for row in rows
    ]


def summarize_counts(counts: Sequence[float]) -> dict[str, float]:
    return {
        "min": percentile(counts, 0.0),
        "p25": percentile(counts, 0.25),
        "p50": percentile(counts, 0.50),
        "p75": percentile(counts, 0.75),
        "p95": percentile(counts, 0.95),
        "max": percentile(counts, 1.0),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--vanilla-result", required=True)
    parser.add_argument(
        "--tokenizer",
        help="model/tokenizer path; omit to use the DTB phase-calibrated fallback",
    )
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--candidate-label", default="fixed_v1")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/fixed_candidates"),
        help=(
            "candidate directory; filename is always derived from the original "
            "test-set name"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the calculation without writing the candidate file",
    )
    parser.add_argument("--report-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = args.run_dir.resolve()
    vanilla_result = resolve_result(run_dir, args.vanilla_result)
    vanilla_rows = read_result_rows(vanilla_result)
    report: dict[str, object] = {
        "schema": 1,
        "run_dir": str(run_dir),
        "scene": scene_key(vanilla_result),
        "vanilla_result": str(vanilla_result),
        "sample_count": len(vanilla_rows),
    }
    if args.tokenizer:
        counts = exact_thought_counts(
            vanilla_rows, args.tokenizer, args.trust_remote_code)
        report.update({
            "token_count_mode": "exact_tokenizer",
            "tokenizer": args.tokenizer,
        })
    else:
        dtb_result = matching_dtb_result(run_dir, vanilla_result)
        ledger = request_ledger(run_dir, dtb_result)
        calibration_rows = load_calibration_rows(dtb_result, ledger)
        counts, validation = calibrated_thought_counts(
            vanilla_rows, calibration_rows)
        report.update({
            "token_count_mode": "dtb_phase_calibrated_estimate",
            "dtb_result": str(dtb_result),
            "request_ledger": str(ledger),
            "calibration_sample_count": len(calibration_rows),
            "calibration_validation": validation,
        })
    distribution = summarize_counts(counts)
    candidate = construct_candidate(distribution["p50"])
    report["vanilla_thought_tokens"] = distribution
    report["calculation"] = {
        "check_raw": 0.20 * distribution["p50"],
        "stride_raw": 0.10 * distribution["p50"],
        "budget_raw": 0.50 * distribution["p50"],
        "derived_max_visible_think_tokens": (
            candidate.fixed_check_length
            + candidate.fixed_num_thinking * candidate.fixed_think_stride
        ),
    }
    report["candidate"] = asdict(candidate)

    output_path = args.output_dir / f"{scene_key(vanilla_result)}.txt"
    report["candidate_label"] = args.candidate_label
    report["output_file"] = str(output_path.resolve())
    if not args.dry_run:
        upsert_candidate(output_path, args.candidate_label, candidate)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n" + candidate_block(args.candidate_label, candidate), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
