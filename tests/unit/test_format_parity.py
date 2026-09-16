"""The agent and subagent formats, held to each other.

Each format keeps its own reader, and a field both define has to mean the same thing
in either file. These tests hold the two readers to that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kingfisher.kinds.agents import reading as agents
from kingfisher.kinds.agents.spec import KNOWN as AGENT_KNOWN
from kingfisher.kinds.agents.spec import AgentError, AgentSpec
from kingfisher.kinds.subagents import reading as subagents
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec

#: Each format as a check needs it: its reader, the keys it defines, the spec it builds.
FORMATS = {
    "agent": (agents.read, AGENT_KNOWN, AgentSpec),
    "subagent": (subagents.read, subagents.KNOWN, SubagentSpec),
}

SHARED_KEYS = AGENT_KNOWN & subagents.KNOWN
SHARED_FIELDS = sorted(
    set(AgentSpec.__dataclass_fields__) & set(SubagentSpec.__dataclass_fields__)
)

HEAD = "name: n\ndescription: d\nsystem_prompt: |\n  Go.\n"
REQUIRED = frozenset(yaml.safe_load(HEAD))

#: Keys a definition writes that reach the spec through a *derived* field
#: instead of one named after them. `model:` is read into `wanted`, which is the
#: derived field the resolution works on, so it has no field of its own.
#:
#: Here rather than in either format because only this test needs it, and a
#: constant defined for a test is what `test_nothing_is_defined_for_tests_alone`
#: exists to refuse.
FOLDED_INTO = {"model": "wanted"}


@pytest.mark.parametrize("kind", sorted(FORMATS))
def test_the_known_set_matches_the_spec_it_builds(kind):
    """Two lists that must agree, in both directions.

    Asked of both formats. Only the subagent's used to be, so `AgentSpec` never marked
    which of its fields no file writes, and nothing would have noticed a key it
    accepted and never read.
    """
    _, known, spec = FORMATS[kind]
    fields_by_name = spec.__dataclass_fields__
    written = {
        name for name, f in fields_by_name.items() if not f.metadata.get("derived")
    }

    assert written | set(FOLDED_INTO) == known

    for key, target in FOLDED_INTO.items():
        assert key not in fields_by_name, f"{key!r} has a field, so it is not folded"
        assert fields_by_name[target].metadata.get("derived"), (
            f"{key!r} claims to fold into {target!r}, which is not a derived field"
        )


#: One document per way of writing the fields both formats define, some read and
#: some refused. `model: [gpt-5, claude-4]` is the one with a history: the fix
#: refusing a list there reached the subagent reader alone, and the agent's went on
#: turning the list into a string.
DOCUMENTS = {
    "nothing optional": HEAD,
    "every shared field, as a list": HEAD + (
        "builtin_tools: [read_file, grep]\n"
        "tools: [csv_profile::csv_profile, fetch]\n"
        "skills: [tabular-qa]\n"
        "subagents: [reviewer]\n"
        "middlewares: [audit]\n"
        "model: gpt-5\n"
        "metadata: {owner: data-eng}\n"
        "source_ids: [A, B]\n"
    ),
    "entries with audiences and settings": HEAD + (
        "tools:\n  - name: fetch\n    source_ids: [A]\n  - csv_profile\n"
        "skills:\n  - name: tabular-qa\n    source_ids: [B]\n"
        "subagents:\n  - name: reviewer\n    source_ids: [A]\n"
        "middlewares:\n  - name: audit\n    settings: {limit: 3}\n"
    ),
    "the star, where both take it": HEAD + (
        'builtin_tools: ["*"]\ntools: ["*"]\nmiddlewares: ["*"]\nsource_ids: ["*"]\n'
    ),
    "an audience needing two names": HEAD + "source_ids: [{finance, senior}, A]\n",
    "several models": HEAD + "model: [gpt-5, claude-4]\n",
    "a number where names go": HEAD + "tools: 3\n",
    "one name twice": HEAD + "tools: [fetch, fetch]\n",
    "an entry key misspelled": HEAD + "tools:\n  - name: fetch\n    sourceids: [A]\n",
    "settings that are not a mapping": HEAD + (
        "middlewares:\n  - name: audit\n    settings: 3\n"
    ),
    "metadata that is not a mapping": HEAD + "metadata: [owner]\n",
    "a blank description": "name: n\ndescription: ''\nsystem_prompt: |\n  Go.\n",
    "no description": "name: n\nsystem_prompt: |\n  Go.\n",
    "a prompt that reflows": "name: n\ndescription: d\nsystem_prompt: Go.\n",
}


def _outcome(kind: str, text: str) -> tuple[str, object]:
    """What one format made of a document: the shared fields it read, or its refusal."""
    read, _, _ = FORMATS[kind]
    try:
        built = read(text, Path("n.yaml"))
    except (AgentError, SubagentError) as refused:
        return "refused", str(refused)
    return "read", {name: getattr(built, name) for name in SHARED_FIELDS}


@pytest.mark.parametrize("label", sorted(DOCUMENTS))
def test_a_shared_field_reads_the_same_in_either_file(label):
    """A fix, a default or a refusal that reaches one reader and not the other."""
    text = DOCUMENTS[label]

    assert _outcome("agent", text) == _outcome("subagent", text)


def test_the_documents_read_every_field_both_formats_define():
    """A shared field no document writes is absent from both specs, and two readers that
    each ignore it agree perfectly. Counted over the documents that are *read*, because
    one refused before reaching a key never compares it.
    """
    read = [
        yaml.safe_load(text)
        for text in DOCUMENTS.values()
        if _outcome("agent", text)[0] == "read"
    ]
    written = set().union(*read)

    unwritten = sorted(SHARED_KEYS - written)
    assert not unwritten, f"no document that is read writes {unwritten}"
    assert len(read) < len(DOCUMENTS), "no document is refused, so no refusal is compared"


#: Where the two formats part on purpose: a delegate refuses `*` for these, and
#: each reason is written beside the field in `kinds.subagents.reading`.
PART_ON_THE_STAR = frozenset({"skills", "subagents"})


def test_the_star_is_the_only_place_the_formats_part():
    """Every shared key is tried rather than these two, so a third field that starts
    answering `["*"]` differently -- or one of these that stops -- fails here instead of
    passing as the rule.
    """
    parted = {
        key
        for key in SHARED_KEYS - REQUIRED
        if _outcome("agent", HEAD + f'{key}: ["*"]\n')
        != _outcome("subagent", HEAD + f'{key}: ["*"]\n')
    }

    assert parted == PART_ON_THE_STAR
