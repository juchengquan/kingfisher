"""The application service: wired once, asked many times."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher import Kingfisher
from kingfisher.application.service import opening_events, turn_message
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.workspace_fs import DataError
from tests.conftest import StubCheckpointer, an_agent, start, subagents_dir
from tests.unit.test_run import StubAgent


class CountingCheckpointer(StubCheckpointer):
    """Counts how many times a thread store had to be opened."""

    built = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).built += 1


def test_an_injected_thread_store_is_opened_once_and_reused(cfg):
    """The reason this object exists. `stream()` used to open the thread store
    on every call; a server serving many turns opens it at startup.

    Still true for a store a deployment made itself, which is the case this was
    written for. The *default* is now a database inside each session, opened for
    the turn and closed after it -- measured at 0.22ms to reopen, against the
    orphaned threads and cross-session contention one shared file cost.
    """
    CountingCheckpointer.built = 0
    store = CountingCheckpointer()
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=store)

    for _ in range(3):
        service.run(Request("go"))

    assert CountingCheckpointer.built == 1  # three turns later, still one
    assert service.threads is store


def test_three_turns_share_one_service_and_still_get_their_own_directories(cfg):
    """Wiring is shared; per-turn state is not."""
    start(cfg, "s")
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    asked = Request("go", agent="only", session_id="s")
    turns = [service.run(asked).turn_id for _ in range(3)]
    assert turns == ["t001", "t002", "t003"]


def test_construction_prepares_only_what_sessions_share(cfg):
    """Eagerly, so a broken workspace fails at startup rather than mid-turn --
    but only the shared tier. `/data` and the rest belong to a session, whose
    path is not known until a request names it."""
    service = Kingfisher(cfg, threads=StubCheckpointer())

    assert service.workspace.is_dir()
    assert (service.workspace / "skills").is_dir()
    assert (service.workspace / "sessions").is_dir()
    assert not (service.workspace / "data").exists()


def test_an_injected_graph_is_reused_and_refuses_narrowing(cfg, session_dir):
    """Injection is by collaborator, not by monkeypatching -- and an agent
    built elsewhere cannot honour restrictions it never saw."""
    agent = StubAgent("ok")
    service = Kingfisher(cfg, graph=agent, threads=StubCheckpointer())

    assert service.graph_for(Request("go"), session_dir) is agent

    with pytest.raises(ValueError, match="pre-built graph"):
        service.graph_for(
            Request("go", capabilities=Capabilities(builtin_tools=("read_file",))), session_dir
        )


def test_a_fresh_agent_is_built_per_request(cfg, session_dir):
    """Deliberately not cached: it reads the workspace's skills and subagent
    definitions, which a user can edit between turns. ~30ms against a model
    call of seconds is not a trade worth taking."""
    # A real checkpointer: this builds a real agent, and deepagents type-checks
    # the saver it is handed.
    an_agent(cfg)
    service = Kingfisher(cfg)
    asked = Request("go", agent="only")

    assert service.graph_for(asked, session_dir) is not service.graph_for(
        asked, session_dir
    )


def test_a_session_holding_a_file_we_cannot_chmod_still_runs(cfg):
    """The bug this fixes: hardening `data/` ran before everything else, so one
    file owned by another user -- a `sudo` run, a restored backup -- aborted
    the turn, and every later turn of that session with it.

    The tool-level deny rule is still in force; the caller is told which paths
    are bare and the run proceeds.
    """
    start(cfg, "s")
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    real_chmod = Path.chmod

    def refuse_everything(self, mode, **kwargs):
        raise PermissionError(1, "Operation not permitted", str(self))

    Path.chmod = refuse_everything
    try:
        events = list(service.stream(Request("go", agent="only", session_id="s")))
    finally:
        Path.chmod = real_chmod

    assert [e.kind for e in events][-1] == "finished"
    assert [e.kind for e in events if e.kind == "protect_failed"]


def test_unhardened_paths_are_reported_to_the_caller(cfg, monkeypatch):
    """Degrading quietly would be worse than crashing: the guard is weaker than
    it looks and nobody would know."""
    start(cfg, "s")
    monkeypatch.setattr(
        "kingfisher.application.service.protect_data",
        lambda _dir: ("theirs.pdf: Operation not permitted",),
    )
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    events = list(service.stream(Request("go", agent="only", session_id="s")))
    (failed,) = [e for e in events if e.kind == "protect_failed"]

    assert "theirs.pdf" in failed.text
    assert [e.kind for e in events][-1] == "finished"  # and the run went on


def test_the_module_level_helpers_are_unchanged(cfg):
    """`run("do a thing")` was the whole public surface before this object, and
    is unaffected by it."""
    from kingfisher import run

    result = run("say hello", cfg=cfg, graph=StubAgent("hello"), checkpointer=StubCheckpointer())
    assert result.answer == "hello"


# -- what a turn opens with -----------------------------------------------
#
# Both of these were inline in `_prepare`, which is 86 lines now and was 123.
# Neither touches the service, so neither needed to be reached through a full
# run -- and reaching them that way is why the cases below went uncovered: the
# only assertion on either was one substring, through a stubbed agent.


class FakeTurn:
    virtual_dir = "/runs/t001"
    shell_dir = "runs/t001"
    virtual_input_dir = "/runs/t001/input"


class FakePlacement:
    def __init__(self, placed=(), replaced=()):
        self.placed = placed
        self.replaced = replaced


def test_a_quiet_turn_opens_with_only_run_start():
    events = opening_events("/runs/t001", (), FakePlacement())

    assert [(e.kind, e.text) for e in events] == [("run_start", "/runs/t001")]


def test_replacing_durable_data_is_counted_not_just_listed():
    """Durable data silently overwritten is the one dangerous case, so the
    count is named. Nothing asserted this before."""
    events = opening_events("/runs/t001", (), FakePlacement(("a.csv", "b.csv"), ("a.csv",)))

    (placed,) = [e for e in events if e.kind == "data_placed"]
    assert placed.text == "a.csv, b.csv (1 replaced)"


def test_placing_without_replacing_says_nothing_about_replacement():
    events = opening_events("/runs/t001", (), FakePlacement(("fresh.csv",)))

    (placed,) = [e for e in events if e.kind == "data_placed"]
    assert placed.text == "fresh.csv"


def test_unhardened_paths_are_reported_before_the_run_starts():
    """Order matters: the caller should know the guard is weaker before it is
    told the turn began."""
    events = opening_events("/runs/t001", ("theirs.pdf: denied",), FakePlacement())

    assert [e.kind for e in events] == ["protect_failed", "run_start"]


def test_a_bare_task_is_told_only_its_run_directory():
    message = turn_message("do a thing", FakeTurn(), (), has_inputs=False)

    assert message == (
        "do a thing\n\nYour run directory for this task is /runs/t001 "
        "(from the shell, runs/t001)."
    )


def test_supplied_files_and_new_data_are_both_named():
    message = turn_message("analyse", FakeTurn(), ("fresh.csv",), has_inputs=True)

    assert "/runs/t001/input" in message
    assert "New files in /data: fresh.csv." in message


def test_the_turn_message_carries_no_output_convention():
    """What the task should *produce* is the task's business. Filenames lived in
    the system prompt once and made every greeting deliberate over two files
    nobody wanted."""
    message = turn_message("say hello", FakeTurn(), (), has_inputs=False)

    assert "report" not in message.lower()
    assert ".md" not in message


# -- a refused request leaves no turn behind ------------------------------
#
# `_prepare` promised this and did not keep it. `--data` naming a missing file
# left nothing; `--input` naming one was refused *after* `allocate_turn` and
# left `t001` -- a stray turn counting against the session's own budget. The
# promise is two functions now, `_admit` and `_open_turn`, with `_Admitted`
# between them, and these are what say it is true.


#: Every way a request can be turned down over the files it names. `--input`
#: is the one that used to strand a turn; the others are the control.
REFUSALS = [
    "--data names a missing file",
    "--input names a missing file",
    "--data names one file twice",
    "--input names one file twice",
]


def _refusal(how: str, tmp_path: Path) -> dict:
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    (tmp_path / "a" / "same.csv").write_text("one")
    (tmp_path / "b" / "same.csv").write_text("two")

    field = "data" if how.startswith("--data") else "inputs"
    if "missing" in how:
        return {field: (tmp_path / "nope.csv",)}
    return {field: (tmp_path / "a" / "same.csv", tmp_path / "b" / "same.csv")}


@pytest.mark.parametrize("how", REFUSALS, ids=lambda s: s)
def test_a_refused_request_leaves_no_turn_behind(cfg, tmp_path, how):
    start(cfg, "s")
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    service.run(Request("first", session_id="s"))  # t001 is real work

    with pytest.raises(DataError):
        service.run(Request("go", session_id="s", **_refusal(how, tmp_path)))

    runs = cfg.workspace / "sessions" / "s" / "runs"
    assert sorted(p.name for p in runs.iterdir()) == ["t001"]


def test_the_admitted_request_is_what_opens_the_turn(cfg):
    """`_admit` returns; `_open_turn` takes only that. Nothing reaches the turn
    without having passed every refusal first."""
    import inspect

    assert list(inspect.signature(Kingfisher._open_turn).parameters) == ["self", "admitted"]


def test_a_narrowed_request_is_told_what_it_did_not_grant(cfg):
    """The silence this closes: the caller found out when the model reached for
    a tool mid-turn and was refused, not when the turn opened."""
    events = opening_events("/runs/t001", (), FakePlacement(), (("tool", ("execute", "ls")),))

    (said,) = [e for e in events if e.kind == "withheld"]
    assert said.text == "2 tool(s) not granted: execute, ls"


def test_an_unrestricted_request_is_told_nothing(cfg):
    """Nothing was withheld, so there is nothing to say. A line on every run
    would be noise, and noise is what gets scrolled past."""
    events = opening_events("/runs/t001", (), FakePlacement(), ())

    assert [e.kind for e in events] == ["run_start"]


def test_what_was_withheld_comes_off_the_assembled_agent(cfg, shipped):
    """Not off a list kept somewhere. The tool surface includes whatever the
    workspace defined, so the only honest answer is what was actually wired --
    which is also what makes a grant go stale when a workspace gains a tool."""
    from kingfisher.infrastructure import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg)  # adds http_fetch, sql_query, sql_tables
    service.start_session("s")

    admitted = service._admit(
        Request(
            "go",
            agent="only",
            session_id="s",
            capabilities=Capabilities(builtin_tools=("read_file",), tools=("sql_query",)),
        )
    )

    by_kind = dict(admitted.withheld)

    # The two kinds are reported apart, which is the whole point of the split.
    assert "http_fetch" in by_kind["tool"]  # a workspace tool
    assert "execute" in by_kind["builtin tool"]  # and a built-in
    assert "read_file" not in by_kind["builtin tool"]  # granted, so not withheld


def test_every_kind_a_request_can_narrow_is_reported(cfg, shipped):
    """Every axis narrows the same way and every one of them went silent the
    same way. One line per kind, and only for kinds that lost something."""
    from kingfisher.infrastructure import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg)
    service.start_session("s")

    admitted = service._admit(
        Request(
            "go",
            agent="only",
            session_id="s",
            capabilities=Capabilities(
                builtin_tools=("read_file",),
                tools=("sql_query",),
                skills=("code-review",),
                subagents=("reviewer",),
            ),
        )
    )
    by_kind = dict(admitted.withheld)

    # Asked of what `seed` actually wrote, rather than named here. The literal
    # tuples this used to assert were arithmetic about the shipped catalogue,
    # so adding a preset failed a test about *reporting* for a reason having
    # nothing to do with reporting. Sortedness is still asserted -- this is a
    # line a person reads -- but the membership comes from the catalogue.
    seeded_skills = set(LocalSkillRepository(cfg.skills_dir).names)
    seeded_subagents = set(LocalSubagentRepository(subagents_dir(cfg)).specs)
    # Not vacuous: the granted name has to be one the catalogue offers, or
    # "everything except it" would be the whole catalogue by accident.
    assert {"code-review"} < seeded_skills
    assert {"reviewer"} < seeded_subagents

    assert set(by_kind) == {"builtin tool", "tool", "skill", "subagent"}
    assert "execute" in by_kind["builtin tool"]  # granted read_file only
    assert "read_file" not in by_kind["builtin tool"]  # and it was granted
    assert "http_fetch" in by_kind["tool"]  # a workspace tool, reported apart
    assert by_kind["skill"] == tuple(sorted(seeded_skills - {"code-review"}))
    assert by_kind["subagent"] == tuple(sorted(seeded_subagents - {"reviewer"}))


def test_a_kind_that_lost_nothing_says_nothing(cfg, shipped):
    """Narrowing tools should not produce a line about skills."""
    from kingfisher.infrastructure import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg)
    service.start_session("s")

    admitted = service._admit(
        Request(
            "go",
            agent="only",
            session_id="s",
            capabilities=Capabilities(builtin_tools=("read_file",)),
        )
    )

    assert [kind for kind, _ in admitted.withheld] == ["builtin tool"]


def test_each_kind_gets_its_own_line(cfg):
    events = opening_events(
        "/runs/t001",
        (),
        FakePlacement(),
        (("tool", ("execute",)), ("subagent", ("extractor",))),
    )

    said = [e.text for e in events if e.kind == "withheld"]
    assert said == ["1 tool(s) not granted: execute", "1 subagent(s) not granted: extractor"]


# -- a session that outlives its directory ----------------------------------


def _wired_to_a_store(cfg, tmp_path):
    """A service whose sessions are kept somewhere the workspace is not."""
    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept-elsewhere")
    return Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), sessions=kept), kept


def test_a_turn_hands_what_it_produced_to_the_store(cfg, tmp_path):
    """`/derived` and `/memory` at the end of a turn, which is what
    `collect_artifacts` already names and what has to outlive the machine.

    `/memory/AGENTS.md` comes along, and that is correct rather than noise:
    `ensure_session_layout` writes the scaffold there, `/memory` is an artifact
    directory, and a restored session that had lost its project-memory file
    would be missing something a turn can edit.
    """
    service, kept = _wired_to_a_store(cfg, tmp_path)
    result = service.run(Request(task="anything"))

    directory = cfg.workspace / "sessions" / result.session_id
    (directory / "derived").mkdir(parents=True, exist_ok=True)
    (directory / "derived" / "report.md").write_text("forty rows", encoding="utf-8")
    service.run(Request(task="again", session_id=result.session_id))

    held = kept.fetch(result.session_id)
    assert held["derived/report.md"] == b"forty rows"
    assert "memory/AGENTS.md" in held
    assert not any(name.startswith(("runs/", "data/")) for name in held), (
        "scratch and uploads are not the store's to keep -- see `keep_from`"
    )


def test_a_session_whose_directory_is_gone_gets_its_files_back(cfg, tmp_path):
    """The prototype's claim, at the level a deployment sees it.

    A container holds a session's files in memory; the container goes; a later
    turn lands on a directory with nothing in it. If the files do not come back
    here, the session has forgotten its own work and every guarantee above it is
    decoration.
    """
    import shutil

    service, _ = _wired_to_a_store(cfg, tmp_path)
    first = service.run(Request(task="anything"))
    directory = cfg.workspace / "sessions" / first.session_id
    (directory / "memory").mkdir(parents=True, exist_ok=True)
    (directory / "memory" / "notes.md").write_text("remember this", encoding="utf-8")
    service.run(Request(task="save it", session_id=first.session_id))

    # The machine goes. The store is the only thing that carried over.
    shutil.rmtree(directory)
    assert not directory.exists()

    service.run(Request(task="and now?", session_id=first.session_id))

    assert (directory / "memory" / "notes.md").read_text(encoding="utf-8") == "remember this"


def test_deleting_a_session_drops_it_from_the_store_too(cfg, tmp_path):
    """Or a deleted session outlives its deletion everywhere that matters. The
    directory going is the visible half; on a host that may not hold data, the
    store is the only half that was ever durable."""
    service, kept = _wired_to_a_store(cfg, tmp_path)
    result = service.run(Request(task="anything"))
    directory = cfg.workspace / "sessions" / result.session_id
    (directory / "derived").mkdir(parents=True, exist_ok=True)
    (directory / "derived" / "a.md").write_text("one", encoding="utf-8")
    service.run(Request(task="again", session_id=result.session_id))
    assert kept.fetch(result.session_id)

    service.delete_session(result.session_id)

    assert kept.fetch(result.session_id) == {}


def test_wiring_no_store_leaves_everything_as_it_was(cfg):
    """The default, and it must stay the default: a deployment allowed to hold
    data on its own disk should notice none of this."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    result = service.run(Request(task="anything"))

    assert service.sessions_store is None
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()
