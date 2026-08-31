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

## The Linux fence

- **GitHub's `ubuntu-latest` runs Landlock ABI 7** against the 6 a full ruleset
  needs, so the Landlock escape tests run there. **bubblewrap installs and is
  still unavailable**, because the image refuses an unprivileged user namespace.
  This was predicted the other way round and the first green run said otherwise.
  Kept current in `.github/workflows/checks.yml` beside the job it governs, and
  reported on every run by `tests/linux/test_a_fence_was_exercised.py`.
  *(2026-08-27.)*

## Costs worth knowing

- **A whole agent rebuild is about 8ms**, so "loaded once" was never the argument
  for the catalogue. *(2026-08-16.)*
- **Each named delegate compiles its own graph, about 4.3ms, every turn**, whether
  or not the task uses it. Drop a name you never use rather than keeping the list
  tidy. *(2026-08-18.)*
- **A skills index costs about 464 tokens for three skills**, injected into the
  prompt -- which is why a narrow agent with a procedure in its prompt does not
  take one. *(2026-08-18.)*
