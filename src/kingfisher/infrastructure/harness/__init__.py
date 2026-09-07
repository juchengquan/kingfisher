"""Where deepagents is spoken to, and the only place it may be.

Some of `infrastructure/` adapts the agent runtime -- deepagents, LangChain,
LangGraph. The rest adapts the disk, the OS and the process environment. Both are
legitimately infrastructure, so no rule caught the mixture until this package
drew the line.

The line matters because it bounds a rewrite. Replace the harness and exactly
these files change; the rest do not know it happened. Read that as the cost of an
*upgrade* rather than a promise of portability -- deepagents is beta and has moved
through three minors in two months, and each one rewrites the same files a swap
would. Supporting a second harness is out of scope, and `docs/decisions.md` says
why: no port describes what a harness is, so that work would begin by discovering
the interface rather than by editing this directory.

`test_the_harness_package_is_the_one_speaking_to_the_harness` is what keeps the
split true, in place of a rule that only asked whether *somebody* in the layer
imported something foreign and passed while any one file did.

The edges that cross out of here are named with their reasons by `HARNESS_EDGES`
in `test_architecture`. `catalogue` and `uploads` import `skills.registry`, to ask
deepagents which skills it will actually load rather than re-implement the parse
and drift against it; `inventory` builds an agent to enumerate what it registered;
`service` drives the whole thing. All are deliberate and the rule permits them --
it is about foreign *imports*, not about edges between kingfisher's own modules.
The boundary claimed is a type boundary: a consumer depends on `read()`'s
signature, not on deepagents, so a harness swap still stops here.

Nothing is imported at this level, and that is load-bearing rather than tidy.
`_EXPORTS` reaches into this package and promises the light names cost nothing to
touch; a single import here would execute on the way to any of them and pull three
provider SDKs in behind it.
"""
