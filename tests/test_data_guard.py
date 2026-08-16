from __future__ import annotations

import os
from pathlib import Path

import pytest

from kingfisher.infrastructure.workspace_fs import (
    DataError,
    place_data,
    place_inputs,
    protect_data,
    writable_data,
)


def test_data_becomes_read_only_to_the_os(workspace):
    """The layer the deny rule cannot provide: the kernel enforces this against
    `execute` too, which tool-level permissions never covered."""
    with writable_data(workspace) as data:
        (data / "input.csv").write_text("a,b\n1,2\n")

    protect_data(workspace)

    with pytest.raises(PermissionError):
        (workspace / "data" / "input.csv").write_text("clobbered")


def test_directory_write_bit_is_dropped_so_files_cannot_be_deleted(workspace):
    """Deletion is governed by the directory's write bit, not the file's."""
    with writable_data(workspace) as data:
        (data / "input.csv").write_text("x")
    protect_data(workspace)

    assert not os.access(workspace / "data", os.W_OK)


def test_writable_data_restores_protection_afterwards(workspace):
    with writable_data(workspace) as data:
        (data / "new.csv").write_text("y")
        assert os.access(data, os.W_OK)

    assert not os.access(workspace / "data", os.W_OK)


def test_protect_data_is_idempotent(workspace):
    protect_data(workspace)
    protect_data(workspace)
    assert not os.access(workspace / "data", os.W_OK)


def _refuse(name: str, monkeypatch):
    """Make `chmod` refuse one file, the way the kernel does for a file we do
    not own. `chmod(2)` returns EPERM to anyone who is not the owner, so a
    single input copied in by another user reproduces this exactly."""
    real = Path.chmod

    def chmod(self, mode, **kwargs):
        if self.name == name:
            raise PermissionError(1, "Operation not permitted", str(self))
        return real(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)


def test_a_file_we_cannot_chmod_is_reported_not_raised(workspace, monkeypatch):
    """One file owned by another user used to abort the run -- and since this
    runs before anything else, every later run of that session too."""
    with writable_data(workspace) as data:
        (data / "theirs.pdf").write_text("x")
        (data / "ours.csv").write_text("y")

    _refuse("theirs.pdf", monkeypatch)
    failures = protect_data(workspace)

    assert len(failures) == 1
    assert "theirs.pdf" in failures[0]
    assert "Operation not permitted" in failures[0]


def test_the_rest_of_the_directory_is_still_hardened(workspace, monkeypatch):
    """Degrading is only acceptable if it degrades to *almost* protected."""
    with writable_data(workspace) as data:
        (data / "theirs.pdf").write_text("x")
        (data / "ours.csv").write_text("y")

    _refuse("theirs.pdf", monkeypatch)
    protect_data(workspace)
    monkeypatch.undo()

    assert not os.access(workspace / "data", os.W_OK)
    with pytest.raises(PermissionError):
        (workspace / "data" / "ours.csv").write_text("clobbered")


def test_an_input_can_still_be_added_beside_a_file_we_do_not_own(workspace, monkeypatch):
    """Refusing a new input because an unrelated old one belongs to someone
    else would be its own bug."""
    with writable_data(workspace) as data:
        (data / "theirs.pdf").write_text("x")

    _refuse("theirs.pdf", monkeypatch)
    with writable_data(workspace) as data:
        (data / "fresh.csv").write_text("a,b\n")

    monkeypatch.undo()
    assert (workspace / "data" / "fresh.csv").read_text() == "a,b\n"


# -- durable session data --------------------------------------------------


def test_a_supplied_file_lands_in_the_sessions_data(session_dir, tmp_path):
    """The point of the feature: somewhere the next turn can still see it."""
    source = tmp_path / "sales.csv"
    source.write_text("a,b\n1,2\n")

    placement = place_data((source,), session_dir)

    assert placement.placed == ("sales.csv",)
    assert (session_dir / "data" / "sales.csv").read_text() == "a,b\n1,2\n"


def test_data_is_read_only_again_afterwards(session_dir, tmp_path):
    """Nobody may hand-chmod /data. That is the behaviour that has to become
    impossible, not merely discouraged -- reaching for sudo is what bricked a
    session once already."""
    source = tmp_path / "sales.csv"
    source.write_text("x")

    place_data((source,), session_dir)

    assert not os.access(session_dir / "data", os.W_OK)
    with pytest.raises(PermissionError):
        (session_dir / "data" / "sales.csv").write_text("clobbered")


def test_data_is_read_only_again_even_when_a_copy_fails(session_dir, tmp_path, monkeypatch):
    """`writable_data`'s finally is what makes this safe. A test that only
    covers the happy path would not notice the copy moving outside it."""
    source = tmp_path / "sales.csv"
    source.write_text("x")

    gone = "disk went away"

    def explode(*_args, **_kwargs):
        raise OSError(gone)

    monkeypatch.setattr("kingfisher.infrastructure.workspace_fs.shutil.copy", explode)

    with pytest.raises(OSError, match=gone):
        place_data((source,), session_dir)

    monkeypatch.undo()
    assert not os.access(session_dir / "data", os.W_OK)


def test_two_sources_with_one_basename_are_refused(session_dir, tmp_path):
    """Silently keeping the last one loses a file the caller asked for."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = tmp_path / "a" / "report.pdf"
    second = tmp_path / "b" / "report.pdf"
    first.write_text("one")
    second.write_text("two")

    with pytest.raises(DataError, match=r"report\.pdf"):
        place_data((first, second), session_dir)

    assert not (session_dir / "data" / "report.pdf").exists()


def test_a_missing_source_is_refused_before_anything_is_written(session_dir, tmp_path):
    good = tmp_path / "good.csv"
    good.write_text("x")

    with pytest.raises(DataError, match=r"ghost\.csv"):
        place_data((good, tmp_path / "ghost.csv"), session_dir)

    assert not (session_dir / "data" / "good.csv").exists()


# -- a turn's input/ gets the same two guarantees ------------------------
#
# It did not, for as long as it existed. The copying was written inline in the
# service as a `mkdir` and a bare `shutil.copy` -- the one place in the
# application layer doing its own I/O -- so it never met the checks its
# documented counterpart had. Both cases below were measured against the real
# service before the fix: the first was accepted, and the second left
# `runs/t001/input/present.csv` behind.


def test_two_inputs_with_one_basename_are_refused(tmp_path):
    """The same loss as for `/data`, and it was silent here."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = tmp_path / "a" / "report.csv"
    second = tmp_path / "b" / "report.csv"
    first.write_text("one")
    second.write_text("two")
    into = tmp_path / "input"

    with pytest.raises(DataError, match=r"report\.csv"):
        place_inputs((first, second), into)

    assert not into.exists()


def test_a_missing_input_leaves_nothing_half_placed(tmp_path):
    present = tmp_path / "present.csv"
    present.write_text("x")
    into = tmp_path / "input"

    with pytest.raises(DataError, match=r"gone\.csv"):
        place_inputs((present, tmp_path / "gone.csv"), into)

    assert not (into / "present.csv").exists()


def test_placing_inputs_names_what_landed(tmp_path):
    one = tmp_path / "one.csv"
    two = tmp_path / "two.csv"
    one.write_text("1")
    two.write_text("2")
    into = tmp_path / "input"

    assert place_inputs((one, two), into) == ("one.csv", "two.csv")
    assert sorted(p.name for p in into.iterdir()) == ["one.csv", "two.csv"]


def test_no_inputs_makes_no_directory(tmp_path):
    """A turn that supplied nothing should not look like one that did."""
    into = tmp_path / "input"

    assert place_inputs((), into) == ()
    assert not into.exists()


def test_resupplying_replaces_and_says_so(session_dir, tmp_path):
    """`--data` is the only supported way to write there, so refusing would
    make updating a dataset impossible. Replacing silently would be worse."""
    source = tmp_path / "sales.csv"
    source.write_text("first")
    place_data((source,), session_dir)

    source.write_text("second")
    placement = place_data((source,), session_dir)

    assert (session_dir / "data" / "sales.csv").read_text() == "second"
    assert placement.replaced == ("sales.csv",)


def test_nothing_is_replaced_on_a_first_supply(session_dir, tmp_path):
    source = tmp_path / "new.csv"
    source.write_text("x")

    assert place_data((source,), session_dir).replaced == ()


def test_supplying_nothing_touches_nothing(session_dir):
    placement = place_data((), session_dir)

    assert placement.placed == ()
    assert placement.replaced == ()
