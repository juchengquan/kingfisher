"""A session's files reached through its backend, wherever that backend keeps them."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain_core.messages import AIMessage

from kingfisher import (
    ArtifactError,
    Kingfisher,
    Request,
    UnknownSessionError,
    backend_at,
    default_backends,
)
from kingfisher.domain.access import parse
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.backend import (
    DefaultBackends,
    SessionBackends,
    SessionClaims,
)
from kingfisher.infrastructure.session_files import (
    collect_artifacts,
    place_inputs,
    read_artifact,
)
from kingfisher.infrastructure.steps import drive
from kingfisher.layout import HARNESS, INPUTS, SESSION_DIRS, SKILLS_ROUTE, routed_paths
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import Through, an_agent, pin, start
from tests.unit.scripted import Scripted


def _calls(name: str, **args: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


class ElsewhereFiles(SessionClaims, CompositeBackend):
    """One session's filesystem in a directory kingfisher is never told about."""

    def __init__(self, kept: Path, skills: Path) -> None:
        super().__init__(
            default=LocalShellBackend(root_dir=kept),
            routes={
                route: FilesystemBackend(
                    root_dir=skills if route == SKILLS_ROUTE else kept / route.strip("/")
                )
                for route in routed_paths()
            },
        )
        self._session_dir = kept


class Elsewhere(SessionBackends):
    """`SessionBackends` keeping each session under `root`, never in its directory.

    What a remote sandbox looks like from here: a working filesystem the agent can
    read, write and run commands in, sharing nothing with the workspace kingfisher
    holds. Routed the way `route_coverage` asks, so it is refused for nothing but the
    thing under test.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def open(self, cfg, session_id, /, *, catalogue=None):
        kept = self.root / session_id
        for name in (*SESSION_DIRS, HARNESS):
            (kept / name).mkdir(parents=True, exist_ok=True)
        skills = (catalogue or Definitions.from_config(cfg)).skills.root
        return ElsewhereFiles(kept, skills)

    def sessions(self, cfg):
        if not self.root.is_dir():
            return ()
        return tuple((p.name, p.stat().st_mtime) for p in self.root.iterdir() if p.is_dir())

    def mark_used(self, cfg, session_id):
        (self.root / session_id).touch()

    def size(self, cfg, session_id):
        return sum(p.stat().st_size for p in (self.root / session_id).rglob("*") if p.is_file())

    def delete(self, cfg, session_id):
        shutil.rmtree(self.root / session_id, ignore_errors=True)


# -- a turn ------------------------------------------------------------------


def test_a_backend_that_keeps_the_session_elsewhere_gets_the_inputs_and_gives_back_the_report(
    scripted, tmp_path
):
    """The failure this exists for: with the session somewhere else, inputs were placed
    in a directory the agent never saw and the report it wrote came back as nothing, on a
    turn that said it had worked.
    """
    an_agent(scripted)
    source = tmp_path / "in.csv"
    source.write_text("alpha\n")
    remote = tmp_path / "remote"
    Scripted.script.extend([
        _calls("read_file", file_path="/inputs/in.csv"),
        _calls("write_file", file_path="/outputs/report.txt", content="made elsewhere"),
        AIMessage(content="done"),
    ])
    kf = Kingfisher(scripted, backends=Elsewhere(remote))

    events = list(kf.stream(Request("go", agent="only", inputs=(source,))))

    read = next(e.text for e in events if e.kind == "tool_result" and e.tool == "read_file")
    assert "alpha" in read, f"the agent never saw the inputs it was given: {read!r}"
    (finished,) = [e for e in events if e.kind == "finished"]
    session_id = finished.result.session_id
    assert "outputs/report.txt" in finished.result.artifacts
    assert kf.artifact(session_id, "outputs/report.txt") == b"made elsewhere"
    assert kf.sessions()[0].id == session_id
    # And none of it went through the directory kingfisher holds, which is what makes
    # the answers above ones only the backend could have given.
    local = scripted.workspace / "sessions" / session_id
    assert not (local / INPUTS / "in.csv").exists()
    assert not (local / "outputs" / "report.txt").exists()


# -- fetching one ------------------------------------------------------------


def _produced(
    cfg, name: str = "outputs/out.txt", content: str = "result", *, backends=default_backends
) -> Kingfisher:
    start(cfg, "s")
    target = cfg.workspace / "sessions" / "s" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return Kingfisher(cfg, backends=backends)


def test_an_artifact_is_fetched_by_the_name_the_turn_reported(cfg, way):
    kf = _produced(cfg, "outputs/nested/out.csv", "a,b\n")

    assert Through(kf, way).artifact("s", "outputs/nested/out.csv") == b"a,b\n"


@pytest.mark.parametrize(
    "name",
    [
        ".harness/agent.yaml",
        "inputs/in.csv",
        "scratchpad/tmp.txt",
        "outputs/../.harness/agent.yaml",
        "/outputs/out.txt",
        "outputs",
    ],
)
def test_only_what_a_turn_produced_can_be_fetched(cfg, name, way):
    """The backend reaches `/.harness` and `/inputs` too. A caller entitled to a report is
    not thereby entitled to the pinned agent or someone's inputs.
    """
    kf = _produced(cfg)
    (cfg.workspace / "sessions" / "s" / HARNESS / "agent.yaml").write_text("secret")

    with pytest.raises(ArtifactError):
        Through(kf, way).artifact("s", name)


@pytest.mark.parametrize(
    "name", ["outputs/../.harness/agent.yaml", "memory/../inputs/in.csv", "outputs"]
)
def test_a_refused_name_never_reaches_the_backend(name):
    """Refused here, not left to the backend: the local one happens to reject `..` on
    its own, which is exactly what would let this check go missing unnoticed -- and a
    remote one is under no obligation to.
    """
    asked: list[list[str]] = []

    class Spy:
        def download_files(self, paths):
            asked.append(paths)
            return []

    with pytest.raises(ArtifactError):
        drive(read_artifact(Spy(), name))
    assert asked == []


def test_a_name_that_is_not_there_is_refused_by_name(cfg, way):
    kf = _produced(cfg)

    with pytest.raises(ArtifactError, match=r"outputs/missing\.txt"):
        Through(kf, way).artifact("s", "outputs/missing.txt")


def test_a_session_that_is_not_there_is_the_same_error_as_ever(cfg, way):
    kf = Kingfisher(cfg, backends=default_backends)

    with pytest.raises(UnknownSessionError):
        Through(kf, way).artifact("nobody", "outputs/out.txt")


def test_a_caller_who_cannot_reach_the_session_cannot_fetch_from_it(cfg, way):
    """Checked as reading a session is. Holding only the id of a session pinned to an
    agent you cannot reach buys nothing, including its outputs.
    """
    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    kf = _produced(policied)
    pin(kf, "s", "only_a")

    # The control beside the escape: the same call, by a caller who does reach it.
    assert Through(kf, way).artifact("s", "outputs/out.txt", source_ids=("A",)) == b"result"
    with pytest.raises(UnknownSessionError):
        Through(kf, way).artifact("s", "outputs/out.txt", source_ids=("B",))


# -- reading one -------------------------------------------------------------


class Counting(DefaultBackends):
    """The default backend, recording each session it is asked for."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def open(self, cfg, session_id, /, *, catalogue=None):
        self.asked.append(session_id)
        return super().open(cfg, session_id, catalogue=catalogue)


@pytest.mark.parametrize(
    "read",
    [lambda reads: reads.artifact("s", "outputs/out.txt"), lambda reads: reads.pending("s")],
    ids=["artifact", "pending"],
)
def test_reading_a_session_opens_its_backend_once(cfg, read, way):
    """Both opened the session's backend once to decide whether the caller reached it
    and again to read through, so remote session backends handed out two sandboxes
    per query.
    """
    counting = Counting()
    kf = _produced(cfg, backends=counting)

    read(Through(kf, way))

    assert counting.asked == ["s"]


def test_asking_whether_a_session_exists_opens_nothing_where_nothing_narrows(cfg, way):
    """Opened to read a pin no check would consult, remote session backends handed out
    a sandbox to say that a session exists.
    """
    counting = Counting()
    kf = _produced(cfg, backends=counting)

    assert Through(kf, way).session("s") is not None
    assert counting.asked == []


def test_a_session_is_opened_once_where_its_pinned_agent_decides(cfg, way):
    """The control beside the one above: under a policy the pin decides, and reading it
    takes the session's backend -- once.
    """
    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    counting = Counting()
    kf = _produced(policied, backends=counting)
    pin(kf, "s", "only_a")

    assert Through(kf, way).session("s", source_ids=("A",)) is not None
    assert counting.asked == ["s"]


# -- the pieces --------------------------------------------------------------


def test_inputs_placed_through_the_default_backend_land_read_only(cfg, session_dir, tmp_path):
    """`InputsBackend` opens `/inputs` for kingfisher's upload and nothing else, so the
    promise that the agent cannot change its inputs outlives the placing.
    """
    source = tmp_path / "in.csv"
    source.write_text("x")
    backend = backend_at(cfg, session_dir)

    placement = drive(place_inputs((source,), backend))

    assert placement.placed == ("in.csv",)
    assert (session_dir / INPUTS / "in.csv").read_text() == "x"
    assert not os.access(session_dir / INPUTS, os.W_OK)
    assert backend.write("/inputs/other.csv", "y").error, "the agent's write got through"


def test_a_refused_upload_is_an_inputs_error_naming_the_file(session_dir, tmp_path):
    """The backend answers per file rather than raising, so an unread answer is a
    placement reported as done with nothing placed.
    """
    source = tmp_path / "in.csv"
    source.write_text("x")

    class Refusing(FilesystemBackend):
        def upload_files(self, files):
            return [
                replace(answer, error="permission_denied")
                for answer in super().upload_files(files)
            ]

    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=session_dir),
        routes={"/inputs/": Refusing(root_dir=session_dir / INPUTS)},
    )

    with pytest.raises(ValueError, match=r"/inputs/in\.csv: permission_denied"):
        drive(place_inputs((source,), backend))


def test_collecting_and_reading_agree_on_every_name(cfg, session_dir):
    """Whatever `collect_artifacts` reports, `read_artifact` accepts: a name handed to a
    caller that the fetch then refused would be a result nobody can open.
    """
    (session_dir / "outputs" / "deep" / "er").mkdir(parents=True)
    (session_dir / "outputs" / "deep" / "er" / "x.bin").write_bytes(b"\x00\x01")
    (session_dir / "outputs" / ".hidden").write_text("h")
    backend = backend_at(cfg, session_dir)

    names = drive(collect_artifacts(backend))

    assert {"outputs/deep/er/x.bin", "outputs/.hidden"} <= set(names)
    for name in names:
        drive(read_artifact(backend, name))


# -- the command -------------------------------------------------------------


def test_the_command_writes_an_artifact_where_it_is_told(at_the_command_line, tmp_path, capsys):
    _produced(at_the_command_line, "outputs/out.txt", "result")
    out = tmp_path / "copy.txt"

    code = main(["artifact", "--session", "s", "outputs/out.txt", "--out", str(out)])

    assert code == 0
    assert out.read_text() == "result"


def test_the_command_writes_bytes_to_standard_output_untouched(
    at_the_command_line, capsysbinary
):
    """Bytes, not text: an artifact is often a PDF or an image, and a text stream would
    re-encode it on its way out.
    """
    start(at_the_command_line, "s")
    outputs = at_the_command_line.workspace / "sessions" / "s" / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "blob.bin").write_bytes(b"\xff\x00\xfe")

    code = main(["artifact", "--session", "s", "outputs/blob.bin"])

    assert code == 0
    assert capsysbinary.readouterr().out == b"\xff\x00\xfe"


def test_the_command_refuses_a_name_that_is_not_an_artifact(at_the_command_line, capsys):
    _produced(at_the_command_line)

    code = main(["artifact", "--session", "s", ".harness/agent.yaml"])

    assert code == 2
    assert "ArtifactError" in capsys.readouterr().err
