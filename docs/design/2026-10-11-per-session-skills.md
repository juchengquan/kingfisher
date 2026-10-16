# Per-session skills

**Status:** proposed. The check it rests on lands with this document:
`a_session_serves_the_skills_kingfisher_read` in `SESSION_BACKENDS_CONTRACT`, which
holds what a session serves under `/skills/` to what kingfisher read. Per-session
skills themselves are not built, and wait for a deployment that needs them.
**Date:** 2026-10-11.
**Occasion:** adding a worked subagent typed as deepagents' `SubAgent` led to the
question of how a subagent's skills reach the composite backend, and from there to the
two readers of the skills catalogue. Kingfisher reads the catalogue itself, at
startup, and builds grants and the read-deny rules from that reading; the agent reads
skills through its session's backend. They agree on the default backends and nothing
compared them on any other. The owner then settled, question by question, what a
session bringing skills of its own would look like if the two are to stay one.
**Cited by symbol, not by line.**

## Already decided

Settled before this document, and not reopened by it.

- **A session is its backend.** Everything that belongs to a session is read and
  written through that session's backend, and nothing per-session is read from a
  directory on this host. See the section of that name in `decisions.md`.
- **A second copy of the backend's mapping drifts.** The tool-path work removed one
  for exactly that reason. See *A tool's path is the backend's path*.
- **Session reads are sequences, written once.** Each is a generator driven by
  `drive` or awaited by `adrive`, and `test_every_sequence_is_driven_or_delegated_to`
  refuses a read written any other way. A turn's setup is one of them,
  `_prepare_steps`.
- **What a session serves under `/skills/` is what kingfisher read.** Checked by the
  kit since this document's own change; see the entry in `decisions.md`.

## What this is for

One deployment serves several companies. Every company shares the deployment's skills
catalogue, and one of them, say Acme, also needs a few skills of its own that the
others must not see. Today the only places a skill can come from are deployment-wide:
the catalogue, a mount named by `KINGFISHER_SKILLS_MOUNTS`, and a subagent's own
skills. None of them can differ between sessions.

## The design

**The shared catalogue does not change.** It is read once per process, at startup,
checked there, and every agent definition's `skills:` line is checked against it.
A session's skills are added to it, never put in its place.

**Skills only.** Agents, subagents and tools stay one set per deployment. A tool or a
subagent declared in Python is code imported into the process, and code that differs
per company is a trust question of its own.

**A session's extra skills live in that session's own storage**, at
`/skills/session/<skill>/SKILL.md`, under the label `session`, so a grant names one as
`session::<name>`. One folder, and skills directly in it, as a mount is. `session`
becomes a name the catalogue may not use for a folder of its own, as
`RESERVED_SKILL_FOLDER` already makes `subagents`, and `refuse_unmountable` would
refuse it as a mount label, so a clash between labels can only happen at startup. Everything under `/skills/` is already write-denied to the
file tools, so the folder needs no rule of its own.

**`default_backends` serves no extras.** Nothing routes `/skills/session/` on the
default, so a single-company deployment is unchanged. A deployment that separates
companies opts in by having its own session backends serve that folder from wherever
it keeps a session.

**One read per turn, and everything from it.** A new step in `_prepare_steps` lists
`/skills/session/` through the backend the turn already opened, with the same lister
the startup reading uses, and hands the result to the build. That turn's grants, its
read-deny rules and the agent's skills index all come from one list: the startup
reading for the shared half, plus this turn's extras. The index is handed that list
rather than listing anything itself, and the index a conversation saved on its first
turn is ignored, so it cannot go stale.

**When an extra cannot be used.** One deepagents will not load is left out and
reported, as `decisions.md` already says of a directory the agent will not load. One
with the same name as a shared skill loses to the shared one and is reported. Letting
it win would let a company change what an agent the deployment defined does, and
keeping both would make a definition's bare grant ambiguous in that company's sessions
only.

**Reports.** A `skills_left_out` event in the turn's `RunEvents`, naming each skill
and why, for whoever manages that company's storage. `kingfisher list --session <id>`
shows one session's extras and what was left out, through `configured_backends`.
Plain `list` and `doctor` have no session, so they show the shared skills and one line
saying a session may add more.

**Who can use them.** An agent whose definition writes `skills: ["*"]`, or a request
naming them. Agent and subagent definitions cannot name one, because a name a
definition writes is checked at startup and a per-session name cannot be. Subagents
name their skills explicitly and are refused the star, so they never get extras.

**A pre-built graph skips the step.** `Kingfisher(cfg, graph=..., backends=...)`
hands kingfisher an agent assembled once, with its own skills set up. There is no
index for kingfisher to hand a list to, so the extras are not read. `ports.md` says so
where it covers pre-built graphs.

## Measured

- **The index is kept from a conversation's first turn**, measured on 2026-10-01 by
  driving two turns of one conversation with a skill added between them. The second
  turn's index did not list it, even when that turn's request granted it by name and
  the grant was accepted. `NarrowedSkills.before_agent` returns early when the state
  already holds `skills_metadata`, and the state is checkpointed.
- **Listing is cheap on this host:** about 2 ms for the five skills in
  `assets/skills`, the median of twenty reads through `kinds.skills.registry`.
  The per-turn step costs one listing even for a session with no extras, which on a
  backend kept elsewhere is a round trip.
- **The parent's skills list does not reach a subagent.** `skills_metadata` is private
  state, stripped before a subagent runs, so a subagent lists its own sources. Driven
  through `task` with the parent's skills both open and narrowed.

## Considered and not taken

- **An address book kingfisher keeps**, mapping each session to a directory on this
  host. One answer would have fed both the routes and the reading, but it is a second
  record of where a session's things are, and it cannot hold a company whose sessions
  are kept elsewhere.
- **A session's skills in place of the catalogue.** Every agent definition's grants
  would become uncheckable until a turn ran.
- **Several labelled folders under `/skills/session/`.** Useful to a company that
  takes skills from several suppliers, and addable later without changing what one
  folder means.
- **Freezing the index, grants and locks per conversation.** Consistent, but only by
  keeping a per-conversation copy of the list.
- **The index's shared half listed through the backend each turn.** A second reader of
  something read once per process by design.
- **Telling the model which skills were left out**, or returning it to the caller. The
  model cannot fix a broken skill, and the caller did not cause it.
- **A request uploading skills into the default backend's session folder.** A new way
  for a caller to write instructions the agent follows, which nothing needs yet.
- **For a pre-built graph, reading the extras anyway and logging that they were
  ignored**, or refusing such a deployment. The first adds a round trip and a log line
  to every turn to repeat the deployment's own choice; the second cannot be checked
  without reading every session's storage.

## Open, and deliberately not decided yet

- **The shell and skills.** A skill names its scripts under `$KINGFISHER_SKILLS`, a
  directory on this host. An extra's scripts are not under it, so they can be read and
  not run, which is already true of a subagent's own skills. A backend whose shell runs
  elsewhere has the same gap for every skill, and that is a question about how files
  reach a remote shell rather than about this list.

## Plan, if this moves

Sliced by behaviour, each slice wired and tested on its own.

1. **A session's extras reach its turn.** The reserved name, the step in
   `_prepare_steps`, the index handed its list with the saved one ignored, grants and
   read-deny rules from the same list, and the shared-name and unloadable rules.
2. **What was left out is said.** The `skills_left_out` event and
   `kingfisher list --session <id>`.
3. **Deployments told.** `ports.md` on serving `/skills/session/` and on pre-built
   graphs, and an upgrading note.
