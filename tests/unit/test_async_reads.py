"""The async paths keep the event loop free: no sync port call is made on its thread."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import yaml
from deepagents.backends import CompositeBackend, FilesystemBackend

from kingfisher import DefaultBackends, Kingfisher, default_backends
from kingfisher.domain.access import parse
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.backend import SessionClaims
from tests.conftest import StubCheckpointer, an_agent, pin, start
from tests.unit.test_run import StubAgent

#: Every sync method the async paths can reach, on the classes that answer them.
PORT_METHODS = {
    DefaultBackends: ("open", "sessions", "mark_used", "size", "delete"),
    SessionClaims: ("claim", "release", "held"),
    CompositeBackend: ("download_files", "upload_files", "ls", "glob", "delete"),
    FilesystemBackend: ("download_files", "upload_files", "ls", "glob", "delete"),
}


def _on_the_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def sync_calls(monkeypatch) -> list[tuple[str, bool]]:
    """Each sync port call made, and whether it was made on the event loop's thread.

    Recorded rather than raised: raised, the error is thrown into the sequence like any
    other failure, and the test would be reading whatever that turned into.
    """
    made: list[tuple[str, bool]] = []
    for cls, names in PORT_METHODS.items():
        for name in names:
            monkeypatch.setattr(cls, name, _recorded(made, cls, name))
    return made


def _recorded(made: list[tuple[str, bool]], cls: type, name: str):
    original = getattr(cls, name)

    def recorded(self, *args, **kwargs):
        made.append((f"{cls.__name__}.{name}", _on_the_loop()))
        return original(self, *args, **kwargs)

    return recorded


@pytest.mark.parametrize("wiring", ["graph", "session backends"])
def test_the_async_reads_make_no_sync_port_call_on_the_loop(cfg, sync_calls, wiring):
    """Driven with `drive` rather than `adrive`, or with a sync call between two steps,
    they still return the right answer -- and hold every coroutine on the loop for the
    round trip, which nothing else in the suite would notice.
    """
    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    kf = (
        Kingfisher(policied, graph=StubAgent("ok"), threads=StubCheckpointer())
        if wiring == "graph"
        else Kingfisher(policied, backends=default_backends)
    )
    start(policied, "s")
    produced = policied.workspace / "sessions" / "s" / "derived" / "out.txt"
    produced.parent.mkdir(parents=True, exist_ok=True)
    produced.write_text("result")
    pin(kf, "s", "only_a")
    sync_calls.clear()

    async def read_all():
        held = ("A",)
        return (
            await kf.asession("s", source_ids=held),
            await kf.apending("s", source_ids=held),
            await kf.aartifact("s", "derived/out.txt", source_ids=held),
        )

    found, waiting, content = asyncio.run(read_all())

    assert (found is not None, waiting, content) == (True, (), b"result")
    # The control: the ports were reached at all, so an empty list below is not a
    # patch that missed.
    assert sync_calls, "no sync port method was reached, on any thread"
    on_the_loop = sorted({name for name, loop in sync_calls if loop})
    assert not on_the_loop, f"made on the event loop's thread: {on_the_loop}"


def test_an_async_turn_makes_no_sync_port_call_on_the_loop(cfg, sync_calls, tmp_path):
    """Setup and the turn's ending are round trips to the session's backend: the lookup,
    the open, the pin, the claim, the data, and at the end the pause, the transcript,
    the listing and the claim again. Made on the loop, each holds every other turn up
    for as long as the backend takes, and every answer still comes back right.
    """
    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(
        cfg,
        access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"),
        session_max_bytes=10**9,
    )
    data = tmp_path / "in.csv"
    data.write_text("a,b\n")
    kf = Kingfisher(policied, graph=StubAgent("ok"), threads=StubCheckpointer())
    start(policied, "s")
    sync_calls.clear()

    asked = Request("go", agent="only_a", session_id="s", data=(data,))
    result = asyncio.run(kf.arun(asked, source_ids=("A",)))

    assert result.completed
    assert sync_calls, "no sync port method was reached, on any thread"
    on_the_loop = sorted({name for name, loop in sync_calls if loop})
    assert not on_the_loop, f"made on the event loop's thread: {on_the_loop}"


def test_opening_a_sessions_files_makes_no_sync_port_call_on_the_loop(cfg, sync_calls):
    """`files_for` on the loop is a session's backend -- a sandbox, on a remote one --
    opened while every other coroutine waits for it.
    """
    kf = Kingfisher(cfg, backends=default_backends)
    start(cfg, "s")
    sync_calls.clear()

    files = asyncio.run(kf.afiles_for("s"))

    assert files is not None
    assert sync_calls, "no sync port method was reached, on any thread"
    on_the_loop = sorted({name for name, loop in sync_calls if loop})
    assert not on_the_loop, f"made on the event loop's thread: {on_the_loop}"


def test_a_turn_reads_the_pin_once(cfg, monkeypatch):
    """Read once to decide whether the caller reaches the session and again to decide
    which agent the turn runs, every turn under a policy paid a round trip for a file
    it already held.
    """
    from kingfisher.layout import HARNESS_ROUTE, PINNED_AGENT

    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    kf = Kingfisher(policied, graph=StubAgent("ok"), threads=StubCheckpointer())
    start(policied, "s")
    asked = Request("go", agent="only_a", session_id="s")
    kf.run(asked, source_ids=("A",))  # pins the agent

    pin = f"{HARNESS_ROUTE}{PINNED_AGENT}"
    reads: list[str] = []
    downloading = CompositeBackend.download_files

    def counted(self, paths):
        reads.extend(path for path in paths if path == pin)
        return downloading(self, paths)

    monkeypatch.setattr(CompositeBackend, "download_files", counted)
    kf.run(asked, source_ids=("A",))

    assert reads == [pin]
