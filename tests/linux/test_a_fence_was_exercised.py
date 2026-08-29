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
import warnings

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

    Deliberately `or` rather than both, and the first green run justified that
    in the direction nobody predicted. This was written expecting Landlock to
    be the half that skips, GitHub's `ubuntu-latest` image having been on a
    kernel below `REQUIRED_LANDLOCK_ABI`. That image is now on ABI 7 against
    the 6 required, so Landlock is the half that runs; bubblewrap is installed
    by the workflow and still unavailable, because the runner refuses an
    unprivileged user namespace.

    Which is the argument for `or`, made by the thing it was guessing about:
    demanding both would put this job red for a reason about the runner rather
    than about this repository, and a job that is red for something nobody can
    fix gets disabled, costing more than the coverage it was buying.

    What it does buy: whichever half is live cannot silently stop being so. If
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
def test_the_half_that_ran_is_named_in_the_log() -> None:
    """Which fence ran, said where a green run will show it.

    `-rs` already names what *skipped* and why, so a reader can infer the rest.
    Inference is what this is for avoiding: "16 skipped" does not say whether
    Landlock skipped because the kernel is old or because the wheel is missing,
    and the answer changes when the runner image moves.

    Through `warnings.warn` rather than `print`, which is what this did first
    and which reports nothing: pytest captures stdout for a passing test, so
    the line went nowhere on the only kind of run that matters. A warning lands
    in the summary without `-s`, and without turning capture off for the whole
    suite to carry one line.
    """
    warnings.warn(
        f"fence coverage: Landlock={'yes' if landlock_ready() else 'no'} "
        f"(ABI {landlock_abi()}, needs {REQUIRED_LANDLOCK_ABI}) "
        f"bubblewrap={'yes' if bubblewrap_available() else 'no'}",
        stacklevel=1,
    )
