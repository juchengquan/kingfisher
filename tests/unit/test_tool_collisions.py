"""Two tools called `fetch`, from folders nobody coordinated."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt.tool_node import ToolNode

from kingfisher.domain.capabilities import Capabilities, CapabilityError, all_but
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.kinds.tools.catalogue import LocalToolRepository
from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
from tests.unit.test_delegation_ceiling import _subagent_graphs

TOOL = """
from langchain_core.tools import tool


@tool
def {name}(url: str) -> str:
    \"\"\"Fetch a URL.\"\"\"
    return "from {vendor}"


TOOLS = [{name}]
"""

SPEC = """
name: {name}
description: Uses {vendor}'s fetch.
system_prompt: |
  Do the thing.
builtin_tools: []
tools: [{grant}]
"""


def _two_vendors(cfg, *, name="fetch"):
    for vendor in ("vendor_a", "vendor_b"):
        directory = tools_dir(cfg) / vendor
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "fetch.py").write_text(
            TOOL.format(name=name, vendor=vendor), encoding="utf-8"
        )


def _delegate(cfg, vendor, *, grant):
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / f"agent_{vendor}.yaml").write_text(
        SPEC.format(name=f"agent_{vendor}", vendor=vendor, grant=grant), encoding="utf-8"
    )


def _fetch_from(graph):
    """What `fetch` returns inside this graph, or why there is none."""
    for node in getattr(graph, "nodes", {}).values():
        for obj in (node, getattr(node, "runnable", None), getattr(node, "bound", None)):
            if isinstance(obj, ToolNode) and "fetch" in obj.tools_by_name:
                return obj.tools_by_name["fetch"].invoke({"url": "x"})
    return None


# -- the catalogue keeps both ---------------------------------------------


def test_two_folders_may_each_define_one_name(cfg):
    """This stopped the deployment before, and was unfixable by anyone who owned neither
    file.
    """
    _two_vendors(cfg)

    assert sorted(one.reference for one in LocalToolRepository(tools_dir(cfg)).found) == [
        "vendor_a/fetch.py::fetch",
        "vendor_b/fetch.py::fetch",
    ]


# -- the whole point ------------------------------------------------------


def test_each_delegate_holds_the_tool_it_named(cfg, session_dir):
    """Two helpers, two vendors, one name."""
    _two_vendors(cfg)
    for vendor in ("vendor_a", "vendor_b"):
        _delegate(cfg, vendor, grant=f"{vendor}/fetch.py::fetch")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(
            builtin_tools=("read_file",),
            tools=("vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"),
            subagents=("agent_vendor_a", "agent_vendor_b"),
        ),
    )
    delegates = _subagent_graphs(graph)

    assert _fetch_from(delegates["agent_vendor_a"]) == "from vendor_a"
    assert _fetch_from(delegates["agent_vendor_b"]) == "from vendor_b"


def test_the_agent_holding_the_grant_holds_neither(cfg, session_dir):
    """It dispatches by name and cannot say which, so keeping one would be keeping the
    wrong one half the time.
    """
    _two_vendors(cfg)

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(
            builtin_tools=("read_file",),
            tools=("vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"),
        ),
    )

    assert _fetch_from(graph) is None


def test_what_the_agent_cannot_hold_is_said_out_loud(cfg):
    """Quietly holding less than was asked for is the failure this codebase refuses
    everywhere.
    """
    from kingfisher.application.reporting import delegate_only

    _two_vendors(cfg)

    assert delegate_only(
        Capabilities(tools=("vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch")),
        cfg,
        catalogue=None,
    ) == ("fetch",)


def test_a_grant_of_everything_still_works(cfg, session_dir):
    """`tools` defaults to `*`, so a catalogue with a collision would otherwise be
    unusable by default.
    """
    _two_vendors(cfg)
    _delegate(cfg, "vendor_a", grant="vendor_a/fetch.py::fetch")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(subagents=("agent_vendor_a",)),
    )

    assert _fetch_from(graph) is None, "the parent cannot tell them apart"
    assert _fetch_from(_subagent_graphs(graph)["agent_vendor_a"]) == "from vendor_a"


def test_a_delegate_can_actually_call_the_one_it_named(cfg, session_dir):
    """The registry says a delegate *holds* it; only a call says it can use it.

    Written because a mutation proved the gap: leaving the allowlist keyed on the
    reference rather than the bare name filters every workspace tool out of the model
    request, and every other test here still passed. The delegate would have held its
    `fetch` and never been offered it.
    """
    _two_vendors(cfg)
    _delegate(cfg, "vendor_b", grant="vendor_b/fetch.py::fetch")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "fetch", "args": {"url": "x"}, "id": "c1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
        capabilities=Capabilities(
            builtin_tools=("read_file",),
            tools=("vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"),
            subagents=("agent_vendor_b",),
        ),
    )

    out = _subagent_graphs(graph)["agent_vendor_b"].invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 6},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "from vendor_b" in transcript, "the delegate could not call the tool it named"
    assert "from vendor_a" not in transcript, "it reached the other vendor's tool"


# -- naming one -----------------------------------------------------------


def test_a_bare_name_two_files_offer_is_refused(cfg, session_dir):
    """The safety property."""
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="more than one source offers"):
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(builtin_tools=(), tools=("fetch",)),
        )


def test_the_refusal_names_both_files(cfg, session_dir):
    _two_vendors(cfg)

    with pytest.raises(CapabilityError) as raised:
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(builtin_tools=(), tools=("fetch",)),
        )

    assert "vendor_a/fetch.py::fetch" in str(raised.value)
    assert "vendor_b/fetch.py::fetch" in str(raised.value)


def test_a_definition_naming_it_bare_is_refused_too(cfg, session_dir):
    """A definition is checked the same way a request is, and at construction -- a
    delegate that would have dispatched to the wrong vendor should never reach a
    turn.
    """
    _two_vendors(cfg)
    _delegate(cfg, "vendor_a", grant="fetch")

    with pytest.raises(CapabilityError, match="more than one source offers"):
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(
                builtin_tools=("read_file",),
                tools=("vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"),
                subagents=("agent_vendor_a",),
            ),
        )


# -- what must not have changed -------------------------------------------


def test_a_unique_name_is_still_granted_flat(cfg, session_dir):
    """Every catalogue without a collision behaves exactly as it did."""
    directory = tools_dir(cfg) / "solo"
    directory.mkdir(parents=True)
    (directory / "only.py").write_text(
        TOOL.format(name="fetch", vendor="solo"), encoding="utf-8"
    )

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(builtin_tools=(), tools=("fetch",)),
    )

    assert _fetch_from(graph) == "from solo"


def test_a_delegate_still_cannot_reach_past_the_request(cfg, session_dir):
    """The ceiling this change had to leave alone.

    Driven, not inspected: the delegate inherits the parent's registry and is stopped by
    its allowlist, so what its `ToolNode` registers is identical either way and only
    calling it proves anything.
    """
    _two_vendors(cfg)
    _delegate(cfg, "vendor_b", grant="vendor_b/fetch.py::fetch")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "fetch", "args": {"url": "x"}, "id": "c1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
        capabilities=Capabilities(
            builtin_tools=("read_file",),
            tools=("vendor_a/fetch.py::fetch",),
            subagents=("agent_vendor_b",),
        ),
    )

    out = _subagent_graphs(graph)["agent_vendor_b"].invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 6},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "from vendor_a" not in transcript, "the delegate ran a tool its caller never had"
    assert "from vendor_b" not in transcript, "the delegate reached past the request"


def test_one_file_defining_a_name_twice_is_still_refused(cfg):
    """Where the refusal still belongs: no second file, so no reference could pick
    between them and nothing downstream could offer a way to say which.
    """
    from kingfisher.kinds.tools.catalogue import ToolError

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "twice.py").write_text(
        TOOL.format(name="fetch", vendor="x").replace("TOOLS = [fetch]", "TOOLS = [fetch, fetch]"),
        encoding="utf-8",
    )

    with pytest.raises(ToolError, match="defined twice in this file"):
        _ = LocalToolRepository(tools_dir(cfg)).found


def test_a_workspace_tool_shadowing_a_builtin_is_still_refused(cfg, session_dir):
    """The other collapse, which is not fixed by references: a workspace `read_file`
    would take a built-in's name, and the built-in has no file to be told apart by.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shadow.py").write_text(
        TOOL.format(name="read_file", vendor="x"), encoding="utf-8"
    )

    with pytest.raises(CapabilityError, match="would replace a built-in"):
        build_agent(
            replace(cfg, skills_enabled=False),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(builtin_tools=(), tools=()),
        )


# -- subtraction, which is a grant written the other way round -------------


def _subtractable(cfg):
    """What `--without-tools` measures a name against, as the driver builds it."""
    from tests.integration import driver as main

    return main._offered(cfg)["tools"]


def test_subtracting_an_ambiguous_bare_name_is_refused(cfg):
    """The grant side refused this from the start; the subtraction side matched the bare
    name against a workspace holding two and removed *both* without a word.
    """
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="more than one source offers it"):
        all_but(("fetch",), offered=_subtractable(cfg))


def test_subtracting_a_reference_leaves_the_other_one(cfg):
    """And the only spelling that says *which* came back as an unknown name, so there
    was no way to subtract one of the pair at all.
    """
    _two_vendors(cfg)

    kept = all_but(("vendor_a/fetch.py::fetch",), offered=_subtractable(cfg))

    assert "vendor_b/fetch.py::fetch" in kept
    assert "vendor_a/fetch.py::fetch" not in kept


def test_a_genuine_typo_still_reads_as_unknown(cfg):
    """The distinction only exists if the other branch survives."""
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="unknown name"):
        all_but(("nosuchthing",), offered=_subtractable(cfg))


def test_the_subtraction_axis_offers_what_a_grant_could_name(cfg):
    """The two were built from different lists, which is how they disagreed."""
    _two_vendors(cfg)

    assert "vendor_a/fetch.py::fetch" in _subtractable(cfg)
    assert "fetch" not in _subtractable(cfg)
