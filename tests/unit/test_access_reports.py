"""What the policy and the workspace disagree about, said once at startup.

Two halves of one rule, read in both directions: a line naming an asset that is
not there, and an asset there that no line names. Neither is fatal, and both
are the kind of drift that is otherwise discovered by a confused user months
later.

The dropping half is not cosmetic. A grant reaches `Offering.refuse_unknown`,
which refuses a name the workspace does not offer -- so a stale line left in
the resolved grant would turn every turn into a refusal rather than a report.
"""

from __future__ import annotations

from collections.abc import Mapping

from kingfisher.domain.access import Access, Audience
from kingfisher.domain.capabilities import ALL


def policy(**entries: Mapping[str, Audience]) -> Access:
    """An `Access` over a flat A/B/C vocabulary, plus anything the entries name."""
    named = {
        g
        for audience in entries.values()
        for a in audience.values()
        if isinstance(a, tuple)
        for g in a
    }
    return Access(
        groups={name: (name,) for name in sorted({"A", "B", "C"} | named)},
        entries=dict(entries),
    )


def offering(**names: list[str]) -> dict[str, list[str]]:
    """What a catalogue holds, per kind, with the kinds nobody named left empty."""
    return {"agents": [], "subagents": [], "tools": [], **names}


def test_a_clean_policy_reports_nothing():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(offering(tools=["sql_query"]))
    assert report.is_clean
    assert report.lines() == ()


def test_a_line_naming_an_asset_that_is_gone_is_reported():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(offering(tools=["sql"]))
    assert report.listed_not_offered == (("tools", "sql_query"),)


def test_a_stale_line_is_dropped_so_a_grant_never_names_a_missing_tool():
    """The reason dropping is load-bearing rather than tidy."""
    access = policy(tools={"sql_query": ("A",), "http_fetch": ("A",)})
    reconciled, _ = access.reconciled(offering(tools=["http_fetch"]))
    assert reconciled.resolve(["A"]).tools == ("http_fetch",)


def test_an_asset_no_group_can_reach_is_reported():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(offering(tools=["sql_query", "pdf_export"]))
    assert report.offered_unreachable == (("tools", "pdf_export"),)


def test_a_rename_produces_both_halves_at_once():
    """The case the two halves exist to make legible together."""
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(offering(tools=["sql"]))
    assert report.listed_not_offered == (("tools", "sql_query"),)
    assert report.offered_unreachable == (("tools", "sql"),)


def test_an_ambiguous_bare_name_is_reported_rather_than_guessed():
    """Two files defining one `fetch` are offered under their written forms, so
    a policy naming the bare name matches neither -- and must say so rather
    than silently grant whichever came first."""
    access = policy(tools={"fetch": ("A",)})
    reconciled, report = access.reconciled(
        offering(tools=["vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"])
    )
    assert report.listed_not_offered == (("tools", "fetch"),)
    assert reconciled.resolve(["A"]).tools == ()


def test_the_report_reads_as_sentences():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(offering(tools=["pdf_export"]))
    rendered = "\n".join(report.lines())
    assert "listed but not offered" in rendered
    assert "no group can reach" in rendered
    assert "sql_query" in rendered
    assert "pdf_export" in rendered


def test_reconciling_does_not_mutate_the_original():
    access = policy(tools={"sql_query": ("A",)})
    access.reconciled(offering())
    assert access.entries["tools"] == {"sql_query": ("A",)}


def test_a_star_audience_reaches_everything_so_is_never_unreachable():
    access = policy(tools={"http_fetch": ALL})
    _, report = access.reconciled(offering(tools=["http_fetch"]))
    assert report.offered_unreachable == ()


def test_every_controlled_kind_is_reconciled():
    """Agents and subagents drift the same way tools do, and a rule that only
    covered tools would be the one nobody noticed was half a rule."""
    access = policy(
        agents={"gone_agent": ("A",)},
        subagents={"gone_delegate": ("A",)},
        tools={"gone_tool": ("A",)},
    )
    _, report = access.reconciled(offering())
    assert report.listed_not_offered == (
        ("agents", "gone_agent"),
        ("subagents", "gone_delegate"),
        ("tools", "gone_tool"),
    )
