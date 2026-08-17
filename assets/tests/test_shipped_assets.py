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
from kingfisher.infrastructure.agent import build_agent
from kingfisher.infrastructure.definitions import skill_name
from kingfisher.infrastructure.skill_store import LocalSkillRepository
from kingfisher.infrastructure.subagent_store import LocalSubagentRepository
from kingfisher.infrastructure.tool_store import LocalToolRepository, tool_name


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
    }


def test_every_preset_tool_describes_itself_to_the_model(shipped):
    """The docstring is what the model reads when deciding whether to call it.
    An example without a real one teaches the wrong shape."""
    for tool in LocalToolRepository(shipped / "tools").tools:
        assert len(tool.description.strip()) > 60  # a trigger, not a title


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

    assert {name for name, s in specs.items() if s.model} == set()
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


def test_the_shipped_catalogue_obeys_the_one_level_rule(shipped):
    """Seeding a catalogue that refuses to load would be the worst kind of
    preset: copied, broken on the first run, and the format blamed."""
    from kingfisher.domain.subagent import refuse_helpers_with_helpers

    refuse_helpers_with_helpers(LocalSubagentRepository(shipped / "subagents").specs)


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
