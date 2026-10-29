"""What `ToolContext.reachable.check_agent` says of an agent file a caller drafted."""

from __future__ import annotations

import pytest

from kingfisher import Capabilities
from tests.conftest import an_agent, tools_dir
from tests.unit.test_reachable import AUDIT, SALES, _asked, _deployment

CHECKS = '''import json

import yaml
from langchain.tools import ToolRuntime

from kingfisher import ToolContext


class _Block(str):
    """Text the agent format asks to be written as a literal block."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    _Block, lambda dumper, text: dumper.represent_scalar("tag:yaml.org,2002:str", text, style="|")
)


def checked(text: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Say what is wrong with a drafted agent file."""
    return json.dumps(runtime.context.reachable.check_agent(text))


def copied_as(name: str, rename: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Check one agent's copy, renamed, as a draft."""
    copy = runtime.context.reachable.agent(name)
    copy["name"] = rename
    copy["system_prompt"] = _Block(copy["system_prompt"])
    text = yaml.dump(copy, Dumper=_Dumper, sort_keys=False)
    return json.dumps(runtime.context.reachable.check_agent(text))


TOOLS = [checked, copied_as]
'''

GOOD = """name: invoices
description: Answers questions about invoices.
model: cheap-model
builtin_tools: [read_file, task]
tools: [open_tool]
skills: [open-skill]
subagents: [helper]
middlewares:
  - {name: counted, settings: {messages: 3}}
interrupt_on: [open_tool]
system_prompt: |
  You answer questions about invoices.
"""


def _drafting(cfg) -> None:
    _deployment(cfg)
    (tools_dir(cfg) / "checks.py").write_text(CHECKS, encoding="utf-8")


def _checked(cfg, text: str, *, source_ids: list[str] = AUDIT, **request) -> list[str]:
    return _asked(cfg, "checked", {"text": text}, source_ids=source_ids, **request)


def _written(line: str, replacing: str) -> str:
    """`GOOD`, one of its lines replaced."""
    assert replacing in GOOD
    return GOOD.replace(replacing, line)


@pytest.mark.parametrize("source_ids", [AUDIT, SALES], ids=["shut-out", "holding"])
def test_a_copy_of_an_agent_passes_as_a_draft(scripted, source_ids):
    """Whatever `agent` hands a caller, renamed, must be a draft the check accepts, or the
    builder's surest start -- an agent the caller already uses -- would be refused."""
    _drafting(scripted)

    assert _checked(scripted, GOOD) == []
    copied = {"name": "pattern", "rename": "invoices"}
    assert _asked(scripted, "copied_as", copied, source_ids=source_ids) == []


@pytest.mark.parametrize(
    ("replacing", "written"),
    [
        ("system_prompt: |", "source_ids: [{}]\nsystem_prompt: |"),
        ("system_prompt: |", "datazone: {}\nsystem_prompt: |"),
        ("skills: [open-skill]", "skills:\n  - {{name: open-skill, source_ids: [{}]}}"),
        (
            "system_prompt: |\n  You answer questions about invoices.\n",
            "system_prompt:\n  text: |\n    Hello.\n\n    {{{{who}}}}\n  slots:\n    who:\n"
            "      - source_ids: [{}]\n        text: |\n          You.\n"
            "      - text: |\n          Them.\n",
        ),
    ],
    ids=["agent", "datazone", "skill", "slot"],
)
def test_a_draft_saying_who_may_use_it_is_refused_whatever_the_id(scripted, replacing, written):
    """A draft naming an id must be refused, and in the same words for a real id as for a
    made-up one: an answer telling them apart would let a caller find which ids exist."""
    _drafting(scripted)

    real = _checked(scripted, _written(written.format("ID-SALES"), replacing))
    made_up = _checked(scripted, _written(written.format("NO-SUCH-ID"), replacing))

    assert real == made_up
    assert len(real) == 1
    assert "decided by whoever installs it" in real[0]


def test_metadata_may_use_the_words(scripted):
    """`metadata` is the author's own: a key there that happens to be `source_ids` locks
    nothing, and refusing it would refuse what another service keeps there."""
    _drafting(scripted)

    assert _checked(scripted, GOOD + "metadata: {source_ids: [anything]}\n") == []


@pytest.mark.parametrize(
    ("field", "usable", "locked"),
    [
        ("tools", "open_tool", "locked_tool"),
        ("skills", "open-skill", "told-skill"),
        ("subagents", "helper", "zoned"),
        ("interrupt_on", "open_tool", "locked_tool"),
    ],
)
def test_a_name_out_of_reach_is_refused_as_one_nothing_defines(scripted, field, usable, locked):
    """Named in a draft, what a caller's ids do not open must be refused in the words a
    typo is, or the check would tell them it exists. A caller holding the lock is the
    control, and passes."""
    _drafting(scripted)

    def naming(name: str) -> str:
        return _written(f"{field}: [{name}]", f"{field}: [{usable}]")

    shut_out = _checked(scripted, naming(locked))
    typo = _checked(scripted, naming("nowhere"))

    assert shut_out == [one.replace("nowhere", locked) for one in typo]
    assert len(shut_out) == 1
    assert _checked(scripted, naming(locked), source_ids=SALES) == []


def test_a_name_this_turns_grant_withholds_is_refused(scripted):
    """A caller's reach is not their grant: a draft must not name what this turn withholds,
    or a deployment carrying a user's permissions in the grant would see them widened."""
    _drafting(scripted)
    grant = Capabilities(
        builtin_tools=("read_file", "task"),
        tools=("checked",),
        skills=None,
        subagents=None,
        middlewares=None,
        endpoints=("fake",),
    )

    draft = GOOD.replace("cheap-model", "elsewhere-model").replace(
        "[read_file, task]", "[read_file, task, execute]"
    )

    said = _checked(scripted, draft, capabilities=grant)

    assert said == [
        "builtin_tools: 'execute' is not one this user can use",
        "tools: 'open_tool' is not one this user can use",
        "skills: 'open-skill' is not one this user can use",
        "subagents: 'helper' is not one this user can use",
        "middlewares: 'counted' is not one this user can use",
        "model: 'elsewhere-model' is not one this user can use",
        "interrupt_on: 'open_tool' is not one this user can use",
    ]


def test_a_draft_named_like_an_agent_the_caller_sees_is_refused(scripted):
    """Installed beside an agent of its name, a draft stops the deployment starting, and
    beside one differing only in capitals, on a disk that ignores them, replaces it. One out
    of the caller's reach is not refused -- that would tell them it exists -- and a caller
    who can see it is refused, the control."""
    _drafting(scripted)
    an_agent(scripted, "Ledger")

    assert _checked(scripted, GOOD.replace("name: invoices", "name: pattern")) == [
        "an agent called 'pattern' already exists: choose another name"
    ]
    assert _checked(scripted, GOOD.replace("name: invoices", "name: ledger")) == [
        "an agent called 'Ledger' already exists: choose another name"
    ]
    assert _checked(scripted, GOOD.replace("name: invoices", "name: hidden")) == []
    assert _checked(scripted, GOOD.replace("name: invoices", "name: hidden"), source_ids=SALES)


@pytest.mark.parametrize(
    "name",
    ["Invoices", "user/invoices", "sales ops", "invoices-", "-invoices", "sales--ops", "a" * 33],
    ids=["capital", "slash", "space", "last-hyphen", "first-hyphen", "two-hyphens", "too-long"],
)
def test_a_drafted_name_is_one_every_disk_spells_one_way(scripted, name):
    """A draft is saved as `<name>.yaml`: `user/operator` landed in a folder of the user's
    outputs, and `Operator` copied beside `operator` on a disk that ignores capitals
    replaces it. The longest name allowed is the control, and passes."""
    _drafting(scripted)

    (refusal,) = _checked(scripted, GOOD.replace("name: invoices", f"name: {name}"))

    assert refusal.startswith(f"name {name!r}: ")
    assert _checked(scripted, GOOD.replace("name: invoices", f"name: {'a' * 32}")) == []


def test_a_tool_named_by_a_file_is_refused_unless_two_share_its_name(scripted):
    """`where::what` naming a file the tool is not in still loads, and the agent runs
    without the tool, so a draft must name a tool as the listing does."""
    _drafting(scripted)

    assert _checked(scripted, _written("tools: [nowhere.py::open_tool]", "tools: [open_tool]")) == [
        "tools: 'nowhere.py::open_tool' is not one this user can use"
    ]


def test_what_the_reader_refuses_is_refused(scripted):
    """A draft the deployment could not load must not pass, or the user is handed a file
    that stops the deployment: a misspelt field, a prompt written to reflow, a subagent
    with nothing to call it through, and a setting the add-on does not take."""
    _drafting(scripted)

    for draft, said in (
        (GOOD.replace("tools:", "tolls:"), "tolls"),
        (GOOD.replace("system_prompt: |\n  You", "system_prompt: >\n  You"), "literal block"),
        (GOOD.replace("[read_file, task]", "[read_file]"), "task"),
        (GOOD.replace("messages: 3", "pages: 3"), "pages"),
    ):
        (refusal,) = _checked(scripted, draft)
        assert said in refusal
