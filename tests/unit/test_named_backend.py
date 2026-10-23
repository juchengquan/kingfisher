"""Naming the command line's backend in configuration, rather than passing an object."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.cli.configured import configured_sessions
from kingfisher.core.config import REMOVED_SETTINGS, RENAMED_SETTINGS, ConfigError
from kingfisher.core.environment import Environment
from kingfisher.core.interfaces import Sessions
from kingfisher.core.wiring import built_from_setting
from kingfisher.plugins import LocalSessions

HERE = __name__

#: The setting these are about. `built_from_setting` takes the name a message must carry
#: as an argument, so a test that spelled it at every call would stop checking
#: that the caller passes it.
SETTING = "KINGFISHER_SESSIONS"


def named(spec: str):
    """`built_from_setting` as `configured_sessions` calls it."""
    return built_from_setting(spec, setting=SETTING, interface=Sessions)


class Recording(Sessions):
    """A `Sessions` that satisfies the interface and keeps nothing."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        return None

    def sessions(self, cfg):
        return ()

    def mark_used(self, cfg, session_id):
        return None

    def size(self, cfg, session_id):
        return 0

    def delete(self, cfg, session_id):
        return None


class BoomError(RuntimeError):
    """What a deployment's own factory raises when its credentials are missing."""


#: Assigned rather than written at the `raise`, which `TRY003` refuses.
NO_CREDENTIALS = "no credentials"

built: list[Recording] = []


def make_backends() -> Recording:
    made = Recording()
    built.append(made)
    return made


def make_nothing() -> None:
    """A factory that forgot to return, which is the likeliest way to get this wrong and
    the one an annotation would not catch in an untyped deployment.
    """


def explode() -> Recording:
    raise BoomError(NO_CREDENTIALS)


@pytest.fixture(autouse=True)
def _forget_what_was_built():
    built.clear()
    yield
    built.clear()


# -- what a named factory does ---------------------------------------------


def test_a_named_factory_is_called_and_its_backends_returned():
    assert isinstance(named(f"{HERE}:make_backends"), Recording)


def test_a_class_with_no_arguments_is_a_factory_too():
    assert isinstance(named(f"{HERE}:Recording"), Recording)


def test_the_factory_is_called_once_per_resolution():
    """No caching here: a deployment wanting one instance can return one."""
    named(f"{HERE}:make_backends")
    named(f"{HERE}:make_backends")

    assert len(built) == 2


def test_a_spec_that_does_not_name_two_things_is_refused():
    with pytest.raises(ConfigError, match="module:name") as caught:
        named("no-colon-here")

    assert SETTING in str(caught.value)


def test_a_module_that_will_not_import_is_refused():
    """An `ImportError` from a module somebody named in an environment variable is a
    configuration mistake, and the traceback would not say which variable.
    """
    with pytest.raises(ConfigError, match="cannot be imported") as caught:
        named("kingfisher_no_such_module_anywhere:build")

    assert SETTING in str(caught.value)


def test_an_attribute_that_is_not_there_is_refused():
    with pytest.raises(ConfigError, match="does not define"):
        named(f"{HERE}:no_such_factory")


def test_a_factory_returning_the_wrong_shape_is_refused():
    """What was returned, and what it has to answer to."""
    with pytest.raises(ConfigError, match="NoneType") as caught:
        named(f"{HERE}:make_nothing")

    assert "sessions" in str(caught.value)


def test_a_factory_that_raises_is_left_alone():
    """Its own exception, with its own traceback: that is the deployment's code failing
    at its own job, not a naming mistake.
    """
    with pytest.raises(BoomError, match=NO_CREDENTIALS):
        named(f"{HERE}:explode")


# -- what the command line gets ----------------------------------------------


def test_naming_nothing_is_the_default():
    assert type(configured_sessions({})) is LocalSessions
    assert type(configured_sessions({SETTING: "  "})) is LocalSessions, "blank is unset"


def test_a_named_factory_is_what_the_command_line_runs_on():
    assert isinstance(configured_sessions({SETTING: f" {HERE}:make_backends "}), Recording)


# -- the settings this replaced -----------------------------------------------


@pytest.mark.parametrize(("removed", "instead"), REMOVED_SETTINGS.items())
def test_a_removed_setting_is_refused_rather_than_ignored(tmp_path: Path, removed, instead):
    """Ignored, what it did would change with nothing said: sessions a deployment kept
    elsewhere lost on upgrade, or a request naming no session run in `default` rather
    than the one the deployment chose.
    """
    read = Environment({"KINGFISHER_WORKSPACE": str(tmp_path), removed: "anything"})

    with pytest.raises(ConfigError, match=f"{removed} was removed") as refused:
        read.config()
    assert instead in str(refused.value)


def test_the_command_plugin_setting_is_refused_rather_than_ignored(tmp_path: Path):
    """Its run events, unused results and PDF handling would stop applying with nothing
    said: the command takes no plugins now. Named, since the rule above passes for
    whatever the table holds."""
    read = Environment({"KINGFISHER_WORKSPACE": str(tmp_path), "KINGFISHER_PLUGINS": "a.b:c"})

    with pytest.raises(ConfigError, match=r"KINGFISHER_PLUGINS was removed.*takes no plugins"):
        read.config()


@pytest.mark.parametrize(("old", "new"), RENAMED_SETTINGS.items())
def test_a_renamed_setting_is_refused_under_its_old_name(tmp_path: Path, old, new):
    """Ignored, the command line ran on the local sessions instead of the ones
    the deployment named -- its agent's shell on this host, `reap` looking elsewhere.
    """
    read = Environment({"KINGFISHER_WORKSPACE": str(tmp_path), old: f"{HERE}:make_backends"})

    with pytest.raises(ConfigError, match=rf"{old} was renamed {new}"):
        read.config()


def test_the_default_satisfies_the_interface_it_is_the_default_for():
    assert isinstance(LocalSessions(), Sessions)
