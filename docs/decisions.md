# Decisions

Why the code is shaped the way it is, so that a question already settled does
not get re-argued from scratch.

Each entry is a decision, not a document. The design documents these came from
were removed when this file was written -- twenty-five of them, about 90,000
tokens, which agents were paying to grep through. **They are not lost:** every
entry names the file it came from, and `git log --diff-filter=D -- docs/design/`
finds the commit that removed them, with the full argument intact.

Read this before proposing a change to something listed here. Several of these
were proposed, built, and then reversed -- those are marked, and the reversal is
the most useful thing on the page.

---

## Packaging: where the definitions live

This reversed twice. The current answer is the third one.

**Definitions live outside the wheel, and where a deployment gets them is a
setting.** `KINGFISHER_ASSETS` names one path, read in `application/config.py`;
`seed(cfg, source)` takes it as a required argument with no `None` branch. This
repository's own set is `examples/`, which is *not* shipped and exists to be read
and copied. `assets/` is committed holding only a `README.md` and ignores
everything else, because it is where a deployment puts content it did not write.
*(2026-08-19, `examples-are-ours-assets-are-yours.md`.)*

**Reversed: definitions as separate pip packages.** Assets were to leave the
repository entirely, become distributions of their own, and be found through
entry points that kingfisher named none of. Built in full, then taken back out.
What survived it: the framework still does not decide what a definition says, and
a fresh workspace still seeds itself on first run.
*(2026-08-17, `assets-as-packages.md`. Reversed 2026-08-18.)*

**Reversed: definitions shipped inside the wheel.** The reversal of the above went
one step too far -- `src/kingfisher/assets/`, shipped to everyone, excluded from
every architecture rule. Its D1 was itself reversed the next day by the entry at
the top of this section. Its D5 held: the tree went back to `src/`, `tests/` and
`service/` at the root.
*(2026-08-18, `the-definitions-ship-with-the-library.md`. D1 reversed 2026-08-19.)*

**Reversed: one `packages/` folder for three distributions.** Tidiness only, no
caller saw it, and it went with the packaging reversal above.
*(2026-08-17, `one-folder-for-the-packages.md`. Reversed 2026-08-18.)*

## The catalogue

**Tools and subagents nest; skills stay flat.** A folder is organisation and never
enters a name; a folder with `__init__.py` is one package and the scan stops
there. Skills stay one level because deepagents reads them off the filesystem and
that is the only shape it offers. Multi-source nesting for skills was measured and
rejected. *(2026-08-16, `nested-discovery.md`.)*

**The catalogue holds paths, not content, under three named roots.** A plain
mapping rather than an object, and library-only -- no environment variable.
Derived roots are created; roots the caller supplied must already exist. A
`CatalogueSource` protocol and its adapter were designed in full and cut before
building, on the grounds that one implementation is not a seam.
*(2026-08-16, `injectable-catalogue.md`.)*

**Two definitions of one name coexist, and the refusal moves to construction.**
The catalogue keeps both; an *agent* holding two of a name is what gets refused.
Refusing at load would stop a deployment over a clash no single agent would ever
see, unfixable by anyone who does not own both files. This was worked out three
times over -- skills got sources, tools got references, subagents came last and
failed hardest.
*(2026-08-17, `skills-from-several-parties.md`, `two-tools-called-fetch.md`,
`two-subagents-called-surveyor.md`.)*

**A reference is a selector, not a checked label.** `vendor_a/fetch.py::fetch`
resolves to one tool. The model never sees a reference -- it is given a flat
`fetch` -- because tool names go to the provider as identifiers and `::` is not
something to put in one. A bare name two folders offer is refused, naming both.
*(2026-08-17, `qualified-tool-references.md`, `two-tools-called-fetch.md`.)*

**A skill's identity is `source::name`**, a folder under the skills root is a
source, and deny rules are built from the path rather than the name.
*(2026-08-17, `skills-from-several-parties.md`.)*

**The skill registry is populated by deepagents' own lister**, which is a private
function with a test pinning it. Two readers of the catalogue used to disagree, so
a caller could activate a skill the agent was never told about. A directory the
agent will not load is reported, not refused.
*(2026-08-17, `skill-registry.md`.)*

## Agents and delegation

**The main agent is a definition.** It used to be assembled from four places that
did not know about each other. The agent file is the baseline and `Capabilities`
only ever narrows it; the prompt is appended to the harness prompt, never
replaces it; naming an agent is required; the agent is fixed when the session
starts and snapshotted into it. *(2026-08-18, `agents-as-definitions.md`.)*

**Omission means different things on different axes, deliberately.** The tool
fields inherit everything available; `skills`, `subagents` and `middleware` omit
to none. Tools are what an agent needs to *act*; the others are what it needs to
know and to ask. *(2026-08-18, `agents-as-definitions.md`.)*

**Delegation is unbounded in depth and is a DAG, not a tree.** A definition may
appear in several places, each is compiled once and its runnable shared, and
cycles are refused for the whole catalogue at load. Compiling per *path* is
exponential -- 15 definitions naming three each is 6,872 compilations and seven
seconds. *(2026-08-18, `subagents-all-the-way-down.md`.)*

**A subagent may be a compiled graph rather than a spec**, told apart by
extension. `SUBAGENTS` is declared and never inferred; name and description are
static text and only the graph comes from a function; the function receives the
model and tools rather than choosing them. An unrecognised extension in
`subagents/` is an error. *(2026-08-18, `compiled-subagents.md`.)*

**A definition may demand a distinct model** with `distinct: true`, and then an
indistinct one is refused rather than reported. `model` may take a list, tried in
order. A subagent naming no model runs its caller's.
*(2026-08-18, `compiled-subagents.md`, `agents-as-definitions.md`.)*

## Capabilities

**One refusal, with the caller naming itself.** Tool-name rules live in the domain
as a value object, `Offering`, beside `Found` -- not a `Tool` entity. Offered
names and sources are stored; grants are derived. There is a test that fails when
a function has no caller outside tests.
*(2026-08-17, `tool-rules-in-the-domain.md`.)*

**Capabilities narrow and never widen**, with one exception: an upload may widen
skills and subagents, because those are the caller's own text. A middleware name
is not -- it selects code the deployment wrote -- so it gets no such exemption.
*(2026-08-18, `agents-as-definitions.md`, and the middleware work of 2026-08-31.)*

## Models and endpoints

**Endpoints and models are separate concepts in one file.** `models.yaml` holds
both; a model names an endpoint. Model parameters live there and a definition
names a model and nothing more. An endpoint whose `key_env` is unset is dropped as
the catalogue loads, `Models` keeps what it dropped, and `doctor` reports it as a
warning rather than staying silent -- silence made a typo in `key_env` look
identical to a shared catalogue naming an endpoint this machine cannot reach.
*(2026-08-16, `model-catalogue.md`; 2026-08-18, `what-the-catalogue-dropped.md`.)*

**The shipped definitions name no models.** A vendor's model id is portable
nowhere, and a file shipped inside a wheel cannot name one.
*(2026-08-16, `model-catalogue.md`.)*

**`doctor` answers "why will this not start?" and nothing else.** It never makes a
model call: no probe, and it points at the caller's own task as the end-to-end
test. *(2026-08-18, `what-the-catalogue-dropped.md`; 2026-08-17,
`a-command-worth-shipping.md`.)*

## The command line

**`seed` and `list`, and the command is a consumer of the library, not an
insider.** Seeding and the inventory became public API to make that true. The
library answers "what does this workspace offer" with a record rather than a list
of names. Bare `kingfisher` prints help. Publishing was deferred, deliberately,
and asked twice. *(2026-08-17, `a-command-worth-shipping.md`.)*

## The HTTP service

**Transport only -- the server never interprets identity**, and lives in its own
wheel, installed by `kingfisher[service]`. `pip install kingfisher` does not put a
web service on disk. One request per turn, streamed, with no result persistence;
files arrive as ids resolved through a `FileStore` port; the turn stops on
disconnect and there is no cancel endpoint. An explicit error-to-status map, with
a test that it is total. *(2026-08-16, `http-surface.md`; 2026-08-17,
`the-service-as-its-own-package.md`.)*

**Session ids are issued, not accepted**, and the tenancy boundary is outside
kingfisher with one guard inside. *(2026-08-16, `session-scoped-api.md`.)*

**A skill's `allowed-tools` is prompt text, not enforcement.** Worth knowing
before trusting it for anything. *(2026-08-16, `session-scoped-api.md`.)*

## Tool failure

**A workspace tool's exception is a failed tool result, not a dead run.**
`WorkspaceToolErrors` converts it to a `ToolMessage` with `status="error"` and
the text carried whole, so the model sees a failure rather than a value. Built-in
tools are untouched -- they already report properly, and `HostPathGuard` covers
the one thing they do not, so widening this to them would put a second opinion
between deepagents and its own error handling. `BaseException` is deliberately
not caught: an interrupt is not a tool telling the model something.

Measured before it was built, on one deployment: the same wrong path cost nothing
through `read_file` and killed a sixteen-call run through `csv_profile`. Which of
the two happened depended on the tool the model reached for, which a deployment
cannot predict.

Built beyond what the design asked for. It specified the agent; delegates and
helpers below them get the guard too, because an agent declares its own roster
and `subagents` defaults to everything in it, so the common case became several
delegates holding the workspace's tools with the guard only on the parent.
*(2026-08-18 as `a-tool-failure-is-not-a-crash.md`; shipped, and the file removed
2026-08-31.)*

**A workspace tool is handed real paths, resolved against its own session.**
`WorkspaceToolPaths` rewrites a `path` argument before the tool sees it, so
`/data/config.ini` means the same thing to a workspace tool as it does to
`read_file`. A workspace tool is an ordinary function that opens files with the
operating system; the built-in file tools do not have the problem because they
close over the backend that roots them at a session, and nothing hands that
backend to a tool the caller supplied.

It closed a leak as well as a usability bug, and the second is how the first was
found. Before it, the only way for the model to make a workspace tool work was to
learn the host layout from the shell -- and from there it could name any session:
`line_count('/workspace/sessions/<other>/secret.txt')` returned an answer. Now
that argument resolves under *this* session and finds nothing, not by being
refused but by there being no way to say it. Symlinks are resolved on both sides,
because `execute` is rooted in a directory the agent can write to and a link at
`/derived/link.txt` pointing at another session was measured returning
`TENANT-A-PRIVATE` through a tool while `read_file` refused the same path.

Rewriting the call rather than wrapping each tool, because tools are not alike --
some are `BaseTool`s from `@tool`, some plain functions -- and the call is the one
shape they share. `PATH_ARGUMENTS` is `{"path"}`, the convention this repository
already enforces on shipped tools; a tool calling it `input_file` is missed, and
that fails visibly on the first call rather than silently, because a name that is
*not* translated cannot reach outside the session either.

**Still open from it.** Whether a repeatedly failing tool should be taken away
from the model rather than left to the recursion limit. Three failures of the
same tool with the same argument is a loop, and a middleware could say so;
nothing needs it yet, and a rule that removes a capability mid-run deserves its
own argument.

*Two other things this document listed as open were answered before it was
written, and were copied here on 2026-08-31 without being checked. The
routed/host mismatch is the entry above. A tool's exception does reach the run
report: `on_tool_error` writes a `tool_error` event even when the middleware has
already converted the exception into a tool result, which is now asserted rather
than assumed.*

## Layering

**`infrastructure/harness/` holds every module that imports deepagents, langchain
or langgraph**, and only that package may. The rule states the swap boundary:
replace the harness and exactly those files are rewritten. The other thirteen
modules stay flat -- a second subpackage would advertise a distinction no test
could hold. Registries and DTOs did *not* move to `application/`, and the package
root did not change. *(2026-08-17, `layer-boundaries.md`.)*

**Architecture rules are mutation-tested, not trusted.** All 44 were audited;
43 held and one had lost its subject. Three of them exist *because* a rule had
stopped working silently. *(2026-08-18, `mutating-the-architecture-rules.md`.)*

## Sessions and storage

**The session directory is the backend root**, `/data` is materialised once at
session creation, writes come back as a manifest, and processes are stateless
while the service is stateful. `sweep()` came off the request path.
*(2026-08-16, `session-scoped-api.md`, `durable-session-data.md`.)*

## Still proposed, not built

One document survives in `docs/design/`, because it describes work that has not
happened. It is a proposal, not history.

*A second one was there until 2026-08-31. `a-tool-failure-is-not-a-crash` had
shipped -- `WorkspaceToolErrors` and `tests/unit/test_workspace_tool_errors.py` --
and its status line had never been changed to say so. Its decisions are under
*Tool failure* above.*

- [**Nothing at rest on this machine**](design/2026-08-21-nothing-at-rest-on-this-machine.md)
  -- no session data on local disk, reached through a door kingfisher does not
  look behind. Several of its claims are still unmeasured, and it says so.
