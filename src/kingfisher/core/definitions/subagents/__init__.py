"""Subagents: registering what a workspace's `subagents/` directory provides.

`spec` is the one file outside this directory's business to import. It is the format's
vocabulary and has no adapter behind it, which is why `domain/` may name it -- see
`test_domain_imports_only_the_standard_library_and_itself`, where that exception is
stated and measured. `catalogue` walks the disk and `harness` reaches the runtime;
neither is free, and the domain may not import either.
"""

#: The name deepagents gives the tool that dispatches a subagent.
#:
#: The format reads it, to refuse a definition that names helpers and withholds the
#: tool that calls them, and so do `core.agent.kinds.tools` and
#: `core.agent.interpreter`. Not under `harness/`, because the obvious home
#: there is an expensive one: `core.agent.kinds.subagents` is 1,588ms and
#: 3,164 modules, and four characters there would cost `interpreter` all of it -- a file
#: that is 12ms and 82 modules with every other import stdlib.
TASK_TOOL = "task"

#: The tool the code sandbox registers, which only an agent ever holds.
#:
#: Handed to the library rather than left to its default, because two things key on
#: it: a nested helper's tool objects are filtered by it, and the format refuses a
#: subagent that lists it. A default that moved upstream would hand every nested
#: helper the probe's sandbox again, and leave the refusal guarding a name nothing
#: registers. Beside `TASK_TOOL` for its reason, and because a kind may not name the
#: layer `interpreter` sits in.
INTERPRETER_TOOL = "eval"
