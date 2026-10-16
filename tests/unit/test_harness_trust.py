"""What kingfisher reads back from a session is kept as written, and nothing signs it.

What stops the agent rewriting its own pinned agent, conversation or paused turn is
the shell's fence -- kingfisher's sandbox, or a backend that passes `shell_denied`.
Where kingfisher can see neither, `doctor` says so; a turn still runs.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, LocalSessions, Request, backend_at
from kingfisher.core.layout import HARNESS, PAUSED_STATE, PINNED_AGENT
from kingfisher.core.request import Decision, DecisionError, Resume
from tests.checks.backend import shell_denied
from tests.conftest import an_agent, start
from tests.unit.scripted import Scripted
from tests.unit.test_approval_gates import _gated, _write
from tests.unit.test_backend_seam import Elsewhere
from tests.unit.test_run import StubAgent


def _harness_file(cfg, session_id: str, name: str):
    return cfg.workspace / "sessions" / session_id / HARNESS / name


def test_what_a_session_keeps_is_written_as_it_is(scripted):
    """No signature beside it. One written for a key nothing reads would be a file
    claiming a protection nobody checks.
    """
    an_agent(scripted)
    start(scripted, "s")
    Scripted.script.append(AIMessage(content="done"))

    Kingfisher(scripted, sessions=LocalSessions()).run(Request("go", agent="only", session_id="s"))

    held = sorted(p.name for p in (scripted.workspace / "sessions" / "s" / HARNESS).iterdir())
    assert PINNED_AGENT in held, "nothing was pinned, so there was nothing to leave unsigned"
    assert not [name for name in held if name.endswith(".sig")], held


class _WrappingTheDefault(LocalSessions):
    """Returns exactly what the default does, and is still not the default."""


@pytest.mark.parametrize(
    "wiring",
    [
        lambda cfg: {"graph": StubAgent("ok"), "sessions": LocalSessions()},
        lambda cfg: {"sessions": _WrappingTheDefault()},
        lambda cfg: {"sessions": LocalSessions(runner=lambda _d: Elsewhere())},
        lambda cfg: {"sessions": LocalSessions(), "cfg": replace(cfg, shell_sandbox="off")},
    ],
    ids=["graph", "wrapped-default", "runner", "sandbox-off"],
)
def test_a_deployment_kingfisher_cannot_see_into_still_starts(cfg, wiring):
    """Each of these was refused at construction until it set a key. Whether its shell
    is kept out of `.harness` is now the deployment's to know, and `doctor`'s to say.
    """
    given = wiring(cfg)

    Kingfisher(given.pop("cfg", cfg), **given)


def test_a_paused_turn_cut_short_is_refused_with_a_reason(cfg):
    """A backend has no rename, so a paused turn is written in place and a crash can
    leave half of one. No prefix of it deserialises -- every one of them was tried --
    and what the caller is told is that, rather than msgpack's own error.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))
    state = _harness_file(cfg, paused.session_id, PAUSED_STATE)
    state.write_bytes(state.read_bytes()[: len(state.read_bytes()) // 2])

    with pytest.raises(DecisionError, match=f"session {paused.session_id}.*cannot be read"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )


def test_the_kit_catches_a_shell_that_can_write_the_harness(cfg, session_dir):
    """Run against the default with the sandbox off, where nothing refuses the shell
    either folder -- and the kit has to name both.

    This asserted `/inputs` was absent, on the belief that its permission bits held
    without a fence. They do not: the shell runs as the uid that owns those files, so
    `chmod u+w` and the same write replaces the caller's file. The kit lifts the bits
    before writing now, which is what turned that assertion from true-looking into
    false, and a deployment author reading it was being told a folder was safe that
    two commands take.
    """
    unfenced = replace(cfg, shell_sandbox="off")

    with pytest.raises(AssertionError) as caught:
        shell_denied(lambda: backend_at(unfenced, session_dir))

    said = str(caught.value)
    assert "/.harness" in said and "/inputs" in said, said


def test_the_kit_passes_where_a_fence_holds_both_folders(cfg, session_dir):
    """The other direction, and the one that makes the rule above mean something: with
    the fence this host offers, the kit finds nothing. Without this, a kit that raised
    for every backend would pass that rule too.
    """
    from kingfisher.plugins.sessions.sandbox import confinement

    if confinement.shell_confinement(cfg).mechanism != confinement.SANDBOX_EXEC:
        pytest.skip("no fence here to hold them")

    shell_denied(lambda: backend_at(cfg, session_dir))


@pytest.mark.parametrize(
    ("change", "because"),
    [
        ({"shell_sandbox": "off"}, "'off'"),
        (
            {"sessions_factory": "deployment.backends:Remote"},
            "KINGFISHER_SESSIONS",
        ),
    ],
    ids=["sandbox-off", "own-backend"],
)
def test_doctor_warns_where_nothing_it_can_see_keeps_the_shell_out(cfg, change, because):
    """A warning and never a failure: the deployment runs, and may well be fenced by
    something this command cannot see. What it can say is where to look.
    """
    from kingfisher.cli.health import _session_files

    (check,) = _session_files(replace(cfg, **change))

    assert check.verdict == "warn"
    assert because in check.detail
    assert "shell_denied" in check.remedy


def test_doctor_is_satisfied_where_the_sandbox_keeps_the_shell_out(cfg):
    from kingfisher.cli.health import _session_files
    from kingfisher.plugins.sessions.sandbox.confinement import harness_unfenced

    if harness_unfenced(cfg) is not None:
        pytest.skip("no sandbox kingfisher applies itself is available on this host")

    (check,) = _session_files(cfg)

    assert check.verdict == "ok"


def test_a_session_key_still_set_is_reported_as_read_by_nothing():
    """Set by a deployment that followed the upgrade notes of the day before. It now
    does nothing, and the only thing that will ever say so is this.
    """
    from kingfisher.cli.health import _retired

    (check,) = _retired({"KINGFISHER_SESSION_KEY": "a" * 64})

    assert check.verdict == "warn"
    assert "KINGFISHER_SESSION_KEY" in check.detail
