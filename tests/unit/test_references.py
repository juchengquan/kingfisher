"""Names that came from outside, and where they may land."""

from __future__ import annotations

from pathlib import Path

from kingfisher import UnsafeReferenceError
from kingfisher.domain.references import within


def test_an_ordinary_name_lands_under_the_root():
    escaped = [
        name
        for name in ("a.csv", "sub/a.csv", "./a.csv")
        if Path("/root") not in within(Path("/root"), name).parents
    ]
    assert not escaped, f"{escaped} did not land under the root"


def test_a_name_that_leaves_the_root_is_refused():
    """One function rather than a check each caller remembers, because the session
    store and the backend both join a name from outside onto a directory.
    """
    allowed = []
    for name in ("../a", "a/../../b", "/etc/passwd", "C:\\Windows\\x", "", ".", ".."):
        try:
            within(Path("/root"), name)
        except UnsafeReferenceError:
            continue
        allowed.append(name)

    assert not allowed, f"{allowed} were joined onto the root instead of refused"


def test_the_rule_never_asks_the_filesystem(tmp_path):
    """Lexical by necessity: the domain may not touch the filesystem, and `resolve` is a
    syscall.
    """
    nowhere = tmp_path / "does" / "not" / "exist"

    assert within(nowhere, "a.csv") == nowhere / "a.csv"
    assert not nowhere.exists()
