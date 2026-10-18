"""The two `SUBAGENTS` TypedDicts, held to the tables the reader refuses by."""

from __future__ import annotations

from typing import Required, get_origin, get_type_hints

import pytest

from kingfisher.core.definitions.subagents.spec import (
    DECLARED,
    PORTABLE,
    CompiledSubagent,
    PortableSubagent,
    SubagentError,
    declared,
)

#: Each shape's type, the keys its reader takes, and one value for every key it requires.
SHAPES = [
    pytest.param(
        PortableSubagent,
        PORTABLE,
        {"name": "n", "description": "d", "system_prompt": "p"},
        id="portable",
    ),
    pytest.param(
        CompiledSubagent,
        DECLARED,
        {"name": "n", "description": "d", "build": lambda model, tools: None},
        id="compiled",
    ),
]


def _keys(typed: type) -> dict[str, bool]:
    """Each key and whether it is required, read off the marker itself.

    Not `__required_keys__`, which postponed annotations leave empty: the marker is a string
    when the class is made, so every key reads as optional there and only here as written.
    """
    hints = get_type_hints(typed, include_extras=True)
    return {key: get_origin(hint) is Required for key, hint in hints.items()}


@pytest.mark.parametrize(("typed", "taken", "_"), SHAPES)
def test_each_type_names_exactly_the_keys_its_reader_takes(typed, taken, _):
    """A key added to `PORTABLE` or `DECLARED` and not to the type, which a checker would
    then refuse in an entry the reader loads -- or the other way round, a typed entry the
    reader refuses as unknown.
    """
    assert set(_keys(typed)) == taken


@pytest.mark.parametrize(("typed", "_", "minimal"), SHAPES)
def test_the_keys_a_type_requires_are_the_ones_its_reader_requires(typed, _, minimal):
    """Driven through `declared` rather than read off `_require`: a type marking a key
    optional that the reader demands passes a checker and fails at load, and the reverse
    makes every author write a key nothing needs.
    """
    required = {key for key, needed in _keys(typed).items() if needed}
    assert set(minimal) == required, "the minimal entry is not this type's"
    assert declared(minimal, "s.py").name == "n"

    # Unmatched: a compiled entry without `build` is read as portable, and refused for that.
    for key in required:
        with pytest.raises(SubagentError):
            declared({k: v for k, v in minimal.items() if k != key}, "s.py")
