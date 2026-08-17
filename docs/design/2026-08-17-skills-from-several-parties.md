# Two skills called `lookup`, from two people who never met

**Status:** planned.
**Date:** 2026-08-17

Skills arrive from different parties -- a vendor pack, a shared catalogue, a
team's own folder -- and nobody coordinates names. Two `lookup` skills is not a
mistake to refuse; it is what a catalogue assembled from several sources looks
like after long enough.

Today the second one silently replaces the first:

```
sources: research/, legal/  ->  which 'lookup' survived: From legal.
```

deepagents merges every source into a dictionary keyed by name, last source
wins, and the model is told about one skill where two exist.

## Why this is buildable for skills and was not for tools

A tool is *called* by name: the model emits a name, the agent looks it up in a
dictionary, and a dictionary holds one entry per key. Two can never coexist and
no syntax anywhere changes that -- which is why `csv_profile::csv_columns` is a
checked label and never a selector.

A skill is *read by path*. The listing hands the model a file to open:

```
- **lookup**: From legal.
  -> Read `/skills/legal/lookup/SKILL.md` for full instructions
```

So two could coexist. What prevents it is a merge deepagents performs before
formatting, and a grant vocabulary that has no way to say which. Both are ours
to change.

## Decisions

| # | Decision | Why |
|---|---|---|
| P1 | **A folder under the skills root is a source.** `skills/research/` becomes one, listed one level deep, as does the root itself. | This is the only mechanism deepagents offers for skills below the top level, and it needs no extra backend route -- the sources are paths inside the one `/skills/` mount. Measured: one root source finds nothing, three folder sources find nine. |
| P2 | **A skill's identity is `source::name`.** `research::lookup`. Flat names keep working where a name is unique. | The same spelling tools already use, because a second syntax for "where this came from" would be worse than a slightly stretched first one. Flat-when-unique keeps every catalogue that has no collisions exactly as it is, which is all of them today. |
| P3 | **A flat grant that is ambiguous is refused, naming both qualified forms.** | This is the whole safety property. Adding a colliding skill turns a working grant into a loud error rather than silently changing which skill a caller gets -- and silently changing it is the failure that made this worth building. |
| P4 | **The collapse is removed in `before_agent` *and* `abefore_agent`.** | They do not delegate: `all_skills: dict[str, SkillMetadata] = {}` appears in both bodies. Overriding one leaves the other collapsing, so a sync run and an `astream` run would offer different skills -- and it fails *open*, which is worse than the sandbox bug this codebase already has a scar from. A test drives the async path for real rather than asserting delegation. |
| P5 | **Deny rules are built from the path, not the name.** | `_skill_denials` writes `/skills/{name}/**`. With folders the real path is `/skills/research/lookup/`, so the rule denies a path that does not exist and an unactivated skill stays readable. A boundary failing open, found while planning this, and fixed by the registry already carrying `path`. |
| P6 | **Uploaded skills keep their own refusal.** An upload may still not take a catalogue name. | Sources are the point here, and a caller is not a party -- `uploads` refuses a name collision so a request cannot stand its own text in for a reviewed skill. That reasoning is unchanged by anything below, and relaxing it is a separate decision with a security argument attached. |

## What this does not fix

The model sees two entries whose bold labels differ only by a qualifier it did
not write, and chooses between them on description. That is better than being
shown one of two and told nothing, which is today, and it is worse than two
skills with distinct names. **Renaming remains the best answer available to
anyone who controls both files** -- this exists for when nobody does.

## Checked before planning

- `LocalShellBackend` has no `aexecute`; the protocol default is
  `await asyncio.to_thread(self.execute, ...)`, so it genuinely delegates and
  `ConfinedShell` overriding only `execute` still covers both paths. The pin in
  `test_confinement.py` passes, including a real `sandbox-exec` run. The skills
  middleware is the opposite case, which is why P4 exists.
- `skills_metadata` is written by `before_agent` and read in exactly one place,
  which feeds `_format_skills_list` -- a method `ScopedSkills` already
  overrides. Both ends of the collapse are already in reach.
