# What upstream actually does

Measured facts about deepagents, langchain and the model surfaces, which the code
cannot tell you and which cost an experiment each to establish.

They were buried in implementation plans -- task lists for work finished months
ago, which nobody had reason to open. The plans were removed when this file was
written; `git log --diff-filter=D -- docs/superpowers/` finds them.

**Every entry is dated, because these are observations of someone else's
software.** An entry old enough to doubt is one to re-measure rather than trust.
Where a fact is already enforced by a test or recorded beside the code, this says
where instead of repeating it -- two copies of a measurement drift.

---

## Streaming

*Probed against a live gateway on 2026-08-16 (MiniMax-M3 via
`api.minimaxi.com/anthropic`). Re-run these if a decision resting on one looks
wrong.*

- **Token streaming works through the compiled graph.** Real deltas arrive:
  `"I'll"`, `" write the number "`, `"7 to"`. Chunks split mid-word.
- **Granularity is coarse.** Sentence-bursts, not a typewriter.
- **Token accounting survives streaming.** The same task with and without
  `messages` mode: `in=7044 out=2`, `usage_present=True` in both.
- **Subagent tokens do not leak.** deepagents does not propagate the parent's
  streaming callbacks into a subagent graph; only the final answer returns, as a
  tool result. No filtering is needed -- and no subagent visibility is available
  either, which is the same fact from the other side.
- **Tool results arrive on the `messages` stream untruncated.** They must be
  skipped explicitly, or preview protection is bypassed and a 50KB file read
  reaches the terminal.
- **Content shape differs by surface.** Anthropic → `str`; OpenAI-compatible →
  `list` of blocks, where `str(content)` yields `"[{'type': 'text', …}]"`.
  `.text` flattens both correctly.
- **`.text` is a callable `str` subclass** in this langchain-core version. Coerce
  with `str(...)` so it does not leak into the domain.
- **`AIMessageChunk` subclasses `AIMessage`**, so any `isinstance` check has to be
  ordered deliberately.
- **No `<think>` output on either surface**, even for a genuine reasoning prompt --
  82 and 57 chunks of step-by-step, zero `<think>`.

### Nothing upstream bridges a sync stream to an async one

*Read against deepagents, langchain-core and langgraph on 2026-09-18, while
`Kingfisher.astream` was being written. It ended up needing no bridge at all --
it drives `graph.astream` -- so this is a record of what is and is not there,
for the next reader who reaches for one.*

- **There is no reusable bridge.** `Runnable.astream`'s default does not iterate
  the sync `stream` at all -- it yields a single chunk from `ainvoke`, which is
  giving up on streaming rather than bridging to it. `langgraph`'s `Pregel` has a
  native `astream` instead of a fallback, and `langchain_core.utils.aiter` only
  takes async iterators as input. Nothing in deepagents does it either.
- **The nearest pattern is `BaseLoader.alazy_load`**, in
  `langchain_core/document_loaders/base.py`: `run_in_executor(None, next,
  iterator, done)` in a loop, with a sentinel. It works, and two things have to be
  added for a turn -- it **never closes the iterator**, which is fine when
  abandoning one costs nothing and not fine for something holding a session's
  claim until its `finally` runs; and the step has to be shielded from the
  caller's cancellation, because closing a generator mid-step raises `generator
  already executing`. Measured against driving `graph.astream` instead: **9.71s
  to cancel a ten-second model call, against 0.00s**, because a thread has to be
  waited out. That is why this is a record rather than what `astream` does.
- **An async generator dropped by another async generator is not finalised
  promptly.** `yield from` closes a nested *sync* generator, and there is no async
  spelling of it: the inner one waits for the event loop's `shutdown_asyncgens`.
  So an outer `aclose()` does not run an inner `finally` -- which, for a turn,
  meant a session stayed claimed until the loop ended. `astream` closes its inner
  turn by hand.
- **A cancelled task's generator is finalised one turn of the loop later, and
  that is soon enough to be unobservable.** Measured by cancelling a turn and
  looking after exactly N turns of the loop: held at 0, released at 1, with an
  explicit `aclose` in the drain or without one. So `arun` has none -- awaiting a
  cancelled task is itself a turn of the loop, which is all the finalizer needs,
  and nothing a test can see distinguishes the two. `astream`'s inner close is
  the opposite case and its guard fails when it goes: there a caller can run
  synchronous work with no loop turn in between.
- **Cancellation cannot interrupt a turn's cleanup, and the reason is
  structural.** A cancellation is delivered at a suspension point, and
  `_turn_lifecycle` is a *sync* context manager -- the claim, the checkpointer and
  the interpreter are released without ever suspending. Cancelling twice, five
  times, during the unwinding, or through `wait_for` and `asyncio.timeout` all
  leave the session free. Made async, that stops being true and every one of
  those tests would pass on timing alone.
- **Context reaches the worker through either helper, but not through the
  executor directly.** `asyncio.to_thread` copies the current context, and
  langchain's `run_in_executor` does it by hand -- `partial(copy_context().run,
  wrapper)`. A bare `loop.run_in_executor(None, f)` does not, and neither does a
  `threading.Thread`, which starts with an empty context. That decides whether a
  caller's ambient `RunnableConfig` -- and the tracing hanging off it -- survives
  into the turn. Measured both ways: the first draft of `astream` ran the turn on
  a thread of its own and silently dropped it.
- **deepagents' own pattern for an async twin is `asyncio.to_thread` per
  method** -- `als`, `aread`, `aglob`, `awrite` and the rest in
  `backends/protocol.py` are each one line of it. Precedent for the approach, not
  a helper to import.
- **`StopIteration` cannot cross a `Future`, and the failure is loud.**
  `asyncio.to_thread(next, gen)` raises `RuntimeError: StopIteration interacts
  badly with generators and cannot be raised into a Future`; langchain's
  `run_in_executor` converts it to a bare `RuntimeError` of its own. Its comment
  there says the future is "pending forever", which is why the sentinel above
  exists -- **that comment is stale on 3.12**, where CPython raises instead of
  hanging. Either way the obvious `to_thread(next, gen)` loop is wrong.
- **A thread pool does not cap how many turns overlap.** Each `astream` waits on
  its hand-off through `asyncio.to_thread`, so the concern was that the default
  executor's `min(32, cpu+4)` would bound concurrency. Measured on an 8-CPU host,
  pool of 12: 8, 12, 20 and 40 concurrent `arun` calls all reached the model call
  together. A dedicated thread runs each turn and blocks on the model; the pool is
  only borrowed to hand an event over, which is a moment rather than the turn.

## Middleware

- **`create_deep_agent` merges middleware by name, replacing in place.**
  `_apply_custom_middleware` matches on `.name`, which defaults to the class name,
  so a deployment's class named like one of deepagents' own removes it from the
  stack rather than running beside it. Kingfisher warns about this; the mechanism
  and the reasoning are in `_warn_if_it_replaces_deepagents` in
  `infrastructure/harness/agent.py`, and the names are discovered at run time
  rather than listed. *(2026-08-31.)*
- **Upstream protects `FilesystemMiddleware` and `SubAgentMiddleware` on one path
  and not the other.** `_apply_excluded_middleware` refuses to strip them;
  `_apply_custom_middleware` will replace them without a word. *(2026-08-31.)*
- **A subagent inherits none of its parent's middleware.** Each definition's is
  built separately, so a cap on an agent bounds nothing its delegate does.
  `delegation.py` says this in four places. *(2026-08-30.)*
- **A sync run does not skip an async-only hook; it raises.** Under `stream`, a
  middleware implementing only `awrap_tool_call` or `awrap_model_call` raises the
  base class's `NotImplementedError` the first time the hook is reached, and one
  implementing only `abefore_model` raises langgraph's `No synchronous function
  provided`. The quiet case is a subclass: override only `abefore_model` of a
  parent implementing both, and the parent's `before_model` runs with no error.
  `guides/middleware.md` draws the consequence. *(2026-09-15.)*

## Skills and subagents

- **deepagents reads skills off the filesystem, one level down.** That is the only
  shape it offers, and it is why skills do not nest when tools and subagents do.
  *(2026-08-16.)*
- **The skills lister is a private function**, called deliberately with a test
  pinning it, because kingfisher's own listing and deepagents' disagreed and a
  caller could activate a skill the agent was never told about. *(2026-08-17.)*
  It has an async twin, `_alist_skills_with_errors`, with the same return shape --
  the skills and a source error it has already logged. *(2026-09-16, deepagents
  0.7.6.)*
- **deepagents accepts two kinds of subagent** -- a spec it builds, and a compiled
  graph it runs as given. A compiled one is never given middleware and never gets
  a skills middleware added to it. *(2026-08-18.)*
- **A skill's `allowed-tools` is prompt text, not enforcement.** *(2026-08-16.)*

## Tool shapes

What a workspace tool may be written as is pinned by `tests/unit/test_tool_shapes.py`
-- a `BaseTool` from `@tool`, a `BaseTool` subclass, or a plain function -- so it is
not repeated here. What that file does not say is what the decorator is *for*, which
cost an experiment to establish.

- **`@tool` buys control, not capability.** A plain function reaches the graph, is
  offered to the model, dispatches, and is covered by `WorkspaceToolErrors` exactly
  as a decorated one is -- deepagents wraps whatever it is handed, so by the time a
  tool is in the graph it has `.name` and `.invoke` either way. The decorator is
  worth reaching for when the name or description must *differ* from the function as
  written, and for nothing else. *(2026-09-04.)*
- **A docstring is required in both forms**, which is the part that surprises: it is
  langchain that insists, not the decorator, and leaving it off raises
  `ValueError: Function must have a docstring if description not provided.` either
  way. *(2026-09-04.)*
- **The two forms differ in where that failure lands.** `@tool` runs at import, so
  the catalogue's loader catches it and names the file --
  `ToolError: shout.py: ValueError: ...`. A plain function is not wrapped until the
  agent is built, so the same `ValueError` arrives from inside langchain with no
  filename on it. That is the decorator's one real advantage, and it is about
  diagnosis rather than behaviour. *(2026-09-04.)*
- **Annotations are optional and worth writing anyway.** `def shout(text):` loads
  and runs; the schema comes back as `{'text': {'title': 'Text'}}` -- an argument
  the model is told the name of and not the type. A silent degradation rather than a
  refusal. *(2026-09-04.)*
- **The return annotation is read by nobody**, and what langchain does with the
  value is `json.dumps` first and `repr` when that fails, so `None` reaches the
  model as `null` and an ordinary object as its repr. Pinned by
  `tests/unit/test_tool_returns.py`, so the cases are not listed twice. What the
  test cannot say is which versions answered: langchain-core 1.5.5, langgraph
  1.2.11, deepagents 0.7.6. *(2026-09-04.)*
- **A `Command` returned by a workspace tool is applied, not wrapped.**
  `_format_output` hands back any `ToolOutputMixin` untouched, and both
  `ToolMessage` and langgraph's `Command` are one -- so a tool can replace its own
  result or write graph state, and `WorkspaceToolErrors` never sees it because it
  catches exceptions and nothing else. The cost is in the transcript: the message
  carries no `name`, so `_event_for` records a `tool_result` naming no tool.
  Documented rather than refused, and `decisions.md` says why. *(2026-09-04.)*

Four more, measured while wrapping the tools a compiled delegate is handed. They are
about what survives *re-wrapping* a tool, which nothing above needed to ask.

- **Inside a graph kingfisher did not build, every exception ends the run.** A tool
  node raises `FileNotFoundError`, `ValueError` and langchain's own `ToolException`
  straight through to the caller -- all three measured against a success control that
  passed. There is no upstream conversion to lean on, so anything wrapping a tool for
  such a graph must *return* its refusals. `handle_tool_error=True` on the wrapper is
  what converts one: a `ToolException` raised in `_run` comes back as a `ToolMessage`
  with `status="error"` carrying the exception's own message, and the artifact pair
  still survives beside it. *(2026-09-18.)*
- **Rebuilding a tool as a `StructuredTool` loses a subclass's arguments, silently.**
  `StructuredTool.from_function` over a delegating callable advertises
  `{'kwargs': ...}` for a `BaseTool` subclass that declares no `args_schema` -- the
  model is told the wrong arguments, nothing raises, and the inner `_run` then fails
  for missing them. A wrapper that subclasses `BaseTool` and carries the inner tool's
  `args_schema`, falling back to `get_input_schema()`, keeps `.args` and the OpenAI
  schema identical across every shape. *(2026-09-18.)*
- **The plain invoke form drops an artifact.** A tool declaring
  `content_and_artifact` returns the pair only through the tool-call form
  (`{"type": "tool_call", ...}`), which comes back as a `ToolMessage` carrying
  `.artifact`; `invoke(dict)` yields the content alone. A wrapper that re-dispatches
  the plain way loses every artifact it passes on, and the content still arrives, so
  nothing looks wrong. *(2026-09-18.)*
- **`ToolNode.invoke` needs a runtime config**, and fails
  `Missing required config key 'N/A' for 'tools'` without one -- identically for a
  succeeding tool and a raising one. Drive it inside a compiled graph instead, or the
  control passes the same way the case does and the measurement says nothing.
  *(2026-09-18.)*

## The Linux fence

- **GitHub's `ubuntu-latest` runs Landlock ABI 7** against the 6 a full ruleset
  needs, so the Landlock escape tests run there. **bubblewrap installs and is
  still unavailable**, because the image refuses an unprivileged user namespace.
  This was predicted the other way round and the first green run said otherwise.
  Kept current in `.github/workflows/checks.yml` beside the job it governs, and
  reported on every run by `tests/linux/test_a_fence_was_exercised.py`.
  *(2026-08-27.)*

## Memory-backed workspaces

*Measured for `nothing-at-rest-on-this-machine.md`, removed 2026-09-04 once what
it proposed had shipped. The rule is enforced in `presentation/cli/health.py` and
stated in `decisions.md`; what is kept here is the evidence, which neither of
those carries.*

Against Docker 29.5.3, alpine, cgroup v2:

| tmpfs vs the container's memory limit | swap | result |
| --- | --- | --- |
| larger | on | **203 MB silently swapped to disk.** No error, and the write succeeded |
| larger | off | **`Killed`** -- the container dies, taking every session in it |
| smaller | off | a clean `No space left on device`, nothing swapped, container survives |

- **Deleting frees the memory.** 200 MB written moved `memory.current` from 1 MB
  to 202 MB; deleting the file returned it to 3 MB, so reaping genuinely
  reclaims rather than merely unlinking. *(2026-08-21.)*
- **A tmpfs counts 1:1 against the limit**, so "smaller" is not sufficient on its
  own -- the mount plus the process's own working set must fit, and the process
  was using memory before any file existed. *(2026-08-21.)*

## mirage, and why it was not adopted

*From the same document. `mirage-ai==0.0.5` installed into a throwaway venv,
scripts written into a RAM mount and executed -- not read from the documentation,
which was wrong or silent on three of these. Kept because the question returns
whenever somebody wants S3 or Postgres mounted as a path, and old enough that it
deserves re-measuring before it decides anything.*

- **A script in memory runs, but cannot both import a package and read the
  mount.** Under the default `monty` runtime it reads the mount and cannot import
  a third-party package; under `local` it imports and cannot see the mount
  (`FileNotFoundError`). Only a real sandbox reconciles the two, so a skill
  shipping code that needs real packages cannot run. *(2026-08-21.)*
- **`MountMode.EXEC` is undocumented and is not a capability boundary.** `EXEC`
  implies write -- a directory mounted `EXEC` accepted a shell redirect and the
  file landed on host disk -- and it is not scoped to its own mount: a script on
  a `WRITE` mount is refused while nothing is `EXEC`, and runs as soon as any
  *other* mount is. *(2026-08-21.)*
- **The driverless macOS backend corrupts writes.** FSKIT flushes pages a file
  did not already have -- a new file, or truncate-then-write -- as NUL bytes, a
  limit that lives in a docstring rather than in the documentation. FUSE avoids
  it and wants a kernel driver on every machine that runs the code, developers'
  included. *(2026-08-21.)*
- **"No subshell" does not mean no processes.** `monty` is a spawned worker
  binary, so arguments resting on there being nothing left to confine were weaker
  than they looked. *(2026-08-21.)*

A sized tmpfs supplies what mirage was wanted for -- memory-backed files at real
paths, with a kernel-enforced limit -- and puts no library under every turn.

## Costs worth knowing

*Re-measured 2026-09-03. All three had drifted, and one of them was never the
number it looked like -- an agent rebuild costs what the workspace holds, and the
figure recorded had been taken against an empty one.*

- **A whole agent rebuild costs what the catalogue holds.** 8ms in an empty
  workspace; **54ms** in one seeded with the shipped `assets_examples/`, where
  the default agent compiles every delegate it is offered. Between them, 25ms
  for a seeded workspace whose agent names no delegates.

  The conclusion it was recorded for still stands -- against a turn of 1.5-1.9s
  even the worst of those is under 4%, so "loaded once" was never the argument
  for the catalogue. What does not stand is quoting 8ms as *the* number: it is
  the emptiest case there is. *(8ms 2026-08-16; the rest 2026-09-03.)*
- **Each named delegate compiles its own graph, about 6ms, every turn**, whether
  or not the task uses it. Drop a name you never use rather than keeping the list
  tidy. *(4.3ms 2026-08-18, 6.2ms 2026-09-03 -- measured as the difference between
  an agent naming three delegates and the same agent naming none.)*
- **A skills index costs about 600 tokens for three skills, and most of it is
  fixed.** deepagents' own scaffolding -- the "Skills System" preamble it wraps
  the listing in -- is ~450 tokens before a single skill is named; the three
  shipped ones add ~150 between them.

  Which sharpens why a narrow agent with a procedure in its prompt does not take
  one: the cost is almost all entry fee, so a workspace with one skill pays
  nearly what a workspace with three does. *(464 tokens 2026-08-18, before the
  split was measured; ~450 + ~50/skill 2026-09-03.)*

## Historical, and not re-measurable

Numbers that described an arrangement this code no longer has. They are kept
because they are the evidence for a decision, not because they can be checked
again -- the thing they measured is gone.

- **6,872 compilations and seven seconds** for 15 definitions each naming three,
  when delegates were compiled per *path* rather than per definition.
  *(2026-08-18.)*
- **132 orphaned threads** in one real workspace, when a conversation lived in a
  database keyed beside the session directory rather than inside it.
  *(2026-08-31.)*
- **363ms to 80ms** for the slowest of 32 concurrent writers, moving from a
  shared checkpoint file to one per session -- and **~20KB of empty database per
  session**, which was that arrangement's cost rather than its benefit.
  *(2026-08-31.)*
- **The streaming observations above** were probed against a live gateway. They
  need one to re-check, so they carry their date and are trusted no further than
  it.
