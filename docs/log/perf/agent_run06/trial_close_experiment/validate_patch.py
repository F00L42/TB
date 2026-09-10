"""Apply experimental patch in a temporary copy; leave production files intact."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
TESTS = [
    "tests/test_dtb_config.py", "tests/test_dtb_state_machine.py",
    "tests/test_dtb_token_prompt.py", "tests/test_dtb_telemetry.py",
    "tests/test_dtb_intervention.py",
]


def main():
    tests = [p for p in TESTS if (REPO/p).exists()]
    assert len(tests)==len(TESTS), "missing requested regression file"
    env = {**os.environ, "PYTHONIOENCODING":"utf-8"}
    with tempfile.TemporaryDirectory(prefix="dtb-trial-close-") as directory:
        target = Path(directory)
        for name in ("dtb", "tests", "tools"):
            shutil.copytree(REPO/name,target/name,ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        env["PYTHONPATH"] = str(target)
        results = []
        for variant in ("original", "closed_trial"):
            if variant=="closed_trial":
                subprocess.run(["git","apply",str(ROOT/"closed_trial.patch")],cwd=target,
                               check=True,stdin=subprocess.DEVNULL)
            cmd = [sys.executable,"-m","pytest","-q",*tests]
            completed = subprocess.run(cmd,cwd=target,env=env,stdin=subprocess.DEVNULL,
                                       capture_output=True,text=True,encoding="utf8",errors="replace")
            result = {"variant":variant,"command":["python","-m","pytest","-q",*tests],
                      "exit_code":completed.returncode,"stdout":completed.stdout,"stderr":completed.stderr}
            results.append(result)
            print(variant,completed.returncode,completed.stdout,completed.stderr,flush=True)
            if completed.returncode:
                break
        (ROOT/"local_validation.json").write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
        return results[-1]["exit_code"]


if __name__=="__main__":
    raise SystemExit(main())
