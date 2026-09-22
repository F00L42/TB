"""Install the wheel in a fresh environment and run tests outside the repository."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def run(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required for this packaging verification tool only")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert not any(n.split("/")[0] in ("dtb", "deer") for n in names)
        metadata = archive.read(next(n for n in names if n.endswith("/METADATA"))).decode()
        assert "Requires-Dist:" not in metadata
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    with tempfile.TemporaryDirectory(prefix="observer-install-") as temp:
        root = Path(temp)
        run([uv, "venv", "--python", sys.executable, str(root / "venv")], root, env)
        python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], root, env)
        # Copy only SDK tests; no repository algorithms or source package on sys.path.
        shutil.copytree(Path(__file__).resolve().parents[1] / "tests", root / "tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        output = run([str(python), "-m", "unittest", "discover", "-s", "tests", "-v"], root, env)
        print(output.strip())
    print("PASS: wheel installed without dependencies; tests ran outside repository")
