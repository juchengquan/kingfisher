# A tool failure is not a crash, unless the workspace wrote the tool

**Status:** designed, not implemented.
**Date:** 2026-08-18

The smoke stopped passing on a real workspace. Not because the analysis was
wrong -- with the workspace tools withheld it scores 11/11 -- but because the
model handed `csv_profile` the agent's routed path, the tool raised
`FileNotFoundError`, and that killed a sixteen-call run.

The tool was right to refuse. Its docstring says so in as many words: *"`path`
is a host path, not one of the agent's virtual paths."* What is wrong is the
consequence. The same mistake through a *built-in* tool costs nothing: the model
is told, looks around with `ls`, and answers sensibly.

## Measured

Same deployment, same mistake -- a path that is not there.

| | |
|---|---|
| `uv run main.py` | `FileNotFoundError` escapes the graph, **exit 1**, 0 checks run |
| `uv run main.py --tools ""` | **11/11 checks passed**, exit 0, 16 model calls |
| `read_file` on a path that is not there | run survives, model investigates, **exit 0**, 5 model calls |
| `csv_profile` on a path that is not there | run dies |

The last two are the finding. One mistake, two outcomes, decided by which tool
the model happened to reach for -- which the deployment cannot predict.

## Three tiers, and the workspace is in the worst one

| what fails | how it is reported | the run |
|---|---|---|
| a built-in tool | `_tool_error(...)` -> `ToolMessage(status="error")` | survives |
| a host path reaching a built-in | `HostPathGuard` -> `ToolMessage` | survives |
| **a workspace tool** | nothing catches it | **dies** |

Upstream's `_default_handle_tool_errors` converts `ToolInvocationError` -- bad
*arguments* -- and re-raises everything else. Kingfisher overrides that for
exactly one type, and says why:

> Only `HostPathError` is caught: a middleware that swallowed every `ValueError`
> would hide real faults behind a retry.

That reasoning is sound about *swallowing* and does not reach the case here.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **A workspace tool's exception becomes a failed `ToolMessage`.** | The two tiers that already work do this, and the model acts on it -- measured: told that a file was missing, it used `ls`, reasoned about the filename, and asked a sensible question. A deployment cannot predict which tool the model reaches for, so it cannot predict whether a wrong argument costs one call or the whole run. |
| D2 | **Returned as an error, not swallowed.** | `status="error"` on the message, the text carried whole. The existing objection is to hiding a fault behind a retry; a failed tool result is the opposite of hidden -- the model sees it, the run log records it, and it reads as a failure rather than a value. |
| D3 | **Bounded by what already bounds it.** | `KINGFISHER_RECURSION_LIMIT` caps the loop -- 150 by default. A tool that always raises fails that many times in a log somebody can read, instead of once with a traceback. Noisier and more diagnosable, which is the right trade for the case D1 is about. |
| D4 | **Workspace tools only. Built-ins keep what they have.** | They already report properly, and `HostPathGuard` exists for the one thing they do not. Widening the catch to everything would put a middleware between deepagents and its own error handling, which is a second opinion on behaviour that is already correct. |
| D5 | **The routed/host path mismatch is not fixed here.** | A workspace tool takes host paths while the agent lives on routed ones, and a docstring is the only defence. That is the deeper fault and it wants its own design -- either routed paths that resolve for workspace tools, or a refusal at the boundary. D1 stops it costing a run; it does not stop it happening. |

## What this does not decide

Whether a *repeatedly* failing tool should be taken away from the model rather
than left to the recursion limit. Three failures of the same tool with the same
argument is a loop, and a middleware could say so. Nothing here needs it, and a
rule that removes a capability mid-run deserves its own argument.

## Sequenced plans

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | A middleware that turns an exception from a *workspace* tool into a failed `ToolMessage`, and a test that the same failure through a built-in is untouched. | — |
| **2** | The smoke passes with the workspace tools granted, which is the case that found this. Asserted, so it cannot quietly stop being true. | 1 |

Phase 1 is the whole change. Phase 2 is what proves it against the run that
started it -- and is worth its own step, because passing with tools withheld is
what disguised the problem in the first place.

## Still undecided

- **Whether `csv_profile` should take a routed path at all.** It is a shipped
  example, and what it currently demonstrates is a hazard: a tool whose contract
  the model cannot see and will get wrong. Rewriting it to accept `/data/...`
  would need D5 answered first.
- **Whether a tool's exception should reach the run report as well.** The model
  sees it either way. Somebody reading afterwards should probably see that a
  tool failed six times without having to read every message, and there is no
  obvious home for that today.
