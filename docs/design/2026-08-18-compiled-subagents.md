# A subagent that is code, and a model chosen rather than named

**Status:** proposed.
**Date:** 2026-08-18

deepagents accepts two kinds of subagent. `SubAgent` is a spec it builds — name,
description, system prompt, tools — and that is what `subagents/*.yaml` has
always described. `CompiledSubAgent` is a graph you built yourself, which
deepagents runs as given.

The second is the one this is about. It is how a delegate gets a shape a prompt
cannot express: a fixed sequence of steps, a structured return type, a retry
that is not the model's decision.

Writing it down turned up a second thing, and it is not about compiled agents at
all. `second-opinion` names a model that can silently become the wrong one, and
the refusal that would catch it already exists for half the cases. That half is
phase 1, because it stands alone and it is a live defect.

## What was measured

**`CompiledSubAgent` cannot be detected by type.** It is a `TypedDict`, so at
runtime it is a `dict` and `isinstance` does not return the wrong answer — it
raises, `TypedDict does not support instance and class checks`. Its
`__required_keys__` is `{name, description, runnable}`, against `SubAgent`'s
`{name, description, system_prompt}`, and deepagents tells them apart with
`if "runnable" in spec`.

**A built graph is nearly opaque.** `create_agent(...)` returns a
`CompiledStateGraph` whose `.name` is the literal string `'LangGraph'` and whose
`.description` is `None` — which is why deepagents wraps it in a dict rather
than taking it bare. Its tools *are* reachable, at
`agent.nodes["tools"].bound.tools_by_name`, but only because `create_agent`
happens to build a node called `tools`. A hand-written graph is the entire
reason this feature exists and need have no such node. The model sits inside a
closure and was not reachable at all.

So inspection answers correctly for the simple case and wrongly for the
interesting one. That is the shape of the bug `skill_registry` exists to
prevent: kingfisher advertised four skills and deepagents loaded three, two of
them different.

**Kingfisher already reads that dict, and already knows it may fail.**
`registered_tools(graph)` in `harness/agent.py` walks exactly this path, and
says why it does not raise: "the path into the tool node is not a public
contract, so a shape we do not recognise yields `()` — which callers read as
*cannot check* — rather than raising and taking down every build over an
introspection detail." The three-state answer S10 needs is therefore already
built, tested and in use; what is new is a caller that reports the third state
instead of treating it as an empty set.

**The two walks do not overlap.** `_definitions_in` collects `SUFFIX = ".yaml"`
at any depth and treats folders as organisation. `_modules_in` collects `.py`,
skips names beginning with `_`, and stops at a folder holding `__init__.py`
because "descending would scan the helper modules it exists to hold as though
each were a tool file." Neither walk can see the other's files, so one directory
can carry both.

**Name collisions are already solved.** `LocalSubagentRepository._defined` keeps
two definitions sharing a name and qualifies each as `where::what`, because
refusing at load time "stopped the whole catalogue loading over a clash no
single agent had yet asked for, and was unfixable by anyone who owned neither
file." A `.py` and a `.yaml` both claiming `reviewer` need no new rule.

**Imported modules cannot collide either.** `_module_name` keys on the full path
plus a hash, so "a workspace file and a real installed package" stay apart — and
so do `tools/analysis/` and `subagents/analysis/`.

**The refusal `second-opinion` needs already exists, for the other half.** Its
own file says an unbound alias is refused, because "an unbound alias falling
back to the default would hand this delegate the one model it exists not to be,
and nothing in the output would look wrong." A *bound* alias that resolves to
the deployment's own model is the same sentence with the same ending — and it is
reported, not refused. `indistinct` says why: kingfisher "cannot know that a
delegate *needs* to differ", since `reviewer` deliberately runs on the same
model. Nobody ever gave a definition a way to say it.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | **A definition may declare `distinct: true`, and then an indistinct model is refused rather than reported.** | The check is written and running; only the intent was missing. `indistinct` reports because it cannot know a delegate needs to differ — so let the delegate say. The precedent is one field away: an *unbound* alias already refuses, for the identical reason, and the bound-but-identical case falls through the same hole with the same silence. |
| S2 | **`model` and `alias` may take a list, tried in order; the first that satisfies `distinct` wins, and none is a refusal.** | This is what makes the model dynamic without inventing a query language: the definition says what it would accept, kingfisher says which one it got. A single value stays legal and means a list of one, so nothing already written changes. |
| S3 | **Compiled subagents live in `subagents/`, told apart by extension.** | One kind, one place. `--list` already prints subagents from one directory and grants already name them from one namespace; a second directory would split both for a difference the caller does not care about. The two walks cannot see each other's files, so this costs no rule. |
| S4 | **Both shapes: a flat `researcher.py`, or a folder with `__init__.py`.** | Tools already resolve this, for the reason that applies here: a folder is what you reach for when it grew helpers, and forcing one on a single-file agent is ceremony. `__init__.py` is the switch, exactly as in `tool_store`. |
| S5 | **A package still has its `.yaml` files read.** | A Python package holding data files is ordinary; nobody is surprised that a package contains a `config.yaml`. The alternative was refusing them, which only existed to stop `analysis/profiler.yaml` vanishing when someone adds an `__init__.py` — and it cannot vanish if the YAML walk never stops. |
| S6 | **`SUBAGENTS` is declared, never inferred.** | The type cannot be detected anyway, so inference would mean "a dict with a `runnable` key" — and module-level scanning sees imported names, so `from .base import RESEARCHER`, written to compose one agent into another, would offer `RESEARCHER` as a delegate nobody meant to expose. Same finding as `TOOLS`: "a helper promoted to a tool by accident is worse than one that never appears." |
| S7 | **`SUBAGENTS` must be a list or tuple.** | `TOOLS` learned this: a `BaseTool` is a pydantic model and pydantic models are iterable, so `TOOLS = add` passed a duck test and then iterated the tool's own fields. A compiled subagent is a `dict`, and a bare `SUBAGENTS = {...}` would iterate its key names. |
| S8 | **Name and description are static text; only the graph comes from a function.** | `--list` prints subagent names today without building anything. A name returned by the function would make listing resolve a model, which needs credentials and a `models.yaml` — an API key to print a list of names. |
| S9 | **The function receives the model and tools; it does not choose them.** | The reverse — handing the file a resolver it may call — enforces nothing, because nothing makes it call. Declared, kingfisher resolves and grant-checks them itself, so the listing is true without inspecting anything and the right thing is the easy thing. A file can still ignore its arguments; that is true of any Python in this workspace, and a smaller hole than nobody being able to see what a delegate uses. |
| S10 | **Verify with `registered_tools`, and distinguish "no tools" from "cannot check".** | The introspection already exists and already degrades safely to `()`. What it does not do is tell its caller which of the two happened, because until now no caller needed to: an empty tuple and an unreadable graph led to the same place. A compiled delegate is the first case where the difference is the whole point, so `--list` must say "no tools" or "could not confirm" and never quietly print the first when it means the second. Advertising an unverified answer is what `skill_registry` was built to stop. |
| S11 | **The type is a check, not a search.** | `CompiledSubAgent.__required_keys__` is deepagents' own declaration of the shape, so validate against it rather than a copy of it — the same move `skill_registry` makes, pinned by a test so an upstream rename fails the build instead of a run. |
| S12 | **A file in `subagents/` with an unrecognised extension is an error.** | `researcher.yml` is invisible to both walks today and would stay invisible. "Quietly offering fewer tools than the workspace defines" is the failure `tool_store` names as the one to avoid, and two kinds sharing a directory doubles the ways to misspell your way into it. |
| S13 | **A `.py` file may declare only the compiled kind.** | deepagents would accept a `SubAgent` dict there too. The prompted kind already has a format with grants, narrowing and an upload path; a second spelling of it in Python would be two ways to write one thing, and the YAML one would be the one with the rules. |

## What a file looks like

```
subagents/
  reviewer.yaml            prompted, as today
  researcher.py            compiled: defines SUBAGENTS
  analysis/                a folder, for tidiness
    profiler.yaml          prompted, still read
    deep_research/
      __init__.py          compiled: defines SUBAGENTS
      _steps.py            a helper, never scanned
```

```python
# subagents/researcher.py
from langchain.agents import create_agent

SUBAGENTS = [
    {
        "name": "researcher",
        "description": "Researches a topic and returns findings.",
        "alias": "cheap",
        "builtin_tools": ["read_file", "glob"],
        "tools": [],
        "build": lambda model, tools: create_agent(model, tools),
    }
]
```

Every key except `build` means what it already means in a `.yaml` file, and is
read, granted and narrowed by the same code. A compiled subagent is a prompted
one with `system_prompt` replaced by `build`.

Three fields deliberately have no effect here and are refused rather than
ignored: `skills`, `middleware` and `subagents`. deepagents applies none of them
to a graph it did not build, so accepting them would be a file writing lines
that do nothing — which is what `refuse_helpers_with_helpers` already exists to
prevent for one of the three.

## What this does not fix

A compiled subagent's tools are outside the grant system in one direction. We
hand in the narrowed list and check the result where the graph permits, but a
file that ignores both arguments and builds its own is not stopped. It is not
stoppable: deepagents runs the graph as given and never applies an allowlist to
it.

So the honest position is S10 — `--list` marks a compiled delegate and says
whether its tools were confirmed. Making it a *grant* was considered and
rejected: anyone who can add a file here can add one to `tools/`, so the switch
adds a step and protects nothing.

## Three things this got wrong, found while building phases 2 and 3

**`builtin_tools` cannot be honoured, so it is refused.** The example above
writes one. Traced through, deepagents' own tools are constructed inside the
parent's assembly and exist as objects only after it — `tool_objects` is passed
to a *helper* and is `None` for a top-level delegate. There is nothing to hand a
graph, so accepting the key would be a line that does nothing, which is the
thing `REFUSED` exists to prevent. It joins `system_prompt`, `skills`,
`middleware` and `subagents` in `NOT_COMPILED`, each with its own reason.

The spec's `builtin_tools` is set to `None` rather than left at `ALL` for the
same reason: a ceiling nothing can fill would have `--run` reporting a delegate
withholding tools it was never able to have.

**S12 was too broad.** "An unrecognised extension is an error" was written
before S4 and S5 let a folder be a Python package. A package is entitled to hold
what it needs beside its `__init__.py` — a JSON fixture, a prompt in a text file
— and refusing every unfamiliar suffix would break that for the sake of one
confusion. So the one confusion is named: `.yml` is refused, because it is valid
YAML everywhere else and a file spelled that way is a definition somebody wrote
and kingfisher silently did not read.

**S11 applies one step later than it reads.** "Validate against
`CompiledSubAgent.__required_keys__`" sounds like it governs what a file
declares. It does not: a declaration is kingfisher's own format, with `build`
where deepagents has `runnable`, so kingfisher owns its key set exactly as it
owns the YAML one. deepagents' declaration governs the dict we *hand over*, and
that is where it is pinned. The useful consequence is that discovery needs no
deepagents at all, and therefore no new entry in `HARNESS_EDGES`.

## Phases

| # | What | Verification |
|---|---|---|
| 1 | `distinct`, and `model`/`alias` accepting a list. `second-opinion` declares it. | Bind `alternate` to the deployment's own model and confirm activation is refused rather than reported. Bind two aliases and confirm the second is chosen. Mutations on the resolution order and on the refusal. No compiled subagent exists yet; this stands alone and fixes a live defect. |
| 2 | Discovery: `.py` in `subagents/`, both shapes, `SUBAGENTS` declared, unknown extensions refused. | A package's `.yaml` still loads. A `_helper.py` never does. A `.yml` is an error. A module without `SUBAGENTS` names itself in the message. |
| 3 | Building: static name and description, `build` called per request with a resolved model and narrowed tools. | Shape validated against `__required_keys__`, pinned by a test. Mutations on each grant that is meant to reach the delegate. |
| 4 | Reporting: `--list` marks compiled delegates, and `registered_tools` learns to say "cannot check" apart from "none". | Run against a `create_agent` graph, where confirmation works, and against a hand-written `StateGraph` with no tool node, where it must say so rather than print an empty list. Mutate the distinction away and watch the second case start lying. |

## Still undecided

- **Whether `distinct` should have degrees.** `indistinct` already returns two
  different reasons — the same model id, and a different id on the same host —
  and a deployment behind a gateway serving several vendors might reasonably
  refuse the first and accept the second. `second-opinion` wants both refused,
  so nothing has asked for the split yet, and a field with one meaningful value
  is a boolean wearing a costume.
- **What a compiled delegate's `build` gets beyond a model and tools.** A
  session directory and the backend are the obvious next asks. Neither has a
  caller, and `test_nothing_is_defined_for_tests_alone` is the standing answer
  to adding one before it does.
- **Whether a compiled delegate can be uploaded.** It cannot, and that is not an
  oversight — `uploads` accepts `skill_refs` and `subagent_refs` and nothing
  else, and a caller uploading Python is a different conversation from a caller
  uploading text.

## Not in scope

**The prompted format.** Nothing about `system_prompt`, skills, middleware or
nesting changes. Phase 1 touches `second-opinion.yaml` and the model resolution
it uses, and that is all.
