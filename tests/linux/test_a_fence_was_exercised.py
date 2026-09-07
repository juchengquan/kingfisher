"""The check that stops this shelf going green having run nothing."""

from __future__ import annotations

import os
import platform
import warnings

import pytest

from kingfisher.infrastructure.sandbox.bubblewrap import bubblewrap_available
from kingfisher.infrastructure.sandbox.confinement import (
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
    """At least one mechanism has to be live, or this shelf tested nothing."""
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
    Landlock skipped because the kernel is old or because the wheel is missing, and
    the answer changes when the runner image moves.
    """
    warnings.warn(
        f"fence coverage: Landlock={'yes' if landlock_ready() else 'no'} "
        f"(ABI {landlock_abi()}, needs {REQUIRED_LANDLOCK_ABI}) "
        f"bubblewrap={'yes' if bubblewrap_available() else 'no'}",
        stacklevel=1,
    )
