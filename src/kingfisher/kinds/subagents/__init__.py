"""Subagents: registering what a workspace's `subagents/` directory provides.

`spec` is the one file outside this directory's business to import. It is the format's
vocabulary and has no adapter behind it, which is why `domain/` may name it -- see
`test_domain_imports_only_the_standard_library_and_itself`, where that exception is
stated and measured. `catalogue` walks the disk and `harness` reaches the runtime;
neither is free, and the domain may not import either.
"""

#: The name deepagents gives the tool that dispatches a delegate.
#:
#: **No kind reads it.** Both readers are `infrastructure.harness.tools` and
#: `infrastructure.harness.interpreter`, so by subject it belongs beside them, and the
#: reason it is here instead is that the obvious home is an expensive one:
#: `infrastructure.harness.subagents` is 1,588ms and 3,164 modules, and four characters
#: there would cost `interpreter` all of it -- a file that is 12ms and 82 modules with
#: every other import stdlib. Moving it means finding a home under `harness/` that
#: stays cheap.
TASK_TOOL = "task"
