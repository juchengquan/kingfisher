# The front door is for outsiders

**Status:** proposed, in part. Slices one and two landed on 2026-09-06 -- the
three rules, then `doctor`'s vocabulary, eight names in all -- and their
decisions are under *The front door* in `decisions.md`. Slice three is `list`'s
eight and is still an argument, so this stays until it lands or is withdrawn.
Everything the first two settled is description now; check it against the code
rather than trusting it.
**Date:** 2026-09-06
**Occasion:** a question about moving the CLI into its own distribution, the way
the service went. The measurement answered it the other way round, and what it
turned up instead is the subject of this document.

## The question that started it, and why the answer is no

The proposal was `kingfisher-cli`: a second wheel, on the precedent of
`kingfisher-service`. The stated reason was keeping the library's import surface
clean.

The precedent does not carry. The service left because a library caller should
not pay for fastapi and uvicorn to import `Request` -- 2.7MB of web framework
for somebody who wanted a dataclass. The CLI's whole foreign cost is
`python-dotenv`: 100KB, no dependencies of its own, and nothing imports the CLI
when you `import kingfisher`, so a library caller pays for it on disk and never
in time. That is not the same kind of number, and *Packaging: where the
definitions live* in `decisions.md` is a record of this repository reversing
packaging moves made for tidiness rather than for a cost anybody could name.

But the stated reason was real, and pointed at something the split would have
made **worse**. `CONSUMERS` in `tests/unit/test_architecture.py` holds every
consumer to the front door: `from kingfisher import X`, never deeper. It already
spans distributions -- `kingfisher_service` is in it. So a `kingfisher-cli` wheel
would be the second out-of-tree entry, and every name the CLI reaches would be
locked public permanently, as a cross-distribution compatibility promise rather
than an in-tree convention revisable in one commit.

Moving the CLI out is the one move that guarantees the outcome it was proposed
to avoid.

## What the door actually promises

Measured by parsing every `from kingfisher import ...` in both consumers rather
than grepping for names, because a grep for `offered` returns five hits in the
guides and all five are the English word.

| | names |
|---|---|
| exported from `kingfisher` | 57 |
| reached by the CLI and no other shipped consumer | 25 |
| reached by the service | 21 |
| reached by neither consumer | 9 |

Neither consumer means neither the CLI nor the service.
`tests/integration/driver.py` imports two of the six and does not ship, so it is
not one.

And by CLI module, which is where the shape of the answer is:

| module | names | what it is |
|---|---|---|
| `__main__.py` | 23 | run, seed, list, doctor, serve, and the nine refusals |
| `progress.py` | 2 | `RunEvent`, `RunResult` |
| `health.py` | 13 | `doctor`'s probes |
| `listing.py` | 10 | `list`'s renderer |

`cli/__init__.py` says what the front-door rule is for: *"the claim this package
exists to serve is that finding and seeding packs is something any caller can do
-- so if the one command that seeds had to reach inside to do it, the claim was
never true and nothing would have said so."*

That claim lives entirely in `__main__.py`, and every one of its 23 names is
something an outside caller genuinely needs. **Not one of the names the export
table records as consumer-forced is in it.** `Confinement` ("the fourth name the
consumer rule has forced public"), `unrunnable_delegates` ("the fifth"),
`offered` ("public because it reached"), `split_reference`, `SKILL_LAYOUT` -- all
of them are in `health.py` and `listing.py`, a diagnostic and a renderer, neither
of which the proof is about.

So the rule is wider than the claim it exists to prove, and the difference is
paid for in promises made to everybody on behalf of a health report.

## The rule: a caller means a caller outside this wheel

`_EXPORTS` already states the test it used when eleven names left the table: *"A
door advertising what nobody walks through cannot answer the only question asked
of it, which is what a caller may rely on."* Under that rule as written the CLI
walks through, and all 25 stay.

The rule becomes **only a caller outside this distribution counts**. The CLI
ships in the same wheel; it is family and may reach into the cupboards. The
service is outside and keeps the strict rule, front door for everything, exactly
as now. Two consumers, two rules, because one of them is not a stranger.

What that gives up, stated rather than discovered: a deployment embedding
kingfisher and wanting its own health endpoint loses the promise on
`Confinement`, `bubblewrap_available`, `landlock_abi`, `shell_confinement`,
`memory_backing` and `unrunnable_delegates`. The names still work, at their own
addresses, and those addresses may move. There is no measured caller who wants
that today, which is what makes this defensible -- and *Where a deployment reads
from* declined to serve `Origins` over HTTP because the service "authenticates
nobody" rather than because nobody wanted the answer, so the demand is plausible
and this is the entry to re-read if it arrives.

## Enforced by name, not by file

The line could have gone between files -- `__main__.py` and `progress.py` held,
`health.py` and `listing.py` free. Rejected: those two files also use `Config`,
`inventory`, `ConfigError`, `Origins` and `Inventory`, which are real public
names. Freeing the files lets them drift to deep imports for those too, and the
proof would quietly stop covering names it should cover with nothing going red.

So: **the front door is mandatory for anything that is on it.** If a name is in
`__all__`, the CLI imports it from `kingfisher`. If it is not, the CLI reaches
its own address, and that deep import is the visible signal that somebody decided
to keep the name off the list. Self-maintaining in the way the file version is
not -- the day a name goes back on the list, every deep import of it goes red
without anyone remembering to move a file between two buckets.

## The witness table

Sixteen names come off. Nothing stops them coming back, and *that* is the part
worth building rather than the eviction: eleven names already left this table
once, and the reason they had accumulated is that each was added for a good
reason and nobody re-measured. "Only outsiders count" in a comment is a criterion
nothing asks about. Adding to `__all__` is one line.

Every public name gets an entry in a table in the test saying why it is public:
the service imports it, a document teaches it, or it is kept for an outside
embedder with the reason written out. Building it added a fourth value the
argument had not reached: `command`, for a name nothing outside this wheel asks
for and the command is the only caller of. Under this rule that is not a witness
at all, so the bucket *is* the eviction list -- which puts the remaining work in
the code rather than only in this document, and lets a test refuse a mislabelled
entry: the service importing one, or the command not.

The test asserts the table and `_EXPORTS` name the same set, and verifies the
*service imports it* entries by parsing the service's imports, so that half
cannot rot. The other half forces whoever adds a name to write down who asked.

Only the service half is safely mechanical. A document mention is a terrible
witness on its own -- `offered` gets five hits in the guides and `run` gets
thirty-one, all of them the English word -- so "documented" is a written claim in
the table, checked by a person, not a grep.

**This is the fifth list of these names**, after `_EXPORTS`, `__all__`, the
`TYPE_CHECKING` re-export block, `LIGHT_EXPORTS | HEAVY_EXPORTS`, and
`application/__init__.py`'s own pair -- in a file whose own docstring says "a
second table is what this whole file exists to distrust". Accepted on the
precedent sitting beside it: `LIGHT_EXPORTS | HEAVY_EXPORTS` is a second table
with a written reason per group, made safe by a test that it is *total* against
`_EXPORTS`. Same deal, same shape, same guard.

## What it costs the light/heavy guard, and the fix

`Confinement` and `shell_confinement` are in `LIGHT_EXPORTS`, which is not
decoration: `test_a_light_export_stays_light` spawns an interpreter and checks
that touching each light name loads no provider SDK. They are on it *because
`doctor` reaches them* -- the note on `unrunnable_delegates` says it is heavy at
"868ms and 3,137 modules, measured -- which is why `doctor` imports it inside the
check rather than at the top of `health`".

That guard is keyed on `__all__`. Take a name off and it leaves the partition,
and nothing watches its import cost again. Somebody adds a `from deepagents
import ...` to `confinement.py` a year from now and `kingfisher doctor` goes from
9ms to 800ms with nothing red.

So the partition is re-keyed on **consumers**: `_EXPORTS` *union* the names the
CLI reaches directly. A union rather than a swap, deliberately -- nine public
names are imported by neither consumer (`LocalSessionStore`, `Origin`, `RunOn`,
`WorkspacePaths`, `run`, `stream`, and the three testing contracts) and `run` is
heavy, so replacing rather than adding would drop them out of the guard. Neither *consumer*: `stream` is
imported by `tests/integration/driver.py`, which does not ship. The union only ever widens coverage, and
it makes the guard say what it always meant: this is about what a *consumer* pays
to import, and the consumers are the CLI and the service.

## The sixteen

`doctor`'s vocabulary: `Confinement`, `bubblewrap_available`, `landlock_abi`,
`shell_confinement`, `memory_backing`, `unrunnable_delegates`,
`destination_hint`, `DEFINITION_KINDS`.

`list`'s vocabulary: `offered`, `split_reference`, `SKILL_LAYOUT`, `spell`,
`ALL`, `AUDIENCED`, `Audience`, `SEED_HINT`.

57 becomes 41. Two that were on this list and are not:

`kinds_at` stays, on `test_the_whole_job_is_reachable_through_the_front_door`
(`tests/unit/test_inventory.py`), which is the written form of the proof and
names it as part of the job. Whether it belongs there is an argument about the
proof, not about the door.

`ALL` goes, checked rather than assumed: the service models the same concept and
declares its own `Literal["*"]` instead of importing it, so no outsider walks
through the door for it. `Audience` goes on a decision already recorded -- *Where
a deployment reads from* declined to open in-code configuration as a first-class
path, and that type is only needed by somebody building definitions in Python.

The blast radius outside `src/` is one file: `tests/unit/test_doctor.py`, two
names. Nothing else imports any of the sixteen through the front door.

No deprecation window, on the precedent the export table set for the eleven that
left before it: *"An outside caller on the old spelling changes one import line,
which is a real cost measured against users this repository cannot see --
accepted at 0.1.0."* Sixteen is more than eleven and the argument is the same one.

## Three slices

**One -- the rules, and two names.** The by-name CLI rule, the witness table with
its totality test, the partition re-keyed as a union, and exactly two evictions:
`Confinement` (light) and `unrunnable_delegates` (heavy), so every new guard has
a live case in both branches. It cannot land emptier than that. A rule with no
cases passes whatever it says, which is the failure `test_the_base_stands_alone`
and `test_architecture` both carry scars from -- `SRC` counted as `parents[1]`,
the collector reading `SRC / name`, both green over nothing. The `decisions.md`
entry lands here too, because the rule change is the decision.

Three mutations, each broken and restored: make `health.py` import a *public*
name deeply and the CLI rule goes red; add a name to `__all__` with no table
entry and the witness test goes red; add a `from deepagents import ...` to
`confinement.py` and the light guard goes red now that `Confinement` is no longer
public.

**Two -- `doctor`'s remaining six.** `bubblewrap_available`, `landlock_abi`,
`shell_confinement`, `memory_backing`, `destination_hint`, `DEFINITION_KINDS`.
Landed, and it was mechanical as predicted: the six left the export table, the
witness table lost six `command` rows, and `health.py` and `__main__.py` took
each name where it lives. Nothing outside `src/` moved but one import line in
`test_doctor.py`.

**Three -- `list`'s eight.** Mechanical once the guards exist.

Each off `main`, one green commit, merged before the next branches. Sixteen
evictions in the first diff would bury the three guards that are the actual
work.

## What this does not do

**The `TYPE_CHECKING` block was left unguarded here, and fixed separately.**
Nothing bound those re-exports to `_EXPORTS`, and the gap predated this work, so
smuggling it in would have hidden it in a diff about something else. It got its
own change on the same day, and the drift was real when the rule first ran:
`spell` and `SessionInfo` were exported with no stub, which a measurement showed
means `Any` to a type checker rather than the class. The direction named above --
a stub for a name that is *not* exported -- turned out to be the rarer half.

**The CLI stays in the wheel, and this only works while it does.** Every eviction
here depends on the CLI being family. Move it to its own distribution afterwards
and the sixteen have to go straight back on -- which is the finding that turned
the original question round, and the reason it is written down here rather than
left in a conversation.
