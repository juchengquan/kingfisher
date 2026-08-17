"""The settings that used to be `KINGFISHER_SERVER_*`, still read.

Renaming an environment variable is the one rename in this split that fails in
silence. A moved import stops the program and names what moved; a variable
nobody reads falls back to its default, and the service comes up on the wrong
port with nothing anywhere saying why.

So both spellings are read. The new one wins, the old one warns, and these pin
all three parts of that -- including the warning, because a compatibility path
nobody is told they are on is how the old name becomes permanent.
"""

from __future__ import annotations

import pytest
from kingfisher_service.config import PREFIX, WAS, ServiceConfig


def test_the_old_name_is_still_read():
    """A deployment that upgrades without editing anything keeps its port."""
    with pytest.warns(DeprecationWarning):
        settings = ServiceConfig.from_env({f"{WAS}PORT": "9001"})

    assert settings.port == 9001


def test_using_the_old_name_says_so():
    """Honoured *and* reported. Honoured so nothing breaks on upgrade; reported
    so this does not quietly become a second name that is load-bearing."""
    with pytest.warns(DeprecationWarning, match=f"{WAS}PORT"):
        ServiceConfig.from_env({f"{WAS}PORT": "9001"})


def test_the_warning_names_what_to_write_instead():
    """A deprecation that does not say the replacement is one the reader has to
    go and look up."""
    with pytest.warns(DeprecationWarning, match=f"{PREFIX}PORT"):
        ServiceConfig.from_env({f"{WAS}PORT": "9001"})


def test_the_new_name_wins_where_both_are_set():
    """Mid-migration, both are present, and the new one is there on purpose."""
    with _quiet():
        settings = ServiceConfig.from_env({f"{WAS}PORT": "9001", f"{PREFIX}PORT": "9002"})

    assert settings.port == 9002


def test_the_new_name_alone_warns_about_nothing():
    """The path every deployment ends on. A warning here would train people to
    ignore the one that matters."""
    with _quiet():
        settings = ServiceConfig.from_env({f"{PREFIX}PORT": "9002"})

    assert settings.port == 9002


def test_neither_name_falls_back_to_the_default():
    with _quiet():
        assert ServiceConfig.from_env({}).port == ServiceConfig().port


def _quiet():
    """Assert no deprecation is raised, which `pytest.warns` cannot say."""
    import warnings
    from contextlib import contextmanager

    @contextmanager
    def check():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            yield
        assert not [w for w in caught if issubclass(w.category, DeprecationWarning)], (
            "warned about an old name that was never used"
        )

    return check()
