"""`scripts/check.py` splits the suite across plain pytest processes and adds them up."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

CHECK = Path(__file__).resolve().parents[3] / "scripts" / "check.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kingfisher_check_script", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclass resolves its annotations through it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = load()

GREEN = "4 passed, 1 skipped, 2 warnings in 1.20s"


def test_every_file_lands_in_exactly_one_process_and_none_is_handed_nothing():
    """A file left out is a test nobody ran, and a process handed no files runs the whole
    suite, since pytest given no paths collects everything."""
    ids = [f"tests/t{n % 5}.py::test_{n}" for n in range(23)]

    for processes in (1, 2, 3, 9):
        shares = check.split(ids, processes)
        files = [file for share in shares for file in share]
        assert sorted(files) == sorted({i.split("::")[0] for i in ids})
        assert len(files) == len(set(files))
        assert 0 < len(shares) <= processes
        assert all(shares)


@pytest.mark.parametrize(
    ("logs", "codes", "expected"),
    [
        pytest.param([GREEN, "3 failed, 2 passed in 1.0s"], [0, 1], 10, id="a failure"),
        pytest.param([GREEN, "Killed: 9"], [0, -9], 10, id="a process killed"),
        pytest.param([GREEN, "a crash before the last line"], [0, 0], 10, id="no last line"),
        pytest.param([GREEN, "2 passed in 1.0s"], [0, 0], 10, id="tests never reached"),
        pytest.param([GREEN, "no tests ran in 0.01s"], [0, 5], 5, id="nothing collected"),
    ],
)
def test_a_red_process_is_never_read_as_green(logs, codes, expected):
    """Exit codes alone pass a process that died before printing anything wrong, and a
    count alone passes one whose failure was in teardown."""
    shards = [check.Shard(("t.py",), code, log) for log, code in zip(logs, codes, strict=True)]

    assert check.problems(shards, expected)
    assert check.problems([check.Shard(("t.py",), 0, GREEN)] * 2, 10) == [], (
        "the control: two clean processes that ran every test listed"
    )


def test_a_real_failure_in_one_process_turns_the_whole_run_red(tmp_path):
    """Driven through real pytest processes rather than built logs, so a summary line pytest
    no longer prints the way the parser expects is caught here and not by a green that lied.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    for n in range(3):
        (tmp_path / f"test_ok{n}.py").write_text("def test_ok():\n    pass\n")
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    logs = tmp_path / "logs"
    logs.mkdir()

    found = check.run_tests(tmp_path, 2, logs)

    assert found in (["process 1 exited 1"], ["process 2 exited 1"]), found

    (tmp_path / "test_bad.py").unlink()
    assert check.run_tests(tmp_path, 2, logs) == [], "the control: the same run, all passing"
