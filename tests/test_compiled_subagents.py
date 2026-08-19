"""A subagent the workspace built itself, found on disk and handed to deepagents.

deepagents takes two kinds. `SubAgent` is a spec it builds, which is what
`subagents/*.yaml` has always described. `CompiledSubAgent` is a graph you built
yourself, which it runs as given -- how a delegate gets a shape a prompt cannot
express.

The two share a directory and are told apart by extension. They cannot see each
other's files: the document walk takes `.yaml` at any depth and never stops, the
module walk takes `.py` and stops at a package, so one tree carries both without
either search knowing the other exists.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.subagent import SubagentError
from kingfisher.domain.subagent.reading import EXPORT, NOT_COMPILED, declared
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel, capture_build

COMPILED = '''"""A delegate the workspace assembled."""

SUBAGENTS = [
    {{
        "name": "{name}",
        "description": "Answers again, elsewhere.",
        "build": lambda model, tools: _graph(model, tools),
    }}
]


def _graph(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)
'''

PROMPTED = """name: reviewer
description: Checks an analysis for arithmetic errors.
system_prompt: |
  You review analyses.
"""


def _write(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


# -- discovery --------------------------------------------------------------


def test_a_python_file_and_a_yaml_file_share_the_directory(cfg):
    root = cfg.workspace / "subagents"
    _write(root, "researcher.py", COMPILED.format(name="researcher"))
    _write(root, "reviewer.yaml", PROMPTED)

    specs = LocalSubagentRepository(root).specs

    assert set(specs) == {"researcher", "reviewer"}
    assert specs["researcher"].build is not None
    assert specs["reviewer"].build is None
    assert specs["researcher"].system_prompt == ""


def test_a_package_is_one_subagent_and_its_helpers_stay_private(cfg):
    """The case that made folders worth having: a graph with a few auxiliary
    functions, where only the compiled agent is exposed."""
    root = cfg.workspace / "subagents"
    _write(root / "deep_research", "__init__.py", COMPILED.format(name="deep-research"))
    _write(root / "deep_research", "steps.py", "SUBAGENTS = [{'name': 'nope'}]\n")

    assert set(LocalSubagentRepository(root).specs) == {"deep-research"}


def test_a_package_still_has_its_documents_read(cfg):
    """The walk stops at a package for *modules* and never stops for documents.

    A Python package holding data files is ordinary, and `analysis/profiler.yaml`
    must not vanish the day somebody adds an `__init__.py` beside it.
    """
    root = cfg.workspace / "subagents"
    _write(root / "analysis", "__init__.py", COMPILED.format(name="profiler-graph"))
    _write(root / "analysis", "profiler.yaml", PROMPTED)

    assert set(LocalSubagentRepository(root).specs) == {"profiler-graph", "reviewer"}


def test_a_leading_underscore_keeps_a_flat_helper_private(cfg):
    root = cfg.workspace / "subagents"
    _write(root, "researcher.py", COMPILED.format(name="researcher"))
    _write(root, "_shared.py", "raise AssertionError('never imported')\n")

    assert set(LocalSubagentRepository(root).specs) == {"researcher"}


def test_two_kinds_claiming_one_name_are_told_apart_by_file(cfg):
    """One namespace, so the rule that already handled two YAML files handles
    this without knowing the second one is Python."""
    root = cfg.workspace / "subagents"
    _write(root, "reviewer.yaml", PROMPTED)
    _write(root, "other.py", COMPILED.format(name="reviewer"))

    assert sorted(LocalSubagentRepository(root).specs) == [
        "other.py::reviewer",
        "reviewer.yaml::reviewer",
    ]


def test_a_yml_file_is_refused_rather_than_skipped(cfg):
    """`.yml` is valid YAML everywhere else, so a file named that way is a
    definition somebody wrote and kingfisher silently did not read."""
    root = cfg.workspace / "subagents"
    _write(root, "reviewer.yml", PROMPTED)

    with pytest.raises(SubagentError, match=r"reviewer\.yaml"):
        _ = LocalSubagentRepository(root).specs


def test_a_module_without_the_export_names_itself(cfg):
    """Refused rather than skipped, for the reason the tool loader gives:
    quietly offering fewer than the workspace defines is the failure
    `CapabilityError` exists to prevent, one layer down.

    Asserting the *wording*, not just the file and the export name. Dropping
    this check entirely still raises -- `None` is not a list, so the next one
    fires -- with a message about the wrong thing, and a looser assertion here
    passed that mutation.
    """
    root = cfg.workspace / "subagents"
    _write(root, "researcher.py", "RESEARCHER = object()\n")

    with pytest.raises(SubagentError, match=r"researcher\.py") as raised:
        _ = LocalSubagentRepository(root).specs

    assert f"must define {EXPORT}" in str(raised.value)


def test_a_bare_mapping_is_refused_because_a_mapping_is_iterable(cfg):
    """`SUBAGENTS = {...}` would loop over its own key names. `TOOLS` learned
    this from pydantic models, which are iterable for a different reason."""
    root = cfg.workspace / "subagents"
    _write(root, "researcher.py", "SUBAGENTS = {'name': 'r', 'description': 'd'}\n")

    with pytest.raises(SubagentError, match="list or tuple"):
        _ = LocalSubagentRepository(root).specs


# -- what a declaration may say --------------------------------------------


def _entry(**extra):
    return {"name": "r", "description": "d", "build": lambda _m, _t: object(), **extra}


#: The smallest thing deepagents will take. It calls `.with_config` on whatever
#: `build` returns, so a bare `object()` is not a stand-in for a graph -- and a
#: test that used one would be asserting against a shape deepagents rejects.
RECORDING = """from langchain_core.runnables import RunnableLambda

import kingfisher.infrastructure.catalogue.subagents as store


def _record(model, tools):
    store.SEEN = (model, [t.name for t in tools])
    return RunnableLambda(lambda state: state)


SUBAGENTS = [
    {{
        "name": "researcher",
        "description": "d",
        "build": _record,
{extra}    }}
]
"""


@pytest.mark.parametrize(
    "key", ["system_prompt", "skills", "middleware", "subagents", "builtin_tools"]
)
def test_a_key_deepagents_would_ignore_is_refused_with_its_reason(key):
    """Refused rather than dropped. A definition writing a line that does
    nothing reads tighter than the delegate it produces, and nothing in the
    output says so -- which is the whole argument the `REFUSED` table makes for
    the other format.

    The *reason* is what is asserted, not merely that something was refused.
    These keys are absent from `DECLARED` too, so deleting the explanations
    still raises -- as an unknown key, which reads as "kingfisher has not got
    round to this" when the answer is that deepagents would ignore it. A
    looser assertion here passed exactly that mutation.
    """
    with pytest.raises(SubagentError, match=key) as raised:
        declared(_entry(**{key: "x"}), "researcher.py")

    assert NOT_COMPILED[key] in str(raised.value)


def test_an_unknown_key_lists_what_the_format_takes():
    with pytest.raises(SubagentError, match="temperature") as raised:
        declared(_entry(temperature=0.2), "researcher.py")

    assert "'build'" in str(raised.value)


def test_build_has_to_be_callable():
    with pytest.raises(SubagentError, match="cannot be called"):
        declared({"name": "r", "description": "d", "build": "nope"}, "researcher.py")


@pytest.mark.parametrize("missing", ["name", "description", "build"])
def test_the_three_required_keys_are_required(missing):
    entry = _entry()
    del entry[missing]

    with pytest.raises(SubagentError, match=missing):
        declared(entry, "researcher.py")


def test_the_model_fields_mean_what_they_mean_in_yaml():
    spec = declared(_entry(model="cheap-model"), "researcher.py")

    assert spec.wanted == "cheap-model"




def test_a_spec_cannot_carry_both_a_prompt_and_a_builder():
    """Checked on the record rather than promised by two parsers, so a spec
    built in code cannot be the one shape neither parser can produce."""
    from kingfisher.domain.subagent import SubagentSpec

    with pytest.raises(ValueError, match="one or the other"):
        SubagentSpec(name="r", description="d", system_prompt="Go.", build=lambda: None)

    with pytest.raises(ValueError, match="neither"):
        SubagentSpec(name="r", description="d")


# -- building ---------------------------------------------------------------


def test_a_compiled_delegate_reaches_deepagents_as_a_runnable(cfg, monkeypatch, session_dir):
    """The shape deepagents wants: three keys, and the graph it will run."""
    _write(cfg.workspace / "subagents", "researcher.py", COMPILED.format(name="researcher"))

    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("researcher",)),
    )

    (delegate,) = captured["subagents"]
    assert set(delegate) == {"name", "description", "runnable"}
    assert delegate["name"] == "researcher"
    # None of the prompted path's fields, because none of them reach a graph
    # deepagents did not build.
    assert "system_prompt" not in delegate
    assert "middleware" not in delegate


def test_the_compiled_shape_is_deepagents_own(cfg, monkeypatch, session_dir):
    """Pinned against their declaration rather than a copy of it, so a rename
    upstream fails here instead of arriving as something confusing later."""
    from deepagents.middleware.subagents import CompiledSubAgent

    _write(cfg.workspace / "subagents", "researcher.py", COMPILED.format(name="researcher"))

    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("researcher",)),
    )

    (delegate,) = captured["subagents"]
    assert set(delegate) == set(CompiledSubAgent.__required_keys__)




def test_a_build_that_returns_nothing_is_refused(cfg, monkeypatch, session_dir):
    _write(
        cfg.workspace / "subagents",
        "researcher.py",
        "SUBAGENTS = [{'name': 'researcher', 'description': 'd', "
        "'build': lambda model, tools: None}]\n",
    )

    capture_build(monkeypatch)
    with pytest.raises(SubagentError, match="returned None"):
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(subagents=("researcher",)),
        )


A_CLASS = """
class Assembler:
    \"\"\"Callable, and not a factory. Constructing it yields no graph.\"\"\"

    def __init__(self, model, tools):
        self.model = model


SUBAGENTS = [
    {"name": "researcher", "description": "d", "build": Assembler}
]
"""


def test_a_class_under_build_is_refused_rather_than_constructed(
    cfg, monkeypatch, session_dir
):
    """`callable()` accepts a class, so this loaded and was *constructed*.

    `Assembler(model, tools)` is a plain object with no `invoke`, which reached
    deepagents and failed somewhere with nothing pointing back at the
    declaration. Only `None` was caught here, and `None` is the least likely of
    the two mistakes: nobody writes `build` meaning to return nothing, and
    naming a class is an easy thing to reach for.
    """
    _write(cfg.workspace / "subagents", "researcher.py", A_CLASS)

    capture_build(monkeypatch)
    with pytest.raises(SubagentError, match="not a graph"):
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(subagents=("researcher",)),
        )


def test_the_refusal_names_what_was_returned_and_the_class_trap(
    cfg, monkeypatch, session_dir
):
    """A reader has to know which of the two mistakes they made. The type they
    got back says it, and the class case gets said outright because nothing
    about `callable()` accepting a class is obvious from a declaration."""
    _write(cfg.workspace / "subagents", "researcher.py", A_CLASS)

    capture_build(monkeypatch)
    with pytest.raises(SubagentError) as refused:
        build_agent(
            cfg,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(subagents=("researcher",)),
        )

    said = str(refused.value)
    assert "Assembler" in said  # what came back
    assert "class is callable" in said  # and why it got that far


def test_something_that_merely_looks_like_a_graph_is_refused(cfg):
    """The looseness the first version of this check had, and admitted to.

    It duck-typed on `invoke`, so an object with that one method got through and
    failed later inside deepagents, which also calls `with_config`. Measured
    across the cases that matter -- a compiled graph, this stub, whatever a
    class constructs to, and `None` -- `Runnable` is what separates the first
    from the rest.
    """
    from kingfisher.domain.subagent import SubagentSpec
    from kingfisher.infrastructure.harness.delegation import compiled

    class OnlyInvoke:
        def invoke(self, *a, **k):
            return {}

    spec = SubagentSpec(
        name="researcher", description="d", build=lambda model, tools: OnlyInvoke()
    )

    with pytest.raises(SubagentError, match="not a graph"):
        compiled(spec, cfg)


def test_the_check_is_the_interface_not_a_particular_graph_class(cfg):
    """Against `Runnable`, which is what `CompiledSubAgent` declares -- not
    against `CompiledStateGraph`, which is an implementation class upstream may
    rename and which would take every compiled delegate down to enforce a
    spelling.

    Asserted with a `Runnable` that is emphatically not a graph: it passes
    because it implements the published interface, which is the whole claim.
    """
    from langchain_core.runnables import RunnableLambda

    from kingfisher.domain.subagent import SubagentSpec
    from kingfisher.infrastructure.harness.delegation import compiled

    not_a_graph = RunnableLambda(lambda state: state)
    spec = SubagentSpec(
        name="researcher", description="d", build=lambda model, tools: not_a_graph
    )

    delegate = compiled(spec, cfg)

    assert delegate["runnable"] is not_a_graph


PROBE = """from langchain_core.tools import tool


@tool
def probe(text: str) -> str:
    \"\"\"Probe something, at length, so the description check is satisfied.\"\"\"
    return text


TOOLS = [probe]
"""


def _with_probe(cfg):
    _write(cfg.workspace / "tools", "probe.py", PROBE)
    _write(
        cfg.workspace / "subagents",
        "researcher.py",
        RECORDING.format(extra='        "tools": ["probe"],\n'),
    )


def _tools_seen(cfg, session_dir, monkeypatch, capabilities):
    capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=capabilities,
    )
    import kingfisher.infrastructure.catalogue.subagents as store

    _model, tools = store.SEEN
    del store.SEEN
    return tools


def test_a_compiled_delegate_is_granted_the_workspace_tools_it_named(
    cfg, monkeypatch, session_dir
):
    """The one narrowing kingfisher can still apply: it hands the graph the
    workspace tools this request granted, chosen by name."""
    _with_probe(cfg)

    seen = _tools_seen(
        cfg, session_dir, monkeypatch, Capabilities(subagents=("researcher",))
    )

    assert seen == ["probe"]


def test_a_request_that_withheld_a_tool_withholds_it_from_the_graph(
    cfg, monkeypatch, session_dir
):
    """Not a guarantee -- the graph may ignore what it is handed, and nothing
    can stop it, because deepagents never applies an allowlist to a graph it did
    not build. What this keeps true is that the honest thing is the easy one."""
    _with_probe(cfg)

    seen = _tools_seen(
        cfg, session_dir, monkeypatch, Capabilities(subagents=("researcher",), tools=())
    )

    assert seen == []


def test_a_compiled_delegate_is_handed_the_tool_it_named_either_way(cfg):
    """`build` is given the tools this delegate was granted, and the grant is
    resolved by the same set membership everything else uses -- so a definition
    writing the documented long form for a tool no other file defines was handed
    an empty list, and a graph that needed it got nothing with no error at all.

    The quietest of the sites this bug touched: the parent refused out loud,
    while this one just built a delegate that could not work.
    """
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool

    from kingfisher.domain.subagent import SubagentSpec
    from kingfisher.domain.tool import Found, tool_name
    from kingfisher.infrastructure.harness.delegation import compiled

    @tool
    def probe(x: str) -> str:
        """A tool called probe."""
        return x

    handed: list = []

    def build(model, tools):
        handed.extend(tools)
        return RunnableLambda(lambda state: state)

    spec = SubagentSpec(
        name="researcher", description="d", tools=("probe.py::probe",), build=build
    )

    compiled(
        spec,
        cfg,
        catalogue=(Found(tool=probe, source="probe.py"),),
        tools=("probe",),
    )

    assert [tool_name(one) for one in handed] == ["probe"]
