"""Naming a session store from configuration, rather than passing an object."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from kingfisher.application.config import Environment
from kingfisher.application.service import _session_store
from kingfisher.config import Config, ConfigError
from kingfisher.domain.ports import SessionStore
from kingfisher.infrastructure.session_store import LocalSessionStore
from kingfisher.infrastructure.wiring import store_named
from tests.conftest import FAKE_CATALOGUE

HERE = __name__

#: The setting these are about. `store_named` is generic now -- the file store
#: passes its own -- so the name a message must carry is an argument, and a test
#: that spelled it at every call would stop checking that the caller passes it.
SETTING = "KINGFISHER_SESSION_STORE_FACTORY"


def named(spec: str):
    """`store_named` as `_session_store` calls it."""
    return store_named(spec, setting=SETTING, port=SessionStore)


class Recording:
    """A `SessionStore` that satisfies the port and remembers nothing else."""

    def __init__(self) -> None:
        self.kept: dict[str, dict[str, bytes]] = {}

    def fetch(self, session_id: str) -> Mapping[str, bytes]:
        return self.kept.get(session_id, {})

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        self.kept.setdefault(session_id, {}).update(files)

    def knows(self, session_id: str) -> bool:
        return session_id in self.kept

    def forget(self, session_id: str) -> None:
        self.kept.pop(session_id, None)


class BoomError(RuntimeError):
    """What a deployment's own factory raises when its credentials are missing."""


#: Assigned rather than written at the `raise`, which `TRY003` refuses.
NO_CREDENTIALS = "no credentials"

built: list[Recording] = []


def make_store() -> Recording:
    store = Recording()
    built.append(store)
    return store


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


def config(workspace: Path, **kwargs) -> Config:
    return Config(workspace=workspace, models=FAKE_CATALOGUE, **kwargs)


# -- what a named factory does ---------------------------------------------


def test_a_named_factory_is_called_and_its_store_returned():
    """The whole feature in one line: a string became a store."""
    store = named(f"{HERE}:make_store")

    assert isinstance(store, Recording)
    assert built == [store]


def test_a_class_with_no_arguments_is_a_factory_too():
    """Zero-argument callable, not zero-argument *function*."""
    assert isinstance(named(f"{HERE}:Recording"), Recording)


def test_the_factory_is_called_once_per_resolution():
    """A store may hold a connection pool, and resolving twice would open two while only
    one is ever used.
    """
    named(f"{HERE}:make_store")

    assert len(built) == 1


# -- the four ways of naming one wrongly -----------------------------------


@pytest.mark.parametrize("spec", ["mystores", "", ":make_store", f"{HERE}:"])
def test_a_spec_that_does_not_name_two_things_is_refused(spec):
    """`module:name`, both halves present."""
    with pytest.raises(ConfigError, match="does not name anything"):
        named(spec)


def test_a_module_that_will_not_import_is_refused():
    """The ordinary operator error -- a package that is not installed in this image --
    and it has to say which module, because the setting is a string nobody sees in a
    traceback.
    """
    with pytest.raises(ConfigError, match="cannot be imported"):
        named("not_a_real_package_anybody_installed:build")


def test_an_attribute_that_is_not_there_is_refused():
    """A renamed factory, or a typo in half of the string."""
    with pytest.raises(ConfigError, match="which does not define it"):
        named(f"{HERE}:no_such_factory")


def test_a_factory_returning_the_wrong_shape_is_refused():
    """Why `SessionStore` gained `runtime_checkable`."""
    with pytest.raises(ConfigError, match="returned NoneType -- not a SessionStore"):
        named(f"{HERE}:make_nothing")


def test_a_factory_that_raises_is_left_alone():
    """The line between kingfisher's job and the deployment's."""
    with pytest.raises(BoomError, match=NO_CREDENTIALS):
        named(f"{HERE}:explode")


# -- which store a deployment gets -----------------------------------------


def test_a_supplied_store_wins_over_a_named_one(workspace):
    """Injected, or derived from configuration, or nothing -- the order the catalogue
    already follows.
    """
    supplied = Recording()

    got = _session_store(supplied, config(workspace, session_store_factory=f"{HERE}:make_store"))

    assert got is supplied
    assert built == []


def test_a_named_factory_is_what_the_service_wires(workspace):
    """The end of the wire, and the reason this is a setting at all: nothing was passed
    to `_session_store` but configuration, and a store came out.
    """
    got = _session_store(None, config(workspace, session_store_factory=f"{HERE}:make_store"))

    assert isinstance(got, Recording)


def test_a_directory_still_builds_the_local_store(workspace, tmp_path):
    """The path that existed before this, unchanged."""
    got = _session_store(None, config(workspace, session_store=tmp_path / "kept"))

    assert isinstance(got, LocalSessionStore)


def test_wiring_neither_is_not_an_error(workspace):
    """`None` is a real answer and the common one: the session directory is the only
    copy, which is correct wherever the host may keep data.
    """
    assert _session_store(None, config(workspace)) is None


# -- saying it twice -------------------------------------------------------


def test_naming_a_store_twice_is_refused(workspace, tmp_path):
    """Refused rather than resolved by precedence.

    Preferring one silently would leave a deployment's sessions in the directory it
    stopped meaning to use, and the message names both variables because the fix is to
    unset one.
    """
    with pytest.raises(ConfigError, match="configured twice") as caught:
        config(
            workspace,
            session_store=tmp_path / "kept",
            session_store_factory=f"{HERE}:make_store",
        )

    assert "KINGFISHER_SESSION_STORE" in str(caught.value)
    assert "KINGFISHER_SESSION_STORE_FACTORY" in str(caught.value)


def test_the_refusal_is_on_the_config_not_the_reader(workspace, tmp_path):
    """Held on `Config` rather than in `config_from_env`, so a config assembled in
    Python obeys the same rule.
    """
    with pytest.raises(ConfigError):
        Config(
            workspace=workspace,
            models=FAKE_CATALOGUE,
            session_store=tmp_path / "kept",
            session_store_factory=f"{HERE}:make_store",
        )


# -- reading it from the environment ---------------------------------------


def test_the_variable_reaches_the_config():
    """The reading, on its own."""
    read = Environment({"KINGFISHER_SESSION_STORE_FACTORY": " mystores:build "})

    assert read.optional_text("KINGFISHER_SESSION_STORE_FACTORY") == "mystores:build"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_variable_set_to_nothing_named_nothing(value):
    """`KINGFISHER_SESSION_STORE_FACTORY=` is a deployment that configured no factory."""
    read = Environment({"KINGFISHER_SESSION_STORE_FACTORY": value})

    assert read.optional_text("KINGFISHER_SESSION_STORE_FACTORY") is None
