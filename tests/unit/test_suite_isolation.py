"""What keeps a developer's machine out of the suite: their `.env`, and one test's
environment reaching the next.
"""

from __future__ import annotations

import ast
import os
from importlib import import_module

import dotenv

from tests.conftest import (
    READS_DOTENV,
    environment_restored,
    refusing,
    repository_root,
)


def test_the_guard_answers_absent_for_its_file_and_reads_any_other(tmp_path, monkeypatch):
    """The checkout's `.env` cannot be written here without overwriting a developer's,
    so the guard is handed a file of this test's own -- which is also what makes this
    bite on a CI runner that has no `.env` at all.
    """
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    (mine / ".env").write_text("ISOLATION_PROBE=leaked\n", encoding="utf-8")
    (theirs / ".env").write_text("ISOLATION_PROBE=read\n", encoding="utf-8")
    monkeypatch.delenv("ISOLATION_PROBE", raising=False)
    load = refusing((mine / ".env").resolve())

    monkeypatch.chdir(mine)
    assert load(".env", override=False) is False, "named the way `main()` names it"
    assert load() is False, "bare, the way the driver calls it"
    assert "ISOLATION_PROBE" not in os.environ

    assert load(theirs / ".env") is True, "a file a test wrote for itself still loads"
    assert os.environ["ISOLATION_PROBE"] == "read"


def test_what_a_test_sets_outside_monkeypatch_is_put_back(monkeypatch):
    """`monkeypatch` restores only what it recorded, and `load_dotenv` records nothing."""
    monkeypatch.setenv("ISOLATION_KEPT", "before")
    monkeypatch.setenv("ISOLATION_GONE", "before")
    monkeypatch.delenv("ISOLATION_ADDED", raising=False)

    with environment_restored():
        os.environ["ISOLATION_KEPT"] = "changed"
        del os.environ["ISOLATION_GONE"]
        os.environ["ISOLATION_ADDED"] = "new"

    assert os.environ["ISOLATION_KEPT"] == "before"
    assert os.environ["ISOLATION_GONE"] == "before"
    assert "ISOLATION_ADDED" not in os.environ


def test_every_test_runs_behind_the_guard(request):
    """The two helpers above are only as good as the fixture that applies them to every
    test, and a fixture that stopped being autouse would leave both tests passing.
    """
    assert "isolated_from_the_developer" in request.fixturenames

    for module in READS_DOTENV:
        assert import_module(module).load_dotenv is not dotenv.load_dotenv, (
            f"{module} still holds the real `load_dotenv`"
        )


def test_every_module_that_loads_a_dotenv_file_is_guarded():
    """A third caller of `load_dotenv` would read the checkout's `.env` in every test
    that reached it, and the guard would never know the module existed.
    """
    root = repository_root()
    calling = set()
    for folder in ("src", "tests", "evals"):
        for path in (root / folder).rglob("*.py"):
            if path.name == "conftest.py":
                continue  # the guard, which calls the real one on purpose
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "load_dotenv"
                for node in ast.walk(tree)
            ):
                relative = path.relative_to(root / "src" if folder == "src" else root)
                calling.add(".".join(relative.with_suffix("").parts))

    assert calling, "no module calls `load_dotenv` -- this rule is about nothing"
    assert calling == set(READS_DOTENV)
