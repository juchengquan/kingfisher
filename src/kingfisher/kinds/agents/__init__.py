"""Agents: registering what a workspace's `agents/` directory provides.

Three files and the split is inherited rather than invented. `spec` holds the
values the rest of the codebase means by "an agent" -- `AgentSpec`,
`AgentError`. `reading` turns a document into one and owns the format.
`catalogue` finds them on disk. That is `subagents` minus the two halves an
agent does not have: no `rules`, because what has to be true across a *set* of
agents is one sentence -- two of a name is refused in `catalogue` where it is
found -- and no `harness`, for the reason below.

**`harness/agent.py` did not come with them, and that is the point rather than
an omission.** *Not a module: agents* refused this package on the grounds that
an agent "is the thing the graph *is* rather than something the graph holds",
and drew from that: "assembling a graph out of the three kinds is kingfisher's
own job, not any kind's". That is right about `build_agent` and reaches no
further. A format's parser is not an assembly, and the entry gave one reason for
three files where it covers one.

What the other three earned their packages with is stated in *Not a module:
middleware*: "a file somebody authors, a reader for it, and a registry built
from what was found". An agent has all three -- `agents/*.yaml`, this `reading`,
and `LocalAgentRepository` behind `AgentRepository` -- which is why it is here
and middleware is not.

**It reaches no framework at all**, and is the only kind module that does not.
`skills` needs deepagents and langgraph, `subagents` deepagents and
langchain-core, `tools` langgraph alone; this needs nothing, and `THIRD_PARTY`
says so with an empty set. That is a consequence of leaving `harness/agent.py`
where it was: the runtime half is what costs the other three their entries, and
an agent's runtime half is not a kind's to hold.

`spec` is the one file outside this directory's business to import. It is the
format's vocabulary and has no adapter behind it, which is why `domain/` may
name it -- `domain/ports.py` types `AgentRepository.specs` with it, beside the
two siblings it already imports for the same reason. `catalogue` walks the disk;
the domain may not import that.

No re-exports, as next door: each module is imported by name, so `spec`
answering for the values is a fact about `spec` rather than about this file.
"""
