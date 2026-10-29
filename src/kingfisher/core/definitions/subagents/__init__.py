"""Subagents: registering what the definitions folder's `subagents/` directory provides."""

#: The name deepagents gives the tool that dispatches a subagent. Not in
#: `core.agent.kinds.subagents`, the obvious home: importing that loads the agent runtime,
#: which `core.agent.interpreter` otherwise does without.
TASK_TOOL = "task"

#: The tool the code sandbox registers, which only an agent ever holds. Handed to the
#: library rather than left to its default: a nested helper's tools are filtered by it and
#: the format refuses a subagent listing it, so a default moved upstream would hand helpers
#: the sandbox and leave the refusal guarding a name nothing registers.
INTERPRETER_TOOL = "eval"

#: The subagent deepagents adds beside every `task` tool, which kingfisher supplies itself.
GENERAL_PURPOSE = "general-purpose"
