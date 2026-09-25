# Several skills roots

**Status:** proposed, nothing built.
**Date:** 2026-09-25.
**Occasion:** whether `SKILLS_ROUTE` could be backed by more than one directory. It
cannot today: `/skills/` is one `FilesystemBackend` over `Config.skills_dir`, the
sandbox is handed that one path, and the shell is told one `$KINGFISHER_SKILLS`. This
is what it would take to mount several, and when it is worth taking.
**Cited by symbol, not by line**, for the reason the other documents here give.

## What is already true

`decisions.md` settled the case this grows out of, under *The catalogue*: when several
parties had to ship skills into one catalogue, the answer was one level of grouping. A
folder directly under the skills root is a source of its own, labelled with its name,
and a skill's identity is `source::name`. `registry.sources` finds those folders,
`skills_sources` turns each into a `(route, label)` pair, and `_skill_denials` builds
each deny rule from the skill's own path.

That answer assumes every party can write into one directory. Where they cannot --
each party's skills are a separate volume, a separate checkout, or a directory the
catalogue's owner may not write into -- a group folder has to be copied into place, and
the copy is the thing that goes stale.

One precedent already mounts a directory under `/skills/` from somewhere else: a
subagent bundle's skills, at `bundled_skills_route(where)`, under
`BUNDLED_SKILLS_ROUTE`. That route sits beneath `/skills/` so the two things that make
the catalogue read-only, the deny rule and the sandbox profile, reach it without being
told. This proposal reuses that shape.

## The change, stated plainly

**An extra root is mounted at `/skills/<label>/` and is, to everything downstream of
the mount, one more group folder.** The index, `source::name`, the deny rules, and
`NarrowedSkills` already understand a group folder, so none of them learns anything.
What changes is where the folder physically is: the backend routes, the sandbox's
grants, and the shell's view of the catalogue.

A deployment writes:

```
KINGFISHER_SKILLS_DIR=/srv/catalogue/skills
KINGFISHER_SKILLS_MOUNTS=vendor=/opt/vendor/skills,team=/srv/team-skills
```

and the agent sees one tree:

```
/skills/                    → the primary root         label "catalogue", and its folders
/skills/incident/           → <primary>/incident/      label "incident", as today
/skills/vendor/             → /opt/vendor/skills       label "vendor", a mount
/skills/team/               → /srv/team-skills         label "team", a mount
/skills/subagents/<where>/  → a bundle's skills        unchanged
```

A grant is written as it is today: `skills: [vendor::lookup]`, or `lookup` alone where
one source offers it.

## Decisions

### 1. A mount is a source, not a second catalogue

The alternative is a list of peer roots, each with its own `catalogue` label and its
own group folders. That doubles every question the registry answers -- which root is a
bare `catalogue::x` in, which root's `incident/` is meant -- and every answer is a new
rule nobody has written yet. A mount as one labelled source needs none: the label is
the whole address.

It follows that **a mount is flat**. deepagents lists a source one level deep, so a
skill must sit directly in the mount's root, and anything deeper is reported the way
`LocalSkillRepository.misplaced` reports it now -- with a depth of two for a mount, not
`DEEPEST`'s three.

### 2. The registry re-prefixes every path it reads from a mount

`registry.read` lists a mount through its own `FilesystemBackend`, so deepagents
reports `/lookup/SKILL.md`. It has to be stored as `/vendor/lookup/SKILL.md` before it
reaches `Listed.path`. `_denied_path` builds the rule from that path, and without the
prefix the rule for an unactivated vendor skill would deny `/skills/lookup/**` -- a
path that does not exist -- and leave the skill readable. That is the exact failure
`_skill_denials`' docstring records from when folders arrived: a boundary failing open,
silently. The mount labels join `SkillRegistry.folders`, so `skills_sources` emits
their sources unchanged.

### 3. Routes are generated beside the bundle routes

One `FilesystemBackend(root_dir=<mount>)` per mount, at `/skills/<label>/`, declared
in `kingfisher.layout` as a `family=True` route the way `BUNDLED_SKILLS_ROUTE` is. The
file tools' read-only rule, `deny_write_under=/skills/**`, covers it by position. The
composite backend's longest-prefix match is what the bundle routes already depend on.

### 4. What is refused at startup

Each refusal names the label and both paths:

- `catalogue`, which is the primary root's own label, and `subagents`, which is
  `RESERVED_SKILL_FOLDER`.
- A label the primary root already has as a folder or a skill. The route would shadow
  it, and the shadowed skills would vanish with no message -- the failure
  `skills_sources` already refuses for `subagents/`.
- A label carrying `SEPARATOR` or `/`, since it becomes half of an identity and a path
  segment.
- A mount inside the primary root or inside another mount, which lists the same skills
  twice under two labels.
- A mount that does not exist. It is a root the deployment supplied, and `decisions.md`
  says supplied roots must already exist.

### 5. The sandbox grants each mount, both ways

Each mount joins `readable_roots` and `protected_roots`, and `_fence_for`'s
`readable`. All three take one `skills: Path` today and become a tuple. The protected
half is the one that matters: a mount missing from it is one the file tools refuse to
write and the shell writes freely, into a directory another party owns.

### 6. The shell sees one tree, through a view

This is the hard part. A skill's file names its scripts by host path --
`"$KINGFISHER_SKILLS/incident/timeline/scripts/timeline.py"` -- and
`capability_skills.md` teaches one rule: `/skills/X` is `$KINGFISHER_SKILLS/X`. A mount
has no host path under the primary root, so the rule is false for it.

**When mounts are configured, `$KINGFISHER_SKILLS` names a view, not the primary
root.** A directory kingfisher owns, outside every session, holding one symlink per
entry of the primary root and one per mount (`vendor → /opt/vendor/skills`). The rule
stays true, and a vendor's skill can write `$KINGFISHER_SKILLS/vendor/...` knowing
nothing but its own label. With no mounts nothing changes: `$KINGFISHER_SKILLS` is the
primary root, as `shell_env` sets it now.

Symlinks work under all three confinements only because decision 5 grants their
targets: Seatbelt, Landlock and bubblewrap all check the resolved path. The view itself
joins the readable and protected roots too.

The view is built where the catalogue is warmed, from the same walk. `Definitions`
caches `registry` per instance, so the view is exactly as fresh as the index the agent
is shown: a skill added to the primary root after startup is missing from both until
the catalogue is read again, rather than from one.

## Considered and rejected

- **One variable per mount** (`$KINGFISHER_SKILLS_VENDOR`). It breaks the single
  path rule the prompt teaches, so every mount costs the model a second rule, and a
  label has to become an environment name -- with its own clashes, `a-b` against
  `a_b`.
- **A list of peer catalogues.** Decision 1.
- **Symlinks placed in the primary root by the operator.** Nothing new to build, and
  it does not work: the shell is granted the primary root and not the target, so a
  script behind the link is refused -- and granting the target by hand leaves it out
  of `protected_roots`, which is the half decision 5 says matters.
- **Rooting `/skills/` at the view** instead of routing each mount. One backend
  instead of several, but the file tools would then depend on how `FilesystemBackend`
  treats a symlink leaving its root, which is upstream's to change. The routes keep
  the file tools on the precedent the bundles already hold.

## The order to build in

Each slice is one green commit and a pull request off `main`:

1. **Mounts are readable.**
   - `WorkspacePaths.skills_mounts: Mapping[str, Path]` and `KINGFISHER_SKILLS_MOUNTS`.
   - The registry reads each mount as a source, re-prefixed (decision 2).
   - The routes, the refusals, and the sandbox grants.
   - `kingfisher list` and `doctor` show each mount's skills under its label, and its
     unloadable and misplaced ones.
   - Seeding never writes into a mount.

   A mount's skill is indexed, grantable, readable by the file tools. Its scripts do not
   run yet, and `guides/configuration.md` says so.
2. **Mount scripts run.** The view and `$KINGFISHER_SKILLS` pointing at it. Driven
   through the real confinement, not a fake runner, because what this slice can get
   wrong is a grant, and a fake runner grants everything.
3. **The prose.** `guides/configuration.md` gains the variable, `guides/formats.md` the
   layout line `SKILL_LAYOUT` quotes, and `decisions.md` the entry. This document goes.

### Guards to mutate

Each should be broken once and seen to go red:

- the re-prefix: drop it, and `read_file` on an unactivated mount skill should
  succeed -- driven through the graph, not asserted on the rule;
- the protected grant: drop a mount from it, and `execute` writing into the mount
  should succeed;
- each startup refusal, the shadowing one first;
- the view: point `$KINGFISHER_SKILLS` back at the primary root, and a mount skill's
  script should fail to run.

## Open, and deliberately not decided yet

- **The variable's syntax.** `label=path` pairs separated by commas, as written above,
  cannot carry a path containing a comma. `docs/guides/configuration.md` has no list
  syntax yet to follow; decide it before slice 1.
- **Where the view lives.** Outside every session and never under a tenant's reach.
  Somewhere under the workspace is the obvious answer; which directory is slice 2's
  choice.
- **Whether this is worth building at all.** Copying each party's directory into a
  group folder at deploy time -- `rsync /opt/vendor/skills/ <catalogue>/vendor/` --
  needs no code, and everything above already works for it, scripts included. This
  proposal pays for itself only where that copy cannot happen: each party's directory
  is a volume that updates on its own schedule, or nobody may write into the shared
  catalogue. Confirm one of those is real before slice 1.

## What this does not do

- It does not change the primary root, its group folders, or anything a deployment
  without mounts sees.
- It does not let a mount group its skills in folders of its own.
- It does not change bundled skills, which stay a subagent's alone.
- It does not make any skills directory writable, by the tools or the shell.
