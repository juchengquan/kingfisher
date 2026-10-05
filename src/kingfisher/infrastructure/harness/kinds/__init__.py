"""Where a kind's definitions meet deepagents: the half of `kinds/` that names its types.

A kind owns its format and its walk over the disk, and may not name a layer; turning
what it found into something the agent runtime takes is here, a module per kind.
Skills are not, because deepagents reads a skill's document itself and
`kingfisher.kinds.skills.registry` reaches the runtime from inside the kind. An agent
is not, because it is the graph `infrastructure.harness.agent` builds.

Imports nothing: a re-export here would make `infrastructure.harness.kinds.tools`,
which is light, pay for `infrastructure.harness.kinds.subagents`, which is not.
"""
