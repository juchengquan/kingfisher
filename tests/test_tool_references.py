"""Saying where a tool lives, in the definition that names it.

A `tools:` entry may be written `where::what`. The name is what reaches
everything else -- a grant, an allowlist, the dictionary the agent dispatches
through -- and the path beside it is a claim about location that gets checked.

It is never a choice between tools. Two of one name cannot both load, so there
is never a second candidate to pick out; `test_two_tools_of_one_name_never_both_load`
is the reason the whole feature is a label rather than a selector.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.domain.tool import Found, Offering, reference, split_reference
from kingfisher.infrastructure import seeding
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.definitions import read_subagent
from kingfisher.infrastructure.tool_store import LocalToolRepository
from tests.conftest import tools_dir

TOOL = """from langchain_core.tools import tool


@tool
def {name}(x: str) -> str:
    \"\"\"A tool called {name}.\"\"\"
    return x


TOOLS = [{name}]
"""

SUBAGENT = """name: {name}
description: A delegate called {name}.
tools: [{tools}]
system_prompt: |
  {body}
"""


SUBJECT = "subagent 'd'"


def _offering(sources):
    return Offering(workspace=tuple(sources), sources=sources)


def _spec(name="d", tools="csv_columns"):
    return read_subagent(
        SUBAGENT.format(name=name, tools=tools, body="x" * 220), Path(f"{name}.yaml")
    )


# -- how a reference is written and read ----------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("csv_profile::csv_columns", ("csv_profile", "csv_columns")),
        ("sql_query.py::sql_tables", ("sql_query.py", "sql_tables")),
        ("research/legal/x.py::find", ("research/legal/x.py", "find")),
        ("plain_name", (None, "plain_name")),
        # `--list` prints a package with its trailing slash for other reasons;
        # pasting that in should not be a near-miss someone has to spot.
        ("csv_profile/::csv_columns", ("csv_profile", "csv_columns")),
        ("  spaced  ::  around  ", ("spaced", "around")),
    ],
)
def test_a_written_reference_splits_into_a_claim_and_a_name(written, expected):
    assert split_reference(written) == expected


def test_a_package_reference_carries_no_trailing_slash():
    """`.py` is what says "file"; its absence says "folder". The slash only
    made `csv_profile/::csv_columns` noisier."""
    assert Found(object(), "csv_profile/").source == "csv_profile/"
    assert reference("csv_profile/", "csv_columns") == "csv_profile::csv_columns"
    assert reference("sql_query.py", "sql_tables") == "sql_query.py::sql_tables"


# -- what a definition does with one ---------------------------------------


def test_a_definition_keeps_what_it_wrote():
    """This used to strip each entry to its bare name, because a name was the
    only thing a grant or an allowlist could key on. Two folders may now each
    define a `fetch`, and the reference is the only thing that says which -- so
    the flattening moved to the two places that genuinely need a bare name.

    Written the short way, an entry stays short: that is still what a catalogue
    without collisions looks like.
    """
    spec = _spec(tools="csv_profile::csv_columns, http_fetch.py::http_fetch, plain")

    assert spec.tools == (
        "csv_profile::csv_columns",
        "http_fetch.py::http_fetch",
        "plain",
    )


def test_the_claims_travel_beside_the_names():
    spec = _spec(tools="csv_profile::csv_columns, plain")

    assert dict(spec.tool_sources) == {"csv_columns": "csv_profile"}
    assert "plain" not in spec.tool_sources, "the short form claims nothing"


@pytest.mark.parametrize("written", ['"*"', ""])
def test_a_selection_naming_nothing_carries_no_claims(written):
    """`["*"]` is everything and `[]` is nothing. Neither names a tool, so
    neither can say where one lives -- which is also why "every entry must
    carry a path" could never have been a clean rule."""
    spec = _spec(tools=written)

    assert dict(spec.tool_sources) == {}
    assert spec.tools in (ALL, ())


def test_the_derived_field_cannot_be_written_by_hand():
    """It is read out of `tools`. A definition writing it is refused like any
    other name this format does not define."""
    with pytest.raises(Exception, match="unknown field 'tool_sources'"):
        read_subagent(
            "name: a\ndescription: d\ntool_sources: {}\nsystem_prompt: |\n  b\n",
            Path("a.yaml"),
        )


# -- the check -------------------------------------------------------------


def test_a_path_that_still_describes_where_the_tool_is_passes():
    _offering({"csv_columns": "csv_profile/"}).refuse_moved(
        _spec(tools="csv_profile::csv_columns").tool_sources, subject=SUBJECT
    )


def test_a_path_that_no_longer_describes_it_is_refused():
    """Old and new, both in the form a definition writes, so the right-hand
    side is what you paste in to fix it."""
    with pytest.raises(CapabilityError) as raised:
        _offering({"csv_columns": "analysis/"}).refuse_moved(
            _spec(tools="csv_profile::csv_columns").tool_sources, subject=SUBJECT
        )

    message = str(raised.value)
    assert "csv_profile::csv_columns" in message
    assert "analysis::csv_columns" in message


def test_the_short_form_is_never_refused():
    """It asked for nothing, so it cannot be wrong about anything."""
    _offering({"csv_columns": "anywhere/"}).refuse_moved(
        _spec(tools="csv_columns").tool_sources, subject=SUBJECT
    )


def test_a_name_nothing_offers_is_left_to_the_other_refusal():
    """`refuse_unknown_tools` says that better, with the full listing. Saying it
    twice in two voices helps nobody."""
    _offering({"csv_columns": "csv_profile/"}).refuse_moved(
        _spec(tools="csv_profile::gone").tool_sources, subject=SUBJECT
    )


# -- where it fires --------------------------------------------------------


def test_a_moved_tool_fails_at_construction_not_on_the_first_turn(cfg):
    """`warm()` reads all three so a broken definition fails at startup. A path
    that no longer resolves is the same mistake one layer in -- and finding it
    when someone finally activates that one delegate means a deployment that
    started while broken."""
    seeding.seed(cfg)
    (tools_dir(cfg) / "csv_profile").rename(tools_dir(cfg) / "analysis")

    with pytest.raises(CapabilityError, match="have moved"):
        Definitions.from_config(cfg).warm()


def test_an_untouched_catalogue_warms_cleanly(cfg):
    """The negative control: the shipped presets use the long form, so this
    would fail if the check were wrong about the layout it ships with."""
    seeding.seed(cfg)

    Definitions.from_config(cfg).warm()


# -- why it is a label and not a selector ----------------------------------


def test_two_tools_of_one_name_both_load_and_are_told_apart(cfg):
    """The reason a path stopped being only a claim about location.

    The agent dispatches by name through a dictionary, so two tools of one name
    cannot both reach one agent. The loader used to refuse the pair outright;
    now it keeps both and the *reference* is what picks between them, which is
    what makes two folders from two vendors survive.
    """
    for folder in ("a", "b"):
        directory = tools_dir(cfg) / folder
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "t.py").write_text(TOOL.format(name="clash"), encoding="utf-8")

    repository = LocalToolRepository(tools_dir(cfg))

    assert sorted(one.reference for one in repository.found) == [
        "a/t.py::clash",
        "b/t.py::clash",
    ]
