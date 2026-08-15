# Real Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `main.py` show the model's prose as it is generated, and show what each tool was actually called with, instead of printing only node-boundary summaries.

**Architecture:** LangGraph's `messages` stream mode is added to `STREAM_MODES`, translated in `adapters/runtime.py` into a new `RunEvent(kind="token")`, and written to the terminal as un-decorated fragments. Because tokens now carry all prose, the `message` event kind is collapsed into `model_call`, which gains the tool arguments it was already discarding. Mode dispatch moves out of `app/` and into the adapter.

**Tech Stack:** Python 3.12, LangGraph / LangChain (behind `adapters/runtime.py`), deepagents, pytest.

**Spec:** This document. It was produced by a grilling session on 2026-08-16; the decisions and the evidence behind them are recorded in "Design Decisions" and "Evidence" below, because every non-obvious choice here was argued and several reversed mid-discussion.

---

## Global Constraints

- **Layering is enforced, not remembered.** `tests/test_architecture.py` parses imports: `domain/` may not import langchain/langgraph/deepagents; `app/` may reach the harness **only** through `adapters/`. Every LangChain shape in this plan lands in `adapters/runtime.py`.
- **No new dependencies.** loguru and rich were considered and deferred (see "Explicitly Out of Scope").
- **Commit style matches the repo:** an imperative sentence describing the change, no `feat:`/`fix:` prefixes. Compare `Stop the framework asking for report.md and result.json`, `Reach the whole request from main.py, and stop a typo being destructive`.
- **`ruff` and `ty` are configured** and must stay green. Notably: magic numbers trip `PLR2004` (name them), and deferred imports inside functions need `# noqa: PLC0415`, which `main.py` already uses.
- **A rebase is likely.** At the time of writing, HEAD is `c6ff08f` with an in-flight refactor uncommitted (`domain/workspace.py` deleted, new `adapters/workspace_fs.py`, `workspace_git.py`, `subagent_store.py`, `domain/layout.py`). The four files this plan touches — `domain/result.py`, `adapters/runtime.py`, `app/run.py`, `main.py` — are **not** part of that refactor, but line numbers cited below may drift. Re-grep before editing.

---

## Design Decisions

Each was resolved deliberately; the reasoning is recorded so a reviewer can reject the reasoning rather than guess at it.

1. **Both token-level prose and richer structural signal.** Node-boundary events alone leave the terminal silent during the one thing that takes minutes — a single long model turn.
2. **`stream()` yields tokens as `RunEvent`s**, not via a callback. A callback creates a second ordering domain, and interleaving with structural events is the entire point.
3. **The streamed text and `result.answer` are identical by construction**, so `main.py` never reprints the answer. Today that costs nothing (see decision 5).
4. **A `channel` field, not a `reasoning` kind.** The channel is provider-independent; the extraction mechanism is not (`<think>` text here, `thinking` blocks on real Anthropic, `reasoning_content` elsewhere). A field keeps provider differences inside `adapters/`.
5. **No `<think>` state machine.** Probing found no `<think>` on *either* MiniMax surface, so a chunk-boundary splitter would be tested only against fixtures the author invented. `normalize_answer` stays as the safety net. `channel` is carried but always `"answer"` today — deliberately unexercised surface, and the one piece of speculative API here.
6. **Layout: bare prose, tagged structure.** Prose owns the left margin. Prefixed continuation lines would require wrapping against `COLUMNS` and would fight the model's own markdown, which arrives mid-chunk.
7. **Everything to stdout, no TTY branch.** A second rendering path is one that rots. A caller wanting clean output uses the library (`run()` → `result.answer`), which is what `main.py:3` says it is for.
8. **`adapters/runtime.py` owns mode dispatch.** `"values"` and `"messages"` are LangGraph vocabulary, as foreign as `input_token_details` was.
9. **Streaming is always on, with no opt-out flag.** The deciding argument is operational, not aesthetic: with `timeout_s=120` and `max_tokens=4096`, a non-streaming POST must complete the *entire* generation inside 120s, whereas SSE resets the read clock per chunk. Streaming is how a long turn *survives*, so the batch entrypoint must not be the fragile one.
10. **`kind="message"` is collapsed into `model_call`.** Once tokens carry all prose, the two events are the same thing — a completed model turn with its usage — split only by whether there was text to show.
11. **Tool arguments are emitted complete at the node boundary**, not streamed. Half a JSON path is unreadable and unactionable, and the fragments would interleave with prose from the same turn.
12. **`str(token_event)` returns raw text.** A token is a fragment; a tag would assert a line boundary that does not exist, and `_line()` would destroy the model's markdown.
13. **The renderer is extracted and tested.** `main.py` currently has zero test coverage — nothing imports it — and every failure mode here is silent and textual.

---

## Evidence

Gathered by probing the live gateway (MiniMax-M3 via `api.minimaxi.com/anthropic`). Re-run these if a decision looks wrong.

- **Token streaming works through the compiled graph.** Real deltas: `"I'll"`, `" write the number "`, `"7 to"`, `" seven"`. Chunks split mid-word.
- **Granularity is coarse.** Expect sentence-bursts, not a typewriter. One observed chunk: `' number 7. Specifically:\n\n1. **The file doesn\'t exist** — the `/data/` directory'`.
- **Token accounting survives.** Same task with and without `messages` mode: `in=7044 out=2`, `usage_present=True` in both.
- **Subagent tokens do NOT leak.** deepagents does not propagate the parent's streaming callbacks into a subagent graph; only its final answer returns, as a tool result. No filtering is needed — and no subagent visibility is available.
- **Tool results DO arrive on the `messages` stream, untruncated.** They must be skipped, or `PREVIEW` protection is bypassed and a 50KB file read reaches the terminal.
- **Content shape differs by surface.** Anthropic surface → `str`; OpenAI-compatible surface → `list` of blocks. `str(content)` yields `"[{'type': 'text', 'text': '…'}]"` on the latter. `.text` flattens both correctly.
- **`.text` is a callable `str` subclass** in this langchain-core version — coerce with `str(...)` so it does not leak into the domain.
- **`AIMessageChunk` is a subclass of `AIMessage`**, so any `isinstance` check must be ordered deliberately.
- **No `<think>` output on either surface**, even for a genuine reasoning prompt (82 and 57 chunks of step-by-step, zero `<think>`).

---

## Explicitly Out of Scope

- **loguru.** It emits levelled, timestamped, newline-terminated *records*; layout (6) requires un-decorated fragments with no newline. It also cannot be a `BaseCallbackHandler`, so it cannot replace `JsonlRunLogger` — the callback class would remain and loguru would replace only six lines of `_write`. If adopted later, `tests/test_architecture.py`'s `FOREIGN` tuple must be extended, or the layering guard silently stops covering a new foreign dependency.
- **Styling** (dimmed reasoning, coloured tags). A separate concern from streaming; the right tool is ANSI or `rich`, not a logger.
- **A `<think>` incremental splitter.** See decision 5.
- **Streaming tool-call arguments live.** See decision 11.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/kingfisher/domain/result.py` | Kingfisher's own run vocabulary | Modify: `RunEvent` gains `args` and `channel`; `__str__` renders tool arguments; the `message` branch is removed; a `token` branch is added |
| `src/kingfisher/adapters/runtime.py` | The anticorruption layer — the only module that knows LangChain shapes | Modify: `STREAM_MODES` gains `"messages"`; new `text_of`, `tool_calls`, `_token_event`; `events_in`/`answer_in` become mode-aware |
| `src/kingfisher/app/run.py` | Orchestration | Modify: the stream loop stops comparing `mode` to string literals |
| `main.py` | The driver — all presentation | Modify: extract `render(events, out)`; stop reprinting the answer |
| `tests/test_main.py` | Renderer behaviour | **Create** — `main.py` has no test coverage today |
| `tests/test_stream.py` | Event stream behaviour | Modify: new token/arg/collapse tests |
| `tests/test_run.py` | Orchestration; hosts `StubAgent` | Modify: `StubAgent` gains a `tokens=` parameter |

---

### Task 1: Tool results read `.text`, not `str(content)`

A live bug, independent of streaming: on the OpenAI-compatible surface, tool result content is a list of blocks and `str()` renders a Python list repr into the terminal. `runtime.py`'s own `final_text` already uses the correct accessor — the two disagree inside one file.

**Files:**
- Modify: `src/kingfisher/adapters/runtime.py` (the `ToolMessage` branch of `_event_for`, ~line 67)
- Test: `tests/test_stream.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `runtime.text_of(message: Any) -> str` — used by Tasks 2, 4 and 5.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_stream.py`:

```python
def test_block_shaped_tool_content_renders_as_text(cfg):
    """The OpenAI-compatible surface returns content as a list of blocks.

    `str(content)` would print "[{'type': 'text', 'text': 'hi'}]" at the user.
    """
    agent = StubAgent(
        "ok",
        updates=[
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=[{"type": "text", "text": "hi"}],
                            name="execute",
                            tool_call_id="c",
                        )
                    ]
                }
            }
        ],
    )
    (tool,) = [e for e in _events(cfg, agent) if e.kind == "tool_result"]

    assert tool.text == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stream.py::test_block_shaped_tool_content_renders_as_text -v`
Expected: FAIL — `assert "[{'type': 'text', 'text': 'hi'}]" == "hi"`

- [ ] **Step 3: Add `text_of` and use it**

In `src/kingfisher/adapters/runtime.py`, add after `usage_of`:

```python
def text_of(message: Any) -> str:
    """A message's text, whatever shape its content takes.

    `.text` flattens both a plain string and a list of content blocks. The
    OpenAI-compatible surface returns the latter, where `str(content)` renders
    a Python list repr at the user. Coerced with `str()` because langchain
    returns a callable `str` subclass, which has no business in the domain.
    """
    return str(getattr(message, "text", "") or "")
```

Then in `_event_for`, replace `text=str(message.content)[:PREVIEW]` with:

```python
            text=text_of(message)[:PREVIEW],
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/ -q`
Expected: PASS, including the pre-existing `test_multiline_tool_output_renders_on_one_line`.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/adapters/runtime.py tests/test_stream.py
git commit -m "$(cat <<'EOF'
Read a tool result's text instead of stringifying its content

The OpenAI-compatible surface returns content as a list of blocks, where
str() renders a Python list repr at the user. `final_text` in this same
module already used the right accessor; the two disagreed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Collapse `message` into `model_call`, and stop discarding tool arguments

Two changes that must land together, because the first removes the branch that made the second look impossible.

Today `_event_for` throws prose away whenever a message also has tool calls (`runtime.py:73-77`), so the model's narration is invisible on exactly the turns where it explains what it is about to do. Meanwhile `tool_names` reads `message.tool_calls` and discards the `args` — so the JSONL knows what `write_file` was called with (`runlog.py:76-78`) and the terminal does not.

Once tokens carry all prose (Task 5), `message` and `model_call` are the same event. Collapsing them now means Task 5 introduces no duplication to clean up.

**Files:**
- Modify: `src/kingfisher/domain/result.py`
- Modify: `src/kingfisher/adapters/runtime.py`
- Test: `tests/test_stream.py`

**Interfaces:**
- Consumes: `runtime.text_of` (Task 1).
- Produces:
  - `RunEvent.args: tuple[Mapping[str, Any], ...]` — index-aligned with `RunEvent.tools`, enforced in `__post_init__`.
  - `RunEvent.channel: str = "answer"` — carried now, used in Task 5.
  - `runtime.tool_calls(message) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]`.
  - `runtime.tool_names` keeps its signature; `runlog.py` needs no change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stream.py`:

```python
def test_a_tool_call_carries_its_arguments(cfg):
    """The JSONL has recorded these all along; the terminal never showed them."""
    (call,) = [e for e in _events(cfg, _agent_with_a_tool_call()) if e.kind == "model_call"]

    assert call.tools == ("execute",)
    assert call.args == ({"command": "echo hi"},)
    assert len(call.args) == len(call.tools)  # parallel arrays, pinned


def test_a_text_only_turn_is_a_model_call_with_no_tools(cfg):
    """`message` is collapsed: a completed turn is a completed turn."""
    agent = StubAgent(
        "ok", updates=[{"agent": {"messages": [AIMessage(content="thinking out loud")]}}]
    )
    events = _events(cfg, agent)

    assert "message" not in [e.kind for e in events]
    (call,) = [e for e in events if e.kind == "model_call"]
    assert call.tools == ()
    assert call.args == ()


def test_a_large_tool_argument_cannot_flood_the_line(cfg):
    """`write_file` takes an entire file as an argument."""
    agent = StubAgent(
        "ok",
        updates=[
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"file_path": "/data/x", "content": "x" * 5000},
                                    "id": "c1",
                                }
                            ],
                        )
                    ]
                }
            }
        ],
    )
    (call,) = [e for e in _events(cfg, agent) if e.kind == "model_call"]

    assert "/data/x" in str(call)
    assert len(str(call)) < 300
    assert "…" in str(call)


def test_tools_and_args_must_be_parallel():
    """Two tuples that must agree; the constructor is where that is enforced."""
    from kingfisher.domain.result import RunEvent

    with pytest.raises(ValueError, match="parallel"):
        RunEvent(kind="model_call", tools=("a", "b"), args=({},))
```

Add `import pytest` and `AIMessage` to the imports at the top of `tests/test_stream.py`:

```python
import pytest
from langchain_core.messages import AIMessage, ToolMessage
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stream.py -v -k "argument or text_only or parallel"`
Expected: FAIL — `RunEvent` has no attribute `args`.

- [ ] **Step 3: Add the fields and rendering to `domain/result.py`**

Extend the imports:

```python
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
```

Add above `RunEvent`:

```python
#: How much of one tool argument to show. `write_file` takes an entire file as
#: an argument; unbounded, one call would fill the terminal.
ARG_PREVIEW = 60


def _flatten(value: Any, limit: int) -> str:
    """One value as a single bounded line."""
    flat = " ".join(str(value).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _render_call(name: str, args: Mapping[str, Any]) -> str:
    """`write_file(file_path=/data/x.csv, content=id,name…)`."""
    if not args:
        return name
    inner = ", ".join(f"{key}={_flatten(value, ARG_PREVIEW)}" for key, value in args.items())
    return f"{name}({inner})"
```

Replace the `RunEvent` field block and add the invariant:

```python
@dataclass(frozen=True)
class RunEvent:
    """A normalised step in a run.

    kinds: `run_start`, `swept`, `sweep_failed`, `model_call`, `tool_result`,
    `token`, `finished`.

    There is no `message` kind. A completed assistant turn is a `model_call`
    whatever it produced; its prose arrives as `token` events as it is
    generated, which is the only place prose lives.
    """

    kind: str
    text: str = ""
    tool: str | None = None
    tools: tuple[str, ...] = ()
    #: Index-aligned with `tools`. Two tuples rather than one tuple of pairs
    #: because `tools` is the older published shape.
    args: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    #: Which stream a `token` belongs to: `answer`, or `reasoning` for a
    #: provider that separates it. Nothing produces `reasoning` yet.
    channel: str = "answer"
    result: RunResult | None = None

    def __post_init__(self) -> None:
        if self.args and len(self.args) != len(self.tools):
            msg = f"args ({len(self.args)}) must be parallel to tools ({len(self.tools)})"
            raise ValueError(msg)
```

In `__str__`, replace the `model_call` branch and delete the `message` branch:

```python
        if self.kind == "model_call":
            pairs = zip(self.tools, self.args or ({},) * len(self.tools), strict=True)
            calls = ", ".join(_render_call(name, args) for name, args in pairs)
            cached = self.usage.get("cache_read", 0)
            arrow = f"→ {calls}" if calls else ""
            return f"[model] {arrow}  (in={self.usage.get('input_tokens', 0)} cached={cached})"
```

- [ ] **Step 4: Collapse the branch in `adapters/runtime.py`**

Replace `tool_names` with:

```python
def tool_calls(message: Any) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    """Names and arguments of the tools a message asks for, index-aligned.

    Built in one expression from one list, so the two tuples cannot drift.
    """
    calls = getattr(message, "tool_calls", None) or []
    return (
        tuple(call["name"] for call in calls),
        tuple(call.get("args") or {} for call in calls),
    )


def tool_names(message: Any) -> tuple[str, ...]:
    """Names of the tools a message asks for, or an empty tuple."""
    return tool_calls(message)[0]
```

Replace `_event_for` with:

```python
def _event_for(message: Any) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=text_of(message)[:PREVIEW],
        )
    if isinstance(message, AIMessage):
        # Every assistant turn is a model call, whether or not it asked for a
        # tool. Its prose is not carried here: it has already arrived as
        # tokens, and repeating it truncated would be the same text twice.
        names, args = tool_calls(message)
        return RunEvent(kind="model_call", tools=names, args=args, usage=usage_of(message))
    return None
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ty check`
Expected: PASS. `tests/test_stream.py::test_stream_surfaces_tool_names_and_cache_usage` and `test_events_render_as_readable_lines` still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/domain/result.py src/kingfisher/adapters/runtime.py tests/test_stream.py
git commit -m "$(cat <<'EOF'
Show what a tool was called with, and stop splitting a turn in two

A completed assistant turn was two different events depending on whether it
had text to show, and the tool-calling half threw the model's narration
away -- so an explanation of what it was about to do was visible only on the
turns that did nothing. One kind now, with the arguments the run log has
been recording all along and the terminal never showed.

Argument values are bounded: write_file takes an entire file as one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Extract the render loop, and give `main.py` its first test

No behaviour change. This exists so Tasks 5's newline discipline lands somewhere testable — `main.py` currently has zero coverage, and every failure mode in this plan is silent and textual.

**Files:**
- Modify: `main.py` (the loop at ~lines 246-256, and the answer print at ~line 274)
- Test: `tests/test_main.py` (**create**)

**Interfaces:**
- Consumes: `RunEvent`, `RunResult` from Task 2.
- Produces: `main.render(events: Iterable[RunEvent], out: TextIO) -> RunResult | None` — Task 5 extends it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main.py`:

```python
"""The driver's rendering, which had no tests at all.

Every failure mode here is silent and textual -- a jammed newline, an
unbounded argument, a tool result leaking through as prose -- so "run it and
look" is exactly the check that misses them.
"""

from __future__ import annotations

import io
from pathlib import Path

import main
from kingfisher.domain.result import RunEvent, RunResult


def _render(events: list[RunEvent]) -> tuple[str, RunResult | None]:
    out = io.StringIO()
    result = main.render(iter(events), out)
    return out.getvalue(), result


def _result() -> RunResult:
    return RunResult(
        session_id="s",
        turn_id="t001",
        answer="42",
        run_dir=Path("/tmp/run"),
        log_path=Path("/tmp/log"),
        swept=(),
        commit=None,
    )


def test_structural_events_render_one_per_line():
    text, _ = _render(
        [
            RunEvent(kind="run_start", text="/runs/s/t001"),
            RunEvent(kind="model_call", tools=("execute",), args=({"command": "ls"},)),
        ]
    )

    assert text.splitlines() == ["[start] /runs/s/t001", "[model] → execute(command=ls)  (in=0 cached=0)"]


def test_the_finished_event_is_returned_not_printed():
    """The answer streamed as it was generated; reprinting it says it twice."""
    expected = _result()
    text, result = _render([RunEvent(kind="finished", text="42", result=expected)])

    assert result is expected
    assert text == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL — `module 'main' has no attribute 'render'`.

- [ ] **Step 3: Extract `render` in `main.py`**

Add to the imports at the top of `main.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher.domain.result import RunEvent, RunResult
```

Add above `main()`:

```python
def render(events: Iterable[RunEvent], out: TextIO) -> RunResult | None:
    """Print a run as it happens, and return its result.

    The terminal event carries the `RunResult` and is not printed: there is
    nothing to say that the preceding events have not already said.
    """
    result: RunResult | None = None
    for event in events:
        if event.kind == "finished":
            result = event.result
        else:
            print(event, file=out, flush=True)
    return result
```

Replace the loop in `main()`:

```python
    result = None
    try:
        result = render(stream(request, cfg=cfg), sys.stdout)
    except CapabilityError as exc:
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/ -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Verify the driver still runs end to end**

Run: `uv run main.py --list`
Expected: workspace, tools, skills and subagents listed, exit 0. (No model call; `--list` returns before the stream.)

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "$(cat <<'EOF'
Make the driver's rendering a function, and test it

No behaviour change. main.py had no test coverage at all, and the rendering
about to be added to it fails in ways that only show on inputs you did not
happen to run: a jammed newline, an unbounded argument, a tool result
leaking through as prose.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Move mode dispatch into the adapter

No behaviour change. `run.py` compares `mode` to `"values"`, which is LangGraph vocabulary sitting in `app/` — the exact duplication `runtime.py`'s own docstring was written to end. Task 5 adds a third mode, so this is the moment to stop the pattern spreading.

**Files:**
- Modify: `src/kingfisher/adapters/runtime.py`
- Modify: `src/kingfisher/app/run.py` (the loop at ~lines 159-173)
- Test: `tests/test_stream.py`

**Interfaces:**
- Consumes: `runtime.text_of` (Task 1).
- Produces:
  - `runtime.events_in(mode: str, chunk: Any) -> Iterator[RunEvent]` — signature **changed**, now takes the mode first.
  - `runtime.answer_in(mode: str, chunk: Any) -> str | None` — replaces `final_text`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_stream.py`:

```python
def test_the_adapter_owns_mode_dispatch():
    """`values` and `messages` are LangGraph's words, not orchestration's."""
    from langchain_core.messages import AIMessage as _AIMessage

    from kingfisher.adapters import runtime

    values = {"messages": [_AIMessage(content="42")]}

    assert runtime.answer_in("values", values) == "42"
    assert runtime.answer_in("updates", values) is None
    assert list(runtime.events_in("values", values)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stream.py::test_the_adapter_owns_mode_dispatch -v`
Expected: FAIL — `module 'kingfisher.adapters.runtime' has no attribute 'answer_in'`.

- [ ] **Step 3: Make the adapter mode-aware**

In `src/kingfisher/adapters/runtime.py`, replace `events_in` and `final_text`:

```python
def events_in(mode: str, chunk: Any) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events.

    Mode dispatch lives here rather than in `app/`: `updates`, `values` and
    `messages` are LangGraph's vocabulary, and the orchestration above should
    no more compare against them than it should read `input_token_details`.
    """
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if (event := _event_for(message)) is not None:
                yield event


def answer_in(mode: str, chunk: Any) -> str | None:
    """The assistant's last message from a `values` chunk, if it has one."""
    if mode != "values":
        return None
    messages = messages_in(chunk)
    if not messages:
        return None
    return text_of(messages[-1])
```

- [ ] **Step 4: Simplify the loop in `app/run.py`**

Replace the body of the `for mode, chunk in graph.stream(...)` loop:

```python
            if (text := runtime.answer_in(mode, chunk)) is not None:
                answer = text
            yield from runtime.events_in(mode, chunk)
```

The `if mode == "values": ... continue` block and its comment go away — the comment's content now lives on `answer_in`.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ty check`
Expected: PASS. `app/run.py` no longer contains the string `"values"`; confirm with `grep -n '"values"' src/kingfisher/app/run.py` returning nothing.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/adapters/runtime.py src/kingfisher/app/run.py tests/test_stream.py
git commit -m "$(cat <<'EOF'
Give the adapter the stream modes, which were never orchestration's words

run.py compared mode against "values" -- a LangGraph name in a module whose
whole claim is that it speaks only kingfisher's. A third mode is about to
arrive, so this is the moment the pattern stops rather than spreads.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Stream the tokens

The payload. Everything before this was clearing the ground.

**Files:**
- Modify: `src/kingfisher/adapters/runtime.py`
- Modify: `main.py`
- Test: `tests/test_stream.py`, `tests/test_main.py`, `tests/test_run.py` (`StubAgent`)

**Interfaces:**
- Consumes: `RunEvent.channel` (Task 2), `runtime.events_in(mode, chunk)` (Task 4), `main.render` (Task 3).
- Produces: `RunEvent(kind="token", text=..., channel="answer")`.

- [ ] **Step 1: Teach `StubAgent` to emit token chunks**

In `tests/test_run.py`:

```python
class StubAgent:
    """Stands in for the compiled graph so the orchestration is testable.

    Emits the same (mode, chunk) shape LangGraph does for
    `stream_mode=["updates", "values", "messages"]`. A `messages` chunk is a
    `(message, metadata)` pair, which is why `tokens` is a list of pairs.
    """

    def __init__(
        self, answer: str, *, updates: list | None = None, tokens: list | None = None
    ) -> None:
        self.answer = answer
        self.updates = updates or []
        self.tokens = tokens or []
        self.state: dict | None = None
        self.config: dict | None = None

    def stream(self, state, config, stream_mode=None):
        self.state, self.config = state, config
        for chunk in self.tokens:
            yield ("messages", chunk)
        for update in self.updates:
            yield ("updates", update)
        yield ("values", {"messages": [AIMessage(content=self.answer)]})
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_stream.py` (extend the import to `from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage`):

```python
def test_prose_arrives_as_token_fragments(cfg):
    """Chunks split mid-word; the renderer, not the domain, reassembles."""
    agent = StubAgent(
        "42",
        tokens=[
            (AIMessageChunk(content="7 to"), {"langgraph_node": "model"}),
            (AIMessageChunk(content=" seven.txt"), {"langgraph_node": "model"}),
        ],
    )
    tokens = [e for e in _events(cfg, agent) if e.kind == "token"]

    assert [t.text for t in tokens] == ["7 to", " seven.txt"]
    assert all(t.channel == "answer" for t in tokens)


def test_a_token_renders_as_its_bare_text(cfg):
    """A fragment is not a line: a tag would assert a boundary that is not there."""
    agent = StubAgent("x", tokens=[(AIMessageChunk(content="a\n\nb"), {})])
    (token,) = [e for e in _events(cfg, agent) if e.kind == "token"]

    assert str(token) == "a\n\nb"  # not collapsed, not prefixed


def test_tool_results_on_the_token_stream_are_not_prose(cfg):
    """They arrive there untruncated; `tool_result` already bounds them."""
    agent = StubAgent(
        "ok",
        tokens=[
            (
                ToolMessage(content="x" * 5000, name="read_file", tool_call_id="c"),
                {"langgraph_node": "tools"},
            )
        ],
    )

    assert not [e for e in _events(cfg, agent) if e.kind == "token"]


def test_usage_only_chunks_produce_no_token(cfg):
    """The final chunk of a turn carries usage and no text."""
    agent = StubAgent("ok", tokens=[(AIMessageChunk(content=""), {"langgraph_node": "model"})])

    assert not [e for e in _events(cfg, agent) if e.kind == "token"]
```

Add to `tests/test_main.py`:

```python
def test_prose_and_tagged_lines_do_not_jam_together():
    text, _ = _render(
        [
            RunEvent(kind="token", text="I'll write the file."),
            RunEvent(kind="model_call", tools=("write_file",), args=({"file_path": "/data/x"},)),
        ]
    )

    assert "I'll write the file.\n[model]" in text


def test_consecutive_tokens_are_not_broken_apart():
    text, _ = _render(
        [RunEvent(kind="token", text="7 to"), RunEvent(kind="token", text=" seven.txt")]
    )

    assert text == "7 to seven.txt\n"


def test_prose_is_closed_off_when_the_run_ends_on_it():
    text, _ = _render([RunEvent(kind="token", text="done"), RunEvent(kind="finished")])

    assert text == "done\n"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_stream.py tests/test_main.py -q`
Expected: FAIL — no `token` events are produced, and `render` writes tokens through `print`.

- [ ] **Step 4: Translate token chunks in `adapters/runtime.py`**

Extend the import to include `AIMessageChunk`:

```python
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
```

Add the mode and the constant:

```python
#: The stream modes we ask for. `updates` drives structural events, `values`
#: carries the full state whose last emission holds the final answer, and
#: `messages` carries the model's output token by token.
#:
#: `messages` is not display-only: LangGraph installs a streaming callback
#: handler for it, which flips every model call from one POST to SSE. That is
#: deliberate. A non-streaming request must finish the whole generation inside
#: `timeout_s`, while SSE resets the read clock on every chunk -- so streaming
#: is how a long turn survives, not only how it is watched.
STREAM_MODES: list[StreamMode] = ["updates", "values", "messages"]

#: A `messages` chunk is a (message, metadata) pair.
TOKEN_CHUNK_PARTS = 2
```

Add `_token_event` above `events_in`:

```python
def _token_event(chunk: Any) -> RunEvent | None:
    """One `messages` chunk into a token event, or nothing.

    Tool results arrive on this stream too, and untruncated -- they are
    already covered by `tool_result`, which bounds them to `PREVIEW`. Chunks
    carrying only usage or only tool-call arguments have no text and are
    likewise nothing to show.
    """
    if not isinstance(chunk, tuple) or len(chunk) != TOKEN_CHUNK_PARTS:
        return None
    message, _metadata = chunk
    # AIMessageChunk before AIMessage: the former is a subclass, and only it
    # appears on this stream.
    if not isinstance(message, AIMessageChunk):
        return None
    text = text_of(message)
    return RunEvent(kind="token", text=text) if text else None
```

Extend `events_in`:

```python
def events_in(mode: str, chunk: Any) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events.

    Mode dispatch lives here rather than in `app/`: `updates`, `values` and
    `messages` are LangGraph's vocabulary, and the orchestration above should
    no more compare against them than it should read `input_token_details`.
    """
    if mode == "messages":
        if (event := _token_event(chunk)) is not None:
            yield event
        return
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if (event := _event_for(message)) is not None:
                yield event
```

- [ ] **Step 5: Write fragments in `main.render`**

```python
def render(events: Iterable[RunEvent], out: TextIO) -> RunResult | None:
    """Print a run as it happens, and return its result.

    Token events are fragments, not lines: written with no newline and no tag,
    so the model's own formatting survives. Everything else is a tagged line.
    `owed` is the one bit of state this costs -- a newline is owed before the
    next tagged line, or the two jam together.

    The terminal event carries the `RunResult` and is not printed: the answer
    has already streamed, and reprinting it says the same thing twice.
    """
    result: RunResult | None = None
    owed = False
    for event in events:
        if event.kind == "token":
            out.write(event.text)
            out.flush()
            owed = True
            continue
        if owed:
            out.write("\n")
            owed = False
        if event.kind == "finished":
            result = event.result
        else:
            print(event, file=out, flush=True)
    if owed:
        out.write("\n")
        out.flush()
    return result
```

- [ ] **Step 6: Stop reprinting the answer**

Delete this line from `main()` (~line 274):

```python
    print(f"\n{result.answer}\n")
```

Update the comment above the deferred `stream` import (~line 242) to describe what now happens:

```python
    # Streaming rather than run(): with no UI, a multi-minute analysis would
    # otherwise print nothing at all until it finished. The answer is not
    # printed again below -- it arrived as it was generated.
    # Deferred: this is the first thing that needs deepagents, and paths
    # that never get here (--help, --list, a bad .env) should not pay for it.
```

- [ ] **Step 7: Run the suite**

Run: `uv run pytest tests/ -q && uv run ruff check . && uv run ty check`
Expected: PASS.

- [ ] **Step 8: Verify against the live model**

Run: `uv run main.py "Write the number 7 into seven.txt in your run directory, then tell me you did it."`

Expected, and check each:
- prose appears **during** the run, not after
- the `[model]` line shows arguments: `→ write_file(file_path=…, content=7)`
- no line is jammed onto the end of prose
- the answer appears **once**
- `usage :` reports non-zero `in=` and a `cached=` percentage — accounting survived SSE

- [ ] **Step 9: Update `main.py`'s module docstring**

The docstring at the top of `main.py` describes the driver's behaviour and does not currently mention that output is live. Add, after the paragraph about `--no-checks`:

```
Output is live: the model's prose is printed as it is generated, un-tagged,
while progress lines stay tagged and aligned. The final answer is not
repeated at the end -- you already watched it arrive.
```

- [ ] **Step 10: Commit**

```bash
git add src/kingfisher/adapters/runtime.py main.py tests/
git commit -m "$(cat <<'EOF'
Stream the model's prose instead of summarising it at node boundaries

The driver printed one line per completed graph node, so a turn that takes a
minute to generate showed nothing for that minute and then a summary of what
had already finished. Tokens now arrive as they are produced.

The mode is not display-only: LangGraph installs a streaming callback for it,
which turns every model call into SSE. That is the point. A non-streaming
request has to finish the whole generation inside timeout_s, while SSE resets
the read clock per chunk -- so the long analyses this was built for are the
ones that most needed it.

Tool results arrive on the same stream, untruncated, and are skipped: they
are already carried, bounded, by tool_result.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage.** Decisions 1-13 each map to a task: 1/2/3 → Task 5; 4/5 → Task 2 (field) and Task 5 (`channel="answer"` asserted, no splitter); 6/12 → Task 5 Step 5 and the bare-text test; 7 → no TTY branch anywhere; 8 → Task 4; 9 → Task 5 Step 4 (`STREAM_MODES`, no flag); 10 → Task 2; 11 → Task 2 (`args`, `ARG_PREVIEW`); 13 → Task 3. The `.text` bug (agreed for this change) → Task 1.

**Type consistency.** `text_of` (Task 1) is used in Tasks 2, 4, 5. `tool_calls`/`tool_names` (Task 2) keep `runlog.py:73` working unchanged. `events_in(mode, chunk)` and `answer_in(mode, chunk)` are introduced together in Task 4 and extended in Task 5. `render(events, out)` is created in Task 3 and extended in Task 5. `RunEvent.args` and `RunEvent.channel` are added in Task 2 and first exercised in Tasks 2 and 5 respectively.

**Known gaps, deliberate.**
- `channel` never takes any value but `"answer"`. Carried on purpose (decision 4), unexercised on purpose (decision 5). If a reviewer wants it removed until a producer exists, that is a defensible position and costs one field.
- The renderer treats a `reasoning` token identically to an `answer` one. Distinguishing them is styling, which is out of scope.
- `answer_in` returns `""` for a `values` chunk whose last message is not an assistant message, which can transiently clobber `answer` mid-run. Pre-existing behaviour, preserved: the final emission is authoritative. Not fixed here so the change stays reviewable.
- Every `AIMessage` now yields a `model_call`, including a hypothetical empty one that previously yielded nothing. Accepted: a completed turn is a fact worth reporting.
