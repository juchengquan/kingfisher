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

## Skills and subagents

- **deepagents reads skills off the filesystem, one level down.** That is the only
  shape it offers, and it is why skills do not nest when tools and subagents do.
  *(2026-08-16.)*
- **The skills lister is a private function**, called deliberately with a test
  pinning it, because kingfisher's own listing and deepagents' disagreed and a
  caller could activate a skill the agent was never told about. *(2026-08-17.)*
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

## The Linux fence

- **GitHub's `ubuntu-latest` runs Landlock ABI 7** against the 6 a full ruleset
  needs, so the Landlock escape tests run there. **bubblewrap installs and is
  still unavailable**, because the image refuses an unprivileged user namespace.
  This was predicted the other way round and the first green run said otherwise.
  Kept current in `.github/workflows/checks.yml` beside the job it governs, and
  reported on every run by `tests/linux/test_a_fence_was_exercised.py`.
  *(2026-08-27.)*

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
