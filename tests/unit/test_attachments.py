"""A file a tool's result carries reaches the model only when one may take it, and
`read_file` is not run on a file no model here takes as it is.

Driven through `build_agent` rather than the middleware alone, because which graphs
carry the guard and which tools it may name are both decided where the agent is built.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.attachments import (
    MAX_IMAGE_CHARS,
    MAX_PDF_BYTES,
    MB,
    AttachmentsMiddleware,
)
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import a_pdf, repository_root, tools_dir
from tests.unit.test_offloads import _Shown

#: Past the limit by a margin, and not a PDF anything parses: `read_file` goes by the
#: extension and never opens it.
LARGE = b"%PDF-1.4\n" + b"0" * (MAX_PDF_BYTES + 44 * 1024)

DECK = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: Tools returning files, which nothing about their names says they will.
EXPORTER = f'''
import base64
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

PDF = base64.b64encode(b"%PDF-1.4" + b"0" * {MAX_PDF_BYTES + 44 * 1024}).decode()
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
    assert len(big) > MAX_PDF_BYTES, "not large enough to be refused"
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
    guard = AttachmentsMiddleware()
    data = "A" * (4 * (MAX_PDF_BYTES // 3 + 1024))
    result = ToolMessage(
        content=[{"type": "file", "base64": data, "mime_type": "application/pdf"}],
        tool_call_id="c1",
    )
    request = SimpleNamespace(tool_call={"name": "read_file", "args": {}, "id": "c1"})

    answer = guard.wrap_tool_call(request, lambda _: result)

    assert "Work from its text" in answer.text
    assert "pdf_text" not in answer.text


def test_a_replaced_pdf_is_a_failed_call_and_leaves_no_saved_row(cfg, session_dir, way):
    """With the guard outside the recorder, the recorder saved an empty row for the PDF and
    stamped the result, and the stamp left the call reading as one that succeeded.

    Driven through what `standing_middleware` builds, with a real recorder and ledger: the
    order is the guarantee, and no test of the guard alone can see it.
    """
    from langchain_core.tools import tool

    from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
    from kingfisher.core.agent.middlewares.standing import standing_middleware
    from kingfisher.core.session.ledger import Ledger
    from kingfisher.core.session.store import KeptLedger
    from tests.unit.test_workspace_tool_errors import _through

    data = "A" * (4 * (MAX_PDF_BYTES // 3 + 1024))

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
        step_limit=cfg.recursion_limit,
        recorder=RecorderMiddleware(ledger, backend, None, cfg.layout.tool_results_route),
    )
    call = {"name": "export_pdf", "args": {"note": "x"}, "id": "c1"}

    answer = _through(stack, call, export_pdf, way)

    assert answer.text.startswith("export_pdf's result is a 259 KB PDF"), answer.text
    assert answer.status == "error"
    assert ledger.entries == ()


def test_the_guide_states_the_limits_the_guard_applies():
    """An author returning a file under a limit the guide names and the guard does not
    learns of it only from a model that never saw the file.
    """
    guide = (repository_root() / "docs" / "guides" / "tools.md").read_text(encoding="utf-8")

    assert f"a PDF over {MAX_PDF_BYTES // 1024} KB" in guide
    assert f"an image over {MAX_IMAGE_CHARS // MB} MB of base64" in guide


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
