# One answer to "which skills does this agent have"

**Status:** planned, not implemented.
**Date:** 2026-08-17

Two things read the skills catalogue today and they do not agree. Kingfisher
lists directories holding a `SKILL.md`; deepagents opens each one and keeps the
ones it can parse. Where those differ, a caller activates a skill, the build
accepts it, and the agent is told about nothing.

Measured, on four directories that all contain a `SKILL.md`:

```
kingfisher advertises : ('Bad_Name', 'good', 'mismatch', 'nodesc')
deepagents loads      : ('Bad_Name', 'good', 'something-else')
advertised but absent : ('mismatch', 'nodesc')
```

`nodesc` has no `description`, so deepagents drops it. `mismatch` has a header
name that disagrees with its folder, so it loads under the *other* name. End to
end:

```
kingfisher says it offers: ('nodesc',)
build refused it?        : no — it built
ScopedSkills allowed     : ['nodesc']
```

The grant is accepted, the filter allows a name deepagents never listed, and the
agent gets zero skills. Nothing says so. That is the "quietly less than you asked
for" failure this codebase refuses everywhere else, sitting in the one kind it
never parses.

## The fix, in one sentence

Ask deepagents what the skills are, once, and keep the answer.

## What was measured before deciding

**The middleware cannot be handed metadata.** `SkillsMiddleware.__init__` takes
`(backend, sources, system_prompt)` — a backend and a list of paths. There is no
seam for pre-loaded `SkillMetadata`. It loads in `before_agent`, per run.

**Skills are re-read every turn**, once per source:

```
after build      : 0 skill listings
after one turn   : 2   ['/skills/', '/skills/uploaded/']
after two turns  : 4
```

**And that costs about 1% of a turn.** One listing of 53 skills on disk measures
**8.2 ms**; two sources is ~16 ms against a turn of 1.5-1.9 s. With the three
shipped presets it is under a millisecond. So the re-reading is real and is not
the reason to do this.

**The lister is private.** `SkillMetadata`, `SkillSource` and `SkillsMiddleware`
are public; `_list_skills` and `_list_skills_with_errors` are not.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | **The registry is populated by deepagents' own lister**, not by parsing `SKILL.md` here. | The bug *is* that kingfisher's idea of a skill differs from deepagents'. Any answer where kingfisher computes its own view leaves the door open; only asking the thing that decides closes it. Parsing here looks like the safe option and is the one that makes the problem worse -- it would put the name rules, the description limit, the size cap and every future upstream change in this repo, while still being a second opinion. |
| S2 | **It calls a private function, and a test pins it.** | The same coupling `WorkspaceScopedBackend` already takes on `_get_backend_and_key`, with the same mitigation stated there: "a deepagents upgrade that renames it fails the build rather than quietly removing the guard". A registry that silently went empty would reintroduce the exact failure it exists to remove, so the pin is not optional. |
| S3 | **The registry lives in infrastructure. No port changes.** | `SkillMetadata` is a deepagents `TypedDict`, and `domain/ports.py` may not name a type one layer out. Every consumer -- `available_skills`, `--list`, the build's validation -- is already in infrastructure or the driver, so nothing has to cross. |
| S4 | **`SkillRepository` keeps `names` and `files`, unchanged.** | Two different questions, and conflating them is what produced the bug. *What files exist to mount* is the repository's -- `skills_backend` needs them and a store-backed catalogue has no other source. *What skills the agent will have* is the registry's. |
| S5 | **Validation asks the registry.** `available_skills` stops returning directory names. | This is the half that closes the silent failure: `capabilities.skills=("nodesc",)` becomes an ordinary unknown-skill refusal, with the listing already built for it. |
| S6 | **A directory the agent will not load is reported, not refused** -- the same shape as `misplaced`. | Refusing would stop a deployment starting over one malformed skill, which is harsher than this codebase is about a definition it can simply not offer. The dangerous half is fixed by S5 regardless: loud where a caller named it, informative where nobody did. The cost, stated: a deployment can start with a broken skill and only find out from `--list`. |
| S7 | **The registry is read through the backend the catalogue already implies** -- a `FilesystemBackend` when the repository has a real root, the store mount otherwise. | `build_backend` already picks on exactly that basis, and `skills_backend` says why: a store "holds every skill's contents for the life of the deployment", so a directory already on disk should stay a filesystem read. Extracting that choice rather than restating it keeps one rule. |
| S8 | **The middleware is not handed the registry.** Deferred, deliberately. | It would mean overriding `before_agent` and owning what it writes into state and how it reports load errors, to save ~1% of a turn. This codebase has a scar from that exact shape: overriding `execute` *and* `aexecute` on the shell backend nested the sandbox inside itself and thirteen tests still passed. Both copies come from the same loader over the same backend, so they agree -- which is all the correctness needs. |

## What changes

| File | Change |
|---|---|
| `infrastructure/skill_registry.py` (new) | build a registry from a repository, via deepagents' lister |
| `infrastructure/catalogue.py` | `warm()` builds it; the catalogue carries it |
| `infrastructure/agent.py` | `available_skills` reads the registry |
| `main.py` | `--list` shows each skill's description, and warns about ones the agent will not load |
| `tests/test_skill_registry.py` (new) | the divergence, the refusal, the warning, and a pin on the private lister |

A catalogue whose skills all parse behaves exactly as it does now, except that
`--list` gains a description per skill -- which subagents already have and
skills, oddly, never did.

## Still undecided

- **Whether the middleware should eventually take the registry.** S8 defers it
  on evidence, not on principle. The evidence to gather is a catalogue large
  enough that 8 ms a listing matters, which is not three presets.
- **What to do about `mismatch`.** A folder whose header names something else
  loads under the header name, so it is neither missing nor wrong -- it is
  *present under a name nobody typed*. S6 reports what the agent will not load;
  this one it will load, as something else. Worth a line in the same warning,
  and not designed here.
- **Uploaded skills.** They arrive per request and are mounted at their own
  source. The registry as planned covers the catalogue; whether a request's own
  skills join it, or get the same check at provisioning time, follows the shape
  `uploads` already uses and has not been decided.
