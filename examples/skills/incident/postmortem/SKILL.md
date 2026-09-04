---
name: postmortem
description: Writing up an incident after it is resolved — assembling the timeline from logs and messages, separating what happened from why, and producing follow-up actions that name an owner and a trigger rather than an intention.
---

# Postmortem

## When to use

A task asks you to write up an incident, outage or regression that has already
been resolved, or to turn a pile of logs and chat messages into an account
somebody can act on.

Not for triaging something still happening. That wants the shortest path to a
mitigation, and this asks you to slow down and reconstruct.

## Where this one lives

This skill sits in `skills/incident/` rather than directly under `skills/`, which
is the only structural thing it is here to show. A folder under the skills root
is a **source**, and `incident` is one — so this skill can be written
`incident::postmortem` as well as `postmortem`.

Both spellings resolve, and the short one is what the agent is offered while the
name is unique. The qualifier exists for the day it is not: two parties may each
ship a `postmortem`, and then naming it bare is refused rather than guessed —
which turns a working grant into a loud error instead of quietly changing which
procedure the caller gets.

One folder of grouping and no more. Tools and subagents nest as deep as you like;
skills do not, because the agent reads them off the filesystem itself and looks
exactly one level down. `skills/incident/deep/postmortem/` would be invisible,
and `kingfisher list` reports anything hiding below that rather than leaving the
catalogue looking empty.

## The procedure

**Build the timeline first, and only then explain it.** Work from timestamps you
can point at — deploy records, log lines, message times. Write each entry as
*time, what was observed, who observed it*. Resist writing causes into this
section; an incident where the first theory was wrong is the ordinary case, and a
timeline that already assumes the answer hides the minutes where nobody knew it.

**Separate the trigger from the cause.** The trigger is what happened on the day;
the cause is the condition that made the trigger sufficient. A deploy that
exposed a latent limit is one trigger and one cause, and a writeup that names
only the deploy leaves the limit in place for the next one.

**Say what detection actually was.** Whether the alert fired, or whether a person
noticed. Time-to-detect is the number most writeups round in their own favour,
and it is the one that predicts the next incident's length.

**Write actions with an owner and a trigger, not an intention.** "Add a limit
alert, owned by the platform team, before the next capacity change" is an action.
"Improve monitoring" is a sentiment. An action nobody is named on is one nobody
does.

**Name what went right.** A rollback that worked, a runbook that was accurate, a
dashboard that showed the problem. These are the parts worth protecting when
somebody later proposes removing them.

## What to hand back

A timeline, a trigger, a cause, detection and recovery times, and the actions.
Say plainly where the record is thin — an unlogged window is a finding about the
logging, and an account that quietly smooths over it teaches nobody anything.
