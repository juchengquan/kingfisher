from __future__ import annotations

import os
from pathlib import Path

import pytest

from kingfisher import backend_at
from kingfisher.infrastructure.session_files import InputsError, place_inputs
from kingfisher.infrastructure.steps import drive
from kingfisher.infrastructure.workspace import (
    LocalSessionDirs,
    protect_inputs,
    writable_inputs,
)


def test_inputs_become_read_only_to_the_os(workspace):
    """The layer the deny rule cannot provide: the kernel enforces this against
    `execute` too, which tool-level permissions never covered.
    """
    with writable_inputs(workspace) as inputs:
        (inputs / "input.csv").write_text("a,b\n1,2\n")

    protect_inputs(workspace)

    with pytest.raises(PermissionError):
        (workspace / "inputs" / "input.csv").write_text("clobbered")


def test_directory_write_bit_is_dropped_so_files_cannot_be_deleted(workspace):
    """Deletion is governed by the directory's write bit, not the file's."""
    with writable_inputs(workspace) as inputs:
        (inputs / "input.csv").write_text("x")
    protect_inputs(workspace)

    assert not os.access(workspace / "inputs", os.W_OK)


def test_writable_inputs_restores_protection_afterwards(workspace):
    with writable_inputs(workspace) as inputs:
        (inputs / "new.csv").write_text("y")
        assert os.access(inputs, os.W_OK)

    assert not os.access(workspace / "inputs", os.W_OK)


def test_protect_inputs_is_idempotent(workspace):
    protect_inputs(workspace)
    protect_inputs(workspace)
    assert not os.access(workspace / "inputs", os.W_OK)


def _refuse(name: str, monkeypatch):
    """Make `chmod` refuse one file, the way the kernel does for a file we do not own."""
    real = Path.chmod

    def chmod(self, mode, **kwargs):
        if self.name == name:
            raise PermissionError(1, "Operation not permitted", str(self))
        return real(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)


def test_a_file_we_cannot_chmod_is_reported_not_raised(workspace, monkeypatch):
    """One file owned by another user used to abort the run -- and since this runs
    before anything else, every later run of that session too.
    """
    with writable_inputs(workspace) as inputs:
        (inputs / "theirs.pdf").write_text("x")
        (inputs / "ours.csv").write_text("y")

    _refuse("theirs.pdf", monkeypatch)
    failures = protect_inputs(workspace)

    assert len(failures) == 1
    assert "theirs.pdf" in failures[0]
    assert "Operation not permitted" in failures[0]


def test_the_rest_of_the_directory_is_still_hardened(workspace, monkeypatch):
    """Degrading is only acceptable if it degrades to *almost* protected."""
    with writable_inputs(workspace) as inputs:
        (inputs / "theirs.pdf").write_text("x")
        (inputs / "ours.csv").write_text("y")

    _refuse("theirs.pdf", monkeypatch)
    protect_inputs(workspace)
    monkeypatch.undo()

    assert not os.access(workspace / "inputs", os.W_OK)
    with pytest.raises(PermissionError):
        (workspace / "inputs" / "ours.csv").write_text("clobbered")


def test_an_input_can_still_be_added_beside_a_file_we_do_not_own(workspace, monkeypatch):
    """Refusing a new input because an unrelated old one belongs to someone else would
    be its own bug.
    """
    with writable_inputs(workspace) as inputs:
        (inputs / "theirs.pdf").write_text("x")

    _refuse("theirs.pdf", monkeypatch)
    with writable_inputs(workspace) as inputs:
        (inputs / "fresh.csv").write_text("a,b\n")

    monkeypatch.undo()
    assert (workspace / "inputs" / "fresh.csv").read_text() == "a,b\n"


# -- a session's inputs ----------------------------------------------------


def test_a_supplied_file_lands_in_the_sessions_inputs(cfg, session_dir, tmp_path):
    """The point of the feature: somewhere the next turn can still see it."""
    source = tmp_path / "sales.csv"
    source.write_text("a,b\n1,2\n")

    placement = drive(place_inputs((source,), backend_at(cfg, session_dir)))

    assert placement.placed == ("sales.csv",)
    assert (session_dir / "inputs" / "sales.csv").read_text() == "a,b\n1,2\n"


def test_inputs_are_read_only_again_afterwards(cfg, session_dir, tmp_path):
    """Nobody may hand-chmod /inputs."""
    source = tmp_path / "sales.csv"
    source.write_text("x")

    drive(place_inputs((source,), backend_at(cfg, session_dir)))

    assert not os.access(session_dir / "inputs", os.W_OK)
    with pytest.raises(PermissionError):
        (session_dir / "inputs" / "sales.csv").write_text("clobbered")


def test_the_folder_an_upload_lands_in_is_the_one_hardened(cfg, session_dir, tmp_path):
    """`protect_inputs` spelled `data/` by hand while `InputsBackend` uploaded under the
    layout's constant, so renaming the constant would have left a caller's inputs
    writable to the shell.
    """
    source = tmp_path / "sales.csv"
    source.write_text("x")

    drive(place_inputs((source,), backend_at(cfg, session_dir)))

    # Found rather than named: a folder spelled here would agree with whichever side
    # spelled it the same way, and pass against the drift it is meant to catch.
    (landed,) = session_dir.rglob(source.name)
    assert not os.access(landed.parent, os.W_OK), f"{landed.parent} was left writable"
    assert not os.access(landed, os.W_OK), f"{landed} was left writable"


def test_inputs_are_read_only_again_even_when_a_copy_fails(cfg, session_dir, tmp_path, monkeypatch):
    """`writable_inputs`'s finally is what makes this safe."""
    source = tmp_path / "sales.csv"
    source.write_text("x")

    gone = "disk went away"

    def explode(*_args, **_kwargs):
        raise OSError(gone)

    monkeypatch.setattr(
        "kingfisher.infrastructure.harness.backend.FilesystemBackend.upload_files", explode
    )

    with pytest.raises(OSError, match=gone):
        drive(place_inputs((source,), backend_at(cfg, session_dir)))

    monkeypatch.undo()
    assert not os.access(session_dir / "inputs", os.W_OK)


def test_two_sources_with_one_basename_are_refused(cfg, session_dir, tmp_path):
    """Silently keeping the last one loses a file the caller asked for."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = tmp_path / "a" / "report.pdf"
    second = tmp_path / "b" / "report.pdf"
    first.write_text("one")
    second.write_text("two")

    with pytest.raises(InputsError, match=r"report\.pdf"):
        drive(place_inputs((first, second), backend_at(cfg, session_dir)))

    assert not (session_dir / "inputs" / "report.pdf").exists()


def test_a_missing_source_is_refused_before_anything_is_written(cfg, session_dir, tmp_path):
    good = tmp_path / "good.csv"
    good.write_text("x")

    with pytest.raises(InputsError, match=r"ghost\.csv"):
        drive(place_inputs((good, tmp_path / "ghost.csv"), backend_at(cfg, session_dir)))

    assert not (session_dir / "inputs" / "good.csv").exists()


# -- a turn's input/ gets the same two guarantees ------------------------
#
# It did not, for as long as it existed. The copying was written inline in the
# service as a `mkdir` and a bare `shutil.copy` -- the one place in the
# application layer doing its own I/O -- so it never met the checks its
# documented counterpart had. Both cases below were measured against the real
# service before the fix: the first was accepted, and the second left
# `runs/t001/input/present.csv` behind.


def test_resupplying_replaces_and_says_so(cfg, session_dir, tmp_path):
    """`--input` is the only supported way to write there, so refusing would make
    updating a dataset impossible.
    """
    source = tmp_path / "sales.csv"
    source.write_text("first")
    drive(place_inputs((source,), backend_at(cfg, session_dir)))

    source.write_text("second")
    placement = drive(place_inputs((source,), backend_at(cfg, session_dir)))

    assert (session_dir / "inputs" / "sales.csv").read_text() == "second"
    assert placement.replaced == ("sales.csv",)


def test_nothing_is_replaced_on_a_first_supply(cfg, session_dir, tmp_path):
    source = tmp_path / "new.csv"
    source.write_text("x")

    assert drive(place_inputs((source,), backend_at(cfg, session_dir))).replaced == ()


def test_supplying_nothing_touches_nothing(cfg, session_dir):
    placement = drive(place_inputs((), backend_at(cfg, session_dir)))

    assert placement.placed == ()
    assert placement.replaced == ()


# -- removal has to undo the hardening ------------------------------------


def test_a_session_that_was_given_inputs_can_still_be_removed(cfg, session_dir, tmp_path):
    """`protect_inputs` drops the write bit off `inputs/`, and deletion is governed by the
    directory's write bit -- so hardening made the session undeletable.
    """
    source = tmp_path / "orders.csv"
    source.write_text("a,b\n1,2\n")
    drive(place_inputs((source,), backend_at(cfg, session_dir)))
    assert not os.access(session_dir / "inputs", os.W_OK), "not hardened; test proves nothing"

    failure = LocalSessionDirs().remove_tree(session_dir)

    assert failure is None, f"still not removable: {failure}"
    assert not session_dir.exists()


def test_removal_reaches_through_nested_hardened_directories(session_dir):
    """`protect_inputs` hardens every directory under `inputs/`, not just the top, so
    unlocking one level would strand anything deeper.
    """
    with writable_inputs(session_dir) as inputs:
        (inputs / "a" / "b").mkdir(parents=True)
        (inputs / "a" / "b" / "deep.csv").write_text("x")
    protect_inputs(session_dir)

    assert LocalSessionDirs().remove_tree(session_dir) is None
    assert not session_dir.exists()


def test_a_directory_we_cannot_unlock_is_reported_not_raised(session_dir, monkeypatch):
    """The same degradation `protect_inputs` chose."""
    with writable_inputs(session_dir) as inputs:
        (inputs / "theirs.pdf").write_text("x")
    protect_inputs(session_dir)
    _refuse("inputs", monkeypatch)

    failure = LocalSessionDirs().remove_tree(session_dir)

    assert failure is not None, "reported success without deleting"
    assert "directory not removed" in failure
    assert session_dir.exists()


def test_an_unrelated_failure_leaves_inputs_hardened(session_dir, monkeypatch):
    """Unlocking is for the one error it can fix."""
    import errno

    with writable_inputs(session_dir) as inputs:
        (inputs / "kept.csv").write_text("x")
    protect_inputs(session_dir)

    real = os.unlink

    def busy(path, **kwargs):
        if str(path).endswith("kept.csv"):
            raise OSError(errno.EBUSY, "Device or resource busy", str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(os, "unlink", busy)
    failure = LocalSessionDirs().remove_tree(session_dir)
    monkeypatch.undo()

    assert failure is not None, "reported success despite a failed unlink"
    assert session_dir.exists()
    assert not os.access(session_dir / "inputs", os.W_OK), (
        "/inputs was left writable on a session that survived the sweep"
    )
