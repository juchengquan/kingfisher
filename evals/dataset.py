"""The smoke dataset: generated, deterministic, with exact ground truth.

Generated rather than checked in, so the ground truth is *computed* from the
rows rather than asserted alongside them. An earlier version planted 12
duplicates and declared 12; the file had 14, because random rows collide.

The messiness real data has — duplicate rows, blanks, inconsistent casing, an
outlier — is placed *outside* the region whose total is asserted, so no check
depends on how the agent chose to treat a duplicate. What the messiness tests
is detection, reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random

from kingfisher.infrastructure.workspace.permissions import writable_data

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
    rng = Random(seed)  # noqa: S311
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


def seed_sample_data(session_dir: Path) -> bool:
    """Write the sample dataset, refreshing it if the generator has changed.

    Takes a *session* directory, not the workspace. `/data` is rooted at a
    session, so a fixture written at workspace level lands where no route
    reaches it -- the seeding reports success and the agent finds `/data`
    empty.

    Self-healing rather than write-once: a fixture that silently goes stale
    while `GROUND_TRUTH` moves would turn every check into a false failure.
    """
    target = Path(session_dir) / "data" / SAMPLE_NAME
    csv, _ = build_dataset()
    if target.exists() and target.read_text(encoding="utf-8") == csv:
        return False
    with writable_data(session_dir) as data:
        (data / SAMPLE_NAME).write_text(csv, encoding="utf-8")
    return True
