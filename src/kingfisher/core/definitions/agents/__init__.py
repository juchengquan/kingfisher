"""Agents: registering what a workspace's `agents/` directory provides.

Three files and the split is inherited rather than invented. `spec` holds the
values the rest of the codebase means by "an agent" -- `AgentSpec`,
`AgentError`. `reading` turns a document into one and owns the format.
`catalogue` finds them on disk. That is `subagents` minus the two halves an
agent does not have: no `rules`, because what has to be true across a *set* of
agents is one sentence -- two of a name is refused in `catalogue` where it is
found -- and no `harness`, for the reason below.

**`core/agent/assembly.py` is not here.** A format's parser is not an assembly:
`core.agent.assembly` builds the graph out of the kinds, and this package reads one
format. What earns a kind a package -- a file somebody authors, a reader for it, and a
registry built from what was found -- is under *The core and its plugins* in
`docs/decisions.md`, and an agent has all three: `agents/*.yaml`, this `reading`, and
`LocalAgentRepository`.

**It reaches no framework at all**, and `THIRD_PARTY` says so with an empty
set. That is a consequence of keeping `core/agent/assembly.py` out of it: an
agent's runtime half is not a kind's to hold.

`spec` is the one file outside this directory's business to import: it is the
format's vocabulary and has no adapter behind it. `catalogue` walks the disk.

No re-exports, as next door: each module is imported by name, so `spec`
answering for the values is a fact about `spec` rather than about this file.
"""
