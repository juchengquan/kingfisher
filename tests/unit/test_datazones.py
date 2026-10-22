"""A datazone: required by a subagent or tool, and named by a caller."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Request, ToolSpec, UnknownSessionError
from kingfisher.cli.__main__ import main
from kingfisher.core.access.checks import caller_holds
from kingfisher.core.access.source_ids import (
    UNSCOPED,
    CallerError,
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
from tests.conftest import Through, agent_named, subagents_dir, tools_dir
from tests.unit.scripted import Scripted

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


@pytest.fixture
def zoned(cfg):
    """A deployment with a finance tool and a finance subagent, and nothing naming `hr`."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "pay.py").write_text(TOOLS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "ledger.yaml").write_text(LEDGER, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(AGENT, encoding="utf-8")
    return cfg


def built(kf: Kingfisher, source_ids, datazone: str | None) -> tuple[set[str], set[str]]:
    """The offered tools and subagents `create_deep_agent` is handed for this caller."""
    caller = with_datazone(source_ids, datazone)
    session = kf.workspace / "sessions" / f"{datazone}-{id(caller)}"
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session, DEFAULT_LAYOUT)
    assembled = build_agent(
        kf.cfg,
        agent=agent_named(kf, "surveyor", source_ids=caller),
        held=caller_holds(kf.vocabulary, caller),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf.grants,
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    tools = {tool_name(t) for t in assembled.tools or ()}
    subagents = {s["name"] for s in assembled.subagents or ()} - {"general-purpose"}
    return tools, subagents


# -- the vocabulary -----------------------------------------------------------------


def test_the_datazones_are_the_ones_the_definitions_require(zoned):
    """Collected with who requires each, a subagent's and a tool's alike."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    assert kf.vocabulary is not None
    assert kf.vocabulary.datazones == {"finance": ("subagent ledger", "tool payroll")}


def test_a_source_id_spelled_like_a_held_datazone_is_refused_where_written(zoned):
    """A lock naming `datazone=finance` would require a zone through the line that cannot
    say one -- on an agent, which may not require a datazone at all."""
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("source_ids: [A, B]", "source_ids: [A, 'datazone=finance']"),
        encoding="utf-8",
    )

    with pytest.raises(AgentError, match="how a datazone is held"):
        Kingfisher(zoned, sessions=LocalSessions())


# -- the caller ---------------------------------------------------------------------


def test_a_caller_in_a_datazone_holds_it_beside_their_source_ids(zoned):
    kf = Kingfisher(zoned, sessions=LocalSessions())

    held = caller_holds(kf.vocabulary, with_datazone(("A",), "finance"))

    assert held == {"A", zone_name("finance")}


def test_a_datazone_with_no_caller_is_refused(zoned):
    """`UNSCOPED` has no caller to be in a zone, so the pair says two things at once."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match="UNSCOPED runs with no caller"):
        caller_holds(kf.vocabulary, with_datazone(UNSCOPED, "finance"))


def test_a_datazone_nothing_requires_opens_nothing_in_a_build(zoned):
    """Refused, a call would pass or fail on which datazones were required that week."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), "payroll")

    assert "line_count" in tools, "the control: a tool for any datazone stays"
    assert "payroll" not in tools
    assert subagents == set()


def test_a_datazone_where_nothing_is_locked_is_refused(cfg):
    """Ignored, the caller would believe the zone narrowed what they were handed."""
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CallerError, match="no definition here carries a lock"):
        caller_holds(kf.vocabulary, with_datazone((), "finance"))


def test_every_turn_door_takes_the_datazone(zoned):
    """A door that dropped it would run the turn as a caller in no datazone -- and would
    not see that `UNSCOPED` cannot be in one."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match="UNSCOPED runs with no caller"):
        kf.run("t", source_ids=UNSCOPED, datazone="finance")
    with pytest.raises(CallerError, match="UNSCOPED runs with no caller"):
        next(iter(kf.stream("t", source_ids=UNSCOPED, datazone="finance")))
    with pytest.raises(CallerError, match="UNSCOPED runs with no caller"):
        asyncio.run(kf.arun("t", source_ids=UNSCOPED, datazone="finance"))


#: Every door a caller's datazone comes in by, as a library call.
ZONE_DOORS = {
    "run": lambda kf, cfg, zone: kf.run("t", source_ids=("A",), datazone=zone),
    "stream": lambda kf, cfg, zone: next(iter(kf.stream("t", source_ids=("A",), datazone=zone))),
    "arun": lambda kf, cfg, zone: asyncio.run(kf.arun("t", source_ids=("A",), datazone=zone)),
    "session": lambda kf, cfg, zone: kf.session("s", source_ids=("A",), datazone=zone),
    "asession": lambda kf, cfg, zone: asyncio.run(
        kf.asession("s", source_ids=("A",), datazone=zone)
    ),
    "sessions": lambda kf, cfg, zone: kf.sessions(source_ids=("A",), datazone=zone),
    "asessions": lambda kf, cfg, zone: asyncio.run(
        kf.asessions(source_ids=("A",), datazone=zone)
    ),
    "inventory": lambda kf, cfg, zone: inventory(cfg, source_ids=["A"], datazone=zone),
}


@pytest.mark.parametrize("zone", ["", "   "])
@pytest.mark.parametrize("door", ZONE_DOORS.values(), ids=ZONE_DOORS.keys())
def test_a_caller_s_blank_datazone_is_refused_at_every_door(zoned, door, zone):
    """A blank datazone was held as one, a name no definition could require, and the
    session a turn ran in was pinned to it for good; a definition's is refused blank.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match="datazone is one name"):
        door(kf, zoned, zone)


#: The doors where naming nobody means the operator, who sees everything.
NOBODY_DOORS = {
    "sessions": lambda kf, cfg: kf.sessions(datazone="finance"),
    "asessions": lambda kf, cfg: asyncio.run(kf.asessions(datazone="finance")),
    "inventory": lambda kf, cfg: inventory(cfg, datazone="finance"),
}


@pytest.mark.parametrize("door", NOBODY_DOORS.values(), ids=NOBODY_DOORS.keys())
def test_a_datazone_with_no_caller_is_refused_where_nobody_is_the_operator(zoned, door):
    """Let through, a datazone with no source ids was dropped, and the call read as the
    operator's: every session there is, the listing no caller is scoped to."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match="names no caller"):
        door(kf, zoned)


def test_the_command_refuses_a_blank_datazone(zoned, at_the_command_line, capsys):
    """The `--datazone` door, which a shell's quoting is the likeliest to leave blank."""
    assert main(["list", "--as", "A", "--datazone", "  "]) == 2
    assert "datazone is one name" in capsys.readouterr().err


def test_a_caller_s_padded_datazone_is_the_one_it_names(turns):
    """`" finance"` was held as given: it reached nothing zoned finance, and the session
    the turn ran in was pinned to `' finance'`, which no finance caller could reach again.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())

    assert with_datazone(("A",), " finance ") == ("A", "finance")
    _turn(kf, "s", " finance ")

    assert kf.session("s", source_ids=("A",), datazone="finance") is not None
    assert kf.session("s", source_ids=("A",)) is None


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


@pytest.mark.parametrize("written", ["*", " * "])
def test_a_star_datazone_is_refused_rather_than_read_as_a_name(written):
    """`datazone: "*"` loaded as a datazone named `*`, which no caller is in, so it shut
    its subagent to everyone and switched on access control -- after seed, reading the
    same `"*"` as "any", had copied the file as open.
    """
    with pytest.raises(SubagentError, match="datazone '\\*' is not 'any' -- leave datazone out"):
        declared(
            {"name": "s", "description": "d", "system_prompt": "Go.", "datazone": written},
            "acme.py",
        )


def test_a_tool_star_datazone_is_refused_too():
    """A tool's datazone is read by the subagent's reader, so `"*"` is the same mistake."""

    def count(path: str) -> str:
        """Count the lines in a text file."""
        return "0"

    with pytest.raises(ToolError, match="datazone '\\*' is not 'any'"):
        found_in(ToolSpec(count, datazone="*"), where="pay.py", holder="TOOLS")


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
def test_a_misspelt_datazone_starts_and_locks_its_holder_shut(zoned, where):
    """Nothing refuses a misspelling now, so it must fail shut: `fiance` is reached by no
    finance caller, and the listing shows it beside `finance`."""
    if where == "subagent":
        (subagents_dir(zoned) / "ledger.yaml").write_text(
            LEDGER.replace("finance", "fiance"), encoding="utf-8"
        )
    else:
        (tools_dir(zoned) / "pay.py").write_text(
            TOOLS.replace('datazone="finance"', 'datazone="fiance"'), encoding="utf-8"
        )
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), "finance")

    if where == "subagent":
        assert subagents == set()
        assert "payroll" in tools, "the control: the tool still spelt right is reached"
    else:
        assert "payroll" not in tools
        assert subagents == {"ledger"}, "the control: the subagent still spelt right"


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


def test_the_listing_names_each_datazone_with_what_requires_it(zoned, at_the_command_line, capsys):
    """The one place a misspelt zone shows, since nothing refuses it: beside the others."""
    assert main(["list"]) == 0
    assert "datazone finance  subagent ledger, tool payroll" in capsys.readouterr().out

    assert main(["list", "--json"]) == 0
    vocabulary = json.loads(capsys.readouterr().out)["vocabulary"]
    assert vocabulary["datazones"] == {"finance": ["subagent ledger", "tool payroll"]}


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


def test_a_zoned_request_refused_at_the_build_leaves_the_session_unzoned(turns):
    """The datazone was written before the build's refusals had run, so a finance request
    refused there narrowed a session no zoned turn had run in, and took it from every
    caller in no datazone.
    """
    from kingfisher.core.access.capabilities import CapabilityError
    from kingfisher.core.definitions.subagents.spec import RunOn

    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", None)

    with pytest.raises(CapabilityError, match="did not activate"):
        kf.run(
            Request("t", agent="surveyor", session_id="s", run_on={"ghost": RunOn("x")}),
            source_ids=("A",),
            datazone="finance",
        )

    assert kf.session("s", source_ids=("A",)) is not None


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
