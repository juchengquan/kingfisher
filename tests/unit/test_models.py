"""Model construction."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest
from deepagents.middleware.summarization import compute_summarization_defaults

from kingfisher.config import Adapter, ConfigError, Endpoint, ModelProfile
from kingfisher.infrastructure.harness.models import ADAPTERS, FILLABLE, build_model
from kingfisher.infrastructure.model_catalogue import load
from tests.conftest import repository_root

if TYPE_CHECKING:
    from typing import Any

OPENAI = Endpoint(
    "openai_responses",
    "https://api.openai.com/v1",
    "sk-not-real",
    adapter=ADAPTERS["openai_responses"],
)


def test_openai_uses_the_responses_api(cfg):
    """The openai adapter targets `/v1/responses`, and only that."""
    model = build_model(cfg.models.models["fake-model"], OPENAI)

    assert model.use_responses_api is True


def test_an_adapter_row_cannot_overrule_a_configured_value(cfg):
    """`extra` is additive: it may not name a value the profile carries."""
    colliding = replace(ADAPTERS["openai_responses"], extra={"max_tokens": 1})

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        _build_with(colliding, cfg.models.models["fake-model"])


def test_a_model_entrys_extra_cannot_overrule_its_own_params(cfg):
    """The same rule from the other side: `extra` in `models.yaml`.

    `model_catalogue` refuses this at parse time so the error can name the file; this is
    the backstop for a `ModelProfile` built any other way.
    """
    profile = replace(cfg.models.models["fake-model"], extra={"max_tokens": 1})

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(profile, OPENAI)


def test_the_model_comes_from_the_profile_it_is_handed(cfg):
    """How a subagent runs elsewhere, now that there is no `Config` parameter:
    `delegation.as_subagent` looks the profile up and hands it over.
    """
    profile, endpoint = cfg.models.resolve("cheap-model")

    assert build_model(profile, endpoint).model == "cheap-model"
    assert build_model(*cfg.models.resolve()).model == cfg.models.default


#: The params `kwargs` passes only when they are set. Listed rather than written
#: into the test below, because the test below was written for `temperature` and
#: `top_p` sat one line under it in `kwargs` for as long, unnamed by anything.
OPTIONAL = ("temperature", "top_p")


def test_an_unset_param_is_not_passed_at_all(cfg):
    """Omitted means absent, not "passed as a default we chose"."""
    for field in OPTIONAL:
        unset = cfg.models.models["fake-model"]
        assert getattr(unset, field) is None
        assert field not in unset.kwargs()

        chosen = replace(unset, **{field: 0.5})
        assert chosen.kwargs()[field] == 0.5


def test_every_param_kwargs_omits_is_named_above():
    """A third optional param would otherwise arrive with no case, the way the
    second one did.
    """
    source = inspect.getsource(ModelProfile.kwargs)
    guarded = tuple(re.findall(r"if self\.(\w+) is not None:", source))

    assert guarded == OPTIONAL, (
        f"`kwargs` omits {guarded} when unset and this file names {OPTIONAL} — "
        "add the new one to OPTIONAL, which is what gives it a case"
    )


def test_every_value_reaches_the_client():
    """A shipped row whose `lands` names the wrong attribute.

    Read back here as well as inside `build_model`, so deleting that check leaves
    something red besides the test written for it.
    """
    for api, adapter in sorted(ADAPTERS.items()):
        endpoint = Endpoint(api, "https://example.invalid/v1", "sk-not-real", adapter=adapter)
        profile = ModelProfile("a-model", "somewhere", max_tokens=321, timeout_s=45)
        model = build_model(profile, endpoint)
        sites = adapter.lands

        assert getattr(model, sites.model) == profile.model
        assert getattr(model, sites.base_url) == endpoint.base_url
        assert getattr(model, sites.api_key).get_secret_value() == endpoint.api_key
        assert getattr(model, sites.max_tokens) == 321
        assert getattr(model, sites.timeout) == 45


@dataclass(frozen=True)
class _Renames(Adapter):
    """A row whose class calls one keyword something else, as most vendors' own do."""

    keyword: str = "base_url"

    def resolve(self) -> Any:
        chat_openai = super().resolve()

        def build(**kwargs: Any) -> Any:
            kwargs[f"gateway_{self.keyword}"] = kwargs.pop(self.keyword)
            return chat_openai(**kwargs)

        return build


def test_a_url_the_class_did_not_keep_is_refused(cfg):
    """A class that does not know `base_url` builds anyway, with the URL unset, and
    sends the endpoint's key to the vendor's default host.

    Driven through the real `ChatOpenAI` rather than a stub model, because what makes
    this dangerous is that class's own habit of moving an unknown keyword into
    `model_kwargs` and carrying on.
    """
    shipped = ADAPTERS["openai_responses"]
    row = _Renames(shipped.chat_class, shipped.lands, shipped.extra)

    with (
        pytest.warns(UserWarning, match="gateway_base_url"),
        pytest.raises(ConfigError, match=r"did not keep 'base_url' at 'openai_api_base'"),
    ):
        _build_with(row, cfg.models.models["fake-model"])


def test_a_refusal_never_quotes_the_key_it_found(cfg, monkeypatch):
    """A class that loses the endpoint's key falls back to one from the environment,
    and the refusal goes to a log.
    """
    ambient = "sk-ambient-not-real"
    monkeypatch.setenv("OPENAI_API_KEY", ambient)
    shipped = ADAPTERS["openai_responses"]
    row = _Renames(shipped.chat_class, shipped.lands, shipped.extra, keyword="api_key")

    with (
        pytest.warns(UserWarning, match="gateway_api_key"),
        pytest.raises(ConfigError, match="did not keep 'api_key'") as refused,
    ):
        _build_with(row, cfg.models.models["fake-model"])
    assert ambient not in str(refused.value)


#: The gateway shape `models.yaml.example` recommends, on the adapter most rows use.
ANTHROPIC = Endpoint(
    "anthropic",
    "https://example.invalid/anthropic",
    "sk-not-real",
    adapter=ADAPTERS["anthropic"],
)


def _row(model: str, **fields: Any) -> ModelProfile:
    """One `models.yaml` row, as the loader would hand it over."""
    return ModelProfile(model=model, endpoint="gateway", **fields)


def test_a_row_reserving_the_whole_window_for_output_is_refused():
    """`max_tokens` is how much the model may write, and a window-sized one reads like
    its own limit -- the mistake this refuses. The reservation comes off the window
    before a request is sized, so every turn was refused before it was sent, with
    deepagents' own message naming no model, no row and no number. Measured through
    `kingfisher run`: `ContextOverflowError` reached the terminal as a traceback.
    """
    with pytest.raises(ConfigError, match="leaving nothing for the conversation"):
        build_model(_row("claude-sonnet-4-5", max_tokens=190_001), ANTHROPIC)


def test_the_refusal_names_the_row_the_window_and_what_to_change():
    """A refusal nobody can act on sends somebody to a file where everything looks
    right -- the same argument `key_env` is kept for.
    """
    with pytest.raises(ConfigError) as refused:
        build_model(_row("claude-sonnet-4-5", max_tokens=190_001), ANTHROPIC)

    said = str(refused.value)
    assert "'claude-sonnet-4-5'" in said
    assert "190001" in said and "200000" in said
    assert "models.yaml" in said


def test_a_row_the_window_leaves_room_for_still_builds():
    """The control, and the ordinary case: this model's real output cap is 64,000, so
    the shape a deployment would actually write has to pass.
    """
    model = build_model(_row("claude-sonnet-4-5", max_tokens=64_000), ANTHROPIC)

    assert model.max_tokens == 64_000


def test_a_model_the_table_never_heard_of_is_left_alone():
    """Every gateway row naming its own model name, which is the commonest row there
    is: no profile, so no window, so no arithmetic to do. Refusing on a number nobody
    declared would refuse the shape `models.yaml.example` recommends.
    """
    model = build_model(_row("a-gateway-model", max_tokens=190_001), ANTHROPIC)

    assert model.max_tokens == 190_001
    # `None` rather than an empty profile, which is the shape the guard has to survive.
    assert model.profile is None


def test_a_window_the_row_declares_itself_is_the_one_checked():
    """`extra: {profile: ...}` reaches the constructor and replaces the whole profile,
    which `findings.md` measured -- so a deployment that declares a smaller window is
    checked against the one it declared, not against the table's.
    """
    row = _row(
        "a-gateway-model",
        max_tokens=19_500,
        extra={"profile": {"max_input_tokens": 20_000}},
    )

    with pytest.raises(ConfigError, match="advertised 20000-token"):
        build_model(row, ANTHROPIC)


def test_the_share_a_request_may_fill_is_the_one_deepagents_applies():
    """`FILLABLE` mirrors a constant inside `SummarizationMiddleware._input_budget`,
    and a mirror nobody drives goes stale quietly -- the refusal above would then reject
    a row that works, or pass one that cannot.

    Driven through their function rather than read off their source, so the shape is
    pinned with the number: the budget is that share of the window *less the output
    reservation*, not the share alone.
    """
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langchain_anthropic import ChatAnthropic

    @dataclass
    class _Asked:
        """The two things `_input_budget` reads off a request."""

        model: Any
        model_settings: dict[str, Any]

    for window, output in ((200_000, 4_096), (200_000, 190_001), (20_000, 1_000)):
        # Built here rather than through `build_model`, because one of these rows is the
        # one `build_model` now refuses and this test is about their arithmetic.
        model = ChatAnthropic(
            model="a-gateway-model",
            api_key="sk-not-real",
            max_tokens=output,
            profile={"max_input_tokens": window},
        )
        # Neither argument is the type they annotate, and that is the point: this
        # drives one function of theirs, and a real backend and a real `ModelRequest`
        # would be a turn.
        middleware = SummarizationMiddleware(
            model=model,
            backend=None,  # ty: ignore[invalid-argument-type]
        )
        asked = _Asked(model=model, model_settings={})

        theirs = middleware._input_budget(asked)  # ty: ignore[invalid-argument-type]

        assert theirs == max(0, int(window * FILLABLE) - output), (window, output)


#: A catalogue whose one model is the mistake, written the way a deployment writes one.
WINDOW_FOR_OUTPUT = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_API_KEY

default: claude-sonnet-4-5

models:
  claude-sonnet-4-5:
    endpoint: gateway
    max_tokens: 190001
"""


TOOL = """
from langchain_core.tools import tool

@tool
def shout() -> str:
    \"\"\"Shout.\"\"\"
    return "ok"

TOOLS = [shout]
"""


def _dead_row(cfg) -> None:
    """A deployment whose one model row cannot start a turn, with a workspace tool --
    which is what brings the startup probe into it, and is the ordinary shape.
    """
    from tests.conftest import an_agent, tools_dir

    an_agent(cfg, "only")
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shout.py").write_text(TOOL, encoding="utf-8")
    (cfg.definitions / "models.yaml").write_text(WINDOW_FOR_OUTPUT, encoding="utf-8")


#: Each verb that reads the catalogue, with the exit code it owes. `sessions` and
#: `reap` need no model at all; the other four report the fault and say so.
ASKED = {
    "doctor": (["doctor"], 1),
    "doctor-json": (["doctor", "--json"], 1),
    "list": (["list"], 1),
    "list-json": (["list", "--json"], 1),
    "sessions": (["sessions"], 0),
    "reap": (["reap"], 0),
}


@pytest.mark.parametrize("verb", sorted(ASKED), ids=sorted(ASKED))
def test_a_dead_model_row_still_leaves_every_other_verb_answering(
    verb, cfg, at_the_command_line, capsys, monkeypatch
):
    """The refusal escaped `inventory` and took the diagnostics with it: all four of
    these exited 2 with *no output*, so the two commands an operator would reach for to
    find the bad row were the first thing it broke -- and `sessions` and `reap`, which
    need no model at all, went with them.

    `inventory`'s own comment records this shape as the reason `CapabilityError` is in
    its catch set; this is the same fault one exception type over.
    """
    from kingfisher.presentation.cli.__main__ import main

    argv, code = ASKED[verb]
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-not-real")
    _dead_row(cfg)

    assert main(argv) == code
    assert capsys.readouterr().out, f"{argv} answered nothing"


def test_the_listing_and_doctor_both_name_the_row(cfg, at_the_command_line, capsys, monkeypatch):
    """A non-zero exit tells a script something is wrong; only the text tells the person
    what, and these two are where they look.
    """
    import json

    from kingfisher.presentation.cli.__main__ import main

    monkeypatch.setenv("GATEWAY_API_KEY", "sk-not-real")
    _dead_row(cfg)

    main(["list"])
    listed = capsys.readouterr().out
    main(["doctor"])
    doctored = capsys.readouterr().out
    main(["doctor", "--json"])
    document = json.loads(capsys.readouterr().out)

    assert "cannot build an agent: model 'claude-sonnet-4-5' reserves" in listed
    assert "agent build" in doctored and "FAIL" in doctored
    named = [one for one in document["checks"] if one["name"] == "agent build"]
    assert named and named[0]["verdict"] == "fail"


def test_a_turn_still_refuses_the_row_it_cannot_run(cfg, at_the_command_line, capsys, monkeypatch):
    """What the startup probe stopped raising. Deferring it would be hiding the fault if
    nothing else refused -- the turn does, naming the row, which is the whole of what
    #747 was for.
    """
    from kingfisher.presentation.cli.__main__ import main

    monkeypatch.setenv("GATEWAY_API_KEY", "sk-not-real")
    _dead_row(cfg)

    assert main(["run", "say hi", "--agent", "only"]) == 2

    assert "configuration error: model 'claude-sonnet-4-5' reserves" in capsys.readouterr().err


SHADOWING_TOOL = """
from langchain_core.tools import tool

@tool
def read_file() -> str:
    \"\"\"Shadow a built-in.\"\"\"
    return "ok"

TOOLS = [read_file]
"""


def test_the_startup_probe_still_refuses_the_question_it_exists_to_ask(cfg, monkeypatch):
    """The load-bearing half of deferring. The probe asks one thing -- does a workspace
    tool wear a built-in's name -- and answers it with `CapabilityError`. Only
    `ConfigError` is suppressed, so the answer it exists for still stops the service.
    """
    from kingfisher import Kingfisher, default_backends
    from kingfisher.domain.capabilities import CapabilityError
    from tests.conftest import tools_dir

    monkeypatch.setenv("GATEWAY_API_KEY", "sk-not-real")
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shadow.py").write_text(SHADOWING_TOOL, encoding="utf-8")

    with pytest.raises(CapabilityError, match="read_file"):
        Kingfisher(cfg, backends=default_backends)


def test_the_command_says_it_in_one_line_rather_than_a_traceback(
    cfg, at_the_command_line, capsys, monkeypatch
):
    """Measured before this: `kingfisher run` on that catalogue raised
    `ContextOverflowError` out of `main` -- a traceback over a config somebody wrote,
    with deepagents' message naming no model and no number.

    Driven through the command rather than through `build_model`, because the traceback
    is what a person met and `ConfigError` reaching `main` is what replaces it.
    """
    from kingfisher.presentation.cli.__main__ import main
    from tests.conftest import an_agent

    monkeypatch.setenv("GATEWAY_API_KEY", "sk-not-real")
    an_agent(cfg, "only")
    (cfg.definitions / "models.yaml").write_text(WINDOW_FOR_OUTPUT, encoding="utf-8")

    code = main(["run", "say hi", "--agent", "only"])

    said = capsys.readouterr().err
    assert code == 2
    assert said.startswith("configuration error: model 'claude-sonnet-4-5' reserves")
    assert "Traceback" not in said


def _build_with(row: Adapter, profile: ModelProfile) -> Any:
    """`build_model` on `OPENAI`, with `row` standing in for its shipped adapter."""
    return build_model(profile, replace(OPENAI, adapter=row))


def test_describing_an_adapter_does_not_import_its_sdk():
    """A deployment uses the wire formats its endpoints name, so naming the classes
    meant importing every provider's SDK to describe endpoints none of them would
    build.
    """
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import kingfisher.infrastructure.harness.models as m;"
        "print('langchain_openai' in sys.modules, 'langchain_anthropic' in sys.modules)"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False False"


def test_a_row_naming_an_absent_class_fails_where_it_is_built():
    """Deferring the import defers the error too, so it has to still be a clear one -- a
    typo'd row must not surface as a mysterious attribute failure.
    """
    row = replace(ADAPTERS["openai_responses"], chat_class="langchain_openai:NoSuchModel")
    with pytest.raises(AttributeError, match="NoSuchModel"):
        row.resolve()

    missing_module = replace(row, chat_class="no_such_package:Thing")
    with pytest.raises(ModuleNotFoundError, match="no_such_package"):
        missing_module.resolve()


def test_the_shipped_minimax_row_sizes_the_summarizer_from_its_window(tmp_path):
    """langchain has no profile for MiniMax, so a profile under `extra` that the loader
    dropped or the class ignored left the summarizer at 170,000 tokens and six messages
    on a 1M window, with nothing saying so.

    The shipped template, through the loader, rather than a row written here: the row
    is what a seeded workspace starts from.
    """
    keys = tmp_path / "keys.yaml"
    keys.write_text("minimax: sk-not-real\nopenai: sk-not-real\n", encoding="utf-8")
    template = repository_root() / "src" / "kingfisher" / "templates" / "models.yaml.example"
    models = load(template, {}, keys_file=keys)

    model = build_model(*models.resolve("MiniMax-M3"))

    assert model.profile == {"max_input_tokens": 1_000_000}
    assert compute_summarization_defaults(model)["trigger"] == ("fraction", 0.85)
