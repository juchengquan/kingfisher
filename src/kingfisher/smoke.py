"""The smoke task: one analysis with exact ground truth, checked structurally.

Two runs on identical input rewrote the prose report almost entirely — same
numbers, different words. Prose cannot be a regression signal. So the task
specifies an answer contract, and the check reads `result.json`'s fields
rather than diffing what the model wrote.

The dataset is generated deterministically and carries the messiness real data
has — duplicate rows, blank values, inconsistent casing, an outlier — while
every asserted quantity stays unambiguous:

  - the messiness is placed *outside* the region whose total is asserted, so no
    check depends on how the agent chose to treat a duplicate or a blank
  - what the messiness tests is *detection*, reported separately

Task-specific instructions live in the task, never in the system prompt: a
general agent's base prompt should read the same whatever the project is.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random

from kingfisher.workspace import writable_data

SAMPLE_NAME = "orders.csv"

REGIONS = ("north", "south", "east", "west")
PRODUCTS = ("widget", "gasket", "flange", "sprocket", "bearing", "coupling")
PRICES = {
    "widget": 9.99,
    "gasket": 7.50,
    "flange": 12.00,
    "sprocket": 15.00,
    "bearing": 4.25,
    "coupling": 22.10,
}
CHANNELS = ("online", "retail", "wholesale")

_START = date(2026, 1, 1)
_DAYS = 90
_ROWS = 1000
_SEED = 7

_DUPLICATE_COUNT = 12
_BLANK_COUNT = 18
_OUTLIER_UNITS = 25_000


@dataclass(frozen=True)
class GroundTruth:
    """What the file actually contains. Computed, never guessed."""

    row_count: int
    distinct_regions: int
    distinct_products: int
    total_units_north: int
    #: Rows that exactly duplicate an earlier row — `duplicated().sum()`
    #: semantics. Measured, not assumed: the random data contributes its own
    #: collisions on top of the planted ones.
    duplicate_row_count: int
    blank_units_count: int
    outlier_units: int


def build_dataset(seed: int = _SEED) -> tuple[str, GroundTruth]:
    """Generate the CSV and the exact truth about it, from one pass."""
    rng = Random(seed)
    rows: list[tuple[str, str, str, str, str, str]] = []

    for i in range(_ROWS):
        # Real calendar arithmetic: naive month/day maths produced 2026-02-29
        # and 2026-02-30, which the agent duly flagged as impossible.
        day = rng.randrange(_DAYS)
        date = (_START + timedelta(days=day)).isoformat()
        region = rng.choice(REGIONS)
        product = rng.choice(PRODUCTS)
        units = rng.randrange(1, 40)
        # Inconsistent casing and padding, so `distinct_regions` tests
        # normalisation rather than naive uniqueness.
        written_region = region
        if i % 7 == 0:
            written_region = region.upper()
        elif i % 11 == 0:
            written_region = f" {region.capitalize()} "
        rows.append(
            (date, written_region, product, str(units), f"{PRICES[product]:.2f}",
             rng.choice(CHANNELS))
        )

    # Messiness goes anywhere except north, so the asserted north total stays
    # unambiguous no matter how the agent treats duplicates and blanks.
    non_north = [i for i, r in enumerate(rows) if r[1].strip().lower() != "north"]

    for i in rng.sample(non_north, _BLANK_COUNT):
        rows[i] = (*rows[i][:3], "", *rows[i][4:])

    duplicated = [rows[i] for i in rng.sample(non_north, _DUPLICATE_COUNT)]
    rows.extend(duplicated)

    outlier_index = next(i for i in non_north if rows[i][1].strip().lower() == "south")
    rows[outlier_index] = (*rows[outlier_index][:3], str(_OUTLIER_UNITS), *rows[outlier_index][4:])

    rng.shuffle(rows)

    total_units_north = sum(
        int(r[3]) for r in rows if r[1].strip().lower() == "north" and r[3]
    )

    header = "date,region,product,units,unit_price,channel"
    csv = "\n".join([header, *(",".join(r) for r in rows)]) + "\n"

    truth = GroundTruth(
        row_count=len(rows),
        distinct_regions=len(REGIONS),
        distinct_products=len(PRODUCTS),
        total_units_north=total_units_north,
        duplicate_row_count=len(rows) - len(set(rows)),
        blank_units_count=sum(1 for r in rows if not r[3]),
        outlier_units=_OUTLIER_UNITS,
    )
    return csv, truth


GROUND_TRUTH = build_dataset()[1]

#: Fixed vocabulary for detected problems. Classifying into given categories is
#: exact to check, unlike keyword-matching prose — where "dupes" and "repeated
#: records" mean the same thing and only one of them matches.
ISSUE_KINDS = ("duplicate_rows", "missing_values", "outlier", "inconsistent_casing")

SMOKE_TASK = f"""\
Analyse /data/{SAMPLE_NAME}.

Write result.json as a whole file in exactly this shape — note that the findings
go inside "answer", alongside the usual top-level keys:

{{
  "answer": {{
    "row_count": <int, data rows excluding the header>,
    "distinct_regions": <int, region names compared case-insensitively and stripped of surrounding whitespace>,
    "distinct_products": <int>,
    "total_units_north": <int, sum of units for the north region, case-insensitive>,
    "duplicate_row_count": <int, rows that exactly duplicate an earlier row across all columns — the count of extra copies, not the number of groups>,
    "blank_units_count": <int, rows whose units cell is empty>,
    "data_quality_issues": [
      {{"kind": <one of: {", ".join(ISSUE_KINDS)}>, "detail": <short description>}}
    ]
  }},
  "artifacts": [...],
  "assumptions": [...],
  "unverified": [...]
}}

The issue kinds mean:

- `duplicate_rows` — rows repeated exactly across every column.
- `missing_values` — cells that are empty where a value is expected.
- `outlier` — a numeric value far outside the spread of the rest of its column,
  by an order-of-magnitude gap or a standard fence such as IQR. Use your own
  judgement about which rule fits and say which you used.
- `inconsistent_casing` — the same logical value written differently, by
  capitalisation or surrounding whitespace.

Include one entry per kind you actually find; omit kinds that do not apply.
Report the same findings in prose in report.md."""


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


def seed_sample_data(workspace: Path) -> bool:
    """Write the sample dataset, refreshing it if the generator has changed.

    Self-healing rather than write-once: a fixture that silently goes stale
    while `GROUND_TRUTH` moves would turn every check into a false failure.
    """
    target = Path(workspace) / "data" / SAMPLE_NAME
    csv, _ = build_dataset()
    if target.exists() and target.read_text(encoding="utf-8") == csv:
        return False
    with writable_data(workspace) as data:
        (data / SAMPLE_NAME).write_text(csv, encoding="utf-8")
    return True


def load_result(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def promote_report(run_dir: Path, workspace: Path, name: str = "smoke") -> Path | None:
    """Copy a run's report to a stable, tracked path.

    Still useful for reading run-over-run, but the pass/fail signal is
    `check_result`, not this diff.
    """
    source = Path(run_dir) / "report.md"
    if not source.exists():
        return None
    destination = Path(workspace) / "reports" / f"{name}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination
