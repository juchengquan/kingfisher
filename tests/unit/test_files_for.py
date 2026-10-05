"""A session's backend built once by the caller, and handed to the turn as `files=`."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.application import service as service_module
from kingfisher.domain.ports import CommandResult
from kingfisher.domain.session import session_dir
from kingfisher.infrastructure.harness.backend import DefaultBackends
from tests.conftest import StubCheckpointer, Through, an_agent
from tests.unit.scripted import Scripted
from tests.unit.test_run import StubAgent
from tests.unit.test_session_files import Elsewhere, _calls

DOORS = ("stream", "astream", "run", "arun")


def _turn(kf, door: str, request: Request, **kwargs):
    """`request` taken through `door`, to the result it finished with."""
    if door == "run":
        return kf.run(request, **kwargs)
    if door == "arun":
        return asyncio.run(kf.arun(request, **kwargs))
    if door == "stream":
        return list(kf.stream(request, **kwargs))[-1].result

    async def drained():
        return [event async for event in kf.astream(request, **kwargs)]

    return asyncio.run(drained())[-1].result


class Counting(DefaultBackends):
    """The default backends, recording each session opened."""

    def __init__(self, *, runner=None) -> None:
        super().__init__(runner=runner)
        self.asked: list[str] = []
        self.opened: list[object] = []

    def open(self, cfg, session_id, /, *, catalogue=None):
        self.asked.append(session_id)
        self.opened.append(super().open(cfg, session_id, catalogue=catalogue))
        return self.opened[-1]


class Sandboxes(Elsewhere):
    """`Elsewhere`, where every `open` is a sandbox of its own: what a turn that
    opened one beside the caller's would be seen running in.
    """

    def __init__(self, root) -> None:
        super().__init__(root)
        self.count = 0

    def open(self, cfg, session_id, /, *, catalogue=None):
        self.count += 1
        return super().open(
            cfg, f"{session_id}/{self.count}", catalogue=catalogue
        )


def test_files_for_opens_the_session_with_the_deployments_runner(cfg, way):
    """Opened without the runner the deployment wired, the caller's backend would be
    well-formed and run its commands somewhere nobody chose.
    """

    class Runner:
        local = True

        def run(self, command, *, timeout=None):
            del timeout
            return CommandResult(output=f"ran {command}", exit_code=0)

    built: list[tuple[object, object]] = []

    def runner(directory):
        built.append((directory, Runner()))
        return built[-1][1]

    counting = Counting(runner=runner)
    kf = Kingfisher(cfg, backends=counting)

    files = Through(kf, way).files_for("s")

    assert files is counting.opened[0]
    assert counting.asked == ["s"]
    assert built == [(session_dir(cfg.workspace, "s"), files.default.runner)]


@pytest.mark.parametrize("door", DOORS)
def test_a_turn_handed_its_files_opens_no_others(cfg, monkeypatch, door, way):
    """Every turn opened the session's backend itself, so a caller using one around the
    turn held a second sandbox on a remote backend, and the agent ran in the other.
    """
    ran_on: list[object] = []

    def build(*args, backend, **kwargs):
        del args, kwargs
        ran_on.append(backend)
        return StubAgent("ok")

    monkeypatch.setattr(service_module, "build_agent", build)
    named = an_agent(cfg)
    counting = Counting()
    kf = Kingfisher(cfg, backends=counting, threads=StubCheckpointer())
    files = Through(kf, way).files_for("s")

    result = _turn(kf, door, Request("go", agent=named, session_id="s"), files=files)

    assert result.completed
    assert counting.asked == ["s"], "the turn opened the session again"
    assert ran_on[0] is files


def test_the_callers_backend_is_the_one_the_agent_works_in(scripted, tmp_path):
    """The control on a backend where a second open is a second place: the agent reads
    what the caller put in through `files` and the caller reads back what it wrote.
    """
    an_agent(scripted)
    Scripted.script.extend([
        _calls("read_file", file_path="/data/brief.md"),
        _calls("write_file", file_path="/derived/report.txt", content="made from the brief"),
        AIMessage(content="done"),
    ])
    sandboxes = Sandboxes(tmp_path / "remote")
    kf = Kingfisher(scripted, backends=sandboxes)
    files = kf.files_for("s")
    files.upload_files([("/data/brief.md", b"the brief")])

    events = list(kf.stream(Request("go", agent="only", session_id="s"), files=files))

    read = next(e.text for e in events if e.kind == "tool_result" and e.tool == "read_file")
    assert "the brief" in read, f"the agent never saw what the caller put in: {read!r}"
    assert "derived/report.txt" in events[-1].result.artifacts
    (fetched,) = files.download_files(["/derived/report.txt"])
    assert fetched.content == b"made from the brief"
    assert sandboxes.count == 1


@pytest.mark.parametrize("door", DOORS)
def test_files_for_a_request_that_names_no_session_are_refused(cfg, door):
    """The turn would mint a session and run in the files of the one they were opened
    for, reporting an id whose session holds nothing the turn did.
    """
    named = an_agent(cfg)
    counting = Counting()
    kf = Kingfisher(cfg, backends=counting, threads=StubCheckpointer())
    files = kf.files_for("s")

    with pytest.raises(ValueError, match="names no session"):
        _turn(kf, door, Request("go", agent=named), files=files)

    assert counting.asked == ["s"]
    assert [s.id for s in kf.sessions()] == ["s"]
