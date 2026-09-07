# The documents

This folder held thirty-eight documents once, and the difference is the point:
agents were grepping their way through ninety thousand tokens of design history
to answer questions that a page could answer.

No count of what is here now. The table is the count, and a number written above
it is one more thing to keep true -- the same rule `CLAUDE.md` applies to the
file count it refuses to print.

| File | What it answers | When to read it |
|---|---|---|
| [`guides/formats.md`](guides/formats.md) | What an agent, subagent, tool or skill file may say. | Writing or changing a definition. |
| [`guides/tools.md`](guides/tools.md) | How to write a workspace tool: the shapes, what it returns, what the loader refuses. | Writing or changing a tool. |
| [`guides/configuration.md`](guides/configuration.md) | Every setting a deployment reads from the environment, and what it defaults to. | Standing one up, or wondering what a variable does. |
| [`guides/ports.md`](guides/ports.md) | How to write an adapter for one of the twelve ports, and how to check it. | Putting sessions, files or commands somewhere kingfisher did not choose. |
| [`guides/middleware.md`](guides/middleware.md) | How to register middleware a definition can name, what it may configure, and what it sees. | Wrapping the agent in code of your own. |
| [`decisions.md`](decisions.md) | Why the code is shaped this way, and what was tried and reversed. | **Before proposing a change** to something it lists. |
| [`findings.md`](findings.md) | What deepagents, langchain and the model surfaces actually do. | Before touching streaming, middleware or delegation. |

The guides are kept current. The other two are records: `decisions.md` says what
was settled and when, `findings.md` says what was measured and when. Neither is a
manual, and an entry old enough to doubt is one to re-check rather than trust.

## `guides/`

How to build and run something on kingfisher, which is a different question
from why the code is shaped this way. The split inside the folder is what the
reader is holding: `formats.md` is a definition -- YAML, fields, what each one
may say; `tools.md` is Python, which has rules of its own because it is code
this process imports and calls rather than data it reads; `configuration.md` is
the environment around both, which belongs to the deployment rather than to any
file in the workspace; `ports.md` is for a deployment that has to put sessions,
files or commands somewhere this package did not choose; and `middleware.md` is
the other code a deployment writes -- not an adapter for something kingfisher
asks for, but a wrapper around the agent that a definition may then select by
name.

Two audiences, and the second one arrived late. The first two pages are for
somebody authoring content *for* the agent; the last three are for somebody
hosting kingfisher. This section said "build something that runs *on*
kingfisher" while the first two of those were being written, and each of them
widened it independently -- which is the sentence doing its job late rather than
twice.

Another guide belongs here when there is another thing to build. Anything
arguing for a shape goes to `decisions.md` and anything measured about upstream
to `findings.md`; a guide that starts arguing with itself is one of those two
wearing the wrong hat.

## `design/`, when there is something to argue

Each of these is an argument rather than a description until its remaining
slices land.

- [An answer that is not
  prose](design/2026-09-05-an-answer-that-is-not-prose.md) — what a run returns
  when a caller wants a schema, and why the format that prompted it is not
  named in the source. Its first slice has landed; the rest waits for something
  that renders.
- [A session owns what it
  costs](design/2026-09-08-a-session-owns-what-it-costs.md) — why every
  per-session file moves into the session directory, where the agent may not
  reach it, and what that removes. Carries one thing that is not a proposal: the
  macOS shell sandbox can be escaped, and that fix is its first slice.

Three have passed through and left the way the rule says. *A store a deployment
can name* was built in four slices; its decisions are under *Wiring a store* in
`decisions.md`. *The front door is for outsiders* was built in three; its
decisions are under *The front door* in `decisions.md`. *A definition is not the
agent's to edit* lasted a day and was built in one, under *Confining the shell*.
*Middleware as a definition kind* lasted the same day and is under *The
catalogue*.

A document belongs there while it is arguing for something; once it is built its
decisions move to `decisions.md`, anything it measured about upstream moves to
`findings.md`, and the file goes -- which is what happened to every one of them.

Two are worth naming, because between them they are the failure this rule exists
to prevent. *A tool failure is not a crash* sat here saying **designed, not
implemented** while `WorkspaceToolErrors` and its test file had been in the tree
for some time. *Nothing at rest on this machine* was audited decision by decision
on 2026-09-01 and still reported "no store port in `domain/ports.py`" six days
after one shipped, and was removed on 2026-09-04 once each remaining claim was
checked against the code.

A proposal that has quietly shipped is worse than a missing document: it reads as
work still to do, and the next person to pick it up rediscovers their own
codebase. **A status line is only true on the day it was written -- check it
against the code before trusting it.**

`superpowers/plans/` held one more and is gone for a neighbouring reason: a plan
for group access control, built and reversed on the day it was written. Its 2,224
lines were implementation steps for a design the loader now refuses by name, and
the part worth keeping -- the argument it lost, which is worth having read before
anyone proposes a central table again -- is in `decisions.md` under *Group
access*.

## What was removed, and how to get it back

Twenty-five design documents, three specs and seven implementation plans, in the
commit that created this page, and the last two proposals on 2026-09-04. They are
in git in full, each recoverable from the commit that removed it:

    git log --diff-filter=D -- docs/design/ docs/specs/ docs/superpowers/
    git show <commit>^:docs/design/2026-08-21-nothing-at-rest-on-this-machine.md
    git show <commit>^:docs/superpowers/plans/2026-08-31-group-access-control.md

The rule this replaces was "`docs/design/` is history and is not rewritten",
written in a plan that is itself now removed. It was right about the danger and
wrong about the remedy: keeping every argument in full is not the only way to
stop one being re-run, and it turned out to be the expensive way. `decisions.md`
carries the same protection -- what was decided, and what was reversed -- at
about a thirtieth of the size.
