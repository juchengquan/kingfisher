from __future__ import annotations

import os
import platform
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher import LocalSessions, backend_at
from kingfisher.core.layout import Layout
from kingfisher.core.session.files import InputsError, place_inputs
from kingfisher.core.session.steps import drive
from kingfisher.plugins.sessions.folders import LocalSessionDirs
from kingfisher.plugins.sessions.permissions import protect_inputs, writable_inputs
from tests.conftest import start

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

#: A deployment that renamed the folder the caller's files go in.
MOVED = Layout(inputs="in", outputs="out", memory="mem", scratchpad="scratch")


@macos
def test_the_callers_files_land_in_the_folder_this_deployment_named_and_are_fenced_there(
    cfg, tmp_path
):
    """`_contents_of` spelt the route as the module constant, so the files landed at
    `/inputs/` whatever the layout said. Under a renamed one that route does not
    exist, so the write fell through to the session root and made a folder nothing had
    routed, hardened or fenced: measured, the agent's own file tools saw an empty
    folder while its shell rewrote the caller's file with exit 0.

    Three things in one rule because they are one fault: where the file lands, whether
    that folder is hardened, and whether the profile refuses it. Any one of them alone
    passes on a file that is in the wrong place.

    The mode bits are lifted first, so what refuses the write is the fence and not the
    permissions -- a refusal from those satisfies `exit_code != 0` on its own, with a
    different errno.
    """
    cfg = replace(cfg, layout=MOVED)
    source = tmp_path / "secret.csv"
    source.write_text("caller,data\n", encoding="utf-8")
    start(cfg, "s")
    session = cfg.workspace / "sessions" / "s"
    backend = backend_at(cfg, session)

    drive(place_inputs((source,), backend, cfg.layout))

    landed = session / MOVED.inputs / "secret.csv"
    assert landed.is_file(), "the caller's file is not in the folder the agent is told"
    assert not (session / "inputs").exists(), "a folder nothing routed was made"
    assert backend.download_files([f"{MOVED.inputs_route}secret.csv"])[0].content == (
        b"caller,data\n"
    ), "the agent's own file tools cannot read what the caller supplied"

    lifted = backend.execute(
        f"chmod -R u+w {MOVED.inputs} 2>/dev/null; printf FORGED > {MOVED.inputs}/secret.csv"
    )

    assert lifted.exit_code != 0
    assert landed.read_text(encoding="utf-8") == "caller,data\n"


def test_resupplying_under_a_renamed_layout_still_says_what_was_replaced(cfg, tmp_path):
    """`replaced` is read off a listing of the inputs folder, and that listing is the
    third place in this function that names a route. Pointed at the default one it
    comes back empty under a renamed layout, so a caller who overwrote a file they had
    supplied before is told nothing was replaced -- which is the one thing this field
    exists to say.
    """
    cfg = replace(cfg, layout=MOVED)
    source = tmp_path / "given.csv"
    source.write_text("first\n", encoding="utf-8")
    start(cfg, "s")
    backend = backend_at(cfg, cfg.workspace / "sessions" / "s")

    first = drive(place_inputs((source,), backend, cfg.layout))
    source.write_text("second\n", encoding="utf-8")
    again = drive(place_inputs((source,), backend, cfg.layout))

    assert first.placed == ("given.csv",) and first.replaced == ()
    assert again.replaced == ("given.csv",), "a resupply read as a first supply"


def test_a_renamed_scratchpad_is_as_private_as_the_default_one(cfg):
    """TMPDIR points at it, so it is the one session folder whose mode is not the
    default. `ensure_session_layout` takes the leaf on its own because
    `keep_tmp_private` cannot pick one directory out of the tuple, and `backend_at`
    passed every other leaf and not that one -- measured, `0o755` where the default
    layout gives `0o700`.

    Both layouts, because the default was the only one driven and the default is the
    one that worked.
    """
    for layout in (Layout(), MOVED):
        made = replace(cfg, layout=layout)
        session_id = f"s-{layout.scratchpad}"
        # Through `open`, which is what lays a session out in production -- the call
        # that was missing the leaf. A test making the directory itself would be
        # passing the argument production omits, which is how this hid.
        LocalSessions().open(made, session_id)
        session = made.workspace / "sessions" / session_id
        mode = (session / layout.scratchpad).stat().st_mode & 0o777

        assert mode == 0o700, f"{layout.scratchpad} is {oct(mode)}"


def test_inputs_become_read_only_to_the_os(workspace):
    """The mode bits, which is all this is: a write from a process holding no fence is
    refused by the kernel.

    It said the kernel enforces this against `execute` too. It does not, and this test
    could not have shown either way -- it writes from the pytest process and never
    calls `execute`. The shell runs as the uid that owns these files, so it lifts the
    bits itself; what keeps `execute` out is a fence rule per mechanism, and
    `test_the_shell_cannot_lift_the_bits_on_the_callers_inputs` is what drives that.
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

    placement = drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

    assert placement.placed == ("sales.csv",)
    assert (session_dir / "inputs" / "sales.csv").read_text() == "a,b\n1,2\n"


def test_inputs_are_read_only_again_afterwards(cfg, session_dir, tmp_path):
    """Nobody may hand-chmod /inputs."""
    source = tmp_path / "sales.csv"
    source.write_text("x")

    drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

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

    drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

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
        "kingfisher.plugins.sessions.backend.FilesystemBackend.upload_files", explode
    )

    with pytest.raises(OSError, match=gone):
        drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

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
        drive(place_inputs((first, second), backend_at(cfg, session_dir), cfg.layout))

    assert not (session_dir / "inputs" / "report.pdf").exists()


def test_a_missing_source_is_refused_before_anything_is_written(cfg, session_dir, tmp_path):
    good = tmp_path / "good.csv"
    good.write_text("x")

    with pytest.raises(InputsError, match=r"ghost\.csv"):
        drive(place_inputs(
            (good, tmp_path / "ghost.csv"), backend_at(cfg, session_dir), cfg.layout
        ))

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
    drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

    source.write_text("second")
    placement = drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))

    assert (session_dir / "inputs" / "sales.csv").read_text() == "second"
    assert placement.replaced == ("sales.csv",)


def test_nothing_is_replaced_on_a_first_supply(cfg, session_dir, tmp_path):
    source = tmp_path / "new.csv"
    source.write_text("x")

    assert drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout)).replaced == ()


def test_supplying_nothing_touches_nothing(cfg, session_dir):
    placement = drive(place_inputs((), backend_at(cfg, session_dir), cfg.layout))

    assert placement.placed == ()
    assert placement.replaced == ()


# -- removal has to undo the hardening ------------------------------------


def test_a_session_that_was_given_inputs_can_still_be_removed(cfg, session_dir, tmp_path):
    """`protect_inputs` drops the write bit off `inputs/`, and deletion is governed by the
    directory's write bit -- so hardening made the session undeletable.
    """
    source = tmp_path / "orders.csv"
    source.write_text("a,b\n1,2\n")
    drive(place_inputs((source,), backend_at(cfg, session_dir), cfg.layout))
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
