"""A session's files reached through its backend, wherever that backend keeps them."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain_core.messages import AIMessage

from kingfisher import ArtifactError, Kingfisher, Request, UnknownSessionError, default_backend
from kingfisher.domain.access import parse
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.session_files import (
    collect_artifacts,
    local_files,
    place_data,
    read_artifact,
)
from kingfisher.layout import DATA, HARNESS, SESSION_DIRS, SKILLS_ROUTE, routed_paths
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import an_agent, pin, start
from tests.unit.scripted import Scripted


def _calls(name: str, **args: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def elsewhere(root: Path):
    """A backend factory that keeps each session under `root`, never in its directory.

    What a remote sandbox looks like from here: a working filesystem the agent can
    read, write and run commands in, sharing nothing with the session directory
    kingfisher holds. Routed the way `route_coverage` asks, so it is refused for
    nothing but the thing under test.
    """

    def make(cfg, session_dir, /, *, catalogue=None, runner=None):
        kept = root / Path(session_dir).name
        for name in (*SESSION_DIRS, HARNESS):
            (kept / name).mkdir(parents=True, exist_ok=True)
        skills = (catalogue or Definitions.from_config(cfg)).skills.root
        return CompositeBackend(
            default=LocalShellBackend(root_dir=kept),
            routes={
                route: FilesystemBackend(
                    root_dir=skills if route == SKILLS_ROUTE else kept / route.strip("/")
                )
                for route in routed_paths()
            },
        )

    return make


# -- a turn ------------------------------------------------------------------


def test_a_backend_that_keeps_the_session_elsewhere_gets_the_data_and_gives_back_the_report(
    scripted, tmp_path
):
    """The failure this exists for: with the session somewhere else, data was placed in
    a directory the agent never saw and the report it wrote came back as nothing, on a
    turn that said it had worked.
    """
    an_agent(scripted)
    start(scripted, "s")
    source = tmp_path / "in.csv"
    source.write_text("alpha\n")
    remote = tmp_path / "remote"
    Scripted.script.extend([
        _calls("read_file", file_path="/data/in.csv"),
        _calls("write_file", file_path="/derived/report.txt", content="made elsewhere"),
        AIMessage(content="done"),
    ])
    kf = Kingfisher(scripted, backend=elsewhere(remote))

    events = list(kf.stream(Request("go", agent="only", session_id="s", data=(source,))))

    read = next(e.text for e in events if e.kind == "tool_result" and e.tool == "read_file")
    assert "alpha" in read, f"the agent never saw the data it was given: {read!r}"
    (finished,) = [e for e in events if e.kind == "finished"]
    assert "derived/report.txt" in finished.result.artifacts
    assert kf.artifact("s", "derived/report.txt") == b"made elsewhere"
    # And none of it went through the directory kingfisher holds, which is what makes
    # the three answers above ones only the backend could have given.
    local = scripted.workspace / "sessions" / "s"
    assert not (local / DATA / "in.csv").exists()
    assert not (local / "derived" / "report.txt").exists()


# -- fetching one ------------------------------------------------------------


def _produced(cfg, name: str = "derived/out.txt", content: str = "result") -> Kingfisher:
    start(cfg, "s")
    target = cfg.workspace / "sessions" / "s" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return Kingfisher(cfg, backend=default_backend)


def test_an_artifact_is_fetched_by_the_name_the_turn_reported(cfg):
    kf = _produced(cfg, "derived/nested/out.csv", "a,b\n")

    assert kf.artifact("s", "derived/nested/out.csv") == b"a,b\n"


@pytest.mark.parametrize(
    "name",
    [
        ".harness/agent.yaml",
        "data/in.csv",
        "scratchpad/tmp.txt",
        "derived/../.harness/agent.yaml",
        "/derived/out.txt",
        "derived",
    ],
)
def test_only_what_a_turn_produced_can_be_fetched(cfg, name):
    """The backend reaches `/.harness` and `/data` too. A caller entitled to a report is
    not thereby entitled to the pinned agent or someone's inputs.
    """
    kf = _produced(cfg)
    (cfg.workspace / "sessions" / "s" / HARNESS / "agent.yaml").write_text("secret")

    with pytest.raises(ArtifactError):
        kf.artifact("s", name)


@pytest.mark.parametrize(
    "name", ["derived/../.harness/agent.yaml", "memory/../data/in.csv", "derived"]
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
        read_artifact(Spy(), name)
    assert asked == []


def test_a_name_that_is_not_there_is_refused_by_name(cfg):
    kf = _produced(cfg)

    with pytest.raises(ArtifactError, match=r"derived/missing\.txt"):
        kf.artifact("s", "derived/missing.txt")


def test_a_session_that_is_not_there_is_the_same_error_as_ever(cfg):
    kf = Kingfisher(cfg, backend=default_backend)

    with pytest.raises(UnknownSessionError):
        kf.artifact("nobody", "derived/out.txt")


def test_a_caller_who_cannot_reach_the_session_cannot_fetch_from_it(cfg):
    """Checked as reading a session is. Holding only the id of a session pinned to an
    agent you cannot reach buys nothing, including its outputs.
    """
    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    kf = _produced(policied)
    pin(kf, "s", "only_a")

    # The control beside the escape: the same call, by a caller who does reach it.
    assert kf.artifact("s", "derived/out.txt", source_ids=("A",)) == b"result"
    with pytest.raises(UnknownSessionError):
        kf.artifact("s", "derived/out.txt", source_ids=("B",))


# -- the pieces --------------------------------------------------------------


def test_data_placed_through_the_default_backend_lands_read_only(cfg, session_dir, tmp_path):
    """`DataBackend` opens `/data` for kingfisher's upload and nothing else, so the
    promise that the agent cannot change its inputs outlives the placing.
    """
    source = tmp_path / "in.csv"
    source.write_text("x")
    backend = default_backend(cfg, session_dir)

    placement = place_data((source,), backend)

    assert placement.placed == ("in.csv",)
    assert (session_dir / DATA / "in.csv").read_text() == "x"
    assert not os.access(session_dir / DATA, os.W_OK)
    assert backend.write("/data/other.csv", "y").error, "the agent's write got through"


def test_a_refused_upload_is_a_data_error_naming_the_file(session_dir, tmp_path):
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
        routes={"/data/": Refusing(root_dir=session_dir / DATA)},
    )

    with pytest.raises(ValueError, match=r"/data/in\.csv: permission_denied"):
        place_data((source,), backend)


def test_collecting_and_reading_agree_on_every_name(session_dir):
    """Whatever `collect_artifacts` reports, `read_artifact` accepts: a name handed to a
    caller that the fetch then refused would be a result nobody can open.
    """
    (session_dir / "derived" / "deep" / "er").mkdir(parents=True)
    (session_dir / "derived" / "deep" / "er" / "x.bin").write_bytes(b"\x00\x01")
    (session_dir / "derived" / ".hidden").write_text("h")
    backend = local_files(session_dir)

    names = collect_artifacts(backend)

    assert {"derived/deep/er/x.bin", "derived/.hidden"} <= set(names)
    for name in names:
        read_artifact(backend, name)


# -- the command -------------------------------------------------------------


def test_the_command_writes_an_artifact_where_it_is_told(at_the_command_line, tmp_path, capsys):
    _produced(at_the_command_line, "derived/out.txt", "result")
    out = tmp_path / "copy.txt"

    code = main(["artifact", "--session", "s", "derived/out.txt", "--out", str(out)])

    assert code == 0
    assert out.read_text() == "result"


def test_the_command_writes_bytes_to_standard_output_untouched(
    at_the_command_line, capsysbinary
):
    """Bytes, not text: an artifact is often a PDF or an image, and a text stream would
    re-encode it on its way out.
    """
    start(at_the_command_line, "s")
    derived = at_the_command_line.workspace / "sessions" / "s" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "blob.bin").write_bytes(b"\xff\x00\xfe")

    code = main(["artifact", "--session", "s", "derived/blob.bin"])

    assert code == 0
    assert capsysbinary.readouterr().out == b"\xff\x00\xfe"


def test_the_command_refuses_a_name_that_is_not_an_artifact(at_the_command_line, capsys):
    _produced(at_the_command_line)

    code = main(["artifact", "--session", "s", ".harness/agent.yaml"])

    assert code == 2
    assert "ArtifactError" in capsys.readouterr().err
