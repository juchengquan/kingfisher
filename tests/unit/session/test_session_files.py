"""A session's files reached through its backend, wherever that backend keeps them."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from typing import Any

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import GlobResult
from langchain_core.messages import AIMessage

from kingfisher import ArtifactError, Kingfisher, Request, UnknownSessionError
from kingfisher.cli.__main__ import main
from kingfisher.core.layout import (
    DEFAULT_LAYOUT,
    HARNESS,
    INPUTS,
    LEDGER_FILE,
    TURNS_FILE,
    Layout,
)
from kingfisher.core.result import LedgerEntry
from kingfisher.core.session.files import (
    InputsChanged,
    InputsPlacement,
    collect_artifacts,
    inputs_changed,
    inputs_seen,
    read_artifact,
)
from kingfisher.core.session.steps import drive
from kingfisher.core.session.transcript import Message, as_record
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import EVERY_TOOL, Through, an_agent, start, supplied
from tests.unit.scripted import Scripted
from tests.unit.stand_ins import Elsewhere


def _calls(name: str, **args: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


# -- a turn ------------------------------------------------------------------


def test_a_backend_that_keeps_the_session_elsewhere_gets_the_inputs_and_gives_back_the_report(
    scripted, tmp_path
):
    """The failure this exists for: with the session somewhere else, inputs were placed
    in a directory the agent never saw and the report it wrote came back as nothing, on a
    turn that said it had worked.
    """
    an_agent(scripted, **EVERY_TOOL)
    source = tmp_path / "in.csv"
    source.write_text("alpha\n")
    remote = tmp_path / "remote"
    Scripted.script.extend([
        _calls("read_file", file_path="/inputs/in.csv"),
        _calls("write_file", file_path="/outputs/report.txt", content="made elsewhere"),
        AIMessage(content="done"),
    ])
    kf = Kingfisher(scripted, sessions=Elsewhere(remote))

    events = list(kf.stream(Request("go", agent="only", inputs=(source,))))

    read = next(e.text for e in events if e.kind == "tool_result" and e.tool == "read_file")
    assert "alpha" in read, f"the agent never saw the inputs it was given: {read!r}"
    (finished,) = [e for e in events if e.kind == "finished"]
    session_id = finished.result.session_id
    assert "outputs/report.txt" in finished.result.artifacts
    assert kf.session(session_id).artifact("outputs/report.txt") == b"made elsewhere"
    assert kf.sessions()[0].id == session_id
    # And none of it went through the directory kingfisher holds, which is what makes
    # the answers above ones only the backend could have given.
    local = scripted.workspace / "sessions" / session_id
    assert not (local / INPUTS / "in.csv").exists()
    assert not (local / "outputs" / "report.txt").exists()


# -- fetching one ------------------------------------------------------------


def _produced(
    cfg, name: str = "outputs/out.txt", content: str = "result", *, backends=None
) -> Kingfisher:
    start(cfg, "s")
    target = cfg.workspace / "sessions" / "s" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return Kingfisher(cfg, sessions=backends or LocalSessions())


def test_an_artifact_is_fetched_by_the_name_the_turn_reported(cfg):
    """A fetch that refused a nested name the turn reported, or read anything but that
    file, leaves the caller a listed result it cannot open.
    """
    kf = _produced(cfg, "outputs/nested/out.csv", "a,b\n")

    assert Through(kf).artifact("s", "outputs/nested/out.csv") == b"a,b\n"


@pytest.mark.parametrize(
    "name",
    [
        ".harness/turns.jsonl",
        "inputs/in.csv",
        "scratchpad/tmp.txt",
        "outputs/../.harness/turns.jsonl",
        "/outputs/out.txt",
        "outputs",
    ],
)
def test_only_what_a_turn_produced_can_be_fetched(cfg, name):
    """The backend reaches `/.harness` and `/inputs` too. A caller entitled to a report is
    not thereby entitled to the conversation or someone's inputs.
    """
    kf = _produced(cfg)
    (cfg.workspace / "sessions" / "s" / HARNESS / "turns.jsonl").write_text("secret")

    with pytest.raises(ArtifactError):
        Through(kf).artifact("s", name)


@pytest.mark.parametrize(
    "name", ["outputs/../.harness/turns.jsonl", "memory/../inputs/in.csv", "outputs"]
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
        drive(read_artifact(Spy(), name, Layout()))
    assert asked == []


def test_a_name_that_is_not_there_is_refused_by_name(cfg):
    """A fetch of a missing file that returned nothing rather than refusing by name
    hands the caller an empty answer to write out as the file.
    """
    kf = _produced(cfg)

    with pytest.raises(ArtifactError, match=r"outputs/missing\.txt"):
        Through(kf).artifact("s", "outputs/missing.txt")


def test_a_session_that_is_not_there_is_the_same_error_as_ever(cfg):
    """Asked for a file in a session that does not exist, a fetch that made the session
    or answered with a missing file hides that the id is wrong.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(UnknownSessionError):
        Through(kf).artifact("nobody", "outputs/out.txt")


# -- reading one -------------------------------------------------------------


class Counting(LocalSessions):
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
def test_reading_a_session_opens_its_backend_once(cfg, read):
    """Both opened the session's backend once to decide whether the caller reached it
    and again to read through, so remote sessions handed out two sandboxes
    per query.
    """
    counting = Counting()
    kf = _produced(cfg, backends=counting)

    read(Through(kf))

    assert counting.asked == ["s"]


def test_asking_whether_a_session_exists_opens_nothing(cfg):
    """Opened to read a pin no check would consult, remote sessions handed out
    a sandbox to say that a session exists.
    """
    counting = Counting()
    kf = _produced(cfg, backends=counting)

    assert Through(kf).session("s") is not None
    assert counting.asked == []


def test_inputs_placed_through_the_default_backend_land_read_only(cfg, session_dir, tmp_path):
    """`InputsBackend` opens `/inputs` for kingfisher's upload and nothing else, so the
    promise that the agent cannot change its inputs outlives the placing.
    """
    source = tmp_path / "in.csv"
    source.write_text("x")
    backend = backend_at(cfg, session_dir)

    placement = drive(supplied((source,), backend, cfg.layout))

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
        drive(supplied((source,), backend, Layout()))


def test_a_backend_s_own_checksum_comes_before_size_and_time():
    """A file uploaded again unchanged has a new time, so read by time alone it would be
    named replaced on every turn of a caller that resends its files; the checksum is the
    field a backend owner fills to prevent that, and a renamed one would go unread.
    """

    class Listing:
        def __init__(self, *entries: dict) -> None:
            self.entries: list[Any] = [{"path": f"/inputs/{e.pop('name')}", **e} for e in entries]

        def glob(self, pattern: str, path: str) -> GlobResult:
            return GlobResult(matches=self.entries if pattern == "**" else [])

    def seen(*entries: dict) -> dict:
        return drive(inputs_seen(Listing(*entries), DEFAULT_LAYOUT))

    before = seen(
        {"name": "same.csv", "checksum": "e1", "size": 1, "modified_at": "t1"},
        {"name": "edited.csv", "size": 1, "modified_at": "t1"},
        {"name": "bare.csv"},
    )
    now = seen(
        {"name": "same.csv", "checksum": "e1", "size": 1, "modified_at": "t2"},
        {"name": "edited.csv", "size": 1, "modified_at": "t2"},
        {"name": "bare.csv"},
    )

    assert inputs_changed(before, now, InputsPlacement()) == InputsChanged(
        replaced=("edited.csv",)
    )


def test_collecting_and_reading_agree_on_every_name(cfg, session_dir):
    """Whatever `collect_artifacts` reports, `read_artifact` accepts: a name handed to a
    caller that the fetch then refused would be a result nobody can open.
    """
    (session_dir / "outputs" / "deep" / "er").mkdir(parents=True)
    (session_dir / "outputs" / "deep" / "er" / "x.bin").write_bytes(b"\x00\x01")
    (session_dir / "outputs" / ".hidden").write_text("h")
    backend = backend_at(cfg, session_dir)

    names = drive(collect_artifacts(backend, cfg.layout))

    assert {"outputs/deep/er/x.bin", "outputs/.hidden"} <= set(names)
    for name in names:
        drive(read_artifact(backend, name, cfg.layout))


def test_a_name_starting_with_a_dot_is_collected_at_any_depth(cfg, session_dir):
    """deepagents' glob leaves a dotfile out of `**`, so a walk asking only for that
    drops `/outputs/.env` from what the caller is handed, and says nothing. Inside a
    dot-folder too, which `**/.*` would miss.
    """
    for name in ("plain.txt", ".env", "deep/.dot", ".cache/x.txt", ".cache/.y"):
        (session_dir / "outputs" / name).parent.mkdir(parents=True, exist_ok=True)
        (session_dir / "outputs" / name).write_text("x")

    names = drive(collect_artifacts(backend_at(cfg, session_dir), cfg.layout))

    assert {
        "outputs/plain.txt",
        "outputs/.env",
        "outputs/deep/.dot",
        "outputs/.cache/x.txt",
        "outputs/.cache/.y",
    } <= set(names)


# -- the command -------------------------------------------------------------


def test_the_command_writes_an_artifact_where_it_is_told(at_the_command_line, tmp_path, capsys):
    """An `artifact --out` that wrote somewhere else, or not at all, tells the person it
    wrote a copy that is not where they asked.
    """
    _produced(at_the_command_line, "outputs/out.txt", "result")
    out = tmp_path / "copy.txt"

    code = main(["artifact", "--session", "s", "outputs/out.txt", "--out", str(out)])

    assert code == 0
    assert out.read_text() == "result"


def test_the_command_makes_the_directory_it_is_told_to_write_into(
    at_the_command_line, tmp_path, capsys
):
    """`--out` into a directory not there yet was a `FileNotFoundError` traceback, after
    the bytes had been fetched; `--log` makes its own."""
    _produced(at_the_command_line, "outputs/out.txt", "result")
    out = tmp_path / "not" / "yet" / "copy.txt"

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


def test_the_command_prints_a_result_by_its_id_and_refuses_one_it_has_not(
    at_the_command_line, capsys
):
    """A row names no file while its result is in the conversation, so this is how a
    person at a terminal reads one; an id no row has is a refusal, not a traceback."""
    _produced(at_the_command_line)
    harness = at_the_command_line.workspace / "sessions" / "s" / HARNESS
    harness.mkdir(parents=True, exist_ok=True)
    entry = LedgerEntry(
        id="A-0001", turn="t1", call="c1", run=None, role="main", tool="web_search",
        args="", access=None, size=len("Ten results"), path=None, summary="web_search",
    )
    rows = [json.dumps(asdict(entry)), json.dumps({"turn": "t1"})]
    (harness / LEDGER_FILE).write_text("\n".join(rows) + "\n")
    said = Message(role="tool", content="Ten results\n[A-0001]", call_id="c1", name="web_search")
    logged = [{"turn": "t1", "parent": None}, {"turn": "t1", **as_record(said)}]
    (harness / TURNS_FILE).write_text("".join(json.dumps(one) + "\n" for one in logged))

    assert main(["result", "--session", "s", "A-0001"]) == 0
    assert capsys.readouterr().out == "Ten results"
    assert main(["result", "--session", "s", "A-0009"]) == 2
    assert "ResultError: no result A-0009" in capsys.readouterr().err


def test_the_command_refuses_a_name_that_is_not_an_artifact(at_the_command_line, capsys):
    """An `ArtifactError` the command does not catch exits 1, the code its help gives a
    turn that ran and stopped, with a traceback where the refusal belongs.
    """
    _produced(at_the_command_line)

    code = main(["artifact", "--session", "s", ".harness/turns.jsonl"])

    assert code == 2
    assert "ArtifactError" in capsys.readouterr().err
