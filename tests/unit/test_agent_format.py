"""The agent format: `/agents/<name>.yaml`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.core.access.capabilities import ALL, CapabilityError
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.definitions.subagents.spec import SubagentError
from tests.conftest import a_subagent

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
    return read(DefinitionText(text, Path(name)))


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


def test_a_missing_required_field_is_refused_by_name():
    assert REQUIRED, "no required fields -- this walks nothing and passes"
    for missing in sorted(REQUIRED):
        written = "".join(text for field, text in REQUIRED.items() if field != missing)

        with pytest.raises(AgentError, match=f"missing required field '{missing}'"):
            _read(written, "plain.yaml")


def test_a_present_but_empty_field_says_so_rather_than_missing():
    """Absent and blank are different mistakes, and "missing" sends somebody looking for
    a line they can see they wrote.
    """
    for field, written in [
            ("description", "name: plain\ndescription:\nsystem_prompt: |\n  Go.\n"),
            # A literal block with nothing indented under it: the document is valid
            # and the block is empty, which reads on screen as a prompt that is
            # there.
            ("system_prompt", "name: plain\ndescription: An agent.\nsystem_prompt: |\n"),
        ]:
        with pytest.raises(AgentError, match=f"'{field}' is present but empty"):
            _read(written, "plain.yaml")


# -- omission, which is where the two formats agree -------------------------


def test_leaving_the_tool_fields_out_grants_none_of_them():
    """Read as all, a forgotten line hands the agent the shell and every tool the
    workspace gains later, and nothing on its first turn shows it.
    """
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.builtin_tools is None
    assert spec.tools is None


def test_the_star_is_how_an_agent_asks_for_every_tool():
    """The control: a reader granting nothing whatever was written passes the test above."""
    spec = _read(MINIMAL.rstrip() + '\nbuiltin_tools: ["*"]\ntools: ["*"]\n', "plain.yaml")

    assert spec.builtin_tools == ALL
    assert spec.tools == ALL


def test_leaving_skills_and_subagents_out_grants_none():
    """The same rule on the other grant fields."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.skills is None
    assert spec.subagents is None
    assert spec.middlewares is None


def test_a_tool_may_say_where_it_lives_and_the_claim_is_kept():
    spec = _read(WHOLE)

    assert spec.tool_sources == {"csv_profile": "csv_profile"}


@pytest.mark.parametrize("subagents", ["[sweeper]", '["*"]'])
def test_naming_subagents_with_built_ins_that_leave_out_task_is_refused(subagents):
    """The shipped `researcher` named `sweeper` and `analyst` named `auditor`, each
    listing built-ins without `task`, so neither could reach a subagent at all.
    """
    body = MINIMAL + f"builtin_tools: [read_file, grep]\nsubagents: {subagents}\n"

    with pytest.raises(AgentError, match="Add task to builtin_tools"):
        _read(body, "plain.yaml")

    assert _read(body.replace("grep]", "grep, task]"), "plain.yaml").subagents, "the control"


# -- where the two formats disagree, deliberately ---------------------------


def test_an_agent_may_list_the_code_sandbox():
    """The sandbox is wired into the agent's own graph, so the refusal a subagent meets
    for `eval` would leave the one definition that holds it unable to name it.
    """
    spec = _read(MINIMAL + "builtin_tools: [read_file, eval]\n", "plain.yaml")

    assert spec.builtin_tools == ("read_file", "eval")


def test_an_agent_may_name_every_subagent_and_a_subagent_may_not():
    """One of the two fields that answer `["*"]` differently in the two folders, and the
    reason is in the files rather than in a table somebody has to remember.
    """
    every = MINIMAL.rstrip() + '\nbuiltin_tools: ["*"]\nsubagents: ["*"]\n'
    assert _read(every, "plain.yaml").subagents == ALL

    subagent = 'name: d\ndescription: A subagent.\nsubagents: ["*"]\nsystem_prompt: |\n  Go.\n'
    with pytest.raises(SubagentError, match="always a loop"):
        a_subagent(subagent, "d.yaml")


def test_each_declined_field_says_why_it_is_declined():
    for field, expected in [
            ("permissions: [x]", "replace"),
            ("response_format: {}", "what a .run returns"),
        ]:
        with pytest.raises(AgentError, match=expected):
            _read(MINIMAL.rstrip() + f"\n{field}\n", "plain.yaml")


def test_gates_are_read_as_names():
    """`interrupt_on` was declined here, with the reason that nothing surfaced a pause
    to the caller. Something does now, so the entry went rather than being reworded --
    and this is what would catch it coming back as a refusal nobody meant to keep.
    """
    spec = _read(MINIMAL.rstrip() + "\ninterrupt_on: [execute, write_file]\n", "plain.yaml")

    assert spec.interrupt_on == ("execute", "write_file")


def test_a_gate_list_refuses_the_star():
    """Every other list in this format reads `*` as "no limit"; here it would mean the
    tightest setting there is, and one spelling cannot carry both senses in one file.
    """
    with pytest.raises(AgentError, match="the star means 'no limit'"):
        _read(MINIMAL.rstrip() + '\ninterrupt_on: ["*"]\n', "plain.yaml")


def test_a_gate_list_written_as_deepagents_mapping_is_refused_where_it_is_read():
    """`interrupt_on: {execute: true}`, deepagents' own shape, was read as one tool named
    "{'execute': True}" and refused only at the build, as a tool nothing defines.
    """
    with pytest.raises(AgentError) as raised:
        _read(MINIMAL.rstrip() + "\ninterrupt_on:\n  execute: true\n", "plain.yaml")

    assert "plain.yaml: interrupt_on is a mapping" in str(raised.value)
    assert "[execute]" in str(raised.value), "it says what to write instead"


def test_an_agent_that_gates_nothing_says_so_as_an_empty_tuple():
    """Absent and empty are the same posture here, unlike `memory` -- there is no
    third state a gate list could be in, so neither needs to be `None`.
    """
    assert _read(MINIMAL, "plain.yaml").interrupt_on == ()


def test_an_unknown_field_is_refused_and_a_near_miss_is_named():
    """A key we ignore is a key the author believes took effect."""
    with pytest.raises(AgentError, match="did you mean 'tools'"):
        _read(MINIMAL.rstrip() + "\ntolls: []\n", "plain.yaml")


def test_memory_has_three_states_and_absent_is_not_off():
    """A switch narrows like every other axis, so only `False` can subtract."""
    assert _read(MINIMAL, "plain.yaml").memory is None
    assert _read(MINIMAL.rstrip() + "\nmemory: true\n", "plain.yaml").memory is True
    assert _read(MINIMAL.rstrip() + "\nmemory: false\n", "plain.yaml").memory is False


def test_a_flag_that_is_not_a_bool_is_refused():
    """`memory: "false"` is a non-empty string, and every non-empty string is true -- so
    the reading Python would take says the opposite of what the file says.
    """
    for written in ["'false'", '"no"', "0", "maybe"]:
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

    assert set(LocalAgentRepository(tmp_path).specs) == {"triage"}


def test_a_folder_called_tools_is_organisation_here_too(tmp_path):
    """The asymmetry in the walk both kinds share: `tools/` and `skills/` are reserved
    under `subagents/`, where they hold a bundle's own assets, and are ordinary folder
    names under `agents/`. Handing this walk the subagent's skip list would lose an
    agent filed in one, and nothing else would say so.
    """
    _write(tmp_path, "tools/triage.yaml", "triage", "Sorts.")

    assert set(LocalAgentRepository(tmp_path).specs) == {"triage"}


def test_two_agents_of_one_name_are_refused_rather_than_disambiguated(tmp_path):
    """Where subagents keep both under a reference, this refuses."""
    _write(tmp_path, "a.yaml", "assistant")
    _write(tmp_path, "nested/b.yaml", "assistant", "Two.")

    with pytest.raises(AgentError, match="two agents are called 'assistant'"):
        _ = LocalAgentRepository(tmp_path).specs


@pytest.mark.parametrize("name", ["assistant.yml", "assistant.YML", "assistant.YAML"])
def test_a_yml_file_is_named_rather_than_silently_skipped(tmp_path, name):
    """`.YML` and `.YAML` were skipped without a word: the near miss was matched in
    lower case only.
    """
    _write(tmp_path, name, "assistant")

    with pytest.raises(AgentError, match=r"rename it to assistant\.yaml"):
        _ = LocalAgentRepository(tmp_path).specs


def test_a_directory_that_is_not_there_holds_no_agents(tmp_path):
    """Empty rather than an error, which is the same answer a derived catalogue gives
    before anyone seeds it.
    """
    assert LocalAgentRepository(tmp_path / "nope").specs == {}


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
    spec = _read(MINIMAL.rstrip() + "\nsource_ids: [A, B]\n", "plain.yaml")

    assert spec.source_ids == ("A", "B")


def test_an_agent_that_says_nothing_is_reachable_by_everyone():
    """An absent optional field means no restriction, which is what it means everywhere
    else in this format -- and what makes adopting audiences incremental rather than
    all-or-nothing.
    """
    assert _read(MINIMAL, "plain.yaml").source_ids == ALL


def test_an_entry_with_no_audience_inherits_the_definitions():
    """A plain list under a policied definition means 'these, at my audience', which is
    what keeps every file written before audiences unchanged.
    """
    spec = _read(MINIMAL.rstrip() + "\nsource_ids: [A]\ntools: [sql_query]\n", "plain.yaml")

    assert spec.declares(frozenset({"A"})).tools == ("sql_query",)
    assert spec.declares(frozenset({"B"})).tools == ()


def test_declaring_with_no_caller_is_what_it_always_was():
    """A deployment with no vocabulary, or an UNSCOPED call."""
    spec = _read(
        MINIMAL.rstrip()
        + "\nsource_ids: [A]\nbuiltin_tools: [read_file]\n"
        + "skills:\n  - name: audit\n    source_ids: [B]\n",
        "plain.yaml",
    )

    assert spec.declares(None).skills == ("audit",)


def test_builtin_tools_takes_no_audience():
    """deepagents registers those itself, so they can be filtered but never left out of
    a graph -- an audience here would promise a boundary it cannot keep.
    """
    with pytest.raises(AgentError, match="this field takes a list"):
        _read(
            MINIMAL.rstrip() + "\nbuiltin_tools:\n  execute:\n    source_ids: [A]\n",
            "plain.yaml",
        )


def test_the_refusal_of_a_mapping_names_skills_as_the_one_field_taking_an_audience():
    """It named tools and subagents too, sending a reader to write the audience the
    reader of either field refuses.
    """
    with pytest.raises(AgentError, match="only a skills entry may say that"):
        _read(
            MINIMAL.rstrip() + "\nbuiltin_tools:\n  execute:\n    source_ids: [A]\n",
            "plain.yaml",
        )


def test_a_mapping_under_middlewares_is_sent_to_the_long_form_not_told_about_builtin_tools():
    """It got the builtin_tools refusal, which says nothing about where settings go."""
    with pytest.raises(AgentError, match=r"- \{name: cap, settings: \{\.\.\.\}\}") as refused:
        _read(
            MINIMAL.rstrip() + "\nmiddlewares:\n  cap:\n    settings: {n: 1}\n",
            "plain.yaml",
        )
    assert "builtin" not in str(refused.value)


def test_an_entry_audience_outside_the_definitions_own_is_recorded_not_judged():
    """Whether one audience can ever reach another is a question about what the names
    *mean*, and this format has no vocabulary to answer it with: `B` may contain `C`,
    or `A` may require it.
    """
    spec = _read(
        MINIMAL.rstrip()
        + "\nsource_ids: [A, B]\nbuiltin_tools: [read_file]\n"
        + "skills:\n  - name: audit\n    source_ids: [C]\n",
        "plain.yaml",
    )

    assert spec.source_ids == ("A", "B")
    assert spec.skill_audiences["audit"] == ("C",)


def test_a_requirement_is_read_as_one_entry_of_the_list():
    """A set in a definition, which is the inline half of the same shape the vocabulary
    uses for a named one -- and it is read here from YAML rather than a dict, since
    `{a, b}` being a mapping of null values is what makes the spelling work at all.
    """
    spec = _read(
        MINIMAL.rstrip() + "\nsource_ids: [admin, {finance, senior}]\n",
        "plain.yaml",
    )

    assert spec.source_ids == ("admin", frozenset({"finance", "senior"}))


def test_only_the_restricted_entries_need_an_audience():
    """The ergonomics of the long form, and the reason an entry may stay short."""
    spec = _read(
        MINIMAL.rstrip()
        + "\nsource_ids: [A, B]\nbuiltin_tools: [read_file]\n"
        + "skills:\n  - name: audit\n    source_ids: [A]\n"
        + "  - review\n  - triage\n",
        "plain.yaml",
    )

    assert spec.skills == ("audit", "review", "triage")
    assert spec.skill_audiences == {"audit": ("A",)}
    assert spec.declares(frozenset({"A"})).skills == ("audit", "review", "triage")
    assert spec.declares(frozenset({"B"})).skills == ("review", "triage")


def test_long_entries_that_restrict_nothing_mean_what_the_list_means():
    """The two spellings have to agree about an unrestricted name, or the mapping form
    would quietly change what a definition holds.
    """
    written = "\nsource_ids: [A]\ntools:\n  - name: sql_query\n  - name: http_fetch\n"
    as_list = "\nsource_ids: [A]\ntools: [sql_query, http_fetch]\n"

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

    from kingfisher.core.definitions.agents.spec import parse
    from kingfisher.core.definitions.seeding import starter_agent

    printed = starter_agent(cfg.definitions / "agents")
    block = printed.split("holding every tool:\n\n", 1)[1].split("\n\nLeave out", 1)[0]
    document = yaml.safe_load(textwrap.dedent(block))

    spec = parse(document, Path("agents/helper.yaml"))

    assert spec.name == "helper"
    assert spec.system_prompt.strip()
    assert (spec.builtin_tools, spec.tools) == (ALL, ALL), "a starter that cannot act"


def test_a_workspace_with_no_agents_is_told_how_to_write_one(cfg):
    """`--from DIR` needs a DIR, and an installed kingfisher has none -- the hint is
    correct and terminal.
    """
    from kingfisher import Kingfisher
    from kingfisher.plugins import LocalSessions

    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError) as refused:
        kf._agent_named("assistant")

    said = str(refused.value)
    assert "offers none" in said
    assert "name: helper" in said, "the starter is not in the refusal"
    assert "system_prompt" in said


def test_the_starter_names_where_agents_are_read_from(cfg, tmp_path):
    """It said "a file in agents/", which sends the reader to write one in the
    workspace, where nothing reads it.
    """
    from dataclasses import replace

    from kingfisher import Kingfisher
    from kingfisher.plugins import LocalSessions

    (tmp_path / "catalogue").mkdir()
    elsewhere = tmp_path / "catalogue" / "agents"
    kf = Kingfisher(replace(cfg, definitions=elsewhere.parent), sessions=LocalSessions())

    with pytest.raises(CapabilityError) as refused:
        kf._agent_named("assistant")

    said = str(refused.value)
    assert f"a file in {elsewhere}/" in said
    assert f"# {elsewhere / 'helper.yaml'}" in said


def test_a_workspace_that_has_agents_is_not_lectured(cfg):
    """The starter is for an empty workspace."""
    from kingfisher import Kingfisher
    from kingfisher.plugins import LocalSessions
    from tests.conftest import an_agent

    an_agent(cfg, "only")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError) as refused:
        kf._agent_named("missing")

    said = str(refused.value)
    assert "only" in said
    assert "system_prompt" not in said


# -- the axes no audience touches -------------------------------------------


def test_the_two_kinds_declare_the_unaudienced_axes_differently():
    """An agent widens `models` to everything and states its own `memory`; a subagent
    leaves both unset. Pinned because nothing else asserts it: `declares` narrows three
    fields against an audience, and the axes it does *not* narrow were carried along by
    two hand-written bodies -- so one body serving both kinds would flatten this with
    the whole suite still green.
    """
    agent = _read(MINIMAL.rstrip() + "\nmemory: false\n", "plain.yaml")
    subagent = a_subagent(
        "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n", "reviewer.yaml"
    )

    for held in (None, frozenset({"A"})):
        said, theirs = agent.declares(held), subagent.declares(held)

        assert said.models == ALL, "an agent may put a subagent on any model it names"
        assert said.memory is False, "the file said so, and only the agent has the field"
        assert theirs.models is None, "a subagent names its own model or inherits one"
        assert theirs.memory is None, "memory is the agent's to decline, not a subagent's"
        assert said.endpoints == ALL and theirs.endpoints == ALL, "no audience reaches these"
