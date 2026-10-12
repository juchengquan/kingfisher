# A ledger of tool results

**Status:** slice 1 of 3 has landed: results are recorded, kept as files, written to
the ledger and listed on `RunResult.ledger`. The stamp, the delta and the index are
still to come. Slice 1 settled two things this left open: a result file the backend
refuses leaves its row with `path` null rather than failing the call, and a `task`
answer's access is the `task` grant's own audience held together with its run's.
**Date:** 2026-10-12.
**Occasion:** the owner's notes on artifacts named three pieces of work. The first, *a
tool that creates a file saves it and returns where it went*, shipped, and its entry in
`decisions.md` left the structured record of what a turn produced to this one. The
owner's earlier plan, *Artifact provenance for conversational agents*, is kept outside
the repository. It is the target, and this is its first stage fitted to kingfisher.
Settled with the owner question by question.
**Cited by symbol, not by line.**

## Already decided

Settled before this document, and not reopened by it.

- **deepagents' own files are kept in the scratchpad.** `artifacts_root` is
  `/scratchpad/`, so an offloaded result is under `/scratchpad/large_tool_results/` and
  summarised conversation under `/scratchpad/conversation_history/`.
- **One stack for every graph that holds the workspace tools.** `tool_guards` builds it
  for the agent, every declared subagent, `general-purpose` and nested helpers. A
  compiled subagent gets `GuardedTool` instead.
- **A tool that creates a file saves it and returns where it went.** Its entry ends by
  leaving a machine-readable copy of those facts to this design.
- **The checkpointer is in-memory, and the transcript is what survives.** Every turn
  replays the transcript into a graph built for that turn.
- **A session is its backend.** Everything kept about a session is read and written
  through it.

## What this is for

Two things go wrong once a conversation is long enough for deepagents to summarise it.

- **An earlier result cannot be found again.** The messages a summary replaces go to
  `/scratchpad/conversation_history/` as one file. A search result from turn 3 is
  somewhere in it, and nothing says which result it was or where it starts.
- **The summary's list of what was produced is written from memory.** The summarising
  model writes it, and drops and misnames things.

A caller has the same gap: `RunResult.artifacts` lists files, and nothing lists what the
tools returned.

Results too large to show are already offloaded by deepagents. Images returned inline
were settled by *A tool that creates a file*. Neither is this design's problem.

## The design

**One record, two readers.** The ledger is a list of tool results, one row each. The
model reads a rendering of it in the user message, and the caller reads this turn's rows
on `RunResult`. Kingfisher writes it. The model never writes a row or picks a name.

### What is recorded

- **Every tool except the built-in file tools:** `ls`, `glob`, `grep`, `read_file`,
  `write_file`, `edit_file` and `delete`. What they read or write is already a file, and
  a row for each would bury the rest. `execute`, `task`, workspace tools and MCP tools
  are recorded.
- **Successful results only.** A failed call has nothing worth finding again, and the
  conversation already shows it. Failed means `ToolMessage.status == "error"`, which
  `WorkspaceToolErrors` sets for a tool that raised.
- **A subagent's tool results are *working* entries.** Its answer, which reaches the
  parent as the result of `task`, is a *main* entry. Everything recorded inside a
  subagent's graph, nested helpers included, belongs to the run of the parent's `task`
  call that started it.

### Where: a hook in `tool_guards`

A `wrap_tool_call` middleware, first in the list `tool_guards` returns. Each of the three
things that position gives is needed:

- **It is inside deepagents' eviction,** so it sees a large result whole, before
  `FilesystemMiddleware` swaps it for a stub.
- **It is ahead of `WorkspaceToolPaths`,** so it records the arguments the model wrote,
  not the host paths translation makes of them.
- **It is ahead of a deployment's middleware,** which `build_agent` appends after
  `tool_guards`. So it records what that middleware left, `ToolNote`'s reminder
  included, which is what the model was shown.

**A per-turn collector.** The turn makes one and hands it to `build_agent`, which hands
it to every `tool_guards` call. The graph is built per turn, so the collector never
outlives one. Calls made together run on threads, so it takes a lock. At the end of
the turn the turn loop takes the rows back.

**Which run a working entry belongs to.** The recorder sets a context variable naming
the call's id around the parent's `task` call. The subagent runs inside that call, so
its recorders read the variable. Slice 1 measures first that the variable reaches the
threads langgraph runs parallel calls on.

### Ids

`A-` and a running number for the session, at least four digits: `A-0042`. The next
number is the last one in the ledger plus one, read when the turn starts.

**Stamped into the result.** A last line, `[A-0042]`, is appended to the text the model
gets, and a result made of content blocks gains a text block. deepagents' stub keeps the
head and the tail of a result, so the stamp survives offloading. `task` returns a
`Command` carrying a `ToolMessage`, and the stamp goes on that message.

### Storage

**Each result is written as it arrives,** to `/scratchpad/results/A-0042-<tool>.txt`,
or `.json` where the text parses as JSON. The file holds the result without the stamp.
It is written through the turn's backend under the turn's rules, as a tool's own
`upload_files` is. It lives in `/scratchpad` because the agent reads it back with
`read_file`, which reads a range of lines, and searches it with `grep`. That needs no new
tool. It is also working material, and is swept with the session.

**A result deepagents offloaded is not kept twice.** The recorder cannot know at the
time, because eviction runs outside it. So at the end of the turn, an entry whose call
id has a file in `/scratchpad/large_tool_results/` points there instead, and its own
copy is removed. That costs one listing per turn. A nested helper has no eviction, so
its copy stays.

**The ledger is written once per turn** to `.harness/ledger.jsonl`, beside the
transcript. It is read when the turn starts, the turn's rows are appended in memory, and
it is written whole at the end of the turn. A turn that pauses for approval writes it at
the pause, because its results are as real as a finished turn's. The turn lock already
allows one turn per session at a time, so there is one writer and nothing to interleave.

**In this order:** result files as they arrive, then the ledger, then the transcript. A
row never points at a missing file. A crash between the last two leaves rows for results
the conversation does not show, which is harmless, and their ids are never reused.

**Then a sweep.** A file in `/scratchpad/results/` with no row is removed. Only a turn
that died before writing its rows leaves one, and only one turn runs at a time, so no
grace period is needed.

### What a row holds

```json
{"id": "A-0042", "turn": "<turn id>", "call": "<tool call id>", "run": null,
 "role": "main", "tool": "web_search", "args": "query='EV price war'",
 "access": ["public"], "size": 14211,
 "path": "/scratchpad/results/A-0042-web_search.txt",
 "summary": "web_search query='EV price war' -- Ten results for EV price war"}
```

- **`run`** is the parent's `task` call id for a working entry, and `null` for a main
  one.
- **`args`** is each argument as `name=value`, with a long value cut, so the row stays
  one line.
- **`summary`** is the tool, its short arguments and the first line of the result. No
  model writes it. A model-written summary would cost a call per result, and it is the
  same summary-from-memory this design exists to replace.

Each turn also writes one closing line, `{"turn": "<turn id>", "summarised": true}`, with
no `id`. Turn ids are opaque strings, not a sequence, so the file's order is the turn
order, and a turn that recorded nothing still appears. `summarised` drives the index
below.

### What the model sees

- **Each recorded result carries its id,** so the agent can cite it in the same turn.
- **The next user message carries a delta:** the rows added since the previous user
  message. A subagent's answer is followed by its working entries, indented, one line
  each. The parent sees where they are, never their contents.

  ```
  <ledger_delta>
  A-0042 web_search query='EV price war' -> 14 KB  /scratchpad/results/A-0042-web_search.txt
  A-0045 task subagent_type='researcher' -> 2 KB  /scratchpad/results/A-0045-task.txt
    A-0043 http_fetch url='https://...' -> 31 KB  /scratchpad/results/A-0043-http_fetch.txt
    A-0044 sql_query query='select ...' -> 6 KB  /scratchpad/results/A-0044-sql_query.json
  </ledger_delta>
  ```

  `turn_message` adds it beside the scratchpad line and the new-files line. It goes in
  the user message, not the system prompt, which is the cached prefix.
- **After a turn that summarised, the rolled-up index replaces the delta.** It shows the
  last five turns in full, as above, and every older turn as one line: its id range and
  a count by tool. The turn that summarised finds deepagents' `_summarization_event` in
  its final state, which `_settled` already reads, and records it on its closing line.
  Kingfisher re-summarises every turn once a conversation is long, so in practice the
  index arrives every turn from then on.
- **`system.md` gains one fixed paragraph** explaining `[A-0042]`, the delta, the index
  and where an entry's file is. The text never changes, so the cached prefix holds.

### What the caller sees

`RunResult.ledger`: this turn's entries, main and working, as a tuple of a frozen
`LedgerEntry` holding a row's fields. It serialises to JSON as the rest of the result
does.

### Access

Each entry records the access of what produced it. For a tool, that is the audience the
grant offered it under: the `source_ids` of its `tools:` entry, or else the definition's.
It is `null` where no `source_ids.yaml` is in force. A subagent's answer records the
intersection of its run's entries, because an answer can repeat anything the run read.

**This is recorded and not acted on.** In practice a session belongs to one caller,
whose access is the same every turn, so the ledger has nobody to hide anything from.
Where it would matter, for a caller whose entitlements change between turns, every turn
replays the transcript, which already carries the content. Filtering the ledger alone
would not be a boundary. Callers who must not see each other's data use separate
sessions. The record is kept so that a later design can act on it without old rows
lacking it.

### Turns, and regeneration

Each entry records its turn. Nothing in kingfisher rewinds a conversation today, so every
recorded turn is still in it, and the first version shows them all. A client may later
regenerate from an earlier turn. How that works is its own design: whether it rewinds in
place or forks, what happens to files already in `/outputs`, and how a client names a
turn. It filters the ledger by turn when it lands.

## Checked

On 2026-10-12, against deepagents 0.7.6, langchain 1.3.15 and langgraph 1.2.11.

- **`tool_guards` sits inside eviction.** `create_deep_agent` puts `FilesystemMiddleware`
  in its core stack, and `_apply_custom_middleware` places kingfisher's middleware after
  the core. langchain treats the first middleware as the outermost, so a
  `wrap_tool_call` in `tool_guards` has returned before eviction looks.
- **Eviction** happens above `tool_token_limit_before_evict` tokens (20,000) at four
  characters each. It writes to `<artifacts_root>/large_tool_results/` under
  `sanitize_tool_call_id` of the call id, and exempts the built-in file tools. The stub
  keeps a head and tail preview, so a last-line stamp shows in it. `execute` writes its
  own capture to the same folder.
- **The compaction record is readable.** `_summarization_event` is private state. It is
  absent from what `invoke` returns and present in `get_state().values`, which is what
  `_settled` reads. This was measured with a toy middleware declaring a private field,
  not by driving deepagents' summariser. Slice 3 drives the real one.
- **`task` returns a `Command`** whose update carries the `ToolMessage`.
- **The graph is built per turn:** `_built_turn` calls `_graph_for`, which calls
  `build_agent`. So a per-turn collector can be passed into the build. A graph the
  deployment supplied is not built here, and records nothing.
- **No hook sees two kinds of call:** calls inside a compiled subagent, whose tools are
  `GuardedTool` with no hook on the result, and calls made from `eval`. A compiled
  subagent's answer is still recorded, through `task`.

## Considered and not taken

- **Collecting from the stream** in the turn loop. It reaches further, including a
  compiled subagent's own calls, and needs no lock. But it can only watch, and this
  design changes the result: the stamp now, and the plan's handles later. A first
  version built on the stream would need middleware added later, with two collectors
  to keep in step. The stream also replays results after a superseded pause and
  carries results the approval step made up. Each needs a filter that depends on how
  langgraph shapes its chunks. Middleware sees only real calls.
- **A handle in place of every result,** as the plan has it. Most results are needed
  at once, and reading each one back would double the round trips of every search.
- **SQLite for the ledger,** as the plan has it, for writers that interleave. Here
  there is one writer per turn, and a session's storage is wherever its backend keeps
  it, which need not be a local file.
- **Stored results only kingfisher reads, served by `list_artifacts` and
  `read_artifact` with access checks.** That is only worth having if agents within a
  session have different access. The shell reads the whole session whatever the file
  tools are told.
- **Filtering the index by caller.** The transcript carries the content to every turn
  anyway.
- **Ids by turn, `A-<turn><n>`,** as the plan has it. Regeneration reuses turn numbers,
  and a running number never repeats.
- **Recording failed calls,** and **a model-written summary per entry.** See *What is
  recorded* and *What a row holds*.
- **Estimating the conversation's size to decide when to show the index.** That is a
  copy of the summariser's threshold, and it drifts when the model or settings change.
- **`<artifact_index>` and `<artifact_delta>`** as the tags, which the owner's notes
  used. In kingfisher an artifact is a file under `/outputs` or `/memory`, as
  `RunResult.artifacts` lists, and the word would come to mean two things.
- **Recording what the shell writes.** No hook sees a file `execute` writes.
  `RunResult.artifacts` lists what is present, which is the sound view of files.

## Not in the first version

- **Swapping a large result's stub for a ledger handle.**
- **From the plan, for later:** reusing a result for an identical call within a
  per-source freshness window, declared supersession, and "derived from" links. Each
  needs information nothing supplies today.
- **Regeneration,** which gets its own design.
- **Calls inside `eval` and inside a compiled subagent.**

## Open, and deliberately not decided yet

- **Finding an old entry once it is rolled up.** The ledger is in `.harness`, which the
  file tools refuse. So an entry older than five turns is reachable by its file, through
  `ls` or `grep` over `/scratchpad/results/`, and not by its summary. If that proves too
  little, the index could also be written as a readable file there.

## Plan

Sliced by behaviour, each slice wired and tested on its own.

1. **Results are recorded and kept.** The recorder in `tool_guards` and the per-turn
   collector. Result files, the ledger and its closing line, written in that order with
   the sweep, and offloaded results pointed at their file. `RunResult.ledger`. First
   measured: the context variable reaching parallel calls.
2. **The model sees them.** The stamp, the delta in `turn_message`, and the `system.md`
   paragraph.
3. **The index after summarising.** `summarised` on the closing line, and the rolled-up
   index, driven through deepagents' real summariser.

The last slice moves these decisions into `decisions.md` and removes this document.
