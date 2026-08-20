"""Saying where a tool lives, in the definition that names it.

A `tools:` entry may be written `where::what`. The path beside the name is a
claim about location, and `refuse_moved` checks it.

It began as a label and became a selector, which is what this file is mostly
about. Two files may each define a `fetch` -- vendors do not coordinate -- and
both load now: the loader used to refuse the pair, and stopped a deployment
over a clash no single agent would ever see. So where a name is its own, a
definition may still write it plainly; where two files answer to one, the
reference is the only thing that picks between them, and the bare name is
refused rather than resolved by a guess. `test_two_tools_of_one_name_both_load_and_are_told_apart`
is that whole story in one test.

The bare name is what an allowlist and the agent's dispatch table key on
either way: a tool is called `fetch` however a definition spelled it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.tools import tool

from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.domain.tool import Found, Offering, reference, split_reference, tool_name
from kingfisher.infrastructure import seeding
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository
from kingfisher.infrastructure.harness.agent import _ToolSurface
from kingfisher.infrastructure.harness.delegation import as_subagent
from kingfisher.infrastructure.harness.narrowing import ToolAllowlist
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


def test_a_moved_tool_fails_at_construction_not_on_the_first_turn(cfg, shipped):
    """`warm()` reads all three so a broken definition fails at startup. A path
    that no longer resolves is the same mistake one layer in -- and finding it
    when someone finally activates that one delegate means a deployment that
    started while broken."""
    seeding.seed(cfg, shipped)
    (tools_dir(cfg) / "csv_profile").rename(tools_dir(cfg) / "analysis")

    with pytest.raises(CapabilityError, match="have moved"):
        Definitions.from_config(cfg).warm()


def test_an_untouched_catalogue_warms_cleanly(cfg, shipped):
    """The negative control: the shipped presets use the long form, so this
    would fail if the check were wrong about the layout it ships with."""
    seeding.seed(cfg, shipped)

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


# -- the two spellings of one unique tool ---------------------------------
#
# `csv_profile::csv_columns` and `csv_columns` name the same tool, and the long
# form is documented as buying a check rather than changing the meaning. An
# offering does not store both: it canonicalises to the bare name wherever a
# name is unique, keeping a reference only where two files clash. Every
# comparison downstream is set membership against that, so the long form of a
# unique tool matched nothing at all.


UNIQUE = {"csv_columns": "csv_profile/"}
#: Two files each defining `fetch`, which is the case references exist for.
CLASHING = ("a/t.py::fetch", "b/t.py::fetch")


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("csv_profile::csv_columns", "csv_columns"),
        ("csv_profile/::csv_columns", "csv_columns"),
        ("csv_columns", "csv_columns"),
    ],
)
def test_either_spelling_of_a_unique_tool_reaches_the_offering(written, expected):
    assert _offering(UNIQUE).spelt((written,)) == (expected,)


def test_a_name_the_offering_cannot_place_comes_back_as_written():
    """So the refusal quotes the file rather than a name it invented."""
    assert _offering(UNIQUE).spelt(("typo::nonesuch",)) == ("typo::nonesuch",)


@pytest.mark.parametrize("written", [ALL, None])
def test_a_grant_that_names_nothing_is_left_alone(written):
    assert _offering(UNIQUE).spelt(written) == written


def test_the_long_form_of_a_unique_tool_is_not_an_unknown_tool():
    """The refusal that stopped `surveyor` and `profiler`, both of which ship
    written this way. Measured: `capability error: this request names unknown
    tool(s): csv_profile::csv_profile`, from a file identical to the one in
    `src/kingfisher/assets/`."""
    _offering(UNIQUE).refuse_unknown(ALL, ("csv_profile::csv_columns",), subject=SUBJECT)


def test_the_long_form_of_a_unique_tool_reaches_the_allowlist():
    """The half that the first fix missed, and that only running it found.

    Resolving the *grant* was not enough: `permitted` builds the middleware's
    allowlist from the written form on its own, so the tool was registered on
    the graph and then refused at the moment the model called it. The transcript
    said `csv_profile` was granted and `Available tools:` did not list it.
    """
    offering = Offering(builtin=("read_file",), workspace=("csv_columns",), sources=UNIQUE)

    permitted = offering.permitted(ALL, ("csv_profile::csv_columns",))

    assert permitted is not None and "csv_columns" in permitted


def test_a_clashing_name_still_has_to_be_spelt_out():
    """The behaviour this must not change. Where two files define `fetch`, the
    bare name is what nobody can act on -- the agent dispatches by name and
    would keep one of the two in silence -- so it stays refused, and `spelt`
    has nothing to place it against."""
    offering = Offering(workspace=CLASHING, sources={})

    assert offering.spelt(("fetch",)) == ("fetch",)
    with pytest.raises(CapabilityError, match=r"write a/t\.py::fetch, b/t\.py::fetch"):
        offering.refuse_unknown(ALL, ("fetch",), subject=SUBJECT)

    offering.refuse_unknown(ALL, ("a/t.py::fetch",), subject=SUBJECT)


def test_the_claim_is_still_checked_by_the_rule_that_checks_claims():
    """`spelt` deliberately does not verify the path, so this has to keep
    firing: it is the whole reason the long form is worth writing."""
    offering = _offering(UNIQUE)

    offering.refuse_unknown(ALL, ("moved/elsewhere.py::csv_columns",), subject=SUBJECT)
    with pytest.raises(CapabilityError, match="moved"):
        offering.refuse_moved({"csv_columns": "moved/elsewhere.py"}, subject=SUBJECT)


# -- and where the two spellings meet the objects -------------------------
#
# The checks above are about a name. These are about a tool actually arriving,
# which is a different question and was a different bug: mutation testing the
# first fix showed `refuse_unknown` and `permitted` pinned and three other
# sites free to stop spelling with the whole suite still green.


@tool
def probe(x: str) -> str:
    """A tool called probe."""
    return x


PROBE = (Found(tool=probe, source="probe.py"),)


class _Request:
    """The one thing `ToolAllowlist` reads off a model request, and the one it
    calls to hand back a narrowed copy."""

    def __init__(self, tools):
        self.tools = tools

    def override(self, tools):
        return _Request(tools)


def test_an_agents_long_form_grant_reaches_the_tools_it_carries():
    """`granted_workspace` narrows the request's grant against what is offered,
    and `narrowed` is set membership with no opinion about what exists -- so
    the long form did not raise here, it silently resolved to nothing and the
    agent carried no workspace tools at all."""
    surface = _ToolSurface(
        offering=Offering.of(PROBE),
        asked=Capabilities(tools=("probe.py::probe",), builtin_tools=()),
        found=PROBE,
    )

    assert [tool_name(one) for one in surface.carried] == ["probe"]


def test_a_delegates_long_form_grant_reaches_the_tools_it_holds(cfg):
    """The same question one level down, where a definition rather than a
    request does the naming. `analysis/profiler.yaml` ships written this way."""
    spec = _spec(tools="probe.py::probe")

    built = as_subagent(
        spec, cfg, catalogue=PROBE, tools=("probe",), builtin_tools=("read_file",)
    )

    assert [tool_name(one) for one in built["tools"]] == ["probe"]


def test_a_delegates_long_form_grant_survives_the_ceiling(cfg):
    """The ceiling merges the two axes into the allowlist the delegate runs
    under, and took the written form as a name. A delegate could hold the tool
    and still be refused the moment it called it -- which is exactly what the
    parent did until `permitted` learned to spell."""
    spec = _spec(tools="probe.py::probe")

    built = as_subagent(
        spec, cfg, catalogue=PROBE, tools=("probe",), builtin_tools=("read_file",)
    )

    allowlists = [one for one in built.get("middleware", ()) if isinstance(one, ToolAllowlist)]
    assert allowlists, "a delegate naming one tool is restricted to it"

    # Through the middleware rather than into its set: what matters is that the
    # tool survives the filter the delegate actually runs under.
    kept = allowlists[0].wrap_model_call(_Request([probe]), lambda request: request)

    assert [tool_name(one) for one in kept.tools] == ["probe"]
