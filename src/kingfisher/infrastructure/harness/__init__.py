"""Where deepagents is spoken to, and the only place it may be.

`infrastructure/` had grown into two unrelated jobs. Some of it adapts the agent
runtime — deepagents, LangChain, LangGraph. The rest adapts the disk, the OS and
the process environment. Both are legitimately infrastructure, so no rule caught
the mixture, and a folder split that way had stopped saying which of the two any
given module was.

The counts that used to be here said 23, ten and thirteen. Two of the three had
gone stale, which is the argument `CLAUDE.md` makes against writing a measured
number into a file nobody re-measures; the directory listing is the answer, and
it is never out of date.

The line matters because it bounds a rewrite. Replace the harness and exactly
these files change; the rest do not know it happened. Read that as the cost of an
*upgrade* rather than a promise of portability — deepagents is beta and has moved
through three minors in two months, and each one rewrites the same files a swap
would. Supporting a second harness is out of scope, and `docs/decisions.md` says
why, along with the part worth knowing first: no port describes what a harness
is, so that work would begin by discovering the interface rather than by editing
this directory.

The split describes the import graph rather than rearranging it, which was true
before this package existed, and `test_the_harness_package_is_the_one_speaking_
to_the_harness` is what keeps it true — in place of a rule that only asked
whether *somebody* in the layer imported something foreign, and passed while any
one file did. That name is checked rather than remembered: the docstring pointed
at `test_only_the_harness_package_speaks_to_the_harness`, which has never
existed, and prose naming a test nobody can find is the decay this module keeps
apologising for.

Four edges cross out of here, and `HARNESS_EDGES` in `test_architecture` names
each one with its reason. This said "one edge" and named `catalogue` alone,
which was true when it was written and had stopped being true well before
anyone read it again — the same decay the table exists to stop, in the prose
describing the table.

`catalogue` and `uploads` both import `skill_registry`, to ask deepagents which
skills it will actually load rather than re-implement the parse and drift
against it. `inventory` builds an agent to enumerate what it registered.
`service` drives the whole thing: an agent to run, a checkpointer to resume it,
a run log, and the runtime that turns its stream into events.

All four are deliberate and the rule permits them — the rule is about foreign
*imports*, not about edges between kingfisher's own modules. The boundary it
claims is a type boundary: a consumer depends on `read()`'s signature, not on
deepagents, so a harness swap still stops here.

Nothing is imported at this level, and that is load-bearing rather than tidy.
`_EXPORTS` names six things in this package and promises the light ones cost
nothing to reach; a single import here would execute on the way to any of them
and pull three provider SDKs in behind it.
"""
