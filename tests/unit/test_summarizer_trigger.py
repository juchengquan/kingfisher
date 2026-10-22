"""What kingfisher sizes by the summariser -- the PDFs a turn may hold, and where results
start being cleared -- is sized by the summariser each graph runs, a deployment's in
deepagents' place included."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    create_summarization_middleware,
)
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import SummarizationMiddleware as LangchainSummarization
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.attachments import AttachmentsMiddleware, pdf_allowance
from kingfisher.core.agent.middlewares.clearing import DEFAULT_MARK, ResultClearingMiddleware
from kingfisher.core.agent.models import summarizer_count, summarizer_trigger, supplied_trigger
from kingfisher.core.config import ConfigError
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.store import KeptLedger
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import EVERY_TOOL, FakeToolCallingModel, an_agent, subagents_dir
from tests.unit.test_offloads import _Shown

#: The notice a summariser in deepagents' place is built with, which is not the subject
#: here but for the one test asking what it says.
pytestmark = pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")

KB = 1024

#: Where `Early` fires: well under deepagents' 170,000 on a model with no profile.
EARLY = 100_000


class Early(SummarizationMiddleware):
    """deepagents' summariser in its own place, firing at `EARLY` tokens."""

    name = "early"
    wants = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any) -> None:
        super().__init__(model, backend=backend, trigger=("tokens", EARLY))
        self.name = "SummarizationMiddleware"


#: Where `Late` fires: past deepagents' 170,000 on a model with no profile.
LATE = 400_000


class Late(SummarizationMiddleware):
    """deepagents' summariser in its own place, firing at `LATE` tokens."""

    name = "late"
    wants = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any) -> None:
        super().__init__(model, backend=backend, trigger=("tokens", LATE))
        self.name = "SummarizationMiddleware"


def calibrated(messages: Any, *, tools: Any = None) -> int:
    """A deployment's counter, calibrated against a gateway counting twice what langchain
    estimates."""
    return 2 * count_tokens_approximately(messages, tools=tools)


class Calibrated(SummarizationMiddleware):
    """deepagents' summariser in its own place, counting with `calibrated`."""

    name = "calibrated"
    wants = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any) -> None:
        super().__init__(model, backend=backend, token_counter=calibrated)
        self.name = "SummarizationMiddleware"


class Foreign(AgentMiddleware):
    """In deepagents' summariser's place, and no summariser kingfisher can read."""

    name = "SummarizationMiddleware"


@dataclass
class _Asked:
    """The two things `_input_budget` reads off a request."""

    model: Any
    model_settings: dict[str, Any]


def _anthropic(window: int | None, output: int = 4_096) -> ChatAnthropic:
    return ChatAnthropic(
        model="a-gateway-model",
        api_key="sk-not-real",
        max_tokens=output,
        profile=None if window is None else {"max_input_tokens": window},
    )


def _deepagents(trigger: Any) -> Any:
    return lambda model: SummarizationMiddleware(model, backend=None, trigger=trigger)  # ty: ignore[invalid-argument-type]


def _langchains(trigger: Any) -> Any:
    return lambda model: LangchainSummarization(model=model, trigger=trigger)


#: Fires on whichever comes first, the count at any size.
EITHER = [("messages", 10), ("tokens", 90_000), ("fraction", 0.3)]


@pytest.mark.parametrize(
    ("summarizer", "window", "messages", "expected"),
    [
        (_deepagents(("tokens", 40_000)), 1_000_000, 0, 40_000),
        (_deepagents(("fraction", 0.5)), 200_000, 0, 100_000),
        (_deepagents(EITHER), 200_000, 0, 60_000),
        (_deepagents({"tokens": 50_000, "messages": 5}), 1_000_000, 5, 50_000),
        (_deepagents({"tokens": 50_000, "fraction": 0.1}), 1_000_000, 0, 100_000),
        (_deepagents(("tokens", 500_000)), 200_000, 0, 185_905),
        (_langchains(("tokens", 30_000)), 1_000_000, 0, 30_000),
        (lambda model: SummarizeByCount(model, None, 60), 200_000, 0, 170_000),
    ],
    ids=[
        "a token clause as written",
        "a fraction of the window",
        "the smallest of an OR list, its message count skipped",
        "an AND clause by its size",
        "an AND clause by the largest of its sizes",
        "past the input budget, the budget",
        "langchain's own",
        "summarize-by-count, by deepagents' own size",
    ],
)
def test_the_trigger_is_where_the_summarizer_in_the_stack_fires(
    summarizer, window, messages, expected
):
    """Read from deepagents' default whatever the stack held, a turn on a deployment's
    earlier summariser held PDFs and kept results whole past where it summarised them.

    Driven through the summariser's own checks, at the trigger and one token short, so a
    reading that parsed the trigger its own way would be caught where the two disagree.
    """
    model = _anthropic(window)
    built = summarizer(model)
    shown = [HumanMessage("x")] * messages
    asked = _Asked(model=model, model_settings={})

    def fires(total: int) -> bool:
        over = getattr(built, "_over_budget", None)
        return built._should_summarize(shown, total) or (over is not None and over(asked, total))

    trigger = summarizer_trigger(model, [built])
    assert trigger == expected
    assert fires(trigger), trigger
    assert not fires(trigger - 1), trigger


#: A conversation whose last reply reports more than langchain estimates, which its default
#: counter scales by, and a tool whose schema deepagents counts.
REPORTED = [
    HumanMessage("x" * 4_000),
    AIMessage(
        "y" * 400,
        response_metadata={"model_provider": "anthropic"},
        usage_metadata={"input_tokens": 9_000, "output_tokens": 100, "total_tokens": 9_100},
    ),
    ToolMessage("z" * 8_000, tool_call_id="c1"),
]
TOOLS = [{"name": "probe", "description": "d" * 400, "parameters": {"type": "object"}}]


def _own(model: Any) -> Any:
    return create_summarization_middleware(model, None)  # ty: ignore[invalid-argument-type]


def _deepagents_calibrated(model: Any) -> Any:
    return SummarizationMiddleware(model, backend=None, token_counter=calibrated)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("summarizer", "tools"),
    [
        (_own, TOOLS),
        (lambda model: Foreign(), TOOLS),
        (_deepagents_calibrated, TOOLS),
        (lambda model: Early(model, None), TOOLS),
        (lambda model: LangchainSummarization(model=model, token_counter=calibrated), []),
    ],
    ids=[
        "deepagents' own",
        "one it cannot read",
        "deepagents', calibrated",
        "deepagents', supplied",
        "langchain's",
    ],
)
def test_clearing_counts_as_the_summarizer_in_the_stack_does(summarizer, tools):
    """Counted by langchain's bare default, clearing measured a mark read off the
    summariser's trigger in another unit, and a deployment calibrating its summariser's
    counter against the gateway moved the trigger but not where clearing started.

    Driven through the count each summariser decides on -- deepagents' with the prompt and
    tools, langchain's on the messages alone -- and on an Anthropic model, where its default
    differs from the bare one: the control below. The unscaled count is asked of the whole
    conversation, because no single message is scaled.
    """
    model = _anthropic(200_000)
    built = summarizer(model)
    deciding = _own(model) if isinstance(built, Foreign) else built
    if isinstance(deciding, SummarizationMiddleware):
        decided = deciding._count_tokens(REPORTED, None, tools)
        helper = deciding._lc_helper
    else:
        decided = deciding.token_counter(REPORTED)
        helper = deciding

    count = summarizer_count(model, [built])
    assert count.request(REPORTED, tools) == decided
    assert decided != count_tokens_approximately(REPORTED, tools=tools), "the control"
    partial = helper._partial_token_counter
    unscaled = partial(REPORTED, tools=tools) if tools else partial(REPORTED)
    assert count.unscaled(REPORTED, tools) == unscaled


def test_a_summarizer_with_no_size_to_read_is_sized_as_deepagents_own():
    """A trigger of message counts alone, or a class kingfisher cannot read, would
    otherwise leave nothing to size by; deepagents' own is the size it falls back to."""
    model = _anthropic(200_000)
    counting = SummarizationMiddleware(model, backend=None, trigger=("messages", 30))  # ty: ignore[invalid-argument-type]

    for unread in (Foreign(), counting):
        assert supplied_trigger(unread) is None
        assert summarizer_trigger(model, [unread]) == summarizer_trigger(model) == 170_000


def test_of_two_summarizers_the_trigger_is_the_later_ones():
    """Read off the first of two answering to the summariser's name, clearing and PDFs
    were sized by the one deepagents' merge drops, since it keeps the later.

    Both orders, so a reading that picked by firing size rather than place is caught.
    """
    model = _anthropic(None)
    early, late = Early(model, None), Late(model, None)

    assert summarizer_trigger(model, [early, late]) == LATE
    assert summarizer_trigger(model, [late, early]) == EARLY


def _decided(summarizer: Any) -> int:
    """What deepagents' `summarizer` decides on for `REPORTED` with `TOOLS`."""
    return summarizer._count_tokens(REPORTED, None, TOOLS)


def test_of_two_summarizers_the_count_is_the_later_ones():
    """Counted by the first of two answering to the summariser's name, clearing measured
    in the unit of the one deepagents' merge drops, since it keeps the later."""
    model = _anthropic(200_000)
    early, recounting = Early(model, None), Calibrated(model, None)

    for stack, kept in (([early, recounting], recounting), ([recounting, early], early)):
        assert summarizer_count(model, stack).request(REPORTED, TOOLS) == _decided(kept)
    assert _decided(early) != _decided(recounting), "the control: the two count differently"


def _built(cfg: Any, session_dir: Any, **given: Any) -> Any:
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        middleware_registry={
            "early": Early,
            "late": Late,
            "foreign": Foreign,
            "calibrated": Calibrated,
        },
        ledger=Ledger("t", KeptLedger()),
        **given,
    )


def _spec(cfg: Any, name: str = "only") -> Any:
    return LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[name]


def _allowance(middleware: Any) -> int:
    (guard,) = [one for one in middleware if isinstance(one, AttachmentsMiddleware)]
    return guard._pdf_bytes


def _high(middleware: Any) -> int:
    (clearing,) = [one for one in middleware if isinstance(one, ResultClearingMiddleware)]
    return clearing.high


def test_every_graph_holds_the_pdfs_its_own_summarizer_lets_through(cfg, session_dir):
    """A graph sized by deepagents' default holds 255,000 bytes of PDF open where the
    summariser it runs fires at a turn of 150,000: the agent, `general-purpose`, which
    runs the agent's middleware, and a subagent naming the summariser itself."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nmiddlewares: [early]\n"
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    (subagents_dir(cfg) / "plain.yaml").write_text(
        "name: plain\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    an_agent(cfg, middlewares="[early]", subagents="[helper, plain]", **EVERY_TOOL)

    built = _built(cfg, session_dir, agent=_spec(cfg))

    graphs = {spec["name"]: spec["middleware"] for spec in built.subagents or ()}
    assert _allowance(built.middleware) == pdf_allowance(EARLY)
    assert _allowance(graphs["general-purpose"]) == pdf_allowance(EARLY)
    assert _allowance(graphs["helper"]) == pdf_allowance(EARLY)
    assert _allowance(graphs["plain"]) == pdf_allowance(170_000), "a subagent naming none"


def test_a_turn_refuses_a_pdf_its_summarizer_would_fire_on(cfg, session_dir):
    """Driven, not read off the middleware: 200 KB of PDF is held whole under deepagents'
    own summariser on this model, and refused under one firing at `EARLY`."""
    (session_dir / "inputs" / "doc.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * (200 * KB - 9))
    an_agent(cfg, middlewares="[early]", **EVERY_TOOL)
    an_agent(cfg, "unnamed", **EVERY_TOOL)

    def handed(agent: str) -> ToolMessage:
        read = {"name": "read_file", "args": {"file_path": "/inputs/doc.pdf"}, "id": "c1"}
        model = _Shown(
            responses=[AIMessage(content="", tool_calls=[read]), AIMessage(content="done")]
        )
        built = _built(cfg, session_dir, agent=_spec(cfg, agent), model=model)
        built.graph.invoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"recursion_limit": 20},
            context=built.context,
        )
        (answer,) = [m for m in model.shown[-1] if isinstance(m, ToolMessage)]
        return answer

    assert handed("unnamed").content_blocks[0].get("type") == "file", "no PDF to refuse"
    assert handed("only").text.startswith("/inputs/doc.pdf is a 200 KB PDF, too large")


def test_clearing_starts_at_half_where_the_deployments_summarizer_fires(cfg, session_dir):
    """At 64,000 tokens, clearing would start well past half a summariser firing at
    `EARLY`, where deepagents' own is the control."""
    an_agent(cfg, middlewares="[early]")
    an_agent(cfg, "unnamed")

    assert _high(_built(cfg, session_dir, agent=_spec(cfg)).middleware) == EARLY // 2
    assert _high(_built(cfg, session_dir, agent=_spec(cfg, "unnamed")).middleware) == (
        DEFAULT_MARK
    )


def test_every_graph_clears_by_the_count_of_the_summarizer_it_runs(cfg, session_dir):
    """A deployment's counter reached the summariser and not clearing, so its calibration
    moved where the conversation was summarised and not where it was cleared: the agent,
    `general-purpose`, which runs the agent's middleware, and a subagent naming it."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nmiddlewares: [calibrated]\n"
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    (subagents_dir(cfg) / "plain.yaml").write_text(
        "name: plain\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    an_agent(cfg, middlewares="[calibrated]", subagents="[helper, plain]", **EVERY_TOOL)

    built = _built(cfg, session_dir, agent=_spec(cfg))

    graphs = {spec["name"]: spec["middleware"] for spec in built.subagents or ()}
    twice = 2 * count_tokens_approximately(REPORTED)
    for middleware in (built.middleware, graphs["general-purpose"], graphs["helper"]):
        assert _counted(middleware) == twice
    assert _counted(graphs["plain"]) < twice, "a subagent naming none"


def _counted(middleware: Any) -> int:
    (clearing,) = [one for one in middleware if isinstance(one, ResultClearingMiddleware)]
    return clearing.count.request(REPORTED, [])


def _marked(cfg: Any, model: str | None = None, **fields: Any) -> Any:
    """`cfg`, with one model's entry -- the default's unless named -- carrying `fields`."""
    models = cfg.models
    name = model or models.default
    entry = replace(models.models[name], **fields)
    return replace(cfg, models=replace(models, models={**models.models, name: entry}))


def test_a_mark_the_deployments_summarizer_reaches_first_is_refused_at_build(
    cfg, session_dir
):
    """`build_model` holds a mark to deepagents' own summariser, which fires at 170,000
    here, so a mark of `EARLY` passed it and never cleared anything under one firing there."""
    an_agent(cfg, middlewares="[early]")
    an_agent(cfg, "unnamed")
    at = _marked(cfg, clear_results_at=EARLY)

    with pytest.raises(ConfigError, match=rf"agent 'only' .* fires at {EARLY} tokens"):
        _built(at, session_dir, agent=_spec(at))
    assert _high(_built(at, session_dir, agent=_spec(at, "unnamed")).middleware) == EARLY
    below = _marked(cfg, clear_results_at=EARLY - 1)
    assert _high(_built(below, session_dir, agent=_spec(below)).middleware) == EARLY - 1


def test_a_mark_past_deepagents_own_stands_under_a_summarizer_firing_later(
    cfg, session_dir
):
    """`build_model` held every mark to deepagents' own summariser, which fires at 170,000
    here, so one of 250,000 was refused under a summariser firing at `LATE`, though that
    one runs in deepagents' place. A graph left with deepagents' own is still refused."""
    an_agent(cfg, middlewares="[late]")
    an_agent(cfg, "unnamed")
    at = _marked(cfg, clear_results_at=250_000)

    assert _high(_built(at, session_dir, agent=_spec(at)).middleware) == 250_000
    with pytest.raises(ConfigError, match="deepagents summarises its conversation at 170000"):
        _built(at, session_dir, agent=_spec(at, "unnamed"))


def test_a_helper_left_with_deepagents_own_summarizer_is_held_to_it(cfg, session_dir):
    """A helper's summariser is the one kingfisher adds, deepagents' own, so the mark it
    inherits from a summoner under `LATE` is refused as past deepagents', not passed for
    the summoner's."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: Checks.\nmodel: cheap-model\nmiddlewares: [late]\n"
        "builtin_tools: [task]\nsubagents: [second-opinion]\nsystem_prompt: |\n  You check.\n",
        encoding="utf-8",
    )
    (subagents_dir(cfg) / "second-opinion.yaml").write_text(
        "name: second-opinion\ndescription: Answers again.\nsystem_prompt: |\n  You answer.\n",
        encoding="utf-8",
    )
    an_agent(cfg, middlewares="[late]", subagents="[reviewer, second-opinion]", **EVERY_TOOL)
    at = _marked(cfg, "cheap-model", clear_results_at=250_000)

    with pytest.raises(ConfigError, match="deepagents summarises its conversation at 170000"):
        _built(at, session_dir, agent=_spec(at))


def test_a_mark_a_subagents_own_summarizer_reaches_first_is_refused_at_build(
    cfg, session_dir
):
    """A subagent's mark is held to its own graph's summariser, as the agent's is: one on
    a model of its own, under a summariser firing at `EARLY`, never cleared anything."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nmodel: cheap-model\nmiddlewares: [early]\n"
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    # The agent names `early` too, which is what lets a subagent name it; its own mark is
    # the default rule's, half of `EARLY`, so only the subagent's can be refused.
    an_agent(cfg, middlewares="[early]", subagents="[helper]", **EVERY_TOOL)
    at = _marked(cfg, "cheap-model", clear_results_at=EARLY)

    with pytest.raises(ConfigError, match=rf"subagent 'helper' .* fires at {EARLY} tokens"):
        _built(at, session_dir, agent=_spec(at))
    below = _marked(cfg, "cheap-model", clear_results_at=EARLY - 1)
    built = _built(below, session_dir, agent=_spec(below))
    graphs = {spec["name"]: spec["middleware"] for spec in built.subagents or ()}
    assert _high(graphs["helper"]) == EARLY - 1
    assert _high(built.middleware) == EARLY // 2


def test_the_build_says_when_it_cannot_read_the_summarizer_it_runs(cfg, session_dir):
    """Sized as deepagents' own without a word, a deployment's summariser firing sooner
    would hold PDFs past it and nobody would know why."""
    an_agent(cfg, middlewares="[foreign]")
    an_agent(cfg, "early", middlewares="[early]")
    model = FakeToolCallingModel(responses=[])

    with pytest.warns(UserWarning) as foreign:
        _built(cfg, session_dir, agent=_spec(cfg), model=model)
    with pytest.warns(UserWarning) as early:
        _built(cfg, session_dir, agent=_spec(cfg, "early"), model=model)

    said = "is neither or fires at no size"
    assert any(said in str(one.message) for one in foreign)
    assert not any(said in str(one.message) for one in early), "a summariser it did read"
