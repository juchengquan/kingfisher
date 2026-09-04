"""Naming a session store from configuration, rather than passing an object.

`Config.session_store` could only ever be a directory, and its own comment said
why: *"a deployment reaching for that passes an object rather than a path"*. That
only works for somebody who has stopped using the two entry points kingfisher
ships -- `presentation/cli/__main__.py` builds its own `Kingfisher` and there is
nowhere to point it. These cover the setting that replaces that advice, and the
four ways of naming one wrongly.

The factories live here rather than in a written-out module because `__name__`
is a module path this process can already import, whatever pytest called it. A
test that wrote a file and prepended `sys.path` would be testing importlib.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from kingfisher.application.config import Environment
from kingfisher.application.service import _session_store
from kingfisher.config import Config, ConfigError
from kingfisher.infrastructure.session_store import LocalSessionStore, store_named
from tests.conftest import FAKE_CATALOGUE

HERE = __name__


class Recording:
    """A `SessionStore` that satisfies the port and remembers nothing else.

    `Mapping` rather than `dict` in both signatures, and that is the port's rule
    rather than a preference: a protocol's parameters are contravariant, so an
    implementation narrowing `files` to `dict` does not satisfy it. `ty` caught
    exactly that on the first draft of this file, which is the check earning its
    place -- a double that does not really implement the port proves nothing
    about a deployment's store that does.
    """

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
    """A factory that forgot to return, which is the likeliest way to get this
    wrong and the one an annotation would not catch in an untyped deployment."""


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
    """The whole feature in one line: a string became a store.

    Called, not merely imported -- the convention is a factory rather than a
    ready-made instance precisely so that construction happens when kingfisher
    is wired rather than as an import side effect.
    """
    store = store_named(f"{HERE}:make_store")

    assert isinstance(store, Recording)
    assert built == [store]


def test_a_class_with_no_arguments_is_a_factory_too():
    """Zero-argument callable, not zero-argument *function*. A deployment whose
    store needs nothing at construction should not have to write a wrapper that
    only says `return Recording()`."""
    assert isinstance(store_named(f"{HERE}:Recording"), Recording)


def test_the_factory_is_called_once_per_resolution():
    """A store may hold a connection pool, and resolving twice would open two
    while only one is ever used. `Kingfisher.__init__` resolves once; this pins
    that `store_named` does not call again on its own."""
    store_named(f"{HERE}:make_store")

    assert len(built) == 1


# -- the four ways of naming one wrongly -----------------------------------


@pytest.mark.parametrize("spec", ["mystores", "", ":make_store", f"{HERE}:"])
def test_a_spec_that_does_not_name_two_things_is_refused(spec):
    """`module:name`, both halves present. A bare module is the mistake worth
    naming: it looks like a plausible setting and there is nothing in it to
    call."""
    with pytest.raises(ConfigError, match="does not name anything"):
        store_named(spec)


def test_a_module_that_will_not_import_is_refused():
    """The ordinary operator error -- a package that is not installed in this
    image -- and it has to say which module, because the setting is a string
    nobody sees in a traceback."""
    with pytest.raises(ConfigError, match="cannot be imported"):
        store_named("not_a_real_package_anybody_installed:build")


def test_an_attribute_that_is_not_there_is_refused():
    """A renamed factory, or a typo in half of the string. The module imported
    fine, so nothing else would have complained."""
    with pytest.raises(ConfigError, match="which does not define it"):
        store_named(f"{HERE}:no_such_factory")


def test_a_factory_returning_the_wrong_shape_is_refused():
    """Why `SessionStore` gained `runtime_checkable`.

    A factory that forgets to return gives `None`, and without this the failure
    arrives at the first turn that tried to save anything -- as an
    `AttributeError` on a variable set at startup, a long way from the setting
    that caused it.
    """
    with pytest.raises(ConfigError, match="returned NoneType -- not a SessionStore"):
        store_named(f"{HERE}:make_nothing")


def test_a_factory_that_raises_is_left_alone():
    """The line between kingfisher's job and the deployment's.

    Kingfisher checks the *name*; the deployment owns the *building*. A store
    that cannot reach its bucket raises something the deployment's own handling
    may know, and replacing it with a `ConfigError` about configuration would
    describe something that is not what went wrong. `store_named` is already on
    the traceback saying which setting reached it.
    """
    with pytest.raises(BoomError, match=NO_CREDENTIALS):
        store_named(f"{HERE}:explode")


# -- which store a deployment gets -----------------------------------------


def test_a_supplied_store_wins_over_a_named_one(workspace):
    """Injected, or derived from configuration, or nothing -- the order the
    catalogue already follows. Whoever passed an object knows more than the
    environment does, and a deployment that does both is not making a mistake:
    it is overriding its own default, which is what a constructor argument is
    for."""
    supplied = Recording()

    got = _session_store(supplied, config(workspace, session_store_factory=f"{HERE}:make_store"))

    assert got is supplied
    assert built == []


def test_a_named_factory_is_what_the_service_wires(workspace):
    """The end of the wire, and the reason this is a setting at all: nothing was
    passed to `_session_store` but configuration, and a store came out."""
    got = _session_store(None, config(workspace, session_store_factory=f"{HERE}:make_store"))

    assert isinstance(got, Recording)


def test_a_directory_still_builds_the_local_store(workspace, tmp_path):
    """The path that existed before this, unchanged. A deployment keeping
    sessions on this host names a directory and gets the adapter."""
    got = _session_store(None, config(workspace, session_store=tmp_path / "kept"))

    assert isinstance(got, LocalSessionStore)


def test_wiring_neither_is_not_an_error(workspace):
    """`None` is a real answer and the common one: the session directory is the
    only copy, which is correct wherever the host may keep data."""
    assert _session_store(None, config(workspace)) is None


# -- saying it twice -------------------------------------------------------


def test_naming_a_store_twice_is_refused(workspace, tmp_path):
    """Refused rather than resolved by precedence.

    Preferring one silently would leave a deployment's sessions in the directory
    it stopped meaning to use, and nothing would say so until somebody went
    looking for a session that is somewhere else. The message names both
    variables because the fix is to unset one and the reader has to know which
    two are in play.
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
    """Held on `Config` rather than in `config_from_env`, so a config assembled
    in Python obeys the same rule. The environment is the common path and not
    the only one -- tests build these directly, and so does anyone embedding the
    library."""
    with pytest.raises(ConfigError):
        Config(
            workspace=workspace,
            models=FAKE_CATALOGUE,
            session_store=tmp_path / "kept",
            session_store_factory=f"{HERE}:make_store",
        )


# -- reading it from the environment ---------------------------------------


def test_the_variable_reaches_the_config():
    """The reading, on its own. Nothing else in this file would notice the field
    being read from the wrong name."""
    read = Environment({"KINGFISHER_SESSION_STORE_FACTORY": " mystores:build "})

    assert read.optional_text("KINGFISHER_SESSION_STORE_FACTORY") == "mystores:build"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_variable_set_to_nothing_named_nothing(value):
    """`KINGFISHER_SESSION_STORE_FACTORY=` is a deployment that configured no
    factory. Letting `""` through would make it one that named a factory and
    cannot be told which -- and, worse, one whose directory setting is now
    refused as a double configuration."""
    read = Environment({"KINGFISHER_SESSION_STORE_FACTORY": value})

    assert read.optional_text("KINGFISHER_SESSION_STORE_FACTORY") is None
