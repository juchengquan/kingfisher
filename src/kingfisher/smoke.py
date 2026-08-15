"""The smoke task: one small, verifiable analysis with a planted anomaly.

This is the behavioural signal the design leans on — the fake-model tests prove
the wiring, and this proves the agent. Its report is promoted to a stable path
so consecutive runs produce a real `git diff` rather than two unrelated files
in two per-session directories.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from kingfisher.workspace import writable_data

SAMPLE_NAME = "sales.csv"

# Eight rows, arithmetic checkable by hand, with one deliberate anomaly:
# west has zero units in January and 240 in February.
SAMPLE_SALES_CSV = textwrap.dedent("""\
    region,month,units,unit_price
    north,2026-01,120,9.99
    north,2026-02,95,9.99
    south,2026-01,310,7.50
    south,2026-02,402,7.50
    east,2026-01,58,12.00
    east,2026-02,61,12.00
    west,2026-01,0,15.00
    west,2026-02,240,15.00
    """)

SMOKE_TASK = (
    f"Profile /data/{SAMPLE_NAME}. Report total revenue per region, and flag "
    f"anything that looks anomalous or worth a second look."
)


def seed_sample_data(workspace: Path) -> bool:
    """Write the sample dataset if it is not already there.

    Returns True if it was written. `/data` is read-only between runs, so this
    goes through `writable_data`, which restores protection afterwards.
    """
    target = Path(workspace) / "data" / SAMPLE_NAME
    if target.exists():
        return False
    with writable_data(workspace) as data:
        (data / SAMPLE_NAME).write_text(SAMPLE_SALES_CSV, encoding="utf-8")
    return True


def promote_report(run_dir: Path, workspace: Path, name: str = "smoke") -> Path | None:
    """Copy a run's report to a stable, tracked path.

    Per-session directories are not diffable against each other; a stable
    destination is what turns repeated smoke runs into a comparison you can
    read with `git diff reports/smoke.md`.
    """
    source = Path(run_dir) / "report.md"
    if not source.exists():
        return None
    destination = Path(workspace) / "reports" / f"{name}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination
