"""Structural checks on result.json.

Two runs on identical input rewrote the prose report almost entirely — same
numbers, different words. Prose cannot be a regression signal, so these read
fields rather than diffing what the model wrote.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.dataset import GROUND_TRUTH, GroundTruth
from evals.task import ISSUE_KINDS


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


def _numeric(answer: dict, key: str, expected: int) -> Check:
    if key not in answer:
        return Check(key, ok=False, detail=f"missing from answer (expected {expected})")
    actual = answer[key]
    try:
        ok = int(actual) == expected
    except (TypeError, ValueError):
        return Check(key, ok=False, detail=f"not a number: {actual!r}")
    return Check(key, ok=ok, detail=f"{actual} (expected {expected})")


def _reported_kinds(answer: dict) -> set[str]:
    kinds: set[str] = set()
    for issue in answer.get("data_quality_issues") or []:
        raw = issue.get("kind") if isinstance(issue, dict) else issue
        if isinstance(raw, str):
            kinds.add(raw.strip().lower())
    return kinds


def _detects(answer: dict, kind: str) -> Check:
    """Exact match on a classification, so wording cannot move the result."""
    reported = _reported_kinds(answer)
    return Check(
        f"detects_{kind}",
        ok=kind in reported,
        detail="reported" if kind in reported else f"not among {sorted(reported) or 'none'}",
    )


ENVELOPE_KEYS = ("answer", "artifacts", "assumptions", "unverified")


def _find_answer(payload: dict) -> dict | None:
    """Locate the findings whether or not the envelope was honoured.

    Contract adherence is reported as its own check; conflating it with the
    analysis would let one shape mistake mask every number, which is exactly
    what happened the first time this ran.
    """
    answer = payload.get("answer")
    if isinstance(answer, dict):
        return answer
    if "row_count" in payload:  # findings written at top level
        return payload
    return None


def check_result(payload: dict, truth: GroundTruth = GROUND_TRUTH) -> list[Check]:
    """Structural assertions on result.json — immune to how the prose is worded."""
    missing = [k for k in ENVELOPE_KEYS if k not in payload]
    envelope = Check(
        "envelope",
        ok=not missing,
        detail="complete" if not missing else f"missing {', '.join(missing)}",
    )

    answer = _find_answer(payload)
    if answer is None:
        return [envelope, Check("answer_shape", ok=False, detail="no findings object found")]

    return [
        envelope,
        _numeric(answer, "row_count", truth.row_count),
        _numeric(answer, "distinct_regions", truth.distinct_regions),
        _numeric(answer, "distinct_products", truth.distinct_products),
        _numeric(answer, "total_units_north", truth.total_units_north),
        _numeric(answer, "duplicate_row_count", truth.duplicate_row_count),
        _numeric(answer, "blank_units_count", truth.blank_units_count),
        *(_detects(answer, kind) for kind in ISSUE_KINDS),
    ]
