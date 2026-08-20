"""Superseded by the driver, kept as the original M0 verification entrypoint.

The sample dataset and smoke task now live in `evals/` so there is one
definition of each rather than two that drift apart.

    uv run tests/integration/driver.py    # equivalent, and promotes the report
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repository root, so the driver imports. A spike is run as a script, so
# `sys.path` starts at `spikes/` -- `kingfisher` resolves because the package is
# installed, and the driver is not part of it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.integration.driver import main

if __name__ == "__main__":
    raise SystemExit(main([sys.argv[0]]))
