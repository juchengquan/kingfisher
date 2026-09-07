"""The settings that used to be `KINGFISHER_SERVER_*`, still read."""

from __future__ import annotations

import pytest
from kingfisher_service.config import PREFIX, WAS, ServiceConfig


def test_the_old_name_is_still_read():
    """A deployment that upgrades without editing anything keeps its port."""
    with pytest.warns(DeprecationWarning):
        settings = ServiceConfig.from_env({f"{WAS}PORT": "9001"})

    assert settings.port == 9001


def test_using_the_old_name_says_so():
    """Honoured *and* reported."""
    with pytest.warns(DeprecationWarning, match=f"{WAS}PORT"):
        ServiceConfig.from_env({f"{WAS}PORT": "9001"})


def test_the_warning_names_what_to_write_instead():
    """A deprecation that does not say the replacement is one the reader has to go and
    look up.
    """
    with pytest.warns(DeprecationWarning, match=f"{PREFIX}PORT"):
        ServiceConfig.from_env({f"{WAS}PORT": "9001"})


def test_the_new_name_wins_where_both_are_set():
    """Mid-migration, both are present, and the new one is there on purpose."""
    with _quiet():
        settings = ServiceConfig.from_env({f"{WAS}PORT": "9001", f"{PREFIX}PORT": "9002"})

    assert settings.port == 9002


def test_the_new_name_alone_warns_about_nothing():
    """The path every deployment ends on."""
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
