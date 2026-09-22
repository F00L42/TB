"""Apply patches only in a temporary fixture, then compile all touched Python.

The supplied upstream checkout remains read-only. No vLLM import is required.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

from build_patch import VLLM_SHA, build

PACKAGE = Path(__file__).resolve().parents[1]
PATCHES = [PACKAGE / "patches/vllm-v0.23.0" / name for name in (
    "observer_serving.patch", "qwen3_coder_parser.patch", "qwen3_reasoning_parser.patch",
)]


def command(args, cwd):
    return subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def patched_tree(upstream, destination):
    expected = build(upstream)
    if expected != PATCHES[0].read_text(encoding="utf-8"):
        raise ValueError("serving patch differs from build_patch.py output")
    paths = set()
    for patch in PATCHES:
        for line in patch.read_text(encoding="utf-8").splitlines():
            if line.startswith("--- a/"):
                paths.add(line[6:])
    for name in paths:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exercise Linux deployment line endings even from a Windows checkout.
        target.write_text((upstream / name).read_text(encoding="utf-8"),
                          encoding="utf-8", newline="\n")
    command(["git", "apply", "--check", *map(str, PATCHES)], destination)
    command(["git", "apply", *map(str, PATCHES)], destination)
    for patch in PATCHES:
        for line in patch.read_text(encoding="utf-8").splitlines():
            if line.startswith("+++ b/") and line.endswith(".py"):
                name = line[6:]
                compile((destination / name).read_text(encoding="utf-8"), name, "exec")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("upstream", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="observer-patch-") as temp:
        paths = patched_tree(args.upstream.resolve(), Path(temp))
    print(f"PASS: 3 patches applied and Python compiled; upstream={VLLM_SHA}; files={len(paths)}")
