"""The definitions this distribution ships have to work.

A definition that does not parse is worse than none: it is copied, it fails,
and the format gets blamed. These run against kingfisher's real loaders and
reach the files the way an installed pack is reached — through
`importlib.resources`, not by a path relative to this file.

They live here rather than in kingfisher because they describe *content*. The
framework's own tests are about seeding, discovery and the formats; whether
`reviewer` consults `second-opinion` is this package's business, and a preset
added here should not turn a test red over there.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage

from kingfisher.domain import skill
from kingfisher.domain.capabilities import ALL, Capabilities
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository
from kingfisher.infrastructure.catalogue.documents import skill_name
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository, tool_name
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel


def test_every_preset_subagent_parses(shipped):
    specs = LocalSubagentRepository(shipped / "subagents").specs

    # `profiler` ships in `subagents/analysis/`, and is named `profiler` all the
    # same: a subagent is named by its `name:` field, so a folder cannot reach
    # it. Its presence in this flat set is the assertion that nesting works.
    #
    # `redactor` ships in a folder too, and in the other kind: `subagents/
    # redactor/` holds the definition it is named after, which makes it that
    # delegate's bundle. It is named `redactor` here for the same reason
    # `profiler` is named `profiler` -- the folder decides what a definition
    # *brings*, never what it is called.
    # `first-look` is the third shape: a Python module that assembles its own
    # graph and exports it as `SUBAGENTS`. It has no `system_prompt` and cannot
    # -- whatever prompt it uses is inside the graph -- which is why the loop
    # below asks each spec for the half it actually has.
    assert set(specs) == {
        "reviewer",
        "extractor",
        "second-opinion",
        "profiler",
        "redactor",
        "first-look",
    }
    for spec in specs.values():
        assert spec.description.strip()
        if spec.build is None:
            assert len(spec.system_prompt) > 200  # a real prompt, not a stub
        else:
            # The invariant `SubagentSpec` enforces: exactly one of the two, so
            # a compiled delegate having no prompt is the format working rather
            # than a preset half-written.
            assert not spec.system_prompt


def test_every_preset_skill_parses(shipped):
    """The mirror of the subagent version, and absent until a probe went looking.

    Seeding a fourth skill preset left the entire suite green, and dropping a
    shipped one would have too. `test_preset_skills_are_discovered` asserts a
    *superset*, which is the right shape for that test -- it is about discovery
    reaching the catalogue -- and the wrong shape for declaring what ships.

    The header's name is checked against the directory because the two are read
    by different paths: a catalogue skill is found by directory
    (`LocalSkillRepository.names`), while an uploaded one is filed under the name in its
    header (`uploads.skill_name`). A preset whose halves disagree is copied,
    uploaded, and lands somewhere its author did not mean.
    """
    root = shipped / "skills"
    shipped_skills = LocalSkillRepository(root).names

    assert set(shipped_skills) == {"code-review", "release-notes", "tabular-qa"}
    for name in shipped_skills:
        text = (root / name / skill.FILENAME).read_text(encoding="utf-8")
        parts = skill.split(text)

        assert parts is not None, f"{name}: no `---` header"
        header, body = parts
        assert skill_name(text) == name  # header and directory agree
        assert yaml.safe_load(header)["description"].strip()
        # A real procedure, not a stub. The same threshold the subagent version
        # uses; the shipped bodies measure 1222-1366 characters.
        assert len(body.strip()) > 200


def test_the_extractor_preset_demonstrates_the_optional_fields(shipped):
    """Both optional fields appear in at least one example, or they are
    documented in the README and shown nowhere."""
    extractor = LocalSubagentRepository(shipped / "subagents").specs["extractor"]

    assert extractor.tools is not None
    assert "write_file" not in extractor.tools  # read-only, as its body claims
    assert extractor.builtin_tools is not None


def test_every_preset_tool_loads(shipped):
    """A tool is code, so "does it parse" means "does it import".

    `csv_profile` and `csv_columns` come from a *package* -- `tools/csv_profile/`
    with an `__init__.py` -- and arrive in this flat set under their own names,
    because a folder cannot reach a name either. That they import at all is the
    part worth having: the package uses a relative import, which is exactly what
    a standalone-module loader cannot resolve.
    """
    tools = LocalToolRepository(shipped / "tools").tools

    assert {tool_name(t) for t in tools} == {
        "http_fetch", "sql_tables", "sql_query", "csv_profile", "csv_columns",
        # A plain function rather than a `BaseTool`, which is the other thing
        # this set is here to show: kingfisher takes either, and a definition
        # should not have to know which one deepagents prefers this month.
        "line_count",
    }


def test_every_preset_tool_describes_itself_to_the_model(shipped):
    """The docstring is what the model reads when deciding whether to call it.
    An example without a real one teaches the wrong shape.

    Read as `.description` or as `__doc__`, because the shipped set holds both
    kinds now and the first version of this test assumed one. A `BaseTool` puts
    the docstring on `.description` when it is built; a plain function still has
    it on `__doc__`, and deepagents reads it from there when it wraps the
    function. The model sees the same sentence either way -- which is the whole
    claim `line_count` exists to make -- so a test about what the model reads
    should not care which kind it was handed.
    """
    for tool in LocalToolRepository(shipped / "tools").tools:
        described = getattr(tool, "description", None) or (tool.__doc__ or "")
        assert len(described.strip()) > 60, f"{tool_name(tool)} says too little"


def test_the_second_opinion_preset_insists_on_differing(shipped):
    """The one definition whose whole reason is to be a different model, and the
    one that could silently stop being one.

    Bind `alternate` to whatever the main agent runs and, without this, the
    delegate builds, answers, and the answer is worth nothing -- with a line in
    the run report as the only sign. `distinct: true` is what turns that into a
    refusal, so a preset that quietly lost the line would be back to the defect
    this shipped to fix.

    Asserted here rather than trusted, because it is one line in a file nobody
    reads twice. Its neighbours deliberately do *not* set it: `reviewer` runs on
    the deployment's own model on purpose, and `extractor` wants a cheap model
    rather than a different one.
    """
    specs = LocalSubagentRepository(shipped / "subagents").specs

    assert specs["second-opinion"].distinct is True
    assert specs["second-opinion"].wanted, "it must name what it may run instead"
    assert {name for name, s in specs.items() if s.distinct} == {"second-opinion"}


def test_no_preset_names_a_model(shipped):
    """A file inside the wheel cannot portably name a vendor's model id.

    `extractor` and `profiler` said `MiniMax-M2.5` and `second-opinion` said
    `gpt-5`. The catalogue is closed now, so any of those would refuse to start
    for a deployment whose `models.yaml` lacks the entry -- and before it was
    closed they were worse, reaching whatever endpoint was configured and
    failing as a 404 mid-run.

    Which model is cheap *here* is a deployment's answer, not a preset's: the
    same reason `KINGFISHER_MODEL_SUBAGENT` was deleted for being the wrong
    granularity. The cost-routing demonstration lives in the README instead.
    """
    specs = LocalSubagentRepository(shipped / "subagents").specs

    named = {
        name
        for name, s in specs.items()
        for candidate in s.wanted
        if candidate.model is not None
    }
    assert named == set()
    assert not [f for f in fields(next(iter(specs.values()))) if f.name == "provider"]


def test_the_reviewer_preset_consults_the_second_opinion(shipped):
    """The README describes this pairing, so a preset had better demonstrate it.

    It is also the shape the field exists for: `reviewer` runs out of road in a
    specific place -- two defensible readings and nothing in the file to choose
    between them -- which is where a *different model* beats more care from the
    same one.
    """
    specs = LocalSubagentRepository(shipped / "subagents").specs

    assert specs["reviewer"].subagents == ("second-opinion",)
    assert specs["second-opinion"].subagents is None  # a helper works alone


def test_the_shipped_catalogue_has_no_delegation_cycle(shipped):
    """Seeding a catalogue that refuses to load would be the worst kind of
    example: copied, broken on the first run, and the format blamed.

    It checked the one-level rule until delegation learned to nest. The rule
    that replaced it is the only thing left that a catalogue can violate here,
    so this follows it rather than being deleted."""
    from kingfisher.domain.subagent.rules import refuse_cycles

    refuse_cycles(LocalSubagentRepository(shipped / "subagents").specs)


@pytest.mark.parametrize(
    "granted",
    [("reviewer",), ("reviewer", "second-opinion")],
    ids=["helper withheld", "helper granted"],
)
def test_the_reviewer_preset_builds_with_and_without_its_helper(
    workspace_with_presets, session_dir, fake_model, granted
):
    """Both grants, because `reviewer` names a delegate of its own.

    Withheld is the path most callers take, and the reason the prompt says "if
    you have one": granting `second-opinion` is a choice, and declining it must
    not cost anyone the reviewer. Granted is the shape the pairing exists to
    demonstrate. A definition that only builds one way is a broken preset --
    copied, failing on first contact, with the format blamed.

    That the withheld build has no `task` tool and the granted one does is
    kingfisher's rule, tested there against its own fixtures. This is about
    these files.
    """
    build_agent(
        workspace_with_presets,
        session_dir=session_dir,
        model=fake_model,
        capabilities=Capabilities(subagents=granted),
    )


def test_the_readme_snippet_runs_and_uses_only_the_public_api(tmp_path, monkeypatch, capsys):
    """The README's Python block, executed rather than eyeballed.

    It is a promise about the front door now -- `from kingfisher import
    paths_from_env, seed` -- where it used to read
    `from kingfisher.infrastructure import seeding`, reaching past the public
    API into a module carrying no stability promise. Nothing checked it either
    way: the framework's own README has six tests holding it to the code and
    this one had none.

    Both halves are asserted. That it *runs* catches a rename; that it imports
    nothing deeper catches the reach coming back.
    """
    import re
    from pathlib import Path

    import kingfisher

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", readme, re.DOTALL)
    assert blocks, "the README stopped carrying a Python example"

    for block in blocks:
        # The example sits in a blockquote, so every line carries the marker.
        source = "\n".join(line.removeprefix(">").removeprefix(" ") for line in block.splitlines())

        assert "kingfisher.infrastructure" not in source
        assert "kingfisher.domain" not in source
        for name in re.findall(r"^from kingfisher import (.+)$", source, re.M):
            for imported in (part.strip() for part in name.split(",")):
                assert imported in kingfisher.__all__, imported

        monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
        exec(compile(source, "README.md", "exec"), {})  # noqa: S102 -- our own file

    # It seeds, which is the thing it claims to do.
    assert "seeded" in capsys.readouterr().out


def test_every_preset_agent_parses(shipped):
    """`agents/` was the one kind nothing here loaded, and two of the two
    definitions in it could not run."""
    specs = LocalAgentRepository(shipped / "agents").specs

    assert set(specs) == {"assistant", "surveyor"}
    for spec in specs.values():
        assert spec.description.strip()
        assert len(spec.system_prompt) > 200  # a real prompt, not a stub


def test_every_preset_names_tools_this_distribution_actually_offers(shipped):
    """The test that was missing, and the reason two broken definitions shipped.

    Every other check here reads a definition or loads a tool. None of them put
    the two together -- and the failure was exactly in the join: `surveyor`
    grants `csv_profile::csv_profile` and `analysis/profiler.yaml` grants
    `csv_profile::csv_columns`, both the documented long form for a tool no
    other file defines, and both refused as unknown by every run.

    `refuse_unknown` rather than a comparison of names, because it is what a
    build calls. A test that agreed with the loader about spelling and not with
    the checker is how this got here.
    """
    offering = Offering.of(LocalToolRepository(shipped / "tools").found)
    defined = {
        **LocalAgentRepository(shipped / "agents").specs,
        **LocalSubagentRepository(shipped / "subagents").specs,
    }

    assert defined, "an empty catalogue would pass every assertion below"
    for name, spec in defined.items():
        offering.refuse_unknown(ALL, spec.tools, subject=f"preset {name!r}")
        offering.refuse_moved(spec.tool_sources, subject=f"preset {name!r}")


# -- the compiled preset ----------------------------------------------------


def compiled(shipped, tools):
    """The shipped `first-look` graph, built the way a run would build it.

    Through `LocalSubagentRepository` rather than by importing
    `kingfisher.assets.subagents.first_look`, and the difference is not
    ceremony. `assets/subagents/` has no `__init__.py`, so that import resolves
    only as a namespace package -- `test_every_kingfisher_import_in_this_
    repository_names_a_module_that_exists` refuses it, correctly, and it is not
    how anything reaches an asset. The catalogue loads these by path, so a test
    that does anything else is exercising a route no deployment has.
    """
    spec = LocalSubagentRepository(shipped / "subagents").specs["first-look"]
    assert spec.build is not None
    return spec.build(FakeToolCallingModel(responses=[AIMessage(content="ok")]), tools)


def line_count(path: str) -> str:
    "Count the lines."
    return f"{path}: 2 line(s)"


def test_the_compiled_preset_builds_a_graph_that_surveys_first(shipped):
    """A shipped asset is judged by whether it runs, so this builds the graph.

    The claim the preset exists to make is structural rather than behavioural: a
    prompt can *ask* for a profiling step and a model may skip it; a graph has no
    edge that reaches the model without passing through the survey node. So this
    asserts the edge, not a transcript.
    """
    graph = compiled(shipped, [line_count])

    assert {"survey", "answer"} <= set(graph.get_graph().nodes)
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("__start__", "survey") in edges
    assert ("survey", "answer") in edges
    assert not any(source == "__start__" and target == "answer" for source, target in edges)


def test_the_compiled_preset_profiles_the_file_it_was_given(shipped, tmp_path):
    """The survey node itself: it finds the path in the request and runs what it
    was granted over it.

    "what is in rows.csv?" is the phrasing a person actually types, and the
    trailing `?` is what the first version of the path heuristic tripped over.
    """
    target = tmp_path / "rows.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    graph = compiled(shipped, [line_count])

    survey = graph.get_graph().nodes["survey"].data.func
    answer = survey({"messages": [HumanMessage(content=f"what is in {target}?")]})

    (message,) = answer["messages"]
    assert "line_count" in message.content
    assert "2 line(s)" in message.content


def test_the_compiled_preset_says_when_it_found_nothing_to_profile(shipped):
    """The heuristic fails loudly. A delegate whose profiling step silently did
    nothing looks identical to one that profiled a dull file, and the whole
    point of this preset is that the step happened.
    """
    graph = compiled(shipped, [line_count])

    survey = graph.get_graph().nodes["survey"].data.func
    answer = survey({"messages": [HumanMessage(content="what is in the file?")]})

    (message,) = answer["messages"]
    assert "nothing was profiled" in message.content


def test_the_compiled_presets_imports_stay_out_of_module_scope(shipped):
    """Measured rather than trusted: this module is imported whenever the
    subagent catalogue is read, `kingfisher list` included, and
    `from langchain.agents import create_agent` costs about 370 ms.

    Read as source rather than by timing, because a timing test would pass on a
    warm interpreter -- every other test here has already imported langchain.
    """
    import ast

    source = (shipped / "subagents" / "first_look.py").read_text(encoding="utf-8")
    module_level = {
        node.module.split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not module_level & {"langchain", "langchain_core", "langgraph", "deepagents"}
