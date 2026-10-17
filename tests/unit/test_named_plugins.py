"""Naming the command line's other plugins in configuration, as its sessions are."""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from kingfisher import Kingfisher
from kingfisher.cli.configured import configured_plugins
from kingfisher.core.config import ConfigError
from kingfisher.plugins import Plugins
from tests.conftest import RecordedEvents

HERE = __name__
SETTING = "KINGFISHER_PLUGINS"


def make_plugins() -> Plugins:
    return Plugins(run_events=RecordedEvents())


def make_keywords() -> dict[str, object]:
    return {"run_events": RecordedEvents()}


def test_naming_nothing_hands_on_nothing():
    """Unset, every plugin is kingfisher's own, so the command passes no keyword at all."""
    assert configured_plugins({}).keywords() == {}
    assert configured_plugins({SETTING: "  "}).keywords() == {}, "blank is unset"


def test_the_plugins_a_setting_names_are_what_the_command_hands_on():
    """A deployment whose turns record their events elsewhere had every command run
    without them, because the command had nowhere to be told."""
    wiring = configured_plugins({SETTING: f" {HERE}:make_plugins "}).keywords()

    assert set(wiring) == {"run_events"}
    assert isinstance(wiring["run_events"], RecordedEvents)


def test_a_setting_that_returns_keywords_instead_is_refused():
    """A dict of keywords looks as if it would do, and is a second spelling nothing checks."""
    with pytest.raises(ConfigError, match="not a Plugins"):
        configured_plugins({SETTING: f"{HERE}:make_keywords"})


def test_every_plugin_is_a_keyword_kingfisher_takes():
    """A field `Kingfisher` does not take would fail every command that set it."""
    taken = set(inspect.signature(Kingfisher.__init__).parameters)

    assert {field.name for field in fields(Plugins)} <= taken
