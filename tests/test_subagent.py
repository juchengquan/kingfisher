"""Parsing `/subagents/<name>.yaml`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.domain.capabilities import CapabilityError
from kingfisher.domain.subagent import (
    KNOWN,
    REFUSED,
    SubagentError,
    SubagentSpec,
    resolved_endpoint,
)
from kingfisher.infrastructure.definitions import read_subagent, skill_name
from kingfisher.infrastructure.subagent_store import load_all

MINIMAL = """name: reviewer
description: Checks an analysis for arithmetic errors.
system_prompt: |
  You review analyses. Be specific about what is wrong.

"""

FULL = """name: reviewer
description: "Checks an analysis for arithmetic errors."
tools: [read_file, glob, grep]
model: MiniMax-M2.5
system_prompt: |
  You review analyses.

"""


def test_minimal_definition_parses():
    spec = read_subagent(MINIMAL, Path("reviewer.yaml"))

    assert spec.name == "reviewer"
    assert spec.description == "Checks an analysis for arithmetic errors."
    assert spec.system_prompt.startswith("You review analyses.")
    # Unset, not empty: the subagent inherits the parent's tools.
    assert spec.tools is None
    assert spec.model is None


def test_optional_fields_and_quoting():
    spec = read_subagent(FULL, Path("reviewer.yaml"))

    assert spec.tools == ("read_file", "glob", "grep")
    assert spec.model == "MiniMax-M2.5"
    assert spec.description == "Checks an analysis for arithmetic errors."  # unquoted


@pytest.mark.parametrize(
    ("text", "because"),
    [
        # A bare scalar is valid YAML and not a definition.
        ("no fields at all", "expected a mapping of fields"),
        ("description: x\nsystem_prompt: |\n  body\n", "missing required field 'name'"),
        ("name: x\nsystem_prompt: |\n  body\n", "missing required field 'description'"),
        ("name: x\ndescription: y\n", "missing required field 'system_prompt'"),
        # Present but blank is a different mistake, and says so.
        ("name: x\ndescription: y\nsystem_prompt: |\n", "'system_prompt' is present but empty"),
        ("name: \ndescription: y\nsystem_prompt: |\n  body\n", "'name' is present but empty"),
        # YAML says why; we say which file. Rejected either way.
        ("name x\ndescription: y\nsystem_prompt: |\n  body\n", "cannot read definition"),
        ("- not\n- a mapping\n", "expected a mapping of fields"),
    ],
)
def test_malformed_definitions_are_rejected(text, because):
    """Loudly, at build time — a subagent that silently loses its prompt would
    fail much later and much less legibly."""
    with pytest.raises(SubagentError, match=because):
        read_subagent(text, Path("broken.yaml"))


def test_load_all_is_empty_when_the_directory_is_absent(tmp_path):
    assert load_all(tmp_path / "subagents") == {}


def test_load_all_keys_on_the_declared_name_not_the_filename(tmp_path):
    directory = tmp_path / "subagents"
    directory.mkdir()
    (directory / "misnamed.yaml").write_text(MINIMAL, encoding="utf-8")

    specs = load_all(tmp_path / "subagents")
    assert set(specs) == {"reviewer"}


def test_load_all_rejects_two_files_claiming_one_name(tmp_path):
    """Otherwise one silently shadows the other depending on sort order."""
    directory = tmp_path / "subagents"
    directory.mkdir()
    (directory / "a.yaml").write_text(MINIMAL, encoding="utf-8")
    (directory / "b.yaml").write_text(MINIMAL, encoding="utf-8")

    with pytest.raises(SubagentError, match="duplicate subagent name"):
        load_all(tmp_path / "subagents")


def test_folded_and_block_list_fields_are_accepted(tmp_path):
    """Two parsers read one format, and ours was the stricter.

    deepagents reads a skill's header with `yaml.safe_load`. A block list is
    the Agent Skills spec's documented form for `allowed-tools`, and a folded
    scalar is how anyone writes a description longer than a line. Rejecting
    them made a skill that loads from the catalogue impossible to upload.
    """
    definition = (
        "name: extractor\n"
        "description: >-\n"
        "  Pulls fields out of documents,\n"
        "  one record at a time.\n"
        "tools:\n"
        "  - read_file\n"
        "  - grep\n"
        "system_prompt: |\n"
        "  You extract.\n"
    )

    spec = read_subagent(definition, tmp_path / "extractor.yaml")

    assert spec.name == "extractor"
    assert spec.tools == ("read_file", "grep")
    assert "one record at a time" in spec.description


# -- fields this format does not define ------------------------------------


def _definition(*extra_lines: str) -> str:
    header = "\n".join(("name: reviewer", "description: d", *extra_lines))
    return f"{header}\nsystem_prompt: |\n  You review analyses.\n"


def test_a_typo_of_an_optional_field_is_refused_not_ignored(tmp_path):
    """The bug this closes. `tolls:` was dropped in silence, and dropping it is
    indistinguishable from honouring it: a missing `tools` means *inherit*, so
    the delegate came out holding every tool its parent had.
    """
    with pytest.raises(SubagentError, match="tolls") as raised:
        read_subagent(_definition("tolls: [read_file]"), tmp_path / "reviewer.yaml")

    assert "did you mean 'tools'?" in str(raised.value)


def test_a_typo_of_a_required_field_names_the_typo(tmp_path):
    """Not "missing required field 'name'", which sends the author looking for
    something they can plainly see they wrote."""
    body = "nmae: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n"

    with pytest.raises(SubagentError, match="nmae") as raised:
        read_subagent(body, tmp_path / "reviewer.yaml")

    assert "did you mean 'name'?" in str(raised.value)


def test_an_unrecognisable_field_is_refused_and_lists_what_is_allowed(tmp_path):
    """No near match, so no guess -- just the field set, which is the only
    honest thing to offer. There is nowhere to put your own keys yet, and the
    message does not pretend otherwise.
    """
    with pytest.raises(SubagentError, match="additional_abc") as raised:
        read_subagent(_definition("additional_abc: 1"), tmp_path / "reviewer.yaml")

    message = str(raised.value)
    assert "did you mean" not in message
    for field in ("name", "description", "tools", "skills", "middleware", "provider", "model"):
        assert field in message


def test_every_unaccepted_field_is_reported_at_once(tmp_path):
    """Not just the first. Two typos used to take two runs to find, and the
    second only after fixing the first."""
    with pytest.raises(SubagentError) as raised:
        read_subagent(
            _definition("tolls: [read_file]", "temperature: 0.2", "permissions: [deny]"),
            tmp_path / "reviewer.yaml",
        )

    message = str(raised.value)
    assert "tolls" in message
    assert "temperature" in message
    assert "permissions" in message
    assert "did you mean 'tools'?" in message  # and each is explained in its own terms


@pytest.mark.parametrize("field", sorted(REFUSED))
def test_a_deliberately_unexposed_field_says_why(tmp_path, field):
    """These are not "not yet". Honouring them would be wrong, and the generic
    message reads as an omission someone might work around."""
    with pytest.raises(SubagentError, match=field) as raised:
        read_subagent(_definition(f"{field}: something"), tmp_path / "reviewer.yaml")

    message = str(raised.value)
    assert "did you mean" not in message
    assert REFUSED[field].split()[0] in message


def test_permissions_explains_the_direction_it_gets_wrong(tmp_path):
    """The one worth a test of its own: it is written to *tighten* a delegate
    and silently did nothing, so the definition read stricter than the agent it
    produced."""
    with pytest.raises(SubagentError) as raised:
        read_subagent(_definition("permissions: [deny]"), tmp_path / "reviewer.yaml")

    message = str(raised.value)
    assert "replace" in message
    assert "read-only" in message


def test_every_known_field_still_parses(tmp_path):
    """The negative control: strictness that rejected a valid definition would
    be a worse bug than the one it fixes."""
    body = (
        "name: reviewer\n"
        "description: d\n"
        "tools: [read_file]\n"
        "skills: [tabular-qa]\n"
        "middleware: [audit]\n"
        "provider: openai\n"
        "model: gpt-5\n"
        "system_prompt: |\n  You review.\n"
    )
    spec = read_subagent(body, tmp_path / "reviewer.yaml")

    assert spec.tools == ("read_file",)
    assert spec.middleware == ("audit",)
    assert spec.provider == "openai"


def test_the_known_set_matches_the_spec_it_builds():
    """Two lists that must agree: a field added to the dataclass but not to
    KNOWN would be refused as unknown the moment anyone used it."""
    assert set(SubagentSpec.__dataclass_fields__) == KNOWN


def test_a_skill_may_carry_fields_kingfisher_does_not_know(tmp_path):
    """Deliberately the opposite rule. Kingfisher does not own the skill format,
    so refusing keys there would reject what deepagents considers valid."""
    body = "---\nname: code-review\nallowed-tools: [read_file]\nlicense: MIT\n---\nBody.\n"

    assert skill_name(body) == "code-review"


def test_a_prompt_that_begins_indented_still_loads(tmp_path):
    """`system_prompt: |` takes its indentation from the first line, so a prompt
    opening with a code example *fails to parse*. The `2` pins the block to a
    fixed column, which is why the presets and the docs use it.

    The prompt's outer whitespace is still stripped, as the markdown body always
    was -- what the indicator buys is that the document loads at all.
    """
    lines = "      ls -la /data\n  Then report what you found.\n"
    header = "name: reviewer\ndescription: d\nsystem_prompt: "

    spec = read_subagent(header + "|2\n" + lines, tmp_path / "reviewer.yaml")
    assert "ls -la /data" in spec.system_prompt
    assert "Then report what you found." in spec.system_prompt

    # The same document without the indicator does not load at all.
    with pytest.raises(SubagentError, match="cannot read definition"):
        read_subagent(header + "|\n" + lines, tmp_path / "reviewer.yaml")


def test_indentation_inside_a_prompt_is_preserved(tmp_path):
    """Only the outer edges are stripped. A numbered list's continuation lines
    carry their indent into the delegate's prompt, which is how the shipped
    presets are written."""
    definition = (
        "name: reviewer\n"
        "description: d\n"
        "system_prompt: |\n"
        "  1. Recompute the figure.\n"
        "     Do not reuse the caller's script.\n"
    )

    spec = read_subagent(definition, tmp_path / "reviewer.yaml")

    assert "\n   Do not reuse" in spec.system_prompt


# -- how the prompt is written ---------------------------------------------

HEAD = "name: reviewer\ndescription: d\n"
STEPS = "  1. Recompute the figure.\n  2. Say which definition you applied.\n"


@pytest.mark.parametrize("style", ["|", "|2", "|-", "|+"])
def test_every_literal_block_is_accepted(tmp_path, style):
    """The indicator and the chomping marker are none of this check's business
    -- they are all the same style, and all of them keep the line breaks."""
    spec = read_subagent(HEAD + f"system_prompt: {style}\n" + STEPS, tmp_path / "reviewer.yaml")

    assert "Recompute the figure.\n2. Say" in spec.system_prompt


@pytest.mark.parametrize("style", [">", ">-", ">2"])
def test_a_folded_prompt_is_refused(tmp_path, style):
    """`>` joins consecutive lines, so two numbered steps reach the delegate as
    one run-on line -- valid YAML, correct-looking file, odd-behaving agent."""
    with pytest.raises(SubagentError, match="reflows it") as raised:
        read_subagent(HEAD + f"system_prompt: {style}\n" + STEPS, tmp_path / "reviewer.yaml")

    assert "system_prompt: |" in str(raised.value)


def test_a_plain_prompt_is_refused(tmp_path):
    """The same damage, without even a marker to notice."""
    with pytest.raises(SubagentError, match="a plain scalar"):
        read_subagent(HEAD + "system_prompt: Recompute the figure.\n", tmp_path / "reviewer.yaml")


def test_a_quoted_prompt_is_refused(tmp_path):
    with pytest.raises(SubagentError, match="reflows it"):
        read_subagent(HEAD + 'system_prompt: "Recompute the figure."\n', tmp_path / "reviewer.yaml")


def test_folding_is_what_the_refusal_is_about(tmp_path):
    """The negative control, so the rule is justified rather than asserted:
    this is what a folded prompt would have handed the delegate."""
    import yaml as _yaml

    folded = _yaml.safe_load(HEAD + "system_prompt: >\n" + STEPS)["system_prompt"]

    assert folded == "1. Recompute the figure. 2. Say which definition you applied.\n"


def test_the_description_may_still_be_folded(tmp_path):
    """Only the prompt is checked. A description is one paragraph, and `>-` is
    how anyone writes one longer than a line -- the skill spec's own form."""
    definition = (
        "name: reviewer\n"
        "description: >-\n"
        "  Checks an analysis for arithmetic errors,\n"
        "  one claim at a time.\n"
        "system_prompt: |\n  You review.\n"
    )

    spec = read_subagent(definition, tmp_path / "reviewer.yaml")

    assert spec.description == "Checks an analysis for arithmetic errors, one claim at a time."


# -- where a delegate runs ------------------------------------------------
#
# The rule used to sit in `infrastructure.delegation` and take a whole `Config`
# to read two values out of. It takes the two values now, so it is reachable
# without a deployment -- which is the same "a domain rule that needs a value
# takes the value" that `test_domain_imports_only_the_standard_library_and_itself`
# already enforces for the record itself.


def _spec(provider: str | None = None, model: str | None = None) -> SubagentSpec:
    return SubagentSpec(
        name="reviewer", description="d", system_prompt="Go.", provider=provider, model=model
    )


def test_a_definition_that_pins_neither_runs_where_everything_else_does():
    assert resolved_endpoint(
        _spec(), model_override=None, provider_override=None, granted=None
    ) == (None, None)


def test_the_definition_wins_when_no_operator_spoke():
    assert resolved_endpoint(
        _spec("openai", "gpt-5"), model_override=None, provider_override=None, granted=None
    ) == ("openai", "gpt-5")


def test_an_operator_overriding_both_wins():
    """The point of the override existing at all."""
    assert resolved_endpoint(
        _spec("openai", "gpt-5"),
        model_override="MiniMax-M2.5",
        provider_override="anthropic",
        granted=None,
    ) == ("anthropic", "MiniMax-M2.5")


def test_overriding_only_the_model_against_a_pinned_provider_is_refused():
    """A MiniMax model name sent to OpenAI is a 404 if you are lucky and a
    wrong-model run if you are not."""
    with pytest.raises(CapabilityError, match="overrode only its model"):
        resolved_endpoint(
            _spec("openai", "gpt-5"),
            model_override="CHEAP",
            provider_override=None,
            granted=None,
        )


def test_overriding_only_the_model_is_fine_when_nothing_is_pinned():
    """The refusal is about a mismatch, not about overriding."""
    assert resolved_endpoint(
        _spec(model="EXPENSIVE"),
        model_override="CHEAP",
        provider_override=None,
        granted=None,
    ) == (None, "CHEAP")


def test_an_endpoint_the_request_may_not_use_is_refused():
    with pytest.raises(CapabilityError, match="may not use"):
        resolved_endpoint(
            _spec("openai"), model_override=None, provider_override=None, granted=()
        )


def test_a_granted_endpoint_goes_through():
    assert resolved_endpoint(
        _spec("openai", "gpt-5"),
        model_override=None,
        provider_override=None,
        granted=("openai",),
    ) == ("openai", "gpt-5")


def test_an_operators_provider_is_clamped_like_the_definitions():
    """An override is an operator's decision, not an exemption from the grant."""
    with pytest.raises(CapabilityError, match="may not use"):
        resolved_endpoint(
            _spec(),
            model_override="m",
            provider_override="openai",
            granted=("anthropic",),
        )


# -- metadata --------------------------------------------------------------


def test_metadata_is_carried_verbatim(tmp_path):
    """Kingfisher does not interpret it. Whatever YAML made of the mapping is
    what a middleware factory will be handed."""
    definition = (
        "name: reviewer\ndescription: d\n"
        "metadata:\n  tier: gold\n  retries: 3\n  tags: [a, b]\n"
        "system_prompt: |\n  You review.\n"
    )

    spec = read_subagent(definition, tmp_path / "reviewer.yaml")

    assert spec.metadata == {"tier": "gold", "retries": 3, "tags": ["a", "b"]}


def test_metadata_defaults_to_empty(tmp_path):
    """Absent is the common case, and an empty mapping saves every reader a
    `None` check for a field that means "nothing extra"."""
    spec = read_subagent(MINIMAL, tmp_path / "reviewer.yaml")

    assert spec.metadata == {}


@pytest.mark.parametrize("written", ["metadata: gold", "metadata: [a, b]", "metadata: 3"])
def test_metadata_must_be_a_mapping(tmp_path, written):
    """A bag with no shape cannot be looked up by key, which is the only thing
    anyone will do with it."""
    definition = f"name: reviewer\ndescription: d\n{written}\nsystem_prompt: |\n  You review.\n"

    with pytest.raises(SubagentError, match="metadata"):
        read_subagent(definition, tmp_path / "reviewer.yaml")


def test_empty_metadata_is_allowed(tmp_path):
    """`metadata:` with nothing under it is not the same mistake as a blank
    required field -- it is a caller who has none, spelled out."""
    definition = "name: reviewer\ndescription: d\nmetadata:\nsystem_prompt: |\n  You review.\n"

    assert read_subagent(definition, tmp_path / "reviewer.yaml").metadata == {}


def test_metadata_survives_loading_the_catalogue(tmp_path):
    """The only consumer there is. A deployment reads its own keys by loading
    the directory itself -- no seam into a run, and none needed.
    """
    directory = tmp_path / "subagents"
    directory.mkdir()
    (directory / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: d\n"
        "metadata:\n  owner: platform-team\n"
        "system_prompt: |\n  You review.\n",
        encoding="utf-8",
    )
    (directory / "namer.yaml").write_text(
        "name: namer\ndescription: d\nsystem_prompt: |\n  One word.\n", encoding="utf-8"
    )

    owners = {
        name: spec.metadata.get("owner", "unowned")
        for name, spec in load_all(directory).items()
    }

    assert owners == {"reviewer": "platform-team", "namer": "unowned"}
