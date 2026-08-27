"""The check that stops this shelf going green having run nothing.

Every test beside this one skips itself when the mechanism it covers is absent
-- `needs_landlock` on a kernel below the ABI, `needs_bubblewrap` in a container
that cannot make a user namespace. That is right for a developer's machine,
where the alternative is a red suite for a boundary they are not working on.

It is wrong for the job that exists to cover them. `pytest` exits 0 on a run
that skipped everything, so a Linux job whose kernel or container quietly
stopped offering a fence would report success for having tested no fence at
all -- which is the failure this whole area keeps having, and which the macOS
job's own comment describes: a boundary "would go unchecked by CI entirely,
which is the half most worth checking".

So on CI, and only on CI, "no fence here" is a failure rather than a skip.
"""

from __future__ import annotations

import os
import platform

import pytest

from kingfisher.infrastructure.bubblewrap import bubblewrap_available
from kingfisher.infrastructure.confinement import (
    REQUIRED_LANDLOCK_ABI,
    landlock_abi,
    landlock_ready,
)

#: The same signal `needs_a_real_toolchain` reads in `test_confinement`. A
#: developer running the suite is told nothing; a runner is held to the reason
#: it was added.
on_ci = os.environ.get("CI") == "true"


@pytest.mark.skipif(platform.system() != "Linux", reason="the fences are Linux's")
@pytest.mark.skipif(not on_ci, reason="a developer's machine owes this nothing")
def test_ci_ran_against_a_real_fence() -> None:
    """At least one mechanism has to be live, or this shelf tested nothing.

    Deliberately `or` rather than both. GitHub's `ubuntu-latest` image has been
    on a kernel below `REQUIRED_LANDLOCK_ABI`, so demanding Landlock would make
    the job red for a reason that is about the runner rather than about this
    repository -- and a job that is red for something nobody can fix gets
    disabled, which costs more than the coverage it was buying.

    What it does buy: the bubblewrap escapes cannot silently stop running. If
    the day comes that neither works on the runner, this says so in one line
    instead of `0 failed` over sixteen skips.
    """
    landlock = landlock_ready()
    bwrap = bubblewrap_available()

    assert landlock or bwrap, (
        "no fence is available on this runner, so every escape test on this "
        f"shelf skipped and the job proved nothing. Landlock ABI here is "
        f"{landlock_abi()} against the {REQUIRED_LANDLOCK_ABI} a full ruleset "
        "needs, and bwrap is missing or cannot make a user namespace. Fix the "
        "runner or drop the job -- do not leave it green"
    )


@pytest.mark.skipif(platform.system() != "Linux", reason="the fences are Linux's")
@pytest.mark.skipif(not on_ci, reason="a developer's machine owes this nothing")
def test_the_kernel_is_reported_whichever_fence_ran() -> None:
    """Not an assertion -- a record, printed into the job's log.

    Which half ran is the thing a reader of a green Linux job most wants and
    cannot otherwise get: "16 skipped" does not say whether Landlock skipped
    because the kernel is old or because the wheel is missing. When the runner
    image moves to 6.12 this is where that shows up.
    """
    print(  # noqa: T201 -- the point is the job log
        f"\nfence coverage: Landlock={'yes' if landlock_ready() else 'no'} "
        f"(ABI {landlock_abi()}, needs {REQUIRED_LANDLOCK_ABI}) "
        f"bubblewrap={'yes' if bubblewrap_available() else 'no'}"
    )
