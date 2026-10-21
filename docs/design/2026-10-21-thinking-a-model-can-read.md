# Thinking a model can read

**Status:** proposed, not built.
**Date:** 2026-10-21.
**Occasion:** the review of #890–#1038 found that kingfisher can hand a model thinking it
cannot check. The owner settled the direction below one question at a time. **Cited by
symbol, not by line.** Nothing here was measured against a live Anthropic endpoint; what
that leaves open is under *Not measured*.

## How it works today

A model with thinking on answers with thinking blocks beside its text, each carrying a
`signature` from the service that produced it. langchain keeps them on the stored
`AIMessage`, and every later call in the session sends them back, so the model keeps
its earlier reasoning. Two things decide whether a block sent back is read.

**Who signed it.** A signature is one vendor's; no other can check it. langchain drops
another *package's* reasoning when it builds a request -- `ChatAnthropic` skips a
`reasoning` block from a reply whose `model_provider` is not `anthropic`, and
`ChatOpenAI`'s Responses input skips Anthropic's `thinking` -- but it tells vendors
apart by package. MiniMax, and any gateway imitating Anthropic's API, comes through
`ChatAnthropic`, so its replies are labelled `anthropic` too. Measured on the request
each chat model builds (langchain-anthropic 1.7.6, langchain-openai 1.5.1), with no call
made: Claude to Claude and OpenAI to OpenAI keep their blocks, OpenAI to Claude and
Claude to OpenAI drop them, and a MiniMax reply sent to Claude keeps MiniMax's
signature.

**Whether what came before it changed.** On Claude Fable 5.1, Opus 5.5, Sonnet 5.5 and
Haiku 5.5 a signature also records the system prompt, the tools and every earlier
message. Change any of them and the API treats the block as another conversation's: on
an account created on or after 2026-08-31 the request is a 400 that retrying never
clears, and on an older one the reasoning is dropped without a word.

Nothing on a reply names the vendor. `response_metadata` holds `model_provider` (the
package), `model` and `model_name` (whatever the server reports) and the vendor's
message `id`; langchain keeps no response headers. What does name the signer is the
address the call went to, and kingfisher holds that: every catalogue model is called
through a named endpoint.

## What goes wrong

**A switch between vendors on one wire format.** A session that ran on MiniMax and now
runs on Claude sends MiniMax's signed thinking to Claude. The Claude API reference says
a signature it cannot verify is always a 400, and its `drop_block` option does not
cover that case. The same holds for any two vendors behind `anthropic`, or two
organisations behind `openai_responses`.

**Edits kingfisher makes to what came before.** On the four models above, each of
these changes the conversation a block was signed against:

- the step budget's note (`StepBudgetMiddleware`), and the cut reply with its note
  asking again (`ModelReplyMiddleware`), each sent with one call and gone from the next;
- clearing (`ResultClearingMiddleware`), which rewrites earlier tool results to handles;
- deepagents' summariser, which keeps the newest part of the conversation verbatim
  behind its summary -- the reference names that kept tail as a shape the check fails;
- a system prompt that differs from one turn to the next, as a session's text does when
  a `SessionInstructions` plugin changes its answer, and memory may when the agent has
  edited `AGENTS.md`.

So a long turn on one of those models loses its reasoning at its first clearing pass or
its second step note, and on a newer account fails there.

## Decided

- **Key on who signed the block, not on the model name and not on langchain's label.**
  Within one vendor its API drops what the next model cannot read, and the reference
  says to send blocks back unchanged across a model switch rather than strip them. The
  label names the package, which is how MiniMax's signature reaches Claude.
- **The endpoint stands for the signer.** It is the one fact about who answered that
  kingfisher holds rather than infers.
- **Safe with nothing configured.** Thinking goes back only to the endpoint and model
  that produced it, and every other switch drops it. That never sends a block the next
  model cannot check, a gateway serving several vendors on one endpoint included. It
  costs the reasoning on a switch within one vendor, such as Opus 5.5 to Sonnet 5.5.
- **A `vendor` field waits for a deployment that needs it.** Set on an endpoint, with a
  per-model override for a gateway serving several vendors, it would let models sharing
  a value keep their thinking across a switch. Not `provider`, which the glossary keeps
  off because it was taken for the wire format. Nothing would read it today: the one
  switch in sight, MiniMax to Claude, is one the default must drop.

## Slices

One pull request each, off main, in this order.

1. **Drop thinking another signer made.**
   - When a reply comes back, record on it the endpoint and model that served it.
     `RunLogger` already looks an endpoint up from a model's name.
   - Before each call, take `thinking`, `redacted_thinking` and `reasoning` blocks out
     of the request -- not the stored conversation -- wherever their reply was served
     by another endpoint or model. A reply recorded before this slice compares by
     `model_name` alone, so a session that ran on MiniMax before it is safe after; so
     does a model a deployment passed in, which has no catalogue entry.
   - On every graph kingfisher builds, sync and async.
   - Tests build the request each chat model would send, as the measurement above did:
     another endpoint's block gone, the same endpoint's unchanged, an older reply by name.
2. **Keep the two notes in the conversation.** Store the step note and the note asking
   again as a message after the tool results, instead of adding them to one request.
   `ChatAnthropic` sends a message stored there inside the same user message as the
   results (measured on the request it builds), which is the form the reference gives
   for a reminder that edits nothing. The model then sees earlier notes on later calls,
   as the reference intends, and the transcript and run events show them. What the cut
   reply becomes once it is kept is this slice's first question. With nothing left that
   is sent for one call alone, #1045's move of the breakpoint guards the next middleware
   that adds some.
3. **A safety net for the edits that remain.** Clearing, the summariser's kept tail and a
   system prompt changed between turns stay edits. The API's
   `thinking.block_binding.prefix_mismatch_behavior: "drop_block"`, under beta
   `thinking-binding-controls-2026-08-01`, drops a block whose prefix changed instead of
   refusing the request. Check first whether a deployment can already send it through a
   model's `extra`, since `ChatAnthropic` takes `betas` and `thinking`: if so, this slice
   is a paragraph in `configuration.md` and perhaps a `doctor` warning, not code. The
   reference calls it a stopgap, since the model reasons out again what it can no longer
   read, in output tokens.
4. **Later, and only for these models: edit nothing.** Anthropic's server-side context
   editing in place of clearing, and a summary that replays nothing older in place of
   deepagents' kept tail. Each is a design of its own; neither is proposed here.

## Not measured

There was no Anthropic key. Each of these needs one live call on a model above; the
reference ships a probe for the `drop_block` ones (`drop_block_probe.py`):

- a MiniMax-signed block sent to Claude is the 400 the reference describes (slice 1);
- a dropped step note fails the next call on a newer account, or drops the reasoning on
  an older one (slice 2);
- `drop_block` sent through `extra` reaches the API as the beta expects (slice 3);
- moving the cache breakpoint, as #1045 does, is not itself an edit: the reference says
  the comparison ignores `cache_control`, and nothing here has checked it;
- whether memory is read again each turn, so that an edited `AGENTS.md` changes the
  next turn's system prompt;
- whether Bedrock and the Anthropic API read each other's signatures, which only the
  `vendor` field would act on.

The reference is the Claude API skill bundled with Claude Code 2.1.303 (*Preserved
thinking*, *Prompt caching*, *Error codes*) and the pages it cites,
`platform.claude.com/docs/en/build-with-claude/preserved-thinking` and
`platform.claude.com/docs/en/build-with-claude/thinking`.
