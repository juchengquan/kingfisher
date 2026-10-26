# Which source ids open something

*Designed 2026-10-26. Not implemented.*

A deployment hands out source ids and then cannot tell what they bought. The
listing answers what a caller reaches; nothing answers *which of their names is
why*, or which single name they do not hold would open something more. This adds
one public call that answers both, for one agent and one caller.

```python
opened = kf.opened_by(
    "surveyor",
    source_ids=["A"],
    datazone="finance",
    extra_datazones=("common",),
    capabilities=None,
)
opened.opens.source_ids    # of theirs, the ids that open something
opened.opens.datazones     # of theirs, the zones that do
opened.would.source_ids    # ids they lack, each of which alone would open something
opened.would.datazones     # zones they lack, likewise
```

It reads the definitions and nothing else -- no session, no backend, no model --
so it is a plain synchronous method, which no other method on the service is.

## It answers about a caller, and is not a caller's own view

A scoped listing carries no vocabulary, no audiences and no access report,
because those are the policy and a caller reading their own view is not the
operator checking it. `would` is policy by construction: it names a lock the
caller cannot pass and the name that would pass it.

The line is not that a terminal is safer than a library. It is that `--as B`
*promises to be B's view*, so it must be exactly that, while this promises an
answer *about* B to whoever administers B -- a different object, addressed to the
deployment that constructed the service and already holds the catalogue and the
vocabulary. Nothing in the code will carry that distinction, which is why it is
written here: the next reader will find this unfiltered beside a listing that
filters and reach for consistency.

A deployment that forwards this to its own end users has handed them the
vocabulary. That is the deployment's mistake to make, and the docstring says so.

## One rule, over entries, for both halves

An audience is an or of ands. Write every entry as a set, singletons included.
Then a name opens something when it sits in an entry the caller satisfies, and
would open something when it is the one name missing from an entry of an audience
they do not reach.

Two readings were turned down. *Named in an audience they reach* over-reports:
under `[{A, C}, B]` a caller holding A and B is told A opened it, when the only
entry naming A also wants C. *Load-bearing* -- an id whose removal shuts
something -- under the ordinary `[A, B]` with both held answers nothing at all,
because either alone suffices, and an empty answer reads as "none of this
caller's ids do anything".

The two halves being one rule is the point, not an economy. One walk produces
both, so they cannot drift; this repository keeps recording pairs of readers that
each computed what a caller reaches and came apart.

Every audience goes through `jointly` even where there is nothing to compose it
with, because that drops entries that are supersets of another entry. Without it
`[A, {A, B}]` puts B in `opens` as part of a satisfied entry, when A alone opens
it and B is never a reason.

*Load-bearing* stays available without a second walk: it is a filter over the
same per-thing record of satisfied and one-short entries, an id in every
satisfied entry of some thing. It arrives as its own field with its own word if
it is wanted. It is not a redefinition of this one, or the method's name says
"opens" and its contents say "is required".

## Zones come back apart from the ids

A datazone is held as one more name, and the walk sees zone requirements as
names. They are split out again before they are returned, as bare zone names.

Folding them in fails twice. The folded set cannot be fed back into the call that
produced it: `held_by` refuses a source id beginning with the datazone prefix,
naming it and saying to pass the zone as a zone, so the answer would name the fix
in the one spelling the API rejects. And folded, "one more id would open this"
and "no id can open this" look identical -- a zoned tool is opened by moving the
caller's zone or adding an extra one, a different lever from granting a name.

This is the shape that has already bitten: a subagent writing only a datazone was
listed as open to everyone, because the zone had been folded into the source-id
column. An agent cannot carry a datazone at all, so the zone fields are filled by
tools and subagents only.

## What the walk covers

The agent's own audience first. Composing audiences down the path is what makes
the one-short rule sound: under an agent open to A or B holding a finance-zoned
tool, a caller holding A is correctly told finance alone opens the tool, while a
caller holding neither is told A or B opens the agent and is *not* told finance
would help, because on its own it would not.

Below the agent, each audience is held jointly with the path above it: the
agent's tools to their own locks, its skills entries, its subagents, and each
reached subagent's own audience, granted tools, skills entries and carried tools.
One level of subagents, as the build activates them -- a helper reaches a run only
where the agent names it too. Carried skills reach whoever reaches the subagent,
so they carry no lock and contribute nothing.

Built-in tools are left out. They take no audience deliberately, because
deepagents registers them and a boundary there could not be kept, so no name
opens one.

Skills are walked only where a skill could be opened at all: with skills
switched off for the deployment, or no read tool in the composed grant, the
entries are inert. Skills are off by default, so walking them unconditionally
would have the default deployment report ids that open skills no model is ever
told about -- and send an operator to grant a name that changes nothing.

## The grant is the run's grant

The universe walked is what a run would use: the deployment's own grant
intersected with the request's, bare subagent names settled the way a run settles
them, then narrowed by what the agent declares for this caller. Skipping the
deployment's grant would report that an id opens a tool no run of that deployment
will ever hand out.

So an empty `opens` does not distinguish *a lock shut it* from *the grant
withheld it*, and the docstring says which question this answers: which of this
caller's names open something in a request this deployment would serve. The
pure-lock question is a different call and would need a different name.

## What it refuses

An agent name nothing defines is refused, loudly, listing every defined agent.
The run path conflates an unknown agent with one out of reach, because out of
reach must read as not offered; here the two are told apart, and the departure
needs a comment or it will be "fixed" back. An out-of-reach agent is answered,
not refused -- it is the case where `would` earns the feature, and the reason the
run path hides it does not apply to the operator's own question.

`UNSCOPED` is refused. It means *no caller*, every reach check reads it as
"nothing narrows", and the walk would short-circuit to everything reachable and
nothing attributable -- an empty answer describing the least restricted caller
there is. An empty sequence is a real caller holding nothing, closed out of
everything locked, and is the most useful call here: it answers what the smallest
grant that opens anything would be, with nothing masked by what the caller
already holds. The refusal says so, because that is the mistake somebody reaching
for `UNSCOPED` is making.

On a deployment where no definition carries a lock, every call raises: a named
caller from `held_by`, which refuses a caller where nothing narrows, and
`UNSCOPED` from the refusal above. On one wired with a pre-built graph there is
no agent spec to name, so every call refuses as an unknown agent.

## Where it lives

The two records go beside the access report, in `core/access/source_ids.py`,
which imports nothing from the agent runtime. Both are public, as the inventory
record is: a public method returning an unexported type leaves a deployment
unable to annotate what it got back, and the rule that a public name must not
drag in the runtime is satisfiable only from a module that is free of it.

The walk goes beside the withheld report, in `core/reporting.py`, which already
imports every piece it needs and is the file holding the function it must agree
with. Not a new module under `core/access/`: the activation it needs imports
access back, which is a cycle, and the one layering rule that package has is that
reach does not depend on activation.

## The test

Beside the datazone tests, on the fixture already there: an agent open to A or B,
a finance-zoned tool and a plain one, and a subagent whose whole lock is a
datazone.

Agreement with the withheld report cannot be equality. That report's universe is
the top-level agent's -- a subagent is never a widener, so subagent-private
tools, subagent skills entries and carried tools are permanently outside it.
Worse, it skips any axis the grant does not narrow, so under the default grant it
answers nothing and a naive agreement test passes whatever this returns.

So the report is used as an oracle instead: grant nothing on the audienced axes
and it reports exactly what is in reach. Then both halves are derived from reach
queries alone, never from the entry walk -- `would` is the ids whose addition
makes the reported set grow, `opens` the ids for which some subset of the
caller's names reaches something that the subset without them does not. The
fixture's vocabulary is small enough to enumerate.

The subset quantifier in that sentence is the whole of it. Fix the subset to the
caller's full set of names and the derivation stops describing this rule and
starts describing *load-bearing* -- the reading turned down above -- because
neither A nor B is necessary to a caller holding both. The fixture's caller
holding A and B is what catches it: the rule says both open the agent, the
load-bearing reading says neither does, and only the version quantified over
subsets agrees with the rule. Taking the subset to be the satisfied entry itself
is what makes the two definitions the same, and superset pruning is what makes
that entry minimal, so dropping the pruning breaks the derivation as well as the
answer.

What the oracle cannot see is asserted directly, with a comment saying why, or
somebody folds those assertions into the oracle comparison and quietly stops
testing half the walk.

The oracle has to take the agent's spec out of the catalogue rather than resolve
it by name. Resolving by name refuses a caller the agent is out of reach for --
measured: a caller holding nothing is told the definitions offer no agent at all
-- so going through the run path's resolution makes the oracle blind to exactly
the callers whose answer matters most. Handed the spec, the build succeeds and
the report is empty, which is the answer.

Mutation-check it: break one-short to two-short, drop the joint composition, drop
the superset pruning. Each goes red on its own, which is also the check that the
oracle is not a second copy of the rule.

### Measured, 2026-10-26

Driven against the fixture through the real report, with nothing granted on the
audienced axes. What each caller reaches:

| caller | reaches |
|---|---|
| A | the plain tool |
| A, in finance | both tools, and the subagent |
| B, in finance | both tools, and the subagent |
| A, in hr | the plain tool |
| A, in hr, with finance as an extra | both tools, and the subagent |
| A and B | the plain tool |
| no ids | nothing |
| an id no definition names | nothing |
| no ids, in finance | nothing |

The last row is the one worth keeping: the zone alone opens nothing while the
agent is shut, which is the composed audience doing its job, confirmed by a
reader that knows nothing about this design. The row above it is the other: an id
no lock names is held and opens nothing.

So the answers this fixture must produce are A opening something and finance
would, for a caller holding A; A and finance both opening something, and nothing
further would, once they are in finance; A and B both opening something, and
neither of them *would*, for a caller holding both; and nothing opening while A
or B would, for a caller holding nothing.

## Language

GLOSSARY.md gains two entries when this lands, not before -- a term for a call
that does not exist yet is the drift this repository keeps paying for.

**Opens**: of the names a caller holds, one that sits in an audience entry they
satisfy, so it is a reason they reach something. Not *needs*: several names may
each open the same thing on their own. *Avoid*: grants, permits, unlocks.

**Would open**: a name a caller does not hold that is the one name missing from an
audience entry otherwise satisfied, so holding it alone would open something.
*Avoid*: missing, required, suggested.
