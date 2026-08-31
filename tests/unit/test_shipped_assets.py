"""The definitions this distribution ships have to work.

A definition that does not parse is worse than none: it is copied, it fails,
and the format gets blamed. These run against kingfisher's real loaders and
reach the files the way an installed pack is reached — through
`importlib.resources`, not by a path relative to this file.

They live here rather than in kingfisher because they describe *content*. The
framework's own tests are about seeding, discovery and the formats; whether
`reviewer` reaches for a helper is this package's business, and a preset added
here should not turn a test red over there.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher.domain import agent as agent_format
from kingfisher.domain import skill
from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.domain.subagent import reading as subagent_format
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository
from kingfisher.infrastructure.catalogue.documents import skill_name
from kingfisher.infrastructure.catalogue.importing import load
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository, tool_name
from kingfisher.infrastructure.harness.agent import build_agent, declared_middleware
from tests.conftest import FakeToolCallingModel, repository_root


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
        "profiler",
        "redactor",
        "show-your-work",
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




def test_no_preset_names_a_model(shipped):
    """A file inside the wheel cannot portably name a vendor's model id.

    `extractor` and `profiler` said `MiniMax-M2.5` once, and a delegate that
    has since gone said `gpt-5`. The catalogue is closed now, so any of those
    would refuse to start
    for a deployment whose `models.yaml` lacks the entry -- and before it was
    closed they were worse, reaching whatever endpoint was configured and
    failing as a 404 mid-run.

    Which model is cheap *here* is a deployment's answer, not a preset's: the
    same reason `KINGFISHER_MODEL_SUBAGENT` was deleted for being the wrong
    granularity. The cost-routing demonstration lives in the README instead.
    """
    specs = LocalSubagentRepository(shipped / "subagents").specs

    assert {name for name, s in specs.items() if s.wanted is not None} == set()
    assert not [f for f in fields(next(iter(specs.values()))) if f.name == "provider"]




def test_the_shipped_catalogue_has_no_delegation_cycle(shipped):
    """Seeding a catalogue that refuses to load would be the worst kind of
    example: copied, broken on the first run, and the format blamed.

    It checked the one-level rule until delegation learned to nest. The rule
    that replaced it is the only thing left that a catalogue can violate here,
    so this follows it rather than being deleted."""
    from kingfisher.domain.subagent.rules import refuse_cycles

    refuse_cycles(LocalSubagentRepository(shipped / "subagents").specs)




def test_the_readme_snippet_runs_and_uses_only_the_public_api(
    tmp_path, monkeypatch, capsys, shipped
):
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

    import kingfisher

    readme = (repository_root() / "README.md").read_text(encoding="utf-8")
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
        # The snippet resolves its source the way any caller does, so the
        # variable it reads has to be set -- which is itself part of what the
        # README now claims.
        monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
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


def compiled(shipped, tools, responses):
    """The shipped `show-your-work` graph, built the way a run would build it.

    Through `LocalSubagentRepository` rather than by importing
    `kingfisher.assets.subagents.show_your_work`, and the difference is not
    ceremony. `assets/subagents/` has no `__init__.py`, so that import resolves
    only as a namespace package -- `test_every_kingfisher_import_in_this_
    repository_names_a_module_that_exists` refuses it, correctly, and it is not
    how anything reaches an asset. The catalogue loads these by path, so a test
    that does anything else exercises a route no deployment has.
    """
    spec = LocalSubagentRepository(shipped / "subagents").specs["show-your-work"]
    assert spec.build is not None
    return spec.build(FakeToolCallingModel(responses=responses), tools)


def preset_module(shipped):
    """The preset as a module, loaded the way the catalogue loads it.

    `from kingfisher.assets.subagents import show_your_work` resolves only as
    a namespace package -- `assets/subagents/` has no `__init__.py` -- and the
    dangling-import rule refuses it. It caught this test doing exactly what
    the preset's own docstring warns against. `importing.load` is what reads
    a definition module for real.
    """
    return load(shipped / "subagents" / "show_your_work.py", declares="SUBAGENTS")


def line_count(path: str) -> str:
    "Count the lines."
    return f"{path}: 2 line(s)"


def calling(name, args, call_id="1"):
    """One model turn that calls a tool, then one that answers."""
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}]),
        AIMessage(content="two lines"),
    ]


def test_the_compiled_preset_records_after_answering(shipped):
    """The claim the preset exists to make is structural: there is no edge that
    reaches the end without passing through the record node. So this asserts the
    edge, not a transcript.
    """
    graph = compiled(shipped, [line_count], [AIMessage(content="done")])

    assert {"answer", "record"} <= set(graph.get_graph().nodes)
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("__start__", "answer") in edges
    assert ("answer", "record") in edges


def test_the_record_names_the_tool_and_its_arguments(shipped):
    """A model asked which tools it used gives a claim. This is computed from
    the tool calls, so it cannot be talked out of what happened.
    """
    graph = compiled(shipped, [line_count], calling("line_count", {"path": "/data/rows.csv"}))

    answer = graph.invoke({"messages": [HumanMessage(content="how long is it?")]})

    final = answer["messages"][-1].content
    assert "line_count" in final
    assert "/data/rows.csv" in final


def test_the_answer_survives_the_record(shipped):
    """deepagents returns a delegate's result by walking back to the last
    `AIMessage` with non-empty text. A record appended as its own message would
    not accompany the answer -- it would replace it, and the caller would get the
    footer and lose the reply.
    """
    graph = compiled(shipped, [line_count], calling("line_count", {"path": "x"}))

    answer = graph.invoke({"messages": [HumanMessage(content="how long is it?")]})

    final = answer["messages"][-1]
    assert isinstance(final, AIMessage)
    assert "two lines" in final.content  # the answer
    assert "line_count" in final.content  # and the record, in one message


def test_a_failed_call_is_recorded_as_failed(shipped):
    """The pair a model is least reliable about: "I verified the totals" after
    the verification tool raised. Read from `ToolMessage.status`, which is a
    field rather than a string to sniff.
    """
    messages = [
        AIMessage(content="", tool_calls=[{"name": "checker", "args": {}, "id": "7"}]),
        ToolMessage(content="boom", tool_call_id="7", status="error"),
        AIMessage(content="I verified the totals."),
    ]

    assert "failed" in preset_module(shipped)._record(messages)


def test_an_answer_that_used_nothing_says_so(shipped):
    """The most useful line this delegate ever prints. An answer produced
    without touching the files it is about is exactly what an auditor looks for,
    and an empty footer reads as though the question was never asked.
    """
    graph = compiled(shipped, [line_count], [AIMessage(content="I already knew that.")])

    answer = graph.invoke({"messages": [HumanMessage(content="how long is it?")]})

    assert "used no tools" in answer["messages"][-1].content


def test_long_arguments_are_cut_rather_than_dropped(shipped):
    """A display limit, not a judgement: it decides how much of a known value to
    show, never what a value means. That is the difference from the path
    heuristic this preset replaced.
    """
    module = preset_module(shipped)

    written = module._arguments({"query": "x" * 500})

    assert len(written) <= module.ARGUMENT_WIDTH
    assert written.endswith("…")


def test_the_compiled_presets_imports_stay_out_of_module_scope(shipped):
    """Measured rather than trusted: this module is imported whenever the
    subagent catalogue is read, `kingfisher list` included, and
    `from langchain.agents import create_agent` costs about 370 ms.

    Read as source rather than by timing, because a timing test would pass on a
    warm interpreter -- every other test here has already imported langchain.
    """
    import ast

    source = (shipped / "subagents" / "show_your_work.py").read_text(encoding="utf-8")
    module_level = {
        node.module.split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not module_level & {"langchain", "langchain_core", "langgraph", "deepagents"}


def test_every_shipped_tool_taking_a_path_says_it_is_a_session_path(shipped):
    """The convention, reversed once the mismatch it documented was removed.

    It used to require the opposite sentence -- `path` is a *host* path, not one
    of the agent's virtual ones. That was true, and was the honest thing to write
    while a tool had no way to be told where a session was: a workspace tool is a
    plain function, and deepagents hands its backend to nobody.

    `WorkspaceToolPaths` bridges that, so a tool now receives a real path
    resolved from the virtual one the agent wrote. The docstring is the
    description the model reads, so a tool still saying "host path" would be
    teaching the exact hunt this closed -- find the layout, build an absolute
    path, reach any session with it.
    """
    import ast

    missing = []
    for module in sorted(shipped.rglob("*.py")):
        if "__pycache__" in module.parts:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or "path" not in {a.arg for a in node.args.args}:
                continue
            doc = ast.get_docstring(node) or ""
            if "virtual path" not in doc:
                missing.append(f"{module.relative_to(shipped)}:{node.name}")

    assert not missing, (
        f"{missing} take a `path` and do not say it is the same virtual path the "
        "file tools take. The model reads the docstring, so one that says otherwise "
        "teaches it to go looking for a host path"
    )


# -- the example nothing seeds ---------------------------------------------


def _call_cap(shipped):
    """`CallCap`, loaded the way a deployment would import it."""
    return _call_cap_module(shipped).CallCap


def _call_cap_module(shipped):
    """The whole example module, for the tests that want more than one name."""
    return load(shipped / "middleware" / "call_cap.py", declares="CallCap")


def _documented_registry(shipped):
    """The wiring block the examples tell you to paste, pasted.

    Both example modules at once, because the block is one block: a deployment
    copying it registers three names over three classes, and a test that built
    half of it would not be checking the thing the comment promises.
    """
    cap = _call_cap_module(shipped)
    note = load(shipped / "middleware" / "tool_note.py", declares="ToolNote")
    return {
        "call-cap-strict": cap.CallCap,
        "call-cap-generous": cap.CallCapGenerous,
        "tool-note": note.ToolNote,
    }


def test_the_middleware_example_is_not_a_definition_kind(shipped):
    """It sits under `examples/` and `seed` does not copy it, which is the one
    surprising thing about this folder and therefore the thing to pin.

    `DEFINITION_KINDS` is the fields of `Definitions`, and `seed` walks exactly
    those. Middleware is not among them by design: a middleware read out of the
    workspace would be code the agent can edit, wrapped around the agent that
    edited it.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    assert (shipped / "middleware").is_dir()
    assert "middleware" not in DEFINITION_KINDS


def test_no_shipped_definition_names_a_middleware(shipped):
    """The curriculum has to keep running after a bare `kingfisher seed`.

    A definition naming `call-cap-strict` is refused when the agent is built --
    `names unregistered middleware` -- on every deployment that has not written
    the factory. Putting that line in a shipped file would break the first run
    of a fresh checkout to demonstrate a feature, which is the wrong trade.

    A star is not that, and this refused one anyway. The assertion was
    `"middleware" not in document`, which is broader than the paragraph above
    and broader for no reason the paragraph gives -- a rule whose test says more
    than its argument does, which is the kind that outlives being right.

    `["*"]` resolves against whatever the deployment registered, and on a fresh
    checkout that is nothing: `approved_middleware` answers `()` and raises
    nothing, which `test_a_wildcard_can_resolve_to_nothing_and_says_nothing`
    pins from the domain side. It degrades where a name refuses, so it is the
    one form of this field a shipped file may carry.
    """
    for kind in ("agents", "subagents"):
        for path in (shipped / kind).rglob("*.yaml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            written = document.get("middleware") or []
            named = [entry for entry in written if entry != "*"]
            assert not named, (
                f"{path.name} names {named}, which is refused on any deployment that "
                'did not register it; `["*"]` is the form that resolves to nothing instead'
            )


def test_the_shipped_star_costs_nothing_on_a_deployment_with_no_registry(shipped):
    """The property the rule above now rests on, driven rather than argued.

    `assistant` carries `middleware: ["*"]`, so this is the exact path a first
    run takes on a checkout that has registered nothing. What it has to do is
    produce no middleware *quietly* -- not raise, which is what a name would do,
    and not report a shortfall either, because a caller cannot register a
    middleware and telling it what it could not have asked for is noise.

    Read off the shipped file rather than a spec built here, because the thing
    that could regress is the file: delete the star and this still passes if it
    asserts on a spec of its own making.
    """
    from kingfisher.infrastructure.harness.agent import declared_middleware

    spec = LocalAgentRepository(shipped / "agents").specs["assistant"]

    assert spec.middleware == ALL, "the file this rests on stopped carrying the star"
    assert declared_middleware(spec, {}, ALL, kind="agent") == []


def test_the_middleware_example_caps_a_turn(shipped, cfg, session_dir):
    """It is code, so "does it parse" means "does it run" -- the same bar
    `test_every_preset_tool_loads` sets for `tools/`.

    Driven rather than inspected: a scripted model asks for three tool calls
    against a cap of two, and the third has to come back refused while the turn
    keeps going. A cap that ended the turn would be a worse thing wearing the
    same name.
    """
    cap = _call_cap(shipped)
    spec = LocalAgentRepository(shipped / "agents").specs["assistant"]
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"c{i}"}],
        )
        for i in range(3)
    ] + [AIMessage(content="done")]

    graph = build_agent(
        cfg,
        agent=replace(spec, middleware=("call-cap-strict",), subagents=None, skills=None),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        middleware_registry={"call-cap-strict": lambda: cap(2)},
    )
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "cap"}, "recursion_limit": 30},
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "limit of 2 tool calls is used up" in transcript
    assert out["messages"][-1].content == "done", "the cap ended the turn instead of the call"


def test_the_note_example_reaches_a_real_tool_result(shipped, cfg, session_dir):
    """It is code, so "does it parse" means "does it run" -- the same bar
    `test_the_middleware_example_caps_a_turn` sets for the cap two tests up.

    Driven by a scripted model rather than by calling `_annotate` directly,
    because what could be wrong is the wiring: a middleware that annotates
    correctly and is never reached by the graph passes every unit assertion and
    does nothing at all.
    """
    note = load(shipped / "middleware" / "tool_note.py", declares="ToolNote")
    spec = LocalAgentRepository(shipped / "agents").specs["assistant"]
    responses = [
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "c1"}]),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        cfg,
        agent=replace(
            spec,
            middleware=("tool-note",),
            middleware_settings={"tool-note": {"text": "Mind the source."}},
            subagents=None,
            skills=None,
        ),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        middleware_registry={"tool-note": note.ToolNote},
    )
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "note"}, "recursion_limit": 30},
    )

    results = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert results, "the scripted call never produced a tool result"
    assert all(m.content.endswith("Mind the source.") for m in results), (
        "the definition's wording did not reach the result the model reads"
    )


def test_the_middleware_example_refuses_a_cap_that_refuses_everything(shipped):
    """`CallCap(0)` would refuse the first call and every one after it, which
    is not a narrower cap but a broken agent. Omitting the name is how you say
    'none', on this axis as on every other."""
    with pytest.raises(ValueError, match="omit the middleware instead"):
        _call_cap(shipped)(0)


def _example_definitions(shipped):
    """The agent and delegate beside `call_cap.py`, read by the formats that own them.

    Read here rather than through `LocalAgentRepository`, which scans a whole
    directory: these two share a folder with each other and with the module,
    so pointing an agent repository at it would try to read a subagent as an
    agent. Nothing loads this folder at run time, which is the point of it.
    """
    root = shipped / "middleware"
    agent_path = root / "researcher.yaml"
    delegate_path = root / "sweeper.yaml"
    return (
        agent_format.parse(
            yaml.safe_load(agent_path.read_text(encoding="utf-8")), agent_path
        ),
        subagent_format.parse(
            yaml.safe_load(delegate_path.read_text(encoding="utf-8")), delegate_path
        ),
    )


def test_the_middleware_examples_are_definitions_the_formats_accept(shipped):
    """Unseeded is not unchecked.

    Nothing copies this folder and nothing loads it, so these two files have no
    run to fail on -- which is exactly the condition documentation rots under.
    `call_cap.py` is driven by a scripted model two tests up for the same
    reason: an example that only ever gets read is an example nobody notices
    has gone stale.

    So they are parsed by the real formats, and the names they exist to
    demonstrate are asserted rather than assumed. A rename of either registry
    entry in `call_cap.py`'s wiring block turns this red.
    """
    agent, delegate = _example_definitions(shipped)

    assert agent.name == "researcher"
    assert agent.middleware == ("call-cap-strict", "tool-note")
    assert delegate.name == "sweeper"
    assert delegate.middleware == ("call-cap-generous", "tool-note")
    assert agent.subagents == ("sweeper",), "the agent half has to name the delegate half"

    # Both spellings in one list, which is what these two files are now for.
    # The cap is bare because `CallCap` opens nothing; the note is written long
    # because `ToolNote` opens `text`.
    assert dict(agent.middleware_settings) == {
        "tool-note": {"text": "Cite the path and line for anything you assert."}
    }
    assert dict(delegate.middleware_settings) == {
        "tool-note": {"text": "Return the path and line, not the file."}
    }
    assert (
        agent.middleware_settings["tool-note"] != delegate.middleware_settings["tool-note"]
    ), "one registry entry configured two ways is the thing this pair demonstrates"


def test_the_middleware_examples_are_why_they_are_not_seeded(shipped):
    """The reason they sit here rather than under `agents/` and `subagents/`.

    `test_no_shipped_definition_names_a_middleware` states the rule; this drives
    the harm behind it. Against a deployment that registered nothing -- which is
    every fresh checkout -- both are refused when the definition is built, and
    refused by name rather than quietly built without the cap they specified.

    Which is also why the rule cannot simply be relaxed for these two. It is not
    that they are unfinished; it is that a definition naming middleware is only
    loadable somewhere the factory exists, and a seeded workspace is not that
    place until its deployment says so.
    """
    agent, delegate = _example_definitions(shipped)

    for spec, kind in ((agent, "agent"), (delegate, "subagent")):
        with pytest.raises(CapabilityError, match="names unregistered middleware"):
            declared_middleware(spec, {}, ALL, kind=kind)


def test_the_middleware_examples_build_against_the_registry_they_document(shipped):
    """The wiring block in `call_cap.py` is copy-pasteable, checked by pasting it.

    The registry below is that block's two entries over the one class, which is
    the lesson those two names carry: a cap a definition could set is not a cap,
    so the variants are registered and a definition chooses among them.

    The delegate's middleware is built from the same registry and is a separate
    object, which is the other half of what this pair shows. A subagent inherits
    none of its parent's middleware, so `researcher` running out of calls says
    nothing about how many `sweeper` has left.
    """
    registry = _documented_registry(shipped)
    agent, delegate = _example_definitions(shipped)

    built = declared_middleware(agent, registry, ALL, kind="agent")
    delegated = declared_middleware(delegate, registry, ALL, kind="subagent")

    assert [type(m).__name__ for m in built] == ["CallCap", "ToolNote"]
    assert [type(m).__name__ for m in delegated] == ["CallCapGenerous", "ToolNote"]
    assert built[0] is not delegated[0], "one instance for both would share a budget"
    # The ceilings the two classes document, read off the objects rather than
    # off `defaults`: the point of registering a class is that the build path
    # applies its defaults, so asserting the attribute would assert nothing.
    assert built[0]._limit == 20
    assert delegated[0]._limit == 100


def test_the_note_example_is_one_class_configured_two_ways(shipped):
    """The half of the axis `call_cap.py` cannot show, driven end to end.

    One registry entry, named by both definitions, built into two objects
    carrying the sentences their own files wrote. That is what a setting buys
    and what two-names-over-two-classes cannot express -- and it is the reason
    `ToolNote` opens `text` where `CallCap` opens nothing.

    Read off the built objects rather than the specs, because the specs were
    already asserted two tests up. What could still be wrong here is the merge.
    """
    registry = _documented_registry(shipped)
    agent, delegate = _example_definitions(shipped)

    note = declared_middleware(agent, registry, ALL, kind="agent")[1]
    delegated = declared_middleware(delegate, registry, ALL, kind="subagent")[1]

    assert note._text == "Cite the path and line for anything you assert."
    assert delegated._text == "Return the path and line, not the file."
    assert note._text != delegated._text
    # The key neither file wrote, which both take from the deployment. The
    # merge is per key rather than all-or-nothing, and this is where that shows.
    assert note._max_length == delegated._max_length == 200


def test_the_note_example_refuses_the_key_it_did_not_open(shipped):
    """`max_length` is a ceiling on what a definition may inject into every tool
    result, so it is shut for the same reason `limit` is.

    Driven against the real class rather than a fixture, because the thing that
    could regress is `ToolNote.yaml_settable` -- someone adding `max_length` to
    it to make a long note fit would pass every other test in this file.
    """
    from dataclasses import replace

    registry = _documented_registry(shipped)
    agent, _ = _example_definitions(shipped)
    greedy = replace(
        agent,
        middleware_settings={"tool-note": {"text": "hi", "max_length": 100_000}},
    )

    with pytest.raises(CapabilityError, match="does not accept"):
        declared_middleware(greedy, registry, ALL, kind="agent")


def test_the_note_example_falls_back_to_the_deployments_wording(shipped):
    """`middleware: [tool-note]` with no settings is a working line, not a no-op.

    `defaults` holds a real sentence rather than an empty string, so a
    definition that names the middleware and says no more still gets a note.
    Worth pinning: an empty default would make the bare form silently do
    nothing, which is the shape of a feature nobody notices is broken.
    """
    from dataclasses import replace

    registry = _documented_registry(shipped)
    agent, _ = _example_definitions(shipped)
    quiet = replace(agent, middleware=("tool-note",), middleware_settings={})

    (built,) = declared_middleware(quiet, registry, ALL, kind="agent")

    assert built._text == registry["tool-note"].defaults["text"]
    assert built._text, "the bare form has to do something"


def test_the_generous_variant_is_a_subclass_rather_than_a_setting(shipped):
    """The shape the whole argument rests on, pinned where it can rot.

    `CallCap.yaml_settable` is empty, so no definition can write `limit:` --
    and the way a deployment offers a looser ceiling is a second class, not a
    second key. If someone later adds `limit` to `yaml_settable` to save a
    class, this fails and says why that trade is the one the module argues
    against.
    """
    module = _call_cap_module(shipped)

    assert module.CallCap.yaml_settable == frozenset(), (
        "a cap a definition can set is not a cap; `limit` stays out of yaml_settable"
    )
    assert issubclass(module.CallCapGenerous, module.CallCap)
    assert module.CallCap.defaults["limit"] == 20
    assert module.CallCapGenerous.defaults["limit"] == 100
