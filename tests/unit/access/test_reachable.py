"""What a tool is handed of the definitions its caller reaches, on `ToolContext.reachable`."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Capabilities, Kingfisher
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, middlewares_dir, start, subagents_dir, tools_dir
from tests.unit.scripted import Scripted

ASKS = '''import json

from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def shown(runtime: ToolRuntime[ToolContext]) -> str:
    """Say what this caller can use."""
    listing = runtime.context.reachable.listing()
    return json.dumps(
        {
            "agents": sorted(listing.agents),
            "builtin_tools": sorted(listing.builtin_tools),
            "tools": sorted(listing.tools),
            "skills": sorted(listing.skills),
            "subagents": sorted(listing.subagents),
            "middlewares": sorted(listing.middlewares),
            "models": sorted(listing.models),
            "default_model": listing.default_model,
            "about": [listing.tools.get("open_tool"), listing.middlewares.get("counted")],
        }
    )


def copied(name: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Show one agent as this caller gets it."""
    return json.dumps(runtime.context.reachable.agent(name))


TOOLS = [shown, copied]
'''

OPEN = '''
def open_tool(text: str) -> str:
    """Counts the open things.

    A second line nobody is shown.
    """
    return text


TOOLS = [open_tool]
'''

LOCKED = '''from kingfisher import ToolSpec


def locked_tool(text: str) -> str:
    """Reads the sales ledger."""
    return text


TOOLS = [ToolSpec(locked_tool, source_ids=["ID-SALES"])]
'''

COUNTED = '''from langchain.agents.middleware import AgentMiddleware


class Counted(AgentMiddleware):
    """Counts the messages a turn has seen."""

    name = "counted"
    defaults = {"messages": 10}
    yaml_settable = frozenset({"messages"})

    def __init__(self, messages: int) -> None:
        self.messages = messages
        super().__init__()


MIDDLEWARES = [Counted]
'''

PATTERN = {
    "source_ids": "[ID-AUDIT, ID-SALES]",
    "model": "elsewhere-model",
    "builtin_tools": "[read_file, task]",
    "tools": "[open_tool, locked_tool]",
    "skills": "[open-skill, told-skill]",
    "subagents": "[helper, spare, zoned]",
    "middlewares": "\n  - {name: counted, settings: {messages: 3}}",
    "interrupt_on": "[locked_tool, open_tool]",
    "memory": "false",
    "metadata": "{team: data}",
    "system_prompt": (
        "\n  text: |\n    Base.\n\n    {{access}}\n  slots:\n    access:\n"
        "      - source_ids: [ID-SALES]\n        text: |\n          SALES-ONLY sentence.\n"
        "      - text: |\n          EVERYONE-ELSE sentence."
    ),
}

AUDIT = ["ID-AUDIT"]
SALES = ["ID-SALES"]


def _deployment(cfg) -> None:
    """Something each caller reaches and something only one of them does, of every kind."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name, text in (("asks", ASKS), ("open", OPEN), ("locked", LOCKED)):
        (tools_dir(cfg) / f"{name}.py").write_text(text, encoding="utf-8")
    for name in ("open-skill", "told-skill", "shared-skill"):
        (cfg.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name}.\n---\nDo it.\n", encoding="utf-8"
        )
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name, lock in (("helper", ""), ("spare", ""), ("zoned", "source_ids: [ID-SALES]\n")):
        (subagents_dir(cfg) / f"{name}.yaml").write_text(
            f"name: {name}\ndescription: The {name}.\n{lock}builtin_tools: [read_file]\n"
            "system_prompt: |\n  You help.\n",
            encoding="utf-8",
        )
    middlewares_dir(cfg).mkdir(parents=True, exist_ok=True)
    (middlewares_dir(cfg) / "counted.py").write_text(COUNTED, encoding="utf-8")
    # `shared-skill` only this agent lists, and `told-skill` is locked to sales.
    an_agent(cfg, source_ids="[ID-AUDIT, ID-SALES]", skills="[shared-skill]", **EVERY_TOOL)
    (cfg.skills_dir.parent / "skill_locks.yaml").write_text(
        "- name: told-skill\n  source_ids: [ID-SALES]\n", encoding="utf-8"
    )
    an_agent(cfg, "hidden", source_ids="[ID-SALES]")
    an_agent(cfg, "pattern", **PATTERN)


def _asked(cfg, tool: str, args: dict[str, Any], *, source_ids: list[str], **request: Any) -> Any:
    """What `tool` answered on one turn of the agent every caller here reaches."""
    Scripted.script.extend(
        [
            AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "c1"}]),
            AIMessage(content="done"),
        ]
    )
    session = start(cfg, f"s{len(Scripted.heard)}")
    Kingfisher(cfg, sessions=LocalSessions()).run(
        Request("go", agent="only", session_id=session, **request), source_ids=source_ids
    )
    # What the model was handed back, whole: an event carries a preview of it.
    (answer,) = [one for one in Scripted.heard[-1] if isinstance(one, ToolMessage)]
    # The ledger's id is the last line of every result.
    return json.loads(answer.text.rsplit("\n", 1)[0])


@pytest.mark.parametrize(
    ("source_ids", "only_theirs"),
    [(AUDIT, False), (SALES, True)],
    ids=["shut-out", "control"],
)
def test_a_tool_is_shown_only_what_its_caller_reaches(scripted, source_ids, only_theirs):
    """A tool listing what a caller can use must leave out every kind of definition their
    ids do not open, or a drafted agent could name what they were never given -- and the
    control, a caller holding the lock, is shown each one, so the lock is what hides it."""
    _deployment(scripted)

    shown = _asked(scripted, "shown", {}, source_ids=source_ids)

    theirs = {
        "agents": {"hidden"},
        "tools": {"locked_tool"},
        "skills": {"told-skill"},
        "subagents": {"zoned"},
    }
    for kind, locked in theirs.items():
        assert (locked <= set(shown[kind])) is only_theirs, kind
    assert {"only", "pattern"} <= set(shown["agents"])
    assert {"open_tool", "shown", "copied"} <= set(shown["tools"])
    assert {"read_file", "task", "execute"} <= set(shown["builtin_tools"])
    assert {"open-skill", "shared-skill"} <= set(shown["skills"])
    assert {"helper", "spare"} <= set(shown["subagents"])
    assert shown["middlewares"] == ["counted"]
    assert shown["models"] == ["cheap-model", "elsewhere-model", "fake-model"]
    assert shown["default_model"] == "fake-model"
    assert shown["about"] == ["Counts the open things.", "Counts the messages a turn has seen."]


@pytest.mark.parametrize("narrowed_by", ["request", "deployment"])
def test_a_narrower_grant_narrows_what_a_tool_is_shown(scripted, narrowed_by):
    """Shown the caller's reach alone, a tool would list what this turn's grant withholds:
    a deployment carrying a user's permissions in the grant would see them widened. From
    the request and from the deployment's own grant, each axis."""
    _deployment(scripted)
    grant = Capabilities(
        builtin_tools=("read_file", "task"),
        tools=("shown", "copied"),
        skills=None,
        subagents=("helper",),
        middlewares=None,
        endpoints=("fake",),
    )
    cfg = replace(scripted, grants=grant) if narrowed_by == "deployment" else scripted
    request = {"capabilities": grant} if narrowed_by == "request" else {}

    shown = _asked(cfg, "shown", {}, source_ids=AUDIT, **request)

    assert shown["builtin_tools"] == ["read_file", "task"]
    assert shown["tools"] == ["copied", "shown"]
    assert shown["skills"] == []
    assert shown["subagents"] == ["helper"]
    assert shown["middlewares"] == []
    assert shown["models"] == ["cheap-model", "fake-model"]
    # A copy keeps to the same grant: its tool, skills, add-on, model and gate are withheld.
    copy = _asked(cfg, "copied", {"name": "pattern"}, source_ids=AUDIT, **request)
    copy.pop("system_prompt")
    assert copy == {
        "name": "pattern",
        "description": "An agent.",
        "builtin_tools": ["read_file", "task"],
        "subagents": ["helper"],
        "memory": False,
        "metadata": {"team": "data"},
    }


def test_an_agent_is_copied_as_its_caller_gets_it(scripted):
    """A copy must hold only what this caller reaches and say only what they are told:
    the slot meant for other callers, a tool, skill or subagent their ids do not open, and
    a gate on that tool would each name something kept from them. A star stays a star."""
    _deployment(scripted)

    copy = _asked(scripted, "copied", {"name": "pattern"}, source_ids=AUDIT)

    prompt = copy.pop("system_prompt")
    assert "EVERYONE-ELSE sentence." in prompt
    assert "SALES-ONLY" not in prompt
    assert "{{" not in prompt
    assert copy == {
        "name": "pattern",
        "description": "An agent.",
        "model": "elsewhere-model",
        "builtin_tools": ["read_file", "task"],
        "tools": ["open_tool"],
        "skills": ["open-skill"],
        "subagents": ["helper", "spare"],
        "middlewares": [{"name": "counted", "settings": {"messages": 3}}],
        "interrupt_on": ["open_tool"],
        "memory": False,
        "metadata": {"team": "data"},
    }
    assert _asked(scripted, "copied", {"name": "only"}, source_ids=AUDIT)["tools"] == ["*"]


@pytest.mark.parametrize("source_ids", [AUDIT, SALES], ids=["shut-out", "holding"])
def test_nothing_a_tool_is_shown_names_an_id(scripted, source_ids):
    """Ids, locks and slots are the deployment's policy: shown to a caller, they say which
    names open what, so they could be guessed at. Asked of a caller holding the lock too,
    whose copy is filled from it."""
    _deployment(scripted)

    said = json.dumps(
        [
            _asked(scripted, "shown", {}, source_ids=source_ids),
            _asked(scripted, "copied", {"name": "pattern"}, source_ids=source_ids),
        ]
    )

    assert "ID-" not in said
    assert "source_ids" not in said
    assert "slots" not in said


def test_an_agent_out_of_reach_reads_as_one_never_written(scripted):
    """Copied for a caller its lock shuts out, `hidden` must answer as a name nothing
    defines does, or asking would tell a caller which agents exist. The same name for a
    caller holding the lock is the control."""
    _deployment(scripted)

    assert _asked(scripted, "copied", {"name": "hidden"}, source_ids=AUDIT) is None
    assert _asked(scripted, "copied", {"name": "nobody"}, source_ids=AUDIT) is None
    assert _asked(scripted, "copied", {"name": "hidden"}, source_ids=SALES)["name"] == "hidden"
