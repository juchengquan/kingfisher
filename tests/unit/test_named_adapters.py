"""Wire formats a deployment adds through `KINGFISHER_ADAPTERS_FACTORY`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from kingfisher.application.config import config_from_env
from kingfisher.config import Adapter, ConfigError, Landing
from kingfisher.infrastructure.harness.models import ADAPTERS, build_model
from kingfisher.infrastructure.model_catalogue import adapters, load

HERE = __name__
GUIDE = Path(__file__).parents[2] / "docs" / "guides" / "configuration.md"

CATALOGUE = """
endpoints:
  gateway:
    api: chat_completions
    base_url: https://gateway.invalid/v1
    key_env: GATEWAY_API_KEY
default: a-model
models:
  a-model:
    endpoint: gateway
"""
KEYS = {"GATEWAY_API_KEY": "sk-gateway-not-real"}


def documented() -> dict[str, Any]:
    """The factory `configuration.md` tells a reader to write, run as written.

    Read from the guide rather than copied here, so the row a reader copies is the row
    that is checked -- a `Landing` wrong in the page and right in this file would pass.
    """
    text = GUIDE.read_text(encoding="utf-8")
    source = next(
        block
        for block in re.findall(r"```python\n(.*?)```", text, re.DOTALL)
        if "def adapters" in block
    )
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # noqa: S102 -- our own guide, parsed by `test_docs_index`
    return namespace["adapters"]()


def not_a_mapping() -> list[Adapter]:
    return list(ADAPTERS.values())


def not_an_adapter() -> dict[str, Any]:
    return {"chat_completions": "langchain_openai:ChatOpenAI"}


def redefines_a_shipped_one() -> dict[str, Adapter]:
    return {"anthropic": ADAPTERS["openai_responses"]}


def catalogue(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(CATALOGUE, encoding="utf-8")
    return path


def test_the_documented_row_builds_where_the_endpoint_says(tmp_path):
    """The guide's `Landing` names an attribute its class does not keep a value in."""
    models = load(catalogue(tmp_path), KEYS, adapters(f"{HERE}:documented"))

    model = build_model(*models.resolve())

    assert model.openai_api_base == "https://gateway.invalid/v1"
    assert model.use_responses_api is not True


def test_a_catalogue_naming_it_is_refused_without_the_setting(tmp_path):
    """The control: without this, the test above passes on a loader that accepts any
    `api` at all.
    """
    with pytest.raises(ConfigError, match="'chat_completions', which kingfisher cannot build"):
        load(catalogue(tmp_path), KEYS, adapters(None))


def test_the_setting_is_read_where_the_catalogue_loads(tmp_path):
    """Read anywhere later, `kingfisher doctor` and `run` refuse the same file a
    deployment's own program accepts, because neither can be handed an argument.
    """
    cfg = config_from_env({
        "KINGFISHER_WORKSPACE": str(tmp_path / "workspace"),
        "KINGFISHER_MODELS_FILE": str(catalogue(tmp_path)),
        "KINGFISHER_ADAPTERS_FACTORY": f"{HERE}:documented",
        **KEYS,
    })

    assert cfg.models.endpoints["gateway"].adapter == documented()["chat_completions"]


def test_a_shipped_name_is_not_replaced():
    """Replaced, every `api: anthropic` already written would move to another class
    with nothing in `models.yaml` to show it.
    """
    with pytest.raises(ConfigError, match="redefines 'anthropic'"):
        adapters(f"{HERE}:redefines_a_shipped_one")


@pytest.mark.parametrize(
    ("factory", "complaint"),
    [
        ("not_a_mapping", "returned list -- not a Mapping"),
        ("not_an_adapter", "'chat_completions' is a str"),
    ],
)
def test_a_factory_returning_the_wrong_shape_is_refused_by_name(factory, complaint):
    """A string where a row belongs would otherwise reach `build_model` and fail inside
    a turn, as an attribute error nobody can trace to the setting.
    """
    with pytest.raises(ConfigError, match=re.escape(complaint)) as refused:
        adapters(f"{HERE}:{factory}")
    assert "KINGFISHER_ADAPTERS_FACTORY" in str(refused.value)


def test_a_deployment_row_is_checked_the_way_a_shipped_one_is(tmp_path):
    """The landing check is what makes an outside row safe; a row that skipped it would
    send its requests to the vendor's default host.
    """
    row = documented()["chat_completions"]
    wrong = Adapter(row.chat_class, Landing(**{**vars(row.lands), "base_url": "model_name"}))
    models = load(catalogue(tmp_path), KEYS, {**ADAPTERS, "chat_completions": wrong})

    with pytest.raises(ConfigError, match="did not keep 'base_url'"):
        build_model(*models.resolve())
