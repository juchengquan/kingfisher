"""A `ToolSpec`: any form of tool, given a new name or description as it loads."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from kingfisher import ToolSpec
from kingfisher.core.definitions.tools.catalogue import (
    CarriedTools,
    LocalToolRepository,
    ToolError,
    found_in,
)
from kingfisher.core.definitions.tools.spec import tool_name
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


async def count_later(path: str) -> str:
    """Count the lines in a text file, without holding the loop."""
    return "0"


class Counter(BaseTool):
    name: str = "counter"
    description: str = "Count the lines in a text file."

    def _run(self, path: str) -> str:
        return "0"


#: Every form `TOOLS` takes bare, made fresh for each test so none sees another's copy.
FORMS = {
    "plain": lambda: count,
    "async": lambda: count_later,
    "decorated": lambda: tool(count),
    "class": Counter,
}


def found(entry: Any) -> Any:
    """One `TOOLS` entry as the loader records it."""
    return found_in(entry, where="count.py", holder="TOOLS")


def shown(made: Any) -> tuple[str, str]:
    """The name and description the model is shown, read off the tool's schema."""
    schema = convert_to_openai_tool(made)["function"]
    return schema["name"], schema["description"]


def own(original: Any) -> tuple[str, str]:
    """What the author's object says of itself."""
    return tool_name(original), getattr(original, "description", None) or original.__doc__


@pytest.mark.parametrize("form", sorted(FORMS))
def test_every_form_takes_a_new_name_and_description_and_its_author_keeps_the_old(form):
    """An override that reached the listing and not the schema, or the schema and not the
    call, would show the model one tool and run another; one made by changing the
    author's object would rename it under every other file that imports it.
    """
    original = FORMS[form]()
    before = own(original)

    one = found(ToolSpec(original, name="tally", description="Tally the lines in a file."))

    assert one.name == "tally"
    assert shown(one.tool) == ("tally", "Tally the lines in a file.")
    assert list(convert_to_openai_tool(one.tool)["function"]["parameters"]["properties"]) == [
        "path"
    ]
    assert asyncio.run(one.tool.ainvoke({"path": "x"})) == "0"
    assert own(original) == before, "the author's own object was changed"


@pytest.mark.parametrize("form", sorted(FORMS))
def test_changing_one_of_the_two_keeps_the_tools_own_other(form):
    """A rename that dropped the description, or the reverse, leaves the model a tool it
    cannot tell when to call.
    """
    original = FORMS[form]()
    own_name, own_description = own(original)

    assert shown(found(ToolSpec(original, name="tally")).tool) == ("tally", own_description)
    assert shown(found(ToolSpec(original, description="Tally.")).tool) == (own_name, "Tally.")


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_spec_that_renames_nothing_hands_on_the_tool_as_written(form):
    """Rebuilt regardless, a plain function would stop being one and a `BaseTool` with
    state would be split from the instance its author holds.
    """
    original = FORMS[form]()

    assert found(ToolSpec(original, source_ids=["A"])).tool is original


class _Described:
    @property
    def description(self) -> str:
        return "Count the lines in a text file."


class _Named:
    @property
    def name(self) -> str:
        return "fixed_counter"


class FixedDescription(_Described, BaseTool):
    name: str = "fixed_counter"

    def _run(self, path: str) -> str:
        return "0"


class FixedName(_Named, BaseTool):
    description: str = "Count the lines in a text file."

    def _run(self, path: str) -> str:
        return "0"


@pytest.mark.parametrize(
    ("made", "field", "other"),
    [(FixedDescription, "description", "name"), (FixedName, "name", "description")],
)
def test_an_override_the_tool_ignores_is_refused(made, field, other):
    """A property inherited ahead of `BaseTool` wins over the copy's value, so the model
    would be shown what the spec was written to replace, and nothing would say so.
    """
    with pytest.raises(
        ToolError, match=rf"^count\.py: tool 'fixed_counter' still answers with its own {field} "
    ):
        found(ToolSpec(made(), **{field: "Tally"}))

    assert shown(found(ToolSpec(made(), **{other: "Tally"})).tool) != shown(made()), (
        "the control: the field it does not compute is changed"
    )


@pytest.mark.parametrize(
    "given", [{"name": ""}, {"description": "  "}, {"name": 3}], ids=["empty", "blank", "number"]
)
def test_an_override_that_is_not_written_out_is_refused(given: dict[str, Any]):
    """An empty name let through would put a tool in front of the model that no
    definition can grant, under a name nothing can write.
    """
    with pytest.raises(ToolError, match=r"^count\.py: the ToolSpec for 'count' gives"):
        found(ToolSpec(count, **given))


def test_a_function_langchain_cannot_rename_is_refused_naming_the_file():
    """`StructuredTool.from_function` raises a `ValueError` of its own, which stopped
    startup with a traceback into langchain and the file named nowhere.
    """

    def undocumented(path: str) -> str:
        return "0"

    with pytest.raises(ToolError, match=r"^count\.py: tool 'undocumented' cannot take .*docstring"):
        found(ToolSpec(undocumented, name="tally"))


@pytest.mark.parametrize("inner", [Counter, "count"], ids=["class", "name"])
def test_a_spec_holding_what_is_not_a_tool_is_refused_as_a_bare_entry_is(inner):
    """Unchecked inside a spec, a class would load and answer every call with a new
    instance of itself, and a name would reach the model in quotes.
    """
    with pytest.raises(ToolError, match=r"^count\.py: TOOLS (names the class|holds str)"):
        found(ToolSpec(inner))


#: Two tools in one file whose names collide only once one is renamed.
RENAMED_ONTO_ITS_NEIGHBOUR = '''
from kingfisher import ToolSpec


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


def tally(path: str) -> str:
    """Tally the lines in a text file."""
    return "0"


TOOLS = [count, ToolSpec(tally, name="count")]
'''


def test_two_tools_one_file_gives_one_name_are_refused_by_the_names_given(tmp_path):
    """Checked on the names the file wrote, a rename onto a neighbour's name would register
    two tools as one, and the later would win in silence.
    """
    (tmp_path / "count.py").write_text(RENAMED_ONTO_ITS_NEIGHBOUR, encoding="utf-8")

    with pytest.raises(ToolError, match="tool 'count' is defined twice in this file"):
        _ = LocalToolRepository(tmp_path).found

    (tmp_path / "count.py").write_text(
        RENAMED_ONTO_ITS_NEIGHBOUR.replace('name="count"', 'name="total"'), encoding="utf-8"
    )
    assert LocalToolRepository(tmp_path).names == ("count", "total"), "the control"


def test_tools_a_definition_carries_are_read_as_a_files_are():
    """The second loader: a Python subagent's tools arrive as objects, and a spec among
    them handed on as it stands would reach deepagents as something that is not a tool.
    """
    carried = CarriedTools(
        tools=(count, ToolSpec(count, name="tally", source_ids=["A"])), source="counter"
    )

    assert carried.names == ("count", "tally")
    assert [one.source_ids for one in carried.found] == ["*", ("A",)]
    with pytest.raises(ToolError, match="tool 'count' is carried twice"):
        _ = CarriedTools(tools=(count, ToolSpec(count_later, name="count")), source="x").found


#: A Python subagent carrying a renamed tool.
CARRIER = '''
from kingfisher import ToolSpec


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


SUBAGENTS = [{
    "name": "counter",
    "description": "Counts lines.",
    "system_prompt": "You count.",
    "tools": [ToolSpec(count, name="tally")],
}]
'''


def test_a_subagent_is_built_with_its_carried_tool_under_the_new_name(cfg):
    """Driven through the real catalogue: a subagent built with the author's object
    rather than the loader's would offer the model `count` while its spec said `tally`.
    """
    from kingfisher.core.agent.kinds.tools import _private_tools

    subagents = cfg.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "counter.py").write_text(CARRIER, encoding="utf-8")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    (one,) = _private_tools(kf.catalogue, "counter", held=None)
    assert shown(one.tool) == ("tally", "Count the lines in a text file.")
