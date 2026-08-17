"""Reading `models.yaml`.

The file decides where every prompt in a deployment goes, so the failures worth
testing are the quiet ones: a key that parses and is dropped, an endpoint that
silently vanishes, a param that looks set and is not.
"""

from __future__ import annotations

import pytest

from kingfisher.config import Config, ConfigError, Endpoint, ModelProfile, Models
from kingfisher.infrastructure.model_catalogue import load

GOOD = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_API_KEY

default: main-model

models:
  main-model:
    endpoint: gateway
  tuned:
    endpoint: gateway
    max_tokens: 2048
    timeout_s: 60
    temperature: 0.2
    top_p: 0.9
    extra:
      reasoning_effort: high
"""

KEYS = {"GATEWAY_API_KEY": "sk-gateway"}


def written(tmp_path, body: str = GOOD):
    path = tmp_path / "models.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def loaded(tmp_path, body: str = GOOD, environ=None):
    return load(written(tmp_path, body), KEYS if environ is None else environ)


# -- what it reads ---------------------------------------------------------


def test_it_reads_endpoints_models_and_the_default(tmp_path):
    catalogue = loaded(tmp_path)
    endpoints, models, default = catalogue.endpoints, catalogue.models, catalogue.default

    assert set(endpoints) == {"gateway"}
    assert endpoints["gateway"].api == "anthropic"
    assert endpoints["gateway"].api_key == "sk-gateway"
    assert set(models) == {"main-model", "tuned"}
    assert default == "main-model"


def test_a_models_key_is_the_id_sent_on_the_wire(tmp_path):
    models = loaded(tmp_path).models

    assert models["tuned"].model == "tuned"


def test_every_param_is_carried(tmp_path):
    models = loaded(tmp_path).models
    tuned = models["tuned"]

    assert (tuned.max_tokens, tuned.timeout_s) == (2048, 60)
    assert (tuned.temperature, tuned.top_p) == (0.2, 0.9)
    assert tuned.extra == {"reasoning_effort": "high"}


def test_an_unset_param_stays_unset(tmp_path):
    """Not filled in with a number kingfisher chose. `temperature` is the one
    that matters: a default would silently change every deployment."""
    models = loaded(tmp_path).models
    plain = models["main-model"]

    assert (plain.temperature, plain.top_p) == (None, None)
    assert (plain.max_tokens, plain.timeout_s) == (4096, 120)  # these do have defaults


# -- keys that parse and would otherwise be dropped ------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (GOOD.replace("default:", "defualt:"), "defualt"),
        (GOOD.replace("    max_tokens: 2048", "    max_token: 2048"), "max_token"),
        (GOOD.replace("    key_env: GATEWAY_API_KEY", "    key: GATEWAY_API_KEY"), "key"),
    ],
)
def test_a_key_this_format_does_not_define_is_refused(tmp_path, body, expected):
    """The rule `domain.subagent` states, for the same reason: ignoring a key is
    indistinguishable from honouring it. `max_token:` singular would parse, be
    dropped, and hand back the default with no error anywhere.
    """
    with pytest.raises(ConfigError, match=expected):
        loaded(tmp_path, body)


def test_extra_cannot_overrule_a_param_the_format_defines(tmp_path):
    """`Adapter.extra` already carries this rule: additive only. A row that
    could overrule a named param would silently discard a value written three
    lines above it."""
    body = GOOD.replace("      reasoning_effort: high", "      max_tokens: 1")

    with pytest.raises(ConfigError, match="already defines"):
        loaded(tmp_path, body)


# -- endpoints that are not there ------------------------------------------


def test_a_model_naming_an_undefined_endpoint_is_refused(tmp_path):
    """A mistake in the file, and distinct from an endpoint dropped for want of
    a key -- which is this machine's situation rather than the file's."""
    body = GOOD.replace("  tuned:\n    endpoint: gateway", "  tuned:\n    endpoint: typo")

    with pytest.raises(ConfigError, match="does not define"):
        loaded(tmp_path, body)


def test_an_endpoint_without_its_key_is_dropped_with_its_models(tmp_path):
    body = GOOD.replace(
        "default: main-model",
        "  other:\n    api: openai\n    base_url: https://example.invalid/v1\n"
        "    key_env: OTHER_API_KEY\n\ndefault: main-model",
    ).replace("  tuned:\n    endpoint: gateway", "  tuned:\n    endpoint: other")

    with pytest.warns(UserWarning, match="OTHER_API_KEY"):
        catalogue = loaded(tmp_path, body)
        endpoints, models = catalogue.endpoints, catalogue.models

    assert set(endpoints) == {"gateway"}
    assert set(models) == {"main-model"}


def test_the_warning_names_the_variable_not_the_endpoint(tmp_path):
    """"endpoint 'other' has no credentials" sends someone to the YAML, where
    everything looks correct. The variable name sends them somewhere useful."""
    body = GOOD.replace("key_env: GATEWAY_API_KEY", "key_env: SOMETHING_ELSE")

    with pytest.raises(ConfigError), pytest.warns(UserWarning, match="SOMETHING_ELSE"):
        loaded(tmp_path, body, environ={})


# -- the file itself -------------------------------------------------------


def test_a_missing_file_is_refused_with_an_example(tmp_path):
    with pytest.raises(ConfigError, match="no model catalogue at"):
        load(tmp_path / "absent.yaml", KEYS)


def test_malformed_yaml_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        loaded(tmp_path, "endpoints: [unclosed\n")


def test_a_missing_default_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="no 'default' model named"):
        loaded(tmp_path, GOOD.replace("default: main-model\n", ""))


def test_an_endpoint_missing_a_required_key_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="missing required key 'base_url'"):
        loaded(tmp_path, GOOD.replace("    base_url: https://example.invalid/anthropic\n", ""))


def test_a_model_missing_its_endpoint_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="missing required key 'endpoint'"):
        loaded(tmp_path, GOOD.replace("  main-model:\n    endpoint: gateway", "  main-model:"))


# -- the one duplication that stayed, and why it cannot drift --------------


def test_a_profile_keyed_by_another_name_is_refused():
    """`ModelProfile.model` is the id sent on the wire and the key is what
    everything looks up by, so a pair that disagree means a delegate asking for
    one model and a client built for another -- silently, since both names are
    real.

    `Endpoint` carries no name at all for the same reason this check exists:
    there was nothing it could say that `ModelProfile.endpoint` did not already,
    so the field went rather than gaining a guard. A profile's model id has
    nowhere else to be, so it stays and is checked.
    """
    with pytest.raises(ConfigError, match="the two cannot differ"):
        Models(
            models={"main-model": ModelProfile("something-else", "gateway")},
            endpoints={"gateway": Endpoint("anthropic", "https://example.invalid", "sk")},
            default="main-model",
        )


def test_the_loader_cannot_produce_a_mismatch(tmp_path):
    """It builds every profile from its key, so this is a guard for a fixture or
    a caller assembling one by hand -- not for `load`."""
    catalogue = loaded(tmp_path)

    assert all(name == profile.model for name, profile in catalogue.models.items())


# -- aliases ---------------------------------------------------------------

WITH_ALIASES = GOOD + """
aliases:
  cheap: tuned
  alternate: main-model
"""


def test_aliases_bind_general_names_to_models(tmp_path):
    aliases = loaded(tmp_path, WITH_ALIASES).aliases

    assert aliases == {"cheap": "tuned", "alternate": "main-model"}


def test_a_catalogue_without_aliases_binds_nothing(tmp_path):
    """Optional: a deployment naming its models directly in every definition
    never needs one."""
    aliases = loaded(tmp_path).aliases

    assert aliases == {}


def test_an_alias_binding_an_undefined_model_is_refused(tmp_path):
    """Both halves are in this document, so it is a plain contradiction rather
    than a fact about the machine -- unlike a definition naming a model, which
    is refused later and elsewhere."""
    body = WITH_ALIASES.replace("cheap: tuned", "cheap: not-a-model")

    with pytest.raises(ConfigError, match="binds 'not-a-model'"):
        loaded(tmp_path, body)


def test_an_alias_binding_nothing_is_refused(tmp_path):
    body = WITH_ALIASES.replace("cheap: tuned", "cheap:")

    with pytest.raises(ConfigError, match="binds nothing"):
        loaded(tmp_path, body)


def test_an_alias_may_not_share_a_models_name(tmp_path):
    """It would make every message about it a lie -- "no model bound to alias
    'tuned'" about a name that is plainly a model."""
    body = WITH_ALIASES.replace("cheap: tuned", "tuned: main-model")

    with pytest.raises(ConfigError, match="also the name of a model"):
        loaded(tmp_path, body)


def test_an_alias_whose_model_was_dropped_is_kept(tmp_path):
    """A real binding this machine cannot currently follow. Kept so the refusal
    at the point of use can name the endpoint and the variable, where refusing
    here could only name the alias."""
    body = WITH_ALIASES.replace(
        "default: main-model",
        "  other:\n    api: openai\n    base_url: https://example.invalid/v1\n"
        "    key_env: OTHER_API_KEY\n\ndefault: main-model",
    ).replace("  tuned:\n    endpoint: gateway", "  tuned:\n    endpoint: other")

    with pytest.warns(UserWarning, match="OTHER_API_KEY"):
        catalogue = loaded(tmp_path, body)
        models, aliases = catalogue.models, catalogue.aliases

    assert "tuned" not in models
    assert aliases["cheap"] == "tuned"


# -- the seam a repository would have added, which is already here ---------


def test_a_deployment_can_supply_models_without_a_file_at_all(tmp_path):
    """Where models.yaml is read from is a `Config` field, and `Models` is a
    record a deployment may build itself -- so holding the model catalogue in a
    database, or assembling it in code, needs no file, no path, and no loader.

    Asserted rather than left implicit. It is true today only by accident of the
    fixtures: `conftest.FAKE_CATALOGUE` is exactly this, so the whole suite
    already runs on an injected catalogue and no test says so. That made it look
    like a gap the way skills, subagents and tools each had one -- and unlike
    those, closing it would have meant adding a port over a seam that works.
    """
    from tests.conftest import FAKE_CATALOGUE
    from tests.test_run import StubAgent

    from kingfisher import Kingfisher
    from kingfisher.domain.request import Request

    assert not (tmp_path / "models.yaml").exists()
    cfg = Config(workspace=tmp_path / "ws", models=FAKE_CATALOGUE)

    service = Kingfisher(cfg, agent=StubAgent("ok"))

    assert service.run(Request("go")).answer == "ok"
    assert service.cfg.models.default == "fake-model"
    assert service.cfg.models.source is None, "nothing was read from disk"


def test_the_loader_is_the_only_thing_that_needs_the_file(tmp_path):
    """The other half of the same point, and what keeps `source` honest: a
    catalogue that *was* read names where it came from, so a refusal can point
    at the file that should have defined what it could not find."""
    catalogue = loaded(tmp_path)

    assert catalogue.source == written(tmp_path)
