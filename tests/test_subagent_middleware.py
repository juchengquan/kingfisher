"""Middleware a definition names, from a registry a deployment supplies.

The one field that selects *code* rather than content, which is why it is the
one an uploaded definition gets no exemption for.
"""

from __future__ import annotations

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel, StubCheckpointer, capture_build


class Audited(AgentMiddleware):
    """Stands in for whatever a deployment actually registers."""

    name = "Audited"


def define(cfg, body: str, name: str = "reviewer") -> None:
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / f"{name}.yaml").write_text(body, encoding="utf-8")


def build(cfg, monkeypatch, registry=None, **caps):
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=cfg.workspace / "sessions" / "s",
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry=registry,
        capabilities=Capabilities(**caps),
    )
    return captured


def middleware_of(captured, name: str) -> list:
    (spec,) = [s for s in captured["subagents"] if s["name"] == name]
    return spec.get("middleware", [])


NAMES_AUDIT = (
    "name: reviewer\ndescription: d\nmiddleware: [audit]\n"
    "system_prompt: |\n  You review.\n"
)


# -- the registry ---------------------------------------------------------


def test_a_definition_gets_the_middleware_it_names(cfg, session_dir, monkeypatch):
    define(cfg, NAMES_AUDIT)

    captured = build(cfg, monkeypatch, registry={"audit": Audited}, subagents=("reviewer",))

    # The deployment's own, which is what this file is about. A delegate also
    # carries guards nothing in its definition mentions -- the host-path
    # correction, and the workspace tools' own failures -- and asserting the
    # whole list would make this fail every time one is added.
    assert [
        type(m).__name__
        for m in middleware_of(captured, "reviewer")
        if type(m).__name__ == "Audited"
    ] == ["Audited"]


def test_the_registry_is_empty_until_a_deployment_wires_one(cfg):
    """Kingfisher cannot define these; only a deployment knows what its
    middleware is. So a `middleware:` line fails until someone says what it
    means, rather than being quietly ignored."""
    assert Kingfisher(cfg, threads=StubCheckpointer()).middleware == {}


def test_an_unregistered_name_fails_loudly(cfg, session_dir, monkeypatch):
    """A mistake in the definition, and the alternative -- running without the
    middleware it asked for -- could mean running without an audit hook."""
    define(cfg, NAMES_AUDIT)

    with pytest.raises(CapabilityError, match="names unregistered middleware"):
        build(cfg, monkeypatch, registry={}, subagents=("reviewer",))


def test_kingfisher_hands_its_registry_to_the_agent(cfg):
    registry = {"audit": Audited}

    assert Kingfisher(cfg, threads=StubCheckpointer(), middleware=registry).middleware is registry


# -- the clamp ------------------------------------------------------------


def test_registering_is_not_permitting(cfg, session_dir, monkeypatch):
    """A deployment may register more than a given request may reach."""
    define(cfg, NAMES_AUDIT)

    with pytest.raises(CapabilityError, match="may not use"):
        build(
            cfg,
            monkeypatch,
            registry={"audit": Audited},
            subagents=("reviewer",),
            middleware=(),
        )


def test_a_granted_name_goes_through(cfg, session_dir, monkeypatch):
    define(cfg, NAMES_AUDIT)

    captured = build(
        cfg,
        monkeypatch,
        registry={"audit": Audited},
        subagents=("reviewer",),
        middleware=("audit",),
    )

    # The deployment's own, which is what this file is about. A delegate also
    # carries guards nothing in its definition mentions -- the host-path
    # correction, and the workspace tools' own failures -- and asserting the
    # whole list would make this fail every time one is added.
    assert [
        type(m).__name__
        for m in middleware_of(captured, "reviewer")
        if type(m).__name__ == "Audited"
    ] == ["Audited"]


def test_grants_clamp_middleware_like_everything_else():
    granted = Capabilities(middleware=("audit",))

    narrowed = granted.intersect(Capabilities(middleware=("audit", "rate_limit")))

    assert narrowed.middleware == ("audit",)


# -- the rule that inverts for uploads ------------------------------------


def test_an_upload_widens_its_own_text_and_nothing_else():
    """Skills and subagents an upload brings are the caller's own text, so
    permitting them grants nothing new. Middleware is not widened at all."""
    granted = Capabilities(skills=("vetted",), middleware=("audit",))

    widened = granted.including(skills=("theirs",), subagents=("mine",))

    assert set(widened.skills or ()) == {"vetted", "theirs"}
    assert widened.middleware == ("audit",)


def test_including_cannot_be_asked_to_widen_middleware():
    """The actual guarantee, and it is structural rather than a check: there is
    no parameter to pass. A middleware *name* selects code the deployment
    wrote, so a widening path would let anyone who can upload a definition
    activate anything registered -- the hole `including` exists to avoid, one
    level down.
    """
    import inspect

    accepted = set(inspect.signature(Capabilities.including).parameters)

    assert "middleware" not in accepted
    assert {"skills", "subagents"} <= accepted


# -- the format -----------------------------------------------------------


def test_the_field_parses_in_both_yaml_forms(tmp_path):
    inline = read_subagent(
        "name: r\ndescription: d\nmiddleware: [a, b]\n"
        "system_prompt: |\n  Body.\n", tmp_path / "r.md"
    )
    block = read_subagent(
        "name: r\ndescription: d\nmiddleware:\n  - a\n  - b\nsystem_prompt: |\n  Body.\n",
        tmp_path / "r.md",
    )

    assert inline.middleware == block.middleware == ("a", "b")


def test_omitting_it_means_none(tmp_path):
    spec = read_subagent("name: r\ndescription: d\nsystem_prompt: |\n  Body.\n", tmp_path / "r.md")

    assert spec.middleware is None


def test_a_request_with_no_opinion_is_still_unrestricted():
    assert Capabilities().is_unrestricted
    assert not Capabilities(middleware=()).is_unrestricted


# -- the whole stack, pinned once -----------------------------------------


EVERYTHING = (
    "name: reviewer\ndescription: d\n"
    "builtin_tools: [read_file]\ntools: [always_fails]\nskills: [tabular-qa]\n"
    "subagents: [helper]\nmiddleware: [audit]\n"
    "system_prompt: |\n  You review.\n"
)

HELPER = "name: helper\ndescription: d\nsystem_prompt: |\n  You help.\n"

A_TOOL = '''
from langchain_core.tools import tool


@tool
def always_fails(anything: str) -> str:
    """Present so the workspace has a tool at all."""
    raise FileNotFoundError(anything)


TOOLS = [always_fails]
'''


def test_what_a_delegate_carries_is_pinned_here_and_only_here(cfg, session_dir, monkeypatch):
    """The exact stack, in order, for a definition that triggers every branch.

    Its neighbours deliberately do not assert this. A test about the allowlist a
    `tools:` line produced should not fail when an unrelated guard is added, so
    they read the entries they are about -- and that left the composition itself
    pinned nowhere. This is the one place it is, so that adding to the stack is
    a decision somebody makes here rather than a thing that happens.

    Order is not cosmetic. The two guards wrap every call and go first, so
    nothing below them can raise past them. `ToolAllowlist` and `NarrowedSkills`
    are what the definition asked for. `SubAgentMiddleware` is what lets it
    delegate at all. A deployment's own goes last, deliberately: "so a
    deployment's middleware sees the tool and skill scoping kingfisher applied
    rather than running ahead of it".
    """
    from tests.conftest import tools_dir
    from tests.test_subagent_skills import offer_skills

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(A_TOOL, encoding="utf-8")
    offer_skills(cfg, "tabular-qa")
    define(cfg, EVERYTHING)
    define(cfg, HELPER, name="helper")

    captured = build(
        cfg,
        monkeypatch,
        registry={"audit": Audited},
        subagents=("reviewer", "helper"),
    )

    assert [type(m).__name__ for m in middleware_of(captured, "reviewer")] == [
        "HostPathGuard",
        "WorkspaceToolErrors",
        "ToolAllowlist",
        "NarrowedSkills",
        "SubAgentMiddleware",
        "Audited",
    ]


def test_a_bare_definition_carries_three_of_them(cfg, session_dir, monkeypatch):
    """The other end of the same pin, and it is not two.

    A definition that narrows nothing is still handed an allowlist, because the
    ceiling it is built against is the *request's* grant rather than its own
    silence -- and that is concrete the moment a workspace defines any tool. A
    delegate may never be offered more than whoever reached it, which is a
    sentence about the caller and stays true of a definition that asked for
    nothing.

    The two guards are there for the reason they are always there: the backend
    rejects host paths for a delegate exactly as it does for its parent, and a
    workspace tool it can reach fails the same way.
    """
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(A_TOOL, encoding="utf-8")
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n")

    captured = build(cfg, monkeypatch, subagents=("reviewer",))

    assert [type(m).__name__ for m in middleware_of(captured, "reviewer")] == [
        "HostPathGuard",
        "WorkspaceToolErrors",
        "ToolAllowlist",
    ]
