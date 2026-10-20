"""A subagent that totals dated figures by EMEA fiscal quarter, for EMEA callers only.

**The shape a deployment's own records translate into.** A deployment that keeps who
may use each subagent beside the subagent writes it here, in the dict, and nowhere
else: `analyst.yaml` grants `quarters` by its bare name and says nothing about who it is
for. A caller reaches it only by passing both checks below, and an agent naming it
cannot widen either.

**`source_ids` and `datazone` are kingfisher's, and nothing below kingfisher sees
them.** Kingfisher reads them to decide whether to build this subagent for a caller,
then hands deepagents a spec made from its own record. Which is also why this dict is
annotated as kingfisher's `PortableSubagent` rather than deepagents' `SubAgent`, as
`versions.py` is: that type has neither key, so a dict carrying them stops
type-checking as one.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from kingfisher import PortableSubagent

#: The EMEA fiscal year starts on 1 April and takes the name of the calendar year it
#: starts in, so a date's fiscal quarter is not its calendar quarter.
FIRST_MONTH = 4


@tool
def fiscal_quarter(day: str) -> str:
    """Name the EMEA fiscal quarter an ISO 8601 date (YYYY-MM-DD) falls in, as FY2026-Q1.

    The fiscal year starts on 1 April: 2026-04-01 is FY2026-Q1 and 2027-03-31 is
    FY2026-Q4.
    """
    try:
        when = dt.date.fromisoformat(day.strip())
    except ValueError:
        return f"unrecognised: {day!r} is not an ISO 8601 date (YYYY-MM-DD)"
    year = when.year if when.month >= FIRST_MONTH else when.year - 1
    return f"FY{year}-Q{(when.month - FIRST_MONTH) % 12 // 3 + 1}"


SUBAGENTS: list[PortableSubagent] = [
    {
        "name": "quarters",
        "description": (
            "Totals dated figures by EMEA fiscal quarter, where the year starts on "
            "1 April. Use when a report states EMEA figures by quarter."
        ),
        "builtin_tools": ["read_file", "ls", "glob", "grep"],
        "tools": [fiscal_quarter],
        # Who reaches it: a caller holding `sales_db` and in the `emea` datazone. Both
        # are declared in `source_ids.yaml`, and a name it does not declare stops the
        # deployment.
        "source_ids": ["sales_db"],
        # A caller in no datazone does not reach it either: forgetting to say which
        # datazone a caller is in narrows what they get rather than widening it.
        "datazone": "emea",
        "system_prompt": (
            "You total figures by EMEA fiscal quarter.\n\n"
            "Given dated figures, place every date with `fiscal_quarter` rather than "
            "working it out yourself: the EMEA year starts on 1 April, so January is "
            "the fourth quarter of the year before, and that is the mistake to avoid.\n\n"
            "Report one total per fiscal quarter, and list any date the tool could not "
            "read rather than leaving its figure out silently."
        ),
    }
]
