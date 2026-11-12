#!/usr/bin/env python3
"""Run the three checks CI runs, with the tests split across plain `pytest` processes.

    uv run python scripts/check.py
    uv run python scripts/check.py --processes 3

ruff and ty run as CI runs them. pytest lists the tests once, gives each process a share
of the test files by how long each took in the last green run, and runs the processes side
by side; the run is green only when every process exited cleanly and between them they ran
every test the listing found. CI runs plain `uv run pytest -q` in one process, which stays
the baseline.

Logs are kept, and their folder named, only when the run is red.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

#: Never more than half the cores: other checkouts run the suite side by side here, and
#: every test that starts a shell starts processes of its own.
PROCESSES = 2

#: What a test can end as in pytest's last line; `warnings` and `deselected` are not.
OUTCOMES = ("passed", "failed", "skipped", "xfailed", "xpassed", "error", "errors")
SUMMARY = re.compile(r"^=*\s*\d+ \w+.* in [\d.]+s")

#: Seconds per test file from the last green run, inside the folder git already ignores.
TIMES = Path(".pytest_cache") / "kingfisher-check-times.json"


@dataclass(frozen=True)
class Shard:
    """What one pytest process was given, and how it ended."""

    files: tuple[str, ...]
    exit_code: int
    log: str


def listed(root: Path) -> list[str]:
    """Every test id pytest collects with no arguments, as CI's run collects them."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        message = f"pytest could not list the tests:\n{done.stdout}{done.stderr}"
        raise SystemExit(message)
    # The ids end at the first blank line; a warning listed after it can name a test too.
    lines = done.stdout.splitlines()
    return lines[: lines.index("")] if "" in lines else lines


def split(
    ids: list[str], processes: int, times: dict[str, float] | None = None
) -> list[tuple[str, ...]]:
    """The test files in at most `processes` shares, slowest first onto the lightest.

    A file is weighed by its time last run, and one never timed by its tests at the
    average time a test took; with no times at all, every test weighs the same. A share
    is never empty: `pytest` given no files runs the whole suite.
    """
    sizes: dict[str, int] = {}
    for test in ids:
        file = test.split("::", 1)[0]
        sizes[file] = sizes.get(file, 0) + 1
    known = {file: seconds for file, seconds in (times or {}).items() if file in sizes}
    tests_known = sum(sizes[file] for file in known)
    per_test = sum(known.values()) / tests_known if tests_known else 1.0
    weight = {file: known.get(file, sizes[file] * per_test) for file in sizes}
    shares: list[list[str]] = [[] for _ in range(max(1, min(processes, len(sizes))))]
    loads = [0.0] * len(shares)
    for file in sorted(weight, key=lambda f: (-weight[f], f)):
        lightest = loads.index(min(loads))
        shares[lightest].append(file)
        loads[lightest] += weight[file]
    return [tuple(share) for share in shares if share]


def recorded(root: Path) -> dict[str, float]:
    """The times the last green run left, or none when there are none to read."""
    try:
        times = json.loads((root / TIMES).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(times, dict):
        return {}
    return {str(f): float(t) for f, t in times.items() if isinstance(t, (int, float))}


def measured(reports: list[Path]) -> dict[str, float]:
    """Seconds per test file, added up from what each process reported."""
    times: dict[str, float] = {}
    for report in reports:
        tree = ElementTree.parse(report)  # noqa: S314 -- pytest's own report, just written
        for case in tree.iter("testcase"):
            file = case.get("file")
            if file:
                times[file] = times.get(file, 0.0) + float(case.get("time") or 0)
    return times


def counts(log: str) -> dict[str, int] | None:
    """pytest's last line as numbers, or `None` when the run never printed one."""
    lines = [line for line in log.splitlines() if SUMMARY.match(line)]
    if not lines:
        return None
    found = re.findall(r"(\d+) (\w+)", lines[-1])
    return {word: int(n) for n, word in found}


def problems(shards: list[Shard], expected: int) -> list[str]:
    """Why the run is red, or nothing when it is green.

    A count as well as the exit codes: a process killed or crashed partway reports what it
    reached, and the tests it never reached fail nothing.
    """
    found: list[str] = []
    ran = 0
    for number, shard in enumerate(shards, 1):
        summary = counts(shard.log)
        if shard.exit_code != 0:
            found.append(f"process {number} exited {shard.exit_code}")
        if summary is None:
            found.append(f"process {number} printed no summary line")
            continue
        ran += sum(n for word, n in summary.items() if word in OUTCOMES)
    if ran != expected:
        found.append(f"the processes ran {ran} tests of the {expected} listed")
    return found


def run_tests(root: Path, processes: int, logs: Path) -> list[str]:
    """The suite across `processes` pytest processes side by side; why it is red, if it is."""
    ids = listed(root)
    times = recorded(root)
    shares = split(ids, processes, times)
    by = "last run's times" if times else "test count"
    print(f"pytest: {len(ids)} tests in {len(shares)} processes, split by {by}", flush=True)
    started: list[tuple[tuple[str, ...], subprocess.Popen[bytes], Path]] = []
    try:
        for number, files in enumerate(shares, 1):
            log = logs / f"pytest-{number}.log"
            report = logs / f"pytest-{number}.xml"
            with log.open("wb") as out:
                started.append((files, subprocess.Popen(  # noqa: S603 -- our own interpreter
                    [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider",
                     f"--junitxml={report}", "-o", "junit_family=xunit1", *files],
                    cwd=root, stdout=out, stderr=subprocess.STDOUT,
                    # Its own group, so an interrupted run takes every shell a test started.
                    start_new_session=True,
                ), log))
        for _, process, _ in started:
            process.wait()
    finally:
        for _, process, _ in started:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    shards = [
        Shard(files, process.returncode, log.read_text(errors="replace"))
        for files, process, log in started
    ]
    for number, shard in enumerate(shards, 1):
        lines = shard.log.splitlines()
        failures = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
        last = next((line for line in reversed(lines) if SUMMARY.match(line)), "no summary")
        print("\n".join([*failures, f"process {number}: {last.strip('= ')}"]), flush=True)
    found = problems(shards, len(ids))
    if not found:
        # Only a green run's: one cut short would weigh what it never reached as nothing.
        (root / TIMES).parent.mkdir(exist_ok=True)
        (root / TIMES).write_text(json.dumps(
            times | measured([log.with_suffix(".xml") for _, _, log in started]), indent=0
        ))
    return found


def tool(name: str) -> str:
    """The venv's own copy, the one `uv run` would start."""
    return str(Path(sys.executable).parent / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--processes", type=int, default=PROCESSES)
    args = parser.parse_args(argv)
    if args.processes < 1:
        parser.error("--processes takes 1 or more")
    root = Path(__file__).resolve().parents[1]
    began = time.monotonic()

    red: list[str] = []
    for name, command in (
        ("ruff", [tool("ruff"), "check", "src/", "tests/", "assets/", "evals/"]),
        ("ty", [tool("ty"), "check"]),
    ):
        print(f"{name}:", flush=True)
        if subprocess.run(command, cwd=root, check=False).returncode != 0:  # noqa: S603
            red.append(f"{name} failed")

    logs = Path(tempfile.mkdtemp(prefix="kingfisher-check-"))
    found = run_tests(root, args.processes, logs)
    red += found
    if found:
        print(f"pytest logs: {logs}")
    else:
        shutil.rmtree(logs)

    took = time.monotonic() - began
    if red:
        print("\nRED: " + "; ".join(red) + f" ({took:.0f}s)")
        return 1
    print(f"\nGREEN: ruff, ty and pytest all passed ({took:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
