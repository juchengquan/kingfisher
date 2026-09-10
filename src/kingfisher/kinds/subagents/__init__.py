"""Subagents: registering what a workspace's `subagents/` directory provides.

`spec` is the one file outside this directory's business to import. It is the format's
vocabulary and has no adapter behind it, which is why `domain/` may name it -- see
`test_domain_imports_only_the_standard_library_and_itself`, where that exception is
stated and measured. `catalogue` walks the disk and `harness` reaches the runtime;
neither is free, and the domain may not import either.
"""

#: The name deepagents gives the tool that dispatches a delegate.
#:
#: It sat in `harness` because neither of those may import the module that assembles
#: them, and that was half a reason: it said why the name is not beside the assembly,
#: never why it belonged in the most expensive module here. `harness` imports
#: deepagents, so four characters cost `interpreter` 1,569ms and 3,160 modules -- in a
#: file whose every other import is stdlib -- and cost `kinds.tools.harness` 1,205ms. From
#: here they are 17ms and 56ms, no SDK loaded.
TASK_TOOL = "task"
