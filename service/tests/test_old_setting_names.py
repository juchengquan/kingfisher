"""The settings that used to be `KINGFISHER_SERVER_*`, and are read no longer."""

from __future__ import annotations

import warnings

from kingfisher_service.config import PREFIX, ServiceConfig

from kingfisher.presentation.cli.health import RETIRED_PREFIX


def test_the_old_prefix_reaches_no_setting():
    """The deprecation is over, and this is the half a passing tree cannot show:
    every other test here sets the current name, so a fallback quietly restored
    would break none of them.

    Taken as `RETIRED_PREFIX` rather than spelled, because the prefix has to mean
    the same thing in two places -- here, where it does nothing, and in `doctor`,
    which is now the only thing that will tell a deployment it is still set.
    """
    settings = ServiceConfig.from_env({f"{RETIRED_PREFIX}PORT": "9001"})

    assert settings.port == ServiceConfig().port


def test_the_old_prefix_does_not_warn_either():
    """Not read *and* not mentioned: a warning would cost every unmigrated
    deployment a line on every start-up while ignoring the value anyway.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ServiceConfig.from_env({f"{RETIRED_PREFIX}PORT": "9001"})

    assert not caught, f"the old prefix was mentioned: {caught}"


def test_the_current_prefix_is_what_works():
    """The control: a port equal to the default would also be the answer if
    `from_env` had stopped reading ports altogether.
    """
    assert ServiceConfig.from_env({f"{PREFIX}PORT": "9002"}).port == 9002


def test_neither_name_falls_back_to_the_default():
    assert ServiceConfig.from_env({}).port == ServiceConfig().port
