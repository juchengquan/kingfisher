# Delegations side by side

**Status:** proposed. Nothing built.
**Date:** 2026-10-27.
**Occasion:** asked how kingfisher streams two subagents running at once, the owner
settled the direction below one question at a time. **Cited by symbol, not by line.**
Everything under *Measured* was driven on scripted models through deepagents 0.7.22 and
the langgraph it pins; nothing here was run against a live model.

## How it works today

A turn streams with `subgraphs=True`, so a subagent's chunks reach the caller's stream
under a namespace of their own, and `SubagentNames` turns that namespace into the name
on `RunEvent.agent`. That name is all an event says about where it came from.

It is enough while one delegation is open. When the agent makes two `task` calls in one
message, deepagents runs them at once and their chunks arrive interleaved:

- **Two different subagents.** Every token is tagged rightly, and `Progress` prints a
  `[writer]` or `[reviewer]` tag at nearly every token, empty ones included, since the
  speaker changes at each.
- **One subagent twice.** Every token says `reviewer`, the CLI never changes speaker,
  and the two answers print merged word by word: `b0a0  b1a1  a2b2`.
- **The results.** Each `task` result arrives as the main agent's `tool_result`,
  saying `tool="task"` and nothing about which call it answers.

The same holds one level down, for a subagent running two helpers of its own.

## Decided

**The stream and the CLI; not the run-events plugin.** A deployment's `RunEvents`
record cannot tell parallel calls apart either, but it is built from langchain's
callbacks rather than the stream, so it needs a mechanism of its own. The ledger
already files a subagent's results under the call that started it, so the record after
the fact is not blind the way the live stream is. It is a later design.

**Every event carries the chain of delegations it is inside.** A `delegations` field on
`RunEvent` holds each one's call id, outermost first, and is `()` for the main agent. So a
reviewer started by `t-a` reports `("t-a",)` and its helper `("t-a", "h-a")`;
`delegations[0]` is what the ledger's `LedgerEntry.run` records, and `delegations[-1]`
names the exact delegation. Only the innermost is ambiguous one level down, and only
the outermost loses two helpers one reviewer runs at once. `agent` stays the innermost
subagent's name.

**Calls and results carry their ids, for every tool.** A `model_call` gets `calls`,
index-aligned with `tools` as `args` already is and refused out of step by the same
check; a `tool_result` gets `call`, the id of the call it answers. Without them a
caller seeing events inside `t-a` cannot tell which `task` call `t-a` was, and nobody
can tell which of two runs just finished -- nor which of two parallel `sql_query`
results is which. The singular and plural follow `tool` and `tools`, and `call` is
the ledger's name for a tool call's id. **Not `call_id`**, which on a
`PendingDecision` names a pause, not a call. A provider that leaves a call's id out
gives `""`, keeping the tuple aligned. `decision_needed` gets neither: a pause does not
hold the tool call's id. The ids stay off every printed line; to a reader a provider's
`toolu_01…` is noise.

**A `delegation_start` event announces each delegation, on every turn it is open in.**
It belongs to the delegation it announces: `agent` is the subagent, `delegations` ends
in its own id, and `text` is what it was asked, whole; printed, it is `[reviewer]` and
the first line of that, cut to one line. It fires when the call begins, not at the
subagent's first output, so a fast delegation never prints its opening live before a
slow sibling is known. A call naming no subagent that exists gets one too, followed at
once by its error result: the agent did ask. **No `delegation_end`**: the `tool_result`
whose `call` is the delegation's id ends it, and a pause is not an end. The event is
what makes a resumed turn readable: a resume repeats no `model_call`, and the CLI
answers an approval in a new process, so without it neither the CLI nor a library
caller would know what `t-a` was, or that two delegations were open.

**The CLI holds a delegation's output while a sibling is open beside it.** Siblings are
delegations with the same parent chain. While two or more are open, everything each one
says -- its prose and its own `[model:…]` and `[tool :…]` lines -- is held, and printed
as one block when its result arrives: its `delegation_start` line as the header, what
it said, then its result line. A lone delegation, by far the common case, stays live
exactly as today. Holding only the prose would have kept a sign of life, but the
one-line events interleave too, and two runs of one subagent would need labels
invented for them; a reader wanting a live record of a long parallel run has `--log`.

- **Finish order.** A block prints as soon as its delegation finishes, so a fast one is
  never kept waiting behind a slow one called before it.
- **The header tells two runs of one subagent apart** by what each was asked, the text
  the `task` line above printed for it.
- **Nesting is the same rule one level down.** A held block's own parallel helpers are
  sub-blocks inside it; a live delegation's parallel helpers are blocks on the terminal.
- **Nothing held is dropped.** A turn that stops with a delegation open -- at a gate,
  at its deadline, on an error -- prints what is held, each block marked unfinished,
  before the line saying why it stopped.
- **Unchanged:** stdout still carries only the main agent's answer, and a subagent's
  prose never counts as that answer, held or not.

## Measured

Probes in a scratch folder, not in the tree; slice 2's tests make them permanent.

- **langgraph names a delegation's call, through the `tasks` stream mode.** A start
  event for a `tools` node has an `id` equal to the namespace segment its subagent's
  chunks carry (`tools:<id>`), and its `input` is a list holding the tool call itself:
  `{'name': 'task', 'args': {'description': …, 'subagent_type': …}, 'id': 't1', …}`.
  So the chain is the namespace translated segment by segment, with no change to
  deepagents, whose `task` tool passes no call id into the subagent's config.
- **Two runs of one subagent have distinct namespaces**, so they are told apart even
  though every chunk names the same subagent.
- **Nested helpers map at every level.** Two reviewers in parallel, each running a
  helper: every helper chunk translated to `("t-a", "h-a")` or `("t-b", "h-b")`,
  driven through `stream` and `astream` alike. A compiled subagent, a graph of its own,
  behaved the same.
- **Every sibling's start precedes any sibling's output**: langgraph announces a step's
  tasks before running them. That is what lets the CLI decide to hold before anything
  has printed.
- **A resume re-announces each delegation it re-enters**, under the same id and call.
  With two delegations open and one paused, the resumed stream announced both, and the
  one that had finished before the pause had its result sent again, in langgraph's raw
  stream. The CLI would print it as a short block: its header and its result.
- **The cost is two events per node per step at each level**, start and end: 34 on the
  nested probe, of which kingfisher reads the four `tools` starts. Each holds
  references to objects already built, not copies.
- **A subagent a program starts, with `task()` inside `eval`, never streams.** Two
  started at once ran and answered, and the stream showed only the `eval` result.

## Slices

One pull request, three commits, each green on its own, in this order. The third
needs the first two.

1. **Ids on calls and results.** The `calls` and `call` fields, read where
   `_event_for` builds the two kinds; `tool_calls` returns the ids beside names and
   arguments. The check in `RunEvent.__post_init__` covers `calls` as it covers
   `args`.
2. **The chain and `delegation_start`.** `tasks` joins `STREAM_MODES`. A map beside
   `SubagentNames` learns segment to call id from each `tools` start whose call is
   `TASK_TOOL`, and ignores every other `tasks` event; `events_in` stamps
   `delegations` on every event and turns those starts into `delegation_start`. A
   segment no `task` call started -- a supplied graph's own subgraph node -- adds
   nothing to the chain. `delegation_start` joins `KINDS`, whose comment already tells
   a consumer to ignore a kind it does not know. Tests drive a real two-level graph
   rather than a fixture of `tasks` chunks: the input's shape is langchain's, and a
   fixture would keep passing after it changed.
3. **The CLI holds side-by-side delegations.** In `Progress`, keyed on `delegations`,
   as decided above. The upgrading notes cover the new fields and kind, and the CLI's
   output where delegations overlap.

Once built, the decisions move to `decisions.md`, the measurements to `findings.md`,
and this file goes.

## Checked first, while building

- Whether kingfisher passes on the result langgraph sends again on resume, today and
  once `delegations` is stamped. `decisions.md` already records that the stream
  replays results after a superseded pause; a repeat here would be the same thing.
- The cost on a long session, beyond counting events: a `model` start carries the whole
  state, by reference.

## Not proposed

- **The run-events plugin**, above.
- **A program's subagents**, which do not reach the stream at all; making them visible
  is a change of its own.
- **Which subagent a pending approval belongs to with two open.** `PendingDecision.agent`
  is `None` then, because the pause is read from the checkpoint rather than the stream,
  and the wrong name beside a call about to be approved is worse than none.
