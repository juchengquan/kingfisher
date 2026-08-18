# Subagents of subagents, with no cycles

**Status:** implemented.
**Date:** 2026-08-18

Delegation goes one level today: a request activates delegates, a delegate may
consult a helper, and the helper works alone. That bound is what makes a cycle
impossible — `reviewer` naming `second-opinion` naming `reviewer` needs a helper
with helpers, and there is no such thing.

This removes the bound and replaces it with the rule it was standing in for.

## What was measured before deciding

**The bound is ours, not deepagents'.** Three levels compile there without
complaint. What stops it here is structural: `_built` passes `helpers` when
building a delegate and omits it when building a helper, so the call that would
construct a third level is simply never made.

**"No cycles" does not bound the cost.** This is the finding that shaped the
design. Compiling a delegate per *path* through the graph explodes on catalogues
with no cycle anywhere — every edge below points forward:

```
definitions  each names   compiled    ~build
         15           3       6872      6.9 s
         20           3     144664    144.7 s
         25           4    8146016     hours
```

**`CompiledSubAgent` removes it rather than capping it.** deepagents accepts a
pre-compiled runnable in place of a spec, and the same runnable can be given to
several parents. A diamond — `top` naming `left` and `right`, both naming
`shared` — compiles four agents for four definitions rather than five for five
paths. Compile once per definition and the cost is linear in the catalogue,
about 1ms each, whatever shape it has.

**The tool ceiling already composes.** It only ever narrows, and a level asking
for everything gets back what survived above it:

```
request grants     ('a', 'b', 'c')
level 1 asks a,b   ('a', 'b')
level 2 asks b,c   ('b',)
level 3 asks a,b,c ('b',)     <- cannot widen back
```

## Decisions

| # | Decision | Why |
|---|---|---|
| N1 | **Depth is unbounded.** `refuse_helpers_with_helpers` goes. | With compile-once the cost is linear in *definitions*, so depth buys no expense. A limit would be a number nobody could justify. |
| N2 | **A definition may appear in several places — a DAG, not a tree.** | Forbidding reuse means copy-pasting a definition to use it twice, which this repository refuses elsewhere. The cycle check is the same walk either way. |
| N3 | **Each definition is compiled once and its runnable shared.** | Not an optimisation — the difference between linear and exponential, measured above. It also makes N2 free rather than expensive. |
| N4 | **Cycles are refused for the whole catalogue, at load.** | The rule it replaces was enforced there for a stated reason: a set of definitions is either coherent or it is not, whoever activates what. It also falls out of work already being done — compiling in dependency order needs a topological sort, and a cycle is what makes one impossible. And it fails at deployment rather than on the one request unlucky enough to activate both ends. |
| N5 | **Activation stays explicit.** A helper the request did not activate is dropped and reported, exactly as now. | The request's `subagents` list is the exhaustive answer to "what can run this turn", and cascading would make granting one name quietly enable fourteen — with nothing left for the withheld report to report. It also composes badly with why the drop exists: declining `second-opinion` would require knowing it sits three levels down. |

## The cost, stated

A long `--subagents` line for a deep tree, and a delegate quietly working alone
when one name is missed. That failure exists today; depth makes it likelier, and
the withheld report is what stands between it and silence.

A shorthand for naming a tree by its root — `--subagents top/**` — would fix the
typing without weakening the grant. Deliberately held until someone hits the
long line, because a grant syntax added before anyone needs it is one nobody can
judge.

## Settled while building

**`DeclaredDelegatesOnly` stays where it is, and that was measured.** It refuses
`task` to a delegate the request did not declare, and exists because deepagents
supplies an unrestricted `general-purpose` delegate "with the same capabilities
as the main agent". The question was whether a nested agent now gets one too --
it holds `task`, so it would be the obvious place. It does not:
`create_deep_agent` adds that delegate and `SubAgentMiddleware` does not. A test
pins it, because an upstream change here would hand back everything a request
withheld, one level down where nothing is watching.

**A definition is compiled once per position, not once per route.** Two
positions rather than one, and the reason is not an optimisation: a top-level
delegate inherits its model and built-in tools from `create_deep_agent`, while a
nested one is refused outright by deepagents without them. So a definition used
both ways compiles twice -- still linear, where per-route is exponential.

**The message names the whole loop**, `reviewer -> second-opinion -> reviewer`,
for the reason a tool collision names both files: one edge does not say which
link to cut, and whoever reads it may own none of them.

**The runtime depth is not bounded here and is not the same question.** Each
delegation is its own graph invocation with its own `recursion_limit`, so depth
in the catalogue does not consume a parent's budget. What it does consume is
tokens and wall clock, which the README already says.

## Found while building

**Testing `seen` before the path passed every cycle.** The first version of
`refuse_cycles` marked a node visited on arrival and skipped anything already
seen -- which is correct for a DAG walk and silently correct-looking here. A
self-loop, a two-cycle and a three-cycle all reported a clean catalogue. The
order is the whole check: a node reached twice is ordinary, a node reached while
still on the path being walked is the loop.
