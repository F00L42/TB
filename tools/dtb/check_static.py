"""Run the reproducible local DTB delivery gate without a model or server."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ("dtb_serving.patch", "qwen3_coder_parser.patch", "qwen3_reasoning_parser.patch")
NAVIGATION = ("README.md", "AGENTS.md", "RESULT.md", "dtb/README.md", "dtb/AGENTS.md",
              "vllm_gateway/README.md", "vllm_gateway/AGENTS.md", "validation/replay/README.md",
              *[str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md")])


def check_navigation():
    """Check local link targets in maintained entrypoints, not historical prose."""
    checked = 0
    for name in NAVIGATION:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", text):
            target = target.strip("<>")
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            relative = unquote(target.split("#", 1)[0])
            if not (path.parent / relative).exists():
                raise RuntimeError(f"Broken navigation link: {name} -> {target}")
            checked += 1
    print(f"PASS: {checked} local navigation link targets (anchors not checked)", flush=True)


def check_fixtures():
    manifest = json.loads((ROOT / "tests/fixtures/provenance.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        content = (ROOT / entry["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"Fixture changed without provenance update: {entry['path']}")
    print(f"PASS: {len(manifest['files'])} preserved fixture hashes", flush=True)


def run(command, *, capture=False):
    print("+ " + " ".join(str(arg) for arg in command), flush=True)
    return subprocess.run(
        [str(arg) for arg in command], cwd=ROOT, stdin=subprocess.DEVNULL,
        check=True, capture_output=capture, text=True, encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true", help="also compare CPU work and frozen route results")
    args = parser.parse_args()
    check_navigation()
    check_fixtures()
    # Check the checkout against this commit's gitlink, not a moving branch/tag.
    for name in ("vllm", "vllm-ascend"):
        relative = f"third_party/{name}"
        if not (ROOT / relative / ".git").exists():
            raise RuntimeError("Missing fixed dependency; run git submodule update --init --recursive")
        expected = run(["git", "rev-parse", f"HEAD:{relative}"], capture=True).stdout.strip()
        actual = run(["git", "-C", relative, "rev-parse", "HEAD"], capture=True).stdout.strip()
        dirty = run(["git", "-C", relative, "status", "--porcelain"], capture=True).stdout.strip()
        if expected != actual or dirty:
            raise RuntimeError(f"{relative} must be clean at gitlink {expected}; got {actual}, dirty={bool(dirty)}")
    run(["git", "diff", "--check"])
    run([sys.executable, "-m", "compileall", "-q", "dtb", "vllm_gateway", "tools", "tests"])
    for name in PATCHES:
        run(["git", "-C", "third_party/vllm", "apply", "--check",
             ROOT / "patches/vllm/v0.23.0" / name])
    # A unique task-local path avoids shared Windows pytest temp permissions;
    # pytest may clear --basetemp, so never accept an arbitrary user directory.
    temporary = ROOT / ".pytest_cache" / ("dtb-check-" + uuid4().hex)
    run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "tests",
         f"--basetemp={temporary}", "--tb=short"])
    if args.benchmark:
        run([sys.executable, "-X", "utf8", "tools/dtb/static_cpu_benchmark.py",
             "--replay", "--output", ROOT / ".pytest_cache/dtb-static-cpu.json"])
    print("PASS: local static/no-model gate. Target-server validation is pending_remote.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise SystemExit(1)
