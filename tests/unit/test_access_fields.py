"""Reading a selection that may say who each of its entries is for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kingfisher.core.access.capabilities import ALL
from kingfisher.core.definitions.agents.reading import read as read_agent
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.definitions.fields import Reader
from kingfisher.core.definitions.subagents.spec import SubagentError, declared
from tests.conftest import a_subagent

read = Reader(source="x.yaml", error=AgentError)


def audienced(value: object, **asked: Any) -> tuple[object, object]:
    """What `skills:` selects and who reaches each entry: the one field whose entries say."""
    selected, audiences, _ = read.sourced(value, **asked)
    return selected, audiences


def test_a_list_is_a_selection_with_no_audiences():
    assert audienced(["a", "b"], absent=ALL, key="skills") == (("a", "b"), {})


def test_omitted_is_the_absent_value_with_no_audiences():
    assert audienced(None, absent=ALL, key="skills") == (ALL, {})


def test_omitted_respects_the_fields_own_absent_value():
    """`tools` inherits everything when omitted; `skills` grants none."""
    assert audienced(None, absent=None, key="skills") == (None, {})


def test_star_still_means_everything():
    assert audienced(["*"], absent=ALL, key="skills") == (ALL, {})


def test_an_empty_list_still_means_none():
    assert audienced([], absent=ALL, key="skills") == ((), {})


def test_entries_select_their_names_and_carry_their_audiences():
    selected, audiences = audienced(
        [
            {"name": "audit", "source_ids": ["A"]},
            {"name": "review", "source_ids": ["*"]},
        ],
        absent=ALL,
        key="skills",
    )
    assert selected == ("audit", "review")
    assert audiences == {"audit": ("A",), "review": ALL}


def test_the_field_level_mapping_is_refused_and_says_what_to_write():
    """The shape this field used to take, and the reason it stopped."""
    with pytest.raises(AgentError, match=r"name: audit, source_ids"):
        audienced({"audit": {"source_ids": ["A"]}}, absent=ALL, key="skills")


def test_an_entry_that_is_neither_a_name_nor_a_mapping_is_refused():
    """`- [A]` has no reading."""
    with pytest.raises(AgentError, match="neither a name nor a mapping"):
        audienced([["A"]], absent=ALL, key="skills")


def test_an_entry_that_states_no_audience_inherits_the_definitions():
    """What makes the mapping form usable: only the entries you actually restrict carry
    a `source_ids:` line, and the rest are selected and left to inherit.
    """
    selected, audiences = audienced(
        [{"name": "audit", "source_ids": ["A"]}, "review", {"name": "triage"}],
        absent=ALL,
        key="skills",
    )
    assert selected == ("audit", "review", "triage")
    assert audiences == {"audit": ("A",)}


def test_long_entries_that_restrict_nothing_are_just_names():
    """The degenerate case reads as what it is, rather than being refused."""
    assert audienced(
        [{"name": "a"}, {"name": "b"}], absent=ALL, key="skills"
    ) == (("a", "b"), {})


def test_a_mistyped_entry_key_is_refused_with_a_suggestion():
    """What the nested form buys that a bare list cannot: an entry has keys, so a typo
    in one is catchable.
    """
    with pytest.raises(AgentError, match="did you mean 'source_ids'"):
        audienced([{"name": "audit", "sorce_ids": ["A"]}], absent=ALL, key="skills")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    """`source_ids: A` would otherwise become the source ids 'A' spelled one letter at a time,
    which is the mistake `selection` refuses one level up.
    """
    with pytest.raises(AgentError, match="a list of names"):
        audienced([{"name": "audit", "source_ids": "A"}], absent=ALL, key="skills")


def test_an_empty_entry_audience_is_refused():
    """`source_ids: []` would mean nobody, and the way to say "no restriction" is to leave
    the line out -- so an empty one is an unfinished edit.
    """
    with pytest.raises(AgentError, match="Leave the line out to inherit the audience around it"):
        audienced([{"name": "audit", "source_ids": []}], absent=ALL, key="skills")


def test_a_star_mixed_with_names_is_refused():
    with pytest.raises(AgentError, match="cannot mean both"):
        audienced([{"name": "audit", "source_ids": ["*", "A"]}], absent=ALL, key="skills")


def test_an_entry_may_not_be_named_the_star():
    """The star is a property of the field, not of an entry."""
    with pytest.raises(AgentError, match="does not take"):
        audienced([{"name": "*", "source_ids": ["A"]}], absent=ALL, key="skills")


def test_a_name_written_twice_is_refused_rather_than_collapsed():
    """The reason this field stopped taking a mapping, asserted here."""
    with pytest.raises(AgentError, match="names 'audit' twice"):
        audienced(
            [
                {"name": "audit", "source_ids": ["A"]},
                {"name": "audit", "source_ids": ["B"]},
            ],
            absent=ALL,
            key="skills",
        )


def test_a_bare_name_and_the_same_name_written_long_are_still_twice():
    """The spellings mix in one list, so the check is on names and not shapes."""
    with pytest.raises(AgentError, match="names 'audit' twice"):
        audienced(
            ["audit", {"name": "audit", "source_ids": ["A"]}], absent=ALL, key="skills"
        )


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AgentError, match=r"x\.yaml"):
        audienced([{"name": "audit", "source_ids": []}], absent=ALL, key="skills")


# -- requiring several source ids at once ---------------------------------------


def test_a_requirement_is_one_entry_of_an_entry_audience():
    assert audienced(
        [{"name": "audit", "source_ids": ["admin", {"finance": None, "senior": None}]}],
        absent=ALL,
        key="skills",
    ) == (("audit",), {"audit": ("admin", frozenset({"finance", "senior"}))})


def test_a_requirement_with_a_value_after_a_name_is_refused():
    """`{finance: senior}` is a mapping with a value, not a set of two names, and
    iterating it for its keys reads as a requirement the author never wrote -- it takes
    `finance` and throws away what was written beside it. That is how the old
    `all_of: {finance: senior}` behaved before it was refused.
    """
    with pytest.raises(AgentError, match=r"\{a, b\}, not \{a: b\}"):
        audienced(
            [{"name": "audit", "source_ids": [{"finance": True}]}],
            absent=ALL,
            key="skills",
        )


def test_a_requirement_of_one_is_that_one_name():
    """Not special-cased: a set of one is satisfied by holding one, which is what the
    bare name means.
    """
    _, audiences = audienced(
        [{"name": "audit", "source_ids": [{"finance": None}]}], absent=ALL, key="skills"
    )

    assert audiences["audit"] == (frozenset({"finance"}),)


def test_an_empty_requirement_is_refused():
    """It would require nothing and so admit everyone, which is not what somebody
    writing a set of names was reaching for.
    """
    with pytest.raises(AgentError, match="requires nothing"):
        audienced(
            [{"name": "audit", "source_ids": [{}]}], absent=ALL, key="skills"
        )


def test_the_retired_keyword_is_refused_as_the_mapping_with_a_value_it_is():
    """Read as a set, `{all_of: [A, B]}` would require the single name `all_of`, which
    nobody holds -- an audience quietly narrowed to nobody. Nothing names the old key any
    more; it is refused because a value is written after it, in a file as from a mapping.
    """
    with pytest.raises(AgentError, match=r"\{a, b\}, not \{a: b\}"):
        audienced(
            [{"name": "audit", "source_ids": [{"all_of": ["A", "B"]}]}],
            absent=ALL,
            key="skills",
        )
    with pytest.raises(AgentError, match=r"\{a, b\}, not \{a: b\}"):
        read_agent(
            DefinitionText(
                "name: x\ndescription: d\nsource_ids: [{all_of: [A, B]}]\n"
                "system_prompt: |\n  Go.\n",
                Path("x.yaml"),
            )
        )


def test_a_requirement_written_as_the_whole_audience_is_refused():
    """`source_ids: {A, B}` has no list to be one entry of, so it reads as the whole
    audience being a set -- and the refusal shows the brackets.
    """
    with pytest.raises(AgentError, match=r"\[\{A, B\}\]"):
        audienced(
            [{"name": "audit", "source_ids": {"A": None, "B": None}}], absent=ALL, key="skills"
        )


def test_a_mapping_with_a_value_as_the_whole_audience_is_not_advised_as_a_set():
    """`source_ids: {all_of: [A, B]}` is a mapping with a value, and the whole-audience
    refusal would have told it to write `[{all_of}]` -- a requirement on a name nobody holds.
    """
    with pytest.raises(AgentError, match=r"\{a, b\}, not \{a: b\}") as refused:
        audienced(
            [{"name": "audit", "source_ids": {"all_of": ["A", "B"]}}], absent=ALL, key="skills"
        )

    assert "[{all_of}]" not in str(refused.value)


def test_everyone_cannot_be_part_of_a_requirement():
    """`*` is everyone, so requiring it alongside a source id is either everyone or that
    source id, and there is no way to tell which was meant.
    """
    with pytest.raises(AgentError, match="everyone"):
        audienced(
            [{"name": "audit", "source_ids": [{"*": None, "A": None}]}],
            absent=ALL,
            key="skills",
        )


def test_a_definitions_own_line_reads_a_requirement_the_same_way():
    """One reader for both sites, so the two cannot drift about what the same list
    means.
    """
    assert read.source_ids(["admin", {"finance": None, "senior": None}]) == (
        "admin",
        frozenset({"finance", "senior"}),
    )


def test_a_python_set_is_a_requirement():
    """A definition written in Python had no way to say "all of" but `{"a": None}`: a set
    was turned into one source id spelled `"{'finance', 'senior'}"`, which nobody holds.
    """
    assert read.source_ids(["admin", {"finance", "senior"}]) == (
        "admin",
        frozenset({"finance", "senior"}),
    )


@pytest.mark.parametrize("inner", [("finance", "senior"), ["finance", "senior"]])
def test_a_list_inside_an_audience_is_refused_rather_than_guessed(inner):
    """It became one source id spelled like the list. Reading it as "all of" instead would
    make the same brackets mean "or" one level up, and a reader who gets the depth wrong
    hands the tool to anybody holding either name.
    """
    with pytest.raises(AgentError, match=r"write \{finance, senior\}"):
        read.source_ids([inner, "admin"])


def test_a_python_set_as_the_whole_audience_is_refused():
    """The Python spelling of `source_ids: {A, B}`, refused the same way and for the same
    reason: there is no list for the requirement to be one entry of.
    """
    with pytest.raises(AgentError, match=r"\[\{A, B\}\]"):
        read.source_ids({"B", "A"})


def test_a_python_set_of_anything_but_names_is_refused():
    """A tuple inside a set would otherwise become one source id spelled like the tuple."""
    with pytest.raises(AgentError, match="set of names"):
        read.source_ids([{("finance", "senior"), "admin"}])


@pytest.mark.parametrize(
    "written",
    [[1001], ["A", True], [{1001: None, "A": None}]],
    ids=["number", "yes", "in-a-requirement"],
)
def test_an_id_yaml_typed_is_refused_rather_than_spelled_out(written):
    """Spelled out with `str()`, `[1001]` was a lock seed counted as no lock and copied
    without a word, and `[yes]` a lock on `"True"`, which no caller writing `yes` holds.
    """
    with pytest.raises(AgentError, match="is not a name"):
        read.source_ids(written)


def test_an_empty_entry_is_still_left_out_beside_a_name():
    """The control for the refusal above: YAML's empty list item is no id YAML typed, and
    refusing it with the numbers would refuse a list a trailing `-` left behind."""
    assert read.source_ids(["A", None]) == ("A",)


def test_an_unquoted_number_in_a_skills_entry_is_refused_too():
    """An entry's audience is read by the same list, so a skill locked to `[42]` was the
    same lock seed could not see."""
    text = (
        "name: a\ndescription: d\nskills:\n  - {name: x, source_ids: [42]}\n"
        "system_prompt: |\n  Hi.\n"
    )
    with pytest.raises(AgentError, match="42 is not a name"):
        read_agent(DefinitionText(text, Path("a.yaml")))


def test_a_tool_spec_naming_a_number_is_refused():
    """A `ToolSpec` goes through the same reader, so `source_ids=[1001]` locked the tool
    to `"1001"` where seed saw no lock."""
    from langchain_core.tools import tool

    from kingfisher.core.definitions.tools.catalogue import ToolError, found_in
    from kingfisher.core.definitions.tools.spec import ToolSpec

    @tool
    def payroll(month: str) -> str:
        """The month's payroll."""
        return month

    spec = ToolSpec(payroll, source_ids=[1001])  # ty: ignore[invalid-argument-type]
    with pytest.raises(ToolError, match="1001 is not a name"):
        found_in(spec, where="tools.py", holder="TOOLS")


def test_an_empty_python_set_is_refused():
    """`set()` requires nothing and so admits everyone, as `{}` does."""
    with pytest.raises(AgentError, match="requires nothing"):
        read.source_ids([set()])


def test_a_definitions_own_line_still_takes_a_single_unbracketed_name():
    """The one thing the two sites do not agree on, kept rather than reconciled: every
    list field in these formats takes a lone name, and an entry's audience is already
    nested in a mapping where a bare string reads as an unfinished edit.
    """
    assert read.source_ids("analysts") == ("analysts",)


@pytest.mark.parametrize("written", ["datazone=finance", ["datazone=finance"]])
def test_a_datazone_spelled_as_a_source_id_is_refused_however_the_line_is_written(written):
    """A lone name returned before the `datazone=` check its list form meets, so
    `source_ids: datazone=finance` loaded and shut the agent to every caller outside a
    datazone it may not even require.
    """
    with pytest.raises(AgentError, match="how a datazone is held"):
        read.source_ids(written)


@pytest.mark.parametrize("written", ["datazone=finance", ["datazone=finance"]])
def test_a_tool_spec_spelled_with_a_held_datazone_is_refused_either_way(written):
    """The same reader as a definition's line, so `ToolSpec(source_ids="datazone=x")`
    went through the same gap.
    """
    from langchain_core.tools import tool

    from kingfisher.core.definitions.tools.catalogue import ToolError, found_in
    from kingfisher.core.definitions.tools.spec import ToolSpec

    @tool
    def payroll(month: str) -> str:
        """The month's payroll."""
        return month

    with pytest.raises(ToolError, match="how a datazone is held"):
        found_in(ToolSpec(payroll, source_ids=written), where="tools.py", holder="TOOLS")


# -- the two fields whose entries say nothing about who may use them -------------

HEAD = "name: x\ndescription: d\nsource_ids: [A, B]\nsystem_prompt: |\n  Go.\n"

#: Each reader a `tools:` or `subagents:` list is written to, and the error it raises.
FORMATS = {
    "agent": (lambda text: read_agent(DefinitionText(text, Path("x.yaml"))), AgentError),
    "subagent": (lambda text: a_subagent(text, "x.yaml"), SubagentError),
}


@pytest.mark.parametrize("kind", sorted(FORMATS))
def test_a_tools_entry_saying_who_may_use_it_is_refused_toward_the_tools_file(kind):
    """A `tools:` entry's audience was a second lock beside the tool's own, so who may use
    a tool had to be read off every agent granting it.
    """
    reader, error = FORMATS[kind]

    with pytest.raises(error, match=r"ToolSpec\(tool, source_ids=\[\.\.\.\]\)") as refused:
        reader(HEAD + "tools:\n  - line_count\n  - name: sql_query\n    source_ids: [A]\n")

    assert "tools entry 'sql_query' writes source_ids" in str(refused.value)
    assert str(refused.value).endswith("Write the entry as - sql_query")


def test_a_bundled_tools_entry_keeps_its_source_in_the_remedy():
    """A remedy saying to write the bare name would move a bundled tool to the catalogue."""
    with pytest.raises(SubagentError, match=r"- \{name: probe, source: bundled\}$"):
        a_subagent(
            HEAD + "tools:\n  - name: probe\n    source: bundled\n    source_ids: [A]\n",
            "x.yaml",
        )


@pytest.mark.parametrize("kind", sorted(FORMATS))
def test_a_subagents_entry_saying_who_may_reach_it_is_refused_toward_its_own_line(kind):
    """A `subagents:` entry's audience was a second lock beside the subagent's own line,
    so who reaches a subagent had to be read off every definition naming it.
    """
    reader, error = FORMATS[kind]

    with pytest.raises(error, match=r"subagents entry 'auditor' writes source_ids") as refused:
        reader(HEAD + "subagents:\n  - name: auditor\n    source_ids: [A]\n")

    assert "the source_ids: line in its own file" in str(refused.value)


def test_a_subagents_entry_written_long_with_nothing_beside_its_name_is_refused():
    """With no audience to carry, the long form would be a second spelling of a name."""
    with pytest.raises(AgentError, match=r"subagents entry 'auditor' is written long"):
        read_agent(DefinitionText(HEAD + "subagents:\n  - name: auditor\n", Path("x.yaml")))


@pytest.mark.parametrize("field", ["tools", "subagents"])
def test_a_field_written_as_a_mapping_is_told_to_be_a_list_of_names(field):
    """The mapping refusal taught the long form with `source_ids`, which is itself refused."""
    with pytest.raises(AgentError, match=rf"write {field}: \[auditor\]") as refused:
        read_agent(
            DefinitionText(HEAD + f"{field}:\n  auditor:\n    source_ids: [A]\n", Path("x.yaml"))
        )

    assert "source_ids: [...]}" not in str(refused.value)


def _build(model: object, tools: object) -> object:
    """Never called: `declared` only checks that `build` can be."""
    return object()


def test_a_compiled_subagents_tools_mapping_is_refused_toward_a_list_of_names():
    """A Python-declared subagent could write each tool's audience as a mapping, a per-agent
    lock nothing else in the format may write.
    """
    with pytest.raises(SubagentError, match=r"write tools: \[sql_query\]") as refused:
        declared(
            {
                "name": "profiler",
                "description": "Profiles.",
                "tools": {"sql_query": {"source_ids": ["A"]}},
                "build": _build,
            },
            "graphs.py",
        )

    assert "ToolSpec(tool, source_ids=[...])" in str(refused.value)


def test_a_compiled_subagents_tools_list_names_its_tools():
    """The control: a refusal of every compiled `tools` would pass the rule above."""
    spec = declared(
        {"name": "profiler", "description": "Profiles.", "tools": ["sql_query"], "build": _build},
        "graphs.py",
    )

    assert spec.tools == ("sql_query",)


@pytest.mark.parametrize("kind", sorted(FORMATS))
def test_a_skills_entry_still_says_who_is_told_about_it(kind):
    """A skill's lock goes nowhere else -- its `SKILL.md` is read whole by the model -- so
    refusing its long form with the other two would leave it no way to be kept from anyone.
    """
    reader, _ = FORMATS[kind]

    spec = reader(
        HEAD + "builtin_tools: [read_file]\n"
        "skills:\n  - name: tabular-qa\n    source_ids: [A]\n  - code-review\n"
    )

    assert spec.skill_audiences == {"tabular-qa": ("A",)}
    assert spec.declares(frozenset({"B"})).skills == ("code-review",)
    assert spec.declares(frozenset({"A"})).skills == ("tabular-qa", "code-review")
