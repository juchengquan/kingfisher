"""A datazone: required by a subagent or tool, and named by a caller."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Capabilities, CapabilityError, Request, ToolSpec, UnknownSessionError
from kingfisher.cli.__main__ import main
from kingfisher.core.access.checks import caller_holds
from kingfisher.core.access.source_ids import (
    DATAZONE_PREFIX,
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

if TYPE_CHECKING:
    from collections.abc import Callable

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


def built(
    kf: Kingfisher, source_ids, datazone: str | None, extras: tuple[str, ...] = ()
) -> tuple[set[str], set[str]]:
    """The offered tools and subagents `create_deep_agent` is handed for this caller."""
    caller = with_datazone(source_ids, datazone, extras)
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


@pytest.mark.parametrize("zone", ["*", " * "])
@pytest.mark.parametrize("door", ZONE_DOORS.values(), ids=ZONE_DOORS.keys())
def test_a_caller_s_star_datazone_is_refused_at_every_door(zoned, door, zone):
    """A caller's `"*"` was held as a zone of that name, which no definition may require
    since a definition's `"*"` is refused, so it reached nothing zoned and pinned the
    session it ran in to `*` for good; a datazone is passed by name or not at all.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match=r"'\*' is not 'any'"):
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


#: Every door a caller's extra datazones come in by, as a library call.
EXTRA_DOORS = {
    "run": lambda kf, cfg, **zones: kf.run("t", source_ids=("A",), **zones),
    "stream": lambda kf, cfg, **zones: next(iter(kf.stream("t", source_ids=("A",), **zones))),
    "arun": lambda kf, cfg, **zones: asyncio.run(kf.arun("t", source_ids=("A",), **zones)),
    "session": lambda kf, cfg, **zones: kf.session("s", source_ids=("A",), **zones),
    "asession": lambda kf, cfg, **zones: asyncio.run(
        kf.asession("s", source_ids=("A",), **zones)
    ),
    "sessions": lambda kf, cfg, **zones: kf.sessions(source_ids=("A",), **zones),
    "asessions": lambda kf, cfg, **zones: asyncio.run(
        kf.asessions(source_ids=("A",), **zones)
    ),
    "inventory": lambda kf, cfg, **zones: inventory(cfg, source_ids=["A"], **zones),
}


@pytest.mark.parametrize("door", EXTRA_DOORS.values(), ids=EXTRA_DOORS.keys())
def test_extra_datazones_with_no_datazone_are_refused_at_every_door(zoned, door):
    """A deployment sending its hardcoded extras with the zone forgotten would hand the
    caller everything locked to them, where a caller in no datazone reaches none of it;
    a door that dropped the extras would not see the zone was missing."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match="names none -- pass datazone= with them"):
        door(kf, zoned, extra_datazones=["finance"])


@pytest.mark.parametrize(
    ("extras", "said"),
    [
        ("finance", "not a string"),
        (["finance", " "], "datazone is one name"),
        (["*"], r"'\*' is not 'any'"),
    ],
    ids=["a bare string", "a blank one", "a star"],
)
def test_extra_datazones_are_named_as_a_datazone_is(zoned, extras, said):
    """A bare string was read a letter at a time, each one a zone; the others are the
    datazone's own refusals, for the same reasons."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match=said):
        caller_holds(kf.vocabulary, with_datazone(("A",), "hr", extras))


def test_the_command_refuses_a_blank_datazone(zoned, at_the_command_line, capsys):
    """The `--datazone` door, which a shell's quoting is the likeliest to leave blank."""
    assert main(["list", "--as", "A", "--datazone", "  "]) == 2
    assert "datazone is one name" in capsys.readouterr().err


def test_the_command_refuses_a_star_datazone(zoned, at_the_command_line, capsys):
    """The `--datazone` door: a quoted `'*'` reaches it as itself, and is refused there too."""
    assert main(["list", "--as", "A", "--datazone", "*"]) == 2
    assert "'*' is not 'any'" in capsys.readouterr().err


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


def test_a_caller_with_an_extra_datazone_gets_what_it_requires(zoned):
    """The control beside it is `hr` alone, which reaches neither."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    tools, subagents = built(kf, ("A",), "hr", ("finance",))

    assert {"payroll", "line_count"} <= tools
    assert subagents == {"ledger"}
    assert "payroll" not in built(kf, ("A",), "hr")[0]


def test_an_extra_datazone_is_never_held_as_a_source_id(zoned):
    """Read as one, `finance` would open whatever a lock names `finance` as a source id."""
    kf = Kingfisher(zoned, sessions=LocalSessions())

    held = caller_holds(kf.vocabulary, with_datazone(("A",), "hr", ["finance"]))

    assert held == {"A", zone_name("hr"), zone_name("finance")}


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


def test_the_command_line_names_extra_datazones(zoned, at_the_command_line, capsys):
    assert main(["list", "--as", "A", "--datazone", "hr", "--extra-datazones", "x,finance"]) == 0
    assert "payroll" in capsys.readouterr().out

    assert main(["list", "--as", "A", "--extra-datazones", "finance"]) == 2
    assert "pass datazone= with them" in capsys.readouterr().err


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


def _turn(kf: Kingfisher, session_id: str, datazone: str | None, extras: tuple[str, ...] = ()):
    Scripted.script.append(AIMessage(content="done"))
    request = Request("t", agent="surveyor", session_id=session_id)
    return kf.run(request, source_ids=("A",), datazone=datazone, extra_datazones=extras)


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


def test_a_session_is_reached_only_from_exactly_the_datazones_its_turn_ran_in(turns, way):
    """Not from fewer: `hr` alone would read what `payroll` returned to `hr` with
    `finance`. Nor from more: a caller in a zone below `hr` holds `hr` and `finance`
    besides its own, and would leave what its own zone returned for `hr` to read.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "hr", ("finance",))
    reads = Through(kf, way)

    def session(datazone, *extras):
        return reads.session("s", source_ids=("A",), datazone=datazone, extra_datazones=extras)

    assert session("hr", "finance") is not None
    assert session("finance", "hr") is not None, "a set: which one the caller is in is theirs"
    assert session("hr") is None
    assert session("finance") is None
    assert session("hr-emea", "hr", "finance") is None
    with pytest.raises(UnknownSessionError):
        _turn(kf, "s", "hr")
    with pytest.raises(UnknownSessionError):
        _turn(kf, "s", "hr-emea", ("hr", "finance"))


class _Racing(LocalSessions):
    """`LocalSessions` running one turn of another's inside the next turn's `mark_used`,
    the one step between that turn's first read of the pins and its claim."""

    race: Callable[[], object] | None = None

    def mark_used(self, cfg, session_id):
        race, self.race = self.race, None
        if race is not None:
            race()
        super().mark_used(cfg, session_id)


def _raced(turns, door: str, datazone: str, racer: str):
    """A finance turn starting session `s` as a `racer` turn pins it under the claim."""
    sessions = _Racing()
    kf = Kingfisher(turns, sessions=sessions)
    sessions.race = lambda: _turn(kf, "s", racer)
    Scripted.script.append(AIMessage(content="done"))
    request = Request("t", agent="surveyor", session_id="s")
    if door == "arun":
        return kf, asyncio.run(kf.arun(request, source_ids=("A",), datazone=datazone))
    return kf, kf.run(request, source_ids=("A",), datazone=datazone)


@pytest.mark.parametrize("door", ["run", "arun"])
def test_a_turn_runs_on_the_pins_read_under_its_claim(turns, door):
    """The pins were read once, before the claim, so a finance turn starting a session
    an hr turn pinned in between ran in it anyway, and left finance's results where hr
    reads them; refused, it would have deleted the session the hr turn ran in.

    Raced through `mark_used` rather than threads, so the hr turn lands in the gap
    every time and not only when the scheduler happens to put it there.
    """
    with pytest.raises(UnknownSessionError):
        _raced(turns, door, "finance", racer="hr")

    kf = Kingfisher(turns, sessions=LocalSessions())
    assert kf.session("s", source_ids=("A",), datazone="hr") is not None
    assert kf.session("s", source_ids=("A",), datazone="finance") is None
    _turn(kf, "s", "hr")  # and the claim was given back


@pytest.mark.parametrize("door", ["run", "arun"])
def test_a_turn_raced_in_its_own_datazone_runs(turns, door):
    """The control: a session pinned under the claim to what this caller reaches is
    one it carries on, not one it is refused."""
    kf, result = _raced(turns, door, "finance", racer="finance")

    assert result.session_id == "s"
    assert kf.session("s", source_ids=("A",), datazone="finance") is not None


class _Unlisted(LocalSessions):
    """`LocalSessions` whose next listing leaves out the sessions in `missing`, as a
    listing that has not caught up with what the backend holds does."""

    missing: frozenset[str] = frozenset()

    def sessions(self, cfg):
        missing, self.missing = self.missing, frozenset()
        return tuple(row for row in super().sessions(cfg) if row[0] not in missing)


def test_a_session_the_listing_missed_is_not_made_anew_for_another_datazone(turns, way):
    """`create=True` took an id the listing lacked for a session nobody had made, and
    handed back a finance session to an hr caller without reading its pins."""
    sessions = _Unlisted()
    kf = Kingfisher(turns, sessions=sessions)
    _turn(kf, "s", "finance")
    reads = Through(kf, way)

    sessions.missing = frozenset({"s"})
    assert reads.session("s", source_ids=("A",), datazone="hr", create=True) is None
    # The control: the caller it is pinned to gets it back.
    sessions.missing = frozenset({"s"})
    assert reads.session("s", source_ids=("A",), datazone="finance", create=True) is not None


def test_a_listing_leaves_out_a_session_in_another_datazone(turns):
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s", "finance")

    assert [one.id for one in kf.sessions(source_ids=("A",), datazone="finance")] == ["s"]
    assert kf.sessions(source_ids=("A",)) == ()


def _pinned_before_the_reader_tightened(cfg, session_id: str) -> None:
    """Pin `session_id` to a lock kingfisher took before #1048 and refuses now."""
    pin = cfg.sessions_root / session_id / ".harness" / "agent.yaml"
    kept = pin.read_text(encoding="utf-8")
    stale = kept.replace("source_ids: [A, B]", "source_ids: datazone=finance")
    assert stale != kept
    pin.write_text(stale, encoding="utf-8")


def test_a_pin_the_reader_now_refuses_reaches_no_scoped_caller(turns, way, caplog):
    """One such pin raised out of every scoped listing, so no caller got one, and quoted
    its lock to a caller asking for that session by id, which a leaked id must not do.
    """
    kf = Kingfisher(turns, sessions=LocalSessions())
    _turn(kf, "s1", None)
    _turn(kf, "s2", None)
    _pinned_before_the_reader_tightened(turns, "s1")

    listed = (
        asyncio.run(kf.asessions(source_ids=("A",)))
        if way == "async"
        else kf.sessions(source_ids=("A",))
    )
    assert [one.id for one in listed] == ["s2"]
    assert Through(kf, way).session("s1", source_ids=("A",)) is None
    with pytest.raises(UnknownSessionError):
        _turn(kf, "s1", None)
    assert "session s1: its pinned agent no longer reads" in caplog.text
    # The operator still sees it, so it can be cleaned up.
    assert {one.id for one in kf.sessions()} == {"s1", "s2"}
    assert Through(kf, way).session("s1", source_ids=UNSCOPED) is not None


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


# -- which names open something ------------------------------------------------------


def _opened(kf: Kingfisher, held: frozenset[str]):
    """What `opened_by` answers for a caller holding exactly these names."""
    zones = sorted(
        one.removeprefix(DATAZONE_PREFIX)
        for one in held
        if one.startswith(DATAZONE_PREFIX)
    )
    return kf.opened_by(
        "surveyor",
        source_ids=sorted(one for one in held if not one.startswith(DATAZONE_PREFIX)),
        datazone=zones[0] if zones else None,
        extra_datazones=tuple(zones[1:]),
    )


def _flat(opening) -> set[str]:
    """One side of the answer back in the one namespace `held_by` holds, for comparing."""
    return set(opening.source_ids) | {zone_name(one) for one in opening.datazones}


def test_a_name_opens_something_only_where_the_caller_satisfies_an_entry(zoned):
    """A caller told that a name of theirs opened something it did not: `A` is named in
    the agent's audience and also, with the zone, in the zoned tool's -- and only the
    first is an entry they satisfy without a datazone.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"A"}))

    assert opened.opens.source_ids == frozenset({"A"})
    assert opened.opens.datazones == frozenset()
    # Nothing: B opens the agent `A` already opens, so adding it reaches nothing more.
    assert opened.would.source_ids == frozenset()
    assert opened.would.datazones == frozenset({"finance"})


def test_both_names_open_the_agent_where_either_alone_would(zoned):
    """The reading this rule was chosen over: dropping either of `A` and `B` shuts
    nothing, so a load-bearing rule answers that neither opens anything.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"A", "B"}))

    assert opened.opens.source_ids == frozenset({"A", "B"})


def test_a_held_datazone_no_definition_requires_opens_nothing(zoned):
    """A caller in `hr` told that `hr` opened something. Nothing here is zoned `hr`, and
    the extra `finance` is what reaches the zoned tool.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"A", zone_name("hr"), zone_name("finance")}))

    assert opened.opens.datazones == frozenset({"finance"})
    assert opened.opens.source_ids == frozenset({"A"})
    assert opened.would == type(opened.would)()


def test_a_shut_agent_names_the_ids_that_would_open_it_and_no_zone(zoned):
    """A caller holding nothing told that `finance` would open the zoned tool. It would
    not: the tool's audience is joint with the agent's, so without `A` or `B` it is two
    names short, and this is what the composition is for.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset())

    assert opened.would.source_ids == frozenset({"A", "B"})
    assert opened.would.datazones == frozenset()
    assert opened.opens == type(opened.opens)()


def test_a_name_no_definition_uses_opens_nothing_and_is_not_reported(zoned):
    """Held all the same and opening nothing, as `held_by` promises -- and absent from
    the answer, which would otherwise hand back a name the deployment invented.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"C"}))

    assert _flat(opened.opens) == set()
    assert opened.would.source_ids == frozenset({"A", "B"})


@pytest.mark.parametrize("granted", ["payroll", "pay.py::payroll"])
def test_a_grant_naming_a_tool_by_its_file_still_reads_its_lock(zoned, granted):
    """A grant written `pay.py::payroll` was compared with the catalogue's short
    spelling, so the zoned tool it grants was skipped and `finance` reported as opening
    nothing. The bare name is the control beside it, and `ledger` is unzoned so that
    the tool is the only thing `finance` could open.
    """
    (subagents_dir(zoned) / "ledger.yaml").write_text(
        LEDGER.replace("datazone: finance\n", ""), encoding="utf-8"
    )
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("tools: [payroll, line_count]", 'tools: ["*"]'), encoding="utf-8"
    )
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = kf.opened_by(
        "surveyor",
        source_ids=["A"],
        datazone="finance",
        capabilities=Capabilities(tools=(granted,)),
    )

    assert opened.opens.datazones == frozenset({"finance"})


@pytest.mark.parametrize("declared", ["payroll", "pay.py::payroll"])
def test_a_subagent_naming_a_tool_by_its_file_still_reads_its_lock(zoned, declared):
    """The same miss on a subagent's own `tools:` line, which no grant reaches. The
    subagent is unzoned and the agent does not carry `payroll`, so its tool is the only
    thing `finance` opens.
    """
    (subagents_dir(zoned) / "ledger.yaml").write_text(
        LEDGER.replace("datazone: finance\n", f"tools: [{declared}]\n"), encoding="utf-8"
    )
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("tools: [payroll, line_count]", "tools: [line_count]"), encoding="utf-8"
    )
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"A", zone_name("finance")}))

    assert opened.opens.datazones == frozenset({"finance"})


def test_what_opens_agrees_with_the_withheld_report(zoned):
    """The second reader of these locks, and the one that could drift: a name reported
    as opening something no run would hand over, or a name whose addition reaches
    something and is left out of `would`.

    Driven against the report rather than against expectations written here, and the
    report is pinned to a grant of nothing on every audienced axis -- it skips an axis
    its grant does not narrow, so under the default grant it answers nothing and this
    test would pass whatever `opened_by` returned.
    """
    from itertools import combinations

    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.reporting import withheld_by_kind
    from kingfisher.core.session.steps import drive

    kf = Kingfisher(zoned, sessions=LocalSessions())
    # Out of the catalogue, never resolved by name: resolution drops an agent out of
    # reach, so a caller holding nothing is told no agent exists and the oracle goes
    # blind to exactly the callers `would` is for.
    spec = kf.catalogue.agents.specs["surveyor"]
    nothing_granted = kf.grants.intersect(Capabilities(tools=(), skills=(), subagents=()))
    reached: dict[frozenset[str], frozenset[tuple[str, str]]] = {}

    def reaches(held: frozenset[str]) -> frozenset[tuple[str, str]]:
        """Everything a run would hand this caller, asked of the withheld report."""
        if held in reached:
            return reached[held]
        session = kf.workspace / "sessions" / f"oracle-{abs(hash(held))}"
        session.mkdir(parents=True, exist_ok=True)
        ensure_session_layout(session, DEFAULT_LAYOUT)
        graph = kf._graph_for(
            Request(task="t", agent="surveyor"),
            capabilities=kf.grants,
            checkpointer=None,
            agent=spec,
            held=held,
            files=drive(kf._files_for(session.name)),
            session_id=session.name,
        ).graph
        told = withheld_by_kind(
            nothing_granted, kf.cfg, graph, kf.catalogue, agent=spec, held=held
        )
        reached[held] = frozenset(
            (kind, name) for kind, names in told if kind != "builtin tool" for name in names
        )
        return reached[held]

    names = ("A", "B", zone_name("finance"))
    subsets = [
        frozenset(picked)
        for size in range(len(names) + 1)
        for picked in combinations(names, size)
    ]
    assert reaches(frozenset(names)) > reaches(frozenset()), "the control: the oracle moves"

    for held in subsets:
        opened = _opened(kf, held)
        # A name whose addition, on its own, reaches something more.
        would = {one for one in set(names) - held if reaches(held | {one}) > reaches(held)}
        # A name some subset of theirs needs to reach what that subset without it does
        # not. Over subsets, not over the whole set: pinned to the whole set this
        # derivation describes the load-bearing reading, which was turned down.
        opens = {
            one
            for one in held
            if any(
                reaches(part) - reaches(part - {one})
                for part in subsets
                if one in part and part <= held
            )
        }

        assert _flat(opened.opens) == opens, f"opens for {sorted(held)}"
        assert _flat(opened.would) == would, f"would for {sorted(held)}"


def test_a_name_only_a_dominated_entry_carries_is_not_a_reason(zoned):
    """`[A, {A, B}]` is opened by A alone, so a caller holding both is told B opened it
    when nothing drops the entry no audience needs.
    """
    agents = zoned.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("source_ids: [A, B]", "source_ids: [A, {A, B}]"), encoding="utf-8"
    )
    kf = Kingfisher(zoned, sessions=LocalSessions())

    opened = _opened(kf, frozenset({"A", "B"}))

    assert opened.opens.source_ids == frozenset({"A"})


def test_unscoped_is_refused_where_an_empty_sequence_is_the_question(zoned):
    """`UNSCOPED` is read everywhere as nothing narrowing, so the walk would answer that
    nothing opens and nothing would -- the emptiest answer, about the least restricted
    caller there is.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    with pytest.raises(CallerError, match=r"UNSCOPED is no caller at all"):
        kf.opened_by("surveyor", source_ids=UNSCOPED)  # ty: ignore[invalid-argument-type]

    assert kf.opened_by("surveyor", source_ids=[]).would.source_ids == frozenset({"A", "B"})


def test_an_agent_out_of_reach_is_answered_and_an_unknown_one_refused(zoned):
    """The run path tells a caller neither exists, so that out of reach reads as not
    offered. Here the first is the answer worth having and only the second is a mistake.
    """
    kf = Kingfisher(zoned, sessions=LocalSessions())

    assert kf.opened_by("surveyor", source_ids=["C"]).would.source_ids == frozenset({"A", "B"})
    with pytest.raises(CapabilityError, match="no agent named 'nowhere'"):
        kf.opened_by("nowhere", source_ids=["A"])


def test_a_deployment_with_no_lock_is_refused_rather_than_answered_empty(cfg):
    """An empty answer where nothing is locked reads as "these ids buy you nothing",
    which is the reading `held_by` refuses a named caller for.
    """
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("source_ids: [A, B]\n", "").replace("tools: [payroll, line_count]\n", ""),
        encoding="utf-8",
    )
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CallerError, match="no definition here carries a lock"):
        kf.opened_by("surveyor", source_ids=[])


#: The same agent with a skills entry narrower than its own line, and no tools.
SKILLED = """name: surveyor
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file, task]
skills:
  - name: payroll-handling
    source_ids: [pii]
  - review
system_prompt: |
  You do the task.
"""


def _skilled(cfg, *, enabled: bool = True) -> Kingfisher:
    """The deployment above, with its skills on disk and the deployment's switch set."""
    agents = cfg.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(SKILLED, encoding="utf-8")
    for name in ("payroll-handling", "review"):
        (cfg.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A procedure.\n---\nBody.\n", encoding="utf-8"
        )
    return Kingfisher(replace(cfg, skills_enabled=enabled), sessions=LocalSessions())


def test_a_skill_entrys_own_id_is_named_as_one_that_would_open_it(zoned):
    """A caller who opens the agent and lacks `pii` is one name short of the entry, and
    nothing said so: asked for this caller, `declares` drops the entry before its lock
    can be read, so `would` could name no id that opens a skill.
    """
    opened = _opened(_skilled(zoned), frozenset({"A"}))

    assert opened.would.source_ids == frozenset({"pii"})
    # The bare entry inherits the agent's own line, which this caller satisfies.
    assert opened.opens.source_ids == frozenset({"A"})


def test_a_skill_nothing_can_open_names_no_id(zoned):
    """An audience on a line the model is never told: granting `pii` would change
    nothing, and sending an operator to grant it is worse than saying nothing.
    """
    off = _opened(_skilled(zoned, enabled=False), frozenset({"A"}))
    assert off.would.source_ids == frozenset(), "skills switched off for the deployment"

    # Withheld by the request, not by the file: an agent naming `skills:` without the
    # read tool is refused at load, so the file can never be in this state.
    shut = _skilled(zoned).opened_by(
        "surveyor", source_ids=["A"], capabilities=Capabilities(builtin_tools=("task",))
    )
    assert shut.would.source_ids == frozenset(), "no read tool granted, so no skill opens"


#: A portable subagent locked to `C`, carrying a tool of its own locked to `D`. Its
#: tools arrive as objects, so no grant reaches them and only their own lock applies.
VAULT = '''
from kingfisher import ToolSpec


def unseal(name: str) -> str:
    """Unseal one record."""
    return "0"


SUBAGENTS = [{
    "name": "vault",
    "description": "Unseals records.",
    "system_prompt": "You unseal.",
    "source_ids": ["C"],
    "tools": [ToolSpec(unseal, source_ids=["D"])],
}]
'''

#: The agent above it, granting nothing but the subagent.
KEEPER = """name: surveyor
description: An agent.
source_ids: [A, B]
builtin_tools: [task]
subagents: [vault]
system_prompt: |
  You do the task.
"""


@pytest.fixture
def carried(cfg):
    """An agent over a locked subagent that carries a tool with a lock of its own."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "vault.py").write_text(VAULT, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(KEEPER, encoding="utf-8")
    return cfg


def test_a_carried_tools_lock_is_read_under_the_subagent_that_brings_it(carried):
    """A caller who cannot reach the subagent told that `D` would open its tool. It
    would not: `C` and `D` are both missing, and the tool is reached only through the
    subagent -- the one place a path of three has to be composed to answer right.
    """
    kf = Kingfisher(carried, sessions=LocalSessions())

    shut = _opened(kf, frozenset({"A"}))
    assert shut.would.source_ids == frozenset({"C"}), "C alone opens the subagent"

    # Through it now, so the carried tool's own lock is the one name still missing.
    through = _opened(kf, frozenset({"A", "C"}))
    assert through.would.source_ids == frozenset({"D"})
    assert through.opens.source_ids == frozenset({"A", "C"})


#: A subagent locked to `C`, granting the finance tool and narrowing one skill further.
AUDITOR = """name: auditor
description: Audits.
source_ids: [C]
builtin_tools: [read_file]
tools: [payroll]
skills:
  - name: payroll-handling
    source_ids: [pii]
system_prompt: |
  You audit.
"""

#: The agent above it, holding nothing itself.
OVERSEER = """name: surveyor
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file, task]
subagents: [auditor]
system_prompt: |
  You do the task.
"""


def test_what_a_subagent_grants_and_narrows_is_read_under_its_own_lock(zoned):
    """A subagent's granted tool and its own skills entry, which nothing else here
    reaches: both are reached only through it, so a caller outside its lock must be told
    neither `finance` nor `pii` would open anything on its own.
    """
    (subagents_dir(zoned) / "auditor.yaml").write_text(AUDITOR, encoding="utf-8")
    (zoned.catalogue_roots["agents"] / "surveyor.yaml").write_text(OVERSEER, encoding="utf-8")
    (zoned.skills_dir / "payroll-handling").mkdir(parents=True, exist_ok=True)
    (zoned.skills_dir / "payroll-handling" / "SKILL.md").write_text(
        "---\nname: payroll-handling\ndescription: A procedure.\n---\nBody.\n", encoding="utf-8"
    )
    kf = Kingfisher(replace(zoned, skills_enabled=True), sessions=LocalSessions())

    shut = _opened(kf, frozenset({"A"}))
    assert shut.would.source_ids == frozenset({"C"}), "only C opens the subagent at all"
    assert shut.would.datazones == frozenset()

    # Through it, each of its parts is one name short on its own account.
    through = _opened(kf, frozenset({"A", "C"}))
    assert through.would.source_ids == frozenset({"pii"})
    assert through.would.datazones == frozenset({"finance"})
    assert through.opens.source_ids == frozenset({"A", "C"})
