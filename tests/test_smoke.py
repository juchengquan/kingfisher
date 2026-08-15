from __future__ import annotations

import csv
import io

from kingfisher.smoke import (
    GROUND_TRUTH,
    ISSUE_KINDS,
    SMOKE_TASK,
    build_dataset,
    check_result,
)


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_dataset_is_deterministic():
    """A regression signal is worthless if the fixture moves under it."""
    first, truth_a = build_dataset()
    second, truth_b = build_dataset()
    assert first == second
    assert truth_a == truth_b


def test_ground_truth_matches_the_generated_file():
    """Truth is computed from the same pass that writes the rows — verify by
    re-parsing, so a generator bug cannot quietly redefine correctness."""
    text, truth = build_dataset()
    rows = _rows(text)

    assert len(rows) == truth.row_count
    assert len({r["region"].strip().lower() for r in rows}) == truth.distinct_regions
    assert len({r["product"] for r in rows}) == truth.distinct_products
    assert sum(1 for r in rows if not r["units"]) == truth.blank_units_count
    assert len(rows) - len({tuple(r.values()) for r in rows}) == truth.duplicate_row_count
    assert (
        sum(int(r["units"]) for r in rows if r["region"].strip().lower() == "north" and r["units"])
        == truth.total_units_north
    )


def test_messiness_avoids_the_asserted_region():
    """North's total must not depend on how the agent treats dupes or blanks."""
    text, _ = build_dataset()
    north = [r for r in _rows(text) if r["region"].strip().lower() == "north"]

    assert all(r["units"] for r in north), "a blank landed in north"
    assert len({tuple(r.values()) for r in north}) == len(north), "a duplicate landed in north"


def test_dataset_carries_real_messiness():
    text, truth = build_dataset()
    rows = _rows(text)

    assert len({r["region"] for r in rows}) > truth.distinct_regions, "no casing variants"
    assert any(int(r["units"]) >= truth.outlier_units for r in rows if r["units"])
    assert len({tuple(r.values()) for r in rows}) < len(rows), "no duplicate rows"


def test_task_states_its_own_output_contract():
    """Task-specific shape belongs in the task, not the system prompt."""
    for key in ("row_count", "distinct_regions", "total_units_north", "data_quality_issues"):
        assert key in SMOKE_TASK


def test_checks_pass_on_a_correct_answer():
    payload = {
        "artifacts": [],
        "assumptions": [],
        "unverified": [],
        "answer": {
            "row_count": GROUND_TRUTH.row_count,
            "distinct_regions": GROUND_TRUTH.distinct_regions,
            "distinct_products": GROUND_TRUTH.distinct_products,
            "total_units_north": GROUND_TRUTH.total_units_north,
            "duplicate_row_count": GROUND_TRUTH.duplicate_row_count,
            "blank_units_count": GROUND_TRUTH.blank_units_count,
            "data_quality_issues": [
                {"kind": "duplicate_rows", "detail": "12 duplicated rows"},
                {"kind": "missing_values", "detail": "18 rows with blank units"},
                {"kind": "outlier", "detail": "one order of 25000 units"},
                {"kind": "inconsistent_casing", "detail": "REGION vs region"},
            ],
        }
    }
    assert all(c.ok for c in check_result(payload)), [str(c) for c in check_result(payload)]


def test_checks_are_immune_to_rewording():
    """The whole point: prose churn must not move the signal."""
    base = {
        "row_count": GROUND_TRUTH.row_count,
        "distinct_regions": GROUND_TRUTH.distinct_regions,
        "distinct_products": GROUND_TRUTH.distinct_products,
        "total_units_north": GROUND_TRUTH.total_units_north,
        "duplicate_row_count": GROUND_TRUTH.duplicate_row_count,
        "blank_units_count": GROUND_TRUTH.blank_units_count,
    }
    terse = {
        "answer": {
            **base,
            "data_quality_issues": [{"kind": k} for k in ISSUE_KINDS],
        }
    }
    verbose = {
        "answer": {
            **base,
            "data_quality_issues": [
                {"kind": "duplicate_rows", "detail": "There appear to be repeated records."},
                {"kind": "missing_values", "detail": "Several rows have an empty units column."},
                {"kind": "outlier", "detail": "One order is anomalously large."},
                {"kind": "inconsistent_casing", "detail": "Region values vary in capitalization."},
            ],
        }
    }
    assert [c.ok for c in check_result(terse)] == [c.ok for c in check_result(verbose)]


def test_a_wrong_number_fails_loudly():
    payload = {"answer": {"row_count": 1, "data_quality_issues": []}}
    checks = {c.name: c for c in check_result(payload)}

    assert not checks["row_count"].ok
    assert str(GROUND_TRUTH.row_count) in checks["row_count"].detail
    assert not checks["total_units_north"].ok
    assert "missing" in checks["total_units_north"].detail


def test_a_non_object_answer_fails_cleanly():
    checks = {c.name: c for c in check_result({"answer": "1012 rows"})}
    assert not checks["answer_shape"].ok


def test_findings_at_top_level_still_get_analysed():
    """A shape mistake must not mask every number — the first live run had
    perfect analysis and reported 0/1 because the envelope was missing."""
    payload = {
        "row_count": GROUND_TRUTH.row_count,
        "distinct_regions": GROUND_TRUTH.distinct_regions,
        "distinct_products": GROUND_TRUTH.distinct_products,
        "total_units_north": GROUND_TRUTH.total_units_north,
        "duplicate_row_count": GROUND_TRUTH.duplicate_row_count,
        "blank_units_count": GROUND_TRUTH.blank_units_count,
        "data_quality_issues": [{"kind": k} for k in ISSUE_KINDS],
    }
    checks = {c.name: c for c in check_result(payload)}

    assert not checks["envelope"].ok, "the contract violation is still reported"
    assert checks["row_count"].ok, "but the analysis is still graded"
    assert checks["total_units_north"].ok
