# One answer to "which skills does this agent have"

**Status:** implemented.
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

**Skills are read once per session, not per turn.** `before_agent` skips the
load when `skills_metadata` is already in state, and that state is
checkpointed:

```
no checkpointer     after turn 1: 2   turn 2: 4   turn 3: 6
with checkpointer   after turn 1: 2   turn 2: 2   turn 3: 2
```

The first row is what a probe without a checkpointer measures, and an earlier
draft of this document reported it as the real cost. Every real run has one, so
the load is two listings per *session* -- 8.2 ms each at 53 skills, once. There
is no per-turn cost to remove.

It has a consequence worth knowing that is not about cost: because that listing
is checkpointed, **a session started before a skill was added never sees it**.
Skills are fixed for the life of a conversation.

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
| S8 | **The middleware is not handed the registry.** Not deferred pending evidence -- there is no case for it. | It would mean overriding `before_agent` and owning what it writes into state and how it reports load errors, to save two listings *per session*. This codebase has a scar from that exact shape: overriding `execute` *and* `aexecute` on the shell backend nested the sandbox inside itself and thirteen tests still passed. Both copies come from the same loader over the same backend, so they agree, which is all the correctness needs. |

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

- **Nested skills, on top of this.** Decided and built — see
  `2026-08-17-skills-from-several-parties.md`. A folder under the root is
  registered as its own source, a skill's identity becomes `source::name`,
  and a bare name that two sources offer is refused rather than resolved.
- **What to do about `mismatch`.** A folder whose header names something else
  loads under the header name, so it is neither missing nor wrong -- it is
  *present under a name nobody typed*. S6 reports what the agent will not load;
  this one it will load, as something else. Worth a line in the same warning,
  and not designed here.
- **Uploaded skills.** Decided, and it was not a preference in the end -- see
  below. They join the registry, *and* get the check at provisioning time.


## What leaving uploads out actually cost

Not a missing feature. `available_skills` merged the session's directory
listing over the catalogue registry while `build_agent` resolved against the
catalogue registry alone, so an uploaded skill was advertised and then refused:

```
available_skills says :  ('code-review', 'mine', 'release-notes', 'tabular-qa')
activating it         :  REFUSED -- unknown skill: 'mine';
                         this workspace offers ('code-review', 'release-notes', 'tabular-qa')
```

Every upload, not a broken one. Two readers disagreeing about what a request
may activate is the exact failure S1-S5 removed, reintroduced by the fix for it
in the half it did not cover -- and nothing tested that half.

Both parts of the original bug were there. An upload with no `description` was
written, listed, accepted by the build, and absent from an agent that reported
nothing wrong.

**One registry answers for both halves now.** `activatable_skills` merges the
cached catalogue registry with a per-session read of the uploads, and both
`available_skills` and `build_agent` call it -- which is the property, rather
than the symptom, and is what a test pins.

**And `materialise_skills` refuses an upload deepagents will not load**, beside
the checks it already makes for a name collision and an escaping path. That is
about *when*: the registry catches it regardless, but the caller hears about it
against the ref they sent rather than later against a name they may not have
chosen. A test writes an unloadable skill straight to disk, past that check, and
asserts the registry still never offers it -- so the two cannot drift.
