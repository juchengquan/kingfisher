# Middleware as a definition kind

**Status:** proposed, and **blocked on [a definition is not the agent's to
edit](2026-09-07-a-definition-is-not-the-agents-to-edit.md)**.
Not sequenced for tidiness: without that one, this proposal puts a guard in a
directory the guarded thing can rewrite, which is the objection it exists to
answer. Nothing here is built.
**Date:** 2026-09-07
**Cited by symbol, not by line.**

Middleware is the one thing a workspace cannot offer. `DEFINITION_KINDS` is the
fields of `Definitions` -- agents, skills, subagents, tools -- and `seed` walks
exactly those, so `assets_examples/middleware/` is copied nowhere. What a
deployment gets instead is a docstring:

    kingfisher = Kingfisher(
        cfg,
        middleware={
            "call-cap-strict":   CallCap,
            "call-cap-generous": CallCapGenerous,
        },
    )

and an instruction to paste it into its own program. The name `call-cap-strict`
is bound nowhere in the shipped tree; parsing the example finds it in the module
docstring and in zero executable string constants.

## The reason it is that way, and what happens to it

The reason is one sentence: a middleware read out of the workspace would be code
the agent can edit, wrapped around the agent that edited it. A cap the capped
thing can rewrite is not a cap.

That argument is exactly as strong as the premise, and the premise is the subject
of the other document. Once the definition roots are denied to the agent's shell,
a `middleware/` directory beside `tools/` is no longer editable by the thing it
constrains, and the sentence no longer applies.

What does *not* change is who may put something there. `seed` writes into those
directories and a caller cannot: uploads layer `LayeredSkills` and
`LayeredSubagents` and nothing else, and `Capabilities.including` refuses to
widen `middleware` by name -- *"a skill or subagent an upload brings is the
caller's own text; a middleware name selects code the deployment wrote"*. Both
stay. This proposal changes where a deployment may put its own middleware, and
nothing about who else may.

## The change, stated plainly

A fifth kind. `middleware/*.py` declaring what it contributes, the way a tool
file declares `TOOLS`:

    from langchain.agents.middleware import AgentMiddleware

    class CallCap(AgentMiddleware):
        defaults = {"limit": 20}
        yaml_settable = ()
        ...

    MIDDLEWARE = [CallCap]

named from a definition in the long form that already exists:

    middleware:
      - name: CallCap
        settings:
          text: Cite the path and line.

The YAML shape is not new. `researcher.yaml` ships `- name: tool-note` with a
`settings:` block today; what changes is that the name may resolve to a class the
workspace defines rather than only to a key in a registry written in Python.

## Decisions

**The name is the class's, the way a tool's name is the tool's.** `TOOLS` is a
list and each entry carries its own name; `MIDDLEWARE` is a list and
`AgentMiddleware` already has `.name`, which `_warn_if_it_replaces_deepagents`
already reads. A mapping of chosen names to classes would be the deployment's
registry moved into a file, which is a second naming scheme for one idea.

The cost is the lesson `call_cap.py` teaches: `call-cap-strict` and
`call-cap-generous` are two names over one class, and names-from-classes cannot
express that. It does not need to -- the example *already* ships two classes,
because varying the ceiling had to happen in code rather than in YAML. The two
registry names were always standing in for two classes.

**The code registry stays.** A deployment that wires middleware in its own
program keeps working, and there are good reasons to: a middleware closing over a
database handle or a metrics client cannot be a file in a workspace. Two sources
for one kind, which is what `layered` already does for skills and subagents.

**A name in both sources is refused, not resolved.** The same rule two agents
with one name follow, and for the same reason: a definition naming it would get
whichever the walk reached last, with nothing anywhere saying which. Refusing
names both sources and says to rename one.

**`yaml_settable` and `defaults` are unchanged.** What a definition may write is
still the class's decision, and the argument for that has nothing to do with
where the class came from: a cap a definition can set is not a cap, whether the
class was registered in code or read from a directory.

**`seed` gains it for free.** `DEFINITION_KINDS` is derived from the fields of
`Definitions` rather than listed, so adding the field is what makes `seed` walk
the directory -- and `_deployment_specific` already leaves behind a definition
naming middleware the workspace cannot build. That refusal keeps working and
starts being satisfiable by seeding rather than only by writing Python.

## Considered and rejected

**Doing this without protecting the roots first.** The whole argument above rests
on the agent not being able to edit what it is capped by. Built in the other
order, this ships the exact failure the current design refuses, and ships it as a
feature.

**A `middleware/` directory outside the workspace, for this kind alone.** The
first shape considered, and rejected once the tools situation was measured:
middleware and tools are both code the harness executes, and giving one a private
safe directory leaves the other exposed while adding a special case. Solving it
for every definition kind is the same work with a smaller surface.

**Letting an upload bring middleware.** Refused, and not narrowly: it would let
anyone who can upload a definition activate code the deployment wrote, which is
the escalation `Capabilities.including` exists to prevent.

## The order to build in

**1. `Definitions` gains a `middleware` field**, with a repository and a
`MIDDLEWARE` export. `DEFINITION_KINDS` follows, and `seed` with it.

**2. Resolution reads both sources**, refusing a name that appears in each.

**3. `guides/middleware.md` gains the workspace half**, beside the registry half
it documents today, and `assets_examples/middleware/` stops being the one example
a workspace cannot load.

### Guards to mutate

* Define one name in both sources -- the refusal test goes red.
* Drop the `middleware` field from `Definitions` -- `DEFINITION_KINDS` shrinks
  and the seeding test goes red, which is the derivation doing its job.
* Write a setting outside `yaml_settable` on a workspace-defined class -- the
  existing settings refusal must still fire, from the new source.

## What this does not do

It does not make middleware the caller's. A definition still only names, an
upload still cannot widen, and the file still has to be put there by whoever
administers the workspace.

And it does not remove the reason the code registry exists. A middleware that
needs a live object -- a connection, a client, a shared counter -- cannot be
described by a file in a directory, and for those the wiring block stays the
answer.
