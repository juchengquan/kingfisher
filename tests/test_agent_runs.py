"""What an agent definition actually does to a run.

`tests/test_agent_format.py` covers what a file may say. This covers what
saying it changes: the tools the graph holds, the model it runs, the prompt it
is given, and what a request can still take away.

The rule underneath all of it is one sentence. The agent file is the baseline
and a request only ever subtracts from it, so a caller cannot reach past what
the deployment reviewed.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.service import Kingfisher
from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.prompting import system_prompt
from tests.conftest import FakeToolCallingModel, capture_build

NARROW = """name: narrow
description: Reads and nothing else.
builtin_tools: [read_file, ls]
system_prompt: |
  You read files and say what is in them.
"""

CHEAP = """name: cheap-one
description: Runs somewhere cheaper.
alias: cheap
system_prompt: |
  You do the cheap half of the work.
"""


def _agents(cfg, *bodies: str) -> None:
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for body in bodies:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def _spec(cfg, name: str):
    from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository

    return LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[name]


def _offered(captured) -> set[str]:
    """The tools the model will actually be offered.

    Off the allowlist rather than off the compiled graph, and the difference is
    the whole mechanism: deepagents registers its whole built-in set whatever we
    say, and `ToolAllowlist` is what decides which of them reach a model call.
    Reading `tools_by_name` would show every tool for every agent and pass no
    matter what a definition declared.
    """
    for middleware in captured["middleware"]:
        allowed = getattr(middleware, "_allowed", None)
        if allowed is not None:
            return set(allowed)
    unnarrowed = "no allowlist was wired, so nothing was narrowed"
    raise AssertionError(unnarrowed)


# -- the agent decides ------------------------------------------------------


def test_an_agent_holds_only_the_tools_its_file_names(cfg, session_dir, monkeypatch):
    """The whole point of the file. `write_file` and `execute` are absent
    because the definition did not name them, not because a caller asked."""
    _agents(cfg, NARROW)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        agent=_spec(cfg, "narrow"),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    held = _offered(captured)
    assert "read_file" in held
    assert not held & {"write_file", "edit_file", "delete", "execute"}


def test_a_request_narrows_the_agent_and_cannot_widen_it(cfg, session_dir, monkeypatch):
    """A caller asking for a tool the agent never held gets the overlap, which
    is empty. That is what makes an untrusted caller safe to accept: every axis
    here subtracts, and none of them adds."""
    _agents(cfg, NARROW)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        agent=_spec(cfg, "narrow"),
        capabilities=Capabilities(builtin_tools=("read_file", "execute")),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    held = _offered(captured)
    assert "read_file" in held
    assert "execute" not in held, "a request reached past what the agent declared"


def test_the_agents_prompt_is_added_after_the_harness_and_the_workspace(cfg):
    """Three parts, most specific last. Replacing the first would not give a
    leaner agent -- it would give one holding tools nobody told it about."""
    (cfg.workspace / "PROMPT.md").write_text("House rule: be terse.", encoding="utf-8")

    assembled = system_prompt(cfg, "You read files and say what is in them.")

    harness = assembled.index("/data")
    house = assembled.index("House rule")
    mine = assembled.index("You read files")
    assert harness < house < mine


def test_the_agent_runs_the_model_its_file_names(cfg, session_dir, monkeypatch):
    """`cheap` is bound by the deployment, not written in the file: an agent
    that travels between deployments cannot portably name a vendor's id."""
    _agents(cfg, CHEAP)
    captured = capture_build(monkeypatch)

    build_agent(cfg, session_dir=session_dir, agent=_spec(cfg, "cheap-one"))

    assert captured["model"].model == "cheap-model"


def test_a_delegate_that_must_differ_is_measured_against_the_agent(cfg, session_dir):
    """The top of the chain the last change built. `second-opinion` is elsewhere
    from the deployment's default and *not* from an agent pinned to the same
    model -- and judged against the default it would look fine."""
    from kingfisher.config import ConfigError
    from tests.conftest import subagents_dir

    _agents(
        cfg,
        "name: elsewhere\ndescription: Runs elsewhere.\nmodel: elsewhere-model\n"
        "subagents: [second-opinion]\nsystem_prompt: |\n  You answer.\n",
    )
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "second-opinion.yaml").write_text(
        "name: second-opinion\ndescription: Answers again.\nalias: alternate\n"
        "distinct: true\nsystem_prompt: |\n  You answer on your own.\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="same model as whatever summoned it"):
        build_agent(
            cfg,
            session_dir=session_dir,
            agent=_spec(cfg, "elsewhere"),
            capabilities=Capabilities(subagents=("second-opinion",)),
        )


# -- naming one -------------------------------------------------------------


def test_a_request_that_names_no_agent_is_refused_once_the_workspace_has_any(cfg):
    """No default and no implicit one. The agent decides where every prompt in
    the session goes and what it costs, and a default would put that choice
    somewhere the call site never mentions."""
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg)

    with pytest.raises(CapabilityError, match="names no agent"):
        service.agent_named(None)


def test_the_refusal_lists_what_the_workspace_actually_offers(cfg):
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg)

    with pytest.raises(CapabilityError, match="cheap-one, narrow"):
        service.agent_named("analyst")


def test_an_empty_workspace_is_told_how_to_get_one(cfg):
    """The other half of that message. "No agent named x, this workspace offers
    none" is where somebody stops; naming the command is where they carry on."""
    service = Kingfisher(cfg)

    with pytest.raises(CapabilityError, match="kingfisher seed"):
        service.agent_named("analyst")


def test_naming_one_is_required_even_where_there_is_nothing_to_name(cfg):
    """No default, and no exemption for an empty workspace either.

    The softer rule -- refuse only once `agents/` holds something -- would mean
    a deployment's behaviour changing the moment somebody added a first agent,
    which is the least expected time for it to change.
    """
    with pytest.raises(CapabilityError, match="names no agent"):
        Kingfisher(cfg).agent_named(None)


def test_the_request_carries_the_name_and_nothing_more(cfg):
    """Names, never definitions -- so an untrusted caller can activate what the
    deployment reviewed and invent nothing."""
    asked = Request("go", agent="narrow")

    assert asked.agent == "narrow"
    assert asked.capabilities == Capabilities()
    assert Capabilities().builtin_tools == ALL


def test_a_request_naming_no_agent_is_still_a_valid_request(cfg):
    """Refused where the catalogue is known, not in the record. `Request` has no
    catalogue to check against, and a rule that fires in two places disagrees in
    one of them eventually."""
    assert Request("go").agent is None


# -- a session keeps the agent it opened with -------------------------------


def test_a_later_turn_runs_what_the_session_opened_with(cfg):
    """Editing an agent file mid-conversation must not change the instructions
    under a history that already happened.

    A deploy mid-session is ordinary -- the catalogue is read when a deployment
    is wired, so a restart is exactly when a live session would otherwise pick
    up a different prompt from the one its own transcript was produced under.
    """
    _agents(cfg, NARROW)
    service = Kingfisher(cfg)
    asked = Request("go", agent="narrow", session_id="s")

    opened = service._agent_for(asked, "s")
    _agents(cfg, NARROW.replace("Reads and nothing else.", "Reads and writes now."))

    assert service._agent_for(asked, "s").description == opened.description


def test_naming_a_different_agent_later_is_refused_rather_than_ignored(cfg):
    """Honouring it is wrong and ignoring it is worse: the caller asked a
    question and would be told nothing."""
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg)
    service._agent_for(Request("go", agent="narrow", session_id="s"), "s")

    with pytest.raises(CapabilityError, match="running 'narrow'"):
        service._agent_for(Request("again", agent="cheap-one", session_id="s"), "s")


def test_naming_the_same_agent_again_is_fine(cfg):
    """A stateless caller sends the same payload every turn and should not have
    to remember what it opened the session with."""
    _agents(cfg, NARROW)
    service = Kingfisher(cfg)
    asked = Request("go", agent="narrow", session_id="s")
    service._agent_for(asked, "s")

    assert service._agent_for(asked, "s").name == "narrow"


def test_a_turn_that_names_nothing_still_gets_the_sessions_agent(cfg):
    """The session decides, not the turn. A caller that named the agent when it
    opened the conversation has said everything it needs to."""
    _agents(cfg, NARROW)
    service = Kingfisher(cfg)
    service._agent_for(Request("go", agent="narrow", session_id="s"), "s")

    assert service._agent_for(Request("again", session_id="s"), "s").name == "narrow"


def test_a_snapshot_is_written_once_and_not_overwritten(tmp_path):
    """The property that makes it a snapshot rather than a cache.

    Its only caller checks first, so this holds it directly: a second writer
    added later would otherwise reintroduce exactly the thing the file exists to
    prevent, and every test above would still pass.
    """
    from kingfisher.infrastructure.workspace_fs import agent_started_with, remember_agent

    remember_agent(tmp_path, "s", "name: first\ndescription: One.\n")
    remember_agent(tmp_path, "s", "name: second\ndescription: Two.\n")

    assert agent_started_with(tmp_path, "s").startswith("name: first")
