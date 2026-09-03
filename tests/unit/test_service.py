"""The application service: wired once, asked many times."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from kingfisher import Kingfisher
from kingfisher.application.reporting import opening_events
from kingfisher.application.turn import turn_message
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.ports import CommandResult
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

    assert service._graph_for(Request("go"), session_dir) is agent

    with pytest.raises(ValueError, match="pre-built graph"):
        service._graph_for(
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

    assert service._graph_for(asked, session_dir) is not service._graph_for(
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


class FreshEachTurn:
    """A provider whose tree exists for one turn and then does not.

    The deployment this seam is for, made testable: nothing survives between
    turns except the store, so a session that remembers anything remembers it
    because it was persisted and restored rather than because a directory
    happened to still be there.
    """

    def __init__(self, root) -> None:
        self.root = Path(root)
        self.turns = 0
        self.log: list[tuple[str, tuple[str, ...]]] = []

    def _contents(self, directory) -> tuple[str, ...]:
        return tuple(sorted(p.name for p in directory.rglob("*") if p.is_file()))

    @contextmanager
    def hold(self, session_id: str):
        self.turns += 1
        directory = self.root / f"{session_id}-turn{self.turns}"
        directory.mkdir(parents=True)
        self.log.append(("held", self._contents(directory)))
        try:
            yield directory
        finally:
            self.log.append(("released", self._contents(directory)))
            shutil.rmtree(directory)


def _wired_to_a_root(cfg, tmp_path):
    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept-elsewhere")
    roots = FreshEachTurn(tmp_path / "for-one-turn")
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), sessions=kept, session_root=roots
    )
    return service, kept, roots


def test_a_turn_runs_in_the_directory_it_was_handed(cfg, tmp_path):
    """The seam itself: kingfisher asks where this session's files are rather
    than deciding, and builds the layout inside whatever it is given."""
    service, _, roots = _wired_to_a_root(cfg, tmp_path)

    result = service.run(Request(task="anything"))

    assert result.run_dir.is_relative_to(roots.root)
    assert not (cfg.workspace / "sessions" / result.session_id).exists()


def test_a_session_survives_a_tree_that_does_not(cfg, tmp_path):
    """The whole design, end to end, with nothing left on the machine.

    Turn one writes a file. Its directory is destroyed. Turn two gets a
    brand-new empty one -- and the file is there, which it can only be because
    the store was written before the tree went and read after the next one
    arrived. If the bracket were narrower than the turn on either side, this is
    the test that would say so.
    """
    service, kept, roots = _wired_to_a_root(cfg, tmp_path)
    first = service.run(Request(task="anything"))
    kept.save(first.session_id, {"derived/report.md": b"forty rows"})

    service.run(Request(task="again", session_id=first.session_id))

    arrived, at_the_end = roots.log[-2], roots.log[-1]
    assert arrived == ("held", ()), "the second turn started with nothing on the machine"
    assert "report.md" in at_the_end[1], "and had the work back before it ended"


def test_the_tree_is_released_when_a_turn_fails(cfg, tmp_path):
    """A mount left behind after every failed turn is an accumulating pile of
    other tenants' session directories, in the box this design exists to
    make safe."""

    class Fails:
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            yield ((), "values", {"messages": []})
            msg = "the model went away"
            raise RuntimeError(msg)

        def get_state(self, config):
            return None

    from kingfisher import LocalSessionStore

    roots = FreshEachTurn(tmp_path / "for-one-turn")
    service = Kingfisher(
        cfg,
        graph=Fails(),
        threads=StubCheckpointer(),
        sessions=LocalSessionStore(tmp_path / "kept"),
        session_root=roots,
    )

    with pytest.raises(RuntimeError, match="went away"):
        service.run(Request(task="anything"))

    assert [kind for kind, _ in roots.log] == ["held", "released"]


def test_the_tree_is_released_when_a_caller_walks_away(cfg, tmp_path):
    """The same claim for the other way a turn ends without finishing. A tree
    released only by the garbage collector is one held for as long as the
    caller keeps the generator, which in a shared box is somebody else's
    problem."""
    service, _, roots = _wired_to_a_root(cfg, tmp_path)

    events = service.stream(Request(task="anything"))
    next(events)
    events.close()

    assert [kind for kind, _ in roots.log] == ["held", "released"]


def _with_a_derived_file(cfg, service, session_id, name, text):
    """A file this turn produced, written where `collect_artifacts` looks."""
    directory = cfg.workspace / "sessions" / session_id
    (directory / "derived").mkdir(parents=True, exist_ok=True)
    (directory / "derived" / name).write_text(text, encoding="utf-8")


def test_a_caller_who_stops_reading_still_has_their_work_kept(cfg, tmp_path):
    """The bug this split exists for, and it was live rather than theoretical.

    `stream` is a generator whose last act is `yield self._finished(...)`, and
    persistence used to live inside that call. A caller who takes the answer and
    stops iterating never advances the body that far, so the turn's files were
    never written to the store -- and the session came back from another machine
    without them, with nothing anywhere saying so.
    """
    service, kept = _wired_to_a_store(cfg, tmp_path)
    opened = service.run(Request(task="anything"))
    _with_a_derived_file(cfg, service, opened.session_id, "half.md", "half a turn")

    events = service.stream(Request(task="again", session_id=opened.session_id))
    first = next(events)
    events.close()

    assert first.kind == "run_start", "the caller stopped before the model, which is the point"
    assert kept.fetch(opened.session_id)["derived/half.md"] == b"half a turn"
    # And the turn ended in every other sense too. `run_start` is yielded before
    # the graph is reached, and that yield used to sit outside the `try` -- so
    # stopping here left the session claimed, the checkpointer open and the
    # interpreter running. A later turn on the same session proves the slot
    # went back.
    assert service.run(Request(task="third", session_id=opened.session_id)).answer == "ok"


def test_a_turn_that_fails_keeps_what_it_made(cfg, tmp_path):
    """A behaviour change, and the one this direction implies.

    A turn used to leave its files in a directory and write nothing, which was
    survivable while the directory was the truth. Once the store is the truth,
    half a turn's work is worth more than none of it -- and the alternative is
    that the way to lose work is for the turn to go wrong.
    """

    class Fails:
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            yield ((), "values", {"messages": []})
            msg = "the model went away"
            raise RuntimeError(msg)

        def get_state(self, config):
            return None

    service, kept = _wired_to_a_store(cfg, tmp_path)
    opened = service.run(Request(task="anything"))
    _with_a_derived_file(cfg, service, opened.session_id, "partial.md", "as far as it got")

    broken = Kingfisher(cfg, graph=Fails(), threads=StubCheckpointer(), sessions=kept)
    with pytest.raises(RuntimeError, match="went away"):
        broken.run(Request(task="again", session_id=opened.session_id))

    assert kept.fetch(opened.session_id)["derived/partial.md"] == b"as far as it got"


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


def test_a_conversation_survives_losing_its_directory(cfg, tmp_path):
    """The transcript's claim, and the reason it is not the checkpointer.

    A checkpointer holds a conversation in whatever the framework chose. This
    holds it in records kingfisher owns, in a file inside the session, so the
    same store that carries results carries the history — and a machine that
    keeps nothing loses neither.
    """
    import shutil

    service, _ = _wired_to_a_store(cfg, tmp_path)
    first = service.run(Request(task="remember the number forty"))
    service.run(Request(task="and the colour blue", session_id=first.session_id))

    directory = cfg.workspace / "sessions" / first.session_id
    before = (directory / ".transcript.jsonl").read_text(encoding="utf-8")
    assert "forty" in before and "blue" in before

    # The machine goes.
    shutil.rmtree(directory)
    service.run(Request(task="what did I say?", session_id=first.session_id))

    after = (directory / ".transcript.jsonl").read_text(encoding="utf-8")
    assert "forty" in after, "the first turn is gone from the conversation"
    assert "blue" in after
    assert "what did I say?" in after


def test_the_graph_is_sent_the_whole_conversation_not_only_the_question(cfg, tmp_path):
    """Where history comes from now. The checkpointer holds one turn and nothing
    after it, so a second turn that saw only its own question would be a session
    with no memory at all."""
    service, _ = _wired_to_a_store(cfg, tmp_path)
    first = service.run(Request(task="the number is forty"))
    service.run(Request(task="and now?", session_id=first.session_id))

    sent = service._graph.state["messages"]

    assert len(sent) > 1, "only the new question reached the graph"
    assert any("forty" in str(getattr(m, "content", m)) for m in sent)


# -- a fence a deployment brings --------------------------------------------


def test_a_runner_is_built_for_each_turn_and_told_the_session(cfg, tmp_path, monkeypatch):
    """The reason this takes a callable rather than an object.

    A shared runner has no way to know which session it is running for --
    `run(command, timeout)` carries nothing -- so anything that fences *by*
    session, which is the reason to supply one, could not work. Kingfisher's own
    Landlock fence has the same shape for the same reason: its policy is
    generated from the session.

    Asserted where the runner is handed over rather than through a turn,
    because a turn that reached a real `build_agent` would need a model, and an
    injected graph skips the call this is about.
    """
    import kingfisher.application.service as service_module

    asked: list[Path] = []
    handed: list[object] = []

    class Runner:
        local = True

        def run(self, command, *, timeout=None):
            del timeout
            return CommandResult(output=f"ran {command}", exit_code=0)

    def build(session_dir: Path):
        asked.append(session_dir)
        return Runner()

    def fake_build_agent(*args, **kwargs):
        handed.append(kwargs.get("runner"))
        return StubAgent("ok")

    named = an_agent(cfg, "worker")
    monkeypatch.setattr(service_module, "build_agent", fake_build_agent)
    service = Kingfisher(cfg, threads=StubCheckpointer(), runner=build)

    first = service.run(Request(task="anything", agent=named))
    service.run(Request(task="again", agent=named, session_id=first.session_id))

    assert len(asked) == 2, "built per turn, not once"
    assert {path.name for path in asked} == {first.session_id}
    assert all(isinstance(runner, Runner) for runner in handed)


def test_a_runner_that_is_not_a_callable_is_refused_at_wiring_time(cfg):
    """With the sentence that says what to type instead. A shared instance is a
    line at the call site; the alternative is a second shape in this
    constructor forever, which is what `threads` carries."""

    class Runner:
        local = True

        def run(self, command, *, timeout=None):
            return CommandResult(output="", exit_code=0)

    with pytest.raises(TypeError, match="lambda session_dir"):
        # The type checker refuses this too, which is the point: the runtime
        # check is for callers who never run one.
        Kingfisher(
            cfg, threads=StubCheckpointer(), runner=Runner()  # ty: ignore[invalid-argument-type]
        )


def test_no_runner_leaves_the_platform_to_decide(cfg):
    """The default, and the case every existing deployment is in."""
    service = Kingfisher(cfg, threads=StubCheckpointer())

    assert service._runner is None
