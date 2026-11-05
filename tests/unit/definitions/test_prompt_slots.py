"""`system_prompt` written long: slots each caller's grant fills."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.core.access.checks import caller_holds
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import a_subagent, agent_named, declared_subagents, subagents_dir

HEAD = "name: analyst\ndescription: Answers from the data.\n"

FIGURES = (
    HEAD
    + """system_prompt:
  text: |
    You answer questions from the data.

    {{figures}}

    Always cite the quarter.
  slots:
    figures:
      - source_ids: [sales_db, sales_archive]
        text: |
          Revenue is booked net of returns.
      - source_ids: [hr_db]
        text: |
          Headcount is in FTE.
      - text: |
          Give figures as the source states them.
"""
)


def _read(text: str):
    return read(DefinitionText(text, Path("analyst.yaml")))


def _refused(text: str, match: str) -> None:
    with pytest.raises(AgentError, match=match):
        _read(text)


# -- what a caller is told --------------------------------------------------


def test_a_slot_is_filled_by_what_the_caller_holds():
    """Unfilled, the model would be sent `{{figures}}` as written."""
    told = _read(FIGURES).prompt_for(frozenset({"sales_archive"}))

    assert told == (
        "You answer questions from the data.\n\n"
        "Revenue is booked net of returns.\n\n"
        "Always cite the quarter."
    )


def test_every_matching_entry_is_sent_in_file_order():
    """Taking the first match would drop the HR line for a caller who selected both."""
    told = _read(FIGURES).prompt_for(frozenset({"hr_db", "sales_db"}))

    assert "Revenue is booked net of returns.\n\nHeadcount is in FTE." in told
    assert "as the source states" not in told


def test_the_default_is_sent_only_where_nothing_matched():
    told = _read(FIGURES).prompt_for(frozenset({"audit_log"}))

    assert "Give figures as the source states them." in told
    assert "Revenue" not in told


def test_an_empty_slot_leaves_no_gap():
    """The blank lines either side of an unfilled slot would otherwise double up."""
    spec = _read(
        HEAD
        + "system_prompt:\n  text: |\n    First.\n\n    {{zone}}\n\n    Last.\n"
        "  slots:\n    zone:\n      - datazone: finance\n        text: |\n          In EUR.\n"
    )

    assert spec.prompt_for(frozenset()) == "First.\n\nLast."
    assert spec.prompt_for(frozenset({"datazone=finance"})) == "First.\n\nIn EUR.\n\nLast."


def test_an_entry_naming_a_source_and_a_datazone_needs_both():
    """The `and` a skills entry has with its definition, so nobody learns a second meaning."""
    spec = _read(
        HEAD
        + "system_prompt:\n  text: |\n    {{audit}}\n  slots:\n    audit:\n"
        "      - source_ids: [audit_log]\n        datazone: finance\n"
        "        text: |\n          Staff names are redacted.\n"
    )

    assert spec.prompt_for(frozenset({"audit_log"})) == ""
    assert spec.prompt_for(frozenset({"datazone=finance"})) == ""
    assert spec.prompt_for(frozenset({"audit_log", "datazone=finance"})) == (
        "Staff names are redacted."
    )


def test_a_run_with_no_caller_is_told_what_any_caller_could_be():
    """`None` reaches everything for every lock, so it fills every slot as well."""
    told = _read(FIGURES).prompt_for(None)

    assert "Revenue" in told and "Headcount" in told


def test_the_short_form_is_sent_as_written():
    spec = _read(HEAD + "system_prompt: |\n  You do the work.\n")

    assert spec.slots == {}
    assert spec.prompt_for(frozenset({"sales_db"})) == "You do the work."


# -- what a definition may write --------------------------------------------


def test_a_blank_no_slot_fills_is_refused():
    """Reserved from now on: the model would be sent the braces."""
    _refused(
        HEAD + "system_prompt: |\n  Use {{figures}}.\n", r"names \{\{figures\}\}, which no slot"
    )
    _refused(
        FIGURES.replace("{{figures}}", "{{ figures }}"), r"names \{\{ figures \}\}, which no slot"
    )


def test_a_slot_placed_nowhere_is_refused():
    _refused(
        FIGURES.replace("{{figures}}", "Figures."), "defines figures, placed nowhere"
    )


def test_slots_do_not_nest():
    _refused(
        FIGURES.replace("Headcount is in FTE.", "See {{figures}}."), "slots do not nest"
    )


def test_a_slot_has_one_default():
    _refused(
        FIGURES.replace("      - source_ids: [hr_db]\n        text:", "      - text:"),
        "a slot has one default",
    )


def test_an_entry_for_everyone_is_refused_since_that_is_the_default():
    _refused(FIGURES.replace("[hr_db]", '["*"]'), "what the slot's default is for")


def test_an_unknown_key_in_an_entry_is_refused():
    _refused(FIGURES.replace("source_ids: [hr_db]", "source_id: [hr_db]"), "did you mean")


def test_a_plain_one_line_entry_is_refused_since_a_hash_cuts_it():
    """Unquoted, `text: Revenue #1 is net` loaded as `Revenue`, the rest read as a comment."""
    _refused(
        FIGURES.replace("text: |\n          Headcount is in FTE.", "text: Headcount #1 is FTE."),
        r"slots\.figures\[1\]\.text is written as a plain scalar",
    )


def test_the_long_forms_text_must_be_literal_too():
    _refused(
        FIGURES.replace("  text: |\n    You answer", "  text: >\n    You answer"),
        r"system_prompt\.text is written as '>'",
    )


def test_the_long_form_needs_its_text():
    _refused(
        HEAD + "system_prompt:\n  slots:\n    x:\n      - text: |\n          Y.\n",
        "written long needs its text",
    )


def test_a_subagent_reads_the_long_form_the_same_way():
    spec = a_subagent(FIGURES.replace("name: analyst", "name: extractor"), "extractor.yaml")

    assert "Headcount is in FTE." in spec.prompt_for(frozenset({"hr_db"}))
    with pytest.raises(SubagentError, match="which no slot fills"):
        a_subagent(HEAD + "system_prompt: |\n  {{x}}\n", "extractor.yaml")


# -- what a deployment starts with ------------------------------------------

LOCKED = """name: surveyor
description: Holds the lock that makes sales_db a name in use.
source_ids: [sales_db]
system_prompt: |
  Survey.
"""


def _stage(cfg, **files: str) -> None:
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / f"{name}.yaml").write_text(text, encoding="utf-8")


def test_a_slot_keyed_on_a_name_no_lock_uses_is_refused_at_startup(cfg):
    """`sales_bd` would never be sent, and a slot key is no lock, or it would make itself legal."""
    _stage(cfg, surveyor=LOCKED, analyst=FIGURES.replace("sales_archive", "sales_bd"))

    with pytest.raises(AgentError, match="keyed on hr_db, sales_bd, which no lock"):
        Definitions.from_config(cfg).warm()


def test_a_slot_keyed_on_names_locks_use_starts(cfg):
    """The control beside the refusal: the same file with every name locked somewhere."""
    _stage(
        cfg,
        surveyor=LOCKED.replace("[sales_db]", "[sales_db, sales_archive, hr_db]"),
        analyst=FIGURES,
    )

    Definitions.from_config(cfg).warm()


# -- what is built ----------------------------------------------------------

SUBAGENT = """name: extractor
description: Turns files into short answers.
system_prompt:
  text: |
    You turn files into short answers.
    {{figures}}
  slots:
    figures:
      - source_ids: [sales_db]
        text: |
          Revenue is booked net of returns.
"""


def test_the_agent_and_its_subagents_are_built_with_the_callers_slots(cfg):
    """Driven through the build, against the caller's grant rather than the subagent's
    reach: `extractor` holds no tool for sales_db, and a CSV the agent pulled from it is
    still net of returns."""
    _stage(
        cfg,
        surveyor=LOCKED.replace("[sales_db]", "[sales_db, sales_archive, hr_db]"),
        analyst=FIGURES + 'builtin_tools: [task]\nsubagents: [extractor]\nsource_ids: ["*"]\n',
    )
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "extractor.yaml").write_text(SUBAGENT, encoding="utf-8")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    session = kf.workspace / "sessions" / "s"
    session.mkdir(parents=True)
    ensure_session_layout(session, DEFAULT_LAYOUT)

    def built_for(held: tuple[str, ...]):
        return build_agent(
            kf.cfg,
            agent=agent_named(kf, "analyst", source_ids=held),
            held=caller_holds(kf.vocabulary, held),
            backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
            capabilities=kf.grants,
            catalogue=kf.catalogue,
            checkpointer=None,
        )

    sales, hr = built_for(("sales_db",)), built_for(("hr_db",))

    assert "Revenue is booked net of returns." in sales.system_prompt
    assert "Headcount is in FTE." in hr.system_prompt
    assert "{{" not in sales.system_prompt + hr.system_prompt
    [extractor] = declared_subagents(sales)
    assert "Revenue is booked net of returns." in extractor["system_prompt"]
    [extractor] = declared_subagents(hr)
    assert "Revenue" not in extractor["system_prompt"]
