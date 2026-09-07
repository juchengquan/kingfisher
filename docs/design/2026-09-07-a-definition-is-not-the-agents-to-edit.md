# A definition is not the agent's to edit

**Status:** proposed. Nothing here is built. It is one change to one tuple plus
the tests that hold it, and it is written down first because the thing it closes
has been half-recorded in `decisions.md` for weeks under a different heading.
**Date:** 2026-09-07
**Cited by symbol, not by line**, for the reason the neighbouring proposal gives:
a document that waits outlives its line numbers.

The agent's shell is confined. `sandbox-exec` on macOS, Landlock or bubblewrap
on Linux, and `writable_roots` decides where it may write: the whole workspace,
plus scratch. `resolve` carves exactly one directory back out --
`protected=(skills,)` -- and that is the only value that call site has ever
passed.

So `tools/` is writable by the confined shell. And a `.py` file that appears in
`tools/` is imported and executed, in this process, outside the confinement,
when the next request builds its catalogue.

## Measured, not reasoned about

Under the profile the deployment actually builds:

    write skills/ (protected)    rc=1  exists=False
    write tools/                 rc=0  exists=True

And a file that appears there is not merely read:

    before: []
    [module-level code ran at catalogue load]
    after writing a file, a fresh repository sees: ['sneak']

`LocalToolRepository` says so itself -- its modules are *"executed to be read"* --
and `_graph_for` builds a graph per request, which is what makes "the next
request" the whole of the delay.

## What this is, in one sentence

A route from a confined shell to unconfined execution in the host process, using
a directory the confinement deliberately includes.

## Half of it is already written down

`decisions.md`, under *Wiring a store*:

> `confinement.writable_roots` returns the whole workspace plus scratch, carving
> out only `skills/`, so `models.yaml`, `agents/`, `subagents/` and `tools/` are
> writable by the agent's shell. The rule is already stated at
> `confinement.resolve` -- *"host-side configuration, and a file the agent could
> edit is not a boundary"*.

That entry has the fact exactly right and draws a different conclusion from it:
that a *store* must be named by an environment variable rather than a workspace
file. True, and it is the same observation one step short. A tool file is not
configuration the agent could corrupt; it is Python the harness imports and runs.
The entry reasons about integrity and the same premise also carries execution.

It even names the neighbour: *"it is the middleware decision again, one object
further in."* This proposal is that sentence taken at face value.

## Nothing relies on the hole

Checked four ways before proposing to close it, because a capability somebody
uses is a different argument from a side effect nobody asked for.

**A caller cannot add tools.** Uploads layer two kinds -- `LayeredSkills` and
`LayeredSubagents`. There is no `LayeredTools`, and `Request` carries only
`skill_refs` and `subagent_refs`.

**The agent's file tools cannot reach the directory.** The backend is rooted at
`session_dir` with routes for `/data`, `/skills`, `/memory` and uploaded skills.
No route addresses a catalogue root, so `write_file` cannot name one. The shell
is the only path, and only by absolute path out of the run directory.

**The prompt never mentions it.** Nothing in `prompts/` tells an agent that
`tools/` exists.

**The guide addresses a person.** `guides/tools.md` opens *"A `.py` file in
`$KINGFISHER_WORKSPACE/tools/` defining `TOOLS`"* and is written throughout to
somebody authoring ahead of a run.

Every test that writes a tool file is a fixture placing one *before* the run,
which is what a deployment does. Nothing exercises an agent writing one.

So this is not a feature with a cost. It is the shell's writable root being the
whole workspace for ordinary work, and the definition directories happening to
sit inside it.

## The change, stated plainly

`protected` takes the definition roots as well as the skills directory:

    protected=(skills, *cfg.catalogue_roots.values())

One tuple, one call site, and the mechanism is the one `skills/` has been proving
works since it was written.

## Decisions

**All four roots, not just `tools/`.** `tools/` is the one that executes, and it
is not the only one that decides something. An agent that edits its own
`agents/*.yaml` can strike out the `groups:` line that says who may reach it, and
groups are read when the catalogue loads -- which is per request. A definition is
the thing that says what an agent may do; a definition the agent can rewrite says
whatever the agent likes. Protecting the executing one and leaving the
authorising ones would be closing the loud half.

**A relocated root needs no rule.** `KINGFISHER_TOOLS_DIR` and its three siblings
already point anywhere, and a root outside the workspace is outside
`writable_roots` and therefore already unreachable. The rule below is derived
from `catalogue_roots`, so a deployment that has moved them gets a `protected`
entry that is merely redundant rather than wrong.

**`models.yaml` and `groups.yaml` are a separate question, and smaller.** Both
are workspace files and both are writable today. They are read by
`config_from_env`, which runs once when `Kingfisher` is constructed -- so an edit
takes effect at the next restart rather than the next request, and the exposure
is a different shape. Worth protecting for its own reasons; not worth folding in
here, because "the agent cannot edit a definition" is one sentence and "the agent
cannot edit configuration" is another with its own argument about who writes
`groups.yaml` and when.

**`seed` is unaffected**, and this is the fact that makes the change cheap.
Seeding runs in the host process as a library call or a CLI command; the profile
wraps the agent's shell and nothing else. A deployment still writes definitions
into a workspace exactly as it does today.

**Read, not hidden.** `protected` denies writes and leaves reads alone, which is
what `skills/` already does and what this needs: an agent reading the tool it is
about to call is ordinary, and the file tools have no route there anyway.

## Considered and rejected

**Narrowing `writable_roots` instead.** The shell needs the workspace -- that is
where a turn's work happens. Enumerating what it may write rather than what it
may not inverts a rule that currently has one exception, and the exception list
would then be every directory a session uses, which grows.

**Refusing to load a tool file whose mtime moved.** Catching the write after the
fact, and it fails in both directions: a deployment editing a tool between turns
is legitimate, and an agent that writes one before the first turn has no
"before".

**Leaving it and documenting it.** The fact is already documented and drew the
wrong conclusion for weeks, which is the argument against documenting it again.

## The order to build in

**1. Protect the definition roots.** The tuple, plus a test per root that a write
is denied and a read is not, plus the negative control that `seed` still writes
into a protected root because it does not go through the profile.

**2. Assert the property rather than the mechanism.** A test that walks
`catalogue_roots` and asserts every one is denied, so a fifth kind added later is
covered by the rule rather than by somebody remembering. This is the test that
makes *Middleware as a definition kind* safe to write, and it is the reason that
proposal waits on this one.

### Guards to mutate

* Drop a root from `protected` -- the per-root test goes red.
* Add a fifth root to `catalogue_roots` and not to `protected` -- the walking
  test goes red, which is the whole point of having it.
* Protect the roots but also deny reads -- the read control goes red.

## What this does not do

It does not make the workspace safe to put a *guard* in. Protecting the roots
stops an agent editing what is already there; it says nothing about who may put
something there in the first place, and `seed` writes into those directories by
design. That distinction is the subject of the next document, and the reason
this one is not about middleware at all.
