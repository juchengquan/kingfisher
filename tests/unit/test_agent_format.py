"""The agent format: `/agents/<name>.yaml`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.agents.catalogue import LocalAgentRepository
from kingfisher.agents.reading import read
from kingfisher.agents.spec import AgentError
from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.subagents import reading
from kingfisher.subagents.spec import SubagentError

WHOLE = """name: surveyor
description: Reads and profiles data without changing anything.
builtin_tools: [read_file, ls, glob, grep]
tools: [csv_profile::csv_profile]
model: cheap-model
memory: false
system_prompt: |
  You survey files before anyone trusts them.
"""

#: The smallest legal definition, one field per entry so the test about a
#: missing field can drop exactly one and leave a document that still parses.
REQUIRED = {
    "name": "name: plain\n",
    "description": "description: An agent with nothing but the required fields.\n",
    "system_prompt": "system_prompt: |\n  You do the work this workspace asks for.\n",
}

MINIMAL = "".join(REQUIRED.values())


def _read(text: str, name: str = "surveyor.yaml"):
    return read(text, Path(name))


# -- what a definition says -------------------------------------------------


def test_a_whole_definition_reads_as_written():
    spec = _read(WHOLE)

    assert spec.name == "surveyor"
    assert spec.builtin_tools == ("read_file", "ls", "glob", "grep")
    assert spec.tools == ("csv_profile::csv_profile",)
    assert spec.memory is False
    assert spec.system_prompt.startswith("You survey files")


def test_three_fields_are_required_and_nothing_else_is():
    """What an agent has to say about itself: what it is called, what it is for, and
    what it is.
    """
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.system_prompt.startswith("You do the work")
    assert spec.wanted is None


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_a_missing_required_field_is_refused_by_name(missing):
    written = "".join(text for field, text in REQUIRED.items() if field != missing)

    with pytest.raises(AgentError, match=f"missing required field '{missing}'"):
        _read(written, "plain.yaml")


@pytest.mark.parametrize(
    ("field", "written"),
    [
        ("description", "name: plain\ndescription:\nsystem_prompt: |\n  Go.\n"),
        # A literal block with nothing indented under it: the document is valid
        # and the block is empty, which reads on screen as a prompt that is
        # there.
        ("system_prompt", "name: plain\ndescription: An agent.\nsystem_prompt: |\n"),
    ],
)
def test_a_present_but_empty_field_says_so_rather_than_missing(field, written):
    """Absent and blank are different mistakes, and "missing" sends somebody looking for
    a line they can see they wrote.
    """
    with pytest.raises(AgentError, match=f"'{field}' is present but empty"):
        _read(written, "plain.yaml")


# -- omission, which is where the two formats agree -------------------------


def test_leaving_the_tool_fields_out_grants_all_of_them():
    """The one place omission grants rather than withholds."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.builtin_tools == ALL
    assert spec.tools == ALL


def test_leaving_skills_and_delegates_out_grants_none():
    """The other half of that sentence."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.skills is None
    assert spec.subagents is None
    assert spec.middleware is None


def test_a_tool_may_say_where_it_lives_and_the_claim_is_kept():
    spec = _read(WHOLE)

    assert spec.tool_sources == {"csv_profile": "csv_profile"}


# -- where the two formats disagree, deliberately ---------------------------


def test_an_agent_may_name_every_subagent_and_a_subagent_may_not():
    """The one field that answers `["*"]` differently in the two folders, and the reason
    is in the files rather than in a table somebody has to remember.
    """
    assert _read(MINIMAL.rstrip() + '\nsubagents: ["*"]\n', "plain.yaml").subagents == ALL

    delegate = 'name: d\ndescription: A delegate.\nsubagents: ["*"]\nsystem_prompt: |\n  Go.\n'
    with pytest.raises(SubagentError, match="always a loop"):
        reading.read(delegate, Path("d.yaml"))


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("permissions: [x]", "replace"),
        ("interrupt_on: [x]", "surfaces an interrupt"),
        ("response_format: {}", "what a .run returns"),
    ],
)
def test_each_declined_field_says_why_it_is_declined(field, expected):
    with pytest.raises(AgentError, match=expected):
        _read(MINIMAL.rstrip() + f"\n{field}\n", "plain.yaml")


def test_an_unknown_field_is_refused_and_a_near_miss_is_named():
    """A key we ignore is a key the author believes took effect."""
    with pytest.raises(AgentError, match="did you mean 'tools'"):
        _read(MINIMAL.rstrip() + "\ntolls: []\n", "plain.yaml")


def test_memory_has_three_states_and_absent_is_not_off():
    """A switch narrows like every other axis, so only `False` can subtract."""
    assert _read(MINIMAL, "plain.yaml").memory is None
    assert _read(MINIMAL.rstrip() + "\nmemory: true\n", "plain.yaml").memory is True
    assert _read(MINIMAL.rstrip() + "\nmemory: false\n", "plain.yaml").memory is False


@pytest.mark.parametrize("written", ["'false'", '"no"', "0", "maybe"])
def test_a_flag_that_is_not_a_bool_is_refused(written):
    """`memory: "false"` is a non-empty string, and every non-empty string is true -- so
    the reading Python would take says the opposite of what the file says.
    """
    with pytest.raises(AgentError, match="write true or false"):
        _read(MINIMAL.rstrip() + f"\nmemory: {written}\n", "plain.yaml")


def test_yaml_spellings_of_true_are_accepted():
    """`yes` and `on` arrive here already a bool, so there is nothing to refuse and
    nothing to special-case.
    """
    for written in ("true", "True", "yes", "on"):
        assert _read(MINIMAL.rstrip() + f"\nmemory: {written}\n", "plain.yaml").memory is True


def test_metadata_is_carried_and_a_bag_with_no_shape_is_refused():
    assert _read(MINIMAL.rstrip() + "\nmetadata:\n  owner: data-eng\n", "plain.yaml").metadata == {
        "owner": "data-eng"
    }

    with pytest.raises(AgentError, match="must be a mapping"):
        _read(MINIMAL.rstrip() + "\nmetadata: gold\n", "plain.yaml")


def test_a_folded_prompt_is_refused_because_it_reflows():
    """`>` joins consecutive lines into one, so a numbered procedure reaches the model
    as a run-on line.
    """
    written = REQUIRED["name"] + REQUIRED["description"] + "system_prompt: >\n  One.\n  Two.\n"

    with pytest.raises(AgentError, match="reflows it"):
        _read(written, "plain.yaml")


def test_the_error_says_which_format_the_broken_file_is_in():
    """`AgentError` rather than `SubagentError`, and that is the whole reason
    `read` is its own function rather than a shared one taking a parser: it is
    what tells somebody which of two folders to open.
    """
    with pytest.raises(AgentError):
        _read("name: plain\n", "plain.yaml")


# -- the directory ----------------------------------------------------------


def _write(root: Path, where: str, name: str, description: str = "One.") -> None:
    """One legal definition at a path."""
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"name: {name}\ndescription: {description}\nsystem_prompt: |\n  Go.\n"
    path.write_text(body, encoding="utf-8")


def test_a_folder_is_organisation_and_the_name_field_is_the_identity(tmp_path):
    _write(tmp_path, "support/triage.yaml", "triage", "Sorts.")

    assert LocalAgentRepository(tmp_path).names == ("triage",)


def test_two_agents_of_one_name_are_refused_rather_than_disambiguated(tmp_path):
    """Where subagents keep both under a reference, this refuses."""
    _write(tmp_path, "a.yaml", "assistant")
    _write(tmp_path, "nested/b.yaml", "assistant", "Two.")

    with pytest.raises(AgentError, match="two agents are called 'assistant'"):
        _ = LocalAgentRepository(tmp_path).names


def test_a_yml_file_is_named_rather_than_silently_skipped(tmp_path):
    _write(tmp_path, "assistant.yml", "assistant")

    with pytest.raises(AgentError, match=r"rename it to assistant\.yaml"):
        _ = LocalAgentRepository(tmp_path).names


def test_a_directory_that_is_not_there_holds_no_agents(tmp_path):
    """Empty rather than an error, which is the same answer a derived catalogue gives
    before anyone seeds it.
    """
    assert LocalAgentRepository(tmp_path / "nope").names == ()


def test_an_agent_naming_several_models_is_refused():
    """Its own test because the two formats keep separate readers: the subagent side was
    the one measured, and a fix that reached only it would leave this one
    stringifying a list with the whole suite green.
    """
    with pytest.raises(AgentError, match=r"model names 2 things"):
        _read(MINIMAL.rstrip() + "\nmodel: [gpt-5, claude-4]\n", "plain.yaml")


def test_an_agent_naming_one_model_is_untouched():
    assert _read(MINIMAL.rstrip() + "\nmodel: gpt-5\n", "plain.yaml").wanted == "gpt-5"


# -- who reaches it, and who reaches what it holds --------------------------


def test_an_agent_may_say_who_reaches_it():
    spec = _read(MINIMAL.rstrip() + "\ngroups: [A, B]\n", "plain.yaml")

    assert spec.groups == ("A", "B")


def test_an_agent_that_says_nothing_is_reachable_by_everyone():
    """An absent optional field means no restriction, which is what it means everywhere
    else in this format -- and what makes adopting audiences incremental rather than
    all-or-nothing.
    """
    assert _read(MINIMAL, "plain.yaml").groups == ALL


@pytest.mark.parametrize(
    ("field_name", "written"),
    [
        ("tools", "tools:\n  - name: sql_query\n    groups: [A]\n"
            "  - name: http_fetch\n    groups: [A, B]\n"),
        ("skills", "skills:\n  - name: audit\n    groups: [A]\n"
            "  - name: review\n    groups: [A, B]\n"),
        ("subagents", "subagents:\n  - name: checker\n    groups: [A]\n"
            "  - name: reviewer\n    groups: [A, B]\n"),
    ],
)
def test_every_audienced_field_takes_a_mapping(field_name, written):
    """All three, because a rule that covered one would be the one nobody noticed was a
    third of a rule.
    """
    spec = _read(MINIMAL.rstrip() + f"\ngroups: [A, B]\n{written}", "plain.yaml")

    first, second = (getattr(spec, field_name))[0], (getattr(spec, field_name))[1]
    assert spec.audiences[field_name] == {first: ("A",), second: ("A", "B")}


@pytest.mark.parametrize(
    ("field_name", "written", "kept"),
    [
        (
            "tools",
            "tools:\n  - name: sql_query\n    groups: [A]\n"
                "  - name: http_fetch\n    groups: [A, B]\n",
            "http_fetch",
        ),
        ("skills", "skills:\n  - name: audit\n    groups: [A]\n"
            "  - name: review\n    groups: [A, B]\n", "review"),
        (
            "subagents",
            "subagents:\n  - name: checker\n    groups: [A]\n"
                "  - name: reviewer\n    groups: [A, B]\n",
            "reviewer",
        ),
    ],
)
def test_every_audienced_field_narrows_for_a_caller(field_name, written, kept):
    spec = _read(MINIMAL.rstrip() + f"\ngroups: [A, B]\n{written}", "plain.yaml")

    assert getattr(spec.declares(frozenset({"B"})), field_name) == (kept,)


def test_an_entry_with_no_audience_inherits_the_definitions():
    """A plain list under a policied definition means 'these, at my audience', which is
    what keeps every file written before audiences unchanged.
    """
    spec = _read(MINIMAL.rstrip() + "\ngroups: [A]\ntools: [sql_query]\n", "plain.yaml")

    assert spec.declares(frozenset({"A"})).tools == ("sql_query",)
    assert spec.declares(frozenset({"B"})).tools == ()


def test_declaring_with_no_caller_is_what_it_always_was():
    """A deployment with no vocabulary, or an UNSCOPED call."""
    spec = _read(
        MINIMAL.rstrip() + "\ngroups: [A]\ntools:\n  - name: sql_query\n    groups: [A]\n",
        "plain.yaml",
    )

    assert spec.declares(None).tools == ("sql_query",)


def test_builtin_tools_takes_no_audience():
    """deepagents registers those itself, so they can be filtered but never left out of
    a graph -- an audience here would promise a boundary it cannot keep.
    """
    with pytest.raises(AgentError, match="this field takes a list"):
        _read(MINIMAL.rstrip() + "\nbuiltin_tools:\n  execute:\n    groups: [A]\n", "plain.yaml")


def test_only_the_audienced_fields_take_a_mapping():
    """Pins the prose in `fields.selection` against `AUDIENCED`, which it cannot import
    -- `domain.access` imports `fields`, so naming it there is a cycle.
    """
    from kingfisher.domain.access import AUDIENCED

    with pytest.raises(AgentError) as raised:
        _read(MINIMAL.rstrip() + "\nbuiltin_tools:\n  execute:\n    groups: [A]\n", "plain.yaml")

    for field_name in AUDIENCED:
        assert field_name in str(raised.value)


def test_an_entry_audience_outside_the_definitions_own_is_recorded_not_judged():
    """Whether one audience can ever reach another is a question about what the names
    *mean*, and this format has no vocabulary to answer it with: `B` may contain `C`,
    or `A` may require it.
    """
    spec = _read(
        MINIMAL.rstrip() + "\ngroups: [A, B]\ntools:\n  - name: sql_query\n    groups: [C]\n",
        "plain.yaml",
    )

    assert spec.groups == ("A", "B")
    assert spec.audiences["tools"]["sql_query"] == ("C",)


def test_a_conjunction_is_read_as_one_entry_of_the_list():
    """`all_of` in a definition, which is the inline half of the same word the
    vocabulary uses for a named one.
    """
    spec = _read(
        MINIMAL.rstrip() + "\ngroups: [admin, {all_of: [finance, senior]}]\n",
        "plain.yaml",
    )

    assert spec.groups == ("admin", frozenset({"finance", "senior"}))


def test_only_the_restricted_entries_need_an_audience():
    """The ergonomics of the long form, and the reason an entry may stay short."""
    spec = _read(
        MINIMAL.rstrip()
        + "\ngroups: [A, B]\ntools:\n  - name: sql_query\n    groups: [A]\n"
        + "  - http_fetch\n  - line_count\n",
        "plain.yaml",
    )

    assert spec.tools == ("sql_query", "http_fetch", "line_count")
    assert spec.audiences["tools"] == {"sql_query": ("A",)}
    assert spec.declares(frozenset({"A"})).tools == ("sql_query", "http_fetch", "line_count")
    assert spec.declares(frozenset({"B"})).tools == ("http_fetch", "line_count")


def test_long_entries_that_restrict_nothing_mean_what_the_list_means():
    """The two spellings have to agree about an unrestricted name, or the mapping form
    would quietly change what a definition holds.
    """
    written = "\ngroups: [A]\ntools:\n  - name: sql_query\n  - name: http_fetch\n"
    as_list = "\ngroups: [A]\ntools: [sql_query, http_fetch]\n"

    mapped = _read(MINIMAL.rstrip() + written, "plain.yaml")
    listed = _read(MINIMAL.rstrip() + as_list, "plain.yaml")

    assert mapped.declares(frozenset({"A"})).tools == listed.declares(frozenset({"A"})).tools
    assert mapped.declares(frozenset({"B"})).tools == listed.declares(frozenset({"B"})).tools


# -- the starter printed when a workspace has no agent at all ----------------


def test_the_starter_agent_the_refusal_prints_actually_loads(cfg):
    """The whole risk of printing a file, and the reason `groups.yaml.example` was
    deleted.
    """
    import textwrap

    import yaml

    from kingfisher.agents.spec import parse
    from kingfisher.infrastructure.workspace.seeding import STARTER_AGENT

    block = STARTER_AGENT.split("A minimal one:\n\n", 1)[1].split("\n\nOmitting", 1)[0]
    document = yaml.safe_load(textwrap.dedent(block))

    spec = parse(document, Path("agents/assistant.yaml"))

    assert spec.name == "assistant"
    assert spec.system_prompt.strip()


def test_a_workspace_with_no_agents_is_told_how_to_write_one(cfg):
    """`--from DIR` needs a DIR, and an installed kingfisher has none -- the hint is
    correct and terminal.
    """
    from kingfisher import Kingfisher

    kf = Kingfisher(cfg)

    with pytest.raises(CapabilityError) as refused:
        kf.agent_named("assistant")

    said = str(refused.value)
    assert "offers none" in said
    assert "name: assistant" in said, "the starter is not in the refusal"
    assert "system_prompt" in said


def test_a_workspace_that_has_agents_is_not_lectured(cfg):
    """The starter is for an empty workspace."""
    from kingfisher import Kingfisher
    from tests.conftest import an_agent

    an_agent(cfg, "only")
    kf = Kingfisher(cfg)

    with pytest.raises(CapabilityError) as refused:
        kf.agent_named("missing")

    said = str(refused.value)
    assert "only" in said
    assert "system_prompt" not in said
