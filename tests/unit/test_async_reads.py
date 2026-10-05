"""The async reads keep the event loop free: no sync port call is made on its thread."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import yaml
from deepagents.backends import CompositeBackend, FilesystemBackend

from kingfisher import DefaultBackends, Kingfisher, default_backends
from kingfisher.domain.access import parse
from tests.conftest import StubCheckpointer, an_agent, pin, start
from tests.unit.test_run import StubAgent

#: Every sync method the three reads can reach, on the classes that answer them.
PORT_METHODS = {
    DefaultBackends: ("open", "sessions"),
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
        Kingfisher(
            policied, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
        )
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
