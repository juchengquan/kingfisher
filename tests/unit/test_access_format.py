"""The `source_ids.yaml` document: the vocabulary, and what it refuses."""

from __future__ import annotations

import pytest

from kingfisher.domain.access import AccessError, SourceIds, parse
from kingfisher.infrastructure import access_policy


def test_a_whole_document_parses():
    source_ids = parse(
        {"source_ids": {"A": {}, "B": {}, "admin": {"contains": ["A", "B"]}}},
        source="source_ids.yaml",
    )
    assert source_ids.names["admin"] == ("admin", "A", "B")
    assert source_ids.names["A"] == ("A",)


def test_source_ids_may_be_written_as_a_bare_list():
    """The common case has no `contains`, and should not need a mapping."""
    assert parse({"source_ids": ["A", "B"]}, source="source_ids.yaml") == SourceIds(
        names={"A": ("A",), "B": ("B",)}
    )


def test_a_missing_source_ids_section_is_refused():
    with pytest.raises(AccessError, match="source_ids"):
        parse({}, source="source_ids.yaml")


def test_contains_naming_an_undeclared_source_id_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"source_ids": {"A": {"contains": ["Q"]}}}, source="source_ids.yaml")


def test_a_cycle_in_contains_is_refused_naming_the_whole_loop():
    """The message names every link, because one edge does not say which to cut."""
    document = {"source_ids": {"A": {"contains": ["B"]}, "B": {"contains": ["A"]}}}
    with pytest.raises(AccessError, match="A -> B -> A"):
        parse(document, source="source_ids.yaml")


def test_a_source_id_containing_itself_is_refused():
    with pytest.raises(AccessError, match="A -> A"):
        parse({"source_ids": {"A": {"contains": ["A"]}}}, source="source_ids.yaml")


def test_contains_is_transitive():
    document = {"source_ids": {"A": {}, "B": {"contains": ["A"]}, "C": {"contains": ["B"]}}}
    assert set(parse(document, source="source_ids.yaml").names["C"]) == {"A", "B", "C"}


def test_reaching_one_source_id_by_two_routes_is_not_a_cycle():
    """A diamond is a wide vocabulary, not a loop, and must not be refused."""
    document = {
        "source_ids": {
            "base": {},
            "left": {"contains": ["base"]},
            "right": {"contains": ["base"]},
            "top": {"contains": ["left", "right"]},
        }
    }
    assert set(parse(document, source="source_ids.yaml").names["top"]) == {
        "top",
        "left",
        "right",
        "base",
    }


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(AccessError, match="grops"):
        parse({"source_ids": ["A"], "grops": {}}, source="source_ids.yaml")


def test_an_asset_section_says_where_audiences_went():
    """The central format's three sections, refused by name."""
    for section in ["agents", "subagents", "tools"]:
        with pytest.raises(AccessError, match="live in the definition"):
            parse({"source_ids": ["A"], section: {"x": ["A"]}}, source="source_ids.yaml")


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AccessError, match=r"vocab\.yaml"):
        parse({}, source="vocab.yaml")


def test_an_absent_file_is_no_vocabulary_rather_than_an_error(tmp_path):
    """Absent means the feature is off, so every deployment that predates it is
    unaffected by the code landing.
    """
    assert access_policy.load(tmp_path / "source_ids.yaml") is None


def test_a_present_file_is_read(tmp_path):
    written = tmp_path / "source_ids.yaml"
    written.write_text("source_ids: [A, B]\n", encoding="utf-8")
    source_ids = access_policy.load(written)
    assert source_ids is not None
    assert set(source_ids.names) == {"A", "B"}


def test_a_malformed_file_refuses_rather_than_starting_open(tmp_path):
    """Fail closed: a vocabulary that will not parse must not become no vocabulary,
    which would leave every definition's audience uncheckable.
    """
    written = tmp_path / "source_ids.yaml"
    written.write_text("source_ids: [A\n", encoding="utf-8")
    with pytest.raises(AccessError, match=r"source_ids\.yaml"):
        access_policy.load(written)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path):
    written = tmp_path / "source_ids.yaml"
    written.write_text("- A\n- B\n", encoding="utf-8")
    with pytest.raises(AccessError, match="mapping"):
        access_policy.load(written)


def test_an_empty_file_is_refused_rather_than_read_as_nothing(tmp_path):
    """A file someone created and has not filled in is not the same as no file, and
    reading it as 'off' is the silent-open failure this area is about.
    """
    written = tmp_path / "source_ids.yaml"
    written.write_text("", encoding="utf-8")
    with pytest.raises(AccessError, match="empty"):
        access_policy.load(written)


# -- requiring several source ids at once ---------------------------------------


def test_a_compound_declares_what_a_caller_must_hold():
    source_ids = parse(
        {"source_ids": {"finance": {}, "senior": {}, "both": {"all_of": ["finance", "senior"]}}},
        source="source_ids.yaml",
    )

    assert source_ids.compounds["both"] == ("finance", "senior")
    # Declared like any other name, because `names` is what says a name exists
    # and an audience may write this one.
    assert source_ids.names["both"] == ("both",)


def test_a_source_id_cannot_both_grant_and_require():
    """Opposite operations: `contains` says what this name hands out, `all_of` says what
    a caller must bring.
    """
    with pytest.raises(AccessError, match="no answer"):
        parse(
            {"source_ids": {"A": {}, "B": {}, "x": {"contains": ["A"], "all_of": ["A", "B"]}}},
            source="source_ids.yaml",
        )


def test_a_list_of_names_that_is_not_a_list_is_refused():
    """A mapping is truthy and it iterates, so it was read as its keys alone.

    `all_of: {finance: senior}` became the single name `finance` and threw away
    what was written beside it, which is not a refusal and not what the author
    asked for either. The rest raised `TypeError` out of the comprehension,
    naming neither the file nor the source id.

    Both keys, because one loop reads them and only the string case was ever
    handed anything but a list.
    """
    for written in [{"finance": "senior"}, 3, True]:
        for key in ["contains", "all_of"]:
            with pytest.raises(AccessError, match="list of source ids"):
                parse({"source_ids": {"A": {}, "x": {key: written}}}, source="source_ids.yaml")


def test_a_name_written_as_a_string_still_says_it_wants_a_list():
    """The case that already had a refusal, kept: `contains: A` reads as a list of
    letters, so the message has to be about the shape rather than the name.
    """
    with pytest.raises(AccessError, match="list of source ids"):
        parse({"source_ids": {"A": {}, "x": {"contains": "A"}}}, source="source_ids.yaml")


def test_an_empty_requirement_is_refused():
    """It would require nothing and so admit everyone, which is what a plain source id
    already means -- so it is an unfinished edit, not a spelling.
    """
    with pytest.raises(AccessError, match="empty"):
        parse({"source_ids": {"A": {}, "x": {"all_of": []}}}, source="source_ids.yaml")


def test_a_requirement_naming_an_undeclared_source_id_is_refused():
    """The same rule `contains` gets, on the other edge: a mistyped part is a
    requirement nobody can meet, and the symptom is a name that derives for no one.
    """
    with pytest.raises(AccessError, match="'Q'"):
        parse({"source_ids": {"A": {}, "x": {"all_of": ["A", "Q"]}}}, source="source_ids.yaml")


def test_a_requirement_loop_is_refused_naming_the_whole_loop():
    """Not for termination -- the fixpoint would stop either way -- but for meaning: a
    loop can never be entered, so every name in it derives for nobody.
    """
    with pytest.raises(AccessError, match="x -> y -> x"):
        parse(
            {"source_ids": {"A": {}, "x": {"all_of": ["y"]}, "y": {"all_of": ["x", "A"]}}},
            source="source_ids.yaml",
        )


def test_a_requirement_may_be_built_on_another():
    """Nesting, which nothing implements: a compound is held once its parts are, and a
    part may itself be one.
    """
    source_ids = parse(
        {
            "source_ids": {
                "a": {},
                "b": {},
                "c": {},
                "ab": {"all_of": ["a", "b"]},
                "abc": {"all_of": ["ab", "c"]},
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.expand(["a", "b", "c"]) == frozenset({"a", "b", "c", "ab", "abc"})
    assert source_ids.expand(["a", "c"]) == frozenset({"a", "c"})


def test_contains_may_not_hand_out_a_compound():
    """The bypass, closed."""
    with pytest.raises(AccessError, match="derived rather than held"):
        parse(
            {
                "source_ids": {
                    "A": {},
                    "B": {},
                    "both": {"all_of": ["A", "B"]},
                    "admin": {"contains": ["both"]},
                }
            },
            source="source_ids.yaml",
        )


def test_the_refusal_names_the_contains_that_would_have_worked():
    """Naming the parts reaches the same callers and says why, so the message hands over
    the line rather than only the objection.
    """
    with pytest.raises(AccessError, match=r"`contains: \[A, B\]` instead"):
        parse(
            {
                "source_ids": {
                    "A": {},
                    "B": {},
                    "both": {"all_of": ["A", "B"]},
                    "admin": {"contains": ["both"]},
                }
            },
            source="source_ids.yaml",
        )


def test_containing_the_parts_still_satisfies_the_requirement():
    """What the refusal points at has to work, or it is not a remedy."""
    source_ids = parse(
        {
            "source_ids": {
                "A": {},
                "B": {},
                "both": {"all_of": ["A", "B"]},
                "admin": {"contains": ["A", "B"]},
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.expand(["admin"]) == frozenset({"admin", "A", "B", "both"})
