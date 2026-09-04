# A store a deployment can name

**Status:** proposed, to be built on four branches off `main` -- none of them
written yet. It stays here until they land, because from `main` this is still an
argument rather than a description. When they merge, its decisions belong in
`docs/decisions.md` and this file goes, per the rule in `docs/README.md`.
**Date:** 2026-09-04

Kingfisher has twelve ports and two entry points. Between them, the entry points
can wire two of the twelve, and the only two settings that reach a store at all
can each name exactly one thing: a directory on this host.

That is not a gap in the ports. `SessionStore` is four methods over bytes and its
docstring is explicit that *"a local directory is a perfectly good implementation
of this port"* -- the constraint forbids kingfisher assuming a disk, not a
deployment choosing one. The gap is that a deployment cannot *say* it chose
something else without abandoning the ways kingfisher ships.

## What is wired today, counted

`Kingfisher.__init__` takes eleven collaborators besides `cfg`: `dirs`,
`threads`, `definitions`, `files`, `sessions`, `session_root`, `runner`,
`catalogue`, `grants`, `middleware`, `graph`. Its own comment says they are
spelled out rather than folded into a parameter object because *"folding them
into a parameter object would hide exactly what is substitutable"*.

Now count what the shipped surfaces pass.

* **The CLI**, `presentation/cli/__main__.py:415`: `Kingfisher(config_from_env())`.
  None of the eleven.
* **The service**, `kingfisher_service/app.py:100`:
  `Kingfisher(cfg, threads=threads, files=files)`. Two -- and `files` is
  `LocalFileStore(settings.file_store_dir)` or `None`, so the only one a
  deployment influences resolves to a directory on this host.

Two settings reach a store. `KINGFISHER_SESSION_STORE` is read at
`application/config.py:204` through `optional_path`;
`KINGFISHER_SERVICE_FILE_STORE_DIR` at `kingfisher_service/config.py:135`, also
as a `Path`. Both take a directory because both are declared as one.

The branch that would use anything else already exists. `application/service.py:265`:

    self.sessions_store: SessionStore | None = sessions or (
        LocalSessionStore(self.cfg.session_store) if self.cfg.session_store else None
    )

with the comment *"Injected, or derived from configuration, or nothing -- the
same order `catalogue` follows and for the same reason: derive from `cfg`, never
invent."* The shape is right and the vocabulary is too narrow: configuration can
only derive a local store, so `or` is the only door left and nothing walks
through it from a shipped entry point.

The assumption is written down, at `config.py:476-478`:

> A directory, because that is what a deployment can name in an environment
> variable. Somewhere else entirely is what `SessionStore` is for, and a
> deployment reaching for that passes an object rather than a path.

**That sentence is what this proposal reverses.** Its first clause is false -- an
environment variable can name a factory, which is how `models.yaml` already
reaches a chat class -- and its second describes a door that only opens for
somebody who has stopped using `kingfisher run` and `kingfisher-service`.

## Why "pass an object" is not the answer

It was the answer for one afternoon of designing this, and it does not hold up.

Passing an object fixes **one construction site**. There are two, and a
deployment controls neither: the server's is inside `kingfisher_service`, the
CLI's is inside `kingfisher`. A deployment can *replace* the server's, because
uvicorn resolves whatever module it is pointed at, and this is the intended
route -- `kingfisher_service/__init__.py` says the package split exists to put
the service *"on the same footing as anyone outside the package"*. There is no
equivalent for `kingfisher run`. The CLI builds its own instance and there is
nowhere to point it.

Even for the server it is awkward. `create_app` takes an already-built
`Kingfisher`, but a real one needs the async checkpointer, which is a context
manager that must be held open for the life of the process:

    async with async_checkpointer(cfg) as threads:
        service = Kingfisher(cfg, threads=threads, sessions=...)

That cannot run at import, and import is where the app is built (`asgi.py`).
`create_app` owns the FastAPI object and therefore its startup hook, so a
deployment is left overriding FastAPI internals or reassembling every router and
middleware by hand. The tests do not hit this because they pass a stub saver
that needs no cleanup (`service/tests/test_server_entry_point.py:24`).

A setting is read **inside `Kingfisher.__init__`**. One resolution point, and
every construction site inherits it -- CLI, service, tests, and a deployment's
own script -- with no new parameter anywhere and no change to `create_app`.

## The change, stated plainly

Two settings, each naming something kingfisher calls with no arguments to get a
store:

    KINGFISHER_SESSION_STORE_FACTORY=mycompany.stores:build_sessions
    KINGFISHER_SERVICE_FILE_STORE_FACTORY=mycompany.stores:build_files

Resolved where the directory form is resolved today, ahead of it in the same
expression. The factory reads its own bucket, region and credentials however it
likes; kingfisher never learns what a store needs.

`"module:name"` is not a new idiom here. `Adapter.chat_class` is exactly that
string, resolved by `getattr(import_module(module_name), class_name)` at
`infrastructure/harness/models.py:88`, and for a related reason: holding the
class meant importing a provider SDK to describe a wire format nobody had asked
for yet.

And two conformance kits, so that an implementation can be checked rather than
merely written.

## Decisions

**The backend stays kingfisher's.** `build_backend` keeps naming
`FilesystemBackend` and `ConfinedLocalShellBackend` itself. The object is not
"where files live": it wraps every shell command in `sandbox-exec` or Landlock,
it is what refuses a host path (`HostPathGuard` only turns that refusal into a
readable message), and its route table is what makes `DATA_IS_READ_ONLY` legal
at all -- deepagents' `FilesystemMiddleware` refuses `permissions=` outright on a
backend that executes, unless every rule is route-scoped. A deployment-supplied
backend takes all three on while `prompts/system.md` still promises them.

**Environment variables, never a workspace file.** Verified rather than assumed:
`confinement.writable_roots` returns the whole workspace plus the scratch
directory, and the only carve-out is `protected=(skills,)`. So `models.yaml`,
`groups.yaml`, `agents/`, `subagents/` and `tools/` are all writable by the
agent's shell. The rule is already stated one function up, at
`confinement.py:519`: *"it is host-side configuration, and a file the agent could
edit is not a boundary."* The agent cannot reach environment variables -- its
shell gets an allowlist of five, plus the skills directory when there is one (`backend.shell_env`) and cannot set any on the
parent process.

**A zero-argument factory, not a class and not an instance.** A class would have
to be constructed, and kingfisher does not know whether a store wants a bucket, a
DSN or a mount point; inventing a URL grammar for stores it knows nothing about
is the version of this that ages worst. A ready-made instance moves construction
to import time, which turns "this deployment cannot reach its bucket" into an
`ImportError` from a module nobody was reading. A callable taking no arguments
says the least and permits the most: a function, or a class with a no-argument
`__init__`, and the deployment's own configuration stays the deployment's.

**Precedence: constructor argument, then factory setting, then directory
setting, then `None`.** The order `service.py:265` already documents, with one
rung added. A supplied object wins because whoever passed it knows more than the
environment does.

**Both settings set is refused at startup, not resolved by precedence.** Two
answers to one question is what this repository refuses everywhere else, and a
deployment that has set both has a mistake worth being told about rather than a
preference worth honouring.

**`SessionStore` and `FileStore` gain `@runtime_checkable`.** They are, with
`CommandRunner` and `SessionRoot`, the only four ports without it. A factory
returning the wrong shape should fail where it was wired, naming the setting,
rather than at the first turn that touches storage.

**Scope is exactly the ports with a kit.** A setting inviting somebody to write
an implementation, without a way for them to check it, is worse than no setting:
the parts easiest to get wrong are the ones that matter -- `LocalSessionStore`
refuses a session id that climbs out of its root, and nothing about the port's
signature says so.

**The kit imports no test framework.** Each contract is a list of named plain
functions taking a factory and raising on failure, so a deployment parametrises
its own runner over them:

    @pytest.mark.parametrize("check", SESSION_STORE_CONTRACT, ids=lambda c: c.__name__)
    def test_my_s3_store(check):
        check(lambda: S3SessionStore(bucket="..."))

Kingfisher gains no test-framework dependency, the deployment keeps per-check
granularity in its own report, and unittest works as well as pytest.

**`FileStore`'s setting stays on `ServiceConfig`, `SessionStore`'s on `Config`.**
An asymmetry, and deliberate: `kingfisher run` takes `--input` and `--data` as
host paths and never builds a `FileStore` at all, because a `FileStore` exists
for callers who have no host paths to give. The setting belongs where the port
is used. This is the one decision here most likely to be wrong, and the guide
says why it is this way so that reversing it is a decision rather than a
discovery.

**Nothing changes for the CLI.** It gets a settable store because the resolution
moved, not because anything was added to it.

## Considered and rejected

**A backend factory on `Kingfisher`, mirroring `runner`.** Rejected for now, and
the shape is recorded because the argument will come back. It cannot be a single
object -- `service.py:500` explains that a graph is built per request *"because
its backend is anchored to the session -- two sessions cannot share a graph
without sharing a filesystem root"* -- so it would be `Callable[[Path],
BackendProtocol]`. What stops it is not the signature: today there is no second
implementation to derive the interface from, and `docs/decisions.md:956`
already warns that *"an interface derived from a single implementation comes out
shaped like that implementation."* This repository has cut one on exactly those
grounds before: a `CatalogueSource` protocol and its adapter were *"designed in
full and cut before building, on the grounds that one implementation is not a
seam"* (`docs/decisions.md:104`). If it is ever opened, **routes only, never the
default slot**, so the confinement and the host-path refusal stay kingfisher's.

**That same rule is the strongest argument against this proposal, and it does not
land.** `LocalSessionStore` is also the only implementation of its port, so a
reviewer reaching for *one implementation is not a seam* has a case to answer
here. The difference is what is being added. `CatalogueSource` proposed a *new*
abstraction ahead of a second implementation; nothing here proposes one.
`SessionStore` is already a port, already documented as swappable, and
`service.py:265` already branches on injected-against-derived. What this changes
is the vocabulary of one setting, so that the branch which exists can reach the
port that exists. A seam nobody can address from a shipped entry point is the
thing being fixed, not a seam being invented.

**A store or backend as a workspace asset.** The original framing of the
question, and the one thing here that is genuinely closed. It is the middleware
decision again: `assets_examples/middleware/call_cap.py` records that middleware
is deliberately not a `DEFINITION_KIND` because *"a middleware read out of the
workspace would be code the agent can edit, wrapped around the agent that edited
it"*, and `domain/capabilities.py:202` puts the line as *"a middleware name is a
selector for code the deployment wrote."* A store is the same object with more
reach. The measurement above -- the whole workspace writable, only `skills/`
protected -- is why.

Worth separating, because conflating them is how this proposal nearly rejected
its own answer: **that decision is about files the agent can write, not about
naming code from configuration.** An environment variable is not an asset.
Anyone who can set `KINGFISHER_SESSION_STORE_FACTORY` can already set
`PYTHONPATH`, so the class-path form opens nothing that was closed.

**Kingfisher shipping an S3 store behind a closed table**, the way `ADAPTERS`
holds one row per wire format. Uniform and safe, and it puts kingfisher in the
business of owning every backing store anyone asks for, starting with a boto3
dependency. The models table earns its closedness because a wire format is a
fixed, small vocabulary that kingfisher must understand to construct a client.
Storage is neither fixed nor small, and kingfisher does not have to understand it
at all.

**A builder parameter on `create_app`.** Designed, and dropped in the same
conversation once the resolution point moved. With the store resolved inside
`Kingfisher.__init__`, `app.py:100` picks it up unchanged and the builder has
nothing left to do. Kept out on the repository's own grounds: two ways to wire
one thing is what it distrusts.

**Giving the CLI its own injection.** Unnecessary after this, and it was the
wrong question: the CLI had no injection because nothing had made settings reach
that far.

**Routing `/data` to a store backend, so object storage needs no mount.** This
is the shape the original question reached for and it breaks a promise
`prompts/system.md` makes to the model in a table: *"nothing in the workspace is
out of the shell's reach"*, with `/data/<name>` mapping to `data/<name>`. File
tools would work and `execute` would not, so the agent could read its inputs and
not run anything over them. `/skills` is the one route that escapes this, and
`skills/backend.py:26` records what it costs -- a store-backed catalogue is the
only shape whose skills cannot be executed. Object storage reaches a session as a
mount, or by being copied in and out, and both work today.

## The order to build in

Four slices, each green on its own, each a pull request off `main`. They do not
depend on each other and must not be stacked.

1. **`SessionStore` takes a factory.** `KINGFISHER_SESSION_STORE_FACTORY` on
   `Config`, resolved in `service.py:265` ahead of the directory form;
   `@runtime_checkable` on the port; both-set refused with a `ConfigError` naming
   both variables. Tests: a factory is called and its result used, a supplied
   object still wins, a factory returning the wrong shape fails at construction,
   both settings refuse. Mutation-test the refusal.

2. **The `SessionStore` conformance kit.** `kingfisher/testing.py`, exporting
   `SESSION_STORE_CONTRACT`. The twelve checks in
   `tests/unit/test_session_store.py` become framework-free functions taking a
   factory; that module parametrises over them against `LocalSessionStore` so the
   kit is proved by the implementation it was written from. The package root is
   its own area in `tests/unit/test_architecture.py`, so its import table needs
   an entry -- that is the rule working, not an obstacle.

3. **`FileStore` takes a factory, and gains a kit.**
   `KINGFISHER_SERVICE_FILE_STORE_FACTORY` on `ServiceConfig`, resolved in
   `app.py`; `@runtime_checkable` on the port; `FILE_STORE_CONTRACT` from the
   five store-level tests now in `tests/unit/test_file_references.py:88-118`.

4. **The guide, and the records.** `docs/guides/` gains a page on writing a
   store: the two settings, the factory convention, a worked S3 `SessionStore`,
   and how to run the kit against it. `docs/decisions.md` gains the decisions
   above. `docs/README.md`'s table gains the guide, and the sentence saying
   `docs/design/` is empty goes back to being true. This file is deleted.

## What this does not do

**A session still materialises on local disk.** The store is how a session
survives the machine, not how it is read during a turn: `restore_into` writes
what the store kept into the session directory and the turn works from there. A
session larger than the volume still fails, and the answer to that is a mount or
a bigger volume, not this.

**`/data` is not lazily backed by object storage.** See the rejection above.

**The backend is unchanged**, so the sandbox, the host-path refusal and the
read-only rules are exactly where they were.

**`kingfisher run --input` and `--data` still take host paths.** The CLI gains a
settable session store and remains a tool for somebody standing on the machine.

**Nothing declares what a store's *performance* contract is.** The kit checks
behaviour -- what comes back, what is refused, what one session cannot see of
another -- and says nothing about a `fetch` that takes four seconds. A store on
the far side of a network is a latency this codebase has never measured.
