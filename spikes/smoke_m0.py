"""Superseded by `main.py`, kept as the original M0 verification entrypoint.

The sample dataset and smoke task now live in `kingfisher.smoke` so there is one
definition of each rather than two that drift apart.

    uv run main.py        # equivalent, and also promotes the report
"""

from __future__ import annotations

import sys

from main import main

if __name__ == "__main__":
    raise SystemExit(main([sys.argv[0]]))
