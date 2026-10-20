"""The `source_ids.yaml` document: the vocabulary, and what it refuses."""

from __future__ import annotations

import pytest

from kingfisher.core.access.policy import load_access_policy, parse
from kingfisher.core.access.source_ids import AccessError, SourceIds


def test_a_whole_document_parses():
    source_ids = parse(
        {"source_ids": ["A", "B"], "datazones": ["emea"]}, source="source_ids.yaml"
    )
    assert source_ids == SourceIds(names=frozenset({"A", "B"}), datazones=frozenset({"emea"}))


def test_a_missing_source_ids_section_is_refused():
    with pytest.raises(AccessError, match="source_ids"):
        parse({}, source="source_ids.yaml")


@pytest.mark.parametrize(
    "written",
    [
        {"A": None, "B": None},
        {"A": None, "admin": ["A"]},
        {"A": None, "both": {"A": None, "B": None}},
        ["A", {"admin": ["A"]}],
        ["A", ["A", "B"]],
    ],
    ids=["mapping", "covers", "requires", "item-with-covers", "item-a-list"],
)
def test_a_name_written_with_a_body_is_refused_saying_what_replaced_it(written):
    """A body was how a name covered or required others. Read as a name it would be one
    nobody holds, or one covering nothing it says; refused, carrying where each moved."""
    with pytest.raises(AccessError, match="a caller sends the names a role covers") as raised:
        parse({"source_ids": written}, source="source_ids.yaml")
    assert "{a, b}" in str(raised.value)


def test_a_datazone_written_with_a_body_is_refused():
    with pytest.raises(AccessError, match="'datazones' is a list of names"):
        parse({"source_ids": ["A"], "datazones": {"emea": ["A"]}}, source="source_ids.yaml")


@pytest.mark.parametrize("written", ["A", 3])
def test_a_section_that_is_not_a_list_is_refused(written):
    with pytest.raises(AccessError, match=r"write source_ids: \["):
        parse({"source_ids": written}, source="source_ids.yaml")


def test_a_name_listed_twice_is_refused():
    with pytest.raises(AccessError, match="'A' twice"):
        parse({"source_ids": ["A", "B", "A"]}, source="source_ids.yaml")


def test_an_empty_name_is_refused():
    with pytest.raises(AccessError, match="empty name"):
        parse({"source_ids": ["A", None]}, source="source_ids.yaml")


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
    assert load_access_policy(tmp_path / "source_ids.yaml") is None


def test_a_present_file_is_read(tmp_path):
    written = tmp_path / "source_ids.yaml"
    written.write_text("source_ids: [A, B]\n", encoding="utf-8")
    source_ids = load_access_policy(written)
    assert source_ids is not None
    assert set(source_ids.names) == {"A", "B"}


def test_a_malformed_file_refuses_rather_than_starting_open(tmp_path):
    """Fail closed: a vocabulary that will not parse must not become no vocabulary,
    which would leave every definition's audience uncheckable.
    """
    written = tmp_path / "source_ids.yaml"
    written.write_text("source_ids: [A\n", encoding="utf-8")
    with pytest.raises(AccessError, match=r"source_ids\.yaml"):
        load_access_policy(written)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path):
    written = tmp_path / "source_ids.yaml"
    written.write_text("- A\n- B\n", encoding="utf-8")
    with pytest.raises(AccessError, match="mapping"):
        load_access_policy(written)


def test_an_empty_file_is_refused_rather_than_read_as_nothing(tmp_path):
    """A file someone created and has not filled in is not the same as no file, and
    reading it as 'off' is the silent-open failure this area is about.
    """
    written = tmp_path / "source_ids.yaml"
    written.write_text("", encoding="utf-8")
    with pytest.raises(AccessError, match="empty"):
        load_access_policy(written)
