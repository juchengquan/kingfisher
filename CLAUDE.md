# Working in kingfisher

## Checks

Exactly what CI runs, and all three must pass before a commit:

    uv run ruff check src/ tests/ service/ assets_examples/
    uv run ty check
    uv run pytest -q

`ty` runs with `error-on-warning`, so an *unused* ignore directive fails too.

**Never run `ruff format`.** It is not in CI and the tree is not formatted to
it: it rewrites most of the repository, burying a real change in noise. `ruff
check` is the only ruff this project runs.

No count here on purpose. This said "56 files", then "90", and was "107" the
next time anybody looked -- a measured number in a file nobody re-measures is a
small lie with a date on it. `ruff format --check` will tell you today's.

`ruff` prints a trailing note about fixable problems that reads like a summary --
it is not one. Look for `All checks passed!` or `Found N errors`, not the last
line.

Run bare `pytest`, not `pytest tests/`: the latter skips the `service/`
distribution, which has its own suite and its own CI job.

## Where to look

| Question | File |
|---|---|
| What can an agent/subagent/tool/skill file say? | `docs/guides/formats.md` |
| How do I write a workspace tool? What may it return? | `docs/guides/tools.md` |
| Why is it built this way? Can I change it? | `docs/decisions.md` |
| What does deepagents/langchain actually do? | `docs/findings.md` |

**Read `docs/decisions.md` before proposing a change to anything it lists.**
Several things in this codebase were proposed, built, and reversed; the reversals
are recorded there precisely so the argument does not get re-run. `docs/design/`
holds only proposals that have not been built.

**Check a proposal against the code before working from it.** A status line is
true on the day it was written and not necessarily after. One document sat in
`docs/design/` saying *designed, not implemented* while the middleware it asked
for had already shipped, and a test cannot catch that -- a proposal names things
that do not exist yet, which is what makes it a proposal. Grep for the thing it
proposes before building it.

Do not go looking for design history in the tree -- it was condensed into those
files deliberately, and the originals are in git if an entry is not enough.

## Conventions

**Comments carry the reasoning, not the mechanics.** This codebase explains *why*
a thing is the way it is, including what was tried and abandoned. Match the
surrounding density -- a terse comment in a file of long ones reads as an
oversight.

**A test's docstring says why the test exists**, not what the assertion does. When
a test guards against a specific past failure, name it.

**Layering is enforced, not remembered.** `tests/unit/test_architecture.py` parses
imports: `domain/` imports nothing foreign, and only `infrastructure/harness/`
may import deepagents, langchain or langgraph. Adding a foreign dependency means
updating those rules, not working around them.

**Measure before building on a premise.** Several decisions in `docs/decisions.md`
exist because a stated premise turned out to be false when someone checked. If a
plan rests on "X is slow" or "Y is not supported", verify it first.

**Mutation-test a new guard.** A test that passes is not the same as a test that
bites -- break the thing it guards and confirm it goes red, then restore.

## Landing work

Independent slices, one green commit each, a pull request per slice off `main`.
Do not stack branches: this repository rebase-merges, which orphans a child PR.

Commit messages here are prose, in the imperative, explaining why the change is
right -- not bullet lists of what changed. Match what `git log` already shows.
