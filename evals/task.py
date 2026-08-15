"""The smoke task text.

Task-specific instructions live in the task, never in the system prompt: a
general agent's base prompt should read the same whatever the project is.
The task names its own output files, which is why the framework does not.
"""

from __future__ import annotations

from evals.dataset import SAMPLE_NAME

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
