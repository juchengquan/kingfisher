"""Middleware a definition names, from a registry a deployment supplies."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import (
    FakeToolCallingModel,
    a_subagent,
    middleware_of,
)
from tests.unit.conftest import define, offer_skills


class Audited(AgentMiddleware):
    """Stands in for whatever a deployment actually registers."""

    name = "Audited"


def build(cfg, registry=None, **caps):
    return build_agent(
        cfg,
        backend=backend_at(
            cfg, ensure_session_layout(cfg.workspace / "sessions" / "s", cfg.layout)
        ),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry=registry,
        capabilities=Capabilities(**caps),
    )


NAMES_AUDIT = (
    "name: reviewer\ndescription: d\nmiddlewares: [audit]\n"
    "system_prompt: |\n  You review.\n"
)


# -- the registry ---------------------------------------------------------


def test_a_definition_gets_the_middleware_it_names(cfg, session_dir):
    """A subagent naming `audit` built without the class the deployment registered for
    it runs without that hook, with nothing to say so.
    """
    define(cfg, NAMES_AUDIT)

    built = build(cfg, registry={"audit": Audited}, subagents=("reviewer",))

    # The deployment's own, which is what this file is about. A subagent also
    # carries guards nothing in its definition mentions -- the host-path
    # correction, and the offered tools' own failures -- and asserting the
    # whole list would make this fail every time one is added.
    assert [
        type(m).__name__
        for m in middleware_of(built, "reviewer")
        if type(m).__name__ == "Audited"
    ] == ["Audited"]


def test_the_registry_is_empty_until_a_deployment_wires_one(cfg):
    """Kingfisher cannot define these; only a deployment knows what its middleware is."""
    assert Kingfisher(cfg, sessions=LocalSessions()).middlewares == {}


def test_an_unregistered_name_fails_loudly(cfg, session_dir):
    """A mistake in the definition, and the alternative -- running without the
    middleware it asked for -- could mean running without an audit hook.
    """
    define(cfg, NAMES_AUDIT)

    with pytest.raises(CapabilityError, match="names unregistered middleware"):
        build(cfg, registry={}, subagents=("reviewer",))


def test_kingfisher_hands_its_registry_to_the_agent(cfg):
    """A `middlewares=` registry the service did not keep would build every agent
    against an empty one, refusing each name a definition writes as unregistered.
    """
    registry = {"audit": Audited}

    kf = Kingfisher(
        cfg, sessions=LocalSessions(), middlewares=registry
    )
    assert kf.middlewares is registry


# -- the clamp ------------------------------------------------------------


def test_registering_is_not_permitting(cfg, session_dir):
    """A deployment may register more than a given request may reach."""
    define(cfg, NAMES_AUDIT)

    with pytest.raises(CapabilityError, match="may not use"):
        build(
            cfg,
            registry={"audit": Audited},
            subagents=("reviewer",),
            middlewares=(),
        )


def test_a_granted_name_goes_through(cfg, session_dir):
    """The control: a clamp refusing a named middleware whatever the request granted
    would pass the rule above.
    """
    define(cfg, NAMES_AUDIT)

    built = build(
        cfg,
        registry={"audit": Audited},
        subagents=("reviewer",),
        middlewares=("audit",),
    )

    # The deployment's own, which is what this file is about. A subagent also
    # carries guards nothing in its definition mentions -- the host-path
    # correction, and the offered tools' own failures -- and asserting the
    # whole list would make this fail every time one is added.
    assert [
        type(m).__name__
        for m in middleware_of(built, "reviewer")
        if type(m).__name__ == "Audited"
    ] == ["Audited"]


def test_grants_clamp_middleware_like_everything_else():
    """A request naming `rate_limit` beyond a grant of `audit` alone would be handed it,
    if middlewares took the request's word instead of being clamped as every other axis
    is.
    """
    granted = Capabilities(middlewares=("audit",))

    narrowed = granted.intersect(Capabilities(middlewares=("audit", "rate_limit")))

    assert narrowed.middlewares == ("audit",)


# -- the format -----------------------------------------------------------


def test_the_field_parses_in_both_yaml_forms():
    inline = a_subagent(
        "name: r\ndescription: d\nmiddlewares: [a, b]\nsystem_prompt: |\n  Body.\n", "r.md"
    )
    block = a_subagent(
        "name: r\ndescription: d\nmiddlewares:\n  - a\n  - b\nsystem_prompt: |\n  Body.\n",
        "r.md",
    )

    assert inline.middlewares == block.middlewares == ("a", "b")


def test_omitting_it_means_none():
    """Read as everything, a subagent's forgotten `middlewares:` line would run every
    middleware the deployment registered.
    """
    spec = a_subagent("name: r\ndescription: d\nsystem_prompt: |\n  Body.\n", "r.md")

    assert spec.middlewares is None


def test_a_request_with_no_opinion_is_still_unrestricted():
    """A default request read as restricted would be refused by every supplied graph,
    and one withholding all middleware read as unrestricted would be served a graph
    built with it.
    """
    assert Capabilities().is_unrestricted
    assert not Capabilities(middlewares=()).is_unrestricted


# -- the whole stack, pinned once -----------------------------------------


EVERYTHING = (
    "name: reviewer\ndescription: d\n"
    "builtin_tools: [read_file, task]\ntools: [always_fails]\nskills: [tabular-qa]\n"
    "subagents: [helper]\nmiddlewares: [audit]\n"
    "system_prompt: |\n  You review.\n"
)

HELPER = "name: helper\ndescription: d\nsystem_prompt: |\n  You help.\n"

A_TOOL = '''
from langchain_core.tools import tool


@tool
def always_fails(anything: str) -> str:
    """Present so the definitions offer a tool at all."""
    raise FileNotFoundError(anything)


TOOLS = [always_fails]
'''


def test_what_a_subagent_carries_is_pinned_here_and_only_here(cfg, session_dir):
    """The exact stack, in order, for a definition that triggers every branch."""
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(A_TOOL, encoding="utf-8")
    offer_skills(cfg, "tabular-qa")
    define(cfg, EVERYTHING)
    define(cfg, HELPER, name="helper")

    # Skills on, because this stack is the one with every branch in it and a
    # subagent's index is the deployment's to switch off -- see
    # `test_a_subagent_gets_no_skills_when_the_deployment_switched_them_off`.
    built = build(
        replace(cfg, skills_enabled=True),
        registry={"audit": Audited},
        subagents=("reviewer", "helper"),
    )

    assert [type(m).__name__ for m in middleware_of(built, "reviewer")] == [
        "CurrentGraphMiddleware",
        "SignedThinkingMiddleware",
        "StepBudgetMiddleware",
        "ModelReplyMiddleware",
        "GrantedOnlyMiddleware",
        # Ahead of the entries below: it rewrites the arguments before they decide
        # anything about them.
        "GuardedCallsMiddleware",
        "ScratchRunsMiddleware",
        "AttachmentsMiddleware",
        "FilesystemMiddleware",
        "BuiltinToolsMiddleware",
        "NarrowedSkillsMiddleware",
        "SubAgentMiddleware",
        "Audited",
        "PromptCachingMiddleware",
        # Under the deployment's own, so what it raises is not answered for the tool.
        "OfferedToolFailureMiddleware",
    ]


def test_a_bare_definition_carries_only_the_guards_and_its_allowlist(cfg, session_dir):
    """The other end of the same pin, and it is not two."""
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(A_TOOL, encoding="utf-8")
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n")

    built = build(cfg, subagents=("reviewer",))

    assert [type(m).__name__ for m in middleware_of(built, "reviewer")] == [
        "CurrentGraphMiddleware",
        "SignedThinkingMiddleware",
        "StepBudgetMiddleware",
        "ModelReplyMiddleware",
        "GrantedOnlyMiddleware",
        "GuardedCallsMiddleware",
        "ScratchRunsMiddleware",
        "AttachmentsMiddleware",
        "FilesystemMiddleware",
        "BuiltinToolsMiddleware",
        "PromptCachingMiddleware",
        "OfferedToolFailureMiddleware",
    ]
