"""One line of doctor's report, and the verdict it carries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: `fail` means a run will break, or will not do what its definitions say -- an agent
#: naming a moved tool runs without it. `warn` means it will, and something about it is
#: worth knowing -- an unconfined shell runs fine.
Verdict = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    """One question, its answer, and what to do about it."""

    name: str
    verdict: Verdict
    detail: str
    remedy: str = ""
