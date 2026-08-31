"""The `groups.yaml` document: the vocabulary, and what it refuses.

Two halves, matching the two modules. `parse` takes decoded fields and is the
domain's; `load` reads the file and is infrastructure's. The seam is the same
one `domain.agent.parse` sits on.

Audiences are not here. They live in the definitions that carry them, and are
tested with those formats -- this file holds no policy at all, which is the
whole reason it is short enough to read at a glance.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.access import AccessError, Groups, parse
from kingfisher.infrastructure import access_policy


def test_a_whole_document_parses():
    groups = parse(
        {"groups": {"A": {}, "B": {}, "admin": {"contains": ["A", "B"]}}},
        source="groups.yaml",
    )
    assert groups.names["admin"] == ("admin", "A", "B")
    assert groups.names["A"] == ("A",)


def test_groups_may_be_written_as_a_bare_list():
    """The common case has no `contains`, and should not need a mapping."""
    assert parse({"groups": ["A", "B"]}, source="groups.yaml") == Groups(
        names={"A": ("A",), "B": ("B",)}
    )


def test_a_missing_groups_section_is_refused():
    with pytest.raises(AccessError, match="groups"):
        parse({}, source="groups.yaml")


def test_contains_naming_an_undeclared_group_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"groups": {"A": {"contains": ["Q"]}}}, source="groups.yaml")


def test_a_cycle_in_contains_is_refused_naming_the_whole_loop():
    """The message names every link, because one edge does not say which to cut."""
    document = {"groups": {"A": {"contains": ["B"]}, "B": {"contains": ["A"]}}}
    with pytest.raises(AccessError, match="A -> B -> A"):
        parse(document, source="groups.yaml")


def test_a_group_containing_itself_is_refused():
    with pytest.raises(AccessError, match="A -> A"):
        parse({"groups": {"A": {"contains": ["A"]}}}, source="groups.yaml")


def test_contains_is_transitive():
    document = {"groups": {"A": {}, "B": {"contains": ["A"]}, "C": {"contains": ["B"]}}}
    assert set(parse(document, source="groups.yaml").names["C"]) == {"A", "B", "C"}


def test_reaching_one_group_by_two_routes_is_not_a_cycle():
    """A diamond is a wide vocabulary, not a loop, and must not be refused."""
    document = {
        "groups": {
            "base": {},
            "left": {"contains": ["base"]},
            "right": {"contains": ["base"]},
            "top": {"contains": ["left", "right"]},
        }
    }
    assert set(parse(document, source="groups.yaml").names["top"]) == {
        "top",
        "left",
        "right",
        "base",
    }


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(AccessError, match="grops"):
        parse({"groups": ["A"], "grops": {}}, source="groups.yaml")


@pytest.mark.parametrize("section", ["agents", "subagents", "tools"])
def test_an_asset_section_says_where_audiences_went(section):
    """The central format's three sections, refused by name.

    A deployment upgrading has a file full of policy. Reading it and dropping it
    would be the quiet catastrophe -- the server would come up believing it was
    locked down -- so each one says where the audiences live now.
    """
    with pytest.raises(AccessError, match="live in the definition"):
        parse({"groups": ["A"], section: {"x": ["A"]}}, source="groups.yaml")


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AccessError, match=r"vocab\.yaml"):
        parse({}, source="vocab.yaml")


def test_an_absent_file_is_no_vocabulary_rather_than_an_error(tmp_path):
    """Absent means the feature is off, so every deployment that predates it is
    unaffected by the code landing."""
    assert access_policy.load(tmp_path / "groups.yaml") is None


def test_a_present_file_is_read(tmp_path):
    written = tmp_path / "groups.yaml"
    written.write_text("groups: [A, B]\n", encoding="utf-8")
    groups = access_policy.load(written)
    assert groups is not None
    assert set(groups.names) == {"A", "B"}


def test_a_malformed_file_refuses_rather_than_starting_open(tmp_path):
    """Fail closed: a vocabulary that will not parse must not become no
    vocabulary, which would leave every definition's audience uncheckable."""
    written = tmp_path / "groups.yaml"
    written.write_text("groups: [A\n", encoding="utf-8")
    with pytest.raises(AccessError, match=r"groups\.yaml"):
        access_policy.load(written)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path):
    written = tmp_path / "groups.yaml"
    written.write_text("- A\n- B\n", encoding="utf-8")
    with pytest.raises(AccessError, match="mapping"):
        access_policy.load(written)


def test_an_empty_file_is_refused_rather_than_read_as_nothing(tmp_path):
    """A file someone created and has not filled in is not the same as no file,
    and reading it as 'off' is the silent-open failure this area is about."""
    written = tmp_path / "groups.yaml"
    written.write_text("", encoding="utf-8")
    with pytest.raises(AccessError, match="empty"):
        access_policy.load(written)
