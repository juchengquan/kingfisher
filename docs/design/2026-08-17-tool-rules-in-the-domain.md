# Where a rule about a tool name lives

**Status:** implemented.
**Date:** 2026-08-17

Tool handling is spread across a dozen modules, which is mostly fine — loading,
enforcement and observation are genuinely different jobs and belong apart. One
part of the spread is not fine: the rule that decides whether a tool name is
valid has two implementations, and the domain holds a third that is dead and
could not be right if revived.

This is about the rules, not the loading. The loading was largely settled by the
repository refactor that landed just before this: `domain/tool.py` holds `Found`
and `tool_name`, `domain/ports.py` holds `AssetRepository` and `ToolRepository`,
and `LocalToolRepository` reads the directory. That work is assumed here rather
than repeated.

## What is actually spread

| Concern | Where | Verdict |
|---|---|---|
| finding tools on disk | `LocalToolRepository`, `layered.for_session` | correct |
| carrying one to the agent | `domain/tool.Found` | correct |
| **which axis a name is on, and whether it exists** | `agent._refuse_unknown_tools` **and** `delegation.refuse_unknown_tools` | **two homes** |
| narrowing to what is permitted | `agent._permitted_tools`, `delegation.tool_ceiling` | *similar, and genuinely different* — see T7 |
| enforcing it at runtime | `scoping.ToolAllowlist` | correct |
| reading tool calls back | `runtime.tool_calls`, `runlog.on_tool_*` | correct |

The two refusals share an identical loop over the same five-tuple —
`(asked, own, other, here, there)` — and differ only in where the selections
come from and how the message is worded. `delegation.py` says so itself:
"`_refuse_unknown_tools` says the same thing to a request, for the same reason."
The duplication audit does not catch it, because it looks for a rule
*transcribed* and this one was *rewritten*.

The tell is `belongs_in`. That is the sentence about which axis a name belongs
on, and it already lives in `domain/capabilities.py`. Half the rule reached the
domain; the half that does the checking did not, because it needs to know what
the workspace offers and nothing in the domain represents that.

## Decisions

| # | Decision | Why |
|---|---|---|
| T1 | **The domain gains the concept, as a value object: `Offering`.** | These are already domain rules by this codebase's own test — no foreign type, kingfisher's vocabulary rather than deepagents'. They were exiled to infrastructure because the *data* is discovered there, not because the *decisions* belong there, and the architecture test spells out the remedy: "a domain rule that needs a value takes the value, as `sweep(workspace, keep)` always did". |
| T2 | **A value object, not a `Tool` entity.** | The tool *is* the callable. A domain entity that deliberately cannot hold it is a shadow of the real thing, and two objects for one concept across two layers is exactly what `scoping.py` was renamed to escape: "Two files with one name across two layers made every import a small act of guessing." `Offering` names what the domain actually knows — which names are on offer, on which axis, and where each is defined. |
| T3 | **It joins `domain/tool.py`, beside `Found`.** | That module already exists and already holds the domain's answer to "what is a tool here". `capabilities.py` keeps the axis-agnostic machinery — `narrowed`, `withheld`, `all_but`, `belongs_in` — which `Offering` imports rather than grows. The two-axis split is tool-specific; skills and subagents have one axis each, which is why this is not a generic type. |
| T4 | **Offered names and sources are stored; grants are derived.** | `_ToolSurface` stores both today, and the cost is visible in it: `unrestricted` exists only because a stored grant lost the information it was derived from — "Distinct from the grants being `ALL`: a workspace tool existing forces the probe, and then the grants are enumerated while the request still narrowed nothing." A derived value was stored, could no longer answer the question it came from, and needed a companion flag. Deriving removes both. |
| T5 | **One refusal, with the caller naming itself.** | `refuse_ungranted_models(wanted, granted=…, subject=…)` in `capabilities.py` already solves this shape. `Offering.refuse_unknown(builtin, tools, subject="this request")` and `subject=f"subagent {name!r}"` gives both messages from one implementation. |
| T6 | **A test that fails when a function has no caller outside tests.** | The recurring failure is not tool-specific. Something gets written, gets a test, and never acquires a caller — so the next person needing it either does not find it, or finds it and gets a wrong answer. That is how one rule became three. |
| T7 | **`ceiling` is a free function, not an `Offering` method.** | Caught while wiring it, and it is the obvious mistake: a delegate is narrowed by what the *request was granted*, not by what the workspace offers. The two differ exactly when a request narrowed something, which is the case a ceiling exists for — so `Offering.ceiling` would have handed a delegate back the tool its caller withheld. It also answers `ALL` where `permitted` answers `None`, because a delegate's selection is narrowed again downstream and a request's goes to a middleware. Two consumers, two conventions; folding them would make one of the two lie. |

## What `Offering` is

```python
@dataclass(frozen=True)
class Offering:
    builtin: tuple[str, ...] = ()
    workspace: tuple[str, ...] = ()
    sources: Mapping[str, str] = field(default_factory=dict)
```

Three things the caller currently threads separately through
`_refuse_unknown_tools`, `_permitted_tools`, `_tool_surface`, `tool_ceiling` and
`refuse_unknown_tools`. The methods are the rules that take them:

- `refuse_unknown(builtin, tools, *, subject)` — a name on the wrong axis, or on
  neither.
- `permitted(builtin, tools)` — the parent's allowlist, or `None` for no
  restriction at all.
- `ceiling(...)` — **not a method.** See T7.

It cannot be built at discovery time, and that is not an oversight: the built-in
names come from `registered_tools(probe)`, an assembled graph. It is built where
`_ToolSurface` is built now, from the `Found` pairs the repository already
returns.

Infrastructure keeps what infrastructure must: the tool objects, taken off the
probe graph, and `tool_name`'s three `getattr` calls.

## The dead surface

Measured on `178b8e1`, not inferred:

| | Production callers | Outcome |
|---|---|---|
| `Capabilities.unknown` | 0 | **deleted** |
| `run.new_session_id` | 0 | **deleted** — found by the guard, not by the survey |
| `LocalToolRepository.tools` | 0 | kept, see below |
| `LocalToolRepository.sources` | 0 | kept, see below |

`Capabilities.unknown` is the one that mattered. It was not merely unused — it
checked `builtin_tools` and `tools` against a single `tools` iterable, so fed the
union it could not spot a misplaced name and fed only workspace tools it would
reject every built-in. The domain's own copy of the rule, and a copy that could
not work.

`new_session_id` was not in the survey at all; the guard found it on its first
run. It returned `uuid4().hex[:12]` — the 48-bit id T2 deliberately replaced,
because 48 bits "is far too little for something that grants access". Dead code
that would have reinstated a reversed security decision if anyone had reached
for it.

The two repository views are **kept**, which reverses the plan. Deleting them
broke twenty tests, and reading those it is clear why: `names` and `sources` are
the natural way to ask a repository what it holds, and the tests that assert on
loading want exactly that. They are not dead weight; they are the right views
with no production caller *yet*, because production now asks an `Offering`
instead. Removing them would have traded twenty clear assertions for twenty
dict comprehensions.

`LocalToolRepository.names` was never a candidate: it is a member of
`AssetRepository`, and `layered` reads it. Unused at one call site is not unused.

## What the guard cannot see

It matches by name, so a name defined twice is covered by either use.
`LocalToolRepository.sources` is invisible to it because `Offering.sources`
exists and is read — two different attributes, one spelling. Making it precise
would mean resolving attributes to their types, which is a type checker's job
and not worth rebuilding here.

The consequence is that it under-reports rather than over-reports, which is the
right direction for a guard nobody wants to argue with. It found what the survey
missed anyway.

## Sequenced plans

Three commits, in this order, each of which works on its own.

| Step | Deliverable | Depends on |
|---|---|---|
| **1** | `Offering` in `domain/tool.py`; both refusals become one method; `ceiling` moves as a free function; `_ToolSurface` keeps only the objects and derives the grants. | — |
| **2** | Delete `Capabilities.unknown` and the two tests holding it up. | — |
| **3** | The no-caller guard, with its exemption list. Deletes `new_session_id`, which it finds. | 2 |

They are commits rather than separate pull requests. Stacking a PR on an unmerged
branch has failed twice in this repository — once GitHub closed the stacked PR
when its base was deleted on merge, once worse — so work that genuinely depends
on unmerged work goes in one PR or waits.

Step 1 changes the wording of the request-side messages, because one rule
produces one message shape. The misplaced-axis message for a request turned out
to be asserted by *nothing*, which is its own small finding: it changed and no
test noticed until one was written for it.

## Skills and subagents, after the fact

This document said skills and subagents had "no equivalent duplication", and
that was asserted rather than measured. Measured afterwards, they had four
copies between them:

| Kind | Request side | Definition side |
|---|---|---|
| skills | `agent` ~line 718 | `delegation.subagent_skills` |
| subagents | `agent._defined_and_activated` | `delegation.subagent_helpers` |

The subagent pair was the same three lines outright, expansion of `ALL`
included, differing only in how the subject was spelled. Tools made a fifth
copy inside `Offering.refuse_unknown`.

| # | Decision | Why |
|---|---|---|
| T8 | **One `refuse_unoffered` in `capabilities.py`, beside `refuse_ungranted_models`.** | Same shape as its neighbour — names, what is offered, and a subject — which is what made five call sites collapse into it without merging anything that differs. Each caller keeps its own resolution and narrowing; only the refusal is shared, because `permitted` and `ceiling` already taught what happens when two similar things are folded on the strength of looking alike. |
| T9 | **The message says `offered:`, claiming no owner.** | It read "this request offers" for tools, where the *workspace* offers them, and "this request names … this request offers" repeats itself. Who owns the set genuinely differs by kind — a workspace offers tools and skills, a request offers the subagents it activated — so one message serving five callers cannot name an owner without being wrong for some of them. |

None of the four messages was asserted by any test, which is how they were free
to drift apart. That is the finding, more than the duplication: the duplication
is what happens when nothing holds the wording of a thing whose entire job is to
be read.

## Not in scope

- **Skills and subagents.** They have one axis each and no equivalent
  duplication. `domain/skill.py` and `domain/subagent.py` are where the same
  treatment would go if it is ever wanted.
- **Anything about loading.** Settled by the repository refactor.
- **`scoping`, `runtime`, `runlog`.** Enforcement and observation, correctly in
  infrastructure, and untouched.
