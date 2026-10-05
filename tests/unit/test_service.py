"""The application service: wired once, asked many times."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kingfisher import DefaultBackends, Kingfisher, default_backends
from kingfisher.application.reporting import opening_events
from kingfisher.application.service import refused_credentials
from kingfisher.application.turn import turn_message
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.domain.ports import CommandResult
from kingfisher.domain.request import Request
from kingfisher.infrastructure.steps import drive
from kingfisher.infrastructure.workspace import ensure_session_layout
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.layout import HARNESS, PINNED_AGENT
from tests.conftest import (
    FAKE_ENDPOINT,
    OTHER_ENDPOINT,
    StubCheckpointer,
    an_agent,
    harness_in,
    start,
    subagents_dir,
)
from tests.unit.test_run import StubAgent


def agent_snapshot(session_dir) -> Path:
    """Where the default backend keeps a session's pinned agent, on disk."""
    return Path(session_dir) / HARNESS / PINNED_AGENT


class CountingCheckpointer(StubCheckpointer):
    """Counts how many times a thread store had to be opened."""

    built = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).built += 1


def test_an_injected_thread_store_is_opened_once_and_reused(cfg):
    """The reason this object exists.

    Still true for a store a deployment made itself, which is the case this was
    written for. The *default* is now a database inside each session, opened for the
    turn and closed after it -- measured at 0.22ms to reopen, against the orphaned
    threads and cross-session contention one shared file cost.
    """
    CountingCheckpointer.built = 0
    store = CountingCheckpointer()
    service = Kingfisher(cfg, graph=StubAgent("ok"), backends=default_backends, threads=store)

    for _ in range(3):
        service.run(Request("go"))

    assert CountingCheckpointer.built == 1  # three turns later, still one
    assert service.threads is store


def test_three_turns_share_one_service_and_still_get_their_own_directories(cfg):
    """Wiring is shared; per-turn state is not."""
    start(cfg, "s")
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )

    asked = Request("go", agent="only", session_id="s")
    turns = [service.run(asked).turn_id for _ in range(3)]
    assert len(set(turns)) == 3, "three turns, three names"


def test_construction_prepares_only_what_sessions_share(cfg):
    """Eagerly, so a broken workspace fails at startup rather than mid-turn -- but only
    the shared tier.
    """
    service = Kingfisher(cfg, backends=default_backends, threads=StubCheckpointer())

    assert service.workspace.is_dir()
    assert (service.workspace / "skills").is_dir()
    assert (service.workspace / "sessions").is_dir()
    assert not (service.workspace / "data").exists()


def test_an_injected_graph_is_reused_and_refuses_narrowing(cfg, session_dir):
    """Injection is by collaborator, not by monkeypatching -- and an agent built
    elsewhere cannot honour restrictions it never saw.
    """
    agent = StubAgent("ok")
    service = Kingfisher(cfg, graph=agent, backends=default_backends, threads=StubCheckpointer())

    # No spec and no source ids: a supplied graph is handed back before either is
    # looked at, which is the thing this asserts.
    asked = Request("go")
    assert service._graph_for(
        asked, asked.capabilities, agent=None, held=None, files=None
    ) is agent

    with pytest.raises(ValueError, match="pre-built graph"):
        narrowed = Request("go", capabilities=Capabilities(builtin_tools=("read_file",)))
        service._graph_for(
            narrowed, narrowed.capabilities, agent=None, held=None, files=None
        )


def test_a_fresh_agent_is_built_per_request(cfg, session_dir):
    """Deliberately not cached: it reads the workspace's skills and subagent
    definitions, which a user can edit between turns.
    """
    # A real checkpointer: this builds a real agent, and deepagents type-checks
    # the saver it is handed.
    an_agent(cfg)
    service = Kingfisher(cfg, backends=default_backends)
    asked = Request("go", agent="only")

    built = drive(service._agent_for(asked, harness_in(session_dir)))
    files = drive(service._files_for(session_dir.name, session_dir))

    def once():
        return service._graph_for(
            asked, service.grants, agent=built, held=None, files=files
        )

    assert once() is not once()


def test_a_session_holding_a_file_we_cannot_chmod_still_runs(cfg):
    """The bug this fixes: hardening `data/` ran before everything else, so one file
    owned by another user -- a `sudo` run, a restored backup -- aborted the turn, and
    every later turn of that session with it.
    """
    start(cfg, "s")
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )

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
    """Degrading quietly would be worse than crashing: the guard is weaker than it looks
    and nobody would know.
    """
    start(cfg, "s")
    monkeypatch.setattr(
        "kingfisher.infrastructure.harness.backend.protect_data",
        lambda _dir: ("theirs.pdf: Operation not permitted",),
    )
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )

    events = list(service.stream(Request("go", agent="only", session_id="s")))
    (failed,) = [e for e in events if e.kind == "protect_failed"]

    assert "theirs.pdf" in failed.text
    assert [e.kind for e in events][-1] == "finished"  # and the run went on


def test_the_module_level_helpers_are_unchanged(cfg):
    """`run("do a thing")` was the whole public surface before this object, and is
    unaffected by it.
    """
    from kingfisher import run

    result = run(
        "say hello",
        cfg=cfg,
        graph=StubAgent("hello"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )
    assert result.answer == "hello"


# -- what a turn opens with -----------------------------------------------
#
# Both of these were inline in setup, when it was one function of 123 lines.
# Neither touches the service, so neither needed to be reached through a full
# run -- and reaching them that way is why the cases below went uncovered: the
# only assertion on either was one substring, through a stubbed agent.


class FakePlacement:
    def __init__(self, placed=(), replaced=()):
        self.placed = placed
        self.replaced = replaced


def test_a_quiet_turn_opens_with_only_run_start():
    events = opening_events("t001", (), FakePlacement())

    assert [(e.kind, e.text) for e in events] == [("run_start", "t001")]


def test_replacing_durable_data_is_counted_not_just_listed():
    """Durable data silently overwritten is the one dangerous case, so the count is
    named.
    """
    events = opening_events("t001", (), FakePlacement(("a.csv", "b.csv"), ("a.csv",)))

    (placed,) = [e for e in events if e.kind == "data_placed"]
    assert placed.text == "a.csv, b.csv (1 replaced)"


def test_placing_without_replacing_says_nothing_about_replacement():
    events = opening_events("t001", (), FakePlacement(("fresh.csv",)))

    (placed,) = [e for e in events if e.kind == "data_placed"]
    assert placed.text == "fresh.csv"


def test_unhardened_paths_are_reported_before_the_run_starts():
    """Order matters: the caller should know the guard is weaker before it is told the
    turn began.
    """
    events = opening_events("t001", ("theirs.pdf: denied",), FakePlacement())

    assert [e.kind for e in events] == ["protect_failed", "run_start"]


def test_a_bare_task_is_told_only_where_to_work():
    message = turn_message("do a thing", ())

    assert message == (
        "do a thing\n\n/scratchpad/ is yours to work in (from the shell, scratchpad)."
    )


def test_files_supplied_with_the_request_are_named():
    """They went to a directory of the turn's own and are session data now, so the
    one line that used to name two places names one.
    """
    message = turn_message("analyse", ("fresh.csv",))

    assert "New files in /data: fresh.csv." in message


def test_the_turn_message_carries_no_output_convention():
    """What the task should *produce* is the task's business."""
    message = turn_message("say hello", ())

    assert "report" not in message.lower()
    assert ".md" not in message


# -- a refused request leaves no turn behind ------------------------------
#
# `--input` naming a missing file was refused once *after* `allocate_turn`, and left
# `t001` behind -- a stray turn counting against the session's own budget. Every
# refusal comes before `run_start` now, and the claim taken before them goes back.


#: Every way a request is turned down after its session is claimed: over the files
#: it names, and over the session already being too large.
REFUSALS = ["data names a missing file", "data names one file twice", "session over budget"]


@pytest.mark.parametrize("how", REFUSALS)
def test_a_refused_request_starts_no_turn_and_keeps_no_claim(cfg, tmp_path, how):
    """Refused after the claim and before it went back, the session stayed claimed
    until the claim went stale; refused after `run_start`, the run log held a turn that
    never ran.
    """
    from kingfisher.domain.session import QuotaExceededError
    from kingfisher.infrastructure.workspace.placement import DataError
    from tests.conftest import RecordedEvents, start
    from tests.unit.test_tenancy import _claim

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.csv").write_text("one")
    (tmp_path / "b" / "same.csv").write_text("two")
    data = {
        "data names a missing file": (tmp_path / "nope.csv",),
        "data names one file twice": (tmp_path / "a" / "same.csv", tmp_path / "b" / "same.csv"),
        "session over budget": (),
    }[how]
    budgeted = replace(cfg, session_max_bytes=1) if how == "session over budget" else cfg
    session = start(budgeted, "s")
    (budgeted.workspace / "sessions" / session / "derived" / "big.txt").write_text("x" * 64)
    recorded = RecordedEvents()
    kf = Kingfisher(
        budgeted,
        graph=StubAgent("ok"),
        backends=default_backends,
        threads=StubCheckpointer(),
        run_events=recorded,
    )

    with pytest.raises((DataError, QuotaExceededError)):
        kf.run(Request("go", session_id=session, data=data))

    assert recorded.named("run_start") == []
    assert not _claim(budgeted, session).exists()


def test_a_narrowed_request_is_told_what_it_did_not_grant(cfg):
    """The silence this closes: the caller found out when the model reached for a tool
    mid-turn and was refused, not when the turn opened.
    """
    events = opening_events("t001", (), FakePlacement(), (("tool", ("execute", "ls")),))

    (said,) = [e for e in events if e.kind == "withheld"]
    assert said.text == "2 tool(s) not granted: execute, ls"


def test_an_unrestricted_request_is_told_nothing(cfg):
    """Nothing was withheld, so there is nothing to say."""
    events = opening_events("t001", (), FakePlacement(), ())

    assert [e.kind for e in events] == ["run_start"]


def test_what_was_withheld_comes_off_the_assembled_agent(cfg, shipped):
    """Not off a list kept somewhere."""
    from kingfisher.infrastructure.workspace import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg, backends=default_backends)  # adds http_fetch, sql_query, sql_tables
    start(cfg, "s")
    asked = Request(
        "go",
        agent="only",
        session_id="s",
        capabilities=Capabilities(builtin_tools=("read_file",), tools=("sql_query",)),
    )

    prepared = drive(service._prepare_steps(asked))

    by_kind = dict(prepared.withheld)

    # The two kinds are reported apart, which is the whole point of the split.
    assert "http_fetch" in by_kind["tool"]  # a workspace tool
    assert "execute" in by_kind["builtin tool"]  # and a built-in
    assert "read_file" not in by_kind["builtin tool"]  # granted, so not withheld


def test_every_kind_a_request_can_narrow_is_reported(cfg, shipped):
    """Every axis narrows the same way and every one of them went silent the same way."""
    from kingfisher.infrastructure.workspace import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg, backends=default_backends)
    start(cfg, "s")
    asked = Request(
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

    prepared = drive(service._prepare_steps(asked))

    by_kind = dict(prepared.withheld)

    # Asked of what `seed` actually wrote, rather than named here. The literal
    # tuples this used to assert were arithmetic about the shipped catalogue,
    # so adding a preset failed a test about *reporting* for a reason having
    # nothing to do with reporting. Sortedness is still asserted -- this is a
    # line a person reads -- but the membership comes from the catalogue.
    # `available_skills`, because the report measures against what the *run* was
    # offered, which resolves a skill in a source folder -- `incident::postmortem`.
    # A directory listing did not show that one, and the two agreed until a sourced
    # skill shipped.
    from kingfisher.infrastructure.harness.activation import available_skills

    seeded_skills = set(available_skills(cfg))
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
    from kingfisher.infrastructure.workspace import seeding

    # Seeded before the service, not after: a catalogue is read when a
    # deployment is wired, so definitions written afterwards are not its.
    seeding.seed(cfg, shipped)
    # An agent of this test's own. `assistant` declares three delegates, and
    # what those name is a different subject from a withheld-tool report --
    # narrowing tools to `sql_query` refuses `profiler` before it gets here.
    an_agent(cfg)
    service = Kingfisher(cfg, backends=default_backends)
    start(cfg, "s")
    asked = Request(
        "go",
        agent="only",
        session_id="s",
        capabilities=Capabilities(builtin_tools=("read_file",)),
    )

    prepared = drive(service._prepare_steps(asked))

    assert [kind for kind, _ in prepared.withheld] == ["builtin tool"]


def test_each_kind_gets_its_own_line(cfg):
    events = opening_events(
        "t001",
        (),
        FakePlacement(),
        (("tool", ("execute",)), ("subagent", ("extractor",))),
    )

    said = [e.text for e in events if e.kind == "withheld"]
    assert said == ["1 tool(s) not granted: execute", "1 subagent(s) not granted: extractor"]


# -- a turn that ends early --------------------------------------------------


def test_a_caller_who_stops_reading_still_gives_the_session_back(cfg):
    """`run_start` is yielded before the graph is reached, and that yield used to sit
    outside the `try` -- so stopping here left the session claimed, the checkpointer
    open and the interpreter running. A later turn on the same session proves the
    slot went back.
    """
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )
    opened = service.run(Request(task="anything"))

    events = service.stream(Request(task="again", session_id=opened.session_id))
    first = next(events)
    events.close()

    assert first.kind == "run_start", "the caller stopped before the model, which is the point"
    assert service.run(Request(task="third", session_id=opened.session_id)).answer == "ok"


def test_the_graph_is_sent_the_whole_conversation_not_only_the_question(cfg):
    """Where history comes from now."""
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )
    first = service.run(Request(task="the number is forty"))
    service.run(Request(task="and now?", session_id=first.session_id))

    sent = service._graph.state["messages"]

    assert len(sent) > 1, "only the new question reached the graph"
    assert any("forty" in str(getattr(m, "content", m)) for m in sent)


# -- a fence a deployment brings --------------------------------------------


def test_a_runner_is_built_for_each_turn_and_told_the_session(cfg, tmp_path, monkeypatch):
    """The reason this takes a callable rather than an object.

    Read off the arguments `open` is given, because that is where a runner goes
    now: a service that built one per turn and then handed it to nothing would
    satisfy the first two assertions on its own.
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

    class Recording(DefaultBackends):
        def open(self, cfg_, session_id, /, *, catalogue=None, runner=None):
            handed.append(runner)
            return super().open(cfg_, session_id, catalogue=catalogue, runner=runner)

    named = an_agent(cfg, "worker")
    monkeypatch.setattr(service_module, "build_agent", lambda *a, **kw: StubAgent("ok"))
    service = Kingfisher(cfg, backends=Recording(), threads=StubCheckpointer(), runner=build)

    first = service.run(Request(task="anything", agent=named))
    service.run(Request(task="again", agent=named, session_id=first.session_id))

    assert len(asked) == 2, "built per turn, not once"
    assert {path.name for path in asked} == {first.session_id}
    assert all(isinstance(runner, Runner) for runner in handed)


def test_a_runner_that_is_not_a_callable_is_refused_at_wiring_time(cfg):
    """With the sentence that says what to type instead."""

    class Runner:
        local = True

        def run(self, command, *, timeout=None):
            return CommandResult(output="", exit_code=0)

    with pytest.raises(TypeError, match="lambda session_dir"):
        # The type checker refuses this too, which is the point: the runtime
        # check is for callers who never run one.
        Kingfisher(
            cfg, backends=default_backends, threads=StubCheckpointer(), runner=Runner()  # ty: ignore[invalid-argument-type]
        )


def test_no_runner_leaves_the_platform_to_decide(cfg):
    """The default, and the case every existing deployment is in."""
    service = Kingfisher(cfg, backends=default_backends, threads=StubCheckpointer())

    assert service._runner is None


# -- a provider that will not take the key ----------------------------------


class _RejectedKeyError(Exception):
    """Shaped like a provider's 401 without importing one.

    `status_code` and `response.request.url` are what `anthropic` and `openai` both
    carry, and matching on those rather than on a class is what lets one branch cover
    every adapter.
    """

    def __init__(self, url: str, status: int = 401) -> None:
        said = f"Error code: {status}"
        super().__init__(said)
        self.status_code = status
        self.response = SimpleNamespace(request=SimpleNamespace(url=url))


def test_a_rejected_key_names_the_variable_it_came_from(cfg):
    """A 401 reached the terminal as a provider traceback that named no variable, so a
    reader went to `models.yaml`, where the `key_env` line is correct and the value it
    points at is not.
    """
    models = replace(
        cfg.models, endpoints={"fake": replace(FAKE_ENDPOINT, key_env="FAKE_API_KEY")}
    )
    refused = refused_credentials(
        _RejectedKeyError(f"{FAKE_ENDPOINT.base_url}/v1/messages"), replace(cfg, models=models)
    )

    assert refused is not None
    assert "endpoint 'fake'" in str(refused)
    assert "FAKE_API_KEY" in str(refused)
    # The half that explains why a correct-looking file was not what was sent.
    assert ".env" in str(refused)


def test_the_endpoint_named_is_the_one_that_refused(cfg):
    """Not the default. A delegate may run somewhere its parent does not, so resolving
    the default model for the name would be confidently wrong on the one 401 that
    needed a different answer.
    """
    refused = refused_credentials(_RejectedKeyError(f"{OTHER_ENDPOINT.base_url}/v1/messages"), cfg)

    assert refused is not None and "'elsewhere'" in str(refused)
    assert "'fake'" not in str(refused), "named the default rather than the one that failed"


def test_anything_but_a_401_keeps_its_traceback(cfg):
    """The control beside the escape. A translation that caught one status too many
    would turn a bug in the graph into a configuration error nobody can act on.
    """
    assert refused_credentials(RuntimeError("the model went away"), cfg) is None
    assert refused_credentials(_RejectedKeyError(FAKE_ENDPOINT.base_url, status=500), cfg) is None


def test_a_turn_translates_a_rejected_key_rather_than_raising_the_providers_error(cfg):
    """Driven rather than inspected: the translation lives in the turn's own `except`,
    and a test that only called `refused_credentials` would still pass with that branch
    deleted.
    """

    class Rejects:
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            yield ((), "values", {"messages": []})
            where = f"{FAKE_ENDPOINT.base_url}/v1/messages"
            raise _RejectedKeyError(where)

        def get_state(self, config):
            return None

    service = Kingfisher(
        cfg, graph=Rejects(), backends=default_backends, threads=StubCheckpointer()
    )

    with pytest.raises(ConfigError, match="rejected the key"):
        service.run(Request(task="anything"))


def _snapshot_reads(monkeypatch) -> list[Path]:
    """Every read of a session's pinned agent, as they happen."""
    from kingfisher.application import service as service_module

    seen: list[Path] = []
    real = service_module.agent_started_with

    def counting(session_dir):
        seen.append(session_dir)
        return real(session_dir)

    monkeypatch.setattr(service_module, "agent_started_with", counting)
    return seen


def _policied(cfg):
    """A deployment with a vocabulary, which is the only case that resolved twice."""
    import yaml

    from kingfisher.domain.access import parse

    an_agent(cfg, "only", source_ids="[A]")
    return replace(cfg, access=parse(yaml.safe_load("source_ids: [A]\n"), source="s.yaml"))


def test_a_turn_resolves_its_agent_once(cfg, monkeypatch):
    """Measured at two, and down different branches of the same function: the first
    call resolved from the catalogue and wrote the pin, the second read that pin back
    and parsed it. Counted here rather than reasoned about, because the second call
    was invisible -- both answers agreed, so nothing was ever wrong.

    Under a policy, because that is the one deployment where the second reader --
    the withheld report -- asks for the spec at all. Without one it never did, so a
    turn here would count one either way and this would pass against the defect.
    """
    from kingfisher.application import service as service_module

    service = Kingfisher(_policied(cfg), backends=default_backends)
    reads = _snapshot_reads(monkeypatch)
    reported: list[object] = []
    real = service_module.withheld_by_kind

    def spying(*args, **kwargs):
        reported.append(kwargs.get("agent"))
        return real(*args, **kwargs)

    monkeypatch.setattr(service_module, "withheld_by_kind", spying)

    # The first event is everything before the model is reached, which is where
    # admission happens and the only part of a turn this is about.
    events = service.stream(Request("go", agent="only"), source_ids=("A",))
    try:
        next(events)
    finally:
        events.close()

    assert len(reads) == 1, f"the pinned agent was read {len(reads)} times in one turn"
    # And the one resolution reached the second reader. Handing it `None` would leave
    # the report measured against the whole catalogue, which is what it filters.
    assert reported and reported[0] is not None


def test_a_supplied_graph_with_no_policy_is_never_asked_which_agent(cfg, monkeypatch):
    """The condition the single resolution is guarded by, and it is not an
    optimisation: such a deployment has no reader for the spec, and resolving one
    anyway would make a session start refusing a request that names a different agent
    mid-conversation -- which for a supplied graph it does not.
    """
    an_agent(cfg, "only")
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )
    reads = _snapshot_reads(monkeypatch)

    service.run(Request("go", agent="only"))

    assert reads == []


def test_a_supplied_graph_under_a_policy_still_resolves_one(cfg, monkeypatch):
    """The other half of that condition, and the half with a reader. A supplied graph
    is handed back without an agent being resolved for it -- but the withheld report
    still filters by what the agent declares, so under a policy the spec is asked for
    even though no build wanted it.
    """
    from kingfisher.application import service as service_module

    service = Kingfisher(
        _policied(cfg), graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )
    reads = _snapshot_reads(monkeypatch)
    reported: list[object] = []
    real = service_module.withheld_by_kind

    def spying(*args, **kwargs):
        reported.append(kwargs.get("agent"))
        return real(*args, **kwargs)

    monkeypatch.setattr(service_module, "withheld_by_kind", spying)

    service.run(Request("go", agent="only"), source_ids=("A",))

    assert len(reads) == 1
    assert reported and reported[0] is not None


def test_the_pinned_agent_is_kept_where_the_turn_runs(cfg, tmp_path):
    """The pin goes wherever the session's own files are, never to a path re-derived
    from its id. It was once written to `<workspace>/sessions/<id>` whatever held the
    session, so `agent_started_with` found none on the next turn and a session fixed to
    the agent it opened with silently was not.

    Driven through `_agent_for` rather than `run`, because that is what resolves a
    turn's agent and writes the pin -- and a supplied graph never reaches it.
    """
    an_agent(cfg, "only")
    an_agent(cfg, "other")
    # A session whose files are not under the workspace at all.
    elsewhere = ensure_session_layout(tmp_path / "for-one-turn" / "a-session")
    service = Kingfisher(cfg, backends=default_backends)

    drive(service._agent_for(Request("go", agent="only"), harness_in(elsewhere)))

    assert agent_snapshot(elsewhere).is_file(), "the pin is not where the turn ran"
    assert not (cfg.workspace / "sessions" / elsewhere.name).exists(), (
        "the pin was written under the workspace, which is not this session"
    )

    with pytest.raises(CapabilityError, match="cannot be changed"):
        drive(service._agent_for(Request("again", agent="other"), harness_in(elsewhere)))
