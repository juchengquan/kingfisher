"""Where deepagents is spoken to, and the only place it may be.

`infrastructure/` had grown to 23 modules doing two unrelated jobs. Ten of them
adapt the agent runtime — deepagents, LangChain, LangGraph. Thirteen adapt the
disk, the OS and the process environment. Both are legitimately infrastructure,
so no rule caught the mixture, and a folder split that way had stopped saying
which of the two any given module was.

The line matters because it is the swap boundary. Replace the harness and
exactly these ten files are rewritten; the other thirteen do not know it
happened. That was true before this package existed — the split describes the
import graph rather than rearranging it — and `test_only_the_harness_package_
speaks_to_the_harness` is what keeps it true, in place of a rule that only
asked whether *somebody* in the layer imported something foreign and passed
while any one file did.

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
