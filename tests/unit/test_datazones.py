"""A datazone: declared in the vocabulary, named by a caller, required by a subagent or tool."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
import yaml
from langchain_core.messages import AIMessage

from kingfisher import Request, ToolSpec, UnknownSessionError
from kingfisher.cli.__main__ import main
from kingfisher.core.access.checks import caller_holds
from kingfisher.core.access.policy import parse
from kingfisher.core.access.source_ids import (
    UNSCOPED,
    AccessError,
    with_datazone,
    zone_name,
)
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.recorder import Audiences
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.subagents.spec import SubagentError, declared
from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, ToolError, found_in
from kingfisher.core.definitions.tools.spec import tool_name
from kingfisher.core.inventory import inventory
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import Through, subagents_dir, tools_dir
from tests.unit.scripted import Scripted

VOCABULARY = "source_ids: [A, B]\ndatazones: [finance, hr]\n"

#: `payroll` is for the finance datazone; `line_count` is for any.
TOOLS = '''
from kingfisher import ToolSpec


def payroll(month: str) -> str:
    """Read one month of payroll."""
    return "0"


def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [ToolSpec(payroll, datazone="finance"), line_count]
'''

#: A subagent for the finance datazone, and nothing else about who reaches it.
LEDGER = """name: ledger
description: Reads the ledger.
datazone: finance
system_prompt: |
  You read the ledger.
"""

#: Open to A and B in any datazone, granting both tools and the subagent.
AGENT = """name: surveyor
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file, task]
tools: [payroll, line_count]
subagents: [ledger]
system_prompt: |
  You do the task.
"""


def vocabulary(text: str = VOCABULARY):
    return parse(yaml.safe_load(text), source="source_ids.yaml")


@pytest.fixture
def zoned(cfg):
    """A deployment declaring two datazones, a finance tool and a finance subagent."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "pay.py").write_text(TOOLS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "ledger.yaml").write_text(LEDGER, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(AGENT, encoding="utf-8")
    (cfg.definitions / "source_ids.yaml").write_text(VOCABULARY, encoding="utf-8")
    return replace(cfg, access=vocabulary())


def built(kf: Kingfisher, source_ids, datazone: str | None) -> tuple[set[str], set[str]]:
    """The workspace tools and subagents `create_deep_agent` is handed for this caller."""
    caller = with_datazone(source_ids, datazone)
    session = kf.workspace / "sessions" / f"{datazone}-{id(caller)}"
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session, DEFAULT_LAYOUT)
    assembled = build_agent(
        kf.cfg,
        agent=kf._agent_named("surveyor", source_ids=caller),
        held=caller_holds(kf.access, caller),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf.grants,
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    tools = {tool_name(t) for t in assembled.tools or ()}
    subagents = {s["name"] for s in assembled.subagents or ()} - {"general-purpose"}
    return tools, subagents


# -- the vocabulary -----------------------------------------------------------------


def test_the_vocabulary_declares_its_datazones():
    assert vocabulary().datazones == {"finance", "hr"}
    assert vocabulary("source_ids: [A]\n").datazones == frozenset(), "none is a legal answer"


@pytest.mark.parametrize(
    ("written", "said"),
    [("finance", "a list of names"), ("[finance, finance]", "twice"), ("['']", "empty")],
)
def test_a_datazones_line_that_cannot_be_read_is_refused(written, said):
    with pytest.raises(AccessError, match=said):
        vocabulary(f"source_ids: [A]\ndatazones: {written}\n")


def test_a_source_id_spelled_like_a_held_datazone_is_refused():
    """Declared, it could be handed out as a source id and stand in for a datazone."""
    with pytest.raises(AccessError, match="declare datazones under"):
        vocabulary("source_ids: [A, 'datazone=finance']\n")


# -- the caller ---------------------------------------------------------------------


def test_a_caller_in_a_datazone_holds_it_beside_their_source_ids(zoned):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    assert caller_holds(kf.access, with_datazone(("A",), "finance")) == {"A", zone_name("finance")}


@pytest.mark.parametrize(
    ("source_ids", "datazone", "said"),
    [
        (("A",), "payroll", "unknown datazone 'payroll'"),
        (UNSCOPED, "finance", "UNSCOPED runs with no caller"),
    ],
)
def test_a_datazone_that_cannot_be_honoured_is_refused(zoned, source_ids, datazone, said):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(AccessError, match=said):
        caller_holds(kf.access, with_datazone(source_ids, datazone))


def test_a_datazone_where_none_are_declared_is_refused(cfg):
    """Ignored, the caller would believe the zone narrowed what they were handed."""
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(AccessError, match="declares no datazones"):
        caller_holds(kf.access, with_datazone((), "finance"))


def test_every_turn_door_takes_the_datazone(zoned):
    """A door that dropped it would run the turn as a caller in no datazone."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(AccessError, match="unknown datazone"):
        kf.run("t", source_ids=("A",), datazone="payroll")
    with pytest.raises(AccessError, match="unknown datazone"):
        next(iter(kf.stream("t", source_ids=("A",), datazone="payroll")))
    with pytest.raises(AccessError, match="unknown datazone"):
        asyncio.run(kf.arun("t", source_ids=("A",), datazone="payroll"))


# -- where a datazone may be written --------------------------------------------------


def test_a_subagent_says_its_datazone_in_every_form():
    """A document, a portable declaration and a compiled one, read by one reader."""
    portable = {"name": "s", "description": "d", "system_prompt": "Go.", "datazone": "hr"}
    compiled = {"name": "c", "description": "d", "build": lambda *_: None, "datazone": "hr"}

    assert declared(portable, "acme.py").datazone == "hr"
    assert declared(compiled, "acme.py").datazone == "hr"


def test_a_datazone_is_one_name():
    with pytest.raises(SubagentError, match="one name"):
        declared(
            {"name": "s", "description": "d", "system_prompt": "Go.", "datazone": ["hr"]},
            "acme.py",
        )


def test_an_agent_has_no_datazone(zoned):
    """It runs in whichever datazone its caller names; a line here would be a second
    answer to a question the call already answered.
    """
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(AGENT + "datazone: finance\n", encoding="utf-8")

    with pytest.raises(AgentError, match="whichever datazone its caller names"):
        Kingfisher(zoned, sessions=LocalSessions())


def test_an_entry_has_no_datazone(zoned):
    """On an entry it could only repeat the tool's own, or contradict it."""
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace(
            "tools: [payroll, line_count]",
            "tools:\n  - name: payroll\n    datazone: hr\n  - line_count",
        ),
        encoding="utf-8",
    )

    with pytest.raises(AgentError, match="the one place it means one thing"):
        Kingfisher(zoned, sessions=LocalSessions())


def test_a_tool_says_its_datazone_and_the_loader_keeps_it(tmp_path):
    (tmp_path / "pay.py").write_text(TOOLS, encoding="utf-8")

    found = {one.name: one for one in LocalToolRepository(tmp_path).found}

    assert found["payroll"].datazone == "finance"
    assert found["line_count"].datazone is None


def test_a_tool_datazone_that_is_not_one_name_is_refused():
    def count(path: str) -> str:
        """Count the lines in a text file."""
        return "0"

    with pytest.raises(ToolError, match=r"^pay\.py: tool 'count': datazone is one name"):
        found_in(ToolSpec(count, datazone=""), where="pay.py", holder="TOOLS")


@pytest.mark.parametrize("where", ["subagent", "tool"])
def test_a_datazone_the_vocabulary_lacks_stops_the_deployment(zoned, where):
    if where == "subagent":
        (subagents_dir(zoned) / "ledger.yaml").write_text(
            LEDGER.replace("finance", "fiance"), encoding="utf-8"
        )
    else:
        (tools_dir(zoned) / "pay.py").write_text(
            TOOLS.replace('datazone="finance"', 'datazone="fiance"'), encoding="utf-8"
        )

    with pytest.raises(AccessError, match="undeclared datazone 'fiance'"):
        Kingfisher(zoned, sessions=LocalSessions())


# -- honoured wherever a reach is decided ----------------------------------------------


def test_a_finance_caller_gets_the_finance_tool_and_subagent(zoned):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), "finance")

    assert {"payroll", "line_count"} <= tools
    assert subagents == {"ledger"}


def test_a_caller_in_another_datazone_gets_neither(zoned):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), "hr")

    assert "line_count" in tools, "the control: a tool for any datazone stays"
    assert "payroll" not in tools
    assert subagents == set()


def test_a_caller_in_no_datazone_gets_only_what_is_for_any(zoned):
    """Closed rather than open: forgetting to pass a datazone must not hand a caller
    every zone's tools, which is the silent widening `source_ids=` refuses as well.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), None)

    assert "line_count" in tools
    assert "payroll" not in tools
    assert subagents == set()


def test_unscoped_reaches_every_datazone(zoned):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, UNSCOPED, None)

    assert "payroll" in tools
    assert subagents == {"ledger"}


def test_the_listing_agrees_with_the_build(zoned):
    finance = inventory(zoned, source_ids=["A"], datazone="finance")
    hr = inventory(zoned, source_ids=["A"], datazone="hr")

    assert "payroll" in finance.tools
    assert "payroll" not in hr.tools
    assert "ledger" in finance.subagents
    assert "ledger" not in hr.subagents


def test_the_command_line_names_a_datazone(zoned, at_the_command_line, capsys):
    assert main(["list", "--as", "A", "--datazone", "finance"]) == 0
    assert "payroll" in capsys.readouterr().out

    assert main(["list", "--as", "A", "--datazone", "hr"]) == 0
    assert "payroll" not in capsys.readouterr().out


def test_the_operator_listing_says_each_datazone(zoned, at_the_command_line, capsys):
    """`ledger` writes no `source_ids:`, so without its datazone the operator was shown
    `[*]` -- everyone -- for a subagent only the finance datazone reaches.
    """
    assert main(["list"]) == 0
    shown = capsys.readouterr().out
    assert "subagent ledger  [*]  datazone: finance" in shown
    assert "tool payroll  [*]  datazone: finance" in shown

    assert main(["list", "--json"]) == 0
    audiences = json.loads(capsys.readouterr().out)["audiences"]
    assert audiences["subagents"]["ledger"] == {"source_ids": ["*"], "datazone": "finance"}
    assert audiences["tools"]["payroll"] == {"source_ids": ["*"], "datazone": "finance"}


def test_a_saved_result_is_stamped_with_its_datazone():
    """A subagent's datazone and a tool's both reach the stamp, so the ledger never
    records a finance result as readable from another datazone.
    """

    class Subagent:
        source_ids = ("A",)
        datazone = "finance"

    stamps = Audiences.of(Subagent(), own={"payroll": (zone_name("hr"),)})

    assert stamps.offered("line_count") == (frozenset({"A", zone_name("finance")}),)
    assert stamps.offered("payroll") == (
        frozenset({"A", zone_name("finance"), zone_name("hr")}),
    )


# -- a session keeps the datazone a turn ran in -------------------------------------


@pytest.fixture
def turns(zoned, scripted):
    """`zoned`, with each turn's model answered by `Scripted`."""
    return replace(zoned, models=scripted.models)


def _turn(kf: Kingfisher, session_id: str, datazone: str | None):
    Scripted.script.append(AIMessage(content="done"))
    request = Request("t", agent="surveyor", session_id=session_id)
    return kf.run(request, source_ids=("A",), datazone=datazone)


def test_a_session_a_zoned_turn_ran_in_is_reached_only_from_that_datazone(turns, way):
    """A finance turn left its tools' results in a session that any caller of its agent
    could read or carry on, in no datazone or in another.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "finance")
    reads = Through(kf, way)

    assert reads.session("s", source_ids=("A",), datazone="finance") is not None
    assert reads.session("s", source_ids=("A",)) is None
    assert reads.session("s", source_ids=("A",), datazone="hr") is None
    assert reads.session("s", source_ids=UNSCOPED) is not None
    with pytest.raises(UnknownSessionError):
        _turn(kf, "s", "hr")


def test_a_listing_leaves_out_a_session_in_another_datazone(turns):
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "finance")

    assert [one.id for one in kf.sessions(source_ids=("A",), datazone="finance")] == ["s"]
    assert kf.sessions(source_ids=("A",)) == ()


def test_the_first_zoned_turn_narrows_a_session_that_had_none(turns):
    """Not the first turn's: an unzoned turn adds nothing a finance caller may not read,
    and the finance turn after it adds what only they may.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", None)
    assert kf.session("s", source_ids=("A",)) is not None

    _turn(kf, "s", "finance")

    assert kf.session("s", source_ids=("A",)) is None
    assert kf.session("s", source_ids=("A",), datazone="finance") is not None


def test_a_session_no_zoned_turn_ran_in_is_reached_from_every_datazone(turns):
    """The control: a caller in a datazone holds everything one in none does."""
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", None)

    for datazone in (None, "finance", "hr"):
        assert kf.session("s", source_ids=("A",), datazone=datazone) is not None


def test_the_command_reads_an_artifact_from_the_datazone_it_was_made_in(
    turns, at_the_command_line, capsysbinary
):
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "finance")
    (turns.sessions_root / "s" / "outputs" / "a.txt").write_text("result")

    asked = ["artifact", "--session", "s", "--as", "A", "outputs/a.txt"]
    assert main([*asked, "--datazone", "finance"]) == 0
    assert capsysbinary.readouterr().out == b"result"
    assert main(asked) == 2


def test_the_pending_calls_are_listed_from_the_datazone_the_session_is_in(
    turns, at_the_command_line, capsys
):
    """`decide` with no answer lists what a session waits on, and must not be the one
    door into a zoned session that drops the caller's datazone.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "finance")

    asked = ["decide", "--session", "s", "--as", "A"]
    assert main([*asked, "--datazone", "finance"]) == 2
    assert "is not waiting on a decision" in capsys.readouterr().err
    assert main(asked) == 2
    assert "no such session" in capsys.readouterr().err
