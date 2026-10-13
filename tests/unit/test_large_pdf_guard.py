"""A PDF too large to read whole reaches the model as advice on reading it, never whole.

Driven through `build_agent` rather than the middleware alone, because which graphs
carry the guard and which tools it may name are both decided where the agent is built.
"""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import backend_at
from kingfisher.infrastructure.harness.middlewares.large_pdf_guard import (
    MAX_PDF_BYTES,
    LargePdfGuard,
)
from tests.conftest import a_pdf, repository_root, tools_dir
from tests.unit.test_offloads import _Shown

#: Past the limit by a margin, and not a PDF anything parses: `read_file` goes by the
#: extension and never opens it.
LARGE = b"%PDF-1.4\n" + b"0" * (MAX_PDF_BYTES + 44 * 1024)

#: A PDF tool returning one, which nothing about its name says it will.
EXPORTER = f'''
import base64


def export_pdf() -> list:
    """Return a PDF. Use never; this is a fixture."""
    data = base64.b64encode(b"%PDF-1.4" + b"0" * {MAX_PDF_BYTES + 44 * 1024}).decode()
    return [{{"type": "file", "base64": data, "mime_type": "application/pdf"}}]


TOOLS = [export_pdf]
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
    return [b for m in messages for b in m.content_blocks if b.get("type") == "file"]


def _with_pdf_text(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    shutil.copy(repository_root() / "assets_examples" / "tools" / "pdf_text.py", tools_dir(cfg))


def test_a_large_pdf_read_whole_reaches_the_model_as_advice(cfg, session_dir):
    """Handed over whole, one large PDF trips the summarizer, which keeps it and sends
    it with no retry, and the turn ends on a context overflow.
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
    assert _answer(model, "c2").text.startswith("Saved big.p1-50.txt (text, ")


def test_an_agent_not_granted_pdf_text_is_not_told_to_call_it(cfg, session_dir):
    """`tool_guards` is handed every tool the workspace defines, so advice read off its
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
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "exporter.py").write_text(EXPORTER, encoding="utf-8")
    model = _calls(("export_pdf", {}))

    _run(cfg, session_dir, model)

    answer = _answer(model)
    assert not _files([answer]), "the model was handed the PDF whole"
    assert answer.text.startswith("export_pdf's result is a 300 KB PDF"), answer.text


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
    guard = LargePdfGuard()
    data = "A" * (4 * (MAX_PDF_BYTES // 3 + 1024))
    result = ToolMessage(
        content=[{"type": "file", "base64": data, "mime_type": "application/pdf"}],
        tool_call_id="c1",
    )
    request = SimpleNamespace(tool_call={"name": "read_file", "args": {}, "id": "c1"})

    answer = guard.wrap_tool_call(request, lambda _: result)

    assert "Work from its text" in answer.text
    assert "pdf_text" not in answer.text


def test_the_guide_states_the_limit_the_guard_applies():
    """An author returning a PDF under a limit the guide names and the guard does not
    learns of it only from a model that never saw the file.
    """
    guide = (repository_root() / "docs" / "guides" / "tools.md").read_text(encoding="utf-8")

    assert f"A PDF `file` block over {MAX_PDF_BYTES // 1024} KB" in guide


def test_a_slice_pdf_pages_saves_is_handed_over_whole(cfg, session_dir):
    """The route a model that reads PDFs takes to a page of one too large to send: a
    slice the guard refused too would leave that model no way to see the page.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    shutil.copy(repository_root() / "assets_examples" / "tools" / "pdf_pages.py", tools_dir(cfg))
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
