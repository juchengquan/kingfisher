# The documents

Four files and two proposals. It used to be thirty-eight, and the difference is
the point: agents were grepping their way through ninety thousand tokens of
design history to answer questions that a page could answer.

| File | What it answers | When to read it |
|---|---|---|
| [`formats.md`](formats.md) | What an agent, subagent, tool or skill file may say. | Writing or changing a definition. |
| [`decisions.md`](decisions.md) | Why the code is shaped this way, and what was tried and reversed. | **Before proposing a change** to something it lists. |
| [`findings.md`](findings.md) | What deepagents, langchain and the model surfaces actually do. | Before touching streaming, middleware or delegation. |

`formats.md` is kept current. The other two are records: `decisions.md` says what
was settled and when, `findings.md` says what was measured and when. Neither is a
manual, and an entry old enough to doubt is one to re-check rather than trust.

## `design/`

One document, and it is a **proposal that has not been built**. This folder is no
longer history -- history was condensed into `decisions.md`.

- [Nothing at rest on this machine](design/2026-08-21-nothing-at-rest-on-this-machine.md)

A document belongs here while it is arguing for something. Once it is built, its
decisions move to `decisions.md`, anything measured about upstream moves to
`findings.md`, and the file goes -- which is what happened to the other
twenty-six.

The twenty-sixth is worth naming, because it is the failure this folder now has
one job to avoid. *A tool failure is not a crash* sat here saying **designed, not
implemented** while `WorkspaceToolErrors` and its test file had been in the tree
for some time. A proposal that has quietly shipped is worse than a missing
document: it reads as work still to do, and the next person to pick it up
rediscovers their own codebase. A status line is only true on the day it is
written -- check it against the code before trusting it.

## `superpowers/plans/`

One document, and it is a plan that was **built and then reversed**. It is not
`design/`, which is for arguments still being made, and it is not history that
belongs only in git — it is the one case those two categories do not cover.

- [Group access control (`access.yaml`)](superpowers/plans/2026-08-31-group-access-control.md)
  — **reversed 2026-08-31, the day it was written.** Do not implement it. The
  central `access.yaml` it specifies is now refused by name at startup; what
  shipped writes the policy in each definition's own `groups:` line.
  `decisions.md` has the reversal and what survived it.

Kept for one reason: the argument it lost is a good one to have read before
proposing a central table again. It opens with a notice saying so, because a
plan full of unticked checkboxes reads as work to do, and this one is not.

A second document here would be a mistake. A plan that was *built* has its
decisions in `decisions.md` and belongs in git history; a plan still being
argued belongs in `design/`. This folder is for the narrow case of a
worked-out design that was tried and rejected, and one is enough to make the
point.

## What was removed, and how to get it back

Twenty-five design documents, three specs and seven implementation plans, in the
commit that created this page. They are in git in full:

    git log --diff-filter=D -- docs/design/ docs/specs/ docs/superpowers/
    git show <commit>^:docs/design/2026-08-17-assets-as-packages.md

The rule this replaces was "`docs/design/` is history and is not rewritten",
written in a plan that is itself now removed. It was right about the danger and
wrong about the remedy: keeping every argument in full is not the only way to
stop one being re-run, and it turned out to be the expensive way. `decisions.md`
carries the same protection -- what was decided, and what was reversed -- at
about a thirtieth of the size.
