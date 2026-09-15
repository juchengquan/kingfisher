"""The `source_ids.yaml` document: the vocabulary, and what it refuses."""

from __future__ import annotations

import re

import pytest

from kingfisher.domain.access import AccessError, SourceIds, parse
from kingfisher.infrastructure import access_policy


def test_a_whole_document_parses():
    source_ids = parse(
        {"source_ids": {"A": None, "B": None, "admin": ["A", "B"]}},
        source="source_ids.yaml",
    )
    assert source_ids.names["admin"] == ("admin", "A", "B")
    assert source_ids.names["A"] == ("A",)


def test_source_ids_may_be_written_as_a_bare_list():
    """The common case covers nothing, and should not need a mapping."""
    assert parse({"source_ids": ["A", "B"]}, source="source_ids.yaml") == SourceIds(
        names={"A": ("A",), "B": ("B",)}
    )


def test_a_missing_source_ids_section_is_refused():
    with pytest.raises(AccessError, match="source_ids"):
        parse({}, source="source_ids.yaml")


def test_covering_an_undeclared_source_id_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"source_ids": {"A": ["Q"]}}, source="source_ids.yaml")


def test_a_cycle_in_what_names_cover_is_refused_naming_the_whole_loop():
    """The message names every link, because one edge does not say which to cut."""
    document = {"source_ids": {"A": ["B"], "B": ["A"]}}
    with pytest.raises(AccessError, match="A -> B -> A"):
        parse(document, source="source_ids.yaml")


def test_a_source_id_covering_itself_is_refused():
    with pytest.raises(AccessError, match="A -> A"):
        parse({"source_ids": {"A": ["A"]}}, source="source_ids.yaml")


def test_covering_is_transitive():
    document = {"source_ids": {"A": None, "B": ["A"], "C": ["B"]}}
    assert set(parse(document, source="source_ids.yaml").names["C"]) == {"A", "B", "C"}


def test_reaching_one_source_id_by_two_routes_is_not_a_cycle():
    """A diamond is a wide vocabulary, not a loop, and must not be refused."""
    document = {
        "source_ids": {
            "base": None,
            "left": ["base"],
            "right": ["base"],
            "top": ["left", "right"],
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
        {
            "source_ids": {
                "finance": None,
                "senior": None,
                "both": {"finance": None, "senior": None},
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.compounds["both"] == ("finance", "senior")
    # Declared like any other name, because `names` is what says a name exists
    # and an audience may write this one.
    assert source_ids.names["both"] == ("both",)


def test_the_shape_decides_which_operation_a_name_writes():
    """The two are told apart by brackets and nothing else, so the pair has to be read
    from one document -- a parser keying off anything but the shape passes on each alone.
    """
    source_ids = parse(
        {
            "source_ids": {
                "A": None,
                "B": None,
                "covers": ["A", "B"],
                "needs": {"A": None, "B": None},
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.names["covers"] == ("covers", "A", "B")
    assert "covers" not in source_ids.compounds
    assert source_ids.compounds["needs"] == ("A", "B")
    assert source_ids.names["needs"] == ("needs",)


def test_a_body_that_is_neither_a_list_nor_a_set_is_refused():
    """A string reads as a list of letters and a number as nothing at all; both used to
    raise `TypeError` out of a comprehension, naming neither the file nor the source id.
    """
    for written in ["A", 3, True]:
        with pytest.raises(AccessError, match="what it covers"):
            parse({"source_ids": {"A": None, "x": written}}, source="source_ids.yaml")


def test_a_requirement_with_a_value_after_a_name_is_refused():
    """`{finance: senior}` is a mapping with a value, not a set of two names. Reading it
    as a set takes `finance` and throws away what was written beside it, which is how
    the old `all_of: {finance: senior}` behaved before it was refused.
    """
    with pytest.raises(AccessError, match=r"\{a, b\}, not \{a: b\}"):
        parse({"source_ids": {"A": None, "x": {"finance": "senior"}}}, source="source_ids.yaml")


def test_the_retired_keywords_are_refused_with_the_line_to_write():
    """A deployment upgrading has a file full of policy written the old way. Read and
    dropped in silence is the failure this whole area exists to prevent, so each word is
    refused carrying the line that replaces it rather than only the objection.
    """
    for key, said in [("contains", r"x: \[A, B\]"), ("all_of", r"x: \{A, B\}")]:
        with pytest.raises(AccessError, match=said):
            parse(
                {"source_ids": {"A": None, "B": None, "x": {key: ["A", "B"]}}},
                source="source_ids.yaml",
            )


def test_a_whole_retired_vocabulary_is_refused_at_once():
    """One refusal for the file, not one per name. A message per source id makes a
    deployment restart for the next one, and the first refusal a realistic old file
    earns is a `{}` line that says nothing about the `contains` further down -- so the
    parse that reports it has to see the whole document before any name is read.
    """
    with pytest.raises(AccessError) as raised:
        parse(
            {
                "source_ids": {
                    "A": {},
                    "B": {},
                    "w": {"contains": ["A", "B"]},
                    "p": {"all_of": ["A", "B"]},
                }
            },
            source="source_ids.yaml",
        )

    said = str(raised.value)
    assert "4 source id(s)" in said
    # Each line pairs what the file says with what to write in its place, so the
    # remedy is the message rather than something to work out from it.
    for was, now in [
        (r"A: \{\}", "A:"),
        (r"B: \{\}", "B:"),
        (r"w: \{contains: \[A, B\]\}", r"w: \[A, B\]"),
        (r"p: \{all_of: \[A, B\]\}", r"p: \{A, B\}"),
    ]:
        assert re.search(rf"{was}\s+->\s+{now}$", said, re.MULTILINE), (
            f"{was} -> {now} missing from:\n{said}"
        )


def test_a_legal_set_of_names_that_look_like_keywords_is_not_a_retired_spelling():
    """`{contains, all_of}` is two source ids in a set, and the value is what separates
    it from the keyword form -- a scan keying off the key alone refuses a legal file.
    """
    source_ids = parse(
        {"source_ids": {"contains": None, "all_of": None, "x": {"contains": None, "all_of": None}}},
        source="source_ids.yaml",
    )

    assert source_ids.compounds["x"] == ("contains", "all_of")


def test_the_old_short_form_for_a_plain_name_is_refused():
    """`A: {}` was how a plain name was written before a body meant something. Read as a
    set it requires nothing, which admits everyone -- so it is refused rather than read
    as the widest thing the file could have said.
    """
    with pytest.raises(AccessError, match=r"A: \{\}\s+->\s+A:"):
        parse({"source_ids": {"A": {}}}, source="source_ids.yaml")


def test_an_empty_covers_list_is_refused():
    """The same unfinished edit on the other bracket, and the message points at the form
    that means what an empty list looks like it means.
    """
    with pytest.raises(AccessError, match="covers nothing"):
        parse({"source_ids": {"A": None, "x": []}}, source="source_ids.yaml")


def test_a_requirement_naming_an_undeclared_source_id_is_refused():
    """The same rule a covers list gets, on the other edge: a mistyped part is a
    requirement nobody can meet, and the symptom is a name that derives for no one.
    """
    with pytest.raises(AccessError, match="'Q'"):
        parse({"source_ids": {"A": None, "x": {"A": None, "Q": None}}}, source="source_ids.yaml")


def test_a_requirement_loop_is_refused_naming_the_whole_loop():
    """Not for termination -- the fixpoint would stop either way -- but for meaning: a
    loop can never be entered, so every name in it derives for nobody.
    """
    with pytest.raises(AccessError, match="x -> y -> x"):
        parse(
            {"source_ids": {"A": None, "x": {"y": None}, "y": {"x": None, "A": None}}},
            source="source_ids.yaml",
        )


def test_a_requirement_may_be_built_on_another():
    """Nesting, which nothing implements: a compound is held once its parts are, and a
    part may itself be one.
    """
    source_ids = parse(
        {
            "source_ids": {
                "a": None,
                "b": None,
                "c": None,
                "ab": {"a": None, "b": None},
                "abc": {"ab": None, "c": None},
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.expand(["a", "b", "c"]) == frozenset({"a", "b", "c", "ab", "abc"})
    assert source_ids.expand(["a", "c"]) == frozenset({"a", "c"})


def test_a_covers_list_may_not_hand_out_a_named_compound():
    """The bypass, closed."""
    with pytest.raises(AccessError, match="derived rather than held"):
        parse(
            {
                "source_ids": {
                    "A": None,
                    "B": None,
                    "both": {"A": None, "B": None},
                    "admin": ["both"],
                }
            },
            source="source_ids.yaml",
        )


def test_a_covers_list_may_not_hand_out_an_inline_requirement():
    """The same bypass with no name to look up: writing the set straight into the list
    is what the set spelling made easy, and it is the requirement defeated by the file
    that imposes it. Refused on the shape, since there is nothing to look up.
    """
    with pytest.raises(AccessError, match="requirement rather than a source id"):
        parse(
            {"source_ids": {"A": None, "B": None, "admin": [{"A": None, "B": None}, "A"]}},
            source="source_ids.yaml",
        )


def test_the_refusal_names_the_covers_list_that_would_have_worked():
    """Naming the parts reaches the same callers and says why, so the message hands over
    the line rather than only the objection.
    """
    with pytest.raises(AccessError, match=r"cover \[A, B\] instead"):
        parse(
            {
                "source_ids": {
                    "A": None,
                    "B": None,
                    "both": {"A": None, "B": None},
                    "admin": ["both"],
                }
            },
            source="source_ids.yaml",
        )


def test_covering_the_parts_still_satisfies_the_requirement():
    """What the refusal points at has to work, or it is not a remedy."""
    source_ids = parse(
        {
            "source_ids": {
                "A": None,
                "B": None,
                "both": {"A": None, "B": None},
                "admin": ["A", "B"],
            }
        },
        source="source_ids.yaml",
    )

    assert source_ids.expand(["admin"]) == frozenset({"admin", "A", "B", "both"})
