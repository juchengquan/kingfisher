"""The `access.yaml` document: what it may say, and what it refuses.

Two halves, matching the two modules. `parse` takes decoded fields and is the
domain's; `load` reads the file and is infrastructure's. The seam is the same
one `domain.agent.parse` sits on, and the tests follow it: everything about the
*format* is asserted against mappings, and only the handful of things that are
genuinely about a file touch the disk.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.access import Access, AccessError, parse
from kingfisher.domain.capabilities import ALL
from kingfisher.infrastructure import access_policy


def test_a_whole_document_parses():
    access = parse(
        {
            "groups": {"A": {}, "B": {}, "admin": {"contains": ["A", "B"]}},
            "agents": {"assistant": ["A", "B"]},
            "subagents": {"reviewer": ["A", "B"]},
            "tools": {"sql_query": ["A"], "http_fetch": ["*"]},
        },
        source="access.yaml",
    )
    assert access.groups["admin"] == ("admin", "A", "B")
    assert access.entries["tools"]["http_fetch"] == ALL
    assert access.entries["tools"]["sql_query"] == ("A",)


def test_groups_may_be_written_as_a_bare_list():
    """The common case has no `contains`, and should not need a mapping."""
    access = parse({"groups": ["A", "B"]}, source="access.yaml")
    assert access.groups == {"A": ("A",), "B": ("B",)}


def test_a_missing_groups_section_is_refused():
    """The vocabulary is what everything else is checked against."""
    with pytest.raises(AccessError, match="groups"):
        parse({"tools": {"sql_query": ["A"]}}, source="access.yaml")


def test_an_asset_naming_an_undeclared_group_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"groups": ["A"], "tools": {"sql_query": ["A", "Q"]}}, source="access.yaml")


def test_contains_naming_an_undeclared_group_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"groups": {"A": {"contains": ["Q"]}}}, source="access.yaml")


def test_a_cycle_in_contains_is_refused_naming_the_whole_loop():
    """The message names every link, because one edge does not say which to cut."""
    document = {"groups": {"A": {"contains": ["B"]}, "B": {"contains": ["A"]}}}
    with pytest.raises(AccessError, match="A -> B -> A"):
        parse(document, source="access.yaml")


def test_a_group_containing_itself_is_refused():
    with pytest.raises(AccessError, match="A -> A"):
        parse({"groups": {"A": {"contains": ["A"]}}}, source="access.yaml")


def test_contains_is_transitive():
    document = {"groups": {"A": {}, "B": {"contains": ["A"]}, "C": {"contains": ["B"]}}}
    assert set(parse(document, source="access.yaml").groups["C"]) == {"A", "B", "C"}


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
    assert set(parse(document, source="access.yaml").groups["top"]) == {
        "top", "left", "right", "base",
    }


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(AccessError, match="tolls"):
        parse({"groups": ["A"], "tolls": {}}, source="access.yaml")


def test_a_skills_section_is_refused_with_its_own_reason():
    """Not a generic 'unknown key', which reads as 'not supported yet' and
    sends a reader looking for a workaround."""
    with pytest.raises(AccessError, match="guidance rather than a capability"):
        parse({"groups": ["A"], "skills": {"code-review": ["A"]}}, source="access.yaml")


def test_a_builtin_tools_section_is_refused_with_its_own_reason():
    with pytest.raises(AccessError, match="deepagents registers them"):
        parse({"groups": ["A"], "builtin_tools": {"execute": ["A"]}}, source="access.yaml")


def test_a_middleware_section_is_refused_with_its_own_reason():
    with pytest.raises(AccessError, match="granted rather than"):
        parse({"groups": ["A"], "middleware": {"call_cap": ["A"]}}, source="access.yaml")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    """`sql_query: A` would otherwise become the groups 'A' spelled one letter
    at a time, which is the mistake `capabilities._normalise` also refuses."""
    with pytest.raises(AccessError, match="a list of group names"):
        parse({"groups": ["A"], "tools": {"sql_query": "A"}}, source="access.yaml")


def test_a_star_mixed_with_names_is_refused():
    """It cannot mean both 'everyone' and 'these', and the file should say so."""
    with pytest.raises(AccessError, match="cannot mean both"):
        parse({"groups": ["A"], "tools": {"sql_query": ["*", "A"]}}, source="access.yaml")


def test_an_empty_audience_is_refused():
    """`sql_query: []` reads as 'nobody', which is spelled by leaving it out --
    and is far more likely to be an unfinished edit."""
    with pytest.raises(AccessError, match="leave it out"):
        parse({"groups": ["A"], "tools": {"sql_query": []}}, source="access.yaml")


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AccessError, match=r"policy\.yaml"):
        parse({"tools": {}}, source="policy.yaml")


def test_a_document_with_only_groups_is_valid():
    """A vocabulary and no entries is a policy that grants nothing, which is a
    legitimate starting point and not an error."""
    assert parse({"groups": ["A"]}, source="access.yaml") == Access(
        groups={"A": ("A",)}, entries={"agents": {}, "subagents": {}, "tools": {}}
    )


def test_an_absent_file_is_no_policy_rather_than_an_error(tmp_path):
    """Absent means the feature is off, so every deployment that predates it is
    unaffected by the code landing."""
    assert access_policy.load(tmp_path / "access.yaml") is None


def test_a_present_file_is_read(tmp_path):
    written = tmp_path / "access.yaml"
    written.write_text("groups: [A, B]\ntools:\n  sql_query: [A]\n", encoding="utf-8")
    access = access_policy.load(written)
    assert access is not None
    assert access.entries["tools"]["sql_query"] == ("A",)


def test_a_malformed_file_refuses_rather_than_starting_open(tmp_path):
    """Fail closed: a policy that will not parse must not become no policy."""
    written = tmp_path / "access.yaml"
    written.write_text("groups: [A]\ntools: {sql_query: [\n", encoding="utf-8")
    with pytest.raises(AccessError, match=r"access\.yaml"):
        access_policy.load(written)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path):
    written = tmp_path / "access.yaml"
    written.write_text("- A\n- B\n", encoding="utf-8")
    with pytest.raises(AccessError, match="mapping"):
        access_policy.load(written)


def test_an_empty_file_is_refused_rather_than_read_as_no_policy(tmp_path):
    """A file someone created and has not filled in is not the same as no file,
    and reading it as 'off' is the silent-open failure this area is about."""
    written = tmp_path / "access.yaml"
    written.write_text("", encoding="utf-8")
    with pytest.raises(AccessError, match="empty"):
        access_policy.load(written)
