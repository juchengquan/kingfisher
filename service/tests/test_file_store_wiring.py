"""Where `input_refs` and `data_refs` are fetched from, when it is not a folder.

`file_store_dir` could only ever be a directory, and its comment said what to do
instead: *"wire something else by building the `Kingfisher` yourself and handing
it to `create_app`"*. That is real advice and a narrow door -- a deployment that
would rather configure than write an entry point had nothing to set.

The setting stays on `ServiceConfig` rather than joining
`KINGFISHER_SESSION_STORE_FACTORY` on `Config`, and the asymmetry is deliberate:
a `FileStore` resolves refs, which is the vocabulary of a caller with no host
paths, and `kingfisher run` takes `--input` as a path on this machine. The port
is the server's, so the setting is too.
"""

from __future__ import annotations

import pytest
from kingfisher_service.app import _file_store
from kingfisher_service.config import PREFIX, ServiceConfig

from kingfisher import ConfigError, LocalFileStore

HERE = __name__

SETTING = f"{PREFIX}FILE_STORE_FACTORY"


class Bucket:
    """A `FileStore` that is not a directory, which is the whole point."""

    def fetch(self, file_id: str) -> dict[str, bytes]:
        return {file_id: b"from somewhere else"}


def make_bucket() -> Bucket:
    return Bucket()


def make_nothing() -> None:
    """A factory that forgot to return."""


# -- reading the setting ---------------------------------------------------


def test_the_factory_is_read_from_the_environment():
    settings = ServiceConfig.from_env({SETTING: f"{HERE}:make_bucket"})

    assert settings.file_store_factory == f"{HERE}:make_bucket"


@pytest.mark.parametrize("value", ["", "   "])
def test_a_variable_set_to_nothing_named_nothing(value):
    """`...FILE_STORE_FACTORY=` is a deployment that configured no factory.
    Letting `""` through would make it one that named a factory and cannot be
    told which -- and, worse, one whose directory setting is now refused as a
    double configuration."""
    assert ServiceConfig.from_env({SETTING: value}).file_store_factory is None


def test_naming_the_file_store_twice_is_refused(tmp_path):
    """Refused rather than resolved by precedence, for the reason
    `Config.__post_init__` refuses the session store twice: preferring one
    silently would serve a caller's refs out of the store this deployment
    stopped meaning to use, and nothing would say so."""
    with pytest.raises(ConfigError, match="configured twice") as caught:
        ServiceConfig.from_env(
            {f"{PREFIX}FILE_STORE_DIR": str(tmp_path), SETTING: f"{HERE}:make_bucket"}
        )

    assert f"{PREFIX}FILE_STORE_DIR" in str(caught.value)
    assert SETTING in str(caught.value)


def test_the_refusal_is_on_the_record_not_the_reader(tmp_path):
    """So a `ServiceConfig` assembled in Python obeys the same rule. The
    environment is the common path and not the only one."""
    with pytest.raises(ConfigError):
        ServiceConfig(file_store_dir=tmp_path, file_store_factory=f"{HERE}:make_bucket")


# -- which store the app wires ---------------------------------------------


def test_a_named_factory_is_what_the_app_wires():
    got = _file_store(ServiceConfig(file_store_factory=f"{HERE}:make_bucket"))

    assert isinstance(got, Bucket)


def test_a_directory_still_wires_the_local_store(tmp_path):
    """The path that existed before this, unchanged."""
    assert isinstance(_file_store(ServiceConfig(file_store_dir=tmp_path)), LocalFileStore)


def test_wiring_neither_is_not_an_error():
    """`None` is a real answer and the default: a request naming files by id
    then fails saying no store is wired, which is the honest reply."""
    assert _file_store(ServiceConfig()) is None


def test_a_factory_returning_the_wrong_shape_is_refused():
    """Why `FileStore` gained `runtime_checkable`. Without it the failure
    arrives at the first request that named a ref, as an `AttributeError` on a
    value set at startup."""
    settings = ServiceConfig(file_store_factory=f"{HERE}:make_nothing")

    with pytest.raises(ConfigError, match="not a FileStore"):
        _file_store(settings)


def test_the_refusal_names_this_setting_and_not_the_session_one():
    """The check that the shared resolver is actually shared.

    `store_named` was written session-store-shaped, with
    `KINGFISHER_SESSION_STORE_FACTORY` spelled into all four of its messages.
    Generalising it means the setting travels as an argument -- and nothing
    else here would notice if it had been left hardcoded, because every other
    assertion in this file would pass against the wrong variable name.
    """
    settings = ServiceConfig(file_store_factory="not_a_real_package_at_all:build")

    with pytest.raises(ConfigError) as caught:
        _file_store(settings)

    assert SETTING in str(caught.value)
    assert "SESSION_STORE" not in str(caught.value)


def test_the_refusal_says_what_the_port_wants():
    """A message naming only the type is a message that sends somebody to the
    source. `fetch` is one word and it is the whole interface."""
    settings = ServiceConfig(file_store_factory=f"{HERE}:make_nothing")

    with pytest.raises(ConfigError, match="answer to fetch"):
        _file_store(settings)
