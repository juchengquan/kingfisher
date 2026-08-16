# Subagent definitions — what the format can and cannot say

**Status:** implemented
**Date:** 2026-08-16

The markdown format defines a subagent with `name`, `description`, `tools`,
`model` and a body that is the system prompt. The question was whether that
covers subagents-of-subagents, tools, skills and the rest. Exploring it found
one impossibility, two gaps, and a live defect.

## What is impossible, not merely unexpressed

**A subagent cannot delegate.** Measured, not read:

```
parent tools        : delete, edit_file, execute, glob, grep, ls, read_file, task, write_file, write_todos
subagent 'reviewer' : delete, edit_file, execute, glob, grep, ls, read_file, write_file
```

deepagents does not give a subagent the `task` tool, so no change to this format
reaches nesting. It is reachable only through `CompiledSubAgent`, which takes a
pre-compiled runnable — a Python object, not something a document can express.

**Decision: leave it.** A subagent that can delegate is a deep agent, and the
value of this format is that a contributor writes one as a *document*. Depth-1
is also deepagents' own choice, and it bounds something worth bounding: each
level multiplies context and cost.

Two smaller findings from the same measurement: a subagent gets no
`write_todos` either, and deepagents inserts a `general-purpose` subagent
alongside whatever the workspace defines.

## What a subagent inherits

| | inherited from the parent? |
|---|---|
| tools | **yes** — deepagents fills `spec["tools"]` when the definition omits them |
| permissions | **yes** — `graph.py:664`, `spec.get("permissions", permissions)` |
| middleware | **no** — a subagent gets only what its own spec carries |
| skills | **no** — which is why it is never told any skill exists |

The permissions result matters: `DATA_IS_READ_ONLY` and the skill denials do
reach delegates, so delegation is not a way around the input guard. deepagents
adds the caveat that already applies here five times over — permissions are
enforced *at the tool level*, and *"direct backend usage does not currently
incorporate permissions"*, which is the shell again.

## `skills:` — a new field (implemented)

Omitted means **none**; naming skills narrows to those, intersected with what
the request activated so a delegate cannot reach past its caller.

That is deliberately *not* how `tools:` behaves, where omitted means inherit.
The asymmetry has a reason worth stating in the format's docs: tools are what a
delegate needs to *act*; skills are what it needs to *know*, and its body
already is its procedure. Inheriting would also cost — measured at **~464 tokens
for three skills**, growing with a catalogue that phase 2 was built to grow —
and would have to be constructed rather than inherited, since a subagent gets
none of its parent's middleware.

## `permissions:` — not exposed

deepagents' field **replaces** the parent's rules rather than narrowing them. A
contributor writing `permissions:` in a document to tighten a delegate would
silently drop `DATA_IS_READ_ONLY`, turning a restriction into the one thing that
unlocks the input guard. Delegates inherit already; that is enough.

`interrupt_on` and `response_format` are likewise unexposed — the first needs a
checkpointer and a human, neither of which exists here.

## The defect: two parsers for one format

deepagents parses skill frontmatter with `yaml.safe_load`. Kingfisher hand-rolls
its own parser, justified in `domain/frontmatter.py` on the grounds that a YAML
dependency *"would accept anchors, multi-line blocks and type coercion"*.
deepagents accepts exactly those, so kingfisher is **stricter than the format it
claims to mirror**:

```
DIVERGE  folded desc  (description: >-)   kingfisher=SkillError   yaml=ok
DIVERGE  list field   (allowed-tools: -)  kingfisher=SkillError   yaml=ok
```

Catalogue skills are never parsed by kingfisher — `skill_store.names` only lists
directories — but **uploaded** ones go through `skill.name_of`. So a skill that
loads fine from the catalogue cannot be uploaded, including any written with the
Agent Skills spec's documented block-list form for `allowed-tools`.

**Decision: keep markdown, parse the frontmatter with real YAML.** The body is a
system prompt and a system prompt is prose; in pure YAML that becomes a block
scalar, worse to write, review and diff. PyYAML is already installed as a
transitive dependency of deepagents and gets declared, because this module now
names it.

## `middleware:` — exposed, and clamped (implemented)

A definition may name middleware from a registry the *deployment* supplies —
`Kingfisher(middleware=…)`, beside `definitions` and `grants` — because
kingfisher cannot define these. Empty by default, so any `middleware:` line
fails loudly until someone wires one.

**It never self-authorises, from any source.** `Capabilities` gains a
`middleware` axis and `grants` clamps it whether the definition came from the
catalogue or an upload. This is the one place the T3 exemption must not apply:
an uploaded skill is the caller's own text, so permitting it grants nothing they
did not already hold, but a middleware *name* selects deployment code. Letting
an upload self-authorise one would reopen the hole T3 closed, one level down.

## Sequence

1. ~~**The YAML fix.**~~ Done.
2. ~~**`skills:`.**~~ Done. Two refusals fell out of it rather than being
   designed: a definition naming a skill nothing defines *raises*, because that
   is a mistake in the definition, while one naming a skill the request did not
   activate is *dropped*, because that is a caller narrower than the definition
   — the same distinction `build_agent` already draws for a request.
3. ~~**The middleware registry.**~~ Done. Both of its refusals *raise*, unlike
   the skills case: running with silently less middleware than a definition
   specified could mean running without the rate limit or audit hook it was
   written to have, which is not the same kind of miss as a delegate lacking a
   procedure. The no-self-authorising rule ended up structural rather than
   checked — `including` has no `middleware` parameter, so there is no widening
   path to get wrong.
