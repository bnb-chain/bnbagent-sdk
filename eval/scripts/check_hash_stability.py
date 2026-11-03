#!/usr/bin/env python3
"""Check the determinism fixture hashes identically on Python 3.10-3.12.

For each interpreter, ``uv`` creates a throwaway venv (downloading the interpreter if needed),
installs ``eval[dev]`` and runs ``eval/tests/determinism/_fixture.py``. The script fails if the
``result_hash`` / ``suite_hash`` differ between versions or from the pinned golden file.

    python3 eval/scripts/check_hash_stability.py                 # compare against the golden file
    python3 eval/scripts/check_hash_stability.py --write-golden  # (re)pin the golden hashes
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent  # eval/
GOLDEN = PACKAGE / "tests" / "determinism" / "golden_hashes.json"
VERSIONS = ("3.10", "3.11", "3.12")


def find_uv() -> str:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).exists():
        sys.exit("uv not found (install it or put it on PATH)")
    return uv


def hashes_for(uv: str, version: str, workdir: Path) -> dict:
    venv = workdir / f"py{version}"
    run = {"capture_output": True, "text": True}
    subprocess.run([uv, "venv", "-q", "-p", version, str(venv)], check=True, **run)
    python = venv / "bin" / "python"
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "-q",
            "--python",
            str(python),
            "--no-sources",
            f"{PACKAGE}[dev]",
        ],
        check=True,
        **run,
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "BNBAGENT_EVAL"))}
    # Append (not prepend) eval/ to sys.path: `tests.determinism` imports from the checkout while
    # `bnbagent_eval` resolves to the copy installed in the throwaway venv.
    code = (
        f"import sys; sys.path.append({str(PACKAGE)!r}); "
        "from tests.determinism._fixture import main; main([])"
    )
    out = subprocess.run([str(python), "-c", code], cwd=workdir, env=env, check=True, **run)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    version_out = subprocess.run([str(python), "-V"], check=True, **run).stdout.strip()
    return {"python": version_out, **data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--versions", nargs="+", default=list(VERSIONS))
    parser.add_argument("--write-golden", action="store_true")
    args = parser.parse_args()
    uv = find_uv()
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="hash-stability-") as tmp:
        for version in args.versions:
            print(f"[{version}] creating venv and installing eval[dev] ...", flush=True)
            try:
                results[version] = hashes_for(uv, version, Path(tmp))
            except subprocess.CalledProcessError as exc:
                print(
                    f"[{version}] failed: {exc}\n{exc.stdout}\n{exc.stderr}",
                    file=sys.stderr,
                )
                return 2
            r = results[version]
            print(f"[{version}] {r['python']}: result_hash={r['result_hash']}")
            print(f"[{version}] {' ' * len(r['python'])}  suite_hash={r['suite_hash']}")
    pairs = {(r["result_hash"], r["suite_hash"]) for r in results.values()}
    if len(pairs) != 1:
        print("FAIL: hashes differ between Python versions", file=sys.stderr)
        return 1
    result_hash, suite_hash = pairs.pop()
    if args.write_golden:
        golden = {
            "result_hash": result_hash,
            "suite_hash": suite_hash,
            "checked_on": sorted(r["python"] for r in results.values()),
        }
        GOLDEN.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
        print(f"pinned {GOLDEN.relative_to(PACKAGE.parent)}")
        return 0
    if GOLDEN.exists():
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        if (golden["result_hash"], golden["suite_hash"]) != (result_hash, suite_hash):
            print("FAIL: hashes are stable across versions but differ from the golden file")
            return 1
    print(f"OK: identical hashes on {', '.join(args.versions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
