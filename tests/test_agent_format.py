"""The agent format: `/agents/<name>.yaml`.

The thing a request runs. Everything else in a catalogue is something an agent
selects from, and until this existed the answer to "which agent?" was assembled
from four places that did not know about each other -- `system.md` with
`PROMPT.md`, three environment switches, `models.yaml`'s `default:`, and
whatever a request narrowed to.

Two questions run through these tests. Does a definition mean what it says, and
where does this format deliberately disagree with the subagent one it borrows
its readers from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.domain.agent import AgentError
from kingfisher.domain.capabilities import ALL
from kingfisher.domain.subagent import SubagentError
from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository
from kingfisher.infrastructure.catalogue.documents import read_agent, read_subagent

WHOLE = """name: surveyor
description: Reads and profiles data without changing anything.
builtin_tools: [read_file, ls, glob, grep]
tools: [csv_profile::csv_profile]
alias: cheap
memory: false
system_prompt: |
  You survey files before anyone trusts them.
"""

MINIMAL = """name: plain
description: An agent defined by nothing but what it holds.
"""


def _read(text: str, name: str = "surveyor.yaml"):
    return read_agent(text, Path(name))


# -- what a definition says -------------------------------------------------


def test_a_whole_definition_reads_as_written():
    spec = _read(WHOLE)

    assert spec.name == "surveyor"
    assert spec.builtin_tools == ("read_file", "ls", "glob", "grep")
    assert spec.tools == ("csv_profile::csv_profile",)
    assert spec.memory is False
    assert spec.system_prompt.startswith("You survey files")


def test_two_fields_are_required_and_the_prompt_is_not():
    """An agent described entirely by its tools and its model is a legitimate
    thing: `system.md` and `PROMPT.md` are already a working prompt, and this
    format only ever adds to them."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.system_prompt == ""
    assert spec.wanted == ()


@pytest.mark.parametrize("missing", ["name", "description"])
def test_a_missing_required_field_is_refused_by_name(missing):
    written = "\n".join(
        line for line in MINIMAL.strip().splitlines() if not line.startswith(missing)
    )

    with pytest.raises(AgentError, match=f"missing required field '{missing}'"):
        _read(written + "\n", "plain.yaml")


def test_a_present_but_empty_field_says_so_rather_than_missing():
    """Absent and blank are different mistakes, and "missing" sends somebody
    looking for a line they can see they wrote."""
    with pytest.raises(AgentError, match="'description' is present but empty"):
        _read("name: plain\ndescription:\n", "plain.yaml")


# -- omission, which is where the two formats agree -------------------------


def test_leaving_the_tool_fields_out_grants_all_of_them():
    """The one place omission grants rather than withholds. An agent can do
    nothing at all without tools, so inheriting is the useful default -- the
    same rule a subagent file follows, said as one sentence for both."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.builtin_tools == ALL
    assert spec.tools == ALL


def test_leaving_skills_and_delegates_out_grants_none():
    """The other half of that sentence. Skills and delegates are what an agent
    needs to *know* and to *ask*, and most need neither -- the skills index is
    injected into the prompt at a measured ~464 tokens for three, and every
    delegate compiles its own graph at ~4.3ms."""
    spec = _read(MINIMAL, "plain.yaml")

    assert spec.skills is None
    assert spec.subagents is None
    assert spec.middleware is None


def test_a_tool_may_say_where_it_lives_and_the_claim_is_kept():
    spec = _read(WHOLE)

    assert spec.tool_sources == {"csv_profile": "csv_profile"}


# -- where the two formats disagree, deliberately ---------------------------


def test_an_agent_may_name_every_subagent_and_a_subagent_may_not():
    """The one field that answers `["*"]` differently in the two folders, and
    the reason is in the files rather than in a table somebody has to remember.

    A subagent naming every subagent has named itself, which is always a loop.
    An agent is not one of the subagents, so here it is the ordinary "give it
    the run of the place".
    """
    assert _read(MINIMAL.rstrip() + '\nsubagents: ["*"]\n', "plain.yaml").subagents == ALL

    delegate = 'name: d\ndescription: A delegate.\nsubagents: ["*"]\nsystem_prompt: |\n  Go.\n'
    with pytest.raises(SubagentError, match="always a loop"):
        read_subagent(delegate, Path("d.yaml"))


def test_distinct_is_refused_with_the_reason_rather_than_as_an_unknown_field():
    """`distinct` says "not the model that summoned me", and nothing summons an
    agent. The generic message would read as "not supported yet" and send
    somebody looking for a workaround."""
    with pytest.raises(AgentError, match="nothing summons an agent"):
        _read(MINIMAL.rstrip() + "\ndistinct: true\n", "plain.yaml")


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
    """A key we ignore is a key the author believes took effect. `tolls:` would
    otherwise produce an agent holding every tool the workspace defines, since a
    missing `tools` means all of them."""
    with pytest.raises(AgentError, match="did you mean 'tools'"):
        _read(MINIMAL.rstrip() + "\ntolls: []\n", "plain.yaml")


# -- the model, and the switch ----------------------------------------------


def test_naming_both_a_model_and_an_alias_is_refused():
    written = MINIMAL.rstrip() + "\nmodel: gpt-5\nalias: cheap\n"

    with pytest.raises(AgentError, match="name one or the other"):
        _read(written, "plain.yaml")


def test_the_model_may_name_several_tried_in_order():
    spec = _read(MINIMAL.rstrip() + "\nalias: [alternate, cheap]\n", "plain.yaml")

    assert [w.alias for w in spec.wanted] == ["alternate", "cheap"]


def test_memory_has_three_states_and_absent_is_not_off():
    """A switch narrows like every other axis, so only `False` can subtract.
    Reading an absent `memory:` as "off" would make every agent file that never
    mentions it quietly decline something the deployment wired."""
    assert _read(MINIMAL, "plain.yaml").memory is None
    assert _read(MINIMAL.rstrip() + "\nmemory: true\n", "plain.yaml").memory is True
    assert _read(MINIMAL.rstrip() + "\nmemory: false\n", "plain.yaml").memory is False


def test_a_quoted_false_is_refused_rather_than_read_as_true():
    with pytest.raises(AgentError, match="write true or false"):
        _read(MINIMAL.rstrip() + '\nmemory: "false"\n', "plain.yaml")


def test_metadata_is_carried_and_a_bag_with_no_shape_is_refused():
    assert _read(MINIMAL.rstrip() + "\nmetadata:\n  owner: data-eng\n", "plain.yaml").metadata == {
        "owner": "data-eng"
    }

    with pytest.raises(AgentError, match="must be a mapping"):
        _read(MINIMAL.rstrip() + "\nmetadata: gold\n", "plain.yaml")


def test_a_folded_prompt_is_refused_because_it_reflows():
    """`>` joins consecutive lines into one, so a numbered procedure reaches the
    model as a run-on line. The document is valid and the only symptom is an
    agent behaving oddly."""
    with pytest.raises(AgentError, match="reflows it"):
        _read(MINIMAL.rstrip() + "\nsystem_prompt: >\n  One.\n  Two.\n", "plain.yaml")


def test_the_error_says_which_format_the_broken_file_is_in():
    """`AgentError` rather than `SubagentError`, and that is the whole reason
    `read_agent` is its own function rather than a shared one taking a parser:
    it is what tells somebody which of two folders to open."""
    with pytest.raises(AgentError):
        _read("name: plain\n", "plain.yaml")


# -- the directory ----------------------------------------------------------


def _write(root: Path, where: str, body: str) -> None:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_a_folder_is_organisation_and_the_name_field_is_the_identity(tmp_path):
    _write(tmp_path, "support/triage.yaml", "name: triage\ndescription: Sorts.\n")

    assert LocalAgentRepository(tmp_path).names == ("triage",)


def test_two_agents_of_one_name_are_refused_rather_than_disambiguated(tmp_path):
    """Where subagents keep both under a reference, this refuses. A request
    names exactly one agent, so there is no roster for a reference to pick
    within -- two files claiming `assistant` means whichever the walk reached
    last, with nothing anywhere saying which."""
    _write(tmp_path, "a.yaml", "name: assistant\ndescription: One.\n")
    _write(tmp_path, "nested/b.yaml", "name: assistant\ndescription: Two.\n")

    with pytest.raises(AgentError, match="two agents are called 'assistant'"):
        _ = LocalAgentRepository(tmp_path).names


def test_a_yml_file_is_named_rather_than_silently_skipped(tmp_path):
    _write(tmp_path, "assistant.yml", "name: assistant\ndescription: One.\n")

    with pytest.raises(AgentError, match=r"rename it to assistant\.yaml"):
        _ = LocalAgentRepository(tmp_path).names


def test_a_directory_that_is_not_there_holds_no_agents(tmp_path):
    """Empty rather than an error, which is the same answer a derived catalogue
    gives before anyone seeds it. What a request gets is "no agent by that
    name", which is the message somebody can act on."""
    assert LocalAgentRepository(tmp_path / "nope").names == ()
