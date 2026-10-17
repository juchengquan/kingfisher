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

  *Corrected 2026-10-12, against scripted models:* only the first sentence's
  outcome held, and only for a graph streamed without `subgraphs=True`. The
  callbacks do reach the subagent's model; langgraph's `StreamMessagesHandler`
  drops a call from a non-empty namespace unless `subgraphs` is set. Set, the
  subagent's chunks arrive under a `tools:<id>` namespace, each carrying
  `lc_agent_name`. Kingfisher has streamed that way since the day of the probe
  and tags a subagent's `model_call`, `tool_result` and `token` events with
  `agent=<name>` -- `SubagentNames` in `core/session/runtime.py`, held
  by `tests/unit/test_stream_subagents.py`.
- **An `updates` chunk carries a message written back under its own id as if it were
  new.** Clearing swaps an older result for its handle that way, and deepagents'
  summariser clips an overflowing result the same way; in the node's update either is
  indistinguishable from a fresh result or reply. Measured on a real writing session:
  every clearing pass re-announced each message it cleared, the replies with their old
  usage. `values` arrives after a step's writes and `updates` before them, so the ids
  the conversation held at the last `values` are what tell a rewrite apart --
  `SeenMessages` in `core/session/runtime.py`, held by
  `tests/unit/test_rewritten_events.py`. *(2026-10-16, langgraph 1.2.15.)*
- **A middleware reaches the `custom` stream from inside the model call.**
  `request.runtime.stream_writer` on a `ModelRequest` is langgraph's writer for the
  node, a no-op where nothing streams, and what it is handed arrives as a `custom`
  chunk when it is written, ahead of the node's update. A subagent's arrives under its
  namespace, as its tokens do, so `SubagentNames` names it. It is the one way to report
  a model call the state never holds. *(2026-10-17, langgraph 1.2.15, langchain 1.4.5.)*
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
  structural.** A cancellation is delivered at an `await` of the task it is aimed
  at. `stream` drives the cleanup with no await in it at all; `astream` runs the same
  sequence as a task of its own, which no cancel is aimed at, and waits for it
  through every cancel -- so the claim, the checkpointer and the interpreter are
  released however the waiting is interrupted. Cancelling twice, five times, during
  the unwinding, or through `wait_for` and `asyncio.timeout` all leave the session
  free. Awaited in the caller's own task instead, every one of those tests would
  pass on timing alone. A task does not outlive its event loop, where a thread
  did; `astream` is waiting on it whenever it runs, so the loop does not end first.
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
- **It does cap calls that hold a worker for their whole length.** The bullet
  above borrows a worker for a moment; a call that blocks for a round trip keeps
  it until it returns, and then `min(32, cpu+4)` is the ceiling. Measured at a
  simulated 50ms round trip on the same host: 200 concurrent calls took 917ms on
  the default executor, 73ms on a 256-thread pool and 56ms awaited natively
  (2026-10-02). Why kingfisher's setup runs on a pool of its own.
- **End to end, kingfisher's awaited path holds; what still queues is the turn's
  ending.** `spikes/async_scaling.py` runs kingfisher against session backends
  whose every call takes 50ms, sync and async alike (2026-10-05). A read awaits all
  three of its round trips: 200 concurrent `apending` calls took 192ms, against
  2754ms handing `pending` to a thread each. A turn on a pre-built graph awaits the
  six in setup and hands the four in its ending to kingfisher's pool, so the pool's
  size is what decides it:

  | Concurrent turns | Thread per call | Pool 4 | Pool 16 | Pool 64 |
  |---|---|---|---|---|
  | 1 | 547ms | 523ms | 522ms | 526ms |
  | 10 | 534ms | 953ms | 532ms | 535ms |
  | 50 | 2697ms | 3126ms | 1178ms | 556ms |
  | 200 | 9153ms | 11178ms | 3173ms | 1274ms |

  Since the ending is awaited as well, a turn hands the pool no round trip at all,
  and the pool's size stops mattering: two hundred concurrent turns took 638ms at a
  pool of 4, 646ms at 16 and 630ms at 64, against a single turn's 518ms.

  The same turns on kingfisher's own filesystem backend take 4ms each and are
  CPU-bound: at 200 at once, 1103ms handed to threads and 1488ms awaited, the
  difference being the async path's many short hops. Awaiting pays where a round
  trip is long, and costs a little where it is not.

## Middleware

- **`FilesystemMiddleware` tells whoever holds `execute` to use absolute paths, and
  maps some mounts to host paths.** The tool's description says "Use absolute paths
  and avoid `cd`". Beside it, on a `CompositeBackend` over a `LocalShellBackend`, the
  system prompt gains "Shell paths vs. virtual paths": each routed mount with its host
  path (in kingfisher `/inputs/`, `/memory/`, `/skills/` and `/.harness/`, which a
  fence still denies where there is one), and the line that a mount without a mapping
  is out of the shell's reach. Unrouted folders get no line at all, so `/outputs` and
  `/scratchpad` read as unreachable although the shell starts in the session root and
  reaches both. Every subagent holding `execute` gets both, and none of `system.md`.
  *(2026-10-13, deepagents 0.7.6.)* The section is appended on every model call where
  the backend executes, as a system-message block of its own, and nothing turns it off:
  `system_prompt=` is prose added before it, not a replacement. Whether a mount gets a
  host path is decided by `isinstance(backend.default, LocalShellBackend)` alone, so a
  `LocalShellBackend` subclass whose commands run on another machine is still given
  this host's paths. *(2026-10-15, deepagents 0.7.22.)*
- **`create_deep_agent` merges middleware by name, replacing in place.**
  `_apply_custom_middleware` matches on `.name`, which defaults to the class name,
  so a deployment's class named like one of deepagents' own removes it from the
  stack rather than running beside it. Kingfisher warns about this; the mechanism
  and the reasoning are in `_warn_if_it_replaces_deepagents` in
  `core/agent/kinds/declared_middleware.py`, and the names are
  discovered at run time rather than listed. *(2026-08-31.)*
- **The memory block gets a cache breakpoint of its own only in deepagents' slot
  for it.** With `memory=`, `MemoryMiddleware` runs after
  `AnthropicPromptCachingMiddleware`, so a `ChatAnthropic` is sent two
  breakpoints: one on the static prompt, one on the memory block after it. One
  passed through `middleware=` without `memory=` is spliced in ahead of the
  caching middleware, both breakpoints land on the memory block, and the static
  prompt has none of its own; one passed beside `memory=` replaces deepagents'
  own in that slot. `test_the_memory_block_keeps_a_cache_breakpoint_of_its_own`
  holds it. And `system_prompt=None` drops the memory file's contents with the
  guidelines: they are loaded into state and never appended. *(2026-10-07,
  deepagents 0.7.6.)*
- **Upstream protects `FilesystemMiddleware` and `SubAgentMiddleware` on one path
  and not the other.** `_apply_excluded_middleware` refuses to strip them;
  `_apply_custom_middleware` will replace them without a word. *(2026-08-31.)*
- **A subagent inherits none of its parent's middleware.** Each definition's is
  built separately, so a cap on an agent bounds nothing its subagent does.
  `core/agent/kinds/subagents.py` says so where a subagent is built. *(2026-08-30.)*
- **A sync run does not skip an async-only hook; it raises.** Under `stream`, a
  middleware implementing only `awrap_tool_call` or `awrap_model_call` raises the
  base class's `NotImplementedError` the first time the hook is reached, and one
  implementing only `abefore_model` raises langgraph's `No synchronous function
  provided`. The quiet case is a subclass: override only `abefore_model` of a
  parent implementing both, and the parent's `before_model` runs with no error.
  `guides/middleware.md` draws the consequence. *(2026-09-15.)*
- **A `before_model` or `after_model` hook is a graph step, and the recursion limit
  counts it.** `create_agent` makes each a node of its own, run on every trip round
  the loop. Kingfisher's loop is `model`, `TodoListMiddleware.after_model`, `tools`:
  every turn of the four tm-* trials that ran out stopped at 50 model calls, 150
  steps. One `before_model` hook more made it 37, measured on a writing turn, and the
  graph's node list showed the fourth node. A `wrap_model_call` adds no node, and the
  state update it returns in an `ExtendedModelResponse` reaches the state from inside
  other layers too: langchain collects every layer's commands and applies them with
  the model's reply. *(2026-10-16, langchain 1.4.5, langgraph 1.2.15.)*
- **deepagents' summarizer keeps an index, not a rewrite, and nothing re-checks
  it.** It stores `_summarization_event.cutoff_index` into `state["messages"]` and
  rebuilds each call's view as the summary plus everything after that index;
  `_apply_event_to_messages` slices by it as stored. A second summarizer that
  rewrites the messages leaves the index stale -- past the end, the model sees only
  the old summary -- and two deepagents summarizers side by side break the same
  way. *(2026-10-01; re-read at deepagents 0.7.22, 2026-10-16.)*
- **A subclass of deepagents' summarizer runs beside the original unless it says
  otherwise.** Its `name` answers `SummarizationMiddleware` only for the exact
  class and the subclass's own class name otherwise, so replacing it is something
  a subclass opts into. `compaction.py` does. *(2026-10-01.)*
- **What it summarizes away is kept, and the summary says where.** One file per
  session, `conversation_history/<session_id>.md` under the root the next entry
  describes, a timestamped section appended each time; through kingfisher's backend
  that is `/scratchpad/conversation_history/`, and the agent reads it back with
  `read_file`. `test_compaction_fires_at_sixty_messages_however_small` reads it back
  that way. *(2026-10-01; the root 2026-10-11.)*
- **It writes two kinds of file on its own, under one root it reads with
  `isinstance`.** A tool result over 20,000 tokens, counted at four characters each,
  is saved to `large_tool_results/<call id>` and the model is handed a head-and-tail
  stub naming the path. Every tool but the file tools is evicted, so `execute` and
  each workspace tool are; when a call overflows the context, the summarizer
  offloads the large tool results it keeps there too. A user message over 50,000 tokens goes to
  `conversation_history/<uuid>.md`, beside the summarizer's history and its
  `media/`. Both folders hang off `CompositeBackend.artifacts_root`, `/` by default,
  which `FilesystemMiddleware` and `SummarizationMiddleware` read only when the
  backend is a `CompositeBackend`: anything else is `/` whatever it carries. The
  `grep` tool's description says offloaded results are under `/large_tool_results/`
  "by default", and does not change with the root. `tests/unit/test_offloads.py`
  drives both evictions. *(2026-10-11, deepagents 0.7.6.)* The limits are the same at
  0.7.22, which adds a third way in: `execute` writes its output to
  `large_tool_results/<call id>` as it runs, and hands back the stub itself, when the
  backend's default is a `BaseSandbox` and that path is not routed. The stub is then what
  a `wrap_tool_call` inside eviction sees, so the file holds no id. Kingfisher routes the
  folder, so its commands take the ordinary eviction. *(2026-10-16, deepagents 0.7.22.)*
- **Eviction joins a block list's text with newlines, and keeps what it was handed.**
  `_extract_text_from_message` joins every text block with `\n`, for the size check
  and for the file, where langchain's `.text` and kingfisher's transcript join them
  with nothing. The stub then stands in for all the text blocks: a plain string where
  every block was text, and one text block ahead of the media blocks otherwise. A
  line appended by a `wrap_tool_call` inside eviction, as the ledger's id is, is the
  stub's last line in both shapes and the offloaded file's too.
  `test_the_id_outlasts_eviction_of_a_result_made_of_blocks` and
  `test_an_offloaded_result_is_pointed_at_deepagents_copy` drive it. *(2026-10-12,
  deepagents 0.7.6, langchain-core 1.5.5.)*
- **Its trigger counts the system prompt and tools; its `keep` does not.** With a
  window small enough that those approach three quarters of it, a profile-based
  trigger fires before every call: the stock prompt (about 2.4k tokens) at a 3.2k
  window summarized before seven of nine calls. At 8k it fired once in sixteen.
  *(2026-09-30; the count re-read at deepagents 0.7.22, 2026-10-16.)*
- **A message written back under its id replaces that message where it stands.**
  deepagents' reducer for `messages` keeps the position and swaps the message, and
  appends one with no id as a new message; langgraph stamps an id on every message
  before it reaches the reducer. That is how the summariser's overflow clip writes a
  shortened result back, and how clearing writes a handle. *(2026-10-16, deepagents
  0.7.22.)*
- **A reply's tool call is sent from one copy and counted from another.** A reply from
  `ChatAnthropic` carries each call in `tool_calls` and again as a `tool_use` block in
  its content, streamed with `partial_json`. langchain-anthropic sends the arguments in
  `tool_calls` where both exist; `count_tokens_approximately` counts the block and skips
  `tool_calls` for a reply whose content is a list. A middleware changing one copy
  changes what is sent or what is counted, not both. langchain-openai's Responses path
  sends a `function_call` block over `tool_calls` the same way. *(2026-10-16,
  langchain-anthropic 1.7.6, langchain-core 1.6.10.)*
- **A call cut at the output cap reaches its tool looking whole.** Streamed,
  langchain-anthropic joins a call's argument chunks and parses them with
  `parse_partial_json`, which closes an open string and brackets and drops characters
  until the text parses. A call cut inside a value arrives in `tool_calls` with the value
  shortened, one cut inside a key arrives without that key, and `invalid_tool_calls` gets
  only what the repair cannot parse at all. MiniMax-M3 needs no repair: it sends a cut
  call's arguments as one delta, the JSON already closed. `create_agent` routes on
  `tool_calls` alone, so every call in a cut reply runs, and a reply left with only an
  invalid call ends the turn as if it had none. langchain-openai's Responses path
  repairs streamed arguments the same way, and says a reply was cut with `status:
  incomplete` and `incomplete_details.reason: max_output_tokens`, having no stop reason.
  Kingfisher's turns always stream: `messages` among the stream modes makes the model
  node's `invoke` take the streaming path. *(2026-10-17, langchain 1.4.5,
  langchain-anthropic 1.7.6, langchain-core 1.6.10, langchain-openai 1.5.1.)*
- **Its event is in the final state of a turn that summarised, and of no other.**
  Driven through `build_agent`'s stack by the service's own turn loop, a fresh graph
  each turn: at a 6,000-token window the first three short turns did not summarise and
  the final `get_state().values` of each held no `_summarization_event`; every turn from
  the fourth did, and held one. Nothing carries it into the next turn's graph, but once
  the replayed transcript is past the trigger every turn summarises again, because the
  messages kingfisher writes back are the whole conversation and never the summarised
  view. It needs something before the last message to replace, and a last message
  longer than `keep` is kept whole rather than summarised away. From 0.7.22 what is
  left has to fit, too: a request still over 95% of the window less the output tokens
  once summarised is refused with `ContextOverflowError` before it is sent. One
  70,000-character question in a 20,000-token window made no event at 0.7.6 and is
  refused at 0.7.22, so the ledger's tests trip the summariser with two half-window
  questions instead, the second fitting once the first is summarised. The budget is
  `max(0, int(max_input_tokens * 0.95) - max_tokens)`, so the configured *output*
  reservation comes off it: on a 200,000-token window, `max_tokens: 190001` leaves
  zero and every turn is refused before it is sent. `build_model` refuses that pair
  now, and `test_the_share_a_request_may_fill_is_the_one_deepagents_applies` drives
  their own `_input_budget` so the mirrored 0.95 cannot go stale quietly. The ledger's
  `summarised` mark rests on this, and
  `test_a_turn_says_on_its_closing_line_whether_it_ended_summarised` holds it.
  *(2026-10-12, deepagents 0.7.6; the budget 2026-10-13, deepagents 0.7.22.)*
- **A PDF reaches the model whole, and is counted at a quarter of its base64.**
  `read_file` returns a PDF as one `file` block whatever `offset` and `limit` say:
  `FilesystemBackend.read` base64-encodes the whole file for any extension in its
  media list, and the tool's description tells the model paging is text-only.
  `count_tokens_approximately` scores an image block at a flat 85 tokens and any
  other block at its `repr` over four, so a 0.5 MB PDF counts as 174,790 tokens and
  a 2 MB one as 699,078. A model with no profile -- MiniMax-M3 through
  `ChatAnthropic` has none -- gets a flat 170,000-token trigger and a `keep` of six
  messages, so one such read trips the summarizer with the PDF as the newest
  message, which it keeps. At 0.7.6 `ContextOverflowError` was caught only around the
  call made when the summarizer did *not* fire, so the call carrying the summary and
  the PDF had no retry. From 0.7.22 every call goes through `_call_with_budget`: an
  overflow, or a request over 95% of the window less the output tokens, clips the
  trailing tool results and retries once. The clip cuts a `read_file` result to its
  text blocks -- none, for a PDF -- and tells the model to page with `offset` and
  `limit`, which `read_file` ignores for a PDF. *(2026-10-13, deepagents 0.7.6 and
  0.7.22, langchain-core 1.5.5 and 1.6.10.)*
- **A model's profile sizes the summarizer, and one passed in replaces langchain's.**
  `ChatAnthropic` built without `profile=` fills it from langchain's own table:
  `claude-sonnet-4-5` arrives with a full profile of 24 keys, `max_input_tokens`
  200,000 among them, and `MiniMax-M3`, which the table lacks, with `None` -- so the
  summarizer takes its fixed 170,000 tokens and six messages. Built with `profile=`,
  the class keeps that dict as given: `{"max_input_tokens": 120000}` on
  `claude-sonnet-4-5` is the whole profile, `pdf_inputs` and `tool_calling` gone with
  the rest. A row's `extra:` reaches the constructor, so `extra: {profile: ...}` in
  `models.yaml` is this.

  **That number is the table's, not this model's, and the table moved.** This read
  1,000,000 until 2026-10-14, measured against langchain-anthropic 1.5.6 and true
  then; 1.7.6 says 200,000, and the upgrade that installed it is the same commit that
  re-dated two entries above without re-measuring this one. It is the figure a
  deployment sizes against -- the trigger for this model is 0.85 of it -- so a stale
  copy here is a five-fold error in the one number anybody reads off the page. Nothing
  kingfisher owns decides it, which is the reason to re-measure rather than record.
  *(2026-10-14, langchain-anthropic 1.7.6; 1,000,000 at 1.5.6.)*

- **deepagents appends its host-paths section with `append_to_system_message`**, and builds
  `FilesystemMiddleware` with no system prompt of its own, so that section cannot merge into
  other prose today. `test_a_mappings_section_merged_into_other_prose_is_left_whole` holds the
  difference, not `test_deepagents_host_paths_reach_only_a_shell_on_this_host`, which passes
  whichever way the section is removed. *(Recorded in the code until 2026-10-17.)*

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

- **deepagents reads a skills source one level deep.** One root source finds nothing in a
  nested layout; three folder sources find all nine. Four directories each holding a skill of
  one name: kingfisher advertised four names and deepagents loaded three, two of them
  different. *(Recorded in the code until 2026-10-17.)*
- **Only `Runnable` tells a compiled graph apart** from an `invoke`-only stub, whatever a class
  constructs to, and `None`; a duck-type on `invoke` is too loose, since deepagents also calls
  `with_config`. *(Recorded in the code until 2026-10-17.)*

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
- **A list or a tuple is read twice, and the second reading is the stricter.**
  `_format_output` in langchain-core makes any list or tuple of strings and content
  blocks into list content. langgraph's tool node then passes on only a string or a
  list of typed block dicts and `json.dumps` everything else, saying in a comment that
  a list of strings is valid content not every chat model is tested with. So
  `("three", "columns")` reaches the model as JSON text, a list or tuple of blocks as
  blocks, a string beside an image block as JSON with the image's base64 in it, and
  `[]` as empty content. Reading `_format_output` alone predicts the first of those
  wrong. Pinned by `tests/unit/test_tool_returns.py`. *(2026-10-12,
  langgraph-prebuilt 1.1.0.)*
- **A `Command` returned by a workspace tool is applied, not wrapped.**
  `_format_output` hands back any `ToolOutputMixin` untouched, and both
  `ToolMessage` and langgraph's `Command` are one -- so a tool can replace its own
  result or write graph state, and `WorkspaceToolErrors` never sees it because it
  catches exceptions and nothing else. The cost is in the transcript: the message
  carries no `name`, so `_event_for` records a `tool_result` naming no tool.
  Documented rather than refused, and `decisions.md` says why. *(2026-09-04.)*

Four more, measured while wrapping the tools a compiled subagent is handed, on
langchain-core 1.5.5, langgraph 1.2.11 and deepagents 0.7.6. They are about what
survives *re-wrapping* a tool, which nothing above needed to ask.

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

- **LangChain names a plain function by `__name__`**: `convert_to_openai_tool` calls one
  `shout`, and a lambda `<lambda>`. A `TOOLS = ["line_count"]` entry was advertised as
  `'line_count'`, quotes and all, and the build died with `AttributeError: 'function' object
  has no attribute 'name'`, naming neither the file nor the entry. A pydantic model class in
  `TOOLS` has no `name` class attribute, so naming falls through to `__name__` and calling it
  instantiates it: the model gets `status="success"` and the repr of a `CallbackManager`, and
  the run carries on. *(Recorded in the code until 2026-10-17.)*

## The run context, and the permissions around a backend

What `ToolContext` and `PermittedBackend` were built on.
`tests/unit/test_tool_context.py` pins the ones a turn depends on, so what is here
is the part a test cannot say: which versions answered, and what was tried that
did not work. All of it langchain-core 1.5.5, langgraph 1.2.11, deepagents 0.7.6.
*(2026-09-30.)*

- **langgraph does not checkpoint the context.** A graph paused at a gate and
  resumed with `Command(resume=...)` and no `context=` ran the approved tool with
  `runtime.context` as `None`; resumed with one, the tool saw the new one. Nothing
  is restored and nothing is refused, so a resume has to be handed it again.
- **A subagent inherits its parent's context, and deepagents is not what passes
  it.** The `task` tool invokes a subagent with a config and nothing else, and the
  subagent's tools saw the parent's context anyway -- for a declared subagent, for
  `general-purpose`, and for a compiled graph handed in as `runnable`.
- **`context_schema` is not what delivers a context.** A graph compiled with no
  schema handed its tools the context it was driven with all the same. What is
  sensitive to the type is the *tool's* annotation: a bare `ToolRuntime` declares a
  context of `None`, and pydantic warns `PydanticSerializationUnexpectedValue` on
  every call that hands it something else.
- **`runtime` is filled in from the tool's schema, so a class that declares its
  schema does not get one.** A plain function and a `@tool` function both do, and
  so does a `BaseTool` subclass that leaves `args_schema` to be inferred from
  `_run`. One that declares `args_schema` is called without `runtime` and raises
  `TypeError`. Adding `runtime: ToolRuntime[...]` to that schema fails earlier, at
  import, with `PydanticSchemaGenerationError` over langgraph's `BaseStore`.
- **deepagents enforces `permissions=` in its file tools' bodies and nowhere
  else**, so a backend called directly keeps none of them. The functions it uses
  are private -- `_check_fs_permission`, `_find_delete_deny_patterns`, the two
  result filters -- and `PermittedBackend` calls the same ones.
- **Its result filters pass `interrupt` entries through**, and so does its delete
  check for a confirmed leaf. Correct for a file tool, where the person has
  answered before the tool body runs, and a hole for any other caller.
- **A rule on `/x/**` does not match `/x`.** It matches `/x/` and everything
  under it, so `ls('/x')` is allowed and comes back empty where `ls('/x/y')` is
  refused. Repeated slashes do not get past a rule: `//inputs/x.txt` matches
  `/inputs/**`.
- **The default backend routes on the leading slash.** `skills/x/SKILL.md` and
  `/skills/x/SKILL.md` validate to one path and are two files to the backend: the
  first misses the `/skills/` route and is looked for under the session.
- **A context variable set around the parent's `task` call reaches every call the
  subagent makes, calls made together included.** A `wrap_tool_call` first in
  `tool_guards` set one around `task`, and the subagent's own read it for two calls
  made in one message and for a nested helper's call. Under `stream` the two ran on two
  pool threads that started in the same instant; under `astream`, as two tasks on the
  loop. langgraph's `BackgroundExecutor.submit` runs each call in a `copy_context()`
  taken as it is submitted, and asyncio copies one into each task, so a value set
  before the subagent starts is in all of them. The ledger's `RUN` rests on this, and
  `test_a_subagents_results_are_working_entries_of_the_run_that_started_it` holds
  both paths. *(2026-10-12, langgraph 1.2.11, langchain 1.3.15, deepagents 0.7.6.)*

## The Linux fence

- **GitHub's `ubuntu-latest` runs Landlock ABI 7** against the 6 a full ruleset
  needs, so the Landlock escape tests run there. **bubblewrap installs and is
  still unavailable**, because the image refuses an unprivileged user namespace.
  This was predicted the other way round and the first green run said otherwise.
  Kept current in `.github/workflows/checks.yml` beside the job it governs, and
  reported on every run by `tests/linux/test_a_fence_was_exercised.py`.
  *(2026-08-27.)*

- **`sandlock`'s own quick-start fails in a container where `confine` works.** Linux 6.12, ABI 6,
  `SYS_ADMIN` available: `sandlock_create failed`, while `confine` in the same container works
  in both directions. *(Recorded in the code until 2026-10-17.)*
- **A policy naming a path the host lacks does not build.** `sandlock_create` fails outright,
  and every command comes back with exit -1 and no output, which is why the system paths are
  filtered against the host (`/lib64` is missing on arm64 Debian). *(Recorded in the code until 2026-10-17.)*
- **Through a bound `/proc`, a bubblewrap shell reads other processes' command lines**, and
  with none it reads nothing. `/proc/1/environ` was denied either way by the user-namespace
  mapping, but a command line is enough. Measured with a token generated at runtime. *(Recorded in the code until 2026-10-17.)*
- **bubblewrap's probe has to run something inside what it binds.** The first one bound `/usr`
  and ran `/bin/true`, which is not under `/usr`, and reported bubblewrap unavailable on a host
  where it worked. *(Recorded in the code until 2026-10-17.)*

## Memory-backed workspaces

*Measured for `nothing-at-rest-on-this-machine.md`, removed 2026-09-04 once what
it proposed had shipped. The rule is enforced in `cli/health.py` and
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

## The code sandbox

*langchain-quickjs 0.3.5, 2026-10-11: driven through `eval` against the same call made
directly, and `filter_tools_for_ptc` called on its own.*

- **`ptc=None` turns `tools.*` off.** It is the library's default and means the program
  reaches no tool at all, not every tool. `[]` exposes nothing either: only a list
  naming something does.
- **A list entry is a name or a `BaseTool`, and an object wins.** A name is matched
  against the tools the agent holds; an object is exposed as given, held or not.
  `filter_tools_for_ptc` puts every object ahead of the name matches and keeps the
  first of each name, so a name an object already answers to is dropped, whichever
  order the list wrote them in. Kingfisher hands over no such name anyway.
- **Neither `tools.<name>()` nor `task()` passes through `ToolNode`.** The bridge calls
  `tool.arun(args)` and `task_tool.arun` itself, so no `wrap_tool_call` on the graph
  sees the call. Nor does an `interrupt_on` gate: `HumanInTheLoopMiddleware` is an
  `after_model` hook reading the tool calls on the model's message, and a call from
  code is never on it. The library's own docstrings warn of the gate for both.
- **The bridge injects `runtime`, state and store as `ToolNode` would**, by reading the
  tool's own schema with langgraph's `_get_all_injected_args`. A wrapper has to
  advertise the inner tool's full `args_schema`, injected fields included, or the tool
  is called without them; `tool_call_schema` drops them.
- **An exception the program does not catch leaves `eval` as itself.** The original is
  re-raised out of the evaluation and meets whatever wraps the `eval` call: in
  kingfisher, `HostPathGuard` turned the backend's host-path refusal into a failed
  `eval` result, and a workspace tool's `FileNotFoundError` ended the turn. Caught in
  the program, either reads as `HostError: Host function failed`, its message lost. A
  tool with `handle_tool_error` returns its error text as an ordinary value instead.
- **The `tools.*` reference renders a return type from `tool.func` or
  `tool.coroutine`**, so a `BaseTool` with neither reads `Promise<unknown>` where the
  same function as a `StructuredTool` reads `Promise<string>`.
- **`task()` exists whenever `subagents=True` and the agent holds a `task` tool**, and
  dispatches inside an `eval` already approved.

- **The sandbox snapshots 1,280KB a turn whether or not `eval` runs.** A floor rather than a
  cost that scales with the work: one run called it zero times in forty-five tool calls and
  still paid it. `max_snapshot_bytes=1` took a workspace's thread database from 2.94MB to
  0.31MB over the same two turns; a deployment that builds state across calls should raise it
  and pay the 1,280KB knowingly. On top of a loaded kingfisher the sandbox costs ~15ms and
  ~6MB resident. A turn that hit `recursion_limit` with it still open printed its traceback
  and then hung for as long as it was left. *(Recorded in the code until 2026-10-17.)*

## Costs worth knowing

*Re-measured 2026-09-03. All three had drifted, and one of them was never the
number it looked like -- an agent rebuild costs what the workspace holds, and the
figure recorded had been taken against an empty one.*

- **A whole agent rebuild costs what the catalogue holds.** 8ms in an empty
  workspace; **54ms** in one seeded with the shipped `assets/`, where
  the default agent compiles every subagent it is offered. Between them, 25ms
  for a seeded workspace whose agent names no subagents.

  The conclusion it was recorded for still stands -- against a turn of 1.5-1.9s
  even the worst of those is under 4%, so "loaded once" was never the argument
  for the catalogue. What does not stand is quoting 8ms as *the* number: it is
  the emptiest case there is. *(8ms 2026-08-16; the rest 2026-09-03.)*
- **Each named subagent compiles its own graph, about 6ms, every turn**, whether
  or not the task uses it. Drop a name you never use rather than keeping the list
  tidy. *(4.3ms 2026-08-18, 6.2ms 2026-09-03 -- measured as the difference between
  an agent naming three subagents and the same agent naming none.)*
- **A skills index costs about 600 tokens for three skills, and most of it is
  fixed.** deepagents' own scaffolding -- the "Skills System" preamble it wraps
  the listing in -- is ~450 tokens before a single skill is named; the three
  shipped ones add ~150 between them.

  Which sharpens why a narrow agent with a procedure in its prompt does not take
  one: the cost is almost all entry fee, so a workspace with one skill pays
  nearly what a workspace with three does. *(464 tokens 2026-08-18, before the
  split was measured; ~450 + ~50/skill 2026-09-03.)*

- **Building one agent, item by item.** 8.1ms median and 9.2ms p95 for an unrestricted agent,
  7.2ms of it `create_deep_agent` compiling the graph. Per item added to an otherwise
  identical build: a subagent +5-6ms, a custom tool +0.47ms (linear to at least 50),
  middleware +0.03ms, a skill 0.0ms (sixteen measure as none), a deny rule 0.0ms (a hundred
  measure as none). The costs add: 10 tools, 5 middleware, 20 deny rules and 2 subagents
  predicted 20.8ms and measured 21.6ms. Of a turn's 15-46ms setup, 9.2ms is building the
  agent. Construction is CPU-bound Python and does not parallelise -- about 100 builds a
  second per process, worker threads slightly worse -- so the ceiling is roughly 150
  concurrent turns, or 34 if each activates eight subagents. Above that, a cache keyed on the
  capabilities *and* a fingerprint of the definitions is the thing to reach for.
  *(2026-09-03; recorded in the code until 2026-10-17.)*
- **What importing costs.** `system_prompt` and `ShellPrompt`, while they shared a module
  with `create_deep_agent`, cost 764ms and 3,107 modules; split out, about 10ms and 113.
  deepagents itself was 21ms of it; the rest was provider SDKs reached through it -- openai
  281ms, anthropic 239ms, google 134ms -- whose pydantic type modules compile validators as
  they import. `core.agent.kinds.subagents` costs 1,588ms and 3,164 modules, against 12ms and
  82 for a module importing only the standard library, and the command starts in about 40ms.
  *(Recorded in the code until 2026-10-17.)*

## Historical, and not re-measurable

Numbers that described an arrangement this code no longer has. They are kept
because they are the evidence for a decision, not because they can be checked
again -- the thing they measured is gone.

- **6,872 compilations and seven seconds** for 15 definitions each naming three,
  when subagents were compiled per *path* rather than per definition.
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
