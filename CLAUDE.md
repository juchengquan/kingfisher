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
| What does this environment variable do? | `docs/guides/configuration.md` |
| How do I put sessions, files or commands somewhere else? | `docs/guides/ports.md` |
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

**Comments carry the reasoning, not the mechanics.** A comment earns its place
only if removing it would let a competent editor make a change that is wrong.
Write that reason once, at the point the mistake would be made.

Four kinds do not earn it, and are the ones to cut on sight:

  - how the code got here -- what it was before, what was tried, what a previous
    version did. That is `docs/decisions.md`, or `git log`.
  - a paragraph restating the paragraph above it at greater length.
  - framing that announces the argument instead of making it ("Two halves, and
    the split is the design").
  - the contract a signature already states.

One kind reads like the first four and is none of them. A sentence saying why a
check is built the way it is -- driven rather than inspected, the control beside
the escape, the real tree rather than a fixture -- looks like commentary on
method, and is what stops the next editor simplifying the check into one that
passes against the thing it was written for.
`test_the_shipped_star_costs_nothing_on_a_deployment_with_no_registry` carries
"delete the star and this still passes if it asserts on a spec of its own
making", and `test_no_rule_here_is_parametrized_over_nothing` exists because a
rule with no cases passes whatever it was meant to check. Cutting those was the
largest thing the prose pass got wrong.

There is no density to match. A file of one-line comments is not under-explained,
and the shortest version that still stops the mistake is the right one.

Write the reason where the mistake is, and never send a reader to the module
docstring for it. That puts the guard at the top of the file and the thing it
guards halfway down, and a cut to either end leaves the other pointing at
nothing. `test_no_prose_defers_to_the_module_docstring` exists because that had
happened four times, once since before the prose was cut, with nothing red.

**A test's docstring names the failure it would catch**, in one sentence. When it
guards a specific past bug, name the bug -- that sentence is often the only
record of it, and it stays however short the rest gets.

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
Do not stack branches: this repository squash-merges, which orphans a child PR.

Commit messages here are prose, in the imperative, explaining why the change is
right -- not bullet lists of what changed. Match what `git log` already shows.
