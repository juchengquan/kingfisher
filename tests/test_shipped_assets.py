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

from kingfisher.domain import skill
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue.documents import skill_name
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository, tool_name
from kingfisher.infrastructure.harness.agent import build_agent


def test_every_preset_subagent_parses(shipped):
    specs = LocalSubagentRepository(shipped / "subagents").specs

    # `profiler` ships in `subagents/analysis/`, and is named `profiler` all the
    # same: a subagent is named by its `name:` field, so a folder cannot reach
    # it. Its presence in this flat set is the assertion that nesting works.
    assert set(specs) == {"reviewer", "extractor", "second-opinion", "profiler"}
    for spec in specs.values():
        assert spec.description.strip()
        assert len(spec.system_prompt) > 200  # a real prompt, not a stub


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
