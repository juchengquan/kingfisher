"""A file a tool's result carries reaches the model only when one may take it, and
`read_file` is not run on a file no model here takes as it is.

Driven through `build_agent` rather than the middleware alone, because which graphs
carry the guard and which tools it may name are both decided where the agent is built.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.attachments import (
    MAX_IMAGE_CHARS,
    MAX_TURN_IMAGE_CHARS,
    MB,
    AttachmentsMiddleware,
    pdf_allowance,
)
from kingfisher.core.agent.middlewares.recorded import readable_size
from kingfisher.core.agent.models import summarizer_trigger
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    a_pdf,
    an_agent,
    repository_root,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted
from tests.unit.test_offloads import _Shown

KB = 1024

#: What deepagents' summariser fires at on a model with no profile, as `_Shown` has none.
UNPROFILED = summarizer_trigger(FakeToolCallingModel(responses=[]))

#: Past the allowance with no profile by a margin, and not a PDF anything parses:
#: `read_file` goes by the extension and never opens it.
LARGE = b"%PDF-1.4\n" + b"0" * (300 * KB)

DECK = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: Tools returning files, which nothing about their names says they will.
EXPORTER = f'''
import base64
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

PDF = base64.b64encode(b"%PDF-1.4" + b"0" * {300 * KB}).decode()
URL = "data:application/pdf;base64," + PDF


def export_pdf() -> list:
    """Return a PDF. Use never; this is a fixture."""
    return [{{"type": "file", "base64": PDF, "mime_type": "application/pdf"}}]


def pdf_as(shape: str) -> list:
    """Return a PDF in the block named. Use never; this is a fixture."""
    return [{{
        "image_url": {{"type": "image_url", "image_url": {{"url": URL}}}},
        "image": {{"type": "image", "base64": PDF, "mime_type": "application/pdf"}},
        "file_url": {{"type": "file", "url": URL}},
    }}[shape]]


def pdf_in_command(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Return a PDF in a message of its own. Use never; this is a fixture."""
    answer = ToolMessage(
        content=[{{"type": "file", "base64": PDF, "mime_type": "application/pdf"}}],
        tool_call_id=tool_call_id,
    )
    return Command(update={{"messages": [answer]}})


def chart() -> list:
    """Return a chart. Use never; this is a fixture."""
    return [
        {{"type": "text", "text": "chart"}},
        {{"type": "image", "base64": "A" * 6_400_000, "mime_type": "image/png"}},
        {{"type": "image", "base64": "A" * 1024, "mime_type": "image/png"}},
    ]


def export_deck() -> list:
    """Return a slide deck. Use never; this is a fixture."""
    return [{{"type": "file", "base64": "UEsDBA" * 1000, "mime_type": "{DECK}"}}]


TOOLS = [export_pdf, pdf_as, pdf_in_command, chart, export_deck]
'''


def _calls(*calls: tuple[str, dict]) -> _Shown:
    """A model that makes each call in turn, then answers."""
    return _Shown(
        responses=[
            *(
                AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}])
                for n, (name, args) in enumerate(calls, 1)
            ),
            AIMessage(content="done"),
        ]
    )


def _run(
    cfg, session_dir, model: _Shown, capabilities: Capabilities | None = None, *, wait=False
) -> dict:
    """One turn on a built graph; `wait` drives it through the async hooks, as
    `astream` does."""
    built = build_agent(
        cfg, backend=backend_at(cfg, session_dir), model=model, capabilities=capabilities
    )
    turn = {"messages": [{"role": "user", "content": "go"}]}
    if wait:
        return asyncio.run(
            built.graph.ainvoke(turn, config={"recursion_limit": 20}, context=built.context)
        )
    return built.graph.invoke(turn, config={"recursion_limit": 20}, context=built.context)


def _answer(model: _Shown, call_id: str = "c1") -> ToolMessage:
    """The tool result the model was handed for `call_id`, as it was handed."""
    (answer,) = [
        m for m in model.shown[-1] if isinstance(m, ToolMessage) and m.tool_call_id == call_id
    ]
    return answer


def _files(messages) -> list:
    return [
        b for m in messages for b in m.content_blocks if b.get("type") in ("file", "image")
    ]


def _with_pdf_text(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    shutil.copy(repository_root() / "assets" / "tools" / "pdf_text.py", tools_dir(cfg))


def _with_exporter(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "exporter.py").write_text(EXPORTER, encoding="utf-8")


def test_a_large_pdf_read_whole_reaches_the_model_as_advice(cfg, session_dir):
    """Handed over whole, one large PDF trips the summarizer, and what happens then is
    worse than a refusal: 0.7.22 clips the overflow tail and calls again, and clipping a
    PDF block to its text leaves nothing, so the model answers from an empty result
    rather than saying it could not read the file.

    This said the turn ended on a context overflow with no retry, which was true of
    0.7.6 and was corrected in the guard itself by the upgrade -- and not here.
    """
    (session_dir / "inputs" / "big.pdf").write_bytes(LARGE)
    model = _calls(("read_file", {"file_path": "/inputs/big.pdf"}))

    state = _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert answer.status == "error"
    assert answer.text.startswith("/inputs/big.pdf is a 300 KB PDF, too large to hand over")
    assert "pdf_text" not in answer.text, "named a tool this graph does not hold"
    # Replaced in what the graph keeps too, so no later call and no checkpoint carries it.
    assert not _files(state["messages"])


def test_the_advice_names_pdf_text_where_the_graph_holds_it_and_the_route_works(
    cfg, session_dir
):
    """Advice naming no way through leaves the model nothing to do with the file."""
    _with_pdf_text(cfg)
    pages = [f"page {n} " + "x" * 1500 for n in range(1, 201)]
    (session_dir / "inputs" / "big.pdf").write_bytes(big := a_pdf(*pages))
    assert len(big) > pdf_allowance(UNPROFILED), "not large enough to be refused"
    model = _calls(
        ("read_file", {"file_path": "/inputs/big.pdf"}),
        ("pdf_text", {"file_path": "/inputs/big.pdf"}),
    )

    _run(cfg, session_dir, model)

    assert "Call pdf_text on it" in _answer(model, "c1").text
    assert _answer(model, "c2").text.startswith("Saved big.p1-200.txt (text, ")


def test_an_agent_not_granted_pdf_text_is_not_told_to_call_it(cfg, session_dir):
    """`standing_middleware` is handed every tool the workspace defines, so advice read off its
    names tells an agent granted none of them to call one it does not hold.
    """
    _with_pdf_text(cfg)
    (session_dir / "inputs" / "big.pdf").write_bytes(LARGE)
    model = _calls(("read_file", {"file_path": "/inputs/big.pdf"}))

    _run(cfg, session_dir, model, Capabilities(tools=()))

    answer = _answer(model)
    assert answer.text.startswith("/inputs/big.pdf is a 300 KB PDF"), answer.text
    assert "pdf_text" not in answer.text


def test_a_pdf_under_the_limit_is_handed_over_as_it_is(cfg, session_dir):
    """The control: a guard that refused every PDF would pass every test above."""
    (session_dir / "inputs" / "small.pdf").write_bytes(a_pdf("a short letter"))
    model = _calls(("read_file", {"file_path": "/inputs/small.pdf"}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert [b["mime_type"] for b in _files([answer])] == ["application/pdf"]
    assert answer.status == "success"


def test_a_large_pdf_any_tool_returns_is_refused_too(cfg, session_dir):
    """A guard on `read_file` alone is stepped around by any tool that returns a PDF."""
    _with_exporter(cfg)
    model = _calls(("export_pdf", {}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert answer.text.startswith("export_pdf's result is a 300 KB PDF"), answer.text


@pytest.mark.parametrize("shape", ["image_url", "image", "file_url"])
def test_a_large_pdf_in_an_image_block_or_a_data_url_is_refused_too(cfg, session_dir, shape):
    """A guard reading only a `file` block's `base64` hands over a PDF a tool labelled an
    image or wrote as a data URL, which goes out whole with every call after it."""
    _with_exporter(cfg)
    model = _calls(("pdf_as", {"shape": shape}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert answer.text.startswith("pdf_as's result is a 300 KB PDF"), answer.text
    assert answer.status == "error"


def test_a_large_pdf_inside_a_command_is_refused_too(cfg, session_dir):
    """A tool writing its own message inside a `Command` passed the guard untouched, and
    its PDF reached the model whole."""
    _with_exporter(cfg)
    model = _calls(("pdf_in_command", {}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert answer.text.startswith("pdf_in_command's result is a 300 KB PDF"), answer.text


def test_an_image_past_the_limit_is_replaced_and_the_rest_kept(cfg, session_dir):
    """An image past what a provider takes gets the whole request refused, and every one
    after it while the image stays in the conversation."""
    _with_exporter(cfg)
    model = _calls(("chart", {}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    text, replaced, kept = (dict(block) for block in answer.content_blocks)
    assert text["text"] == "chart"
    assert replaced["text"] == (
        "chart's result is a 6.1 MB image, larger than the 5 MB one image may be."
    )
    assert kept["type"] == "image", "an image under the limit was replaced too"
    assert answer.status == "success", "the text and the small image came back"


def test_an_image_under_the_limit_is_read_as_it_is(cfg, session_dir):
    """The control: a guard that refused every image, or `read_file` on every one, would
    pass the test above."""
    (session_dir / "inputs" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(1024))
    model = _calls(("read_file", {"file_path": "/inputs/photo.png"}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert [b["mime_type"] for b in _files([answer])] == ["image/png"]
    assert answer.status == "success"


def test_another_file_a_tool_returns_is_replaced_with_advice(cfg, session_dir):
    """A `.pptx` block was counted by its base64, hundreds of thousands of tokens, then
    dropped from the request with a note, under `read_file`'s name, blaming the model."""
    _with_exporter(cfg)
    model = _calls(("export_deck", {}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the deck"
    assert answer.text == (
        f"export_deck's result is a file of type {DECK}, which can't be handed to the model "
        "here. If you hold a tool that can read it, use that instead; otherwise say that "
        "this part could not be done."
    )
    assert answer.status == "error"


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("call.mp3", ("audio", "audio")),
        ("clip.mp4", ("video", "video")),
        ("clip.mkv", ("video", "video")),
        ("deck.pptx", ("a .pptx file", ".pptx files")),
    ],
)
def test_read_file_is_not_run_on_a_file_no_model_here_takes(cfg, session_dir, way, name, said):
    """Read, audio and video reach the model as one string of base64 millions of
    characters long, and a `.pptx` as a block the summarizer counts and the request drops.
    """
    what, reads = said
    (session_dir / "inputs" / name).write_bytes(os.urandom(64 * 1024))
    model = _calls(("read_file", {"file_path": f"/inputs/{name}"}))

    _run(cfg, session_dir, model, wait=way == "async")

    answer = _answer(model)
    assert answer.text == (
        f"Error: /inputs/{name} is {what}, which can't be handed to the model here. If you "
        f"hold a tool that can read {reads}, call it on /inputs/{name}; otherwise say that "
        "this part could not be done."
    )
    assert answer.status == "error"


def test_the_async_path_is_guarded_and_reads_what_the_model_was_offered(cfg, session_dir):
    """A turn driven by `astream` goes through `awrap_tool_call` and `awrap_model_call`
    alone, so a guard on the sync pair would hand that turn the PDF whole.
    """
    _with_pdf_text(cfg)
    (session_dir / "inputs" / "big.pdf").write_bytes(LARGE)
    model = _calls(("read_file", {"file_path": "/inputs/big.pdf"}))

    _run(cfg, session_dir, model, wait=True)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert "Call pdf_text on it" in answer.text


def test_a_call_after_no_model_call_names_no_tool():
    """A call replayed on a resume follows no model call here, and a guard that
    guessed would name a tool the graph may not hold.
    """
    guard = AttachmentsMiddleware(UNPROFILED)
    data = "A" * (400 * KB)
    result = ToolMessage(
        content=[{"type": "file", "base64": data, "mime_type": "application/pdf"}],
        tool_call_id="c1",
    )
    request = SimpleNamespace(tool_call={"name": "read_file", "args": {}, "id": "c1"})

    answer = guard.wrap_tool_call(request, lambda _: result)

    assert "Work from its text" in answer.text
    assert "pdf_text" not in answer.text


def test_a_tool_offered_as_a_dict_is_one_the_advice_may_name():
    """Read by its attribute alone, a tool offered as a dict went missing from what the
    model was offered, while the filter outside held it to the grant by name."""
    guard = AttachmentsMiddleware(UNPROFILED)
    data = "A" * (400 * KB)
    result = ToolMessage(
        content=[{"type": "file", "base64": data, "mime_type": "application/pdf"}],
        tool_call_id="c1",
    )
    request = SimpleNamespace(tool_call={"name": "read_file", "args": {}, "id": "c1"})

    guard.wrap_model_call(SimpleNamespace(tools=[{"name": "pdf_text"}]), lambda _: None)
    answer = guard.wrap_tool_call(request, lambda _: result)

    assert "Call pdf_text on it" in answer.text


def test_a_replaced_pdf_is_a_failed_call_and_leaves_no_saved_row(cfg, session_dir, way):
    """With the guard outside the recorder, the recorder saved an empty row for the PDF and
    stamped the result, and the stamp left the call reading as one that succeeded.

    Driven through what `standing_middleware` builds, with a real recorder and ledger: the
    order is the guarantee, and no test of the guard alone can see it.
    """
    from langchain_core.tools import tool

    from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
    from kingfisher.core.agent.middlewares.standing import standing_middleware
    from kingfisher.core.agent.middlewares.step_budget import StepBudget
    from kingfisher.core.session.ledger import Ledger
    from kingfisher.core.session.store import KeptLedger
    from tests.unit.test_workspace_tool_errors import _through

    data = "A" * (400 * KB)

    @tool(response_format="content_and_artifact")
    def export_pdf(note: str):
        """Return a large PDF."""
        return [{"type": "file", "base64": data, "mime_type": "application/pdf"}], None

    ledger = Ledger("t", KeptLedger())
    backend = backend_at(cfg, session_dir)
    stack = standing_middleware(
        "agent",
        names=frozenset({"export_pdf"}),
        paths=None,
        layout=cfg.layout,
        backend=backend,
        budget=StepBudget(cfg.recursion_limit),
        execution_timeout_s=cfg.execution_timeout_s,
        trigger=UNPROFILED,
        recorder=RecorderMiddleware(ledger, backend, None, cfg.layout.tool_results_route),
    )
    call = {"name": "export_pdf", "args": {"note": "x"}, "id": "c1"}

    answer = _through(stack, call, export_pdf, way)

    assert answer.text.startswith("export_pdf's result is a 300 KB PDF"), answer.text
    assert answer.status == "error"
    assert ledger.entries == ()


def test_the_guide_states_the_limits_the_guard_applies():
    """An author returning a file under a limit the guide names and the guard does not
    learns of it only from a model that never saw the file.
    """
    guide = " ".join(
        (repository_root() / "docs" / "guides" / "tools.md").read_text(encoding="utf-8").split()
    )

    assert f"{readable_size(pdf_allowance(UNPROFILED))} on a model with no profile" in guide
    assert f"an image over {MAX_IMAGE_CHARS // MB} MB of base64" in guide
    assert f"past {MAX_TURN_IMAGE_CHARS // MB} MB of images in the turn" in guide


def test_a_slice_pdf_pages_saves_is_handed_over_whole(cfg, session_dir):
    """The route a model that reads PDFs takes to a page of one too large to send: a
    slice the guard refused too would leave that model no way to see the page.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    shutil.copy(repository_root() / "assets" / "tools" / "pdf_pages.py", tools_dir(cfg))
    pages = [f"page {n} " + "x" * 1500 for n in range(1, 201)]
    (session_dir / "inputs" / "big.pdf").write_bytes(a_pdf(*pages))
    model = _calls(
        ("read_file", {"file_path": "/inputs/big.pdf"}),
        ("pdf_pages", {"file_path": "/inputs/big.pdf", "pages": "2"}),
        ("read_file", {"file_path": "/scratchpad/big.p2.pdf"}),
    )

    _run(cfg, session_dir, model)

    assert not _files([_answer(model, "c1")]), "the whole document was handed over"
    assert _answer(model, "c2").text.startswith("Saved big.p2.pdf (PDF, ")
    assert [b["mime_type"] for b in _files([_answer(model, "c3")])] == ["application/pdf"]


def _pdf_of(session_dir, name: str, size: int) -> dict:
    """A PDF of exactly `size` bytes in the session's inputs, and the call reading it."""
    (session_dir / "inputs" / name).write_bytes(b"%PDF-1.4\n" + b"0" * (size - 9))
    return {"file_path": f"/inputs/{name}"}


def _together(*calls: tuple[str, dict]) -> _Shown:
    """A model asking for every call in one reply, then answering."""
    asked = [{"name": name, "args": args, "id": f"c{n}"} for n, (name, args) in enumerate(calls, 1)]
    return _Shown(responses=[AIMessage(content="", tool_calls=asked), AIMessage(content="done")])


def test_pdfs_each_under_the_allowance_are_stopped_where_together_they_pass_it(
    cfg, session_dir
):
    """Judged one file at a time, ten 200 KB invoices each passed, and from the third on
    deepagents' summariser ran before every model call."""
    model = _calls(*(("read_file", _pdf_of(session_dir, f"p{n}.pdf", 100 * KB)) for n in (1, 2, 3)))

    _run(cfg, session_dir, model)

    assert [len(_files([_answer(model, f"c{n}")])) for n in (1, 2, 3)] == [1, 1, 0]
    assert _answer(model, "c3").text == (
        "/inputs/p3.pdf is a 100 KB PDF, and the PDFs already open in this turn take up "
        "200 KB of the 249 KB a turn holds. Work from its text, or from a part of it, instead."
    )


def test_images_each_under_5_mb_are_stopped_where_together_they_pass_10(cfg, session_dir):
    """Judged one at a time, four 4.5 MB images each passed, and together went out with every
    model call after them: 18 MB of a request Bedrock refuses past 20."""
    each = 4_718_592  # 4.5 MB of base64
    for n in (1, 2, 3):
        (session_dir / "inputs" / f"photo{n}.jpg").write_bytes(os.urandom(each * 3 // 4))
    model = _calls(*(("read_file", {"file_path": f"/inputs/photo{n}.jpg"}) for n in (1, 2, 3)))

    _run(cfg, session_dir, model)

    assert [len(_files([_answer(model, f"c{n}")])) for n in (1, 2, 3)] == [1, 1, 0]
    assert _answer(model, "c3").text == (
        "/inputs/photo3.jpg is a 4.5 MB image. The images already open in this turn total "
        "9.0 MB, and a turn holds at most 10 MB; work from those, or say what is left."
    )


@pytest.mark.parametrize(
    ("window", "size", "whole"),
    [
        (None, 200, True),
        (128_000, 200, False),
        (None, 300, False),
        (1_000_000, 300, True),
    ],
    ids=["no profile holds 200 KB", "128k does not", "no profile refuses 300 KB", "1M holds it"],
)
def test_what_a_turn_holds_follows_the_graphs_own_model(cfg, session_dir, window, size, whole):
    """One limit for every model is more than a 128k window's summariser lets through, and a
    fraction of what a 1M window holds."""
    model = _calls(("read_file", _pdf_of(session_dir, "doc.pdf", size * KB)))
    if window is not None:
        model.profile = {"max_input_tokens": window}

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert bool(_files([answer])) is whole, answer.text
    if not whole:
        assert answer.text.startswith(f"/inputs/doc.pdf is a {size} KB PDF, too large to hand")


def test_calls_asked_for_together_count_what_each_other_handed_over(cfg, session_dir, way):
    """Each call asked for in one reply runs against the conversation as it was before any
    of them answered, so three 100 KB PDFs read at once all went out whole, past the
    249 KB a turn holds."""
    model = _together(
        *(("read_file", _pdf_of(session_dir, f"p{n}.pdf", 100 * KB)) for n in (1, 2, 3))
    )

    _run(cfg, session_dir, model, wait=way == "async")

    answers = [_answer(model, f"c{n}") for n in (1, 2, 3)]
    assert sorted(len(_files([one])) for one in answers) == [0, 1, 1]
    (stopped,) = [one for one in answers if not _files([one])]
    assert "already open in this turn take up 200 KB of the 249 KB" in stopped.text


def test_another_run_calling_its_model_leaves_this_runs_tally_alone():
    """Reset at every model call, the tally of one run's calls in flight is wiped when a
    second run of the same subagent calls its model, as two `task` calls at once do, and
    the first run's next PDF goes out past the total."""
    guard = AttachmentsMiddleware(UNPROFILED)

    def asking(*ids: str, message: str) -> list:
        calls = [{"name": "export_pdf", "args": {}, "id": one} for one in ids]
        return [HumanMessage("go"), AIMessage(content="", id=message, tool_calls=calls)]

    first, second = asking("a1", "a2", message="m-a"), asking("b1", message="m-b")

    def read(conversation: list, call_id: str, size: int) -> ToolMessage:
        data = "A" * (4 * (size // 3))
        result = ToolMessage(
            content=[{"type": "file", "base64": data, "mime_type": "application/pdf"}],
            tool_call_id=call_id,
        )
        call = {"name": "export_pdf", "args": {}, "id": call_id}
        request = SimpleNamespace(tool_call=call, state={"messages": conversation})
        return guard.wrap_tool_call(request, lambda _: result)

    assert _files([read(first, "a1", 200 * KB)]), "the first PDF was not handed over"
    guard.wrap_model_call(SimpleNamespace(tools=[], state={"messages": second}), lambda r: r)

    assert "take up 200 KB of the 249 KB" in read(first, "a2", 100 * KB).text


HELPER = (
    "name: helper\ndescription: Reads.\nmodel: cheap-model\n"
    'builtin_tools: ["read_file"]\n'
    "system_prompt: |\n  You read.\n"
)


def _first_shown(call_id: str) -> ToolMessage:
    """The result of `call_id` as a `Scripted` model was first handed it."""
    return next(
        one
        for heard in Scripted.heard
        for one in heard
        if isinstance(one, ToolMessage) and one.tool_call_id == call_id
    )


def test_a_subagent_on_another_model_holds_what_its_own_model_holds(scripted):
    """Held to the agent's allowance, a subagent on a 1M window was refused a 300 KB PDF
    its own summariser is nowhere near."""
    models = dict(scripted.models.models)
    models["cheap-model"] = replace(
        models["cheap-model"], extra={"profile": {"max_input_tokens": 1_000_000}}
    )
    cfg = replace(scripted, models=replace(scripted.models, models=models))
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(cfg, subagents="[helper]", **EVERY_TOOL)
    read = _pdf_of(cfg.workspace / "sessions" / start(cfg, "s"), "big.pdf", 300 * KB)
    asked = {"description": "read it", "subagent_type": "helper"}
    Scripted.script.extend([
        AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}]),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": read, "id": "s1"}]),
        AIMessage("it holds zeros"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": read, "id": "c2"}]),
        AIMessage("done"),
    ])

    Kingfisher(cfg, sessions=LocalSessions()).run(Request("go", agent="only", session_id="s"))

    assert _files([_first_shown("s1")]), "the subagent was held to the agent's allowance"
    assert _first_shown("c2").text.startswith("/inputs/big.pdf is a 300 KB PDF, too large")


def test_a_new_turn_holds_no_pdf_an_earlier_turn_opened(scripted):
    """Carried into the next turn, the total would refuse a PDF in a turn holding none: what
    an earlier turn opened comes back as its result's text alone."""
    an_agent(scripted, **EVERY_TOOL)
    folder = scripted.workspace / "sessions" / start(scripted, "s")
    # Both before the first turn, which leaves the inputs read-only.
    reads = [_pdf_of(folder, f"p{n}.pdf", 200 * KB) for n in (1, 2)]
    service = Kingfisher(scripted, sessions=LocalSessions())
    for n, read in enumerate(reads, 1):
        Scripted.script.extend([
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": read, "id": f"r{n}"}]),
            AIMessage("read"),
        ])
        service.run(Request("go", agent="only", session_id="s"))

    second = next(heard for heard in Scripted.heard if any(
        isinstance(one, ToolMessage) and one.tool_call_id == "r2" for one in heard
    ))
    assert any(isinstance(one, ToolMessage) and one.tool_call_id == "r1" for one in second), (
        "the earlier turn was not replayed, so this passes whatever the total carries"
    )
    assert _files([_first_shown("r2")]), "the second turn's PDF was refused"
