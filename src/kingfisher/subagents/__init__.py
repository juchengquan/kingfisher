"""Subagents: registering what a workspace's `subagents/` directory provides.

Five files and the split is inherited rather than invented. `spec` holds the
values the rest of the codebase means by "a subagent" -- `SubagentSpec`,
`SubagentError`, `RunOn`. `reading` turns a document into one and owns the
format. `rules` holds what has to be true across a *set* of them, which is a
different question from whether any one is well-formed: two of a name, a cycle,
a model that resolves to the thing a delegate exists not to be. `catalogue`
finds them on disk, and `harness` compiles one into the delegate deepagents
expects.

That three-way split was already argued for when these were `domain/subagent/`,
and moving them did not make it wrong. What changed is that the two halves which
used to sit in other layers now sit beside it.

**It does not share with `tools` or `skills`.** All three resolve a
`source::name` their own way, and the duplication is the price of each kind
changing without the other two being consulted.

`spec` is the one file outside this directory's business to import. It is the
format's vocabulary and has no adapter behind it, which is why `domain/` may
name it -- see `test_domain_imports_only_the_standard_library_and_itself`, where
that exception is stated and measured. `catalogue` walks the disk and `harness`
reaches the runtime; neither is free, and the domain may not import either.

No re-exports. Each module is imported by name, so `spec` answering for the
values is a fact about `spec` rather than about this file.
"""
