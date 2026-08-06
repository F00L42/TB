#!/usr/bin/env python3
"""Generate reusable benchmark report tables and restrained static charts.

The workflow compares one baseline variant with one candidate variant.  It
discovers scene CSVs, strictly pairs rows by QA_ID, validates the published
summary files, writes auditable tables, and renders overview/per-scene charts
as both SVG and PNG without requiring a plotting framework.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont


SCRIPT_VERSION = "1.1.0"
TIMESTAMP_RE = re.compile(r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

PALETTE = {
    "background": "#F7F8FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F0F3F6",
    "text": "#24313C",
    "muted": "#667581",
    "grid": "#D8DEE4",
    "frame": "#B8C2CB",
    "baseline": "#8A98A5",
    "candidate": "#466F89",
    "token": "#6B858E",
    "positive": "#557A68",
    "positive_fill": "#DFE9E3",
    "negative": "#9A6262",
    "negative_fill": "#F0DEDE",
    "neutral_fill": "#E7EAED",
    "both_fill": "#DCE6ED",
}

SVG_FONT_FAMILY = "Microsoft YaHei, Noto Sans CJK SC, Source Han Sans SC, DejaVu Sans, sans-serif"

REGULAR_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
BOLD_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


@dataclass(frozen=True)
class PairRecord:
    scene_key: str
    scene: str
    slug: str
    qa_id: str
    baseline_e2e_s: float
    candidate_e2e_s: float
    baseline_tokens: float
    candidate_tokens: float
    baseline_correct: bool
    candidate_correct: bool
    baseline_error: str
    candidate_error: str

    @property
    def e2e_gain_s(self) -> float:
        return self.baseline_e2e_s - self.candidate_e2e_s

    @property
    def e2e_gain_pct(self) -> float:
        return 100.0 * self.e2e_gain_s / self.baseline_e2e_s

    @property
    def token_saving(self) -> float:
        return self.baseline_tokens - self.candidate_tokens

    @property
    def token_saving_pct(self) -> float:
        if self.baseline_tokens == 0:
            return 0.0
        return 100.0 * self.token_saving / self.baseline_tokens

    @property
    def transition(self) -> str:
        if self.baseline_correct and self.candidate_correct:
            return "both_correct"
        if self.baseline_correct:
            return "vanilla_only"
        if self.candidate_correct:
            return "candidate_only"
        return "both_wrong"


def _first_existing(candidates: Sequence[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "No CJK-capable font found. Install Microsoft YaHei, Noto Sans CJK, "
        "or configure a supported font before generating charts."
    )


REGULAR_FONT_PATH = _first_existing(REGULAR_FONT_CANDIDATES)
BOLD_FONT_PATH = _first_existing(BOLD_FONT_CANDIDATES)


@lru_cache(maxsize=128)
def pil_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD_FONT_PATH if bold else REGULAR_FONT_PATH), size=size)


def rgba(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    value = color.lstrip("#")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        int(max(0, min(255, round(opacity * 255)))),
    )


class DualCanvas:
    """Record simple vector marks while drawing a matching PNG."""

    def __init__(self, width: int, height: int, title: str, description: str):
        self.width = width
        self.height = height
        self.image = Image.new("RGBA", (width, height), rgba(PALETTE["background"]))
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-desc">',
            f'<title id="chart-title">{html.escape(title)}</title>',
            f'<desc id="chart-desc">{html.escape(description)}</desc>',
            f'<rect width="{width}" height="{height}" fill="{PALETTE["background"]}"/>',
        ]

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str,
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        self.draw.line((x1, y1, x2, y2), fill=rgba(color, opacity), width=max(1, round(width)))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{color}" stroke-width="{width:.2f}" opacity="{opacity:.3f}"{dash_attr}/>'
        )

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.draw.rectangle(
            (x, y, x + width, y + height),
            fill=rgba(fill, opacity) if fill else None,
            outline=rgba(stroke, opacity) if stroke else None,
            width=max(1, round(stroke_width)) if stroke else 1,
        )
        attrs = [f'x="{x:.2f}"', f'y="{y:.2f}"', f'width="{width:.2f}"', f'height="{height:.2f}"']
        attrs.append(f'fill="{fill}"' if fill else 'fill="none"')
        if stroke:
            attrs.extend([f'stroke="{stroke}"', f'stroke-width="{stroke_width:.2f}"'])
        attrs.append(f'opacity="{opacity:.3f}"')
        self.parts.append(f'<rect {" ".join(attrs)}/>')

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        fill: str,
        stroke: str | None = None,
        stroke_width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=rgba(fill, opacity),
            outline=rgba(stroke, opacity) if stroke else None,
            width=max(1, round(stroke_width)) if stroke else 1,
        )
        stroke_attr = f' stroke="{stroke}" stroke-width="{stroke_width:.2f}"' if stroke else ""
        self.parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" '
            f'opacity="{opacity:.3f}"{stroke_attr}/>'
        )

    def polygon(
        self,
        points: Sequence[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        stroke_width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.draw.polygon(
            points,
            fill=rgba(fill, opacity),
            outline=rgba(stroke, opacity) if stroke else None,
        )
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        stroke_attr = f' stroke="{stroke}" stroke-width="{stroke_width:.2f}"' if stroke else ""
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill}" opacity="{opacity:.3f}"{stroke_attr}/>'
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        size: int = 14,
        color: str | None = None,
        anchor: str = "start",
        valign: str = "top",
        bold: bool = False,
        opacity: float = 1.0,
    ) -> None:
        color = color or PALETTE["text"]
        font = pil_font(size, bold)
        bbox = self.draw.textbbox((0, 0), value, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        px = x if anchor == "start" else x - text_w / 2 if anchor == "middle" else x - text_w
        py = y if valign == "top" else y - text_h / 2 if valign == "middle" else y - text_h
        self.draw.text((px, py), value, font=font, fill=rgba(color, opacity))
        svg_anchor = {"start": "start", "middle": "middle", "end": "end"}[anchor]
        svg_y = py
        weight = "500" if bold else "400"
        self.parts.append(
            f'<text x="{x:.2f}" y="{svg_y:.2f}" text-anchor="{svg_anchor}" '
            f'dominant-baseline="text-before-edge" fill="{color}" opacity="{opacity:.3f}" '
            f'font-family="{SVG_FONT_FAMILY}" font-size="{size}" font-weight="{weight}">'
            f'{html.escape(value)}</text>'
        )

    def save(self, base_path: Path) -> list[Path]:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        png_path = base_path.with_suffix(".png")
        svg_path = base_path.with_suffix(".svg")
        self.image.convert("RGB").save(png_path, format="PNG", optimize=True)
        svg_path.write_text("\n".join(self.parts + ["</svg>"]), encoding="utf-8")
        return [svg_path, png_path]


def normalize_scene(filename: str, prefix: str) -> str:
    stem = Path(filename).stem
    if not stem.startswith(prefix):
        raise ValueError(f"{filename!r} does not start with expected prefix {prefix!r}")
    return TIMESTAMP_RE.sub("", stem[len(prefix) :])


def parse_bool(value: str, *, field: str, location: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid boolean {value!r} for {field} at {location}")


def parse_float(value: str, *, field: str, location: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid number {value!r} for {field} at {location}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Non-finite number {value!r} for {field} at {location}")
    return result


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_variant(run_dir: Path, prefix: str) -> dict[str, Path]:
    test_result_dir = run_dir / "test_result"
    if not test_result_dir.is_dir():
        raise FileNotFoundError(f"Missing test_result directory: {test_result_dir}")
    discovered: dict[str, Path] = {}
    for path in sorted(test_result_dir.glob(f"{prefix}*.csv")):
        key = normalize_scene(path.name, prefix)
        if key in discovered:
            raise ValueError(f"Duplicate normalized scene {key!r}: {discovered[key]} and {path}")
        discovered[key] = path
    if not discovered:
        raise ValueError(f"No {prefix}*.csv files found below {test_result_dir}")
    return discovered


def summary_by_scene(run_dir: Path, prefix: str) -> dict[str, dict[str, str]]:
    summary_path = run_dir / "test_result" / "_SUMMARY.csv"
    rows = read_csv_rows(summary_path)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        filename = row.get("结果文件名", "")
        if not filename.startswith(prefix):
            continue
        key = normalize_scene(filename, prefix)
        if key in result:
            raise ValueError(f"Duplicate summary scene {key!r} in {summary_path}")
        result[key] = row
    return result


def _assert_close(actual: float, expected: float, tolerance: float, label: str) -> None:
    if abs(actual - expected) > tolerance:
        raise ValueError(f"{label}: actual {actual:.6f}, expected {expected:.6f}, tolerance {tolerance}")


def reconcile_summary(
    rows: Sequence[dict[str, str]],
    summary: dict[str, str],
    label: str,
) -> None:
    e2e = [parse_float(row["耗时(s)"], field="耗时(s)", location=label) for row in rows]
    tokens = [parse_float(row["Token数"], field="Token数", location=label) for row in rows]
    correct = [parse_bool(row["裁判正确"], field="裁判正确", location=label) for row in rows]
    _assert_close(len(rows), float(summary["裁判有效条目"]), 1e-9, f"{label} row count")
    _assert_close(float(np.mean(e2e)), float(summary["平均时延"]), 0.00011, f"{label} mean E2E")
    _assert_close(float(np.mean(tokens)), float(summary["平均Token数"]), 0.011, f"{label} mean tokens")
    _assert_close(sum(correct), float(summary["裁判正确条目"]), 1e-9, f"{label} correct count")


def load_comparison(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base_dir = (config_path.parent / config["baseline"]["run_dir"]).resolve()
    candidate_dir = (config_path.parent / config["candidate"]["run_dir"]).resolve()
    baseline_prefix = config["baseline"]["prefix"]
    candidate_prefix = config["candidate"]["prefix"]
    baseline_files = discover_variant(base_dir, baseline_prefix)
    candidate_files = discover_variant(candidate_dir, candidate_prefix)
    if set(baseline_files) != set(candidate_files):
        missing_candidate = sorted(set(baseline_files) - set(candidate_files))
        missing_baseline = sorted(set(candidate_files) - set(baseline_files))
        raise ValueError(
            f"Scene mismatch. Missing candidate={missing_candidate}; missing baseline={missing_baseline}"
        )

    label_config = config.get("scenes", {})
    if set(label_config) != set(baseline_files):
        missing_labels = sorted(set(baseline_files) - set(label_config))
        extra_labels = sorted(set(label_config) - set(baseline_files))
        raise ValueError(f"Scene label mismatch. Missing labels={missing_labels}; extras={extra_labels}")

    baseline_summaries = summary_by_scene(base_dir, baseline_prefix)
    candidate_summaries = summary_by_scene(candidate_dir, candidate_prefix)
    all_pairs: list[PairRecord] = []
    pairs_by_scene: dict[str, list[PairRecord]] = {}
    input_files: list[Path] = [
        base_dir / "test_result" / "_SUMMARY.csv",
        candidate_dir / "test_result" / "_SUMMARY.csv",
    ]

    for scene_key in sorted(baseline_files):
        baseline_path = baseline_files[scene_key]
        candidate_path = candidate_files[scene_key]
        input_files.extend([baseline_path, candidate_path])
        baseline_rows = read_csv_rows(baseline_path)
        candidate_rows = read_csv_rows(candidate_path)
        reconcile_summary(baseline_rows, baseline_summaries[scene_key], f"baseline/{scene_key}")
        reconcile_summary(candidate_rows, candidate_summaries[scene_key], f"candidate/{scene_key}")

        baseline_by_id: dict[str, dict[str, str]] = {}
        candidate_by_id: dict[str, dict[str, str]] = {}
        for variant, rows, target in (
            ("baseline", baseline_rows, baseline_by_id),
            ("candidate", candidate_rows, candidate_by_id),
        ):
            for row_number, row in enumerate(rows, start=2):
                qa_id = row.get("QA_ID", "").strip()
                if not qa_id:
                    raise ValueError(f"Blank QA_ID in {variant}/{scene_key} row {row_number}")
                if qa_id in target:
                    raise ValueError(f"Duplicate QA_ID {qa_id!r} in {variant}/{scene_key}")
                target[qa_id] = row
        if set(baseline_by_id) != set(candidate_by_id):
            raise ValueError(f"QA_ID mismatch for {scene_key}")

        scene_info = label_config[scene_key]
        scene_pairs: list[PairRecord] = []
        for qa_id in sorted(baseline_by_id):
            baseline_row = baseline_by_id[qa_id]
            candidate_row = candidate_by_id[qa_id]
            location = f"{scene_key}/{qa_id}"
            pair = PairRecord(
                scene_key=scene_key,
                scene=scene_info["label"],
                slug=scene_info["slug"],
                qa_id=qa_id,
                baseline_e2e_s=parse_float(baseline_row["耗时(s)"], field="耗时(s)", location=location),
                candidate_e2e_s=parse_float(candidate_row["耗时(s)"], field="耗时(s)", location=location),
                baseline_tokens=parse_float(baseline_row["Token数"], field="Token数", location=location),
                candidate_tokens=parse_float(candidate_row["Token数"], field="Token数", location=location),
                baseline_correct=parse_bool(
                    baseline_row["裁判正确"], field="裁判正确", location=f"baseline/{location}"
                ),
                candidate_correct=parse_bool(
                    candidate_row["裁判正确"], field="裁判正确", location=f"candidate/{location}"
                ),
                baseline_error=baseline_row.get("错误", "").strip(),
                candidate_error=candidate_row.get("错误", "").strip(),
            )
            scene_pairs.append(pair)
        pairs_by_scene[scene_key] = scene_pairs
        all_pairs.extend(scene_pairs)

    expected = config.get("expected", {})
    if expected.get("scenes") is not None and len(pairs_by_scene) != expected["scenes"]:
        raise ValueError(f"Expected {expected['scenes']} scenes, found {len(pairs_by_scene)}")
    if expected.get("pairs") is not None and len(all_pairs) != expected["pairs"]:
        raise ValueError(f"Expected {expected['pairs']} pairs, found {len(all_pairs)}")

    return {
        "config": config,
        "config_path": config_path,
        "baseline_dir": base_dir,
        "candidate_dir": candidate_dir,
        "pairs": all_pairs,
        "pairs_by_scene": pairs_by_scene,
        "input_files": input_files,
    }


def pct(values: Sequence[float], quantile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), quantile))


def summarize_pairs(pairs: Sequence[PairRecord], row_type: str, scene: str) -> dict[str, Any]:
    n = len(pairs)
    if not n:
        raise ValueError(f"Cannot summarize empty pair collection: {scene}")
    v_e2e = np.asarray([pair.baseline_e2e_s for pair in pairs], dtype=float)
    f_e2e = np.asarray([pair.candidate_e2e_s for pair in pairs], dtype=float)
    v_tokens = np.asarray([pair.baseline_tokens for pair in pairs], dtype=float)
    f_tokens = np.asarray([pair.candidate_tokens for pair in pairs], dtype=float)
    v_correct = np.asarray([pair.baseline_correct for pair in pairs], dtype=float)
    f_correct = np.asarray([pair.candidate_correct for pair in pairs], dtype=float)
    gains = v_e2e - f_e2e
    token_savings = v_tokens - f_tokens
    transitions = [pair.transition for pair in pairs]
    return {
        "row_type": row_type,
        "scene": scene,
        "n": n,
        "baseline_e2e_mean_s": float(np.mean(v_e2e)),
        "candidate_e2e_mean_s": float(np.mean(f_e2e)),
        "e2e_gain_s": float(np.mean(gains)),
        "e2e_gain_pct": float(100.0 * np.mean(gains) / np.mean(v_e2e)),
        "faster_count": int(np.sum(gains > 0)),
        "faster_pct": float(100.0 * np.mean(gains > 0)),
        "baseline_e2e_p50_s": pct(v_e2e, 50),
        "candidate_e2e_p50_s": pct(f_e2e, 50),
        "baseline_e2e_p95_s": pct(v_e2e, 95),
        "candidate_e2e_p95_s": pct(f_e2e, 95),
        "baseline_visible_tokens_mean": float(np.mean(v_tokens)),
        "candidate_visible_tokens_mean": float(np.mean(f_tokens)),
        "visible_token_saving": float(np.mean(token_savings)),
        "visible_token_saving_pct": float(100.0 * np.mean(token_savings) / np.mean(v_tokens)),
        "baseline_judge_accuracy_pct": float(100.0 * np.mean(v_correct)),
        "candidate_judge_accuracy_pct": float(100.0 * np.mean(f_correct)),
        "accuracy_delta_pp": float(100.0 * (np.mean(f_correct) - np.mean(v_correct))),
        "baseline_correct": int(np.sum(v_correct)),
        "candidate_correct": int(np.sum(f_correct)),
        "both_correct": transitions.count("both_correct"),
        "vanilla_only": transitions.count("vanilla_only"),
        "candidate_only": transitions.count("candidate_only"),
        "both_wrong": transitions.count("both_wrong"),
        "net_correct": int(np.sum(f_correct) - np.sum(v_correct)),
        "client_errors": sum(bool(pair.baseline_error) + bool(pair.candidate_error) for pair in pairs),
        "status": "paired",
    }


def build_summaries(comparison: dict[str, Any]) -> dict[str, Any]:
    baseline_label = comparison["config"]["baseline"]["label"]
    candidate_label = comparison["config"]["candidate"]["label"]
    scene_summaries = []
    for scene_key, pairs in comparison["pairs_by_scene"].items():
        summary = summarize_pairs(pairs, "SCENE", pairs[0].scene)
        summary["scene_key"] = scene_key
        summary["slug"] = pairs[0].slug
        summary["baseline_label"] = baseline_label
        summary["candidate_label"] = candidate_label
        scene_summaries.append(summary)
    scene_summaries.sort(key=lambda row: row["e2e_gain_pct"], reverse=True)

    macro = {
        "row_type": "MACRO",
        "scene": "场景等权宏平均",
        "n": len(scene_summaries),
    }
    for key in (
        "baseline_e2e_mean_s",
        "candidate_e2e_mean_s",
        "e2e_gain_s",
        "faster_pct",
        "baseline_e2e_p50_s",
        "candidate_e2e_p50_s",
        "baseline_e2e_p95_s",
        "candidate_e2e_p95_s",
        "baseline_visible_tokens_mean",
        "candidate_visible_tokens_mean",
        "visible_token_saving",
        "baseline_judge_accuracy_pct",
        "candidate_judge_accuracy_pct",
        "accuracy_delta_pp",
    ):
        macro[key] = float(np.mean([row[key] for row in scene_summaries]))
    # Macro percentages are equal-weight means of the 13 scene-level ratios.
    # Each scene ratio itself uses mean(delta) / mean(baseline), matching RESULT.md.
    macro["e2e_gain_pct"] = float(np.mean([row["e2e_gain_pct"] for row in scene_summaries]))
    macro["visible_token_saving_pct"] = float(
        np.mean([row["visible_token_saving_pct"] for row in scene_summaries])
    )
    for key in (
        "faster_count",
        "baseline_correct",
        "candidate_correct",
        "both_correct",
        "vanilla_only",
        "candidate_only",
        "both_wrong",
        "net_correct",
    ):
        macro[key] = None
    macro["client_errors"] = int(sum(row["client_errors"] for row in scene_summaries))
    macro["status"] = "13 scenes, equal weight"
    macro["baseline_label"] = baseline_label
    macro["candidate_label"] = candidate_label

    micro = summarize_pairs(comparison["pairs"], "MICRO", "请求加权微平均")
    micro["baseline_label"] = baseline_label
    micro["candidate_label"] = candidate_label
    return {
        "scenes": scene_summaries,
        "macro": macro,
        "micro": micro,
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
    }


def validate_expected(summaries: dict[str, Any], expected: dict[str, Any]) -> None:
    micro = summaries["micro"]
    macro = summaries["macro"]
    checks = {
        "faster": micro["faster_count"],
        "baseline_correct": micro["baseline_correct"],
        "candidate_correct": micro["candidate_correct"],
        "client_errors": micro["client_errors"],
    }
    for key, actual in checks.items():
        if key in expected and actual != expected[key]:
            raise ValueError(f"Expected {key}={expected[key]}, found {actual}")
    percentage_checks = {
        "macro_e2e_gain_pct": macro["e2e_gain_pct"],
        "micro_e2e_gain_pct": micro["e2e_gain_pct"],
        "micro_visible_token_saving_pct": micro["visible_token_saving_pct"],
    }
    for key, actual in percentage_checks.items():
        if key in expected:
            _assert_close(actual, float(expected[key]), 0.011, key)


def fmt_number(value: Any, decimals: int = 2) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return f"{float(value):.{decimals}f}"


def allin1_columns(baseline_label: str, candidate_label: str) -> list[tuple[str, str]]:
    return [
        ("row_type", "类型"),
        ("scene", "场景"),
        ("n", "N"),
        ("baseline_e2e_mean_s", f"{baseline_label}平均E2E_s"),
        ("candidate_e2e_mean_s", f"{candidate_label}平均E2E_s"),
        ("e2e_gain_s", "E2E节省_s"),
        ("e2e_gain_pct", "E2E优化_pct"),
        ("faster_count", "更快请求数"),
        ("faster_pct", "更快请求_pct"),
        ("baseline_e2e_p50_s", f"{baseline_label}_P50_s"),
        ("candidate_e2e_p50_s", f"{candidate_label}_P50_s"),
        ("baseline_e2e_p95_s", f"{baseline_label}_P95_s"),
        ("candidate_e2e_p95_s", f"{candidate_label}_P95_s"),
        ("baseline_visible_tokens_mean", f"{baseline_label}平均可见Token"),
        ("candidate_visible_tokens_mean", f"{candidate_label}平均可见Token"),
        ("visible_token_saving", "可见Token节省"),
        ("visible_token_saving_pct", "可见Token节省_pct"),
        ("baseline_judge_accuracy_pct", f"{baseline_label}_Judge正确率_pct"),
        ("candidate_judge_accuracy_pct", f"{candidate_label}_Judge正确率_pct"),
        ("accuracy_delta_pp", "精度变化_pp"),
        ("baseline_correct", f"{baseline_label}正确条目"),
        ("candidate_correct", f"{candidate_label}正确条目"),
        ("vanilla_only", "baseline_only"),
        ("candidate_only", "candidate_only"),
        ("net_correct", "净正确条目"),
        ("client_errors", "客户端错误"),
        ("status", "状态"),
    ]


def output_rows(summaries: dict[str, Any]) -> list[dict[str, Any]]:
    return [*summaries["scenes"], summaries["macro"], summaries["micro"]]


def write_allin1(output_dir: Path, summaries: dict[str, Any]) -> list[Path]:
    rows = output_rows(summaries)
    baseline_label = summaries["baseline_label"]
    candidate_label = summaries["candidate_label"]
    columns = allin1_columns(baseline_label, candidate_label)
    csv_path = output_dir / "allin1.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[label for _, label in columns])
        writer.writeheader()
        for row in rows:
            writer.writerow({label: row.get(key, "") for key, label in columns})

    md_path = output_dir / "allin1.md"
    headers = [
        "类型",
        "场景",
        "N",
        f"平均E2E {baseline_label}→{candidate_label} (s)",
        "时延优化",
        f"平均可见Token {baseline_label}→{candidate_label}",
        "Token节省",
        f"Judge {baseline_label}→{candidate_label}",
        "精度变化",
        "净正确条目",
    ]
    md_lines = [
        f"# {candidate_label} vs {baseline_label}：ALLin1 汇总表",
        "",
        f"> 正向定义：时延优化 = {baseline_label} - {candidate_label}；"
        f"Token 节省 = {baseline_label} - {candidate_label}；",
        f"> 精度变化 = {candidate_label} - {baseline_label}。"
        "可见 Token 不包含隐藏 confidence probe Token。",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        net = fmt_number(row.get("net_correct"), 0)
        faster_count = row.get("faster_count")
        faster = f"；{faster_count}/{row['n']} 更快" if faster_count is not None else ""
        cells = [
            row["row_type"],
            row["scene"],
            fmt_number(row["n"], 0),
            f"{fmt_number(row['baseline_e2e_mean_s'])} → {fmt_number(row['candidate_e2e_mean_s'])}",
            f"{fmt_number(row['e2e_gain_s'])} s / {fmt_number(row['e2e_gain_pct'])}%{faster}",
            f"{fmt_number(row['baseline_visible_tokens_mean'], 1)} → "
            f"{fmt_number(row['candidate_visible_tokens_mean'], 1)}",
            f"{fmt_number(row['visible_token_saving'], 1)} / "
            f"{fmt_number(row['visible_token_saving_pct'])}%",
            f"{fmt_number(row['baseline_judge_accuracy_pct'])}% → "
            f"{fmt_number(row['candidate_judge_accuracy_pct'])}%",
            f"{fmt_number(row['accuracy_delta_pp'])} pp",
            net,
        ]
        md_lines.append("| " + " | ".join(cells) + " |")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return [csv_path, md_path]


def stable_jitter(key: str, amplitude: float) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return (unit - 0.5) * 2.0 * amplitude


def nice_domain(values: Sequence[float], include_zero: bool = True, symmetric: bool = False) -> tuple[float, float]:
    low = float(min(values))
    high = float(max(values))
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if symmetric:
        bound = max(abs(low), abs(high), 1.0)
        low, high = -bound, bound
    span = max(high - low, 1.0)
    pad = span * 0.08
    return low - pad, high + pad


def nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    span = max(high - low, 1e-9)
    raw = span / max(1, count - 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    residual = raw / magnitude
    step = (1 if residual <= 1 else 2 if residual <= 2 else 5 if residual <= 5 else 10) * magnitude
    start = math.floor(low / step) * step
    end = math.ceil(high / step) * step
    ticks = []
    value = start
    while value <= end + step * 0.1:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return ticks


def scale(value: float, domain: tuple[float, float], pixel_range: tuple[float, float]) -> float:
    low, high = domain
    start, end = pixel_range
    if high == low:
        return (start + end) / 2
    return start + (value - low) / (high - low) * (end - start)


def draw_horizontal_axis(
    canvas: DualCanvas,
    domain: tuple[float, float],
    x_range: tuple[float, float],
    y_top: float,
    y_bottom: float,
    unit: str,
    tick_count: int = 5,
) -> None:
    for tick in nice_ticks(*domain, count=tick_count):
        if tick < domain[0] - 1e-9 or tick > domain[1] + 1e-9:
            continue
        x = scale(tick, domain, x_range)
        color = PALETTE["baseline"] if abs(tick) < 1e-9 else PALETTE["grid"]
        canvas.line(x, y_top, x, y_bottom, color, width=1.4 if abs(tick) < 1e-9 else 1.0)
        canvas.text(x, y_top - 24, f"{tick:g}{unit}", 12, PALETTE["muted"], anchor="middle")


def draw_box_strip(
    canvas: DualCanvas,
    values: Sequence[float],
    keys: Sequence[str],
    y: float,
    domain: tuple[float, float],
    x_range: tuple[float, float],
    positive_color: str,
    negative_color: str,
    point_radius: float = 3.0,
) -> None:
    arr = np.asarray(values, dtype=float)
    q1, median, q3 = np.percentile(arr, [25, 50, 75])
    p5, p95 = np.percentile(arr, [5, 95])
    mean = float(np.mean(arr))
    for value, key in zip(values, keys):
        x = scale(value, domain, x_range)
        point_color = positive_color if value >= 0 else negative_color
        canvas.circle(x, y + stable_jitter(key, 11), point_radius, point_color, opacity=0.30)
    x5, x1, xm, x3, x95 = [scale(v, domain, x_range) for v in (p5, q1, median, q3, p95)]
    canvas.line(x5, y, x95, y, PALETTE["baseline"], 1.5)
    canvas.line(x5, y - 6, x5, y + 6, PALETTE["baseline"], 1.5)
    canvas.line(x95, y - 6, x95, y + 6, PALETTE["baseline"], 1.5)
    canvas.rect(x1, y - 12, max(1.0, x3 - x1), 24, PALETTE["surface"], PALETTE["candidate"], 1.5)
    canvas.line(xm, y - 12, xm, y + 12, PALETTE["candidate"], 2.0)
    mean_x = scale(mean, domain, x_range)
    canvas.polygon(
        [(mean_x, y - 7), (mean_x + 7, y), (mean_x, y + 7), (mean_x - 7, y)],
        PALETTE["candidate"],
    )


def render_overview_matrix(output_base: Path, summaries: dict[str, Any]) -> list[Path]:
    baseline_label = summaries["baseline_label"]
    candidate_label = summaries["candidate_label"]
    rows = [*summaries["scenes"], summaries["macro"], summaries["micro"]]
    width = 1740
    row_height = 52
    top = 172
    height = top + row_height * len(rows) + 96
    canvas = DualCanvas(
        width,
        height,
        f"{candidate_label} vs {baseline_label}：13 场景总览",
        "按场景展示端到端时延优化、可见 Token 节省和 Judge 精度变化。",
    )
    canvas.text(28, 24, f"{candidate_label} vs {baseline_label}：13 场景总览", 28, bold=True)
    micro = summaries["micro"]
    macro = summaries["macro"]
    canvas.text(
        28,
        67,
        f"13/13 场景平均更快；请求加权 E2E -{micro['e2e_gain_pct']:.2f}%，"
        f"可见 Token -{micro['visible_token_saving_pct']:.2f}%；"
        f"Judge 宏平均 {macro['accuracy_delta_pp']:+.2f} pp / 微平均 {micro['accuracy_delta_pp']:+.2f} pp",
        16,
        PALETTE["muted"],
    )
    canvas.text(
        28,
        100,
        f"正值表示 {candidate_label} 更优；场景按 E2E 优化率排序",
        13,
        PALETTE["muted"],
    )

    label_x = 28
    panels = [
        ("端到端时延优化", "e2e_gain_pct", (390, 790), PALETTE["candidate"], "%"),
        ("可见 Token 节省", "visible_token_saving_pct", (875, 1275), PALETTE["token"], "%"),
        ("Judge 精度变化", "accuracy_delta_pp", (1360, 1700), None, " pp"),
    ]
    e2e_domain = nice_domain([row["e2e_gain_pct"] for row in rows], include_zero=True)
    token_domain = nice_domain([row["visible_token_saving_pct"] for row in rows], include_zero=True)
    accuracy_domain = nice_domain(
        [row["accuracy_delta_pp"] for row in rows], include_zero=True, symmetric=True
    )
    domains = [e2e_domain, token_domain, accuracy_domain]

    for (title, _, x_range, _, unit), domain in zip(panels, domains):
        canvas.text((x_range[0] + x_range[1]) / 2, 122, title, 16, anchor="middle", bold=True)
        draw_horizontal_axis(canvas, domain, x_range, top - 4, height - 70, unit)

    for index, row in enumerate(rows):
        y = top + index * row_height + row_height / 2
        is_summary = row["row_type"] != "SCENE"
        if is_summary:
            canvas.rect(18, y - row_height / 2 + 2, width - 36, row_height - 4, PALETTE["surface_alt"])
        elif index % 2:
            canvas.rect(18, y - row_height / 2 + 2, width - 36, row_height - 4, PALETTE["surface"], opacity=0.45)
        label = row["scene"]
        n_label = f"N={row['n']}" if row["row_type"] != "MACRO" else "13 场景"
        canvas.text(label_x, y - 13, label, 14, bold=is_summary)
        canvas.text(338, y - 12, n_label, 12, PALETTE["muted"], anchor="end")
        for panel_index, ((_, key, x_range, base_color, _), domain) in enumerate(zip(panels, domains)):
            value = float(row[key])
            x_zero = scale(0.0, domain, x_range)
            x_value = scale(value, domain, x_range)
            if panel_index == 2:
                color = PALETTE["positive"] if value >= 0 else PALETTE["negative"]
            else:
                color = base_color or PALETTE["candidate"]
            canvas.line(x_zero, y, x_value, y, color, 3.0, opacity=0.85)
            canvas.circle(x_value, y, 5.5 if not is_summary else 6.5, color)
            value_label = f"{value:+.2f}" if panel_index == 2 else f"{value:.2f}"
            if x_value > x_range[1] - 48:
                canvas.text(x_value - 9, y - 11, value_label, 12, color, anchor="end", bold=is_summary)
            else:
                canvas.text(x_value + 9, y - 11, value_label, 12, color, bold=is_summary)

    canvas.text(
        28,
        height - 44,
        "可见 Token = 客户端 completion Token，不含隐藏 confidence probe；宏平均为场景等权，微平均为请求等权。",
        12,
        PALETTE["muted"],
    )
    return canvas.save(output_base)


def render_latency_distribution(
    output_base: Path, summaries: dict[str, Any], pairs_by_scene: dict[str, list[PairRecord]]
) -> list[Path]:
    rows = summaries["scenes"]
    all_values = [pair.e2e_gain_pct for pairs in pairs_by_scene.values() for pair in pairs]
    domain = nice_domain(all_values, include_zero=True)
    width = 1640
    row_height = 58
    top = 172
    height = top + row_height * len(rows) + 90
    canvas = DualCanvas(
        width,
        height,
        "请求级 paired E2E 优化分布",
        "每行一个场景，每个点是一条 QA 请求的端到端时延优化率。",
    )
    micro = summaries["micro"]
    baseline_label = summaries["baseline_label"]
    candidate_label = summaries["candidate_label"]
    slower_count = sum(value < 0 for value in all_values)
    canvas.text(28, 24, "请求级 paired E2E 优化分布", 28, bold=True)
    canvas.text(
        28,
        67,
        f"{micro['faster_count']}/{micro['n']}（{micro['faster_pct']:.2f}%）配对请求更快；"
        f"{slower_count} 个请求位于零线左侧",
        16,
        PALETTE["muted"],
    )
    canvas.text(
        28,
        101,
        "每点 = 一条 QA；箱体 = Q1–Q3；须 = P5–P95；竖线 = 中位数；菱形 = 均值",
        12,
        PALETTE["muted"],
    )
    x_range = (390, 1460)
    draw_horizontal_axis(canvas, domain, x_range, top - 6, height - 65, "%", tick_count=7)
    for index, row in enumerate(rows):
        y = top + index * row_height + row_height / 2
        if index % 2:
            canvas.rect(18, y - row_height / 2 + 2, width - 36, row_height - 4, PALETTE["surface"], opacity=0.45)
        pairs = pairs_by_scene[row["scene_key"]]
        values = [pair.e2e_gain_pct for pair in pairs]
        canvas.text(28, y - 11, row["scene"], 14)
        canvas.text(360, y - 10, f"N={len(pairs)}", 12, PALETTE["muted"], anchor="end")
        draw_box_strip(
            canvas,
            values,
            [f"{pair.qa_id}:e2e" for pair in pairs],
            y,
            domain,
            x_range,
            PALETTE["candidate"],
            PALETTE["negative"],
        )
        canvas.text(
            1608,
            y - 10,
            f"{row['faster_count']}/{row['n']} 更快",
            12,
            PALETTE["muted"],
            anchor="end",
        )
    canvas.text(
        28,
        height - 42,
        f"优化率 = ({baseline_label} E2E - {candidate_label} E2E) / {baseline_label} E2E。",
        12,
        PALETTE["muted"],
    )
    return canvas.save(output_base)


def render_accuracy_transitions(output_base: Path, summaries: dict[str, Any]) -> list[Path]:
    rows = summaries["scenes"]
    micro = summaries["micro"]
    baseline_label = summaries["baseline_label"]
    candidate_label = summaries["candidate_label"]
    unchanged = micro["both_correct"] + micro["both_wrong"]
    unchanged_pct = 100.0 * unchanged / micro["n"]
    max_count = max(
        max(row["vanilla_only"], row["candidate_only"]) for row in [*rows, micro]
    )
    width = 1540
    row_height = 58
    top = 174
    height = top + row_height * (len(rows) + 1) + 82
    canvas = DualCanvas(
        width,
        height,
        "Judge 配对结果转移",
        f"每行展示 {baseline_label}-only 正确、{candidate_label}-only 正确和净正确条目变化。",
    )
    canvas.text(28, 24, "Judge 配对结果转移", 28, bold=True)
    canvas.text(
        28,
        67,
        f"{unchanged}/{micro['n']}（{unchanged_pct:.2f}%）结果未翻转；"
        f"{baseline_label}-only {micro['vanilla_only']} 条，"
        f"{candidate_label}-only {micro['candidate_only']} 条，净变化 {micro['net_correct']:+d} 条",
        16,
        PALETTE["muted"],
    )
    canvas.text(
        28,
        102,
        f"左侧 = {baseline_label}-only 正确；右侧 = {candidate_label}-only 正确",
        12,
        PALETTE["muted"],
    )

    center_x = 870
    half_width = 330
    canvas.line(center_x, top - 8, center_x, height - 60, PALETTE["baseline"], 1.5)
    tick_step = 5 if max_count >= 20 else 2 if max_count >= 6 else 1
    for tick in range(0, max_count + 1, tick_step):
        delta = half_width * tick / max_count if max_count else 0
        for x, label in ((center_x - delta, tick), (center_x + delta, tick)):
            canvas.line(x, top - 8, x, height - 60, PALETTE["grid"], 1.0)
            canvas.text(x, top - 31, str(label), 12, PALETTE["muted"], anchor="middle")

    display_rows = [*rows, micro]
    for index, row in enumerate(display_rows):
        y = top + index * row_height + row_height / 2
        is_summary = row["row_type"] == "MICRO"
        if is_summary:
            canvas.rect(18, y - row_height / 2 + 2, width - 36, row_height - 4, PALETTE["surface_alt"])
        elif index % 2:
            canvas.rect(18, y - row_height / 2 + 2, width - 36, row_height - 4, PALETTE["surface"], opacity=0.45)
        canvas.text(28, y - 11, row["scene"], 14, bold=is_summary)
        if not is_summary:
            canvas.text(345, y - 10, f"N={row['n']}", 12, PALETTE["muted"], anchor="end")
        left_count = row["vanilla_only"]
        right_count = row["candidate_only"]
        left_w = half_width * left_count / max_count if max_count else 0
        right_w = half_width * right_count / max_count if max_count else 0
        canvas.rect(center_x - left_w, y - 10, left_w, 20, PALETTE["negative"])
        canvas.rect(center_x, y - 10, right_w, 20, PALETTE["positive"])
        canvas.text(center_x - left_w - 8, y - 10, str(left_count), 12, PALETTE["negative"], anchor="end")
        canvas.text(center_x + right_w + 8, y - 10, str(right_count), 12, PALETTE["positive"])
        canvas.text(
            1510,
            y - 11,
            f"净 {row['net_correct']:+d}；{row['accuracy_delta_pp']:+.2f} pp",
            12,
            PALETTE["positive"] if row["net_correct"] >= 0 else PALETTE["negative"],
            anchor="end",
            bold=is_summary,
        )
    regression_count = sum(row["accuracy_delta_pp"] < 0 for row in rows)
    canvas.text(
        28,
        height - 40,
        f"净变化接近 0 不等于每个场景都非劣；{regression_count}/{len(rows)} 场景正确条目下降。",
        12,
        PALETTE["muted"],
    )
    return canvas.save(output_base)


def draw_scene_gain_panel(
    canvas: DualCanvas,
    pairs: Sequence[PairRecord],
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> None:
    e2e_values = [pair.e2e_gain_pct for pair in pairs]
    token_values = [pair.token_saving_pct for pair in pairs]
    domain = nice_domain([*e2e_values, *token_values], include_zero=True)
    x_range = (x0 + 145, x0 + width - 28)
    draw_horizontal_axis(canvas, domain, x_range, y0 + 64, y0 + height - 30, "%", tick_count=5)
    canvas.text(x0 + 18, y0 + 14, "配对收益分布", 17, bold=True)
    for label, values, y, color, suffix in (
        ("E2E 优化", e2e_values, y0 + 128, PALETTE["candidate"], "e2e"),
        ("Token 节省", token_values, y0 + 224, PALETTE["token"], "token"),
    ):
        canvas.text(x0 + 18, y - 12, label, 13)
        draw_box_strip(
            canvas,
            values,
            [f"{pair.qa_id}:{suffix}" for pair in pairs],
            y,
            domain,
            x_range,
            color,
            PALETTE["negative"],
            point_radius=3.2,
        )


def draw_judge_matrix(
    canvas: DualCanvas,
    summary: dict[str, Any],
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> None:
    baseline_label = summary["baseline_label"]
    candidate_label = summary["candidate_label"]
    canvas.text(x0 + 18, y0 + 14, "Judge 配对转移", 17, bold=True)
    matrix_x = x0 + 180
    matrix_y = y0 + 92
    cell_w = 205
    cell_h = 72
    canvas.text(
        matrix_x + cell_w,
        y0 + 52,
        candidate_label,
        13,
        PALETTE["muted"],
        anchor="middle",
    )
    canvas.text(matrix_x + cell_w / 2, y0 + 72, "正确", 12, PALETTE["muted"], anchor="middle")
    canvas.text(matrix_x + cell_w * 1.5, y0 + 72, "错误", 12, PALETTE["muted"], anchor="middle")
    canvas.text(
        x0 + 84,
        matrix_y + cell_h,
        baseline_label,
        13,
        PALETTE["muted"],
        anchor="middle",
    )
    canvas.text(x0 + 152, matrix_y + cell_h / 2 - 8, "正确", 12, PALETTE["muted"], anchor="end")
    canvas.text(x0 + 152, matrix_y + cell_h * 1.5 - 8, "错误", 12, PALETTE["muted"], anchor="end")
    cells = [
        (0, 0, "both correct", summary["both_correct"], PALETTE["both_fill"]),
        (1, 0, "baseline-only", summary["vanilla_only"], PALETTE["negative_fill"]),
        (0, 1, "candidate-only", summary["candidate_only"], PALETTE["positive_fill"]),
        (1, 1, "both wrong", summary["both_wrong"], PALETTE["neutral_fill"]),
    ]
    for col, row, label, count, fill in cells:
        cx = matrix_x + col * cell_w
        cy = matrix_y + row * cell_h
        canvas.rect(cx, cy, cell_w - 6, cell_h - 6, fill, PALETTE["frame"], 1.0)
        canvas.text(cx + 14, cy + 10, label, 12, PALETTE["muted"])
        canvas.text(cx + cell_w - 20, cy + 28, str(count), 25, anchor="end", bold=True)
    color = PALETTE["positive"] if summary["net_correct"] >= 0 else PALETTE["negative"]
    canvas.text(
        x0 + 18,
        y0 + height - 48,
        f"精度变化 {summary['accuracy_delta_pp']:+.2f} pp；净正确条目 {summary['net_correct']:+d}",
        14,
        color,
        bold=True,
    )


def render_scene_detail(
    output_base: Path,
    summary: dict[str, Any],
    pairs: Sequence[PairRecord],
) -> list[Path]:
    baseline_label = summary["baseline_label"]
    candidate_label = summary["candidate_label"]
    width, height = 1660, 980
    canvas = DualCanvas(
        width,
        height,
        f"{summary['scene']}：{candidate_label} vs {baseline_label}",
        "逐请求展示端到端时延配对关系、时延与可见 Token 收益分布，以及 Judge 配对转移。",
    )
    canvas.text(28, 24, summary["scene"], 28, bold=True)
    canvas.text(
        28,
        66,
        f"N={summary['n']}；平均 E2E {summary['baseline_e2e_mean_s']:.2f}s → "
        f"{summary['candidate_e2e_mean_s']:.2f}s（-{summary['e2e_gain_pct']:.2f}%）；"
        f"可见 Token -{summary['visible_token_saving_pct']:.2f}%；"
        f"Judge {summary['accuracy_delta_pp']:+.2f} pp",
        16,
        PALETTE["muted"],
    )
    canvas.text(
        28,
        96,
        f"P50 {summary['baseline_e2e_p50_s']:.2f}s → {summary['candidate_e2e_p50_s']:.2f}s；"
        f"P95 {summary['baseline_e2e_p95_s']:.2f}s → {summary['candidate_e2e_p95_s']:.2f}s；"
        f"{summary['faster_count']}/{summary['n']} 请求更快",
        12,
        PALETTE["muted"],
    )

    # Parity panel.
    panel_x, panel_y, panel_w, panel_h = 28, 130, 770, 790
    canvas.rect(panel_x, panel_y, panel_w, panel_h, PALETTE["surface"], PALETTE["frame"], 1.0)
    canvas.text(panel_x + 18, panel_y + 14, "E2E 配对关系", 17, bold=True)
    plot_x0, plot_y0 = panel_x + 92, panel_y + 82
    plot_size = 620
    max_value = max(
        max(pair.baseline_e2e_s for pair in pairs),
        max(pair.candidate_e2e_s for pair in pairs),
    )
    domain = (0.0, max_value * 1.06)
    ticks = nice_ticks(*domain, count=6)
    for tick in ticks:
        if tick < domain[0] or tick > domain[1]:
            continue
        x = scale(tick, domain, (plot_x0, plot_x0 + plot_size))
        y = scale(tick, domain, (plot_y0 + plot_size, plot_y0))
        canvas.line(x, plot_y0, x, plot_y0 + plot_size, PALETTE["grid"], 1.0)
        canvas.line(plot_x0, y, plot_x0 + plot_size, y, PALETTE["grid"], 1.0)
        canvas.text(x, plot_y0 + plot_size + 12, f"{tick:g}", 11, PALETTE["muted"], anchor="middle")
        canvas.text(plot_x0 - 12, y - 8, f"{tick:g}", 11, PALETTE["muted"], anchor="end")
    canvas.rect(plot_x0, plot_y0, plot_size, plot_size, None, PALETTE["frame"], 1.0)
    canvas.line(plot_x0, plot_y0 + plot_size, plot_x0 + plot_size, plot_y0, PALETTE["baseline"], 1.5, "6 5")
    for pair in pairs:
        x = scale(pair.baseline_e2e_s, domain, (plot_x0, plot_x0 + plot_size))
        y = scale(pair.candidate_e2e_s, domain, (plot_y0 + plot_size, plot_y0))
        color = PALETTE["candidate"] if pair.e2e_gain_s > 0 else PALETTE["negative"]
        canvas.circle(x, y, 4.0, color, stroke=PALETTE["surface"], stroke_width=0.8, opacity=0.58)
    canvas.text(
        plot_x0 + plot_size / 2,
        plot_y0 + plot_size + 42,
        f"{baseline_label} E2E（s）",
        13,
        anchor="middle",
    )
    canvas.text(plot_x0, plot_y0 - 28, f"{candidate_label} E2E（s）↑", 13)
    canvas.text(
        plot_x0 + plot_size - 10,
        plot_y0 + 16,
        f"等值线上方：{candidate_label} 更慢",
        11,
        PALETTE["negative"],
        anchor="end",
    )
    canvas.text(
        plot_x0 + 10,
        plot_y0 + plot_size - 30,
        f"{summary['faster_count']}/{summary['n']} 更快",
        12,
        PALETTE["candidate"],
        bold=True,
    )

    right_x = 830
    canvas.rect(right_x, 130, 802, 350, PALETTE["surface"], PALETTE["frame"], 1.0)
    draw_scene_gain_panel(canvas, pairs, right_x, 130, 802, 350)
    canvas.rect(right_x, 510, 802, 410, PALETTE["surface"], PALETTE["frame"], 1.0)
    draw_judge_matrix(canvas, summary, right_x, 510, 802, 410)
    canvas.text(
        28,
        944,
        "可见 Token 不含隐藏 confidence probe；所有点均按 QA_ID 严格配对。",
        12,
        PALETTE["muted"],
    )
    return canvas.save(output_base)


def write_scene_index(scene_dir: Path, summaries: dict[str, Any]) -> Path:
    baseline_label = summaries["baseline_label"]
    candidate_label = summaries["candidate_label"]
    lines = [
        f"# {candidate_label} vs {baseline_label}：逐场景图",
        "",
        "以下 13 个场景与总览图使用相同排序和指标方向。",
        "",
    ]
    for index, row in enumerate(summaries["scenes"], start=1):
        lines.extend(
            [
                f"## {index}. {row['scene']}",
                "",
                f"![{row['scene']}](./{row['slug']}.png)",
                "",
            ]
        )
    path = scene_dir / "index.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_workbook_source(
    output_dir: Path,
    comparison: dict[str, Any],
    summaries: dict[str, Any],
) -> Path:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scene_order = {row["scene_key"]: index for index, row in enumerate(summaries["scenes"])}
    pairs = sorted(comparison["pairs"], key=lambda pair: (scene_order[pair.scene_key], pair.qa_id))
    offsets: dict[str, dict[str, int]] = {}
    cursor = 2
    for row in summaries["scenes"]:
        n = len(comparison["pairs_by_scene"][row["scene_key"]])
        offsets[row["scene_key"]] = {"start_row": cursor, "end_row": cursor + n - 1}
        cursor += n
    source = {
        "schema": 1,
        "comparison_id": comparison["config"]["comparison_id"],
        "baseline_label": comparison["config"]["baseline"]["label"],
        "candidate_label": comparison["config"]["candidate"]["label"],
        "baseline_run_dir": str(comparison["baseline_dir"]),
        "candidate_run_dir": str(comparison["candidate_dir"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "palette": PALETTE,
        "scene_summaries": [dict(row, **offsets[row["scene_key"]]) for row in summaries["scenes"]],
        "macro": summaries["macro"],
        "micro": summaries["micro"],
        "pairs": [asdict(pair) for pair in pairs],
    }
    path = data_dir / "allin1_workbook_source.json"
    path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def git_metadata(repo_root: Path) -> dict[str, Any]:
    def run_git(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=repo_root, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = run_git("status", "--porcelain")
    return {
        "commit": run_git("rev-parse", "HEAD"),
        "branch": run_git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def write_manifest(
    output_dir: Path,
    repo_root: Path,
    comparison: dict[str, Any],
    summaries: dict[str, Any],
    profile: str,
    revision: str,
    output_files: Sequence[Path],
) -> Path:
    manifest_path = output_dir / "report_manifest.json"
    manifest = {
        "schema": 1,
        "generator": "docs/report/chart/generate_report.py",
        "generator_version": SCRIPT_VERSION,
        "comparison_id": comparison["config"]["comparison_id"],
        "profile": profile,
        "revision": revision,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence": "measured",
        "git": git_metadata(repo_root),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pillow": Image.__version__,
            "font_regular": str(REGULAR_FONT_PATH),
            "font_bold": str(BOLD_FONT_PATH),
        },
        "inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in comparison["input_files"]
        ],
        "metrics": {
            "e2e_gain": "mean(vanilla_e2e - candidate_e2e)",
            "e2e_gain_pct": "mean(gain) / mean(vanilla_e2e)",
            "visible_token_saving": "mean(vanilla_completion_tokens - candidate_completion_tokens)",
            "visible_token_saving_pct": "mean(saving) / mean(vanilla_completion_tokens)",
            "accuracy_delta_pp": "100 * (candidate_correct_rate - vanilla_correct_rate)",
            "pairing": "normalized scene key + QA_ID",
        },
        "actual": {
            "scenes": len(summaries["scenes"]),
            "pairs": summaries["micro"]["n"],
            "faster": summaries["micro"]["faster_count"],
            "baseline_correct": summaries["micro"]["baseline_correct"],
            "candidate_correct": summaries["micro"]["candidate_correct"],
            "macro_e2e_gain_pct": summaries["macro"]["e2e_gain_pct"],
            "micro_e2e_gain_pct": summaries["micro"]["e2e_gain_pct"],
            "micro_visible_token_saving_pct": summaries["micro"]["visible_token_saving_pct"],
            "macro_accuracy_delta_pp": summaries["macro"]["accuracy_delta_pp"],
            "micro_accuracy_delta_pp": summaries["micro"]["accuracy_delta_pp"],
        },
        "palette": PALETTE,
        "outputs": [
            {"path": str(path.relative_to(output_dir)), "sha256": sha256_file(path)}
            for path in sorted(set(output_files))
            if path.exists()
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def ensure_output_dir(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(
            f"Output revision already exists and is not empty: {path}. Use a new --revision value."
        )
    path.mkdir(parents=True, exist_ok=True)


def generate(config_path: Path, profile: str, revision: str) -> Path:
    comparison = load_comparison(config_path)
    summaries = build_summaries(comparison)
    validate_expected(summaries, comparison["config"].get("expected", {}))
    output_root = (config_path.parent / comparison["config"]["output_root"]).resolve()
    output_dir = output_root / comparison["config"]["comparison_id"] / revision
    ensure_output_dir(output_dir)
    output_files: list[Path] = []
    output_files.extend(write_allin1(output_dir, summaries))
    output_files.append(write_workbook_source(output_dir, comparison, summaries))

    figure_root = output_dir / "figures"
    if profile in {"overview", "all"}:
        overview_dir = figure_root / "overview"
        output_files.extend(render_overview_matrix(overview_dir / "01_overview_matrix", summaries))
        output_files.extend(
            render_latency_distribution(
                overview_dir / "02_latency_gain_distribution",
                summaries,
                comparison["pairs_by_scene"],
            )
        )
        output_files.extend(
            render_accuracy_transitions(overview_dir / "03_accuracy_transitions", summaries)
        )
    if profile in {"scenes", "all"}:
        scene_dir = figure_root / "scenes"
        for row in summaries["scenes"]:
            output_files.extend(
                render_scene_detail(
                    scene_dir / row["slug"],
                    row,
                    comparison["pairs_by_scene"][row["scene_key"]],
                )
            )
        output_files.append(write_scene_index(scene_dir, summaries))

    repo_root = Path(__file__).resolve().parents[3]
    write_manifest(output_dir, repo_root, comparison, summaries, profile, revision, output_files)
    return output_dir


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Comparison JSON configuration")
    parser.add_argument(
        "--profile",
        choices=("overview", "scenes", "all"),
        default="all",
        help="Output scope",
    )
    parser.add_argument("--revision", required=True, help="Non-overwriting output revision, e.g. v1")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = generate(args.config.resolve(), args.profile, args.revision)
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
