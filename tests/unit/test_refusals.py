"""Every way a catalogue is refused, and whether `doctor` says so.

Five refusals escaped `doctor` at once, and each escaped differently: an agent file
it never looked at, a middleware directory nothing read, a clash that made the command
itself crash, and two definitions naming a tool by a path it had moved from. They were
found by hand. This is what makes the next one fail the build instead.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.presentation.cli.health import examine, worst

SRC = Path(__file__).resolve().parents[2] / "src" / "kingfisher"

#: The areas a definition is read in. A refusal outside these is something else --
#: a request being narrowed, a port being wired -- and is not what `doctor` promises.
AREAS = ("kinds", "infrastructure/catalogue")

#: The errors a kind raises. `load` and `require_literal_prompt` raise neither: they
#: take the error class from the caller, which is how two refusals hid from the first
#: version of this rule -- it looked for `raise <Kind>Error` and they raise a parameter.
KIND_ERRORS = frozenset({
    "AgentError", "CapabilityError", "MiddlewareError",
    "SkillError", "SubagentError", "ToolError",
})

AGENT = "name: {name}\ndescription: An agent.\nsystem_prompt: |\n  Do the thing.\n"
SUBAGENT = "name: {name}\ndescription: A subagent.\nsystem_prompt: |\n  Do the thing.\n"
TOOL = (
    "from langchain_core.tools import tool\n\n\n@tool\ndef {n}(x: str) -> str:\n"
    '    """Do {n}."""\n    return x\n\n\nTOOLS = [{n}]\n'
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _root(cfg, kind: str) -> Path:
    return cfg.catalogue_roots[kind]


# -- one catalogue per refusal ----------------------------------------------
#
# Each writes the smallest thing that reaches the refusal it is filed under, and
# `test_a_defect_reaches_the_refusal_it_is_filed_under` is what holds it there: the
# filing is checked against the frame that actually raised, not taken on trust.

def two_agents_of_a_name(cfg):
    _write(_root(cfg, "agents") / "a.yaml", AGENT.format(name="dup"))
    _write(_root(cfg, "agents") / "b.yaml", AGENT.format(name="dup"))


def an_agent_that_will_not_parse(cfg):
    _write(_root(cfg, "agents") / "bad.yaml", "name: [unclosed\n")


def an_agent_missing_a_field(cfg):
    _write(_root(cfg, "agents") / "a.yaml", "name: a\n")


def an_agent_gating_every_tool_with_a_star(cfg):
    _write(
        _root(cfg, "agents") / "a.yaml",
        'name: a\ndescription: d\nsystem_prompt: |\n  Go.\ninterrupt_on: ["*"]\n',
    )


def a_prompt_written_so_it_reflows(cfg):
    _write(
        _root(cfg, "subagents") / "s.yaml",
        'name: s\ndescription: d\nsystem_prompt: "one line"\n',
    )


def a_tool_module_that_will_not_import(cfg):
    _write(_root(cfg, "tools") / "broken.py", "def (:\n")


MIDDLEWARE = (
    "from langchain.agents.middleware import AgentMiddleware\n\n\n"
    "class {c}(AgentMiddleware):\n    name = {n!r}\n\n\nMIDDLEWARES = [{c}]\n"
)


def two_files_defining_a_middleware_of_a_name(cfg):
    _write(_root(cfg, "middlewares") / "a.py", MIDDLEWARE.format(c="A", n="dup"))
    _write(_root(cfg, "middlewares") / "b.py", MIDDLEWARE.format(c="B", n="dup"))


def a_middleware_that_is_not_one(cfg):
    _write(_root(cfg, "middlewares") / "m.py", "class M:\n    name = 'm'\n\n\nMIDDLEWARES = [M]\n")


def a_bundle_holding_two_definitions(cfg):
    _write(_root(cfg, "subagents") / "s" / "s.yaml", SUBAGENT.format(name="s"))
    _write(_root(cfg, "subagents") / "s" / "t.yaml", SUBAGENT.format(name="t"))


def a_subagent_filed_as_yml(cfg):
    _write(_root(cfg, "subagents") / "near.yml", SUBAGENT.format(name="near"))


def a_subagent_with_a_field_nobody_reads(cfg):
    _write(_root(cfg, "subagents") / "s.yaml", SUBAGENT.format(name="s") + "nope: 1\n")


def a_declared_subagent_with_no_name(cfg):
    _write(_root(cfg, "subagents") / "m.py", "SUBAGENTS = [{'description': 'd'}]\n")


def a_subagent_that_will_not_parse(cfg):
    _write(_root(cfg, "subagents") / "bad.yaml", "name: [unclosed\n")


def a_subagent_missing_a_field(cfg):
    _write(_root(cfg, "subagents") / "thin.yaml", "description: A subagent.\n")


#: A portable declaration -- no `build`, so kingfisher assembles it -- with one key
#: spliced in. One template rather than six literals: every defect below differs by
#: exactly that key, and six copies would let five of them drift out of the format
#: while still refusing something.
PORTABLE = (
    "SUBAGENTS = [{{'name': 'p', 'description': 'd', "
    "'system_prompt': 'Go.'{extra}}}]\n"
)

#: The same, with a real tool defined above it to carry.
CARRYING = (
    "from langchain_core.tools import tool\n\n\n"
    "@tool\ndef one(x: str) -> str:\n"
    '    """Do one."""\n    return x\n\n\n'
    "SUBAGENTS = [{{'name': 'p', 'description': 'd', 'system_prompt': 'Go.', "
    "'bundle': {{'tools': {tools}}}}}]\n"
)


def a_compiled_subagent_whose_build_is_not_callable(cfg):
    _write(
        _root(cfg, "subagents") / "c.py",
        "SUBAGENTS = [{'name': 'c', 'description': 'd', 'build': 'nope'}]\n",
    )


def a_portable_subagent_naming_a_model(cfg):
    _write(_root(cfg, "subagents") / "p.py", PORTABLE.format(extra=", 'model': 'cheap'"))


def a_portable_subagent_carrying_tools_that_are_not_a_list(cfg):
    _write(
        _root(cfg, "subagents") / "p.py",
        PORTABLE.format(extra=", 'bundle': {'tools': 'nope'}"),
    )


def a_portable_subagent_whose_skills_are_a_relative_path(cfg):
    _write(
        _root(cfg, "subagents") / "p.py",
        PORTABLE.format(extra=", 'bundle': {'skills': 'skills'}"),
    )


def a_portable_subagent_carrying_a_class(cfg):
    _write(_root(cfg, "subagents") / "p.py", CARRYING.format(tools="[str]"))


def a_portable_subagent_carrying_two_tools_of_a_name(cfg):
    _write(_root(cfg, "subagents") / "p.py", CARRYING.format(tools="[one, one]"))


def a_tool_module_declaring_nothing(cfg):
    _write(_root(cfg, "tools") / "x.py", "X = 1\n")


def a_tool_module_defining_a_name_twice(cfg):
    _write(
        _root(cfg, "tools") / "twice.py",
        "from langchain_core.tools import tool\n\n\n"
        '@tool("probe")\ndef first(x: str) -> str:\n    """Probe."""\n    return x\n\n\n'
        '@tool("probe")\ndef second(x: str) -> str:\n    """Probe."""\n    return x\n\n\n'
        "TOOLS = [first, second]\n",
    )


def a_subagent_naming_a_tool_that_moved(cfg):
    _write(_root(cfg, "tools") / "here.py", TOOL.format(n="probe"))
    _write(
        _root(cfg, "subagents") / "s.yaml",
        SUBAGENT.format(name="s") + "tools: [gone.py::probe]\n",
    )


def a_subagent_miscounting_its_own_folder(cfg):
    _write(_root(cfg, "subagents") / "s" / "s.yaml", SUBAGENT.format(name="s"))
    _write(_root(cfg, "subagents") / "s" / "tools" / "t.py", TOOL.format(n="unlisted"))


@dataclass(frozen=True)
class Refusal:
    """One function that refuses a catalogue, and how a person finds out.

    `raises` is checked against the code rather than kept as a note: a refusal added
    to a function already in this table would otherwise inherit an entry written
    about a different one.
    """

    raises: int
    #: A catalogue that reaches it. `None` when no file on disk can.
    defect: Callable[..., None] | None = None
    #: Why nothing on disk reaches it, which is a claim about *when* it fires rather
    #: than about whether it matters.
    unreachable: str = ""


#: Every refusal in the catalogue-reading code. Deny by default: a function that
#: refuses and is not named here fails `test_every_refusal_in_the_catalogue_is_named`,
#: and an entry naming a function that no longer refuses fails it too.
REFUSALS: dict[str, Refusal] = {
    "kinds/agents/catalogue.py::LocalAgentRepository._defined": Refusal(
        1, defect=two_agents_of_a_name),
    "kinds/agents/reading.py::read": Refusal(1, defect=an_agent_that_will_not_parse),
    "kinds/agents/spec.py::parse": Refusal(3, defect=an_agent_missing_a_field),
    "kinds/agents/spec.py::_gated_tools": Refusal(
        1, defect=an_agent_gating_every_tool_with_a_star),
    # Raises the error class it was handed, which is how it stayed out of the first
    # version of the rule below.
    # The walk both kinds' definition roots are read by. A subagent catalogue reaches
    # it here because that call passes `skipping` and the agent one does not, so the
    # defect filed covers the shape with the extra branch in it.
    "kinds/documents.py::documents_in": Refusal(1, defect=a_subagent_filed_as_yml),
    "kinds/documents.py::require_literal_prompt": Refusal(
        1, defect=a_prompt_written_so_it_reflows),
    "kinds/importing.py::load": Refusal(2, defect=a_tool_module_that_will_not_import),
    # The envelope all three kinds' modules arrive in. A tool catalogue reaches it
    # here because one of them has to; what the other two get from the same two
    # refusals is now the same sentence rather than a near copy of it.
    "kinds/importing.py::exported_from": Refusal(2, defect=a_tool_module_declaring_nothing),
    "kinds/middlewares/catalogue.py::LocalMiddlewareRepository.found": Refusal(
        1, defect=two_files_defining_a_middleware_of_a_name),
    "kinds/middlewares/catalogue.py::_refuse_unless_buildable": Refusal(
        2, defect=a_middleware_that_is_not_one),
    "kinds/subagents/catalogue.py::LocalSubagentRepository.bundles": Refusal(
        1, defect=a_bundle_holding_two_definitions),
    "kinds/subagents/spec.py::_refuse_unknown": Refusal(
        1, defect=a_subagent_with_a_field_nobody_reads),
    "kinds/subagents/spec.py::_carried": Refusal(
        4, defect=a_portable_subagent_carrying_tools_that_are_not_a_list),
    "kinds/subagents/spec.py::_portable": Refusal(
        4, defect=a_declared_subagent_with_no_name),
    "kinds/subagents/spec.py::_skills_directory": Refusal(
        3, defect=a_portable_subagent_whose_skills_are_a_relative_path),
    # Filed on the build rather than on a missing name, which is what it used to be:
    # an entry with no `build` is now a portable declaration rather than a compiled
    # one missing a key, so the old defect reaches `_portable` and never gets here.
    "kinds/subagents/spec.py::declared": Refusal(
        5, defect=a_compiled_subagent_whose_build_is_not_callable),
    "kinds/subagents/reading.py::read": Refusal(1, defect=a_subagent_that_will_not_parse),
    "kinds/subagents/spec.py::parse": Refusal(2, defect=a_subagent_missing_a_field),
    "kinds/tools/catalogue.py::CarriedTools.found": Refusal(
        1, defect=a_portable_subagent_carrying_two_tools_of_a_name),
    "kinds/tools/catalogue.py::LocalToolRepository.found": Refusal(
        1, defect=a_tool_module_defining_a_name_twice),
    "kinds/tools/catalogue.py::refuse_untoollike": Refusal(
        2, defect=a_portable_subagent_carrying_a_class),
    "kinds/tools/spec.py::Offering.refuse_moved": Refusal(
        1, defect=a_subagent_naming_a_tool_that_moved),
    # The four a file on disk cannot reach. Each was measured the same way the others
    # were -- by writing the defect and watching a catalogue accept it -- rather than
    # reasoned about, because "nothing can reach this" is the claim in this table
    # most likely to be wrong and least likely to be noticed.
    "kinds/skills/registry.py::SkillRegistry.resolve": Refusal(
        3, unreachable="a grant naming a skill two sources both offer, which is a request"),
    "kinds/subagents/rules.py::refuse_miscounted": Refusal(
        1, defect=a_subagent_miscounting_its_own_folder),
    "kinds/subagents/rules.py::refuse_cycles": Refusal(
        1, unreachable="asked in `_activated_subagents`, over the set a request "
                       "activates rather than over a file on disk"),
    "kinds/subagents/rules.py::refuse_two_of_a_name": Refusal(
        1, unreachable="two of a name coexist in a catalogue on purpose; it is an agent "
                       "holding both that is refused, which is a request"),
    "kinds/tools/spec.py::Offering.refuse_unknown": Refusal(
        1, unreachable="a request naming a tool the workspace does not offer"),
}


def _refusals_in(path: Path, found: dict[str, int]) -> None:
    """Every function in one file that raises a kind's error, and how often.

    Counts a `raise` of a name the function was *given* -- whether handed as the class
    itself or as a field of something handed -- as well as one it names, so a refusal
    that takes its error class from the caller cannot slip past. Three of them did,
    each in one of those two ways, until this was written to look for both.
    """

    def walk(node: ast.AST, stack: list[str], params: dict[str, set[str]]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = [*stack, child.name]
                taken = {a.arg for a in child.args.args + child.args.kwonlyargs}
                walk(child, inner, {**params, ".".join(inner): taken})
            elif isinstance(child, ast.ClassDef):
                walk(child, [*stack, child.name], params)
            elif isinstance(child, ast.Raise):
                raised = getattr(child.exc, "func", None)
                # `raise declares.error(msg)` as well as `raise error(msg)`: an
                # envelope carrying the kind's error class is still a class the
                # function was handed, and reading only `.id` counted it as no
                # refusal at all -- which is how the first two hid.
                callee = getattr(raised, "id", None) or getattr(
                    getattr(raised, "value", None), "id", None
                )
                handed = callee is not None and any(
                    callee in params.get(".".join(stack[: index + 1]), set())
                    for index in range(len(stack))
                )
                if callee in KIND_ERRORS or handed:
                    key = f"{path.relative_to(SRC).as_posix()}::{'.'.join(stack)}"
                    found[key] = found.get(key, 0) + 1
            else:
                walk(child, stack, params)

    walk(ast.parse(path.read_text(encoding="utf-8")), [], {})


def _refusals_in_the_code() -> dict[str, int]:
    """The whole of it, over the areas a definition is read in."""
    found: dict[str, int] = {}
    for area in AREAS:
        for path in sorted((SRC / area).rglob("*.py")):
            _refusals_in(path, found)
    return found


def _raised_by(exc: BaseException) -> str:
    """The last frame inside the package, as this table keys them."""
    frame, deepest = exc.__traceback__, None
    while frame is not None:
        code = frame.tb_frame.f_code
        if f"{SRC}/" in code.co_filename:
            deepest = (code.co_filename.split(f"{SRC}/")[1], code.co_qualname)
        frame = frame.tb_next
    assert deepest is not None, "nothing in the package raised it"
    return f"{deepest[0]}::{deepest[1]}"


def test_every_refusal_in_the_catalogue_is_named():
    """A refusal added without an entry, or an entry outliving its refusal."""
    in_code = _refusals_in_the_code()
    assert in_code, "no refusals found at all, so this rule is about nothing"

    missing = sorted(set(in_code) - set(REFUSALS))
    assert not missing, (
        f"{missing} refuse a catalogue and are not in REFUSALS — add an entry saying "
        "which broken catalogue reaches it, or why none can"
    )
    gone = sorted(set(REFUSALS) - set(in_code))
    assert not gone, f"{gone} are in REFUSALS and refuse nothing any more; delete them"


def test_each_entry_counts_the_refusals_it_covers():
    """So a refusal added to a function already here inherits an entry written about
    a different one, and nothing says so.
    """
    in_code = _refusals_in_the_code()
    wrong = {
        key: (entry.raises, in_code[key])
        for key, entry in REFUSALS.items()
        if entry.raises != in_code.get(key)
    }
    assert not wrong, (
        f"{wrong} say (named, actual) — check `doctor` still reports the new one, "
        "then update the count"
    )


@pytest.mark.parametrize(
    "key", sorted(k for k, v in REFUSALS.items() if v.defect is not None)
)
def test_a_defect_reaches_the_refusal_it_is_filed_under(key, cfg):
    """The filing, checked rather than trusted.

    A defect that quietly stopped reaching its refusal would leave the rule below
    passing on a different failure entirely -- which is most of what these entries
    claim, and none of it is visible from the message.
    """
    defect = REFUSALS[key].defect
    assert defect is not None, "the parametrize above selects on this"
    defect(cfg)

    with pytest.raises(Exception) as caught:
        Definitions.from_config(cfg).warm()

    assert _raised_by(caught.value) == key


@pytest.mark.parametrize(
    "key", sorted(k for k, v in REFUSALS.items() if v.defect is not None)
)
def test_doctor_fails_on_every_refusal_a_file_can_reach(key, cfg):
    """The promise: `doctor` exiting zero means nothing in the catalogue will break.

    Driven against each broken catalogue rather than asserted about the checks,
    because what matters is the exit code somebody gates on and not which check
    happens to carry the message.
    """
    defect = REFUSALS[key].defect
    assert defect is not None, "the parametrize above selects on this"
    defect(cfg)

    assert worst(examine(cfg)) == "fail"


def test_a_refusal_no_file_can_reach_says_why():
    """The other half of the table, which a green tree cannot show.

    An entry with neither a defect nor a reason is a refusal nobody checked, and it
    would sit here looking like one that had been.
    """
    silent = sorted(
        key
        for key, entry in REFUSALS.items()
        if entry.defect is None and not entry.unreachable.strip()
    )
    assert not silent, f"{silent} have no defect and no reason; one or the other"
