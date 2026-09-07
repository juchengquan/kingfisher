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
values is a fact about `spec` rather than about this file. `TASK_TOOL` below is
a definition rather than a forwarding: there is one of it, so there is no second
listing for it to drift against.
"""

#: The name deepagents gives the tool that dispatches a delegate.
#:
#: A fact about this package rather than about any module in it, which is what
#: puts it here rather than in one. `spec` is the format and no definition
#: writes `task`; `harness` compiles a delegate and both readers are outside
#: this directory -- the interpreter decides whether a sandbox may dispatch one,
#: and the tool surface hides it from a compiled graph's roster.
#:
#: It sat in `harness` because neither of those may import the module that
#: assembles them, and that was half a reason: it said why the name is not
#: beside the assembly, never why it belonged in the most expensive module here.
#: `harness` imports deepagents, so four characters cost `interpreter` 1,569ms
#: and 3,160 modules -- in a file whose every other import is stdlib -- and cost
#: `tools.harness` 1,205ms. From here they are 17ms and 56ms, no SDK loaded.
#:
#: Not `domain/`, which is where it reads like it belongs. `docs/decisions.md`
#: settles that shape twice, for `Config` and again for `layout`, and the test
#: both moves applied is "does a domain rule read it" rather than does it sound
#: like vocabulary. None does. Not `infrastructure/harness/` for the other half
#: of the `layout` entry: `tools` is nowhere near deepagents, and a name that
#: imports nothing does not belong in the package the layering rule quarantines
#: for importing a framework.
TASK_TOOL = "task"
