# Decisions

Why the code is shaped the way it is, so that a question already settled does
not get re-argued from scratch. Each entry is a decision and the reason for it.
How the code came to that shape is `git log`, not this page. Where an entry names
a design document, `git log --diff-filter=D -- docs/design/` finds the commit that
removed it, with the full argument intact.

Read this before proposing a change to something listed here. Several of these
were proposed, built, and then reversed -- those are marked, and the reversal is
the most useful thing on the page.

---

Ordered outside-in: what a deployment writes, what a request may do with it, what
a run then meets, the surfaces it is reached through, and last the shape of the
code itself.

| | |
|---|---|
| **What a deployment authors** | [The definition format](#the-definition-format) · [The catalogue](#the-catalogue) · [Agents and delegation](#agents-and-delegation) · [Packaging](#packaging-where-the-definitions-live) |
| **What a request may do** | [Capabilities](#capabilities) · [Source-id access](#source-id-access) · [Models and endpoints](#models-and-endpoints) |
| **What a run meets** | [What a tool returns](#what-a-tool-returns) · [What a turn's tools returned](#what-a-turns-tools-returned) · [Tool failure](#tool-failure) · [Confining the shell](#confining-the-shell) · [Sessions: what persists](#sessions-what-persists-and-where) · [Wiring a store](#wiring-a-store) · [A session is its backend](#a-session-is-its-backend) · [A tool's path is the backend's path](#a-tools-path-is-the-backends-path) |
| **The surfaces** | [The command line](#the-command-line) · [What doctor promises](#what-doctor-promises) · [Where a deployment reads from](#where-a-deployment-reads-from) · [The HTTP service](#the-http-service) |
| **The codebase itself** | [The core and its plugins](#the-core-and-its-plugins) · [The architecture rules](#the-architecture-rules) · [How much a comment says](#how-much-a-comment-says) · [The size of the test suite](#the-size-of-the-test-suite) |
| | [Proposals, and what became of them](#proposals-and-what-became-of-them) |

*Sessions* and *Wiring a store* sit together and are not one section: the first is
what survives a turn, the second is how a deployment names a storage port.

---

## The definition format

**An entry is a name, or a mapping of `name` and what that field lets a name
carry.** `source_ids` for `skills`, `source` for `tools` and `skills`, `settings` for
`middlewares`; a `subagents:` entry is a name alone. One long form, read through one
loop, so a reader who has met one field has met them all. `tools` and `subagents`
entries no longer take `source_ids` -- *Source-id access* has why.

It replaced a field-level mapping keyed by name, because **that shape could not see a
name written twice.** YAML collapses `{a: X, a: Y}` before any reader runs, so a tool
named twice with two audiences silently lost one -- an access restriction that
disappears without a word, the failure this format exists to prevent. The list form
refuses a duplicate in the shared loop, for every field at once. The mapping also made
the common case pay for the rare one: adding one audience rewrote every entry in the
field. The old form is refused, naming the entry to write; so is `all_of`, since a
requirement is written as the set it is. *(2026-09-03.)*

## The catalogue

**Tools and subagents nest; skills stay flat.** A folder is organisation and never
enters a name; a folder with `__init__.py` is one package and the scan stops
there. Skills nest one level and no further, because deepagents lists a skills
source one level deep: a folder under the skills root is a source of its own, and a
skill below that is reported as misplaced. Nesting skills to any depth was measured
and rejected; one level of grouping was accepted the next day, when several parties
had to ship skills into one catalogue. *(2026-08-16, `nested-discovery.md`;
2026-08-17, `skills-from-several-parties.md`.)*

**The catalogue holds paths, not content, one root per kind.** `Definitions.from_roots`
takes a plain mapping rather than an object, and that mapping is library-only -- the
environment names one definitions folder. A `CatalogueSource` protocol and its adapter
were designed in full and cut before building: one implementation is not a seam.
*(2026-08-16, `injectable-catalogue.md`.)*

**Two definitions of one name coexist, and the refusal moves to construction.**
The catalogue keeps both, and what an agent holding two of a name gets depends on
the kind: two subagents are refused, and two tools leave it with neither, which the
run reports. Refusing at load would stop a deployment over a clash no single agent
would ever see, unfixable by anyone who does not own both files. Worked out three
times -- skills got sources, tools got references, subagents came last and failed
hardest. *(2026-08-17, `skills-from-several-parties.md`, `two-tools-called-fetch.md`,
`two-subagents-called-surveyor.md`.)*

**A reference is a selector, and a checked label too.** `vendor_a/fetch.py::fetch`
resolves to one tool, and where a name is unique the path is still checked: a
subagent naming a tool at a path it has moved from stops startup and a subagent's
build, while an agent naming one is reported by `doctor` and `kingfisher list` and
runs without the tool. The model never sees a reference -- it is given a flat
`fetch` -- because tool names go to the provider as identifiers and `::` is not
something to put in one. A bare name two folders offer is refused, naming both.
*(2026-08-17, `qualified-tool-references.md`, `two-tools-called-fetch.md`.)*

**One definition naming one dispatch name twice is refused, however it spells it.**
Compared verbatim, `shared.py::shared` beside a bundled `shared` went through and the
bundle silently won over the catalogue file the author named; `a.py::fetch` with
`b.py::fetch`, and `fetch` with `a.py::fetch`, slipped through the same way. Measured
on 2026-10-11, the plain pair as control. All three are refused, naming both spellings,
since "names 'fetch' twice" sends a reader hunting a second `fetch` they did not write.
It is refused at the reader, unlike *Two definitions of one name coexist*, because a
definition naming both owns the problem and can edit the line. **The comparison is
per-field**: a tool dispatches by its flat name, so `tools` compares bare names, while a
skill's identity *is* `source::name` and two parties may both ship a `lookup`, so
`skills` compares as written and leaves bare-against-qualified to the registry, where
ambiguity can be seen. A mutation applying the tools rule to skills reddens a control.
*(2026-10-11, from an architecture review.)*

**A skill's identity is `source::name`**, a folder under the skills root is a
source, and deny rules are built from the path rather than the name.
*(2026-08-17, `skills-from-several-parties.md`.)*

**`unloadable` compares a skill by its own path, not by its name.** By name, a `lookup`
that loaded from `research/` vouched for a broken `legal/lookup`: no `unloadable` line,
`doctor` green, and `legal::lookup` refused with nothing saying why -- in the very case
sources exist for. Measured on 2026-10-11, distinct names as control. The two sides are
spelled differently on purpose, since `Listed.path` already carries a mount's label and
prefixing both makes every mounted skill read as unloadable;
`test_a_broken_skill_in_a_mount_is_reported_under_its_label` asserts the whole tuple
because a membership test passed against that mistake. *(2026-10-11, from an
architecture review.)*

**The skill registry is populated by deepagents' own lister**, which is a private
function with a test pinning it. Two readers of the catalogue used to disagree, so
a caller could activate a skill the agent was never told about. A directory the
agent will not load is reported, not refused.
*(2026-08-17, `skill-registry.md`.)*

**Only `core.definitions.skills.registry` calls it.** An upgrade that moves deepagents'
private listers, sync or async, should mean one edit. The registry wraps both as
`list_skills` and `alist_skills`, which `core.agent.middlewares.narrowing` calls;
`test_only_the_registry_names_the_private_listers` fails if another module names
either, and both are pinned. A listing becomes a `Listed` where it arrives, so its keys
are read in one function; the skills index hands deepagents' own dictionaries back,
because its formatting reads them. *(2026-09-16.)*

**And the repository stopped answering `names`.** Listing directories one level down,
it missed both skills in a source folder and advertised one whose file does not parse.
Listing deeper fixes the first and never the second, because only deepagents' parse
decides what loads: any answer here is the disagreement, so there is none.
`LocalSkillRepository` keeps `root` and `misplaced`, facts about the directory;
`reachable` answers "is anything here?", and the registry answers "which names?".
*(2026-09-21.)*

**A skills directory can be mounted beside the catalogue, as one more folder of it.**
`KINGFISHER_SKILLS_MOUNTS` names `label=path` pairs, each routed at `/skills/<label>/`
and read as a source under its label, so the index, `source::name`, the deny rules and
narrowing learned nothing. It is for a party that cannot write into the catalogue, where
a deploy-time copy went stale; the copy still works for anyone who can make it. A mount
is one source, so it is flat. The label prefix on each registry path is what fails open
if dropped: a deny rule built without it names a path that does not exist. A label
carrying `::` or `/`, a missing mount and overlapping ones are refused before anything
is mounted. Not taken: several peer catalogues, which doubles every question the
registry answers. *(2026-09-25, `several-skills-roots.md`.)*

**With a mount, the shell reads the catalogue through a view.** The prompt teaches one
rule, `/skills/X` is `$KINGFISHER_SKILLS/X`, which a mount has no host path to keep. So
`skills_view` builds a directory of links in `.kingfisher/`, named for what it holds so
a changed catalogue gets a new view rather than a stale one, and renamed into place so a
turn never reads half of one. Old views are not pruned: telling which no running turn
uses costs more than the links. Not taken: a variable per mount, which breaks the one
rule; links an operator places in the catalogue, whose targets the shell is not granted;
and rooting `/skills/` at the view, which leaves the file tools to how upstream's
backend treats a link leaving its root. *(2026-09-25, `several-skills-roots.md`.)*

**Middleware is a definition kind.** `middlewares/*.py` declaring `MIDDLEWARES`,
read like `tools/`, seeded like everything else, and named from a definition in
the long form that already existed. It was the one kind a workspace could not
offer, on the argument that a middleware read out of the workspace would be code
the agent can edit wrapped around the agent that edited it -- true while the
shell could write into a definition root, and false the moment `protected` was
widened to every one of them. The premise moved, so this followed.

**The name comes from the class**, the way a tool's comes from the tool.
`AgentMiddleware.name` is a *property* answering the class's own name, so
`getattr(cls, "name")` is a `str` exactly when a subclass overrode it -- checked rather
than trusted, or the selector would stringify into `<property object at 0x...>`.

**Classes only in a file, where a registry may also hold a zero-argument
factory.** A file is imported once, so an object in `MIDDLEWARES` would be shared by
every graph in the process -- the state leak that makes a cap stop capping. A class does
everything a factory does and takes settings besides. The registry keeps the factory
because a factory can close over a live object, which a file cannot.

**Both sources are merged once, and a name in both is refused.** `build_agent`
resolves the registry before either branch reads it, so an agent and its subagents
select from one mapping. A clash is refused rather than resolved because both sources
belong to one deployment and renaming is available -- unlike a tool clash between two
vendors, which is why that one is qualified instead.

**A deferred `langchain` import**, because the module-level one made nine light
exports heavy at once -- `seed`, `inventory`, `Origins` and the rest pulling the
agent runtime for names that never reach it.

**The shipped pairing had never been built, and did not work.** The agent granted
`call-cap-strict` and `tool-note` while its subagent `sweeper` named
`call-cap-generous`, and an agent's `middlewares:` is the ceiling its subagents are
clamped by. Every test touching the pair had replaced the spec or supplied a registry.
`assistant` grants all three now and runs under both caps, the stricter deciding;
`test_the_middleware_pairing_builds_from_the_workspace_alone` builds it with no
registry. *(2026-09-07.)*

**The kind is `middlewares`, in the plural its four siblings were already in.** The
name was never free to differ: `seeding` walks `DEFINITION_KINDS`, so the folder spells
the field on `Definitions`, which `test_what_the_catalogue_loads_is_accounted_for`
holds equal to the axis on `Capabilities`. One name across five surfaces, so it was
taken as the breaking change it is -- the definition field, the request field,
`Kingfisher(middlewares=...)`, `MIDDLEWARES`, the folder. The folder is the quiet one:
a populated `middleware/` offers nothing, and nothing refuses an older layout.

**The layout names no kind.** `LAYOUT_DIRS` is what is always in the workspace --
`sessions` and `.kingfisher` -- and kind folders are made where the definitions are. A
folder where nothing reads misleads more than a missing one: it looks like the place to
put a definition, and one put there is never loaded. A folder an earlier start left is
kept, since deleting an operator's directory is not the layout's call. *(2026-09-24,
from an architecture review.)*

**The mass noun stays wherever it is still a mass noun.** `middlewares` names the
kind -- a directory, a field, a key, a table row. `LocalMiddlewareRepository`,
`approved_middleware`, `core/agent/kinds/declared_middleware.py` and every sentence
about *a middleware* keep the singular, as `LocalToolRepository` sits behind a kind
called `tools`. *(2026-09-15.)*

**A class may ask the harness for what only the harness knows.** `wants`, beside
`defaults` and `yaml_settable`, names kwargs filled in where the agent is assembled:
`model`, `backend`, `definition`. Not because "no asset can depend on a deployment
object" -- a registered class can always close over one, as `guides/middleware.md`
documents -- but because *this build's* answer is decided per agent and per subagent,
and a `middlewares/` file is imported before any agent exists, so there is no model yet
to capture.

**The set is open and lives at the two call sites.** No constant enumerates it.
Each site in `build_agent` builds a mapping from what it holds, `_instantiate` pulls
out the keys a class named, and a want nothing answers is refused with the site's own
listing. Both sites go through one builder;
`test_both_kinds_are_handed_the_same_things_to_want` catches a second mapping written
inline.

**A wanted key is a name in yaml and an object in Python.** A `model` a definition
writes is resolved -- catalogue lookup, endpoint check, instance -- in `model_named`,
which `model_object` calls too: two copies of those steps, one forgetting the endpoint
check, is a run sent where the caller refused. `backend` and `definition` have no name
a file could carry, so a value written for one is refused, on the class.

**A subagent is handed its own model, not its agent's.** Its model is not built until
`as_subagent`'s last line, so the one in scope where its middleware is made is the
agent's -- wrong in the direction that costs money, since a subagent pinned to the cheap
model would be compacted by the expensive one. `_with_helpers` passes the right one.

**The example is `compaction.py`, and it runs.** It is the motivating case: handed
`model: "gpt-5"` as a string, langchain's summarizer calls `init_chat_model`, which
infers a provider and reads credentials from the environment, around the catalogue and
the endpoint's `base_url`. Measured: an unset `trigger` never fires, and a backend
reports a failed write by returning one rather than raising. *(2026-09-15. Reversed
2026-10-01, next entry.)*

**Reversed: `compact` as a second summarizer.** Subclassing langchain's
`SummarizationMiddleware` ran it beside deepagents' summarizer, and the two keep
different books: deepagents' records its cutoff as an index into `state["messages"]`;
langchain's rewrites them to a summary and twenty messages. After a rewrite the index
pointed past the end, and on a scripted run the model saw the system prompt and an old
summary for twelve consecutive calls; `test_a_compacted_agent_still_sees_what_it_just_read`
holds it. `Compact` is now deepagents' summarizer in its place, with one clause added to
the trigger: sixty messages, whatever their size. The instance answers to deepagents'
name, so the replacement notice fires by design, and its settings match the summarizer
every other agent runs. Not taken: dropping `compact` from the shipped agents, which
breaks every workspace naming it. *(2026-10-01.)*

**The shipped `assistant` names its middleware, and does not write the star.**
`middlewares: ["*"]` was argued as safe because a star resolves to nothing where
nothing is registered. *Middleware is a definition kind* made that false: `seed` copies
the examples into `middlewares/`, so a seeded workspace wrapped the first agent a reader
runs in all of them, unannounced -- measured by building it; the star's test used an
empty registry, the one a seeded workspace never has. Naming all four puts what it runs
under in its file and prompt, and a middleware added later does not arrive unasked. The
price: a plain `kingfisher seed` leaves `assistant` behind, and `--all` brings it. Not
taken: seeding `middlewares/` only under `--all`, which undoes half of *Middleware is a
definition kind* for one line, and a star skipping workspace files, which makes one
registry's two sources mean different things. *(2026-09-16.)*

**The definition repositories are internal, and each declares what is read from
it.** `ports.md` listed four as ports to replace, though neither `Definitions` nor the
interfaces were exported. Reads through `getattr` with a default hid behaviour: a
repository without `documents` never pinned a session to its agent, so a later turn
asking for another agent was served, and one without `bundles` dropped a subagent's
carried tools. One implementation is not a seam, so every member a consumer reads is
declared on the reader, and `test_nothing_reads_a_repository_member_with_a_default`
holds it. *(2026-09-16.)*

**Removed with it: skills mounted from a store.** An in-memory store filled from a
repository's `files` let a skill catalogue not be a directory. Once the repositories
were internal, the only ones with no directory were test fakes, so the store and the
no-directory branches went; the shell is always told `$KINGFISHER_SKILLS`.
*(2026-09-17.)*

**One envelope for what a Python module declares.** `TOOLS`, `SUBAGENTS` and
`MIDDLEWARES` were each loaded with the same `getattr` and two refusals, and the copies
had drifted: middleware told an author the type it got and stopped, where the others
said what to write instead. No duplication rule finds that, because the three say it
about different nouns. `core.definitions.importing.Export` carries what differs, and
`exported_from` is the envelope. `test_refusals.py` counts refusals against the parsed
source, so it records that each lives in one place. *(2026-09-18.)*

**One walk for both definition roots.** The two walks differed by the error class and
one `continue`, around a byte-identical `.yml` refusal -- a copy that has not drifted
yet. `documents_in` takes the error class and a `skipping` set, and that set is the
asymmetry: `tools/` and `skills/` under `subagents/` hold a bundle's assets, while under
`agents/` they are ordinary folders. Passed at both sites, an agent under `agents/tools/`
is lost with nothing red; `test_a_folder_called_tools_is_organisation_here_too` holds
that side. *(2026-09-18.)*

**And the union it answers is discriminated once.** Both readers wrote the same five
lines around `decode`, and `fields_of` is those five. Dropping the file name from the
refusal left the suite green, because the table in `test_subagent.py` matched on the
reason only; it is driven for both kinds now. *(2026-09-24.)*

**Both readers take a `DefinitionText`, the text and the file it came from.** A path
alone cannot serve the agent catalogue, which pins the exact document it parsed into a
session: opening the file again for the pin lets an edit between the reads run a
session's first turn on one agent and later turns on another. *(2026-09-24.)*

**A subagent lists what it takes from its own folder, and only that arrives.** Each
`tools:` and `skills:` entry may say `source: shared` (the catalogue, what a plain name
means) or `source: bundled` (the subagent's own folder). When the folder granted by
itself, a file dropped in was granted with no line changing, a rename took everything
away with only a warning, and a descriptive `bundle:` key sat among fields that grant
and granted nothing. Listing as the grant removes all three.

**`bundled` names where the file is, not who may use it.** A label on a file in the
shared `tools/` would make privacy something any definition can put on or take off,
and two subagents claiming one tool would need a rule of their own.

**An unlisted file in a subagent's folder is refused, not ignored.** Every upgrading
deployment has such files, and ignoring them would lose each silently.
`core.definitions.subagents.rules.miscounted` compares list and folder both ways, `warm`
refuses, `doctor` fails on `bundled entries`, and the build filters to the list too.
A `skills/` folder beside a compiled subagent, which cannot list skills, is refused by
the same rule.

**Subagents only.** `source: bundled` in an agent file is refused: an agent has no
folder. A bundled entry takes no `source_ids` -- it reaches whoever reaches the
subagent -- and no `file::name` path. A subagent inheriting the catalogue with `"*"`
and listing its own `fetch` gets its own, and `list` marks the shadowing; a folder named
after no definition stays a warning. *(2026-09-23.)*

## Agents and delegation

**The main agent is a definition.** It used to be assembled from four places that
did not know about each other. The agent file is the baseline and `Capabilities`
only ever narrows it; the prompt is appended to the harness prompt, never
replaces it; naming an agent is required; the agent is fixed when the session
starts and snapshotted into it. *(2026-08-18, `agents-as-definitions.md`.)*

**Reversed: omission means different things on different axes.** Leaving out `tools:`
or `builtin_tools:` granted everything available -- the shell, and every tool file
added later -- while `skills`, `subagents` and `middlewares` left out granted none, on
the argument that tools are what an agent needs to act. Every grant field now reads
alike, in every format: unset is none, `["*"]` is all, `[]` an explicit none. **A
forgotten line fails closed**, where it used to grant everything silently; **it fits
source-id locks**, which a default handing every new tool to every agent works against;
and **one rule** leaves no asymmetry to explain. The `Capabilities` a caller passes still
read unset as no narrowing. Every shipped definition that left a line out gained
`["*"]`. A package typed to deepagents' `SubAgent` now holds no built-in until the
deployment names some, and a subagent's `tools` is always set, to `[]` when nothing is
granted: deepagents reads a missing key as the parent's tools, so a subagent granted
none was built holding the agent's, with only its allowlist filtering them -- driven,
the parent's tool seen arriving. `general-purpose` still gets the parent's.
*(2026-08-18, `agents-as-definitions.md`. Reversed 2026-10-18.)*

**A subagent names its skills; `["*"]` is refused there and taken on an agent.** A
subagent is handed a skills index only where its skills are *named*, so under the
ordinary request, which grants every skill, `skills: ["*"]` read as all of them and
arrived as none. Refused at the read, beside the `subagents` star: the habit comes from
a request, where the star is the ordinary way to say everything. The two tool axes still
take it.

**An agent grants the helpers its subagents consult, and the docs said otherwise
for months.** They promised an agent naming `reviewer` got whatever `reviewer` named,
and `kingfisher list` printed that chain, while the build narrowed a subagent's helpers
by the agent's own `subagents:` -- so naming only the parent built a subagent with no
`task`. Measured both ways. The prose moved to the code: `middlewares:` already makes an
agent's line its subagents' ceiling, and a `subagents:` granting what it did not name
would be the only field where naming one thing grants another, compiling a graph per
turn for a helper nobody asked for. The listing prints what an agent activates
(`core.access.reach.activated`). A *request* naming `reviewer` alone still gets it
without its helper, dropped rather than refused. *(2026-09-17.)*

**Delegation is unbounded in depth and is a DAG, not a tree.** A definition may
appear in several places, each is compiled once and its runnable shared, and a
cycle anywhere in the catalogue is refused, whether or not the agent being built
reaches it. Compiling per *path* is exponential -- 15 definitions naming three each
is 6,872 compilations and seven seconds. At startup `kingfisher list` reports a cycle,
and the first build of an agent with subagents refuses it. *(2026-08-18,
`subagents-all-the-way-down.md`.)*

A name the walk cannot place -- a helper nobody defines, a bare name two files share --
is no edge. Resolving the shared one to every file it could mean was not taken: it
refuses the whole catalogue over a loop that exists only if the choice goes one way, and
the build refuses the same name before following it. *(2026-10-12.)*

**A subagent name nothing answers to is reported, not refused at load.** It loads, the
run reaching it refuses it, and `kingfisher list` names who wrote it and exits 1.
Refusing at load, as a helper listed without `task` is, was not taken: that shape cannot
work in any catalogue, while this one is one file at odds with the rest, and refusing it
would stop every request over a definition none of them may use. *(2026-10-12.)*

**A subagent may be a compiled graph rather than a spec**, told apart by
extension. `SUBAGENTS` is declared and never inferred; name and description are
static text and only the graph comes from a function; the function receives the
model and tools rather than choosing them. A `.yml` file, in any case, is refused as the
`.yaml` it was meant to be; anything else is left alone, since a package there may hold
a fixture or a prompt. Refusing every unfamiliar suffix was reversed the day it was
written. *(2026-08-18, `compiled-subagents.md`.)*

**One stack for every graph that holds the workspace tools.** The agent, a subagent and
deepagents' `general-purpose` each composed their own middleware, and the last got none:
deepagents fills a spec naming no tools from the parent, so `general-purpose` held the
agent's tool objects with no host-path guard, no error guard and no path translation.
`standing_middleware` builds all three, so a fourth cannot be written half-right.

**A compiled subagent's tools are wrapped, because it is the fourth graph.** It has no
middleware -- deepagents runs it as given -- and the shipped `show-your-work`'s first
path-taking call died with `FileNotFoundError`, ending the run. `GuardedTool` carries
the translation and error conversion on the tool objects, reversing for that path only
the reasoning `GuardedCallsMiddleware` records, that the call is rewritten because
the tools are not alike. So the wrapper normalises: a plain function becomes the tool
`create_agent` would make, and a `BaseTool` keeps its `args_schema`, since a wrapper
advertising `**kwargs` tells the model the wrong arguments. A refusal is returned, not
raised, because nothing in a graph kingfisher did not build catches one -- measured for
`ToolException`, `ValueError` and `FileNotFoundError` -- which also keeps
`ToolMessage.status` true. *(2026-09-18.)*

**Middleware is standing, switched on by a setting, or offered, and what decides is
whether kingfisher promises it.** Standing is what every caller is guaranteed on every
graph -- the guards, the allowlists, the recorder, result clearing, the attachments
guard -- built by `standing_middleware` for the agent, a declared subagent and
`general-purpose`. Memory and the interpreter are switched by a setting; everything
else is offered, and a definition names it. The order is standing, switched, a
subagent's helpers, offered, so whatever a deployment chose sees a call only after
kingfisher has judged it and rewritten its paths. Moving memory and the interpreter
after every standing one left all 3,319 tests green, so their old place held nothing.
*(2026-10-17.)*

**A compiled subagent is outside the promise, and that is said rather than closed.** A
guarantee reaches it only on its tools, through `GuardedTool`, and at the `task`
boundary, where its answer is recorded. Closing it would mean wrapping a graph
kingfisher did not build. *(2026-10-17.)*

**`TodoListMiddleware` stays standing, for now.** `build_agent` reads which tools are
built in off the middleware its probe holds, so an offered checklist would leave the
probe and `builtin_tools: [write_todos]` would name a tool that is not there. Moving it
waits on the built-ins being read some other way. *(2026-10-17.)*

**Two pairs of standing middleware are one each now.** `GuardedCallsMiddleware` turned
both kinds of exception into a failed result, a workspace tool's first so it keeps its
type, from the outer place, so a `grep` that `BuiltinToolsMiddleware` runs as several
searches fails once. *(Its workspace-tool half is `WorkspaceToolFailureMiddleware` since
2026-10-19; see* What a deployment's own middleware raises *under* Tool failure.*)* `GrantedOnlyMiddleware` refuses both what a request was not granted
and an undeclared subagent, granting everything where nothing was narrowed.
`core.agent.middlewares.results.failed` builds every failed result kingfisher's own
hand back. Offered ones keep their names, because `kingfisher` and `kingfisher.plugins`
are the addresses a file in `assets/` may import. A helper to append a line to a result
was not built: `BuiltinToolsMiddleware` and `RecorderMiddleware` both append, but a
correction for the model and an id for a later search are written for different readers.
**And two more pairs.**
`ModelReplyMiddleware` handles a reply cut at the output cap and a call the provider
refused, reading a refusal on the retry too; `BuiltinToolsMiddleware` makes both changes
to deepagents' built-in tools. *(2026-10-17.)*

**A call from `eval` is a fifth way to dispatch, and it met none of the guards.** With
the sandbox wired, `eval` holds `tools.<name>()` for each granted tool and `task()`
wherever it may delegate, through langchain-quickjs's bridge rather than `ToolNode`, so
no `wrap_tool_call` and no approval gate sees them. Measured against the same calls made
directly: a tool returned another session's `inputs/` file and the host's `/etc/hosts`;
`/inputs/x` reached a tool untranslated; the shipped `csv_profile` on a missing file
ended a `Kingfisher.run` turn; and gated `write_file` and `task` ran with no pause. Only
the backend's host-path refusal and the allowlist the sandbox is built from held. So
**the granted workspace tools go in as `GuardedTool`s, built-ins as names** -- the
compiled-subagent exception again, for the same reason, and errors come back to the
program as text; **a gated tool is left out of `tools.*`, and a gate on `task` turns
`task()` off**, since gating `eval` itself would put a person in front of arithmetic;
and **a nested helper is handed no `eval`**, since the probe's sandbox is wired before
the grant is known and a helper whose allowlist refused `task` reached a subagent
through it, leaving a sandbox thread `release_interpreter` never reached. A top-level
subagent never had `eval`. `None` turns `tools.*` off: an unrestricted request has never
reached a tool from code. *(2026-10-11.)*

**A definition naming subagents has to hold the tool that calls them.** The shipped
`reviewer` named `profiler` without `task` in its `builtin_tools`, so its own allowlist
refused every consultation at the call, where the model answers without the helper, and
nothing said so at load. Measured on agents too: two shipped agents had never reached
their subagents. Both formats refuse at load, from one function, `subagents:` naming
anything while `builtin_tools` is a list without `task` or is left out. A *request*
narrowing `task` away is still narrowed at the call, because there the caller chose it.
*(2026-10-12.)*

**A definition naming skills has to hold the tool that opens them.** A skill is offered as
an index and read with `read_file`, as deepagents' own prompt says, so without it every
skill the model opens is a refused call. The shipped `redactor` named its bundled skill
while holding no `read_file`, deliberately, and carries its procedure in its prompt now.
Both formats and a portable Python entry refuse `skills:` naming anything, bundled entries
and `["*"]` included, while `builtin_tools` leaves `read_file` out, beside the `task` rule
and whatever the skills switch says. A *request* narrowing it away is told of no skills --
no index, no skills text in the prompt, none reported withheld -- and a subagent loses its
own as well, though those skip the switch: an index it cannot follow is only refusals.
`general-purpose`, measured, is handed no skills index. *(2026-10-18.)*

**A subagent may not list `eval`.** No subagent holds the sandbox in any configuration:
deepagents builds a subagent's stack from its own spec, `general-purpose` carries the
guards, the allowlist and registered middleware, a nested helper's tools leave `eval`
out, and a compiled subagent lists no built-ins. Listing it granted nothing with the
sandbox wired and was an unknown tool without, so it is refused at load either way, in
both subagent formats; the agent format takes it. A tool *called* `eval` could reach a
subagent only through middleware a deployment registers -- not a configuration to keep
open. The name lives in `core.definitions.subagents` beside `TASK_TOOL`, so the helper
filter and the refusal key on one spelling. *(2026-10-12.)*

**The skills switch is the deployment's, for subagents too.** `cfg.skills_enabled`
says what is wired and a request says what it wants of that. Asking only the request,
a subagent was handed the catalogue's index with skills off, while the agent got none
and `doctor` said none would be offered. A subagent's *own* bundle stays outside the
switch: the backend mounts its route either way, so withholding the index would leave
its files reachable and unnamed. *(2026-09-18, from an architecture review.)*

**A third reader was still asking only the request: the withheld run report.** With
skills off it still said `1 skill(s) not granted: beta`, implying another was granted,
while `doctor` said none would be offered -- measured on 2026-10-11 through a whole
turn. Where nothing is wired the offered set is `()`, so nothing was withheld. **The
rule that should have caught it asserted the defect**:
`test_every_kind_a_request_can_narrow_is_reported` ran on the `cfg` fixture, where
`Config.skills_enabled` is *off*, and so required the answer the switch suppresses. It
switches skills on now. *(2026-10-11, from an architecture review.)*

**A definition names at most one model**, and a subagent naming none runs its
caller's. A list there is refused rather than read. `indistinct` reports a
subagent that named a model and did not end up anywhere different.
*(2026-08-18, `compiled-subagents.md`, `agents-as-definitions.md`.)*

**What a subagent inherits is now held rather than commented.** A subagent naming no
model gets none on its spec -- deepagents hands it the caller's -- so its middleware
must be told the model it will run; a helper under a cheap-pinned subagent once ran the
expensive one. Only the helper half had been held: handing an inheriting subagent's
middleware `None` left all 2,084 tests green.

**The duplication that occasioned this was left alone, and that is the decision.** A
subagent's model is resolved twice per build. Measured: **0.010ms** to build against an
admit at 15-46ms, so no saving; both sites resolve from the same inputs, so they cannot
disagree; and the review's case, a top-level call carrying an inherited model, cannot
arise. `default_model=inherited if nested else None` equals `default_model=inherited`
for the one caller there is: it guards a caller that does not exist, which is why no
test can hold it. *(2026-09-23, from an architecture review.)*

**Reversed on 2026-08-19: a list of models, and `distinct: true`.** `model` took a
list tried in order, and `alias` was the only thing that could use one, so the two
went together. `distinct: true` turned `indistinct`'s report into a refusal, and
`second-opinion` was its only user in the shipped catalogue, so both went; the field
can come back if that subagent does.

**A gate is keyed by the name a call carries, and `interrupt_on` takes nothing else.**
It was judged against the spelling a *grant* uses, which is `vendor_a.py::fetch` once
two files define `fetch` -- so the bare name was refused as unknown, the long one was
accepted, and deepagents, which keys an interrupt by the name on the call, matched
neither. Driven: the subagent holding the tool ran it unapproved, and no spelling
existed that worked. A bundled tool could not be gated in any spelling at all, in the
one graph it exists in. Both read off the names calls carry now. The reference form is
refused at the read rather than resolved to the bare name, because the bare name stops
every tool answering to it: that is more than the reference asks for, and a gate
silently doing more is the same defect the other way round. A name only a *compiled*
subagent's folder holds is refused with the reason it cannot fire -- deepagents hands a
graph it did not build none of this agent's gates. *(2026-10-19, from an architecture
review.)*

**And a compiled subagent is handed none of the gated tools, rather than refused one.**
The same hole one door along, and older: a gated *catalogue* tool handed to a compiled
subagent ran there unapproved, measured on a tree without either change. `eval` had
already settled it -- `interpreter.py` leaves a gated tool out of `tools.*`, "since from
here it would run with nobody asked" -- and a compiled graph is the only other surface a
call reaches without meeting a gate, so it gets the same answer. Refusing the build
instead was not taken: a compiled subagent inheriting `["*"]` would stop every deployment
whose agent gates any workspace tool, over a tool that subagent may never call. The cost
is real and stated rather than hidden -- the subagent loses a capability it inherited, and
`kingfisher list` grew an `interrupt_on` line so a reader can see which. The exception
above stands: where **only** a compiled subagent's folder holds the name, withholding
would empty it of the tool it ships with, and being told beats a subagent that silently
cannot work. *(2026-10-19, from an architecture review.)*

**A turn resolves its agent once.** Admission resolved it twice down two branches --
from the catalogue, writing the pin, then reading the pin back -- counted at two calls,
two snapshot reads and two parses per turn under a policy. `_claimed` resolves it and
`_graph_for` takes the spec and source ids as required keywords: one caller, and a
default meaning "work it out yourself" would be a branch alive only in tests. **It is
resolved only where something will read it**, and that is behaviour: a deployment with
its own graph and no policy has no reader for the spec, and resolving anyway would make
it refuse a request naming a different agent mid-conversation. Both halves are driven.
*(2026-09-23, from an architecture review.)*

## Packaging: where the definitions live

This reversed twice. The current answer is the third one.

**Definitions live outside the wheel, and where a deployment gets them is a
setting.** `KINGFISHER_ASSETS` names one path; `seed(into, source)` takes it as a
required argument with no `None` branch. This repository's own are in `assets/`,
outside the wheel, committed and held to working by the suite. Definitions from
another project belong outside the checkout, and are seeded with a second `--from`.
*(2026-08-19, `examples-are-ours-assets-are-yours.md`. Its second directory
reversed 2026-10-16, below.)*

**A subagent can travel, and kingfisher discovers nothing.** A `SUBAGENTS` entry
with no `build` is a declaration kingfisher assembles, restricted to what a definition
can answer without seeing the deployment it lands in: `name`, `description`,
`system_prompt`, `builtin_tools`, `tools`, `skills`, `metadata` and `source_ids`.
`tools` holds the tool objects and `skills` absolute directories, never names. The
three it may not write -- `subagents`, `middlewares`, `model` -- each name something only
one deployment knows, and are refused with that reason rather than as unknown keys.

**What it carries goes in the plain fields, not under `bundle`.** `bundle` was the key a
document used to describe its folder, so one word meant a claim in one format and the
goods in the other; the objects already say *these are not names*. A string or a
`{name: ...}` in a portable `tools` is refused as a name, and `bundle` is refused naming
the plain fields. *(2026-09-23.)*

**This is not the entry above, and the difference is who does the finding.** The
packaging reversal was about *discovery* through entry points. Here a deployment writes
`subagents/acme.py` holding `from acme_agents import SUBAGENTS`, and that file is the
whole opt-in, in the workspace where `Origins` and `doctor` already report from.

**The rules hang on the shape, not on the origin.** A re-export is indistinguishable
from an entry typed into the file, so `build` present means the compiled rules and
absent the portable ones. A second export name or a self-declared marker would be a rule
about origin that any local file could claim.

**Both Python declarations check a required field the same way, in one body.** The
compiled branch checked only presence, so `{"name": "", "description": "", "build": f}`
reached deepagents, which dispatches on the name and shows the description to the parent
model, and `kingfisher list` exited **zero** over it -- against the rule that a zero exit
means nothing in the catalogue will break. Absent and blank stay different messages, as
"missing" sends somebody hunting a line they can see they wrote. Driven over **all four
readers**, with a control; `models.yaml` already refused every blank required key.
*(2026-09-27, from an architecture review.)*

**Reversed: a declaration without `build` may not say who reaches it.** Refused on
the reason the other three are, leaving the granting agent to carry the audience. That
made a deployment translating subagents from records that already say who may use each
write the audience again on every agent naming one -- the line one of them forgets. The
reason was weaker here, too: a name the vocabulary lacks stops startup, and with no
vocabulary the line does nothing, so it fails loudly or not at all; the other three have
no such check. Taken, read as a document's `source_ids:` is, and never handed to
deepagents. Carried tools may say who they admit through a `ToolSpec`. *(2026-09-23.
Reversed 2026-10-16.)*

**What it carries is its own, and `builtin_tools` is the exception.** Carried
tools reach that subagent and nothing else -- they enter no catalogue, so nothing
can grant or narrow them, which is what atomic means. Built-ins are the host's
rather than the definition's, so they stay narrowed by the request: otherwise
`pip install` would be a way to put back a shell a deployment had turned off.

**`Holdings` gained a second backing rather than the spec gaining contents.**
`SubagentSpec.bundled` holds names a definition lists from its folder, and `carried`
holds what it brought; a spec has one or the other. Tool objects on the spec for folder
bundles too would make *parsing* a definition import its bundle's Python, where now the
import waits until a bundle's tools are asked for. *(2026-09-16.)*

**A portable definition's `skills` are directories on the host, a list allowed.** In
deepagents' `SubAgent["skills"]` each entry is a path in the backend, which a package
cannot know, so here each is a directory that must exist on this host, and `/skills/...`
is refused naming the likely mistake. Several sit numbered under the bundle's one label,
so identity does not depend on the directory; two holding one name are refused, since
the subagent could hold only one. *(2026-09-25.)*

**A subagent's own skills folder is read flat, as a mount is.** The index listed it one
level down while startup read it as a catalogue of sources, so a skill in a subfolder was
granted and never on the menu. Both readers ask the repository whether it is `flat`; a
skill too deep is reported by `kingfisher list` and `doctor`, and a definition listing
one is refused. Not taken: one level of grouping, which needs a source per subfolder and
a clash rule, for subagents holding a handful of skills. *(2026-10-11.)*

**deepagents' other `SubAgent` keys are refused with a reason, not as unknown.** A
spec typed to that TypedDict is the likeliest to write `permissions`, `interrupt_on`,
`response_format` or `middleware`, and "not defined" reads as *not yet*. `middleware` is
not carried as tools are: deepagents merges it by name, so the `FilesystemMiddleware`
its docs suggest would replace the one holding the deployment's backend and rules.
`CompiledSubAgent`'s `runnable` joined them on 2026-10-12: a graph built before the
deployment saw it has its tools fixed, and `build` is what its author meant. The drift
test reads the keys off the TypedDict, so a new one has to be decided here.
*(2026-09-25.)*

**Considered and not taken: `own_subagents`, private nested helpers.** A portable
definition cannot give a helper another tool surface (its bundle is one set) or model
(`model` is unportable), leaving only context isolation -- which `build` answers. The
cost: recursion in the reader, a cycle check over a constructible
`entry["own_subagents"] = [entry]`, a compilation memo two packages' private `checker`s
would collide in, and rules for narrowing and listing a private helper. So `subagents`
stays refused, and its advice names the two real answers -- ship the helper as a subagent
of its own, or write `build` -- rather than folding the step into the prompt, which
cannot buy a fresh context. `test_the_helper_refusal_points_at_a_key_that_exists`
holds the advice to the format. Nobody has hit the refusal; build it when somebody does.
*(2026-09-17.)*

**Reversed: a second directory, ignored, for definitions somebody else wrote.**
`assets/` held only a `README.md` with everything else ignored, so a fetched skill with
its own `LICENSE.txt` could not reach an MIT repository through `git add -A`, while this
repository's set sat beside it under another name. Nothing went into the ignored one, so
the set took `assets/`, and such definitions live outside the checkout. An ignored
subdirectory is wrong because the suite walks every file under `assets/` as ours, so a
fetched skill could fail a change that never touched it. The cost: a sentence in
`assets/README.md` does not stop a careless `git add -A`. *(2026-08-19. Reversed
2026-10-16.)*

**Reversed: definitions as separate pip packages.** Assets were to leave the
repository entirely, become distributions of their own, and be found through
entry points that kingfisher named none of. Built in full, then taken back out.
What survived it: the framework still does not decide what a definition says, and
the integration driver still seeds a fresh workspace on its first run. The
`kingfisher` command and the service do not -- the library ships no definitions,
so they have nothing to seed from until `--from` or `KINGFISHER_ASSETS` names some,
and an empty workspace gets `SEED_HINT` instead.
*(2026-08-17, `assets-as-packages.md`. Reversed 2026-08-18.)*

**Reversed: definitions shipped inside the wheel.** The reversal of the above went
one step too far -- `src/kingfisher/assets/`, shipped to everyone, excluded from
every architecture rule. Its D1 was reversed the next day by the entry at the top of
this section; its D5, the tree back at the root, held.
*(2026-08-18, `the-definitions-ship-with-the-library.md`. D1 reversed 2026-08-19.)*

**Reversed: one `packages/` folder for three distributions.** Tidiness only, no
caller saw it, and it went with the packaging reversal above.
*(2026-08-17, `one-folder-for-the-packages.md`. Reversed 2026-08-18.)*

**Seeding lays the definitions folder out itself, and its parameter says what it is.**
`seed(into: Destination, source: Path)` returns a `Seeded`. `into` rather than `cfg`,
because `Destination` is a Protocol so seeding can run before a `Config` exists -- the
model catalogue is a file among the definitions, so reading one raises before the folder
does. `Seeded`, because it records something finished. It calls `ensure_definitions`
first: seeding a folder never laid out reported success and left no
`models.yaml.example`, a dead end a library caller could not see from the signature.
*(2026-09-01.)*

**`config_from_env`, not `from_env`.** It returns a `Config` and the bare name
said none of that -- imported at package level, which is how most calls read, it
could have returned anything, and it sat beside a qualified `paths_from_env`.
*(2026-09-01.)*

**"Catalogue" means a definition catalogue, and the two functions that meant
something else are renamed.** `models.yaml` is the model catalogue and a definition
directory is one too, and four functions were called `_catalogue`, two in neither sense.
In `doctor`, `_catalogue` checked `models.yaml` and `_catalogues` the definitions, one
letter apart; they are `_models` and `_catalogues`. **No rule against a singular beside
its plural**: `_agent` beside `_agents` is what a reader expects, and the trap was two
names with different subjects, which no rule on the letters can see. *(2026-09-24, from
an architecture review.)*

**`subagent` is the noun, and `delegate` is only ever the verb.** The formal names
followed deepagents -- `subagents/`, `subagents:`, `SUBAGENTS`, `SubagentSpec` -- while
prose and helpers used `delegate` as often, so readers asked whether the two differed.
`delegate` stays as the verb or the act (`test_delegation_ceiling.py`), and
`test_subagent_vocabulary.py` refuses the noun; `spikes/` is left as the record it is.
**The public spellings changed with it, with no alias kept** -- `subagent_only`,
`agent_subagents` in `kingfisher list --json`, the `doctor` rows `subagent tools` and
`subagent bundles`, the listing's `subagents:` -- because an alias keeps the second word
where a script reads; the upgrading notes list all four. *(2026-10-11.)*

**Seeding keeps its own tolerant reader, and the spellings it covers are held.**
`source_ids_named` and `middleware_named` read one field from a document that may not
parse, because seeding runs before there is a catalogue and must decide about every
file rather than choke on it. **Measured, and the replacement is what would cause the
bug**: a definition naming source ids and missing a `description` is left behind today,
but asked of the format it would read as unreadable and be copied into a workspace where
its names are not declared. On every definition in `assets/` and every spelling of an
audience the two readers agree. **Two of those four spellings were held by nothing**: a
requirement set and a bare-string source id could stop being read with the suite green;
`test_every_way_of_naming_a_source_id_is_left_behind` drives each through `seed`.
*(2026-09-24, from an architecture review.)*

**Definitions have a folder of their own, apart from the workspace.**
`KINGFISHER_DEFINITIONS_DIR` is required and holds every kind, with `models.yaml` and
`source_ids.yaml` beside them, under fixed names. `KINGFISHER_WORKSPACE` holds what
sessions make -- `sessions/` and `.kingfisher/` -- and nothing a person writes. As one
folder, a deployment that set none of the seven per-item settings kept its reviewed
definitions in the one folder the macOS fence lets every session's shell read.

*One folder, not a setting per kind.* `KINGFISHER_AGENTS_DIR`, its four siblings,
`KINGFISHER_MODELS_FILE` and `KINGFISHER_SOURCE_IDS_FILE` are refused if still set:
ignored, each would serve the definitions folder's copy where a deployment had chosen
another. Skills somebody else maintains are what `KINGFISHER_SKILLS_MOUNTS` is for.

*Required, with no default inside the workspace*, because a default would rebuild the
mix by omission. Seeding writes only here, so `WorkspacePaths.workspace` is optional.

*A start reads the folder and writes nothing there.* It may be shared or mounted
read-only, so a start making kind folders or examples would change everyone's, and one
making a missing folder would hide a failed mount behind an empty catalogue. A missing
folder is refused; a missing kind folder means none of that kind. So `backend_at` does
not make `skills/`, and `core.definitions.skills.registry.sources` offers no source for
a missing root, which deepagents would log about every turn. `ensure_layout` lays out
the workspace alone, and `ensure_definitions`, which seeding calls, makes the kind
folders and places both examples.

*Neither folder may be inside the other*, refused by `Config` and `WorkspacePaths`
alike: sessions inside the definitions put runtime state in the folder people review,
and definitions inside the workspace are back where every shell can read them.

*What the shell reaches*, measured on macOS through `backend_at`: a definitions folder
outside the home folder is readable and never writable, and under it hidden but for
`skills/`, which skill scripts run in. The profile still denies the definition roots and
the two files by path, which covers nothing today and holds if a definitions folder is
ever let back inside the workspace.

*Nothing checks for definitions left in a workspace.* They are read by nothing, moving
them is a one-time upgrade job, and a check on the workspace's top is one a session's
shell could set off. `Origins` carries `definitions` beside `workspace`, spelling a path
inside it `definitions/<name>`. *(2026-10-13.)*

## Capabilities

**One refusal, with the caller naming itself.** Tool-name rules live in a value
object, `Offering`, beside `FoundTool` in `core.definitions.tools.spec` -- not a
`Tool` entity. Offered names and sources are stored; grants are derived. There is a
test that fails when a function has no caller outside tests.
*(2026-08-17, `tool-rules-in-the-domain.md`.)*

**Capabilities narrow and never widen.** There was one exception: an upload could
widen skills and subagents, because those were the caller's own text, and a
middleware name got no such exemption because it selects code the deployment wrote.
Uploads were removed on 2026-09-16, and nothing widens now.
*(2026-08-18, `agents-as-definitions.md`, and the middleware work of 2026-08-31.)*

**And a turn is built inside the ceiling, which nothing checked.** `_claimed` narrows
the deployment's grants by the request and hands the result to the build. Handing the
request's own capabilities instead -- one line -- let a caller reach a tool the
deployment never granted, with all 2,083 tests green, since every fixture but two
leaves the deployment unrestricted. `_graph_for`'s `capabilities` defaulted to
`request.capabilities`, that escalation written into a signature, and is required now.
`test_a_turn_is_built_inside_the_deployments_ceiling` reads what the build was handed at
the seam rather than computing the intersection: a test that works out the answer the
way the code does agrees with itself and checks nothing. *(2026-09-23, from an
architecture review.)*

**The axes are read off the dataclass.** Written three times -- as fields, in
`__post_init__`, in `intersect` -- nothing held the lists together. Measured by adding a
ninth axis: it kept whatever list it arrived as, and `intersect` answered `'*'` for it,
so a narrowing widened with the suite, ruff and ty green. Both loops read `SELECTIONS`,
`fields(Capabilities)` minus the fields marked `SWITCH`. The marker is on the exception,
so the default fails closed: an unmarked non-selection is refused by `_normalise` at the
first construction rather than left out of the narrowing. `intersect` uses `replace`,
so a dropped axis keeps the grant whole rather than resetting to the default -- safer,
and why the obvious test did not bite; the rule drives each axis with two overlapping
sets, neither containing the other. *(2026-09-18.)*

**None of kingfisher's own middleware is offered to a definition, and that was the
alternative considered.** A name a definition can write is a name it can leave out, and
`GrantedOnlyMiddleware` is what holds a request to its grant -- omitting it would widen,
which *Capabilities narrow and never widen* allows no exception for. Nor do they fit the
registry: its entries are built from stored defaults, and these per request from the
names it resolved and its backend.

**An agent is told only of the built-in tools it was granted.** One granted
`builtin_tools: []` still read the sections on the file tools and the shell, deepagents'
host paths and langchain's `write_todos` prose, because `GrantedOnlyMiddleware` takes a
tool out of the request and none of what describes it -- and a model told of a tool
calls it, spending each call on a refusal. The file tools' section now names only those
held, and the shell's, with the host paths, appears where `execute` is. deepagents and
langchain write their prose whatever the grant, and `BuiltinToolsMiddleware` takes
theirs out by block, reading what the request holds: `GrantedOnlyMiddleware` sits just
inside `ModelReplyMiddleware`, ahead of the tools' own middleware, so the request they
see holds only what was granted, and an ungranted call is refused once, where a `grep`
of `a|b` was split in two and refused twice. Result clearing alone is handed the grant,
being first so it measures what the summariser counted. `TodoListMiddleware` itself
stays, and only its prose goes. A grant that changes the prompt changes the tool list
too, which Anthropic caches ahead of the system prompt, so no cache is lost that was not
already. Where to write moved to the session's section, since it holds for an agent with
only the shell. *(2026-10-18.)*

The rule holds sentence by sentence. *What your tools returned* names only the reading
tools held -- `glob` only beside `read_file`, since a file found and not opened is a call
spent on nothing -- and with none says to call the tool again; with the shell, not that
nothing reads the files, since the shell may. Memory is saved with `edit_file`, else
`write_file`, never the shell, which may run where `/memory` is not. An agent holding
neither, as the shipped `operator` does, is told in the guidelines that its memory can be
read and not added to, and not in the memory section: a request declining memory keeps
that section as the cached prefix while its file is denied. No subagent is handed memory;
deepagents gives it only to `mode: fork`, which kingfisher refuses.
deepagents' own tool descriptions name only held tools too, in its wording where it has a
variant: it picks `execute`'s and `grep`'s itself, but from every built-in, since it runs
before the grant is applied. `edit_file`'s claim to error on a file not read first was
false for every agent -- it checks only the path and permissions -- so it becomes a reason
to read first, and goes where `read_file` is not held; its indentation line, which speaks
of the read output, keeps only the indentation there. *(2026-10-18.)*

## Source-id access

**An audience lives in the definition it is about.** An agent or subagent writes
`source_ids:` for who may reach it, a tool writes its lock in its own file, and an
entry of `skills:` may be written long -- `{name: X, source_ids: [...]}` -- for who
is told about that one, since a skill's own file is `SKILL.md`, which the model reads
whole and a deployment may not own. One central file, `source_ids.yaml`, holds the vocabulary -- names only, no policy; `contains` is a
retired spelling there and refused. An audience resolves into an ordinary
`Capabilities`, so nothing downstream changed: an ungranted tool is never attached to
the graph and an ungranted subagent is never compiled. *(2026-08-31.)*

The rule that makes it safe to add to an existing definition: **`source_ids:` is the
default audience for everything the definition holds, and the ceiling on what a
`skills:` entry may say.** So an omitted or plain-list `tools:` keeps its exact meaning,
and a skills entry is always an *and* with the definition's own line -- the only way to
reach an entry is through the definition holding it, so naming a source id from outside
that line adds a second requirement rather than replacing the first. A definition with
no `source_ids:` line is reachable by everyone, and the report of every such definition
is built at startup -- default-open must not also be silent. `kingfisher list` prints
it; a plain start says nothing.

**A tool may say who may use it, in its own file.** `ToolSpec(tool, source_ids=[...])`
is an **and** on every grant of that tool, and the tool's only lock: nothing an agent
writes can widen or narrow it. A deployment whose tools are written in Python had
nowhere else to put it -- its translation to a LangChain tool dropped the field -- and
writing it on every agent that grants the tool is the line one of them forgets.

It is applied where the grant is resolved, not where the catalogue is read: taking the
tool out of the catalogue for a caller would have a definition naming it refused by
`refuse_unknown` for that caller alone -- the failure that reversed the central
`access.yaml` below. So the build takes it out of the grant, `kingfisher list` and the
run report out of what they say is offered, and the ledger stamps a result no wider than
its tool, each through `core.definitions.tools.spec.out_of_reach`. A subagent's own tool
is held back the same way.

**The audience is kingfisher's record, not the tool's `metadata`.** `BaseTool.metadata`
was the obvious home and is wrong: LangChain passes it to every callback, so an audience
kept there is in the trace of every call. It lives on the `FoundTool` the loader makes,
never on the LangChain object. *(2026-10-16.)*

**A datazone is a second field, held as one more name.** A caller is in at most one
datazone, and a subagent or tool may require one; an agent may not, because it runs in
whichever datazone its caller names. A prefixed source id -- `datazone:finance` in
`source_ids:` -- would have needed no new field, and was turned down for that reason: a
caller has exactly one datazone and any number of source ids, and folding one into the
other would leave "exactly one" to whoever builds the caller's list.

Inside, it is one more name, deliberately. A caller's datazone joins their expanded
source ids as `datazone=finance` in `held_by`, and a definition's becomes one more name
its audience requires -- so every reach check asks it too, and the ledger stamps a zoned
result with no new column. The prefix is refused as a source id. On its way in it rides
among the caller's source ids as a `Datazone`, a `str`: the dozen doors before `held_by`
pass it on unread, where a second parameter would be one more each had to remember. A
caller in no datazone reaches only what names none -- the closed answer, as for a call
naming no caller. An agent naming a tool from another datazone is not refused at
startup: the tool is left out of that caller's build, and works for a caller in its
zone. *(2026-10-16.)*

**The operator's listing prints both.** A tool's own audience is a line of its own,
`tool sql_query  [sales_db]`; a subagent's own tool is named `counter::count`, because
the file `audienced_tools` keys it by sits in a folder the key does not name. A datazone
is printed beside the source ids, not folded in, because a subagent writing only
`datazone: finance` was listed as `[*]` -- everyone. Only skills get a roll-up: a tool's
or subagent's lock is one line in its own file with nothing that could differ from it,
while a skills entry is a lock per use, and a use wider than its neighbours is what a
roll-up shows. *(2026-10-17.)*

**A session keeps the datazone of its first zoned turn.** A datazone decided which
tools a turn was handed and nothing after: a session answered to its pinned agent, which
has no datazone, so a finance turn's results sat where any caller of that agent could
read them with `artifact` or carry the session on from no datazone. The zone is pinned
beside the agent, in `.harness/datazone`, and a session's audience is its agent's and
that zone, so every door that asks the pin asks the zone. The first *zoned* turn's
rather than the first turn's: an unzoned turn holds nothing a finance caller may not
read, so a finance turn may carry on an unzoned session, which from then on answers
only to finance. Taken from the caller's datazone rather than from the ledger's stamps
on what the tools returned: a supplied graph keeps no ledger, and the caller's zone is
what decided everything the turn could reach. *(2026-10-19.)*

**Who holds a tool is printed under each holder.** Each agent and subagent prints
`tools:` and `builtin tools:` lines beside an agent's `subagents:`, so which agents list
`http_fetch` needs no file opened. Under the holder, not as a roll-up by tool: listing is
the one way to hold a tool, and a roll-up would print that list again sorted the other
way. A tool is spelt as the workspace tools section spells it, so it reads the same under
every agent.

A star prints beside what it covers today, `* — today csv_profile, ...`, since the names
alone read as fixed and a star also covers what is added later. A subagent's is not
expanded, `* — whatever the calling agent holds`, because a subagent never gets more than
its caller; its `--json` `names` are only its own tools. A scoped listing answers each
line from the same `offers_tool` that filters the `tools` section: a second answer would
hand back under an agent a tool that section withheld. *(2026-10-18.)*

**Reversed: a `tools:` or `subagents:` entry says who reaches that one.** Written long,
`{name: X, source_ids: [...]}`, an entry was a lock per agent: an *and* with the
definition's own line and with the thing's own lock; a compiled subagent wrote it as a
`tools` mapping of name to audience. Each thing has one lock now, in its own file, and
an entry is its name. **One place to read**: who can use `http_fetch` is answered by its
tool file, not every agent that lists it, and a lock on each agent's entry is the line
one of them forgets. Who may use a tool in an agent is three checks, each in one obvious
place: the caller can open the agent, the agent lists the tool, the caller passes the
tool's lock. **Per-agent control is by listing.** The one case lost is the same tool
locked differently in two agents, which the deployment this was decided for does not
need.

`skills:` keeps the long form, for the reasons *Not taken: a skill's lock in its own
`SKILL.md`* gives. A long `tools:` or `subagents:` entry writing `source_ids` is refused
at load, saying where the lock goes, as is a compiled subagent's `tools` mapping and a
long `subagents:` entry with nothing beside its name. A ledger stamp is the definition's own audience joined with the tool's
lock. The shipped `analyst` grants `auditor` bare, so an `audit_log` caller of `analyst`
reaches it, which `auditor`'s own line already admits. *(2026-08-31. Reversed
2026-10-18.)*

**Not taken: a skill's lock in its own `SKILL.md`.** Until 2026-10-18 the reason given
for the entry was that a skill has no file of its own, which is not true. The Agent
Skills frontmatter has a `metadata:` map, which deepagents parses and leaves out of the
listing it shows the model, so a lock there could be read. Three things keep it on the
entry. The listing tells the agent to read `SKILL.md`, frontmatter and all, so the
policy would be in the model's context, where a caller is otherwise shown none of it. A
vendor's skill pack is a file the deployment does not edit, so the entry is the one
place it controls -- the reason `ToolSpec` wraps a tool rather than changing it. And the
format is deepagents', unchanged, whose `metadata` holds strings only, so a requirement
written as a set `{a, b}` would need a spelling of its own. Little is lost: a skill was
never a boundary, and the locks that matter are on the tools its procedure needs.
*(2026-10-18.)*

**Reversed: a central `access.yaml` listing every asset by name.** Built in full --
vocabulary, per-asset audiences, reconciliation against the catalogue, two load reports
-- then replaced within the same branch. What killed it was not taste. A central table
can name an asset the workspace no longer offers, and that stale entry had to be
*dropped* rather than reported, because the grant it produced reached
`Offering.refuse_unknown` and turned every turn into a refusal. A definition *is* the
asset it is about, so that failure has no shape now, and the reconciliation and its
reports went with it. What survived: `UNSCOPED`, the refusal of a call that names no
caller, the per-turn re-check of a session's pinned agent, and the closed vocabulary.
*(2026-08-31, reversed the same day.)*

**Reversed: `for_groups` and the `Caller` handle.** A caller said who they were once and
reused the handle; now every call takes `source_ids=`. Counted before removing: zero
production callers, and the one consumer serving several callers passed `source_ids=` at
all seven call sites. The stated benefits did not survive checking: the grant it
resolved once measures **0.81 microseconds**, because the transitive closure is computed
when `source_ids.yaml` loads, and refusing an unknown source id "at the boundary" gains
one call, since `expand` refuses before any agent is built. What was real was script
ergonomics, and one accident: the reader of `source_ids` took anything but a tuple as
*no opinion*, so the handle's coercion was the only reason `["A"]` narrowed. Any sequence
now means what it looks like, and a bare string is refused. *(2026-09-03.)*

**An audience list is an `or`, and an entry of it may be an `and`.** A requirement --
several source ids held at once -- is written two ways deliberately: named in
`source_ids.yaml` for anything reused, inline as one entry of a list for a one-off. The
same shape both places, so the named form is literally a *name for* the inline one: the
argument against two spellings was drift, and one form with one evaluation answers it.
The result is or-of-ands, the shape access rules take, out of one field. `expand` does
the work -- covering closes first, then a compound joins the held set once its parts are
held -- so two consequences follow rather than being chosen. **Covering satisfies a
requirement**; the alternative is an `admin` who covers both parts yet is weaker than
the sum of what they reach. And **nesting works for free**. *(2026-09-01.)*

**A compound is derived, and nothing may hand it over directly.** A caller may not
present one: accepting it would let one assertion stand in for the two the requirement
exists to demand, and the refusal names the parts. A covers list may not either, by the
same rule: `admin: [finance-senior]` gave an admin the compound while they held neither
part -- the requirement defeated by the file declaring it. Naming the parts reaches the
same people and is visible, since the listing prints what a compound requires and never
a covers list.

**The two operations are told apart by shape, not by a keyword.** `contains` and
`all_of` were the spelling until the format was asked why YAML needed English words for
two things it has brackets for. A list body is what a name covers, a set body what it
requires:

```yaml
source_ids:
  sales_db:
  warehouse: [sales_db, audit_log]      # covers
  sales_db_pii: {sales_db, pii}         # requires
```

A name writing both operations stopped being expressible, so no refusal for it is
maintained. The listing stopped inventing `finance_db+pii`, which no file could write
and `+` made ambiguous; it prints `{finance_db, pii}`, and `--json` nests, since a script
should not parse a set out of a string. The cost was one new refusal: a set inside a
covers list -- `warehouse: [{sales_db, pii}, audit_log]` -- is the covers-list bypass with
no name to look up, so it is refused where it is written. Both retired words are refused
rather than ignored, or an upgrading deployment's policy would be read and dropped in
silence; `A: {}` is refused with them, since an empty set admits everyone.

**The refusal is for the file, not the name**, which makes it a migration rather than a
wall. One message per source id costs a restart per line and says nothing of the
`contains` further down; the scan reads the whole document first and prints each line
beside the line that replaces it, built from what the file says -- the argument of the
seeding message under *Reversed: `groups.yaml.example`* below. A set whose names happen
to be `contains` and `all_of` stays legal: the value is what marks a keyword.
*(2026-09-15.)*

**In Python, a requirement is a set, and a list inside an audience is refused.** The
only Python spelling of *all of* was `{"a": None, "b": None}`, which nobody types; a set
became one source id spelled `"{'a', 'b'}"`. A set now reads as the requirement it looks
like. A list or tuple inside the audience is refused rather than read as *all of*,
though a caller's own format may nest it that way: a bracket whose meaning depends on
its depth is misread in the direction that widens -- `(a, b)` at the top is *a or b* --
and one bracket per operation makes that impossible at any depth. The `--json` listing
still nests a requirement as an inner list, because JSON has no set; handing that back
in is refused rather than misread. *(2026-10-16.)*

**Reversed: `refuse_dead`, the rule that an entry audience must overlap its
definition's.** Measured, it had no true positives left and one class of false ones. An
entry audience is already an **and** with the definition's, so `[senior]` under
`[analysts, auditors]` is a good second requirement, and the fault it meant to catch --
`[auditors]` under `[analysts]` by someone trying to widen -- is the same shape, so no
rule can separate them. What survived is the looking: `SourceIds.narrowing_in` feeds
`AccessReport.narrowed`, said once beside `unrestricted`. The typos it really caught are
`refuse_undeclared`'s, refused by name, and a misspelling is no longer explained as a
reachability problem. *(2026-09-01, both the same day.)*

**`source_ids.yaml` now holds vocabulary with a rule in it**, against its pitch of "a
dictionary, not a policy". A compound still answers "what does this name mean", with a
condition; judged to stay on the right side, because what it cannot do is say who
reaches what, the property that sank the central design. Recorded because it is cheap
to disagree with now and expensive later. The `--json` `access` key became
`{names, requires}`, since a compound has no honest place in a name-to-closure mapping.

**`builtin_tools` takes no audience, and that is not an omission.** deepagents registers
its own tools, so kingfisher can filter them but never leave them out of a graph -- a
live run was measured where a model called `execute` from memory. Gating them here would
promise a boundary it cannot keep. What gates them is which *agents* a source id may
open, since an agent declaring a read-only builtin set cannot yield the shell to anyone.

**Out of reach reads as not offered.** An asset a caller's source ids do not reach is
absent from listings, from the "this workspace offers ..." in a refusal, and from the
report of what a run withheld -- so nothing lets a caller enumerate the catalogue by
guessing. What can be asked of a tool or a skill is whether an agent this caller reaches
offers it to them, and of a tool whether they pass its own lock.

**Reversed: the withheld report hides only what source-id narrowing removed.** The
exception held that what an agent never declared is a fact about the agent. Measured:
`list --as B` showed `tools: ['line_count']` while a run as `B` was told
`1 tool(s) not granted: secret_export`, a tool no definition `B` reaches offers; the
caller supplies `capabilities`, so narrowing their own grant handed them the catalogue.
The report now asks the listing's question through the same `offers` in
`core.access.reach`, since two readers answering what a caller reaches is how these came
apart. With no vocabulary, or under `UNSCOPED`, `offers` admits every name. Skills are
compared by the skill each spelling means, so an audience on `catalogue::audit` hides
`audit` too. *(2026-10-13, from an architecture review.)*

**A scoped view is filtered in the record, not at a renderer.** Measured through the
real command, `--json` handed a caller holding one source id the name, file and subagent
chain of every definition out of reach, plus the vocabulary, every audience and the
access report: `Inventory` filtered two fields, the text form covered for the rest, and
its own comment claimed the names were already filtered. `inventory` filters now, so
every renderer is handed the same truthful record, and what a caller reaches is computed
once for every field. The vocabulary, the audiences and the access report are the
*policy*, and a caller reading their own view is not the operator checking it, so a
scoped view carries none of them. **The rules search the whole rendered output for a
name that should not be there**, in both forms, rather than naming the fields that
leaked: a field added later would leak past a field-by-field assertion. *(2026-09-26,
from an architecture review.)*

**Nor for the fields naming a subagent, until 2026-10-12.** A subagent's own tools and
skills, the compiled and shadowing ones and their faults were carried whole, invisible
to the rule above because its fixture held no `subagents/`; a caller holding `B` was
shown seven fields of names for subagents only `A` reaches. They are kept to the same
`for_caller_subagents` answer now. Orphaned folders belong to no definition to filter
by and are the operator's diagnostic, so a scoped view leaves them out. A scoped
listing's exit status follows the view. The rule's fixture holds subagents out of reach,
valid and faulty. *(2026-10-12, from an architecture review.)*

**It did not hold for the tool and skill catalogues at all.** `reaching("tools", ...)`
looked up audiences under a key the walk never built, matched everything at `"*"`, and
filtered nothing -- replacing the call with the unfiltered tuple left all 2,167 tests
passing -- so a caller was shown tools and skills only agents they cannot open offer, and
each tool's file. A listing asks whether **any agent this caller reaches offers it to
them**, which is `offers`, with a tool's own lock applied beside it. Three things about
that walk were each a silent mutation until a rule held it. **Reachable definitions, not
all of them**: `["*"]` means anyone who reaches *that definition*, which a run gates by
the caller opening it and a listing must gate itself. **`["*"]` ends the walk**: `ALL`
is the string `"*"`, so a union would collect the character; an unset `tools:` or
`skills:` offers nothing, and the filter reads what the definition resolved to for this
caller. **Skills are compared by identity, not spelling**: `audit` or
`catalogue::audit`. *(2026-09-27, from an architecture review.)*

**Nor for the faults, until 2026-10-13.** `broken` -- `undeclared_in`'s complaint about an
undeclared source id -- went into `agents_error` and `subagents_error` whole: a caller
holding `B` was handed a subagent out of reach, its source id and every declared one,
beside the `access: null` that exists to withhold them, and exited 1. Only `list --as`
and `inventory(source_ids=...)` reach that state; a deployment in it is refused at
construction. **The faults are not all filtered the same way, and the differences are
the whole of it.** A misfiled skill is loaded and offered, so it is kept to what reached
definitions offer, as `skills` is -- emptying it would take away the one skill fault a
caller can act on, and a rule holds that so the three cannot collapse into one
`if scoped`. A skill that would not load, or nested too deep, has no name to filter by
and is the operator's diagnostic. `broken` is policy, so it goes with the policy. The
fault maps key agents as well as subagents, and a subagents-only fixture left the agent
half unheld -- the only survivor of that review's twenty-one mutations -- so each fixture
holds both kinds. *(2026-10-13, from an architecture review.)*

**The HTTP surface asked who was calling and authenticated nobody.** It went on
2026-09-15; its decisions stay as the record. Reading source ids off a header was
shipped but never defaulted, so trusting one was a line somebody wrote. A policy with
no identity source, or a source with no policy, refused at startup -- the second is a
server somebody believes is locked down and is not. `UNSCOPED` was unreachable over
HTTP: it is a value a person types at a call site they can see, and from a request it is
one a bug can produce. **The caller gets a code, the log gets the reason**: a session out
of reach answered as a wrong id does, and an undeclared source id got a body naming no
source id while the logger got the list. What the
library still does: every call names its caller with `source_ids=`; a presented
compound is refused with an `AccessError` naming the parts; `kingfisher run --as
UNSCOPED` passes `UNSCOPED` on purpose; a turn in a session whose agent the caller
cannot reach raises the `UnknownSessionError` a wrong id does, and `session` answers
`None` for it. *(2026-09-01.)*

**A turn checks who is calling before it touches the session.** The per-turn re-check
the `access.yaml` reversal lists among its survivors was performed by nothing: every
turn after the first returned the pinned agent unasked, so a caller holding another's
session id ran in it with everything that agent grants. The test named for the case
never pinned an agent. The check is the first thing a turn does, before the session is
marked used, claimed or written to -- refused at the agent, the caller's files were
already in the session's inputs, which was measured. The refusal is word for word an
unissued id's, so holding a real id teaches nothing. *(2026-09-15.)*

**Who may continue a session is the caller's to decide.** A caller holding another's
session id, and reaching its agent, continues that conversation and sees all of it.
Measured on 2026-10-17: a caller holding `B` was shown a result from a tool only `A` may
use, run earlier in the session. Three fixes were turned down. Gating on the results'
stamps leaves out what carries none, the uploads and the conversation itself. Pinning a
session to whoever started it decides who owns a conversation, which kingfisher cannot
know. Hiding the results cannot hold, because the model's replies repeat them.
Kingfisher has no users, only the source ids a call names, trusted as given; who owns a
conversation is the deployment's knowledge. So source ids decide what a turn may use --
its agent, tools and subagents -- never what the conversation already holds. The stamps
stay, as the record a caller reads off `RunResult.ledger`. *(2026-10-17.)*

**One reader of what a caller says.** Naming an agent and reading a session each tested
for a tuple, so `["B"]` opened an agent restricted to `A`, and `"B"` got through both.
`core.access.checks.held_by` is the only place that decides what a shape of `source_ids`
means, and `test_one_function_reads_what_shape_a_caller_s_source_ids_are_in` holds that
across the package. `with_datazone` is the one other reader, and asks only whether it may
append a datazone, leaving a bare string for `held_by` to refuse.
*(2026-09-15.)*

**One walk of the definitions, for three questions.** `audit` reports what a policy
leaves open, `undeclared_in` the first definition naming an undeclared source id, and a
listing what each says; `core.access.checks.walked` yields `(kind, name, said)` once for
all three. Shared and not merged: a report that stopped at the first fault would be a
refusal, and a refusal that carried on would be a report. `Kingfisher.__init__` refuses
first, because a typo makes a line both undeclared and narrowing, and as a narrowing it
explains the wrong fault. *(2026-09-16.)*

**One reader of what a definition says, and one narrowing of it.** The narrowing of a
definition's audienced fields was written out six times while `AUDIENCED` already named
the three fields. `core.access.reach.stated` and `narrowed_for` are the one of each,
beside `reaching`, because a kind may not hold a helper the kinds share. `narrowed_for`
returns the three selections and never a `Capabilities`, deliberately: an agent widens
`models` where a subagent leaves it unset, and `memory` is an agent's to state, so the
axes no audience touches stay with the definition. *(2026-09-16.)*

**Reversed: `groups.yaml.example`, shipped in the package and placed by
`ensure_layout`.** `seed` names `source_ids.yaml` in a skip message, and no example of it
could be reached from an installed deployment. True, and the example still did not help,
visible only by running it: it ships *one* vocabulary, so seeding this repository's set
said `declare analysts, auditors, senior-analysts` beside a file declaring
`readers, writers, staff, senior, senior-writers`. So the remedy travels in the message,
built from the names actually missing -- one line, the union across every skipped
definition, because per-definition copies are overlapping partial lists. The flat form
only: whether a name covers or requires is the deployment's choice, and nothing can infer
it. `assets/source_ids.yaml` is the only source ids example.

**`models.yaml.example` stays, and the asymmetry is the point.** `models.yaml` is
required with no fallback, and its example is a hundred lines of annotation nothing else
carries; its missing-file error already prints a minimal catalogue inline. `source_ids`
is still not seeded: it is not a definition kind, and copying a policy would make access
control something a deployment inherits rather than adopts. *(2026-09-02, reversed
2026-09-04.)*

**`groups` is `source_ids`.** The concept did not move; the word changed everywhere it
appeared. Renaming the definitions' key alone was the first shape and the wrong one: it
leaves a reader two words for one thing. **The prose noun is "source id", never bare
"source"**: that word means *where a definition was read from* here --
`definitions_source`, `access_source`, `skill_sources`, `models.source`, and 197 bare
uses. **The shipped example's vocabulary moved with the key**, from job titles to
`sales_db`, `audit_log`, `warehouse`, `pii` and `sales_db_pii`, because job titles teach
the wrong reading of the key. No compatibility window, on the precedent the export table
set at 0.1.0: `run(groups=)` is a `TypeError` and `groups:` an unknown field. One
spelling fails silently, stated rather than discovered: a file still named `groups.yaml`
is no file, which means *no policy*, so the workspace is reachable by everyone with
nothing red -- accepted as the documented meaning of an absent vocabulary.
*(2026-09-15.)*

**One place asks who is calling.** A call naming nobody under a policy was refused at two
of three doors; reading a session answered, because `held_by` returns `None` for no
vocabulary, nobody named and `UNSCOPED`, and every reach check reads `None` as everything
-- wrong for the second, and the answer named the agent someone else's session is pinned
to. `caller_holds` is the refusal and the lookup together, and `_prepare_steps` and
`Kingfisher.session` go through it. `_agent_named` is not a door: it filters on what
`_prepare_steps` found, since a second ask could only repeat the answer or refuse what
the first had already refused. Housekeeping takes no caller:
`sessions()` naming nobody is the operator's view of every session, and deleting acts on
a `Session` handle already handed out. `kingfisher reap` passes `source_ids=UNSCOPED`
where it reads a session, which is what a caller who means no caller writes, and
`kingfisher list` stays exempt -- read-only, run by whoever has the policy in front of
them. *(2026-09-18.)*

**A subagent's own line is a ceiling at build, where it had been one only on paper.**
`formats.md` makes a subagent's `source_ids:` its intrinsic ceiling and the default
audience for its entries, and the listing read it so, but the build never asked.
Measured through `build_agent` with the guide's `reviewer` and a caller in `C`: a
subagent written `source_ids: [A]` built because the agent named it, with tools, skills
and helpers its entries keep from `C` -- the build a caller in `A` got. The shipped
`auditor` never showed it, because `analyst` narrows the same tools the same way. A
subagent its own line does not reach is now left out of what an agent activates, and
what a built one holds goes through its `declares(held)` before the request's grant,
while what is refused stays the file's own names, so a typo is refused whoever calls.
Helpers are narrowed by the activated set, not the grant, which may be `"*"`. The tests
build the guide's example for `C` and the control for `A`. *(2026-10-12.)*

**What a caller is told about subagents follows the same reach as the build.** Measured,
a caller in `C` with subagents written `source_ids: [A]` was told `insider` was not
granted, saw it on a listed chain, and was shown its model in the report of subagents
running on the host. The build and both run reports drop a subagent through
`within_reach`, the host report reads the build's roster via `for_the_caller`, and a
listing's chain is kept to `for_caller_subagents`. *(2026-10-12.)*

**A refusal of a subagent nothing defines offers only the ones the caller reaches.** It
listed every definition after `offered:`; both refusals list through `offered_to` now.
Whether one is raised is unchanged: a typo in a file is refused whoever is calling.
*(2026-10-12.)*

**A name a request writes reads the same whether nothing defines it or it is out of
reach.** Measured with a real turn, a caller in `C` under an agent writing
`subagents: ["*"]`: naming `ghost` was refused, naming `insider` -- defined, kept from
`C` by its own line -- ran without it and said nothing. So `C` could tell a real name
from a typo by guessing, which *out of reach reads as not offered* exists to stop.
Closing the gap was chosen over rewording the guide, and for a request the rule that a
refusal must not depend on who sent it is reversed: it now depends on who sent it,
because that is the only way it can stop depending on what exists.

A request's own names are settled by `settled_request` in the service, while still apart
from the grant and the agent: the build is handed the two combined, and a grant listing
a subagent out of `C`'s reach would have `C` refused for a name `C` never wrote. Where
both offer every subagent, a name out of reach is refused in a typo's words; where either
is a list, it narrows the request silently, typo and out-of-reach alike. A bare `x` with
two definitions means the one the caller reaches, and `"*"` expands to what they reach.
A file's mistakes stay caller-independent -- a subagent nothing defines, two of a name or
a loop is the file's fault and no caller can fix it -- and an agent's names are checked
as its file wrote them, by `refuse_misnamed_by`, so a typo behind an entry audience is
refused for a caller it excludes too. Three refusals still name a definition out of
reach, because no true wording hides that it exists: a file naming a bare name two
definitions share, an agent's file naming both, and a loop through definitions the
caller cannot reach. *(2026-10-12.)*

**Naming a caller where nothing narrows is refused, at one door.** With no
`source_ids.yaml` any name expands to nobody and the answer is unrestricted. A turn was
refused for it and a listing was not, so `kingfisher list --as marketing` printed the
whole catalogue and exited zero -- read as what marketing reaches, not as a workspace
with no policy. It is the state the HTTP surface refused as "a server somebody believes
is locked down and is not". The refusal is in `held_by`, so every door shares it; the
turn's own copy had also refused `UNSCOPED`, the one value meaning *no caller*.
`UNSCOPED` and an empty sequence pass; a name does not. *(2026-10-14, from an
architecture review.)*

**A bundle that will not import is told to every caller, and that is not a leak.**
Definition-scoped faults are filtered to a caller's reach, and `bundles_error` is not.
`warm` reads every bundle and the service constructor calls it, so a bundle that will
not import stops the deployment before any request names a caller -- measured -- and
withholding it would hide why that caller's own run refuses to start; `subagents_error`
is kept for the same reason. Each bundle is read on its own, so a failure is named by
whose folder it was in and every broken one is reported. The traceback is carried over
the re-raise on purpose: `test_a_defect_reaches_the_refusal_it_is_filed_under` keys a
refusal by the deepest frame inside the package. *(2026-10-14, from an architecture
review.)*

**A caller is told only what some path reaches, and a declaration is not a path.**
`formats.md` promises to hide "an asset a caller's source ids do not reach", and "no
definition they reach declares it" left three gaps, each measured against the real
build. A tool or skill a reached subagent declares and no reached agent grants: `ceiling`
and `subagent_skills` gave the subagent nothing while the listing offered the name, so
`offers` is asked of reached *agents* -- a subagent is never a widener. And a subagent
its own line admits but no reached agent names: a request naming it wired nothing,
because `Capabilities.subagents` may only narrow what an agent declares; both
conditions are asked together in `handed_work_by`, shared by the listing and the report.
`handed_work_by`, `within_reach` and `activated` sit in `core.access.reach` rather than
in `activation.py`, which imports deepagents and would cost `kingfisher list` a provider
SDK.

The refusals are listings too, and were narrowed. What a refusal *judges* is not: a file
naming a tool nothing offers is its fault whoever asks, and a definition naming a real
tool out of one caller's reach is no error at all, which
`test_a_definition_naming_a_real_tool_out_of_reach_is_not_refused` holds -- narrowing
the judgement would refuse one file for some callers and not others. Unscoped, an
operator sees everything, including the subagent nobody activates, which is what they
read a listing to find. *(2026-10-14, from an architecture review.)*

**What a caller reaches is read from the catalogue's own spelling, and asked of every
agent they can open.** Four gaps, each found by mutating the guarded thing rather than
by reading. `offers` compared `csv_profile::csv_profile`, the documented long form,
against the catalogue unspelt, so a scoped caller was told they reached no tools while
the build handed them one; `placed` is passed as `same=` now. Two readers asked a spec
raw and leaked a moved tool's path and a subagent out of reach; both read
`declares(held)`. A refusal about a file listed only what the agent being built names,
but a *file* is fixed by editing it and may name anything the caller reaches through any
agent they can open, while a *request* is bound by the agent it opened; `offered_to`
takes `by=` for the difference.

The fourth was a guard over nothing, twice. `agents_to_ask` filtered by the caller's
reach and short-circuited an unscoped caller, and mutating either away left the suite
green: `narrowed_for` already narrows every subagent an unopenable agent names away, and
`handed_work_by` answers an unscoped caller itself. A condition restated one level up
from the place that holds it is the same defect as a rule that cannot fail. Both are
gone, and the ten mutations of this slice all bite. *(2026-10-15, from an architecture
review.)*

## Models and endpoints

**Endpoints and models are separate concepts in one file.** `models.yaml` holds
both; a model names an endpoint. Model parameters live there and a definition
names a model and nothing more. An endpoint whose `key_env` is unset is dropped as
the catalogue loads, with a warning; `Models` keeps the models that named it, and
`doctor` warns about those rather than staying silent -- silence made a typo in
`key_env` look identical to a shared catalogue naming an endpoint this machine
cannot reach. *(2026-08-16, `model-catalogue.md`; `what-the-catalogue-dropped.md`.)*

**`max_retries` is the SDK's, set per model.** How patient to be with a provider that
refuses or cannot answer differs by provider and plan, so a model row says it, and the
SDK backs off and reads the provider's retry-after. Unset is the SDK's own. A `Landing`
with nowhere to keep it refuses a model that sets it, since a keyword a class does not
know is moved into the request body. Kingfisher adds no retry layer: one would multiply
with the SDK's. *(2026-10-17.)*

**The shipped definitions name no models.** A vendor's model id is portable
nowhere, and a file shipped inside a wheel cannot name one.
*(2026-08-16, `model-catalogue.md`.)*

**A wire format is named after what it speaks, and an unbuildable one is
refused as the file loads.** The Responses-API row was called `openai`, promising
what every gateway speaks and delivering `/v1/responses`, which almost none do:
`api: openai` for a gateway loaded, built, and failed inside the first turn. It is
`openai_responses`, and `model_catalogue.load` refuses an `api` naming no format,
listing what can be built. Neither half works alone: the check alone catches only typos,
the rename alone is a breaking change nothing enforces. Refused rather than dropped, and
checked *before* the missing-key drop: a missing key is a fact about one machine, an
unbuildable `api` a fact about the file, and checking it second would make one catalogue
load here and fail on the machine that holds the key.

**No Chat-Completions row was added**, though it is what those gateways speak. MiniMax
and every gateway worth pointing at publish an Anthropic-compatible endpoint, the
recommended path; a deployment that needs one adds it itself. *(2026-09-04.)*

**A row says where its class keeps each value, and a model that did not keep one
is refused as it is built.** A chat class handed a keyword it does not know moves it
into `model_kwargs` with a warning and builds anyway; for `base_url` the request then
goes to the vendor's default host carrying the gateway's key -- measured on
`ChatOpenAI`. `WireFormat.lands` records where each value lands, and `build_model` reads
every value back through it, so the check holds for a row from anywhere. `temperature`
and `top_p` are not read back: `ChatOpenAI` drops `temperature` for `gpt-5`, which
rejects it. A refusal never quotes a key -- a class that loses the endpoint's key falls
back to one from the environment, and the message goes to a log. *(2026-09-24.)*

**A deployment adds a wire format through a setting, in Python.**
`KINGFISHER_WIRE_FORMATS` names a zero-argument callable returning `WireFormat`s by
name, the convention `KINGFISHER_SESSIONS` follows, merged over kingfisher's two as the
catalogue loads. Each endpoint carries the format its `api` resolved to, so building a
model never looks a name up again in a table that might not hold it.

**Considered and rejected: a `Kingfisher(adapters=...)` argument.** `api` is checked in
`config_from_env`, before the constructor sees an argument, so the check would have had
to move later. And `kingfisher doctor` and `run` build their own instance with nowhere to
pass one: a catalogue naming a deployment's format would load in its own program and be
refused by the commands meant to diagnose it.

**Considered and rejected: rows in `models.yaml`.** A row names a class to import, and
that file is shared across a fleet and reviewed as data -- "it says where your prompts
go".

**A shipped name is refused, not replaced.** Letting a deployment's `anthropic` win
would move every `api: anthropic` endpoint to a class its author never chose, with
nothing in `models.yaml` to show it. *(2026-09-24.)*

**`doctor` answers "why will this not start?" and nothing else.** It makes no probe
call, and points at the caller's own task as the end-to-end test.
*(2026-08-17, `a-command-worth-shipping.md`; `what-the-catalogue-dropped.md`.)*

**The command line's model keys may sit in a keys file it is told about.** An endpoint
says `key: <entry>` instead of `key_env: <VAR>`, naming an entry in the file
`KINGFISHER_MODEL_KEYS_FILE` names. `api_key:` inline in `models.yaml`, the obvious way,
puts keys in the workspace, which the macOS fence lets the agent's shell read -- more
exposed than a `.env` under the home folder. `key_env` stays for a runtime that hands
secrets over as variables; an endpoint names exactly one. `key_file:` per endpoint, the
Docker/Kubernetes shape, was left as a later addition.

**No default, only the setting names the file, and only the keys move.** A home-folder
default would be the first place kingfisher picked outside the workspace by itself; a
workspace default puts keys where the shell reads them; a path in `models.yaml` is one
machine's path in a shared file. No setting, or no such entry, drops the endpoint as a
missing variable does; a named file that is missing, unreadable, not a mapping, or holding
a key YAML reads as a number refuses to start. Moving every other `KINGFISHER_*` setting
into YAML is a separate argument, not made here.

**`doctor` says where each key came from and who else can read it, and refuses
nothing.** `credentials` names each endpoint's source. `keys file` says whether the
running fence lets the shell read the file, by each fence's shape: on macOS anything
outside the home folder is readable, so a workspace-only check would call `/tmp` safe.
`keys file mode` names a file other users can read. `starting environment` names a key
present before kingfisher started, on macOS or with no fence, where the shell reads a
process's starting environment -- knowable because the command line records the names
`.env` added. A line in the guide is not a guard, and this exposure is one a deployer
cannot otherwise see.

**Reversed: no keys example.** Renaming one in place puts real keys in the workspace;
with `keys file` reporting exactly that, `seed` places `keys.yaml.example` beside
`models.yaml.example`, with no live entry and the instruction to copy it out. The fences
these rules lean on are measured in `guides/configuration.md`, under *What each fence
hides*. *(2026-10-11, `where-the-command-line-keeps-its-keys.md`.)*

**A refusal about a credentials file says where it failed, not what was there.** PyYAML
reproduces the line it stopped on, so `not valid YAML: <error>` printed the key on it --
measured on three shapes a person types. The complaint and a line and column are kept,
the content is not; `models.yaml`, read the same way, took the same change. The test that
should have caught it used a harmless value.

**The keys file is protected from the shell where `models.yaml` already was.** A shell
able to edit it picks the key the next start sends, and one able to make it unparseable
refuses that start. Named in `shell_confinement`, not `authored_files_for`, because that
mapping also decides what `ensure_layout` scaffolds and a keys file must never be written
for a deployment that did not ask. Linux needs nothing: its writable set is the session's
own directories. `protected_files` exists only in the macOS profile, where the whole
workspace is writable. *(2026-10-13, from an architecture review.)*

**A model's window is declared under `extra: profile:`, not as a key of its own.**
`profile` is a constructor argument of every langchain chat model and `extra` already
hands a class what this format does not define; `max_input_tokens:` would be a second
spelling of one argument. The template sets it on `MiniMax-M3`, which langchain has no
profile for, and where deepagents' summarizer otherwise falls back to 170,000 tokens and
six messages on a 1M window. It replaces langchain's profile rather than merging, which
would be a special case in `build_model` for a model no shipped row declares one on.
*(2026-10-13.)*

**A row that reserves the whole window for output is refused when the model is built.**
deepagents takes `max_tokens` off the advertised input window, so `max_tokens: 190001`
against 200,000 leaves a budget of zero and every turn is refused unsent -- measured as a
`ContextOverflowError` naming no model, row or number, reaching the terminal as a
traceback. It is a `ConfigError` from `build_model`, printed as one line. Refused rather
than warned, because no request of any size can be sent. The window comes from the
provider package's profile, which moves (once from 1,000,000 to 200,000 under this
project); a row failing to start today beats every turn dying. A `ContextOverflowError`
from input that genuinely does not fit is still a traceback: catching it in the command
would grant `cli` a langchain dependency, which `THIRD_PARTY` makes somebody argue for.
*(2026-10-14, from an architecture review.)*

**The shipped MiniMax rows reserve 32,768 and 16,384 output tokens, not 4,096 and
2,048.** Across sixteen real MiniMax-M3 turns, 21 model calls stopped at exactly 4,096.
All 19 that called a tool hit it before the argument was written and came back `Field
required` (one session retried a whole-file write a dozen times); the other two were
answers cut mid-sentence and reported complete. MiniMax caps M3 at 524,288 and suggests
131,072; the template stays well under because deepagents summarizes at 95% of the window
less `max_tokens` as well as at 85% of the window, so above a tenth of the window it
summarizes sooner than the window says -- a test holds the row to that. The code's
default stays 4,096, since it applies on any vendor, including a small local endpoint
that would refuse a large reservation. *(2026-10-16, from the transcript-clearing trials.)*

**A reply cut off at the output cap has its cut call refused or its answer asked for
again, and three cut replies in a row stop the agent.** Nothing in the arguments shows a
cut: MiniMax-M3, live at caps of 60 and 1,500, sent a cut call with its JSON closed and
`write_file`'s `content` stopping mid-word, and langchain's streaming repair does the same
for any provider. Only the stop reason shows it, and `ModelReplyMiddleware` reads it.
Only the last call can be cut, so the others run and it is answered "Not run" with a way
round, writing in parts; where a provider puts no calls in the content, the last is
refused anyway, since a refused call costs a retry and a cut one saves a shortened file.
Its arguments over 500 characters become notes of their length, or the cut text is resent
on every later call. Three in a row stop the agent, each having spent the whole
reservation -- in a row, not per turn, so writing in parts may hit the cap now and then.
Cut replies are counted in the saved conversation, not the request, since the step
budget's warning goes last in the request and ended the count at none: a 10-call subagent
near its budget was asked twice more after its third. The main agent's turn ends
`max_tokens`; a subagent's answer goes back headed as cut off and the turn goes on,
because the caller can work round it. Each cut is marked on the `model_call` events, the
only trace of a cut the agent got past.

**Not taken: retrying quietly with a higher cap** -- it overrides `models.yaml`, spends
without asking, and fails at the model's own maximum. Nor standing text in `system.md`,
paid on every call. It sits inside the model and tool calls because a hook beside them is
a graph step, which the recursion limit counts. A subagent the workspace compiled itself
gets none of it. A cut call behind an approval gate still goes to the reviewer; approving
it runs nothing, and the two rarely meet.

A cut final answer is asked for again, shorter, anything long written under the outputs
folder first and named only once written -- measured at a 2,000-token cap, looser wording
had the agent name files it never wrote. Shorter rather than continued: stitching is new
logic, a continuing model repeats itself at the seam, and a cut 32,768-token answer
belongs in a file anyway. Neither the cut answer nor the request enters the conversation,
which would resend them; a cut call too broken to parse is asked again the same way, as a
call with no result beside it is refused by the provider. These hidden attempts still
count toward three, and since their text has already streamed, a `model_call` event goes
to langgraph's `custom` stream at the cut. *(2026-10-17, from a design session and live
calls.)*

**`doctor` reports a row that cannot start a turn, by building the model.** Reversed: it
was held that building costs ~700ms of provider import against 17ms for kingfisher, and
`test_nothing_here_calls_a_model` forbade it. Measured wrongly: `deepagents` imports both
provider packages itself, so the marginal cost is nil and construction 0.03-0.18ms;
`doctor` already built the default model through `inventory`; and the rule patched
`models.build_model` while the caller imported it directly, so it never bit. Deleted
rather than repaired. `inventory` carries the failure as `build_error` rather than letting
it escape, because one bad row had made `doctor`, `list`, `sessions` and `reap` all exit 2
with no output. The startup probe that dragged in `sessions` and `reap` answers with
`CapabilityError`, so only that is suppressed there; a turn that needs the model still
refuses, naming the row. *(2026-10-15, from an architecture review.)*

**A provider that cannot answer now stops the turn, and a subagent's tells its parent.**
A 429, a 5xx (Anthropic's 529 among them), a timeout or a dropped connection escaped `run`
as two tracebacks and exit 1 -- on 2026-10-17 a plan's rate limit took 16 of 21 parallel
turns down that way. These may pass, so the turn stops as `model_unavailable`, the
provider's words on its `cut_short` event and its output kept; running again carries on.
A 401 stays a configuration error; other 4xx and bugs keep their traceback. Recognised on
the model call by status code and type names through the causes, so no SDK is imported
and a tool's own timeout is not taken for the model. A subagent's comes back as a failed
`task` result, because the parent may go on without that part. **Not taken: retrying in
kingfisher.** The SDKs retry twice with backoff already; a second layer multiplies, up to
nine requests for one call against a quota, and no measured failure has outlasted their
window.

## What a tool returns

**A workspace tool's return type is langchain's rule, and kingfisher adds none.**
Nothing reads a return annotation, and `GuardedCallsMiddleware` catches exceptions and
passes values through: a dict reaches the model as `json.dumps`, and a `Command` is
applied by the graph, not wrapped.

**Refusing the `Command` was considered and rejected**, and will be proposed again. The
check is five lines. The loader's refusals each catch a *near miss* that yields a
successful wrong answer -- `TOOLS = [Shout]` for `[Shout()]` loads and answers with a
repr. Returning a `Command` takes an import of `langgraph.types` and means it; a rule
would make kingfisher a second name for someone else's contract, which `guides/tools.md`
refuses to be. Documented instead: a `Command` writing its own message leaves its
`tool_result` event naming no tool, and a `files` update reaches nothing, as the file
tools are on a real filesystem. `findings.md` has the measurement and
`tests/unit/test_tool_returns.py` pins it, so a langchain change lands there.
*(2026-09-04.)*

**A tool that creates a file saves it and returns where it went, never its contents.**
Returned, a chart or export is its whole body in the model's context and nothing the
caller can open; saved, it is one line. Output that only runs long is middleware's, which
deepagents evicts to `/scratchpad/large_tool_results/`. `guides/tools.md` has the recipe,
and `bar_chart` and `csv_filter` follow it.

**There is no kingfisher helper.** A tool calls `runtime.context.backend.upload_files`
itself, deepagents' documented call, in about ten lines. A helper would have guarded the
one silent mistake: a refused path comes back on the response's `error`, not raised, so a
tool that does not look answers "Saved" for no file. That is held instead by the recipe
carrying the check in its code, and by tests pointing each shipped writer, and the guide's
example, at `/inputs/` and expecting a failure.

**`save_as`, never `path`.** Optional: left out, the file goes to `/outputs/`; given, it
is a full virtual path the session's rules may refuse. `path` is taken -- kingfisher
rewrites an argument called `path` to the host file a tool *reads*.

**An existing file is overwritten without a word.** Saying "replaced" costs a lookup
before every save; the agent chose the path and can `ls` first.

**An image is returned as a path, never as image blocks.** A block is resent with every
later model call; `read_file` hands the image over when the model wants to look.

**No machine-readable copy of these facts.** The structured record is the ledger's, one
shape for every tool, under *What a turn's tools returned*; each docstring says what its
`save_as` does. These first shipped writers take no `path` and write through
`ToolContext` under the turn's rules, so *A tool's path is the backend's path* is
untouched. *(2026-10-12.)*

**A PDF is read as text a tool saves, not whole through `read_file`.** `read_file` hands
a PDF over as one block whatever `offset` and `limit` say, and a large one trips the
summarizer (`findings.md` has how). `pdf_text` saves the text under a `--- page N ---`
line per page, for the agent to read in ranges and `grep`.

**A tool, because a shell is not always there.** A request has a shell only where the
backend executes and it was granted one, and a remote sandbox has whatever its image
holds; a workspace tool runs in kingfisher's process and reads through `download_files`,
which every backend answers. It imports pdfplumber in its body, so without the `agent`
group only this tool fails -- a tool file that fails to import refuses the whole folder.

**`/scratchpad/` when not told, not `/outputs/`.** The text is the agent's working
material, not what the caller asked for. The pages are in the default name, so a second
range does not replace the first.

**Text only, five hundred pages a call.** The parse runs in kingfisher's process, outside
every sandbox: pdfminer is Python, while pdfplumber's `repair=` would run Ghostscript and
`.to_image()` native PDFium, so neither is used. The cap was fifty: asked for a figure on
page 120 of a 300-page report, MiniMax-M3 read 1-50, sampled 51-60 and 280-300, and said
there was no such figure. The text goes to a file, so the cap bounds only the parse: the
Federal Reserve's 222-page 2023 annual report took pdfplumber 11.1 seconds, 314 ms a page
at worst; pypdf took 3.6 seconds and got a little less text. *(2026-10-13.)*

**`pdf_text`'s `pages` defaults to the word `all`, not to the span `1-500`.** A span is
clipped to the document before the cap applies, so on a 503-page report the default
`1-500` reported nothing left out while `1-503` named the three dropped -- and leaving
`pages` out is what the docstring recommends. `all` gives the cap something to name.
*(2026-10-14, from an architecture review.)*

**And the pages the document does not have are named too, which the clip was
swallowing.** `_present` narrows a span to the document, which keeps `1-99999999` from
being a hundred million page numbers, and said nothing of what it cut: `pages="3-5,9"` on
a three-page report answered `pages 3 of 3`. Named as spans, for the reason the clip
exists; nothing is said below page one, which no document has. *The word `all` was held
by nothing either*: changing `EVERYTHING` to `"whole"` left the suite green while every
recommended call failed, so the rule reads the constant and sends it four ways a model
might write it. *(2026-10-15, from an architecture review.)*

**A shipped tool that promises CSV is written by `csv`.** `sql_query` joined values with
`","` under a docstring saying CSV, so a comma, quote or newline in a value returned
different data -- measured, three rows as four lines. It uses `csv.writer`, as
`csv_filter` does, and says its row cap in a trailing `--` line the docstring names.
`tests/unit/test_tools_that_read_a_database.py` drives the three tools that open a
database, which "does it import" had covered, and holds connections closed:
`with sqlite3.connect(...)` commits and does *not* close, so every call leaked one;
`closing` is what a read wants. *(2026-10-14, from an architecture review.)*

**A file in a tool's result the model may not take is replaced by a line saying what it
was.** `AttachmentsMiddleware`, in `standing_middleware`, reads every block carrying a
file in every tool's result -- a guard on `read_file`'s name is stepped around by a
workspace tool -- and in the message a `Command` carries. The rest of the result is kept,
and the call is an error only when nothing else came back. It sits inside the recorder:
outside, the recorder saved a 0-byte row for a replaced PDF and its stamp left the call
reading as a success. *(2026-10-13; images, other files, `read_file` and the turn's
totals 2026-10-19.)*

**PDFs up to half the graph's summariser trigger in a turn, known by their type in
whatever block carries them.** The trap is the conversation's size, not one file's: ten
200 KB invoices each passed a 256 KB limit, and from the third the summariser ran before
every model call. So a turn holds `pdf_allowance(summarizer_trigger(model))` of PDF, half
the trigger as the summariser counts a PDF, a token per four characters of base64, which
is three bytes a token: 249 KB with no profile, where the trap measured about 478 KB; 130
to 159 KB on a 128k window by its `max_tokens`; 185 KB for claude-sonnet-4-5's 200k at its
default 64k reservation; 332 KB for gpt-5; 1.2 MB for the template's MiniMax-M3. One PDF
past it is past it. The graph's own model, because one figure for all was more than a
128k window lets through and a fraction of a 1M one. Counted from the graph's saved
conversation, what the summariser has folded away included, so it can come out high and
never low; it starts each turn at nothing, since an earlier turn comes back as its
results' text. Bytes, because the summarizer counts them and pages would mean parsing every
PDF a tool returns. By type, because a 1 MB PDF in an `image` block, or inside a
`Command`, was handed over whole.

**An image over 5 MB of base64, or past 10 MB of them in a turn.** 5 MB is what a provider
measures, and the smallest limit: Bedrock and Google Cloud take 5 MB an image, Anthropic's
API 10. Past it the request is refused, and every later one while the image stays in the
conversation. The summarizer is no bound, counting an image at a flat 85 tokens: ten 6 MB
images in one turn sent 84 million characters and it never fired. The total is half the
20 MB Bedrock takes in a request, counted as the PDFs are: four images of 4.5 MB each
passed alone, and together put 18 MB in every call after them.

**Calls asked for together count what each other handed over.** Each runs against the
conversation as it stood before any of them answered: three reads asked for in one reply
each saw no other's result, on three threads in a sync turn, and three 100 KB PDFs went
out whole. So the guard keeps what it handed over for calls not yet in the conversation,
under a lock, by the message that asked for them, and drops it at the next model call of
the run holding that message. By message rather than since the last model call: two
`task` calls to one subagent run two conversations through one instance, as a pre-built
graph runs every session, and one run's model call would wipe what the other's calls in
flight had handed over. Kept apart, each run is held to its own conversation, which is
what its model is sent. Which call of a batch is stopped is the order they finish in.

**Any other `file` block, whatever its size.** A 2 MB `.pptx` was counted as about
700,000 tokens, then dropped from the request by deepagents'
`UnsupportedContentMiddleware`, which told the model under `read_file`'s name that it
does not support file content, whichever tool returned it. Only OpenAI's Responses API
is sent one, and the summarizer counts it there too. A reference by URL or file id
carries nothing to count, and passes.

**`read_file` is not run on audio, video, or a `.ppt` or `.pptx`.** The kinds are
deepagents' own reading of the extension, which counts `.mkv` as video. Langgraph turns a
block list holding audio or video into one JSON string before any middleware sees it, so
a 4 MB `.mp3` reached the model as 5.6 million characters of text, and only refusing the
call stops it; a `.pptx` is the block above. The refusal says to call a tool that reads
that kind of file, if the model holds one.

**It names `pdf_text` only where the model is offered it.** `standing_middleware` is
handed every tool the workspace defines, granted or not, so the guard reads the tools off
the model's own request instead. A call replayed on a resume follows no model call and
gets the advice with no tool named.

**What it leaves.** A PDF small in bytes and long in pages still goes whole. A subagent
the workspace compiled itself has no kingfisher middleware, so neither its `read_file`
nor its tools are guarded. *(2026-10-13.)*

**`pdf_pages` saves a page range as a smaller PDF, for a model that reads PDFs.**
`pdf_text` leaves out a chart, a table's layout and a scanned page; a slice small enough
is a PDF `read_file` hands over whole. MiniMax-M3 sees one drawn: handed a page holding
only a red rectangle, as a turn sends it, it described the rectangle, its colour and
place. MiniMax-M2.5 read the text and found that page blank. MiniMax's API reference
lists no document block for either; the live call settled it.

**A slice past 249 KB is refused, not saved**, what a turn holds on a model with no
profile. Saved, `AttachmentsMiddleware` may replace it with advice, a file the model is
told to open and never sees. The guard's own limit follows each graph's model and what the
turn already holds, neither of which a tool can know, so this is the figure it applies
with nothing declared: on a smaller allowance the guard replaces a slice with advice
saying so, and on a larger one the model asks for fewer pages than it needed to. The tool
keeps its own copy, since a shipped tool is written against the public API; a test holds
the copy to the guard's rule.

**Copied, not drawn.** Drawing is PDFium, native code in kingfisher's process outside
every sandbox, and a model that reads PDFs draws the page itself. pypdf copies pages and
is Python. *(2026-10-13.)*

**A tool that reads a file caps what it reads, and `pdf_text` releases each page as it
goes.** A backend hands a file back whole, the parse runs on the turn's thread outside
every fence, and `KINGFISHER_TURN_TIMEOUT_S` is read *between* stream chunks; a thread
cannot be interrupted mid-parse, so the bound is set before the work starts. The cap is
32 MB, a per-module constant because these are examples copied one file at a time, which
is why `test_a_tool_reads_a_file_one_way` compares the copies. A bound, not a prediction:
a CSV costs by the *cells* sampled, not the bytes -- 32 MB of two-column rows peaked at
358 MB, the same 32 MB over 4,000 columns at **998 MB**, 16.8 million strings at about 50
bytes each. A gigabyte is survivable where unbounded is not.
*(2026-10-15, from an architecture review.)*

**The rule reads every file under the assets, pinned by path rather than counted.** A
walk of the tool catalogue missed `subagents/redactor/tools/mask_secrets.py`, a
subagent's own tool, which shipped reading a file uncapped with every rule green. The set
is pinned in `FETCHES_A_FILE`, with no `len(...) >= 2` threshold beside it.
*(2026-10-15, from an architecture review.)*

**Bytes were the wrong bound for the PDF, and measuring showed it.** 1.6 MB of dense
pages peaked at **1,655 MB**, because pdfplumber keeps each page's objects and text map
until the document closes; the byte cap and timeout a review asked for would not have
touched that. Releasing each page -- `flush_cache()` and `get_textmap.cache_clear()`,
both, since either alone left 1,020 MB or 1,626 MB -- took it to **93 MB** and a quarter
faster, and end to end the tool grew the process 12 MB on 500 dense pages, so
`MAX_PAGES` stayed. pypdf needed nothing: 2,000 pages copied in 0.16s and 109 MB.
*(2026-10-14, from an architecture review.)*

**`mask_secrets` could be made to spend days on one line, and answered for a fifth of
a file as though it were the file.** It is the shipped tool written for content nobody
trusts, so its input is the attacker's. Its address pattern
`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` holds `.`, `-`, `+` and `%`, each
starting a fresh `\b` the class consumes through, so a run with no `@` backtracked from
every position: 128 KB of `1.` took 7.25 seconds and 1 MB 472, clean n², about five and a
half days at the tool's 32 MB, on the turn's thread. European-formatted numbers and SNMP
OIDs reach it. Every repetition is bounded now by RFC 5321's 64 octets for a local part
and 255 for a domain: linear, 440 times faster at 64 KB, the same addresses matched.
**Rejected: possessive quantifiers**, the fix a reader reaches for, measured wrong twice:
`[A-Za-z0-9.-]++\.` cannot give back the dot, so it matches nothing, and the cost stays
n², since what repeats is the start positions.

**And the line cut was silent, in the tool whose whole purpose is not being silent.**
`[:max_lines]` ran before counting, so a thousand-line file with its credential on the
last line answered `200 line(s), 0 masked`, which `skills/redaction/` tells the subagent
to read as nothing sensitive. The header says `200 of 1001` and names what it skipped,
and `max_lines` is in the docstring. Raising it is this subagent's only remedy -- it holds
no `read_file`, deliberately -- so it has a ceiling and the answer a bound in characters,
as `sql_query` has: lines are not characters. A non-positive `max_lines` is refused, not
read as `0 line(s)` or `[:-1]`. One `sk-live-123` had been the whole coverage; each
pattern now has a rule and a time bound driven on its own worst shape.
*(2026-10-16, from an architecture review.)*

**Four more shipped tools: two that did not bound what they spend, and two that let a
file's own text into an answer.**

**A header cell forged a report row.** `Column.line` formatted the name into a
one-line-per-column answer, so a header cell holding a newline and a plausible line
reported a column nobody wrote, and `csv_columns` returned an extra name. Escaped rather
than stripped, so the name still says what the file holds.

**Four tools died on a cell 0.6% of their own cap.** `csv.field_size_limit()` is 131,072
against a 32 MB read, and the model got `Error: Error: field larger than field limit
(131072)` -- no file, column or remedy. Each says the three now, in its own file. Not
fixed by widening the limit, process-wide state an example tool has no business setting.

**`sql_explain` bounded rows and not characters**: a two-million-character alias came
back whole. It carries `sql_query`'s number and marked `--` line.

**`log_levels` capped what it printed and not what it held.** One `Counter` per distinct
minute: 883,011 minutes in 19 MB peaked at 506 MB against 199 MB in one minute. Fifty
thousand are tracked, about 18 MB, and the answer says how many lines fell outside, as a
doubt about the list, because the minutes kept are the first met, not the loudest.
`status_codes` keys on a three-digit status and cannot pass a thousand; a rule measures
it.

*What the guards taught.* A rule whose fixture is sized from the constant it tests cannot
survive that constant being broken -- a billion-line cap built a billion-line fixture --
and one that patches its cap down leaves the shipped number held by nothing, so both
tools carry a separate floor. *(2026-10-16, from an architecture review.)*

**`http_fetch` bounded nothing, guarded the wrong URL, and read every page as UTF-8.**
Each driven against a loopback server written for the purpose:

  - **`timeout=` is a socket timeout**, per operation: a server sending a byte every two
    seconds was still being read after ninety, and whoever the URL names decides how long
    that is. A deadline covers the whole call; the socket timeout stays for a connection
    that sends *nothing*. `read` blocks until it has every byte asked for, so the deadline
    is checked between `read1` calls, which return what has arrived.
  - **The scheme check guarded the URL the model wrote.** urllib's redirect handler
    permits `ftp:`, and a 302 fetched `ftp://ftp.gnu.org/welcome.msg`. A redirect handler
    of its own refuses it.
  - **The body was decoded as UTF-8 whatever the server said.** The declared charset is
    read, and one this machine lacks falls back rather than raising `LookupError`, which
    would let the server decide whether the tool fails.

*A rule about a bound cannot assert the elapsed time afterwards*: unbounded, the call
never returns and the rule hangs. Both time rules run the fetch on a thread and fail if
it has not answered, telling a raise from a hang. *What this leaves*: the tool will still
fetch `http://169.254.169.254/` or anything in its network, which a deployment exposing
it must decide; it is an example of a narrow grant, not of a safe one.
*(2026-10-15, from an architecture review.)*

**Three shipped tools answered wrongly about a file a real deployment holds, and the
bound was never the one the row count suggested.** Each found by handing the tool the
input.

**`csv_profile` merged two columns that share a name.** Keyed by header name,
`region,sales,region` pooled both columns while `csv_columns` listed three. Gathered by
position, a repeated name carrying its column number, the only place the distinction
lives; a row wider than the header is counted, not silently cut.

**`bar_chart` wrote files no viewer can open, and said it had saved a chart.** `inf`
passed the negative check and made every bar `n/inf` (`nan` for itself); `nan` and
`1e400` arrive the same way, so the guard reads the number and refuses it as a negative is
refused -- neither has a length. `escape` handles markup only, so a NUL went into an SVG
XML 1.0 forbids it in; such characters are dropped and counted.

**`sql_query` bounded rows and nothing else.** `SELECT b.a FROM big b, t LIMIT 200` on a
20 MB database returned four billion characters, 3.3 GB resident; a four-table join on
1,000 rows takes about 14 hours that nothing could interrupt. `docs/guides/tools.md` has
the reasoning under *A tool that runs a query*. Time is SQLite's progress handler, the
one thing that stops work under way, reported naming the bound, which `OperationalError:
interrupted` does not. Width is one number used twice: rows stop at it with a marked `--`
line, and a single value past it is refused by column with `substr` suggested -- read per
value before conversion, a row at a time, because that is where the memory went.

**Not changed: `WITH` is still refused.** The guard admits a statement starting `select`,
so a read-only common table expression is refused. The time bound would make admitting it
safe, but widening what the tool grants is its own decision, not one to ride along with a
fix. *(2026-10-15, from an architecture review.)*

## What a turn's tools returned

**Every tool result the model is handed is recorded, kept as a file and given an id,
and kingfisher writes the record, never the model.** One ledger, two readers: the model
reads a rendering of it in the user message, the caller reads the turn's rows on
`RunResult.ledger`. It exists for what goes wrong once deepagents summarises a long
conversation: an earlier result is somewhere in one history file with nothing saying
where it starts, and the summary's list of what was produced is the summarising model's
memory of it. Settled with the owner question by question; this is the first stage of
the owner's plan, *Artifact provenance for conversational agents*, kept outside the
repository. *(2026-10-12.)*

**What is recorded: every successful result but the file tools' and `write_todos`.**
`ls`, `glob`, `grep`, `read_file`, `write_file`, `edit_file` and `delete` touch what is
a file already, and a row each would bury the rest. `write_todos` rewrites one list the
conversation already carries: measured, three updates beside one real search made three
of the four `<ledger_delta>` lines a todo list. `execute`, `task`, workspace and MCP
tools are recorded. A failed call has nothing worth finding again, and the conversation
shows it. A subagent's results are *working* entries under the parent's `task` call,
nested helpers included; its answer is a *main* one. A result's attachments, an image or
a PDF, are not kept: its file is its text, and its row's summary says what was left out
and how large it was.

**Middleware, not the stream.** The stream reaches further, to a compiled subagent's own
calls, and needs no lock, but it can only watch, and the ledger changes the result: the
id stamped on it, perhaps a handle later. The stream also replays results after a
superseded pause and carries results the approval step made up, each needing a filter on
how langgraph shapes its chunks. Middleware sees only real calls.

**The recorder is ahead of the guards in `standing_middleware`, so it records the
arguments the model wrote**, not the host paths `GuardedCallsMiddleware` makes of
them; moved last, `test_the_arguments_recorded_are_the_ones_the_model_wrote` goes red.
Its other two properties are not that position's: it sees a long result whole, inside
deepagents' eviction, because deepagents lands every custom middleware after its own;
and it records what the model was shown, a `ToolNote` included, because `build_agent`
appends a deployment's middleware after everything else. One collector serves every
graph a turn builds, locked because calls made together run on threads, and a context
variable names the `task` call a subagent runs inside; `findings.md` has it reaching
both loops.

**Ids run for the session, `A-` and at least four digits.** Not per turn, as the plan
had them: regeneration would reuse turn numbers, and a running number never repeats.
The id is the result's last line, or a text block holding `\n[A-0042]` after a block
list, which the transcript joins with nothing between; eviction's stub keeps the head and
tail, so it shows there.

**Each result is a file in `/scratchpad/tool_results/`, `A-0042-<tool>.txt` or `.json`,**
written as it arrives through the turn's backend. The scratchpad, because the agent reads
one back with `read_file` and searches with `grep`, no new tool, and it is swept with the
session. Not `results/`, which is the name anyone gives a folder of their own work; beside
deepagents' `large_tool_results/` the two read as a pair. A file the backend refuses
leaves the row's `path` null and the call succeeds: failed, the model may call again and
run the tool twice.

**An offloaded result is pointed at deepagents' copy, not kept twice.** Eviction runs
outside the recorder, so at the end of the turn one `ls` of `large_tool_results/`
repoints each entry whose call id is there, and kingfisher's copy goes in the sweep. A
nested helper has no eviction and keeps its own.

**A name is not an identity, and the size is what settles it.** deepagents names an
eviction after the call id alone, in a folder that outlasts the turn. Measured across two
turns, a reused id pointed the second row at the first result's file -- the row read
`A-0002 web_search -> 32 B`, the file held the previous turn's 101 KB -- and the sweep
removed the copy that was right. A crash cutting a row, one id twice in a turn, and an
agent and its subagent (two conversations in one turn, each with a `c1`) all reach it,
and nothing asks a provider for more uniqueness, so this is kingfisher's to detect. The
file deepagents keeps ends in the recorder's id line, so its length is known exactly: a
row is repointed only onto a file of that length, and a backend reporting no size is no
match, which keeps our copy, this call's whole result. A check against paths *earlier*
rows named was the first fix and went, being a guard no mutation could fail: two rows of
different turns match one file's length only if they are the same length, and then
deepagents has overwritten it and this turn's row is right.

**On a tie, neither row takes it.** Measured: two calls of one id, results of equal length
and different content, left the row for `subject='one'` pointed at the bytes of
`subject='two'` and the `one` result swept away, under a row still carrying its own size
and summary -- which `system.md` tells the model to read back rather than call again.
deepagents writes the *last* tied call into the file, measured, but that is a write order
nothing promises, so neither row takes it, as with no size; the cost is one extra copy in
a turn where a provider reused an id. The rule that should have caught it used two
identical `loud` results, so it could not fail; it is driven with results that differ in
content and not length now, beside a rule that a differing length still repoints.
*(2026-10-15, from an architecture review.)*

**Result files, then the ledger, then the transcript, then a sweep.** So a row never
names a missing file and a result the conversation shows always has a row; the other way
round, a crash leaves the next turn reading an id the ledger never gave out, and giving
it again. A paused turn writes its rows before its state. The ledger is
`.harness/ledger.jsonl`, rewritten whole at the end of the turn; not SQLite, as the plan
had it, which is for writers that interleave, and here there is one. A line a crash cut
short is dropped rather than refused, or it would refuse every turn after. The sweep
removes only a file whose name begins with a result id and that no row names: the agent
writes in the scratchpad too, and a sweep of every unnamed file deleted the agent's own
file in the turn that wrote it. Narrowed rather than refusing the write, because a draft
under `/scratchpad` is what the tools guide offers a tool author. One turn runs per
session, so there is no grace period. *(2026-10-13, from an architecture review.)*

**The recorder's own folder is refused, and that is not the same decision.** Only
`tool_results/` is write-denied to the file tools, `delete` included. A row names its file
and the agent must not rewrite what a later turn reads back as true: measured, the row
stayed at `size: 32` while the file held `forged`. Mounted only so the rule is
expressible, as `.harness` is, since deepagents builds no graph with a deny outside a
route on an executing backend; readable, since reading back is the point. **Reversed: the
tools' rules covering the recorder**, on the reasoning that a result file should be held
to what a tool's save is -- under that reading the folder could not be denied at all. It
writes through the turn's backend, so a deployment's own refusals still apply, and
`_unkept` reports one. *(2026-10-13, from an architecture review.)*

**The deny covers both folders a row can name, and the shell's half is the fences'.**
`large_tool_results/` was unrouted, so a plain `write_file` forged an evicted row's file;
it is routed and write-denied too, and deepagents' own eviction is not subject to the
rule. `execute` bypasses all of it -- measured, the file tool was refused and the shell
got in -- and the sweep cannot see an overwrite, since it skips every path a row names.
**Left as it is:** rows from before the move name `results/`, and nothing denies that; a
third permanent route to protect rows a version without this property wrote is not worth
it, with no layout version to move, so they age out with their sessions. *(2026-10-14,
from an architecture review.)*

**Two fences of three now refuse the shell both folders a row can name.** Once older
results are cleared to handles, the saved file is the only copy, so an overwrite is the
record rather than a forgery. `sandbox-exec` refuses both with the regex it writes for
`.harness`, and bubblewrap binds them read-only after the session. macOS needed one rule
more: renaming the scratchpad away, writing through the new name and renaming it back
beat a rule that finds the folders by position -- measured, `FORGED` and exit 0 -- so the
scratchpad's own node is refused writes, as `sessions/<id>` is for `.harness`. bubblewrap
binds only what exists, so `ensure_session_layout` makes both folders and `backend_at`
refuses a session without them. Landlock cannot while it grants the scratchpad whole, a
read-only grant under a writable parent being stepped over (`_session_writable`), which
is also why `shell_denied` does not ask a deployment's backend for it. Two of three is not
a boundary, so nothing claims one. *(2026-10-16, before clearing results to handles.)*

**The caller's inputs are refused the shell by each fence, not by their mode bits.** The
shell runs as the uid that owns them: measured through `execute`, a plain write is
refused, but `chmod u+w` and the same write replaces the caller's file, and `rm -rf
inputs` takes the folder, the only copy and versioned by nothing. So each fence refuses
it as it refuses `.harness`: a regex on macOS, a read-only bind in bubblewrap, and on
Landlock by leaving `inputs` out of the grant. Reads stay, and the rule asserts the rest
of the session stays writable, so a fence refusing everything fails it. **The contract
kit said this held when it did not**: `shell_denied` never lifted the bits, and its
control asserted `/inputs` absent from an unfenced shell's failure -- the belief stated as
a test. It lifts them first now, the `chmod` allowed to fail, names both folders, and
has a case where the fence holds so a kit raising for everything fails too.
*(2026-10-14, from an architecture review.)*

**Access is recorded and not acted on, because the session is the boundary.** A row holds
the audience its tool was granted under, and a `task` answer its run's audiences
together. Nothing filters on it: a session belongs to one caller, and every turn replays
a transcript that already carries the content, so filtering the ledger alone would be no
boundary. Callers who must not see each other's data use separate sessions. Serving
results only through access-checked tools was declined on the same ground, and because
the shell reads the whole session. It is kept so a later design need not find old rows
lacking it.

**No model-written summaries.** A row's `summary` is the tool, its short arguments, the
first line of the result, and any attachment its saved copy left out. A model-written
one costs a call per result and is the summary-from-memory the ledger exists to replace.

**What the model sees goes in the user message, never the system prompt.** The system
prompt is the cached prefix, and anything there that moves with the ledger misses the
cache on every call. `system.md` carries fixed text, *What your tools returned*, to the
agent alone. A new message carries `<ledger_delta>`, the rows since the previous user
message, a subagent's indented under its answer so the parent sees where they are and
never what they hold. A resume adds no message, so *since* reaches back past it. No block
when nothing was recorded, on a resume, or for a graph the deployment supplied.

**A subagent reads the id on its results and is told nothing about it, and that is left
as it is.** Measured, the agent's prompt is 9,543 characters and explains the id; a
subagent's is 1,902 and does not. Two deliberate rules meet: every graph is stamped, and a
subagent gets its own text and nothing of the harness's, asserted byte for byte. The
stamp is the agent's handle on delegated work. Explaining it to a subagent puts harness
prose in every subagent's prompt, half of it about blocks a subagent never receives; not
stamping leaves an evicted subagent result nothing can find, since grepping for the
`[A-0042]` line is how one too long to show is found. *(2026-10-13, from an architecture
review; the alternatives were built far enough to price them.)*

**After a summary, the index replaces the delta, and nothing estimates size.** The delta
lists only what is new, and the messages that listed the rest are what the summary
replaced. So when any turn since the previous user message ended on a summarised
conversation, the message carries `<ledger_index>`: the last five exchanges that recorded
anything in full, each older one as its id range and a count by tool. An exchange is a
turn that carried a message and the resumes after it, so a paused `task` and its answer
are never split. Shown on the summariser's own mark, not an estimate of size, which would
copy its threshold and drift when the model or settings change.

**A turn's closing line carries two marks, each written only when true.** Every turn
writes `{"turn": ...}`, even one that recorded nothing, because turn ids are not a
sequence. `resumed` is how the delta and index find the last user message. `summarised`
marks a turn whose final state holds deepagents' `_summarization_event`, read where
`_settled` reads the pause because `invoke` leaves private state out; a resume restores
the event with the checkpoint, and counts too.

**`ledger_*`, not `artifact_*`.** The owner's notes said `<artifact_index>`; in kingfisher
an artifact is a file under `/outputs` or `/memory`, as `RunResult.artifacts` lists, and
the word would mean two things.

**Older results and file text are cleared to handles once the agent's conversation is
large.** Before each model call on the main agent, once the conversation reaches a high
mark and a quarter of it could be freed, the oldest saved results, file tools' results
and the text inside `write_file` and `edit_file` calls become handles until it is under
half the mark. A recorded result keeps its summary, size and id and names its file; a
file tool's names the call that gets it back; a write or edit says the file holds the
current text. The latest batch and the three most recent clearable results stay whole.
Settled with the owner after four real sessions were measured. *(2026-10-16.)*

**The recorder and clearing stay two classes**, because the recorder runs alone on every
subagent; what they share is how a record is written -- a row's arguments and summary,
and the id a result ends in -- in `core.agent.middlewares.recorded`. *(2026-10-19.)*

**The scope is what was resent, not what was saved.** Of what four four-turn MiniMax
sessions resent per call, ledger results were 14.7%, file reads 36.5%, write and edit
text 30.7%. Those last have the file, so their handle points at it and says it may have
changed; a snapshot would be a second store to write and sweep. Inline `execute` scripts,
what the user and agent said, and `task` answers exist nowhere else, and a failure is
what stops the same call being made again, so they stay whole. The transcript keeps no
status, so a replayed failure is known by its wording.

**Inside the model call, replacing in place, writing nothing.** A pass clears the request
and returns the handles as a state update, so every later call and the transcript are
built from the cleared conversation. **Reversed: a `before_model` hook.** It is a graph
step, and the acceptance run measured it: the recursion limit's 150 steps bought 37 model
calls instead of 50, and a writing turn stopped there. Inside the call the summariser
counts the uncleared conversation on the one call a pass starts on, which with the
default mark at most half its trigger only a very large batch reaches. Topping up the
limit, and wrapping the summariser, were the other options; the owner chose this. Each
message keeps its id and position, since the summariser's cut is an index. No file is
written, so reading a handle cannot start another pass, and a handle is never cleared
again: read as what it replaced, an offloaded result's was pointed at a swept copy.
*(2026-10-16, after the acceptance run.)*

**An offloaded result is named by a search for its id, not by a path.** deepagents' copy
is named by call id, and a later call with the same id overwrites it, so a path would
name another call's result where a search finds nothing. A tool that offloads its own
output hands the recorder the stub, and is named by our copy.

**A handle names only a tool the agent holds.** The path goes to an agent holding
`read_file`, the search to one holding `grep`, and any other is told to call the tool
that made the result again. A model told to `grep` calls `grep` whatever it holds, and
without it spends the step on a refusal. One holding `read_file` but not `grep` is not
given an offloaded result's path, for the reason above. The grant is the one
`GrantedOnlyMiddleware` filters by, so a handle never names a tool it takes away. A handle keeps its wording once written, so a later request on the
session holding fewer tools may be pointed at one it lacks: it is refused once, told what
it holds. Accepted rather than re-worded per call, for a grant that shrank between turns.
*(2026-10-18.)*

**The marks.** The high mark is the model entry's `clear_results_at`, or else the smaller
of 64,000 tokens and half the summariser's trigger, read from
`compute_summarization_defaults` and never copied, so a small window still clears first.
The low mark is half the high. Replaying the 372 model calls of those sessions chose
them: at 64k, four passes, input tokens 72% and transcripts 60% of a run without
clearing, 82% of the cost at Anthropic's cache prices, each pass re-caching everything
after the earliest message it touched; 48k fired seven times, once on a session's last
call, which then cost 116%. Keeping two, three or five recent results made no
difference. The quarter guard stops a pass on every call once what cannot be cleared
nears the low mark: at a 20k mark one session fired 40 times without it and 12 with it.

**One setting, and no flag and no off switch.** The trigger moves with the model's
window, so the mark sits beside the model, and a flag would be a second place to look. A
mark at or past the summariser's trigger is refused when the model is built; the highest
allowed almost never clears, the nearest thing to off. The summariser also fires on the
last reply's reported usage, which this does not read. A model the deployment passes in
takes the default. The ledger is unchanged by clearing.

**Still open: the summariser re-runs every turn once past its trigger.** Its event is in
state the next turn's graph is not given, so each turn summarises again and appends the
same history once more. The fix designed writes the summary and the messages after its
cut as the transcript. Clearing makes the trigger rare on a large window; on a small one
it is the next thing to fix.

**Not built, and still open.**

- **A handle in place of a large result's stub.** A handle in place of *every* result,
  as the plan has it, was declined: most results are needed at once, and reading each
  back would double the round trips of every search.
- **Reusing a result for an identical call within a freshness window, declared
  supersession, and "derived from" links.** Each needs information nothing supplies.
- **Regeneration, which gets a design of its own:** rewinding or forking, what becomes
  of files already in `/outputs`, how a client names a turn. Every row records its turn
  so it can filter by one.
- **Calls inside `eval` and inside a compiled subagent,** which no hook sees; the
  compiled subagent's answer is recorded through `task`. Nor what the shell writes;
  `RunResult.artifacts` is the sound view of files.
- **Finding an old entry by anything but its id.** Its file is found by `glob` for
  `A-0042-*` under `/scratchpad/tool_results/` or `grep` for `[A-0042]` under
  `/scratchpad/large_tool_results/`, as `system.md` says and
  `test_an_older_results_file_is_found_by_its_id_as_the_prompt_says` drives. A rolled-up
  entry's arguments and summary are in the ledger, under `.harness`, out of reach; if
  that proves too little, the index could be written as a readable file.

## Tool failure

**A workspace tool's exception is a failed tool result, not a dead run.**
`GuardedCallsMiddleware` converts it to a `ToolMessage` with `status="error"`, text whole.
Built-in tools are untouched but for a refused host path, the one failure they do not
report themselves; converting the rest would put a second opinion between deepagents and
its own error handling. `BaseException` is not caught: an interrupt is not a tool telling
the model something. Measured first: the same wrong path cost nothing through
`read_file` and killed a sixteen-call run through `csv_profile`, depending on which tool
the model reached for. Subagents and their helpers get the guard too, since they can hold
the workspace's tools. `on_tool_error` still writes a `tool_error` event for a converted
exception, which is asserted. *(2026-08-18, `a-tool-failure-is-not-a-crash.md`.)*

**What a deployment's own middleware raises is not this guard's to convert, and it
cannot tell.** That middleware is appended last, below the guard: driven with one whose
`wrap_tool_call` raises, a workspace tool's call came back `status="error"` under the
tool's name, while on `read_file` the turn ended with the raw `RuntimeError`. **Left as
it is.** Telling them apart needs a tag below the deployment block, which means
interleaving `standing_middleware` with the deployment's in each of the three graphs
pinned to its order, and carries a decision rather than a fix: whether a hook that falls
over should end every turn or none. The middleware guide says what happens today. The
same placement hands a hook host paths: the model wrote
`line_count(path="/inputs/n.txt")` and the middleware saw the session's absolute path --
the opposite of what the recorder sees by its position. *(2026-10-16, from an
architecture review.)*

  - **Decided: it ends every turn.** A hook that falls over has no way to tell the model
    about a fault of its own, so the quiet answer blamed the tool for it; and a rule that
    did not run is not a rule, so carrying on is the worse of the two. The conversion was
    the half that could not tell, so it moved rather than gaining a tag:
    `WorkspaceToolFailureMiddleware` answers for what a workspace tool itself raised and
    goes under the deployment's block, while `GuardedCallsMiddleware` keeps the
    translation and the host-path and `task` conversions in the outer place, where a
    `grep` run as several searches still fails once. Both are in `guarded_calls.py`, so
    the two catches are still read together, and `GuardedTool` still answers alike.
    `standing_middleware` now takes what goes **inside** it rather than each caller
    appending afterwards, so the one layer that has to stay innermost cannot be left off
    a graph; the three graphs pass their switched and declared middleware as `inner`. A
    hook that wants a call to fail softly returns a failed result instead of raising,
    which the guide now says. The host-paths half of the entry above is still open and
    untouched. *(2026-10-19.)*

**A call refused before it runs is answered by the middleware that refused it; what
running it raised is the guards' that convert.** Its translation is caught around
itself alone, as `GuardedTool`'s is. Catching everything below it, the translator
answered a built-in's host-path refusal first on any graph holding a workspace tool,
appending its rule after a doubled full stop, and gave a workspace tool's own refusal
that rule in place of its type. Each guard's tests passed alone; only the stack showed
it. A `ToolException` goes back as written on both routes, being the one exception a
tool writes for the model.
`test_a_workspace_tool_fails_alike_through_the_stack_and_on_a_compiled_subagent` holds
the stack to `GuardedTool`'s answers, which `SessionPaths` says must be the same. The
`task` hand-back of a subagent's refused model call is here by the same rule, and
`ModelReplyMiddleware` keeps refusing a cut call. *(2026-10-18.)*

**One middleware rewrites a workspace tool's paths and converts what any call raised,
as `GuardedTool` does on a compiled subagent's tools.** The middleware and the tool must
answer identically, and as two classes the middleware's two catches had already
disagreed, in the entry above. `GuardedCallsMiddleware` keeps both, the translation's
around itself alone, and sits where the conversion sat, outside
`BuiltinToolsMiddleware`, so a split `grep` whose path is refused fails once
(`test_a_split_grep_whose_path_is_refused_fails_once`). That moved the translation out
past `AttachmentsMiddleware` and `BuiltinToolsMiddleware`, which read no workspace tool's
arguments: the guards' tests passed with only the class's name and the pinned stacks
changed. *(2026-10-19.)*

**A workspace tool is handed real paths, resolved against its own session.**
`GuardedCallsMiddleware` rewrites a `path` argument before the tool sees it, so
`/inputs/config.ini` means to a workspace tool what it means to `read_file`; how is under
*A tool's path is the backend's path*. A workspace tool opens files with the operating
system, and nothing gives it the backend the file tools close over. It closed a leak too:
without it the model learned the host layout from the shell, and from there
`line_count('/workspace/sessions/<other>/secret.txt')` answered. Now that argument
resolves under *this* session and finds nothing. Links are resolved on both sides, since
a link the shell made to another session was measured returning `TENANT-A-PRIVATE`
through a tool while `read_file` refused it. The call is rewritten rather than each tool
wrapped, because `@tool` objects and plain functions share only the call.
`PATH_ARGUMENTS` is `{"path"}`, the convention shipped tools follow.

**Corrected: an argument with another name could reach outside the session.** A tool
taking `input_file`, handed another session's real path, returned `TENANT-A-PRIVATE`.
Every other string argument of a workspace tool call is refused when it names a host
path -- the file tools' refused roots, `/root/`, `/proc/`, `/sys/`, `/dev/`, or the
sessions directory, which catches `/workspace/sessions/...` in a container. Defence, not
a boundary: a relative path, or text a tool turns into a path, still reaches it.
`tools.md` says only `path` is translated. *(2026-09-17.)*

**Still open from it.** Whether a tool failing repeatedly with the same argument should
be taken away rather than left to the recursion limit. Nothing needs it yet, and a rule
that removes a capability mid-run deserves its own argument.

**A caller's tool can be handed the turn's backend, under the turn's permissions.** The
graph is built with `context_schema=ToolContext`, every turn is driven with one, and a
tool declaring `runtime: ToolRuntime[ToolContext]` reads `runtime.context.backend` in the
virtual paths the model writes -- for the tool that lists, searches or writes, where the
`path` translation serves one that opens a file. The run context rather than a closure,
because a tool is imported once and a backend is built per turn; it is the one value
that is per run, reaches every tool, and is never shown to the model.

**Wrapped, because the backend keeps none of the rules.** deepagents applies
`permissions=` inside its file tools and nowhere else: measured on the default backend, a
write to `/skills/x/SKILL.md` landed in the shared catalogue and `/.harness/run.jsonl` was
read back whole. `PermittedBackend` puts the turn's rules in front with deepagents' own
private helpers, since a second matcher is the copy that drifts. It answers as the file
tools do and no more strictly. It differs in three ways, each because a call inside a
tool is not a file tool call:

- **`interrupt` is a refusal**, since nothing pauses for a call in a tool's body and
  deepagents' filters pass `interrupt` entries through. None is written today; this is so
  the first is not a hole.
- **There is no `execute`**, which no rule reaches.
- **A refused path is an `error` on the result**, the convention every other failure of
  these methods follows.

**Every turn, and a resume is a turn.** langgraph keeps the context out of the
checkpoint: resumed without it, a paused graph ran the approved call with
`runtime.context` as `None`. So it travels with the keywords both loops are driven with.
**A graph the caller built is driven with none**, the keyword left off rather than
`None`, since its context is its builder's.

**It costs an author two rules, documented rather than guarded.** The argument must not
be called `path`, which is rewritten to a host path the backend does not take; and the
tool is a function, since a `BaseTool` subclass with its own `args_schema` is called
without `runtime`. Each fails on the first call naming what went wrong, and a test pins
each. `ToolContext` is on the front door apart from the wrapper, so naming it does not
load deepagents. **Not a sandbox for the tool:** a tool's own `open()` meets nothing.
*(2026-09-30.)*

## Confining the shell

**The two fences share a toolchain and not a workspace**, and the asymmetry is the
design. `sandbox-exec` is `(allow default)` with denies: it takes away the home and hands
back what must stay reachable, so `readable_roots` names the workspace. Landlock and
bubblewrap are allow-lists handing back one session, so naming the workspace there would
return every other tenant's -- measured before the fence existed, tenant B's
`cat ../<A>/derived/secret.txt` got `TENANT-A-PRIVATE`. So `toolchain_roots` is the shared
half, and having `fence_for` call `readable_roots` -- tidier, three lines shorter -- would
reopen that hole. Without it the Linux side granted nothing for the venv `shell_env` puts
first on `PATH`, and an allow-list skips an ungranted entry rather than refusing it: the
shell ran `/usr/local/bin/python3`, without the agent's libraries.
`test_every_directory_on_the_agent_s_path_is_reachable` states the property, so a path
added later by another route is caught. *(2026-09-04.)*

**A definition is not the agent's to edit.** Every directory in `catalogue_roots` is
denied to the confined shell, not only `skills/`. `tools/` sat in the writable workspace,
and `LocalToolRepository` *executes* its modules each time a `Kingfisher` is constructed:
measured under the profile a deployment builds, a write to `skills/` was denied, one to
`tools/` succeeded, and a fresh repository ran the module-level code of the file. The
other kinds are denied one step along: an agent editing its `agents/*.yaml` strikes out
the `source_ids:` line saying who may reach it, and `subagents/` and `middlewares/` hold
Python too. *Wiring a store* rests on the same premise, *"a file the agent could edit is
not a boundary"*. Nothing relied on the hole, checked four ways: no caller could add
tools, the file tools' catalogue routes are read-only, the prompt never mentions
`tools/`, and `guides/tools.md` addresses a person authoring before a run. **Writes, not
reads**, since reading a skill and running its scripts is ordinary; the read control is
parametrised beside the write test so denying the directory outright fails it. **An
absent root is still named**, since `kingfisher seed` can run between two backend builds.
`test_every_definition_root_is_protected` walks `catalogue_roots`, so a new kind is
covered the day it exists, which is what made *Middleware is a definition kind*
buildable. *(2026-09-07.)*

**Reversed: `models.yaml` and `source_ids.yaml` are protected too.** They were left out
as read only at construction, but so are the definitions; and `source_ids.yaml` is who may
reach what, so a shell editing it could grant itself anything from the next start. The
files a deployment writes, the keys file with them, are denied by exact `path`, being
beside folders that stay writable, at the place the configuration reads them from, so a
relocated file is covered and a missing one cannot be created. macOS only: the Linux
fences grant nothing outside the session. *(2026-09-17.)*

**The profile was not the agent's to edit either, and was.** It sat in `.kingfisher`
inside the writable workspace, and `sandbox-exec -f` re-reads it for every command, so
two commands sufficed: write `(allow default)` over it, run under it. Measured: the
second read the home and created `skills/PWNED.md`. `profile` now requires the path it is
written to and denies writes to it by `path` -- required, because a caller who forgets it
gets no boundary -- which covers overwrite, append, unlink and rename-over. It is replaced
rather than truncated, so no turn binds to half a profile, and `.kingfisher` is protected
whole.

**A session's `.harness` is denied to the shell too, by a pattern** --
`(deny file-write* (regex #"^…/sessions/[^/]+/\.harness…"))` -- because a profile naming
the sessions that existed when written would be rewritten as sessions arrive, and
concurrent turns would race on the file each is bound by. bubblewrap binds it read-only
over the session it mounted. *(2026-09-08.)*

**The profile is named for its rules, not fixed at `shell.sb`:** `.kingfisher/shell.<digest>.sb`,
the digest over every rule but the one naming the file. Processes serving one session
each may share a workspace, and two configured differently would rewrite one fixed file,
every turn bound by whichever wrote last. `Confinement.profile` carries the path, since
nothing else can spell it. A stale profile is a few hundred bytes left. *(2026-10-13.)*

**Measured and closed: a pattern held `.harness` by where it sat, and the path could
move.** Measured with a real `sandbox-exec` through the agent's backend, renaming
`sessions/` aside, or carrying one session's folder out of it, rewrote a pinned agent and
put it back. `_sessions_denial` denies writes to the nodes `sessions/` and each
`sessions/<id>`, `$` ending the pattern so a session's own folders stay writable --
measured, it still makes, renames and removes them. A hard or soft link from a writable
folder was measured closed the same day, pinned by
`test_a_link_from_a_writable_folder_does_not_reach_the_harness`, so no rule names
`file-link`. `doctor` stays `ok`; that a location-based deny is only as good as the paths
it enumerates lives here, not in a check crying wolf every run. bubblewrap's read-only
mount cannot be renamed away, and Landlock never grants `sessions/`. *(2026-10-12.)*

**Landlock cannot take back what it has granted, and the Linux job is what
established that.** The first policy granted the session writable and its `.harness`
readable, reading the kernel as taking the most nested rule. A Landlock rule only grants,
and a write walks up until one answers, so on the first run of
`tests/linux/test_fence_escapes.py` the shell overwrote the agent its session was pinned
to. `policy_for` grants the session's folders one at a time, never the session; `inputs`
is left out. The price is that the shell cannot write into the directory it starts in,
where bubblewrap and macOS can carve one out; the alternative left the pinned agent, the
conversation, the turn lock and the run log writable by the shell. *(2026-09-08.)*

**The two Linux fences share one front door.** They differ in the middle -- Landlock
confines between `fork` and `exec`, bubblewrap prepends to the argv -- and `sandbox/linux.py`
holds the rest: `present`, `outcome` and `MAX_OUTPUT_BYTES`. Each keeps its own path list,
since bubblewrap binds no `/proc` and builds its own `/dev`, and one shared list is how a
mechanism gains a path the other meant to deny. The duplication hid that `test_fence.py`
exercised the Landlock runner on any host while `test_bubblewrap.py` barely touched its
runner.

**The fence's own claims were the part untested.** Mutating what `argv_for` builds, on
2026-10-01: binding the whole host **writable** left all 2,293 tests green, as did
deleting the `.harness` bind or moving it above the session's. The rules listed what must
*not* be bound, where the claim is what may be, and the fixture made no `.harness`, so
`present` dropped that bind from every assertion. They check the whole set now, as
`test_the_shell_may_write_exactly_these_places_in_a_session` does for Landlock, on a
fixture from `ensure_session_layout`. It mattered: `FENCES_HARNESS` is what `doctor`'s
`session files` check reads to call bubblewrap `ok`, and bubblewrap's escape tests run on
no CI job, the Linux image refusing the user namespace, so
`tests/linux/test_a_fence_was_exercised.py` vouches for *a* fence and not this one. Six
mutations fail a named test now, one being the runner inheriting `os.environ`, whose first
rule passed an explicit `env` and so could never reach the fallback. *(2026-10-05, from an
architecture review.)*

**A mechanism is named, not spelled out.** `LANDLOCK`, `BUBBLEWRAP` and `SANDBOX_EXEC` are
one vocabulary and `MODES` another, overlapping on one word: a mode is what a deployment
asks for, a mechanism what ended up confining, and `auto` is never one. A spelling
compared in several places reads a renamed mechanism as *not a fence* -- an unfenced
shell beside a `Confinement` reporting one. `LINUX_FENCES` is the pair `fence_for` builds
a runner for. *(2026-09-20.)*

**A runner that says nothing keeps the fence, and one function decides it.** `local`
defaults to `True`, the safe direction, and was read twice: whether to wrap, and what the
`Confinement` claims. Flipping the second left all 2,109 tests passing, and with it a
local runner declaring nothing ran unsandboxed under
`Confinement(wrap=_unwrapped, elsewhere=True)`, reported as somebody else's boundary. The
test named for it replaced the confinement after building it;
`test_a_runner_that_says_nothing_is_confined_here` asserts on `elsewhere`, since
Landlock's wrap is the identity by design. `runs_locally` is the one reading. **The
contract kit keeps its own on purpose:** importing the module holding `runs_locally`
would take `tests/checks/runner.py`, which a deployment copies, from 70 loaded modules to
3,104, measured on 2026-10-16, to share one `getattr`. *(2026-09-25, from an architecture
review.)*

**Sessions reaching each other is reported, not refused.** The macOS profile gives the
whole workspace back, so one session's shell can read and change every other's files:
measured on 2026-10-11, a second session's shell read the first's `outputs/` and wrote
into it. Narrowing it means a profile per session, which one text per workspace was built
to avoid. Where no fence starts -- most servers on a kernel before 6.12 without a working
bubblewrap, Docker Desktop, `external` -- nothing keeps sessions apart either. **Refusing
a turn there was weighed and not taken:** kingfisher is mostly run as a package, and a
stop most ordinary servers would hit is friction for nearly every deployment to protect
one case, several people in a workspace, that a warning can name. So `doctor`'s
`sessions apart` names it, quiet under a Linux fence and for backends it cannot see
into, and so does the warning about an unconfined shell. People sharing a Mac get a
workspace each; `compose.yaml` stays `external`, one person per container. The prompt
does not tell the agent other sessions are unreachable. *(2026-10-11.)*

**The profile's rules follow one link on `sessions`, and the two halves of that move
together.** The rules resolved the workspace and not a `sessions` symlinked under it, and
the profile matches real paths, so moving sessions to another volume, as `ports.md`
recommends, put every session outside every rule: measured, every command failed at
`cd`, naming no fence. A symlinked *workspace* is the control, and worked. Every rule
naming the root asks `fenced_sessions`, because the half-fix is worse: allows resolved and
denies not left `inputs` and `.harness` writable, exit 0. The driven rule asserts both at
once. **One link deep is as far as a static profile reaches**: a session that is itself a
link is unusable, the safe direction, and `doctor`'s `session paths` row warns of it;
quiet under a Linux fence, built from the opened session directory, and for backends it
cannot see into. *(2026-10-15, from an
architecture review.)*

**A `"` in the workspace path cost the deployment, and what a profile's regex literal
accepts is a small dialect.** `_sb` escapes quotes in string literals, but the `regex`
rules took `re.escape` alone, and a quote is legal in a macOS directory name: with a
workspace `my "work" stuff` every command exited 65 with `sandbox-exec: unbound variable:
work"`. **The obvious fix is wrong.** `#"..."` is raw, so doubling backslashes broke every
path `re.escape` touches, `pytest-of-...` among them, and `\"` ends the literal like a bare
quote. Measured spelling by spelling, since none is documented: `.`, `[[:punct:]]` and
`[^/]` match a quote, while `\x22`, `\042`, `"` and `.{1}` parse and match nothing.
`[^/]` stands in, not `.`, which crosses a separator; it can only deny more. A text rule
decodes each literal as sbpl does, on every platform, and a driven one hands the file to
sandbox-exec, because a pattern Python compiles is not one that engine compiles.
*(2026-10-15, from an architecture review.)*

**Landlock, chosen against two measured alternatives.** bubblewrap works only with
`--security-opt seccomp=unconfined`, disabling the syscall filter in a box shared by
tenants. Per-session Unix users through `setpriv` deny by ownership: every file, forever,
the agent's own included. Landlock denies by default, needs no privileges or relaxed
seccomp, and cost 0.2ms -> 0.8ms a command under Docker's default profile. The policy is
generated, because the first hand-written one failed open: it granted `/tmp` writable with
the workspace under `/tmp`, and every read succeeded, so it looked as if it worked.
`Sandbox` has forty fields. *(Recorded in the code until 2026-10-17.)*

**The macOS profile denies writes from `/` down, and headless Chrome pays for it.**
Under the profile Chrome fails in 0.4s with `Failed to create a ProcessSingleton for your
profile directory`, where unsandboxed it renders a PDF in 2.0s; the write rule is the
whole cause, and no flag avoids it, since Chrome reaches `/private/var/folders/<user>`
whatever `--user-data-dir` says. Allowing writes to the parent of
`tempfile.gettempdir()` was verified to fix it with `.env`, the home and the repository
still refused. Not allowed: nothing shipped needs a browser, and the model asked to stay in
the workspace wrote `/tmp/preview.pdf` and twice tried `pip install`. *(Recorded in the
code until 2026-10-17.)*

## Sessions: what persists and where

These began as decisions in *Nothing at rest on this machine* and were built
without the rest of it, which is what that document's N2 asked for -- the parts
that need no memory-backed filesystem, first and separately.

**`/runs` and `.tmp` became one `/scratch`, and a turn stopped being a place.**
A session held two scratch directories with one purpose: `.tmp`, the shell's `TMPDIR`,
which the agent was never told about, and `runs/<turn>`, named in every task message.
Neither was returned to a caller, and a turn's directory was never swept, so it stayed
for the life of the session, counted against its quota. The folders were also the turn
counter, so a session restored on another host, where nothing restores `runs/`, began
again at `t001`. Nobody compares two turn ids -- one reaches a printed line, the run log
and `RunResult` -- so `allocate_turn` generates one now, and a caller's own still wins.
It buys no de-duplication on a retry, which the id never gave.

**A request's files go to `/inputs` with the session's.** Once a turn had nowhere of its
own, a per-turn field and a per-session one were two fields for one operation, and they
merged. Two turns sending one name no longer keep both: the second replaces it, visibly,
through `place_inputs`, which re-hardens the directory and reports what it replaced --
the cost of the merge, stated rather than discovered. The per-turn `--input` went here;
the flag and `Request.inputs` came back with the per-session meaning (see "The names a
caller writes follow the folder" below).

**The three lifetimes are told apart by destination**: `/inputs` is the caller's and
read-only, `/scratchpad` is the agent's and returned to nobody, `/outputs` and `/memory`
are what the caller gets back. The dot came off `.tmp` because its only reason was that
the agent never named it, and `SESSION_DIRS` is "the names the agent addresses". Told
only the virtual form of a path, the agent passed it to `execute` 4 times in 10, each
failing and costing about three times the whole task; so a folder and its route
correspond as every route does -- drop the slash -- and folders are renamed rather than
given prettier virtual names. *(2026-09-21.)*

**`execute`'s own description states the shell's path rule, and a miss is answered
with it.** deepagents' description says "Use absolute paths", and the only absolute paths
a model knows are the file tools', which the shell reads from the host's root. `system.md`
says otherwise, but a subagent never sees it, while the description reaches every graph
holding the shell. So `BuiltinToolsMiddleware`, in `standing_middleware`, replaces that
line with the rule -- appending it where deepagents has reworded the line, which
`test_every_graph_holding_the_shell_is_told_its_paths_rather_than_absolute_ones` then
fails on -- and appends a correction to an `execute` error naming a session folder spelt
the file tools' way. A correction rather than a rewrite of the command, because the miss
the agent made despite `system.md` was a path inside a script the command ran, which no
rewrite sees. Not `system.md`'s table copied into every subagent's prompt: it would
contradict the description beside it, and cost every subagent the tokens. The
description also says `timeout` is in seconds, and its longest: over 71 trial turns
models passed milliseconds, 5,000 to 40,000, each refused as past deepagents' hour.
*(2026-10-17.)*

Measured over ten real turns each way, half with the agent running its own script and
half asking `reviewer` to work in the shell: turns with a miss did not fall (3 before, 4
after, noise at this size), but what followed one did. Before, two of the three that
missed missed again; after, each of the four was right on its next call. Host-absolute
spellings went from 7 in 29 shell calls to 2 in 27. *(2026-10-13.)*

**Still rare, and still not worth rewriting the command.** Over 21 real turns of two
data tasks, 8 of 109 shell commands named a session folder the file tools' way, all 8
answered with the correction. The one turn they cost anything ran out of its
deployment's cap of 20 tool calls with 3 spent here, and the cap is what lost its output.
So the command is not rewritten, and the folders get no environment variables, which the
model would still have to choose to use. Reopen it on turns that keep missing after the
correction. *(2026-10-17.)*

**`read_file` reads 1,000 lines unless told otherwise, and says to read a file whole.**
deepagents defaults to 100 lines and tells the model to page "instead of reading them
whole". Over 71 trial turns `read_file` was a third of every call, 87% of reads named a
limit, mostly 100, copying the description, and the turns that ran out of their 50 calls
spent 121 calls reading. So `BuiltinToolsMiddleware` hands each graph's model a
`read_file` saying 1,000, and fills in 1,000 where a call names no limit; a limit the
model names is a slice it wanted. 1,000 lines of ordinary width stays under deepagents'
cut of a read at 80,000 characters. deepagents is not patched, for this or anything: its
tools are changed from outside, each with a test that fails when an upgrade moves what it
changes. *(2026-10-17, from the trial transcripts.)*

**`grep` searches each string of `a|b`, and says so.** deepagents' `grep` is literal,
and its description gives `foo|bar` as what not to write. Over 71 trial turns, 97
searches written as regex found nothing, 63 of them alternations, and 85% were followed
by another search. `BuiltinToolsMiddleware` runs non-empty strings joined by `|` as one
literal search each, answers once with each string's matches under a line naming it, and
tells the model that is what `|` does. A pattern with an empty part, such as a table cell
`| a |`, is searched as written, since an empty string matches everything. Escapes and
anchors are left alone: deepagents already points at `rg` in the shell, and unescaping
would guess at what was meant. *(2026-10-17, from the trial transcripts.)*

**An agent near the end of its budget is told how many model calls it has left, and a
subagent is held to the same budget.** Over 71 trial turns, every turn either finished in
38 calls or fewer or ran to the 50-call limit and stopped mid-work, never told it was
close. `StepBudgetMiddleware` reads langgraph's own step counter and limit, so it follows
`KINGFISHER_RECURSION_LIMIT`, and from 80% of the steps adds a note as the last thing each
model call reads: about how many calls remain, at three steps a call, and on the last that
it is the last. The note is never stored: kept, it would be resent stale on every later
call, and coming after the cached part it leaves the prompt cache whole. The limit stays
at 150 steps.

A subagent does not run under that limit: deepagents binds 9,999 steps to the graph it
builds, and the turn's limit does not override that. Measured under a 30-step turn, a
subagent saw 9,999 and made 41 calls. So `standing_middleware` holds each subagent to the
agent's budget in model calls, counted off its own conversation: warned from four fifths
of it, and on its last allowed call any tool calls it still asks for are dropped and its
text handed back under a line saying it stopped at its limit. The turn goes on. Enforced
there rather than by binding a limit onto graphs deepagents compiles, which leaves
deepagents' config alone. Its calls are counted in the saved conversation, not the
request, since a summary leaves earlier calls out of the request and the count restarted
at each one: a 10-call subagent summarised partway made 41 calls, never warned or stopped.
After the `read_file` and `grep` changes, no trial turn on a neutral prompt reached the
warning. *(2026-10-17, from the trial transcripts and a scripted turn.)*

**What the agent is told about the shell is said by the shell.** `system.md`,
`capability_skills.md` and `BuiltinToolsMiddleware` told every deployment's agent four
things true only of kingfisher's own shell: it runs on the host in the session root,
`TMPDIR` is the scratchpad, `KINGFISHER_SKILLS` is the catalogue, and every session
folder is reached by dropping the slash. A deployment whose shell runs elsewhere reported
it; the prompt was built from `Config` alone, so nothing about the backend could reach it.

`runs_locally` cannot choose the wording. Measured on 2026-10-15 with the plainest runner
a deployment writes, through `LocalSessions(runner=...)`: local or not, it started in the
process's directory, with the system's `TMPDIR`, no `KINGFISHER_SKILLS` and no `inputs/`,
because a runner is handed the command and nothing else. `local` says whether the fence
goes in front of the command, not what the shell sees. The only shell kingfisher can vouch
for is the one `backend_at` builds without a runner.

So the shell carries a `ShellPrompt` in three parts -- a section of the agent's prompt, a
paragraph after the skills section, and the line in `execute`'s description -- because
three readers see different ones: one string could not know whether skills are on, and
would never reach a subagent, which is told only the description. Kingfisher's own shell
carries its wording in `shell_here.md` and its siblings; a runner's is handed in beside
the runner; a deployment's sandbox sets the attribute. A shell saying nothing is described
neutrally -- only what holds wherever a shell runs, and deepagents' own `execute` line --
rather than refused: neutral wording misleads nobody, and a refusal would stop a
deployment until it wrote prose. A part left `None` is neutral, and `""` says nothing.

**Two sentences the base prompt stated unconditionally, and a blank nobody could
misspell loudly enough.** Each was found by driving rather than reading.

*The inputs folder is not read-only "by the filesystem itself".* That half is a fence, and
`off` and `external` are supported settings: with the sandbox off, `chmod u+w` and a write
replaced the caller's file and `rm -rf` took the folder, both exit 0. The prompt claims
only the file-tool half, which holds on every run, and a rule drives a real `write_file`
under all three confinement settings rather than asserting the wording. What the fence
does is the operator's to know, from `doctor`.

*"One of two places, and never anywhere else" is false the moment memory is on*, since the
same prompt then tells the agent to write `/memory`. The sentence names what is *not* the
agent's to write, rather than counting what is: a count is a claim an optional section can
break.

*A blank spelt wrong was neither refused nor filled.* The scan for a `{{leaf}}` matched
`\w+`, so `{{ inputs }}`, `{{tool-results}}` and `{{inputs.dir}}` passed the refusal and
the fill and reached the model as braces, while `{{inptus}}` was refused, which made the
gate look as if it worked. One pattern serves both on purpose, so a blank either is wrong
about the other reports; it matches anything between doubled braces now, and the rule
drives all four misspellings across all three parts. *(2026-10-16, from an architecture
review.)*

**On the shell, not on the session backends.** The session backends are what a deployment
subclasses, and the subclass `ports.md` shows swaps in a sandbox of its own: on the class,
the wording would be inherited and that sandbox's agents told it was kingfisher's. On the
shell, a sandbox is neutral until it says something, and what it says sits beside the code
deciding what it sees. A blank no layout has is refused when the `ShellPrompt` is made,
not on the first turn. `shell_prompt=` without a runner is refused: kingfisher's own shell
describes itself, and instructions for every agent go in the instructions file or
`instructions=` on `Kingfisher`. The correction on a missed path goes only to a shell whose
`ShellPrompt` says `starts_in_session`, since it spells the path from the session root.
*(2026-10-15.)*

**deepagents' host paths reach only a shell on this host.** deepagents appends a section
giving each routed folder's host path to any shell of its local class, which kingfisher's
is whatever runner it holds -- so an agent whose commands ran elsewhere was told paths only
this machine has. `BuiltinToolsMiddleware` takes the section out where the shell is not
kingfisher's own on this host: a runner whose `local` is false, or a deployment's own
shell. A local runner keeps it, since its paths are real there. Removed rather than
corrected: making a remote shell a class deepagents reads as remote would have it say the
folders are out of reach, as much a guess, contradicting a deployment whose `ShellPrompt`
says its sandbox mirrors them, with no way to turn it off. Taken out by block, matched on
deepagents' heading, because the section arrives as a system-message block of its own;
merged into other text it would be left whole, and
`test_deepagents_host_paths_reach_only_a_shell_on_this_host` would fail.
`standing_middleware` is handed the turn's backend, so the `execute` line and this section
are asked of the same shell. *(2026-10-15.)*

**`HOME` is `scratch` too, and `.home` is gone.** `HOME` had a directory of its own so
that whatever a tool caches under `~` would land in the session, be swept by `reap` and be
counted by `session_bytes`. Nothing wrote there: workspace tools run in the kingfisher
process and resolve `~` against the operator's home, no shipped asset caches, and the one
`.home` on disk was empty. The scratchpad already has every property the job needs -- per
session, swept, counted, writable under all three fences, and `TMPDIR` besides -- so
`HOME` points there, and the fence grants one directory fewer. A tool that does cache
under `~` leaves a dot-directory in the agent's scratchpad, which is the cost accepted.
*(2026-09-24.)*

**`/scratch` is `/scratchpad`.** The name the agent types, in both spellings, and the
shell's `HOME` and `TMPDIR` with it. One constant moved; the rest was the prompt, which a
test now ties to the layout -- without it, `system.md` could have kept teaching `/scratch`
with everything green. *(2026-09-24.)*

**A session's history is kingfisher's own records, not a framework's.**
`core/session/transcript.py` keeps what the agent *did* as well as what it said -- tool
calls and results, not only human and assistant text -- since an agent that cannot see
what it already did will do it again. Portable on purpose: the next turn may not be run by
this harness.

**The checkpointer is in-memory, and the transcript is what survives.** So a turn's
*working* state does not cross a turn boundary though its conversation does: an agent
resuming a session does not find a half-finished `TodoListMiddleware` checklist it has no
memory of writing, for a task the caller may have dropped. That is structural rather than
enforced, so it is asserted. A checkpoint preserves resumable graph state, and kingfisher
resumes a graph only in the one case below, so what is left for a saver is one turn's
supersteps.

**Narrowed once, for approval gates.** A turn stopped at an `interrupt_on` gate writes what
its saver holds into `.harness/paused.state`, and a `Resume` reads it back and continues
that graph. Nothing is kept unless a turn stopped to ask, and it is dropped once answered
or superseded, so an ordinary turn still starts with empty channels. The file tools are
refused that file, but `execute` bypasses them and what refuses the shell is a fence a
deployment can switch off. So the checkpoint is msgpack through langgraph's serialiser,
and not `InMemorySaver(factory=PersistentDict)`, the persistence seam langgraph ships and
the obvious thing to reach for: it is unconditionally pickle, and a tampered pickle read
back is arbitrary code at resume time where tampered msgpack is only bad graph state.

The pending writes go with the checkpoint. A turn can stop on one gated call while a
sibling in the same superstep has already run; drop those writes and the sibling runs
*again* on resume -- a tool firing twice for one decision, in identical end state, so
nothing but the side effect tells them apart. *(2026-09-22.)*

**Dropped: a deployment's own saver.** `threads=` let a deployment hand every turn a
langgraph saver of its own. Driven on 2026-10-17 it had four faults with one cause -- a
saver remembering earlier turns, beside a transcript replayed into each: every follow-up
turn's model saw the earlier exchanges twice; a turn stopping at a gate crashed as it
ended on any saver but `InMemorySaver`, whose own mappings writing the pause reads; on a
shared `InMemorySaver`, one session's pause carried every session's checkpoints; and a
resume ran on a copy read from that file, so the deployment's saver never saw the turn it
finished. A turn needs a saver only for its own length -- its end reads the final state
once, to write the transcript -- so a deployment's saver bought nothing, and the pause
file already keeps a paused turn in the session. Removed with `checkpointer=` on `run` and
`stream`, `Plugins.threads`, `SharedThreads` and `reap`'s sweep of threads no session
owned; `test_a_follow_up_turn_hands_the_model_the_earlier_exchange_once` holds the
doubling.

**Not taken: a thread per turn in the deployment's saver.** It fixed all four and kept the
plugin, for a use nobody had named -- each turn's step-by-step state for a deployment's own
tools to read. *(2026-10-17.)*

**A pre-built graph runs on the turn's saver too.** A graph passed as `graph=` ran on the
saver it was compiled with, so on a saver of its own every follow-up turn saw the earlier
exchanges twice, and on none the turn had no final state to read and wrote no transcript.
Each turn now runs `graph.copy` with the turn's saver, as a definition-built agent does.
*(2026-10-17.)*

**The mark beside it records the agent the turn ran, not the one the request named.** A
session's agent is fixed when it opens, so every turn after the first names none, and a
pause recorded from the request recorded `""`: a resume naming the session's own agent
was refused as *"paused under agent 'none'"*, and one naming another agent with the same
wrong sentence. The rule meant to hold this passed throughout, because it ran on an
injected graph under no policy, where no agent resolves, so it refused every name alike,
the right one too. It is driven on a session with a pinned agent now, beside the
accepting half it never had.

**Nothing recorded is not a mismatch.** A deployment that supplies its own graph and no
policy never resolves an agent, and a *request* naming one is not refused there, so a
resume must not be either. The `agent` clause fires only where an agent was recorded. A
resume naming an agent the session is not running is also refused by the pin in
`_agent_for`, but `_refuse_stale_pause` stays ahead of it because the paused state is
deserialised first. *(2026-10-01, from an architecture review.)*

What the old per-session sqlite bought survives by another route, measured rather than
assumed: a conversation deleted with its session (one workspace held 132 orphaned threads
after every session was reaped), a conversation the quota can see, and no cross-session
contention (at 32 concurrent writers the slowest went from 363ms on a shared file to 80ms
on its own). The transcript is a file in the session, and nothing is shared. What went is
~20KB of empty database per session, which was the cost.

**Sqlite is gone entirely, and so are the two dependencies that carried it.**
`langgraph-checkpoint-sqlite` and `aiosqlite`, which N13 asked to drop, outlived the
transcript only for `build_checkpointer` and `async_checkpointer`, exported builders of
one database per workspace. The second existed because `astream` refused a saver without
async methods -- `SqliteSaver` raises on `aget_tuple` -- and `InMemorySaver` implements
both halves, which left two builders whose only callers were their own tests, in the shape
the library had moved away from: one database shared by every session. Removed rather than
kept for symmetry, a breaking change made at `0.1.0`, when it costs least. *(2026-09-04.
Its pointer to `threads=` for durable graph state was reversed on 2026-10-17: see "Dropped:
a deployment's own saver".)*

**`doctor` checks whether a memory-backed workspace can be filled safely.**
`core/backing.py` reads the filesystem type, its size, the cgroup limit and whether swap
is permitted, and `cli/health.py` reports them. A memory filesystem *larger* than the
container's limit does not fail when it fills: the kernel swaps its pages out, which is
data at rest, arrived at silently, with the write succeeding. Only one smaller than the
limit gives a clean `ENOSPC`.

**The run log never crosses the wire.** Nothing in a result is a host path, so a result
serialises whole: `artifacts` names files relative to the session, and `Session.artifact`
fetches them. While a result carried host paths they were typed `Path`, so `json.dumps`
raised rather than stringifying them. Rejected: a mirrored pydantic model, a second home
for that rule and the kind that gets it wrong helpfully -- a `Path` serialiser makes the
error go away and ships exactly the leak. *(2026-09-15.)*

**The session quota is checked between turns and never during one.** This reverses
*Nothing at rest*'s N11, which said the bound could be metered on the tool-call hook
because kingfisher wraps every tool call. It cannot: `execute` writes without any file tool
seeing it, so only a filesystem quota underneath could stop a running turn. The check
stops the next turn making it worse.

**A session's files reach a store the deployment wired, and its directory is a port
too.** `SessionStore` and `SessionRoot` were built because bytes left a session through a
host path, which only worked for a caller on that host; both went on 2026-09-30, when the
backend became where a session is (*A session is its backend*). What carried over: **a
local directory is a perfectly good implementation** -- what the constraint forbids is
kingfisher *assuming* a disk, not a deployment choosing one.

**A session owns what it costs: everything per-session lives in the session.** Four things
did not: a session's pinned agent, its conversation, a turn's lock and its run log sat
under `state_dir`, one directory per kind keyed by id, and the shell's `TMPDIR` was shared
by the workspace. None was deleted with the session or counted by `session_bytes`, and
nothing swept them -- the 132 orphaned threads again, by another route. And shared scratch
was a channel between tenants: every fence granted it writable, so what one caller's agent
derived, another's could read.

**Uniform rather than by size**, which is the decision worth recording. Moving the large
things and leaving the small -- the pin is a kilobyte, a claim an empty directory -- buys
the same two properties for a smaller diff, and is where the reasoning would have gone to
hide: the pin is small, and it is also the file that decides which endpoint a session's
prompts reach and whose credentials pay. A layout whose rule is "unless it is small"
cannot be checked by reading it.

**Two enforcement points, because one is bypassable.** `.harness` carries read *and* write
denies for the file tools and a rule in the fence for the shell. The read deny makes it
invisible rather than visibly forbidden: a deny filters entries out of `ls`, `glob` and
`grep` instead of erroring, so the model never sees a directory it would then try to open.

**The pin crosses a machine now, which closed a hole rather than tidying one.** Kept
outside what travelled with a session, it was missing on another host, so the session
re-pinned from *that* host's catalogue and took whatever agent the request named --
*"a session is fixed to the agent it opened with"* held on one machine and failed across
two. It lives in the session's `.harness`, wherever the session's backend keeps it. The run
log does not: it is diagnostics, and goes to `RunEvents`.

**The claim moving in deleted `_discard_dead_claims` outright.** It existed because a claim
could outlive the session it named; one inside that session cannot. The cost is that
`busy` is asked per session rather than read from one listing. The claim is on the
session's backend now; see *A session is its backend*.

**A constant's property and a constant's wiring need separate rules.**
`claim_stale_after` is the turn deadline plus the longest model timeout, so a claim
outlives the window a turn stops *in*, and a rule held that by supplying the constant at
every call -- which says nothing of whether a turn passes it. Measured on 2026-10-11:
reinstating the original bug at the turn's call site, `stale_after=cfg.turn_timeout_s`,
left all 2,546 tests green, while the same mutation at `Disposal` reddens
`test_retention_and_claim_agree_on_when_a_claim_went_stale`. The wiring is now watched at
the `claim` seam rather than raced, and the rule asserts the two numbers *differ*: they
were once the same, and a rule comparing against either would have passed then.
*(2026-10-11, from an architecture review.)*

**A sweep that removes nothing says which reason spared each session.** `expired` folds a
busy session into `kept`, so `reap` blamed the age for a session `kingfisher sessions`
printed as hours idle, and sent the reader to a shorter `--older-than`, which no age can
help: none sweeps a session a turn is running in. `SweepPlan` and `SweepResult` carry the
busy count, the difference between the two `expired` calls `Disposal` already makes. Three
sentences rather than one, because three things can be true: every survivor is busy, some
are and the rest too young, or the age decided -- the control, held by the existing rule
about which age is named. *(2026-10-11, from an architecture review.)*

**No migration and no fallback reader.** The failure a fallback would paper over is
silence rather than breakage: a pin the new code cannot find means a session re-pins and
may change agent mid-conversation, and a transcript read from the new path comes back
empty. A fallback also has no forcing function to be removed. The workspace marker was
given a layout version to make an old workspace loud, refusing on what was left rather
than on the number, since an operator who deleted everything listed was otherwise still
refused, and deleting the marker made the workspace read as new. *(2026-10-06.)*

*Reversed: the version went as well, and nothing refuses an older workspace now.* The
number would have ratcheted on every change to a layout reshaped week to week, and the
state it protected is disposable: a conversation, a pin and some files, none meant to
outlive the shape it was written in. `MARKER_TEXT` is a bare identity line nothing reads,
and `ensure_layout` neither refuses an old workspace nor rewrites its marker. What this
gives up is that loudness; the replacement, once workspaces outlive config churn, is a
fingerprint of the configurable names in the marker and a mismatch refused. *(2026-10-15,
#756.)*

**`KINGFISHER_STATE_DIR` and `KINGFISHER_SCRATCH_DIR` are gone.** Scratch first:
per-session and relocatable are not both expressible, because `session_bytes` counts one
directory and anywhere else is a cost the quota cannot see. Then state, left holding one
generated file. Neither was set anywhere -- `.env.example` had both commented out -- and
`doctor` names each as retired. *(2026-09-08, `a-session-owns-what-it-costs.md`.)*

*Not set anywhere, but still read in one place.* The integration driver read
`cfg.state_dir` on the last lines of a smoke run, which only a live model call reaches, in
a tree where `ty` ignored unresolved attributes under `tests/**` -- so every smoke run
finished its turn and then raised. The copy was dropped, not moved: nothing read it, and
the pass/fail signal was always `check_result`. `test_a_smoke_run_reaches_its_end` drives
those lines without a model. The `ty` override was narrowed to test modules, where all 55
findings it hid were, and `evals/`, which the driver imports, was brought into both `ty`
and CI's `ruff`; `test_ty_exempts_test_modules_and_nothing_else_under_tests` and
`test_every_directory_holding_python_is_type_checked` keep it so. *(2026-09-16.)*

**Containerise and use a sized tmpfs; do not adopt mirage for the filesystem.** That was
*Nothing at rest*'s closing recommendation and what shipped. A tmpfs inside a container
gives memory-backed files that are *real paths* -- any program opens them -- with a size
limit the kernel enforces, and no library, driver or version risk. Mirage was measured
rather than argued about, and `findings.md` carries what it does. What it would still be
good for is the question that exploration set aside: S3, Drive and Postgres mounted as
paths, which kingfisher has no story for and which is a far smaller change than replacing
the filesystem. *(N11, N13 to N16, N20 and N22 built through 2026-09-01. All from
`nothing-at-rest-on-this-machine.md`, removed 2026-09-04.)*

**`run(delete_session=True)` reports a deletion that failed, on the result.**
`RunResult.deletion_failure` carries the reason: on the result rather than logged, because
the caller asked for the deletion in the same call and looks there for how the turn went;
and rather than raised, because the turn finished and its answer is worth keeping. A turn
stopped at a bound keeps its session on purpose and leaves the field empty -- `stop_reason`
says why. *(2026-09-15.)*

**`/memory` lasts as long as its session, and the prompt says so.** `/memory` is deleted
with its session, so a prompt telling the agent to record what "would change how a future
session works" had it writing for a reader who never arrives. It says *later turns of this
conversation*, the lifetime `/outputs` has. Memory shared between sessions is a separate
design, not made: `docs/design/2026-09-10-a-mount-kingfisher-can-name.md` leaves `/memory`
off a shared mount because a per-caller route on shared storage "needs a tenancy argument
that has not been made". Until that design exists, "a future session" does not go back
into the prompt. *(2026-10-07.)*

**The guidelines deepagents appends after it say the same, because they are
kingfisher's.** deepagents' `MEMORY_SYSTEM_PROMPT`, straight after kingfisher's section,
called each correction "a chance to improve permanently" and told the agent to skip what is
"irrelevant in future conversations". `prompts/memory_guidelines.md` is that template with
five lines changed; the rest is still the library's. `create_deep_agent` takes no argument
for the template, but merges middleware by name, in place, so `build_agent` keeps
`memory=` and passes a `MemoryMiddleware` of its own carrying it, which takes deepagents'
slot. Measured by driving a real build with a recording `ChatAnthropic`, sync and async:
the stack unchanged, `MemoryMiddleware` last after `AnthropicPromptCachingMiddleware`, and
two cache breakpoints as before, one on the static prompt and one on the memory block.

Two other ways were measured and not taken. `system_prompt=None` appends nothing, so the
memory file stops reaching the model too, and putting it back is the middleware written
again. A `MemoryMiddleware` passed *instead of* `memory=` lands ahead of
`AnthropicPromptCachingMiddleware` with one breakpoint where there were two, so every edit
to the memory file costs the whole prompt its cache.
`test_the_memory_guidelines_promise_nothing_past_the_session` reads deepagents' template
off the library and fails if a line kingfisher's copy leaves out reaches the model; it
cannot catch "permanently" written back into kingfisher's own copy, which a test could only
match as a phrase. `test_the_memory_block_keeps_a_cache_breakpoint_of_its_own` holds the
slot. *(2026-10-07.)*

**A session id is one path segment, checked where it becomes a path.** Nothing checked it,
and every path built from an id joined it on as given. Measured: `..` laid a session out in
the workspace's own folder, an absolute id laid one out wherever it pointed, and deleting
`""` removed every session and reported success. So `session_dir` refuses it rather than
any one door: a door is one caller, and every path built from an id is built there.

**One segment means one visible entry in a directory**: not empty, not starting with `.`,
and holding neither `/` nor NUL. Not hidden, because the macOS profile guards `.harness` in
every session and a session of that name could not write its files. `\` was weighed and is
allowed: Windows and URL parsers read it as `/`, but POSIX allows it in a folder name, and
refused, such a session was listed and never opened, and `reap`, which opens each expired
session to ask whether a turn holds it, raised there and swept nothing. A rule narrower
than the filesystem's names makes sessions nothing can remove; covering Windows would take
`:` and its reserved names too, for a host no fence of kingfisher's runs on.

**The refusal is `UnknownSessionError`.** Every caller already handles it -- the command
line reports it among its `REFUSALS`, and a deployment mapping it to a 404 needs nothing
new -- where a `ValueError` would be a second way of saying "no such session" for each to
learn. The rule is `names_a_session`, apart from the refusal, so the listing is held to it
too. *(2026-10-07.)*

**`/data` is `/inputs` and `/derived` is `/outputs`.** The agent knows a folder by little
more than its name: "data" did not say its files were the caller's and not to be written,
and "derived" did not say what goes there is handed back; `inputs` and `outputs` say which
way files go. `/memory` keeps its name. The folder on disk moved with the route, because
the agent reaches both, and a route renamed over a folder left as it was would break the
path table's one rule, drop the slash. Artifact names follow, as paths in the session. The
shell's guard on the inputs folder and the upload into it name one constant, and a test
finds where an upload landed rather than naming the folder: a name spelt by hand beside the
constant would have left a caller's inputs writable to the shell. *(2026-10-07.)*

**The names a caller writes follow the folder.** `data` was kept when the two fields merged
because it named where a caller's files land, and by that reason the folder's new name
carries over: `kingfisher run --input`, `Request.inputs` and the `inputs_placed` event,
with the per-session meaning `data` had, not the per-turn one from before the merge. The
names inside follow, so the folder and what guards it are spelled one way: `place_inputs`,
`InputsPlacement`, `InputsError`, `InputsBackend`, `protect_inputs` and `writable_inputs`.
No alias keeps the old names: it would be two names for one thing again, which is what the
merge removed. *(2026-10-07.)*

**A workspace can hold one session.** Some services make a workspace *be* a session, and
`KINGFISHER_SESSION_ID` said so: a request naming no id ran in it, any other id was refused
as unissued, and it was listed before any turn, since it existed by configuration. A
setting rather than an argument, because the default sessions object serves every
workspace a process opens and is handed the `Config` on every call; and read in
`_session_for`, where an id was minted, rather than asked of the backends, which would
have been a port method every deployment's own had to grow for one field. *Not a reversal
of "Session ids are issued, not accepted"* (2026-08-16): the deployment issued the one id.
*(2026-10-07.)*

**Reversed: the one session has a folder of its own, and the setting pins a process rather
than a workspace.** First built, the session was `<workspace>/sessions` itself, and the
missing level cost a special case each: a delete had to empty a mountpoint rather than
remove it; the macOS profile's `.harness` pattern missed `sessions/.harness`, leaving the
pin, the conversation and a paused turn writable to the shell; a restart with the id
changed carried the new id on the old session's conversation, so the id needed a record;
and a workspace used both ways had to be refused at startup. With the definitions in a
folder of their own, the workspace is only what sessions make, and one process per session
can share it, each listing and reaping its own. So the session is in `sessions/<id>/` like
any other, and the startup check and the record went. *(2026-10-13.)*

**Removed: the setting.** Once a request names its session and one naming none runs in
`default`, `KINGFISHER_SESSION_ID` had nothing left to do. It is refused at startup if still
set, in the table that refuses the store settings: ignored, a deployment's no-id requests
would land in `default` instead of the session it chose. *(2026-10-14.)*

**What keeps the shell from making a folder the listing would read as a session is
`_sessions_denial`'s node deny, not the folder rule's optional segment.** The folder rule
matched `sessions/<folder>` as well as `sessions/*/<folder>`, through an optional session
segment, which was redundant -- creating or renaming anything directly under `sessions/` is
a write against that node, which `_sessions_denial` refuses for every name at once -- and
collided: `inputs` is a legal session id, so a session called `inputs` had its whole tree
denied and every command in it failed with `Operation not permitted`, saying nothing, and
the id comes from the request. The segment is required now, and
`test_the_shell_cannot_leave_a_folder_the_listing_would_read_as_a_session` drives the node
deny over `inputs`, `.harness` and a name no rule mentions, so the half that remains is held
by what actually holds it. *(2026-10-15, from an architecture review.)*

**deepagents' own files are kept in the scratchpad.** deepagents writes two kinds of file
unasked: a tool result over about 80,000 characters goes to `large_tool_results/` with a
stub naming the path handed to the model, and the summarizer keeps what it replaced, and a
user message too long to send, under `conversation_history/`. Both hang off
`CompositeBackend.artifacts_root`, which defaults to `/`, so they sat at the session root,
in no folder the layout names and never returned. `WorkspaceScopedBackend` passes the
layout's scratchpad route as `artifacts_root`: they are the agent's working material,
returned to nobody, where `/outputs` would have handed every evicted result to the caller. `system.md` names both folders, because the agent sometimes searches
rather than follows a stub, and deepagents' `grep` description puts offloads under
`/large_tool_results/` "by default", which is no longer where they are.
`test_the_prompt_names_the_folders_deepagents_writes_on_its_own` reads the root off the
backend, so a rename cannot leave the line behind.

*One setting, and one condition on it.* deepagents reads `artifacts_root` only off a
`CompositeBackend` -- `FilesystemMiddleware` and `SummarizationMiddleware` ask `isinstance`
and fall back to `/` -- so a deployment's own backend has it by subclassing
`WorkspaceScopedBackend` or returning what `backend_at` built, and a `CompositeBackend`
assembled from nothing is back at `/`. The contract kit's `offloads_in_the_scratchpad`
checks it the way deepagents reads it, and is not refused at startup: it finds a folder in
the wrong place whose stub still names the right path, and refusing a working deployment
over that costs more than the folder. The Landlock fence grants a session's folders and
never the session itself, so the shell can write these where it could not write the
root-level ones; they are working material nothing kingfisher reads back trusts, so that is
accepted rather than fenced. *No migration*: an older session keeps its root-level folders
until it is reaped. *(2026-10-11.)*

**Not built: refusing a save outside the session's folders.** A file tool handed a bare
`result.json` saves it at the top of the session, where nothing is returned, and the turn
reports success. A guard refusing such a save was built once, beside a layout change closed
unmerged, and measured before being rebuilt: two tasks that never said where to save, 21
turns, 68 file-tool saves -- 16 in `/outputs`, 51 in `/scratchpad`, one host path refused
and corrected, none anywhere else -- and nothing at the top of any session from the shell.
The one turn that delivered nothing ran out of tool calls with its result in `/scratchpad`,
which no guard would have changed. The occurrence that prompted it predated `system.md`
saying nothing else in the session is the agent's to write, and the reason it was a
middleware rather than a backend refusal is gone: deepagents' own files are in the
scratchpad, so nothing kingfisher runs writes the session's top. Reopen it on a turn that
strays, not on the argument. *(2026-10-17.)*

## Wiring a store

`SessionStore` and `SessionRoot` were reversed on 2026-09-30 -- the backend is where a
session is, under *A session is its backend*. What stays here is why they were built,
and what outlived them.

**The session directory is the backend root**, and housekeeping is off the request path.
A caller's files are placed in `/inputs` on every turn that brings them, replacing any of
the same name, and what a turn wrote comes back as a list of names.
*(2026-08-16, `session-scoped-api.md`, `durable-session-data.md`.)*

**A deployment names its store in a setting; it does not pass an object.** Passing an
object reaches only a construction site the deployment controls, and the command builds
its own `Kingfisher` with nowhere to point it. The library half went on 2026-09-30 --
`Kingfisher` takes its sessions as an argument -- and the setting stayed for the command,
which alone reads `KINGFISHER_SESSIONS` (*Sessions are always named*).

**A zero-argument factory, not a class and not an instance.** `module:name`, the string
`WireFormat.chat_class` uses. Kingfisher does not know whether a store wants a bucket, a
DSN or a pool, so the factory reads its own configuration; a URL grammar for stores
kingfisher knows nothing about ages worst, and a ready-made instance moves construction
to import time, where "cannot reach the bucket" arrives as an `ImportError` from a module
nobody was reading.

**What is checked is the name, not the building.** A spec that will not parse, a module
that will not import, a missing attribute, a result of the wrong shape: `ConfigError`,
naming the setting. A factory raising its *own* exception passes through untouched -- that
is the deployment's code failing at its own job, in a type their handling may know, and
`built_from_setting` is on the traceback saying which setting reached it.

**Two settings naming one thing are refused at startup, not resolved by precedence**, on
the record so a config assembled in Python obeys it too. Preferring one silently leaves
sessions where the deployment stopped meaning to keep them. Written for the two store
settings, both since removed.

**Environment variables, never a workspace file.** Measured: `writable_roots` returned the
whole workspace, carving out only `skills/`, so `models.yaml` and every definition folder
were writable by the agent's shell -- and a file the agent could edit is not a boundary.
Definitions are fenced now, but only under the fences kingfisher applies; under `off` or
`external` a file is whatever the deployment makes it. The agent cannot reach environment
variables at all: its shell gets an allowlist of five plus `KINGFISHER_SKILLS`. This is
why a store as a workspace *asset* is closed rather than deferred -- the middleware
decision again, one object further in.

**A port a deployment can name gets a runnable contract.** A setting inviting an
implementation with no way to check it is worse than no setting, and the parts easiest to
get wrong matter most: the first kit found the port's security question -- whether a
session id is known -- with no test anywhere, and a store answering `True` for every id
passed 2,255 other tests while letting a caller resume a session they invented. Each check
raises `AssertionError` with the whole story, never a bare `assert`, which `python -O`
strips. They are `tests/checks/runner.py`, `backend.py` and `sessions.py`: kingfisher holds
its own implementations to them, and a deployment writing its own copies them.

**Reversed 2026-10-16: the kit shipped in the library**, as `kingfisher.testing`, importing
no test framework so `pip install kingfisher` gained no test dependency. It left because
the runtime package carried test code and the public API named tuples of functions. What
each check holds is written on the interface it is about, and the two that only look
stayed in the core as `refuse_unusable_backend`. *(`core-and-plugins.md`.)*

**The backend stays kingfisher's, and a store as a workspace asset is refused.** *(The
first half was reversed on 2026-09-15, below; the refusal stands.)* The backend is not
"where files live": it fences every shell command, refuses host paths, and its route table
is what makes `read_only_permissions` legal -- deepagents refuses `permissions=` on a
backend that executes unless every rule is route-scoped. Routing inputs to a store would
break what `plugins/sessions/prompts/shell_here.md` tells the model, *"nothing in the
workspace is out of the shell's reach"*, leaving the agent able to read its inputs and
unable to run anything over them. Object storage reaches a session as a mount at
`<workspace>/sessions`, or through a deployment's own `Sessions`.

**What the agent addresses is a table in `kingfisher.core.layout`.** One entry per path:
whether the composite mounts it, which scope denies writes under it, whether its members
are generated per catalogue. `plugins.sessions.backend` turns the entries into mounts and
`core.agent.assembly` the scopes into deny rules. It exists because one route was three
facts in two modules -- a path constant, a mount in a dict literal, and a
`FilesystemPermission` typing the path again -- which must agree, since
`FilesystemMiddleware` refuses `permissions=` unless every rule is scoped to a route, and
nothing made them agree but care, with the failure arriving at a turn. The leaves are
named once and everything is composed from them: a second spelling is how two halves of a
layout drift apart. *(2026-09-07.)*

**Writability hangs on a scope, not on a route.** `/skills/**` is one rule covering the
catalogue and every bundle's mount; a rule per route would grow a deployment's permission
list with its bundles, protecting nothing more.

**Agreed as "a test ties them" and built one step stronger, because a rule refused the
agreed form.** `routed_paths` read only by a test is what
`test_nothing_is_defined_for_tests_alone` catches, and would have left `routed=False` as
documentation. So `backend_at` keys its mounts off the table, and a route declared with
nothing to back it raises at build. **Unrouted paths are in the table** too -- `/outputs`
and `/scratchpad` reach the shell's backend -- because an absence from a dict literal is
not a record.

**The table outlived its readers anyway, past the rule meant to catch it.** Once the routes
came off a `Layout`, the module-level `ROUTES` and its three functions were read by tests
and by a test pinning them to `Layout.routes`, and `test_nothing_is_defined_for_tests_alone`
passed: it matched names by spelling, and `Layout.denied_scopes` was called. Both dead-name
rules now resolve a module-level name to the module defining it, and count it live only if
something live reaches it, so a table read only by dead functions is dead too. A method is
still matched by spelling, since an attribute read cannot say which class it means. The
first walk found the table, its three functions, three route constants only it read, and
`subagents.spec.DIRECTORY`, alive on `agents.spec.DIRECTORY`'s import. *(2026-10-19.)*

**The rename reached everything the agent addresses and neither edge the caller does.**
`Layout` threaded the leaves through the routes, fences, prompt, ledger, sweep and
`doctor`, while the module where a caller's files go in and results come out read the
module constants. Measured through one real turn under a renamed layout: `artifacts` came
back `()` with the report sitting in the session, `read_artifact` refused the configured
names, and the caller's file landed at an inputs route that layout does not mount -- so it
fell through to the session root, a folder nothing routed, hardened or fenced, and the
shell rewrote it with exit 0. `core/session/files.py` takes the layout as an argument and
imports no route constant, so the names are not there to be spelt again. The same pass
found a renamed scratchpad laid out `0o755` instead of `0o700`, the scaffolding having been
handed one leaf; `ensure_session_layout` takes the whole `Layout`.

**None of it was visible, and the fixture is why.** Mutating the constants reddened
nineteen tests, all on the default layout. The one renamed-layout rule pre-created its
session *passing the argument production omits*, and `tests/conftest.py`'s `start` laid
out the default folders whatever the configuration said, so a measurement of production
was a measurement of the helper. It takes the layout now, and each renamed case is paired
with the default, because the default working is exactly what hid this.
*(2026-10-15, from an architecture review.)*

**Rejected:** kingfisher shipping an S3 store behind a closed table like the shipped wire
formats, which would make this package the owner of every backing store anyone asks for.
A backend factory on `Kingfisher` was deferred at the same time, and opened on
2026-09-14, below. *(2026-09-04, `a-store-a-deployment-can-name.md`.)*

**The seam is open, and the reason is tenancy rather than capability.** A remote runtime
was already served by a runner and a mount; neither survives a deployment that forbids
one caller's session reaching another's. A mount is established once, outside the
process, before any session exists, and giving a runtime-created session its own needs
privileges: a container holding `CAP_SYS_ADMIN` is one command from the agent's shell
remounting its workspace executable. So every session sat on one mount, separated by a
path prefix -- the shared storage a per-caller route was declined on for want of a tenancy
argument. A deployment that forbids the sharing *is* that argument. The backend is opened
per turn for its session, so each caller can have its own filesystem, separated in the
wiring rather than by naming convention. The deferral's condition, "routes only, never the
default slot", was what was wrong with it: the default slot is the shell and every
unrouted path, so reserving it ruled out the one deployment that needed the seam.

**Per turn, never one instance.** A backend is rooted at a session, so one instance handed
in at construction is one filesystem for every caller -- the leak a deployment replaces the
backend to avoid, written where nothing looks wrong. What is handed in opens a backend per
turn, and `LocalSessions` refuses a runner that is not a callable with a `TypeError`. A
setting waited until something wanted one; the command did. *(2026-09-14.)*

**A deployment names the filesystem its agents run on, and kingfisher no longer picks
one.** The reason is that the backend is the security boundary, and the honest version is
narrow. The default was never lax -- a fence around every command, host-path refusal, the
route table a read-only rule needs -- so requiring the parameter makes no deployment safer
by itself. What it buys is that nobody wires the service without learning the boundary is
there; the cost, named, is that a parameter you must type is one you notice you can
change. Written down because the argument is tempting to overstate. **What replaces the
old shape's protection** -- being handed the default kept the route table and confinement
by doing least -- is `refuse_unusable_backend`, asking `why_no_shell` and
`unrouted_denials` of every backend opened. Both only look, costing a turn nothing; the
kit's checks that write files and run commands stay a deployment's to run.

**Considered and rejected:** requiring the old optional adjuster unchanged, ceremony with
the default still built inside; a narrower `(session_dir) -> backend` factory, making the
deployment build the closure and drop whatever else the default was handed; and both a
required factory and the optional adjuster, two parameters for one question.
*(2026-09-15, in three slices.)*

**Not kept: a default for `run` and `stream`.** As conveniences over a default
`Kingfisher`, with `formats.md` teaching `run(Request(...))` first, they carried one -- in
the signature, then in the body so a graph caller was not handed one it never named.
*Reversed 2026-10-16:* they take `sessions` with no default, as `Kingfisher` does; a
default there was the core picking what a deployment's agents run on.

## A session is its backend

**Every read and write of a session's files goes through that session's backend,
kingfisher's own included.** The agent reached a session through the backend a factory
returned; kingfisher reached it through a `Path` and assumed they were one directory.
With a backend that runs elsewhere they were two places: a request's files went where the
agent could not read them, the report it wrote came back as nothing, and the turn said it
had worked. That covers a request's inputs (`upload_files`), what a turn left (`glob`, and
`download_files` for `Session.artifact`), the pinned agent, the conversation and a paused
turn (`HarnessFiles`), and the turn lock. *(2026-09-30, built in four slices.)*

**The backend is opened by a `Sessions`**, per turn with a session *id*, because a backend
that runs elsewhere has no directory here; and asked what only something seeing every
session can answer -- `sessions`, `mark_used`, `size`, `delete`. Each takes the `Config`,
because the default is one object serving every workspace. A deployment building on
`LocalSessions` subclasses it, and a plain function is refused: it can build a backend and
cannot say which sessions there are. `backend_at` is public because building on the
default needs it.

**The lock is on the sessions**: `claim`, `release`, `held` -- a create that fails while
another claim is live, which `write` cannot be. Not chosen: a lock beside the backend,
which asks less of an adapter and gives a session two things to wire to one place.

**A claim answers a token, and only that token releases it.** `release(cfg, session_id)`
removed whatever claim was there, so a turn that outlived `stale_after` ended the claim
of the turn that had taken it over, and the stale bound -- the turn deadline plus the
longest model timeout -- was all that protected it. A takeover was check, delete,
create, and two callers judging one dead claim at once could both win: forced, the
second deleted the claim the first had just taken. Now the stale claim is renamed
aside under a name derived from it, so only one rename succeeds, and checked to be the
claim judged before the slot is taken. What is left needs a turn outliving
`stale_after`, as before, and only in the instant between reading a claim and moving
it. Not chosen: a separate lock for takeovers, which a crash would hold for good. The
interface changed rather than `LocalSessions` alone, on the maintainer's call: a token
is the only way a release can say whose claim it gives back. *(2026-10-19.)*

**`SessionRoot` and `SessionStore` are gone**, and `KINGFISHER_SESSION_STORE` and
`KINGFISHER_SESSION_STORE_FACTORY` are refused rather than ignored: ignored, a deployment
relying on one for durable sessions would lose them on upgrade with nothing said. Keeping
a session when the machine may not is a durable backend's job, or storage mounted at
`<workspace>/sessions`. A `synced(backend, store)` wrapper lost, as a second answer to one
question. The old root's one check that mattered -- two sessions are two places -- is
`two_sessions_are_kept_apart`.

**`/.harness` is in the shell's reach on a remote backend, so it was defended twice**:
`shell_denied` drives `execute` at `/.harness` and `/inputs`, and kingfisher signed the
pin, the conversation and a paused turn with `KINGFISHER_SESSION_KEY`, an HMAC bound to
the session and file name, refusing anything unsigned.

**Reversed: the session key.** Nothing signs what a session keeps; the shell's fence is
the one defence, and `doctor` says where it cannot see one. The key was configuration for a
threat only the deployment's runtime can shape -- which backend, where its shell runs, what
it can read. Where kingfisher fences the shell no key was needed, and where it does not the
key was only as safe as where it was kept: with the sandbox off, the shell read it out of
a `.env` on the first try, and sandbox-off was a case the key was required for. Protection
a backend needs belongs to the runtime that brings it. `doctor`'s `session files` check
warns for the sandbox off or external and for sessions of a deployment's own, pointing at
`shell_denied`; a key still set is reported as retired. A paused turn needs no signature:
no prefix of a paused state deserialises, measured at every length and held by
`test_no_part_of_a_checkpoint_resumes`. Not taken: an optional key on the backend, keeping
signing code for a protection nothing requires; and session state out of the shell's
reach, splitting a session back into two places. *(2026-10-01.)*

**The run log is `RunEvents`, not session state.** It was the one file appended to while
a turn ran, which a backend cannot do, and most wanted for the turn that crashed. Events go
to the `kingfisher.run` logger or a sink the deployment passes, carrying `session_id` and
`turn_id`.

**Still local, on purpose**: `LocalSessions`' confinement, because confining a shell means
something only on the host that runs it; and a supplied runner, built from
`<workspace>/sessions/<id>`, because a runner is about where commands run on this host.

**Named for what they are: a backend is one session's, and a `Sessions` opens them.** A
`backend=` that refuses a backend reads as a bug. A session is opened by a named `open`
rather than `__call__`, which collapses the constructor's two checks into one and gives an
async twin a name to follow. "Factory" means only what a setting names, a function of no
arguments, as `KINGFISHER_WIRE_FORMATS` names one. *(2026-10-02; the present names are
from *The core and its plugins*.)*

**The old setting name is refused, which the policy on renamed settings does not do.**
*The deprecation is over* has a stale name reported, because its default may be what was
wanted. Here it cannot be: a deployment that named its own sessions did not want the
command's, and its agent's shell, on this host. So the earlier names are in
`RENAMED_SETTINGS`, refused where the environment is read, stopping a library deployment
too. Not the shim that went, which read the old name in place of the new.

**A pre-built graph does not stand in for sessions.** `graph=` was first refused beside a
backend, then accepted instead of one rather than force 72 call sites to name something
provably discarded. That stopped being true: kingfisher places inputs, collects what a
turn left and takes the lock through a backend every turn, and beside a graph it guessed a
folder under `<workspace>/sessions/<id>` -- so a graph compiled on a backend keeping
sessions elsewhere had its inputs placed where its agent never looked. A graph needs
sessions beside it now, at 127 call sites. Nothing checks that the two reach the same
place, because a compiled graph does not say what it was compiled on.
*(Reversed 2026-10-05.)*

**`sessions` has no default.** A missing argument is reported by the type checker where
the call is written, earlier than any message at construction, and Python refuses it at
runtime for a caller nobody type-checks. *(2026-10-05.)*

**What a session opens is checked where it is opened.** Run only where an agent was built,
which beside a graph is never, the check let a backend with no shell reach kingfisher's own
calls and fail at `claim` with an `AttributeError`. It runs in `_files_for` on every
session opened, for an `isinstance` and a scan of the routes. *(2026-10-05.)*

**The runner belongs to the sessions, not to `Kingfisher`.** `Kingfisher(runner=)` worked
out a host path for every session whatever its sessions were, and an `open` that dropped
the runner got a well-formed backend running commands under the wrong fence, invisible at
runtime. `LocalSessions(runner=...)` builds it in `open` from the directory it lays out;
sessions of a deployment's own decide how their commands run. Kingfisher's own are told
apart by exact type, not identity, since a subclass can return its backend with anything
changed. *(2026-10-05.)*

**What a session serves under `/skills/` is checked against what kingfisher read.** Grants
and read-deny rules come from kingfisher's own reading; the agent reads through its
backend, which a deployment's own may serve from anywhere. Uncompared, a granted skill
could be missing from the agent's index, and one kingfisher never read had no deny rule and
stayed readable whatever a request narrowed. `a_session_serves_the_skills_kingfisher_read`
lists every source through one opened session with deepagents' own lister and compares
names and paths with the folders read directly -- **by what is served, not what backs a
route**, which would refuse a backend serving the same folders another way and pass the
right kind over the wrong folder. **In the kit rather than on every open**: it lists every
source, for a wiring mistake that does not change between turns. **Bundles and mounts come
from one list, `skill_mounts`**, which `backend_at` mounts and the check reads.
*(2026-10-11.)*

## A tool's path is the backend's path

**A workspace tool's `path` is resolved by the session's backend, under the turn's rules,
one path at a time.** Joining the virtual path onto the session directory was a second copy
of the backend's mapping, drifted three ways, each measured on 2026-09-30: it handed a tool
`<session>/skills/...`, which does not exist; the real `/.harness` pin and conversation,
which the file tools are refused and the shell fenced from, though a tool runs in this
process outside every fence; and, on a backend keeping sessions elsewhere, a local path to
nothing.

**In order, per path:** the turn's rules as `read_file` applies them; where the backend
keeps the path on this host -- a composite's routes, a `FilesystemBackend`'s root, or the
backend's own `host_path`; then containment against the root that answered, links
resolved. A path not kept here is refused naming `ToolContext`. `SessionPaths` holds the
turn's `PermittedBackend`, which reads its rules when it checks, because `build_agent`
builds the guards before it finishes adding rules.

**Per path, not per backend.** "Refuse path tools on a remote backend" was the wrong rule:
a backend may keep all, some or none of its files here, so the question is asked of the
path at the call. A startup rule would refuse a deployment over a tool it never calls.
**A subagent is handed the parent's paths**, rather than guessing its session from
`backend.workspace`, which a deployment's backend need not have.

**A path under a write-denied scope is handed over.** Under `/inputs` that is safe on the
default backend, the directory read-only on disk; under `/skills` it is not, the catalogue
being writable and shared. Decided for a `fill_template(path="/skills/report/template.md")`
that reads, against a `tidy_whitespace(path=...)` that would rewrite for every session:
workspace tools are the operator's reviewed code, every shipped one only reads, and
refusing would leave a path tool unable to read a skill's file.

**`host_path` is part of the documented backend contract**, optional, and checked by
`a_host_path_stays_in_its_session`, which refuses an answer naming another session's file.
Decided for a deployment keeping sessions on a network mount, whose path tools keep
working; a backend's answer is taken at its word, there being no root to hold it to, which
is what the check is for. **The shipped path tools read through `ToolContext`** and
`download_files`, so the examples work on any backend and teach the route that does.
`kingfisher decide` asks the sessions whether a session exists rather than looking for a
folder, and nothing in a result is a host path, so a result serialises whole.
*(2026-10-01, built in four slices.)*

**`reject_host_path` is scoped to the workspace, and was scoped to one session.**
`WorkspaceScopedBackend` stored a `workspace` and was handed the session directory --
the one value that made the suggested virtual path right, so the scope came with it.
Everything else fell through to `HOST_ROOTS`, which names no workspace. Measured on
2026-10-01: on a developer machine every probe was refused, only because `tmp_path` sits
under `/private/`; at `/workspace`, which `Dockerfile` sets, a sibling session's file, a
skill, `models.yaml` and an agent definition were **accepted**, and a write to one was
mirrored inside the session -- what `core/agent/prompts/file_tools.md` tells the model is
refused. The guard takes both: the session for the suggestion, the workspace for the
scope; anything else under the workspace gets no virtual spelling, since inventing one
would send the model somewhere else again. **The rule that named this case could not reach
it**: `test_a_host_path_to_a_file_tool_is_refused_not_mirrored` passed on the host-root
clause, every suite workspace being a `tmp_path`, so `reject_host_path` is driven directly
against a `/workspace`-shaped one. *(2026-10-06, from an architecture review.)*

## The command line

**The command is a consumer of the library, not an insider.** Seeding and the inventory
became public API to make that true, and the library answers "what does this workspace
offer" with a record. Bare `kingfisher` prints help. Publishing was deferred, deliberately,
and asked twice. *(2026-08-17, `a-command-worth-shipping.md`.)*

**Reversed: the command is for what the library cannot do for itself.** It kept the
surface at `seed` and `list`, and measured the library's completeness rather than the
user's -- "the library can already do it, in Python" is true of every command-line tool,
and here meant an editor, four lines, and knowing a bare task string is refused. Replaced
by *what does a person at a terminal need to do*: `run` is the first verb. What survives:
a bare driver invocation spends real money on the smoke, wrong for a stranger's first
command, and a verb with a required task cannot be reached by accident.
*(2026-09-04, `the-verb-that-runs-a-task.md`.)*

**Housekeeping is a verb, and it goes in through the front of the library.** `kingfisher
sessions` lists id, idle time and cost; `kingfisher reap` deletes at the TTL, at
`--older-than`, or one `--session` whatever its age. Both build a whole `Kingfisher`, at
1.3s and a refusal without `models.yaml`. A lighter path needing only the directory buys a
fast cleanup that silently leaves behind whatever the configured sessions keep elsewhere.

**Both defaults are about which mistake is cheap.** `--older-than` refuses a bare number
and takes `30m`, `12h`, `7d`, or `0`: seconds would make `--older-than 7`, from somebody
meaning a week, sweep every idle session. Bare `reap` sweeps at the TTL, so the least-typed
invocation is not the most destructive, and when it removes nothing it names what decided,
because a silent sweep's next user deletes the directory by hand. *(2026-09-14.)*

**A run can be told to take its session with it, and only a finished turn is.**
`--delete-session` and `run(delete_session=True)` dispose of the session once the turn
ends. A turn stopped at a bound keeps it and says so: those leftovers are worth something,
and the conversation is what a retry is rebuilt from. A failed deletion does not change the
exit code, which says how the *turn* ended. **Offered on `run` and not on `stream`**: a
generator has no *after the turn* this library controls, and a `finally` fires on
`GeneratorExit` too, so a session would go because somebody closed a loop early. The rule
both need lives in `RunResult.completed`, and
`test_no_surface_decides_for_itself_what_a_finished_turn_is` stops a second copy.
*(2026-09-14.)*

**Rejected: sweeping at the start of a run.** Not on cost, measured first and nothing: a
sweep finding nothing is 1.65ms at fifty sessions and 33ms at a thousand, against a turn of
1.5-1.9s. On shape: a command that deletes as a side effect of being asked something else
has a blast radius nobody reads in `kingfisher run --help`. `reap` is the janitor's door,
and a schedule has cron and `reap()`. Nothing enforces the TTL, and `configuration.md` says
so. The measurement was never the objection. *(2026-09-15.)*

**`run` takes no flag that narrows capabilities.** Its flags say what runs, where, on what
and for whom, and what happens around it. Narrowing is a deployment's concern with better
homes -- `Config(grants=...)` clamps, an agent file declares what it holds -- and
`--without-*` freezes what the workspace offers *now*. The cost: no "without the shell"
from the command line; write an agent that declares it. `--input` was never a candidate for
cutting, `/inputs` being read-only to the agent and so the only way to hand it a file (see
"The names a caller writes follow the folder" under "Sessions: what persists and where").
Nor was `--as`, measured: on a workspace declaring source ids a run naming nobody is
refused, so `run` without it would break exactly the deployments that took access control
seriously. Unlike `list --as`, absent is not the operator's view: a listing reads, a turn
acts. *(2026-09-04.)*

**The answer goes to stdout and everything watched goes to stderr**, so `> answer.md`
keeps the answer and `2>/dev/null` the quiet. A `--quiet` flag was rejected: it asks the
caller for correct behaviour and does nothing for whoever forgets it. **A subagent's prose
is progress, not answer**: the streams keep the voices apart, and an extractor's notes are
not what anybody redirected stdout for. `Progress` moved out of the driver rather than
being copied, since two renderers would disagree about the next event kind. *(2026-09-04.)*

**The exit code carries `stop_reason`, because prose on stdout leaves nowhere else to put
it.** `0` finished, `1` stopped at a bound, `2` never ran -- so `kingfisher run ... >
report.md && publish report.md` does not publish half a report. A code per reason was
rejected: it repeats a stderr line in a vocabulary that grows with `STOP_REASONS`. Errors
that would reach a stranger as a traceback are reported as `2`; `SessionBusyError` keeps
its own branch, because "wait" is different advice from "fix something". *(2026-09-04.)*

**Two of those errors were missing, and `decide` is where it showed.** `DecisionError` and
`AgentError` were in neither `REFUSALS` nor any `except`: one sentence arrived as a clean
`2` from `decide --session X` and as a traceback with exit `1` from `decide --session X
--approve abc` -- the code a script reads as a turn stopped at a bound. `AgentError` is
taken from its own module rather than the front door, since no outside caller has asked;
`SubagentError` is public because one did.

**`doctor` asked whether every *subagent* resolves to a model and said "every
definition".** It exited `0` while `run --agent` refused with the error the row promised
absent. Both kinds are asked over one shared body, because two places deciding whether a
definition can run is how the second half went missing.

**An unreadable built-in set is said to be unknown, not counted as zero.** A middleware
that will not import takes down the probe the built-in set is read off, and `tools_error`
is rightly not set -- the catalogue walked, `middlewares_error` carries the fault -- so the
row printed `0 built in` with an `ok` over ten. `builtin_tools_checked` says so, beside
`shadowed_checked` and `moved_tools_checked`.

**A `source_ids:` line is inert with no vocabulary, and `--json` now agrees.** `as_json`
published every definition's lines with no `source_ids.yaml`, reporting a policy not in
force. Answered in `inventory`, because the record is what both forms are handed, and the
agreement rule compares access fields both ways.

**A refusal offers only the source ids a caller may present.** `expand` listed compounds
too, beside a clause refusing a compound, sending whoever mistyped a name into a second
refusal; `refuse_undeclared` still lists them, since a definition may name one. The idle
column `sessions` prints is floored, since it is read into `reap --older-than`.
*(2026-10-13, from an architecture review.)*

**`help` went, and `--seed`, `--from` and `--all` went off the driver.** `kingfisher help
seed` was `kingfisher seed --help` byte for byte; the driver's seeding flags duplicated
`kingfisher seed`, kept as "the driver you already have open" while nothing else could run
a task. **`--list` stayed, and not as a listing**: it is the only way to reach the driver's
`main` without calling a model, which six tests are built on -- written into the flag,
because it looks as removable as the ones that went. *(2026-09-04.)*

**And what it reads is the syntax of a definition, in both languages it has.** `seed`
decided what a deployment could not build by reading two YAML fields of two kinds, so
three ways of naming an audience were invisible: `datazone:`, which it never looked at;
anything a subagent declared in Python; and a tool's audience, which has nowhere but
Python to live. Driven, on the README's own path: `kingfisher seed`, then exactly the
`source_ids.yaml` the report printed, left `kingfisher list` and `doctor` failing with
`subagent 'quarters': names undeclared datazone 'emea'` -- the shipped `quarters.py`,
copied for months, with nothing in the report naming it. A tool was one coincidence from
the same fault: `sql_query.py` requires `sales_db`, and only two YAML files happening to
require it too put that name in the printed line.

So the reader reads Python as well, by `ast` rather than by importing -- seeding runs
before there is a catalogue, and importing a definition to decide whether to copy it
would run a deployment's code to answer a question about it. By syntax rather than by
text, because `subagents/timestamps/timestamps.py` documents `source_ids` and `datazone`
in its docstring: a reader matching the words leaves a worked example behind. A tool
*package* is decided whole, since dropping the module that names the audience and copying
the rest leaves a folder that is not a tool. And the remedy is keyed by what a file asks
for rather than by the field that said so: told to declare source ids, a reader writes one
line of the two that file needs. The tolerant-reader limit is unchanged and still
stated -- a name computed rather than written out is invisible, as a value the YAML reader
cannot walk is. *(2026-10-19, from an architecture review.)*

**`Seeded` says what it left behind, because three callers said it and two were wrong.**
`seed` leaves a definition alone for one of two reasons with opposite remedies -- middleware
is registered in code, a source id declared in a file -- hence `UNCONSULTED` and `REMEDY`,
held total. The integration driver and `seed_example.py`, the script people copy, each told
a reader to *register* a source id. `Seeded.report()` is every line a person is owed, and
every caller prints it; `test_only_the_record_says_what_a_skipped_definition_needs` finds
any `skipped …` line composed outside `seeding.py`, searching the README, the driver, the
example and `evals/` too. *(2026-09-23.)*

## What doctor promises

**`kingfisher doctor` exiting zero means nothing in the catalogue will break** -- at startup
or on a later request. Five refusals escaped it at once: an agent file it never read; a
middleware directory nothing read; a workspace tool wearing a built-in's name, which made
`doctor` itself crash over a deployment that starts fine; and two definitions naming a moved
tool, the agent half refused *nowhere* -- it ran without the tool its author granted.
*(2026-09-14.)*

**Checking was already separate; it was the separation that leaked.** The proposal to split
validation out and lazy-load at `run` failed measurement twice: `warm()` reads only what a
run needs anyway, a broken file raising while read, so there is nothing to split; and
reading costs 17.5ms for seven tools against ~1,000ms for the runtime. The checking step
was `doctor`, and it lacked coverage, not a home.

**Lazy tool loading was rejected on a structural blocker, not the cost.** A tool's name
lives inside its module -- three of eight shipped names were not derivable from the
filename -- so "import only what is granted" needs a map built by importing everything. A
tool needing a heavy library imports it inside the function: 98% of the saving, 89 modules
and 65ms becoming 1 and 1ms.

**Startup got stricter where the failure had nowhere else to surface.** A broken middleware
module and a shadowed built-in stop `Kingfisher(...)`, about 11ms once; the shadow probe
runs where the workspace defines tools -- the catalogue's or a subagent bundle's -- keeping
it off the tests with nothing defined at all. **An agent naming a moved tool fails `doctor`
and does not stop startup**, unlike the subagent case: it would stop a deployment that runs
today over a file nobody touched, and the promise is that `doctor` tells you.
`test_doctor_sees_an_agents_moved_tool_that_nothing_else_would` asserts both halves.

  - **A bundle is the second door, and it was open.** The probe walked the catalogue only,
    and the comment at its call site said as much: *only with `tools:` nothing else
    shadows*. Driven, a `read_file` in `subagents/surveyor/tools/` reached the subagent's
    spec unrefused and took the name deepagents dispatches: with no `limit` in its schema,
    where deepagents' own has one, every model call that subagent made died with
    `KeyError: 'limit'`; with a `limit`, nothing failed and it had quietly replaced the
    reader that enforces `read_only_permissions`. `doctor` called the workspace healthy and
    exited 0. Both doors are judged now -- a folder's tools and the objects a portable
    entry carries -- for every bundle the catalogue holds rather than only the subagents a
    request activated, since the clash is a fact about the workspace. A bundle that will
    not load is skipped rather than reported here, and a subagent file that will not parse
    leaves the probe no bundles: either failure belongs to the rows that name the subagent,
    and raising it from the tool probe sent a reader to the catalogue instead.
    *(2026-10-19, from an architecture review.)*

**`REFUSALS` is what stops the next one.** The table in `tests/unit/test_refusals.py` has an
entry per catalogue-reading function that can refuse a definition, naming a broken
catalogue that reaches it or why none can, deny by default both ways, with the raise count
checked so a new refusal cannot inherit an entry about another. It counts a raise of a
class the function was *handed*, which `importing.load` and
`documents.require_literal_prompt` do; driving it found a sixth escape, a bundle folder
holding two definitions; and each defect is run and the frame that raised compared with its
key.

**`doctor` asks the fence the questions a run asks.** It kept its own copy of the required
ABI and its own kernel reading, and told a kernel that could be fenced to wait "until that
is wired" beside the fence's own warning naming the missing package. It reads
`landlock_abi`, `landlock_ready` and `REQUIRED_LANDLOCK_ABI` through the confinement module,
so the two cannot disagree. *(2026-09-15.)*

**The inventory answers by kind, so a consumer walks rather than lists.** Every reader
spelled the kinds out, a hand-written list of a growing set that went wrong twice: `doctor`
read three of five kinds, then four, and `middlewares_error` was computed and read by
nothing for a release. `Inventory.by_kind()` answers a `Catalogued` per kind, written out
so a checker follows the attributes, and
`test_the_per_kind_view_covers_every_kind_there_is` holds it total against
`DEFINITION_KINDS`. `skills` has no single error, a skill failing one at a time, and
`bundles_error` is no kind, a bundle being one subagent's folder. Each `*_error` is driven
through `failed` one at a time, since that is how one goes missing. Not named `Kind`: the
origins record has one, meaning what sort of *place*. *(2026-09-23.)*

**A non-zero listing says why, to the person and not only to a script.** `middlewares_error`,
`bundles_error` and `moved_tools` each made `kingfisher list` exit 1 with nothing in the
text saying so, because the exit code and the renderer were two hand-written lists, and
every rule asserted on `--json` -- passing while the person looking at a broken workspace
was told nothing. Each `*_error` must now make the listing non-zero **and** be said in the
text; troubles that are not a kind's error are held by a table pinned to `failed`'s source.
**`render` has no branch now**: an early return when `tools/` would not walk dropped every
section not remembered on that path, `_access` among them, so one unparseable `.py` hid the
access report. **The middlewares section prints the name and not the module**, since a
`middlewares/*.py` class's `__module__` is a made-up unique name; an imported class is
printed `(from ...)`, as the subagents section marks `carried`, and `Inventory.middlewares`
carries the distinction so `--json` is honest too. *(2026-09-27, from an architecture
review.)*

**The inventory's `try` blocks each keep their own catch-set**: exactly the kinds the block
reads, each extra type carrying its incident -- `CapabilityError` in `_tools`, because the
tool probe refuses a workspace tool shadowing a built-in; `SubagentError` in the agents
walk, because resolving delegation reads the subagent catalogue. One catch-set would say
each block might fail any way. The agents walk could be narrowed to `AgentError` with all
2,104 tests green, turning a broken subagent into a traceback over fine agents;
`test_a_broken_subagent_does_not_take_the_agents_half_down` holds it. The workspace tool
catalogue's read has its own block, since inside the one feeding `bundles_error` a broken
`tools/` module showed as both `FAIL tools` and `FAIL subagent tools` with no subagents.
`shadowed` answers `None` where that read failed, and `shadowed_checked` says so: an empty
mapping cannot tell "nothing shadows" from "not compared". *(2026-10-11, from an
architecture review.)*

**Removed: `SkillError`.** Exported as an error a caller must tell apart, and raised
nowhere once uploads went: a skill's failures are carried -- `skills_unloadable`,
`skills_misplaced`, `skills_misfiled`. No compatibility window, on the 0.1.0 precedent:
`except SkillError` becomes an `ImportError` rather than a handler that never fires. **What
let it survive is worth more than the removal**: the export rules check names against
tables and *never ask whether a published error can occur*, which
`test_every_error_this_table_names_can_actually_happen` does, counting a class handed to
somebody who raises it. *(2026-09-24, from an architecture review.)*

## Where a deployment reads from

**The capability flags are `KINGFISHER_*_ENABLED`, and the old names are read by
nothing.** `KINGFISHER_SKILLS` answered both whether skills are wired and -- exported to
the shell -- *where* the catalogue is. A deployment writing the path set a flag to a value
the parser (`1`, `true`, `yes`, `on`) reads as false, and skills went off silently:
`'/workspace/skills' -> skills enabled: False`. All four were renamed, so no reader must
remember which carries a suffix, and `_ENABLED` says "whether" is not "where".

**Not solved by inferring the flag from the catalogue**, worse four ways: the skills
section is in the cached system prompt, which would change when a file appears; several
deployments may share one reviewed definitions folder, and inference opts them all in;
"I set it and it is off" becomes "I set nothing and it turned on", leaving nothing to grep;
and a capability would come from data rather than configuration. *(2026-09-05.)*

**The deprecation is over, and what replaced it is `doctor` rather than a refusal.** The
shims reading both names are gone. Renaming an environment variable is the one rename that
fails in silence, but refusing to start was not taken: a stale line is untidy rather than
broken, and stopping over it makes an upgrade look like a failure. `health.RETIRED` and
`RETIRED_PREFIXES` carry the dead names and `doctor` warns about any set -- a place to find
out rather than a thing that happens to you. A list of names nothing reads is what nothing
else can check, so `test_no_message_names_a_variable_nothing_reads` holds a name retired or
read, never both, and `test_every_retired_name_is_one_the_file_lists` holds it against
`.env.example`, where a typo would warn about a variable nobody has. Prefixes are matched
rather than enumerated, or the copy misses the next setting; `KINGFISHER_SERVICE_*` joined
`KINGFISHER_SERVER_*` when the service went. *(2026-09-10.)*

**One record, `Origins`, and every surface prints it.** Kingfisher read from eleven places
and nothing said which: `kingfisher list` named four, `doctor` one, the library none, and
`tools` none at all, the headers being hand-written lines. `Inventory` carries the record
and both commands print it, so a new place appears in both untouched.
*(2026-09-02, `where-this-deployment-reads-from.md`.)*

**It reports what was loaded, not what was configured.** `Config.catalogue_roots` is the
fallback -- a `Kingfisher` reads only the folder `Config.definitions` names, but
`inventory` may be handed a `Definitions` of the caller's own -- so a report from
configuration alone is wrong for the caller that read somewhere else. `Origins.of` does
not call `resolve_definitions`, which refuses a definitions folder that is not there: a
report must say where an unseeded deployment would read, not fail on it.

**`sessions` is asked of the sessions in use, never of the setting that built them.**
`inventory` held only a `Config`, so every surface printed `<workspace>/sessions` whatever
was configured: measured on 2026-10-11, `doctor` and `kingfisher list` printed
`./sessions` where the library said `<supplied>`, three rows above a `doctor` check that
knew better. The `sessions` command printed a path beside a correct listing, sending the
one deployment the setting is for to a directory its sessions never touch; it prints a path
only where kingfisher knows it, a sentence otherwise, and `"root"` `null` in JSON. The
surfaces are driven together, since a rule reading one would have passed, with
`test_both_forms_of_the_listing_name_where_they_read` as the control on kingfisher's own
sessions, where the bug was invisible. *(2026-10-11, from an architecture review.)*

**Each entry carries a kind, not a formatted string.** `default` is decided by comparing
against the derived location, so naming the default path explicitly is `default`;
`relocated` is any other configured path, `overridden` means the configuration is not what
is read, `supplied` is a repository with no directory, `unset` carries where it looked.
`--json` and the line `Kingfisher` logs at construction read it, so "nothing configured"
and "you handed me one" never arrive as spellings to match on.

**`Config` remembers where it looked for `source_ids.yaml`**, as `access_source`, because
with no file there is no record to hang a path on, and "not set, and here is where I
looked" is the one line that makes a policy written one directory off visible -- otherwise
the deployment comes up reachable by everyone and says nothing.

**The library's first logger is `kingfisher.origins`, and not `kingfisher`.** One INFO
record per construction. `print` would make the library unusable by a server, and
`warnings.warn` means "probably not what you meant". The name was load-bearing: `kingfisher`
is every other logger's parent, so raising it to INFO for this would have turned on the
audit trail a deployment had left off, while there was one.

**Reversed: `doctor` says where a catalogue came from, not only what it holds.** It
gained two rows a path alone could not express. One warned of an empty catalogue at a
relocated path, because resolving a catalogue *created* its directory, so a mistyped root
said `0 defined` as a fresh workspace does; it went when a start stopped making the folder
and began refusing a missing one. The other said a configuration was being ignored, which
needed `examine` to take the inventory rather than build one agreeing with `cfg` by
construction. Its premise was `Kingfisher(catalogue=)`; once #784 removed that, `doctor`'s
inventory and `cfg` read the one folder, and the row could not fire -- breaking it turned
red only a test handing `examine` an inventory built by hand. Removed rather than kept
guarding nothing. `examine` still takes the inventory, so the header `doctor` prints and
the rows under it come from one read. *(2026-09-02. Reversed 2026-10-13 and 2026-10-19.)*

**Rejected along the way, each for its own reason.** In-code configuration as a first-class
path -- `Models`, `Endpoint` and `ModelProfile` are constructible, but this was about making
YAML and directories legible, not replacing them. Merging `WorkspacePaths` into `Config`,
which requires a `Models` found through `WorkspacePaths`, so it would make `Models.resolve()`
partial. Nesting one in the other -- 7 constructions and 38 reads, against 17
`replace(cfg, ...)` calls that would become nested. Serving the record over HTTP, which
authenticated nobody. And a `Config.paths` property nothing would read.

## The HTTP service

**Reversed: an HTTP service in this repository.** `kingfisher-service` was removed
because nobody ran it. It was already its own distribution, imported nothing past the
front door, and no container or compose file started it, so it came out as a deletion
rather than a migration; the code is in the commit that removed service/. The entries
below are what it decided while it existed. Library features it alone used were each decided
separately, after counting what still used them; `file_store_named`, which turned its
setting into a store, went with it. *(2026-09-15.)*

**Taken: the async turn path goes.** `astream` and `arun` existed so turns could
overlap on one event loop, which a server needs and nothing left here is; their only
callers were their own tests and one spike. They were also the second copy of the turn
-- `_astream_turn` repeated `_stream_turn` down to its cleanup, and mutation testing
had found a flag the copy set by hand. A caller wanting turns to overlap runs `run` on
threads. *(2026-09-15. **Half of this was reversed** -- see *Reversed in half* below,
which is where a caller should start: `astream` is back and turns overlap on one loop.
What stayed removed is the second copy of the turn.)*

**They do overlap, and it took two checks to say so.** Eight turns in eight sessions:
**26.14s one after another, 5.27s together, 4.96x** on the live gateway
(`spikes/concurrent_turns.py`). Not 8x: construction is CPU-bound and unhelped by
threads. `test_turns_in_separate_sessions_are_in_flight_at_once` is the half a gateway
cannot answer -- a barrier where the model call goes, driven rather than timed, with a
control so it cannot pass against a barrier one turn fills. The spike is the half a
fake model cannot answer: an endpoint serving one request at a time would make the
threads pointless however good the library is. The measurement also found a bug:
eight concurrent turns crashed on macOS because the sandbox profile's scratch file was
named after the process, and threads share a pid, so the advice above had been
unrunnable on the platform it is developed on. Fixed in `write_atomically`, whose
scratch is named per thread too. *(2026-09-18.)*

**What `write_atomically` prevents was itself unheld, and the window is fail-open.**
Its two rules -- no scratch left beside the profile, eight concurrent writers all
succeed -- are both met by a plain truncating `write_text`: replacing the body with one
left all 2,390 tests green. A torn profile does not fail closed. It is `(version 1)`,
then `(allow default)`, then the denies, so a prefix is a *valid* profile permitting
everything, and `sandbox-exec -f` re-reads the file at every command while `resolve`
rewrites it per build. Driven through a real 22-line profile, `cat` of a file under the
operator's home is refused at full length and **prints the file** at two lines. So the
rule watches the rename: what sits at the profile's path the instant before the replace
must be the whole of the last profile, which also kills a scratch *copied* over the
profile, the plausible wrong fix. The race itself is not provoked, which would be
timing-dependent. The scratch sits beside the profile because `os.replace` is atomic
only within one filesystem.

**A rule was written and withdrawn here.** It asserted that a prefix of the profile
denies nothing -- true, and the premise above -- but it would go red the day the
profile became deny-by-default, an improvement, so it was a rule against a better
version of the code. The premise is prose in the surviving rule's docstring instead.

**The new rule shipped without `@macos` and CI's Linux job is what found it.** On Linux
`resolve` writes no profile, so the assertion was a `FileNotFoundError` rather than a
skip, and every other job is macOS. It was missed by reading from the `def` line down,
with the neighbour's decorator one line above. The marker now has a rule,
`test_every_rule_reading_the_written_profile_is_marked_for_macos`, and its predicate
took two attempts: "calls `resolve`" named eight platform-independent rules, and
itself, because `"resolve("` is a literal in its body. It matches the syntax tree
instead -- a rule that both drives `resolve` and reads the profile it wrote.
*(2026-10-06, from an architecture review.)*

**Reversed in half: `astream` and `arun` are back, the second copy of the turn is
not.** The removal missed an asymmetry. A caller on an event loop who wants an *answer*
writes `asyncio.to_thread(kf.run, ...)`; one who wants events as they arrive has to run
the turn on a thread and hand each event across a queue -- twenty lines, easy to get
subtly wrong, and described nowhere. Streaming is what an async caller wants, so the
workaround was fiddliest where it was needed most.

**What is not back is the second copy of the turn.** `stream` drives `graph.stream`
and `astream` drives `graph.astream`, and that is the whole difference: every bound and
translation is in `_turn_outcome`, every release in the shared ending, and the state is
one `_Turn` record.

**Driving the graph for real is what makes cancelling immediate**: **0.00s** against
**9.71s** for a thread-backed turn, measured on a ten-second model call. A cancelled
`await` abandons the request; a thread has to be waited out.

**What the async path asks of a deployment, and the sync path does not.** The
`a`-prefixed middleware hook is the one that runs there, so a middleware written only
as `wrap_model_call` raises the first time an `astream` turn reaches it -- loudly,
measured against langchain's own machinery. It cannot reach a caller of `stream`, and
the async pair had no callers when it landed, so it is a requirement of a new API
rather than a break. `guides/middleware.md` says it where a deployment reads it.

**Two things that had to be closed by hand.** `_built_turn` runs on kingfisher's pool,
because it is CPU-bound and on the loop would cost every other turn 15-46ms. And
`astream` closes `_astream_turn` itself: an async generator dropped by another is
finalised by the loop's `shutdown_asyncgens`, not when it goes out of scope, and there
is no async spelling of `yield from`'s close. Without it a caller who stopped reading
left the session claimed until the loop ended;
`test_a_cancelled_turn_does_not_keep_running_behind_the_caller` fails with
`SessionBusyError` when it is removed.

**Not taken: a thread-backed `astream`.** The first version stepped the sync turn
through `asyncio.to_thread`, the `BaseLoader.alazy_load` shape, because sync-only
middleware raises under a native async graph -- which looked like a reason to avoid the
native path rather than a requirement to document. Its cancel had to wait out the
thread, up to 120 seconds. A hand-rolled thread and queue before it passed every
behaviour test while dropping the caller's context, since a bare `threading.Thread`
starts with an empty one. `findings.md` keeps the measurements. One cost is not free on
either path: a turn that calls `eval` holds its own QuickJS runtime.

**Cancelling waits.** A cancelled `astream` returns once the turn has ended and its
session is free. Returning sooner would leave a window in which a retry gets
`SessionBusyError` for reasons the caller cannot see, which is worse than a slow
cancel. `test_a_cancelled_turn_does_not_keep_running_behind_the_caller` fails with
that error when the wait is removed.

**Methods on `Kingfisher`, and no module-level pair.** `run` and `stream` have
module-level conveniences; the async twins do not, because a new name in `__all__`
needs a witness and no caller outside this wheel has asked. The day one does, it is
four lines. *(2026-09-18.)*

**A cancel during setup left the session claimed.** A cancelled `await` on
`asyncio.to_thread` returns at once while the thread goes on, so setup claimed the
session and handed the turn to a future nobody read: with setup held 0.3s and cancelled
0.1s in, the claim stayed on disk until it went stale. Every cancellation test had
cancelled during the model call. Now the cancel waits for setup and ends the turn
through the lifecycle's own ending -- *Cancelling waits* again.
`test_cancelling_during_setup_leaves_the_session_free` lets setup go only after the
cancel has landed, so it cannot pass by setup finishing first. *(2026-10-02.)*

**Kingfisher's own thread pool, one per process.** `asyncio.to_thread` borrows the
loop's default executor, `min(32, cpu+4)` workers, and a call blocking on a round trip
holds its worker throughout: at 50ms, 200 concurrent calls took 917ms there, 73ms on
256 threads and 56ms awaited natively. Host work goes through `core.session.threads`,
which copies the caller's context as `to_thread` does. One pool, because threads are
the process's to spend; `KINGFISHER_THREAD_POOL_SIZE` is read when a service is built,
and a second size is refused there, naming both, rather than queueing behind whichever
service came first. *(2026-10-02.)*

**The session reads have async twins, and each read is written once.** `asession`,
and `apending` and `aartifact` on the `Session` it returns, asked for by a deployment
on an event loop -- the witness *Methods on `Kingfisher`* was waiting for. They await
the ports' own async methods rather than taking a thread each: at a thousand
concurrent 50ms calls, 4491ms on the default executor, 257ms on 256 threads, 62ms
awaited.

**Written once, as the port calls a read makes.** A sequence is a generator that
yields each call and is sent the answer; `drive` makes the sync call and `adrive`
awaits its `a` twin, so every check between the calls, and their order, exists once.
Two hand-written copies of each read was the alternative, which is what the second copy
of the turn was. Every session I/O helper is one -- `HarnessFiles`, the transcript and
pause formats, the pinned agent, placing data, what a turn left -- kept together in
`core.session.store`. *(2026-10-02.)*

**A sequence called without a driver does nothing, which is how this shape fails
quietly.** `test_every_sequence_is_driven_or_delegated_to` requires `yield from`,
`drive` or `adrive` around each call; it found the test helper `pin` writing no pin.
Finding calls by name needs names nothing else uses, so `HarnessFiles` says `fetch`,
`store` and `drop`, not the backend's `read`, `write` and `delete`;
`test_a_sequence_s_name_is_not_also_a_plain_function_s` keeps it so.

**A cancel waits for a call that changes something, and abandons a read.**
*Cancelling waits*, one level down: a claim taken or a sandbox opened during the
cancel would otherwise reach nobody, so the sequence is handed the answer before the
cancel. Work on this host goes to the pool and is always waited for, because a thread
runs to the end regardless.

**`aopen` and `asessions` are defaults on the protocol**, as is every async twin
`Sessions` declares, each running its sync method on kingfisher's pool -- the shape
deepagents' `BackendProtocol` gives its own. A subclass of `Sessions` inherits them;
a class that only matches it must write them, since `isinstance` asks for every member,
which is the breaking half. The session's *backend* is read through deepagents' async
methods, which for its filesystem backends use the default executor -- a local read; a
backend a round trip away overrides them.

**`session()` opens a backend only when something reads it.** With nothing to narrow
the caller, the pin decides nothing, and opening a remote backend to say a session
exists was a sandbox for nothing. *(2026-10-02.)*

**Deleting a session has an async twin.** `Session.adelete` and
`arun(delete_session=True)` end in one sequence, written once and awaited. `adelete`
is a default on the protocol, and the kit checks it. *(2026-10-05.)*

**Setup is one sequence for both loops.** `_prepare_steps` is the lookup, the open,
the pin, `mark_used`, the claim, the quota, the data, the pause, the agent and the
transcript; only `_built_turn` is host work -- inline for `stream`, on the pool for
`astream`. A refused request leaves no turn behind because `allocate_turn` makes
nothing and the sequence's order keeps that promise;
`test_a_refused_request_starts_no_turn_and_keeps_no_claim` says so.

**A refused request takes back a session it made.** Opening a session makes it, and the
refusals read it through what was opened, so a refused `kingfisher run` left an empty
`default`. Setup asks the listing before the open, and on a refusal deletes a session
that was not there, before the claim goes back. Not taken: making the session after the
refusals, which need its backend to read the pin and the pause; and deleting one found
empty, which would take a session `create=True` made for its first turn. *(2026-10-19.)*

**A cancel can land at any of setup's steps now, and each is tried.** The claim goes
back from the sequence's own `except`. A cancel during the build has no later step to
go in at, so `adrive_finished` hands the built turn back with the cancel and `astream`
ends it like any turn. `test_cancelling_at_any_step_of_setup_leaves_the_session_free`
counts setup's steps on a warm turn and cancels at each, so a step added later is tried
too.

**Read once, and closed on failure.** The pin is read once and handed down rather than
once per question. `_built_turn` closes a saver it opened for a build that then raised,
since no turn exists for an ending to close it. *(2026-10-05.)*

**A pause is cleared only where there is one, and the pool defaults to 16.** Against
session backends whose every call takes 50ms (`spikes/async_scaling.py`, numbers in
`findings.md`), reads scaled flat once awaited and the ending grew with concurrent
turns. Three of its seven round trips were `clear_pause`, run whether or not there had
been a pause; setup now drops a superseded mark, so only a resume clears one. The
default went from 64 to 16. 4 was asked for and measured: each turn then held a thread
through its ending, ten concurrent remote turns took 1.8x as long as one, and two
hundred took longer than a thread per turn. 16 keeps ten at the cost of one; at fifty,
64 does twice as well, which is why `configuration.md` tells a deployment with many
remote turns at once to raise it.

**The size is per process, not per `Kingfisher`, and nothing is spent until it is
used.** Instances share the one pool, so the number bounds the process -- measured with
an instance per session, the turns came out the same -- and a pool starts a thread only
when work arrives with none idle: after five turns in a row it held one.
*(2026-10-05.)*

**A turn's ending is awaited too, run as a task no cancel reaches.** It had been a
plain function on the pool for `astream`, because on the loop its round trips -- the
pause, the transcript, the artifacts, the claim -- held up every other turn. Still, at
a pool of 16, two hundred turns took 3173ms against one turn's 522ms. The ending is now
a sequence like setup, its host work on the pool, which `stream` drives with no await
and `astream` runs through `finished`, as a task the caller's cancel never reaches,
waited for however often the cancel comes. Two hundred turns take 646ms at 16 and 638ms
at 4: the size no longer decides it.

**That replaces a guarantee rather than keeping it.** A thread nothing could interrupt
also outlived its event loop; a task does not, and a loop stopped outright is the case
given up. `test_the_turns_ending_runs_to_the_end_however_often_it_is_cancelled` cancels
at every step three times over and requires every step to have run. `release` gained
`arelease`, since the protected task is what keeps it from stopping halfway. The pool
stays 16 because session backends whose async methods are the protocol's defaults still
run every call there. *(2026-10-05.)*

**Asked and declined: making a turn a langchain `Runnable`.** It would bring `batch`,
`astream_events` and LCEL with an interface that ecosystem knows, and it removes none
of the work: `Runnable.astream`'s default yields one chunk from `ainvoke` rather than
iterating `stream`, so the bridge above would still be written underneath. It adds a
second vocabulary for what a request may do: `RunnableConfig` answers "how should this
run", `Capabilities` answers "what may this request reach" and only narrows, and there
is nowhere in the first for the second. What was worth having -- a caller's context and
its tracing reaching the turn -- arrived with the bridge. A `Runnable` adapter needs a
witness first. *(2026-09-18.)*

**Taken: files passed by id go.** `Request.input_refs` and `data_refs` named files
for a `FileStore` to resolve, the service's vocabulary; with it gone every use was in
the feature's own tests. The port went, with `LocalFileStore`, its kit, `Planted` and
the `contents=` placement path. A caller whose files are elsewhere copies them here and
passes paths, as `kingfisher run --input` does. *(2026-09-16.)*

**Taken: opening a session without a turn goes.** `start_session`,
`Kingfisher.remember_agent` and `open_session_for` minted a session and fixed its agent
before any turn, which only a server does, and the only callers left were tests. The
route had cost a data-loss bug twice -- a stub session laid out under the workspace
whatever `session_root` answered and swept by `reap` out of its own store, and a pin
written where a custom root's turn would not look. *(2026-09-16.)* *Making a session
before its first turn returned on 2026-10-14, as `kf.session(id, create=True)` for
placing inputs, under "Reversed: a session is named by the request" below.*

**Reversed: `files_for` opened only a session a turn issued.** It opened through the
session backends' `open`, which makes what it is asked for, so a made-up id became a
session a turn would accept -- the route above, back by another door. It was made to
refuse an id the listing lacked, at the public doors and not in `_files_for`, where it
would refuse every turn that starts a session. *(2026-10-07.)* Undone 2026-10-14 by the
reversal below: an unused id starts a session by design. What the check reached for is
held by the id itself -- `names_a_session` admits one visible path segment, so a made-up
id names only a folder directly under `sessions/`.

**Taken: definitions passed by id go.** `Request.skill_refs` and `subagent_refs` sent
catalogue ids fetched through a `DefinitionStore` -- a route for a caller with no
filesystem here, which only the service was. The port went, with uploads.py,
`UploadError` and `Kingfisher(definitions=)`, and so did `Capabilities.including`, the
single exception to *Capabilities narrow and never widen*. Nothing widens now, so the
exception is gone rather than merely unused. *(2026-09-16.)*

**Taken: a session contributes no definitions.** The half that read them went too: the
per-turn overlay in layered.py, `read_uploaded` and its registry merge, the
/skills/uploaded route, and the `SESSION_PLUMBING` entry that made the directory and
granted the shell write access to it -- so the fence under *Landlock cannot take back
what it has granted* grants one place fewer. What a request may activate is what the
catalogue holds. *(2026-09-16.)*

**Transport only -- the server never interprets identity**, in its own wheel, installed
by `kingfisher[service]`, so `pip install kingfisher` put no web service on disk. One
request per turn, streamed, no result persistence; files as ids through a `FileStore`;
the turn stopped on disconnect, with no cancel endpoint; an error-to-status map with a
test that it was total. *(2026-08-16, `http-surface.md`; 2026-08-17,
`the-service-as-its-own-package.md`.)*

**Reversed: a session is named by the request.** Any id names a session, the first
turn naming one starts it, and a request naming none runs in `default`. The rule it
replaced -- ids are issued, not accepted: a supplied id may resume but never create, and
minted ids are 128 bits *(2026-08-16, `session-scoped-api.md`)* -- protected one
deployment, a service forwarding a user's input as an id. Every other caller paid: one
with ids of its own kept a table from them to kingfisher's, and choosing an id at all
meant `KINGFISHER_SESSION_ID`, a process per session. So the tenancy boundary is
outside kingfisher with no guard inside: a service derives the ids it sends and never
forwards a user's, as `ports.md` says.

What still holds: the access policy refuses a caller at a session whose pinned agent it
does not reach, in the words an absent session gets. Reading or deleting a session
nobody made answers "no session" and makes nothing -- a read that made one would be a
write nobody asked for. `kf.session(id, create=True)` makes one, since placing inputs
before the first turn is its purpose. An id may not start with `.`: `.harness` is
guarded by the macOS rule in every session, and the listing skips what no request could
name. `default` is an ordinary id, listed and reaped like any other. *(2026-10-14.)*

**A skill's `allowed-tools` is prompt text, not enforcement.** Worth knowing before
trusting it for anything. *(2026-08-16, `session-scoped-api.md`.)*

## The core and its plugins

**The package is a core, the plugins kingfisher ships, and the command.** This entry
replaces *The front door*, *Layering* and *Splitting a file*; their text is in git.
*(2026-10-16 to 2026-10-17, `core-and-plugins.md`.)*

```
kingfisher/
  __init__.py     the front door: names for embedding and for tool files
  core/           what every deployment runs; interfaces.py, what a plugin must be
    access/       who reaches what
    agent/        one request's graph, kingfisher's own middleware, prompts/,
                  and kinds/, where each kind meets deepagents
    session/      one session's state and one turn
    definitions/  the five kinds, the catalogue, seeding
    *.py          flat: config, layout, request, result, service, run, inventory, ...
  plugins/        what the core never names: sessions/ and events.py
  cli/            the command
```

**The layers had stopped being the dependencies.** domain/ imported the kinds,
application/ imported the harness at module scope in more than a dozen places, and a
dozen more imports were deferred into functions to get past the rule rather than
because the dependency was wrong. The deepagents 0.7.22 upgrade changed three files,
two outside the harness, so the folder no longer drew an upgrade's list; startup cost
is held by a fresh-interpreter check instead.

**Words.** A *plugin* is a part a deployment may replace, and kingfisher ships one of
each; its *interface* is what the core needs of it, stated once in
`core/interfaces.py`. "Port", "adapter" and "extension" name none of these; `Adapter`
became `WireFormat`, being a model wire format. Middleware is upstream's type used as
is, so it has no interface here, and the conversation is not a plugin: "Dropped: a
deployment's own saver" under *Sessions: what persists and where* says why.

**The core imports no plugin.** A default the core falls back to lives beside its
interface: `LoggedRunEvents`, `InstructionsFile`, the shipped wire formats. `plugins/`
holds what the core never names -- `LocalSessions` and `JsonlRunEvents`. What the core
needs beyond an interface it reads as extras an implementation may declare --
`host_path`, `shell_prompt`, `unprotected`, `runs_here`, `where` -- never by identity:
one `isinstance` would make kingfisher's own sessions impossible to ship apart.
`test_the_core_imports_nothing_from_plugins` holds it; only `cli/` is excused.

**A plugin imports the core and never another plugin**, and `kingfisher.plugins`
counts as another, since it names every plugin and would be the back door.
`test_no_plugin_imports_another`.

**A plugin of kingfisher's own takes only what an outsider could.** `LocalSessions`
reached twenty-five names no public address carried, and the kit in `tests/checks/`,
which plugin authors are told to copy, fifteen. Whatever a default needs and an
outsider cannot import is a gap in the interface, so the gaps were closed rather than
the default excused: `cfg.layout` with `Layout` public, `Config.sessions_root` and
`Config.kingfisher_dir`, `Definitions`, the backend checks and the skills lister, and a
`ShellPrompt` where the core had asked by identity.
`test_a_plugin_has_of_kingfisher_only_what_an_outsider_has` holds both. A plugin takes
a door's name from the module the door takes it from, since it may not import its own
door; the kit takes it through the door. **Not taken: shipping `LocalSessions` as a
package of its own.** It needs nothing the core does not, `sandlock` is already an
extra, and a second release buys an install nobody asked to skip.

**Inside `LocalSessions`, a function that needs the layout takes the `Layout`.** The
folders, backend and three fences took folder names one at a time, each defaulting to
the shipped name, so a new caller could forget one and lay out or fence a renamed
deployment's session under the shipped name, silently. One required `Layout` is all of
them; `Layout.made_dirs` and `Layout.shell_read_only` name the two sets spelled out
twice each. The same went for `unrouted_denials` and the backend kit, whose `make` now
returns `(Config, backend)`. Kingfisher's own backend serves any path, so a kit
spelling a shipped name passes against it either way; each kit is also driven against
a backend serving only the layout's folders, which is where that shows. *(2026-10-17,
from a review of the plugin work.)*

**Sessions are always named.** `Kingfisher`, `run()` and `stream()` refuse to run
without them. The sessions plugin is the sandbox and decides where each caller's files
are, so a default would be a guess, and a guess there is the leak a deployment replaced
it to avoid. The command names `LocalSessions` in `cli/configured.py` unless
`KINGFISHER_SESSIONS` names others. `graph=` is kept and never stands in for sessions,
since a turn's inputs, leavings and lock go through them whatever runs the agent.

**`Plugins` is a record for `KINGFISHER_PLUGINS` alone.** The command builds its own
`Kingfisher`, so the setting names a callable returning one; in the library the plugins
stay keywords. **Not taken: one `Plugins` object as the argument.** It would change more
than a hundred places to say the same thing one level down, and wire formats do not fit:
they apply as the configuration loads, where `doctor` checks every endpoint's `api:`.

**`Config` holds what the core reads.** The shell's fence and `PATH` additions are
`LocalSessions`' settings, as `ShellSettings`, and `KINGFISHER_SESSIONS` and
`KINGFISHER_PLUGINS` are the command's: nothing in the core read any of the four.
`LocalSessions()` reads `KINGFISHER_SHELL_SANDBOX` and `KINGFISHER_SHELL_PATH_EXTRA`, so
one `.env` fences the command and a library deployment alike, and `shell=` decides in
code. `doctor` and `kingfisher list` ask the sessions how they are fenced, as
`Kingfisher.origins` does, rather than read a setting that may not be the one applied. **Not taken: a `Config.shell` record**, which keeps one
plugin's settings on the core's record under another name. **Not taken: leaving them
flat** -- that is how the core's record came to configure a plugin and the command.

**Two public addresses.** `kingfisher` is for embedding and tool files: `Config`,
`Request`, the results, `Session`, `Capabilities`, the errors, `run` and `stream`,
seeding and `inventory`, `ToolContext` and `ToolSpec`. `kingfisher.plugins` is for
writing a plugin: the interfaces, the records they speak in, and kingfisher's own
implementations. Everything else is private and may move with no deprecation window: at
0.1.0 a caller on an old spelling changes one import. Both are lazy tables: `import
kingfisher` is 5.0ms and `kingfisher.plugins` 5.1ms, against 909ms and 3,143 modules for
`Kingfisher`. *(Measured 2026-10-17, Python 3.12.7, best of seven fresh interpreters.)*

**One session, one handle, checked once.** `Kingfisher` keeps what is asked of the
service -- `run`, `stream` and their twins, `session`, `sessions`, `reap`, `origins` --
and one session is asked through the `Session` that `session` hands out: `pending`,
`artifact`, `files`, `size`, `delete`, each with an `a` twin. It replaced `SessionInfo`
and five pairs of id-taking methods, some of which checked `source_ids` and some not,
so whether a caller could act on a session depended on which method was asked.
`session` checks once; a session's agent and the caller's ids are fixed when it opens,
so no later answer could differ. `None` answers both an absent session and one out of
reach, so a leaked id is worth nothing, and `create=True` still answers `None` for one
that exists out of reach. The handle keeps its backend, so a narrowed read opens one
sandbox, not two.

**Not taken: `s.run(...)` on the handle.** It would be a second way to start a turn,
and a first turn makes its session, which no handle exists for yet. *(2026-10-17.)*

**Not on it: `LoggedRunEvents`.** It is the default, so naming it gives a deployment
nothing, and it loads langgraph: 279ms and 834 modules, against 39ms for the interfaces.

**A caller means a caller outside this wheel.** The command may import anything, but a
name on either address comes through that address:
`test_a_consumer_uses_the_library_only_through_its_public_api`, by name rather than by
file. `kinds_at` is on the door because
`test_the_whole_job_is_reachable_through_the_front_door` names it as the job. **Not
taken: freeing whole command files from the door.** They use public names too and would
drift to deep imports with nothing red; held by name, putting a name on an address turns
every deep import of it red.

**Which address a name is on says who it is for, and `WITNESSES` says who asked.** One
table per address, keyed by `LAZY_TABLES` so a third door cannot open without one:
`document`, `embedder`, or `interface`, which a second rule holds to a class in
`core/interfaces.py` so the word is a claim about the tree. It was swept out with the
layer rules it was filed beside, and for a week the record asked for a witness and
nothing checked; nothing slipped through -- the root door shrank by the seven names that
moved to `kingfisher.plugins` -- so what was lost was the next name. Restored
2026-10-17. *(2026-10-17, from an architecture review.)*

**Three listings per address, held to each other.** `__all__`, `_EXPORTS` and the
`if TYPE_CHECKING:` stubs agree, and a stub is written `X as X`, which under PEP 484
makes it a re-export. `__getattr__` returns `Any`, so a name without a stub imports,
passes every test and is untyped.

**A package's `__init__` imports nothing, or does it lazily.** It runs before every
submodule, so one eager re-export reaching the runtime puts most of a second in front of
every import beneath it. `core/`, `core/definitions/` and `core/agent/kinds/` import
nothing: each kind is 5.2ms, where `import deepagents` alone is 845ms. *(Same
measurement.)*

**A folder table lists what the package's other folders take from one folder.**
`core/access/__init__.py` is the first: a lazy `_EXPORTS` and its `X as X` stubs, carrying
exactly the names imported from outside the folder, so a move inside it edits the table
rather than its importers. The split that made `reach.py` edited an import in ten files
outside the folder, where a table would have changed seven of its own entries. It is not
an address and not an interface: nothing outside the wheel is told to import it, so it
has no witness, and no `__all__`, since nobody star-imports a private folder; the two
listings left are held to each other as a door's are. Lazy because the folders loop
where the modules do not -- `core/access` and `core/definitions` with its kinds import
each other, as do `core/agent` and `core/session` -- so an eager re-export would turn the
loop into an import cycle. The public tables keep naming the module defining each name,
so the startup check finds a public name in one step. `FOLDER_TABLES` and
`test_a_folder_with_a_table_is_reached_through_it` hold it. A pilot: whether another
folder gets one waits on what this one cost, and so does how the plugin rule reads a
table, since no plugin takes anything from `core/access`. *(2026-10-19.)*

**The rules**, all in `tests/unit/test_architecture.py`:

  - **`THIRD_PARTY`**, a per-folder library allowlist, deny by default, so an upgrade
    is a list of files rather than a search. The runtime goes to `core/agent`,
    `core/session`, `plugins`, and the three kinds that must name its types:
    `core/definitions/skills`, `core/definitions/tools`, `core/definitions/middlewares`.
  - **The startup check**, imported for real in a fresh interpreter, replacing hand-kept
    tables of light and heavy names: `test_importing_kingfisher_loads_nothing_installed`,
    `test_the_command_starts_without_the_agent_runtime`, and
    `test_a_public_name_that_runs_no_agent_loads_no_runtime` for every name outside
    `RUNS_AN_AGENT`. `langchain_core` counts as the runtime, since `ToolSpec` names no
    langchain type.
  - **`RECORDS` touch nothing outside the process**, so a record can be built without
    a workspace behind it. `core/access/policy.py` is held to it outside
    `load_access_policy`, the one function that opens the file, so a vocabulary parses
    from a mapping. A method call is
    caught by its name whatever it is called on, a bare `open` wherever the module has
    not bound the name itself; a dynamic import or a `getattr` is not, since the rule
    makes the easy violation loud rather than the determined one impossible.
  - **The flat core writes nothing to disk** except `core/workspace.py`, asserted to
    write, so a move fails rather than excusing nothing.
  - **`test_only_one_module_changes_a_mode`**, over the whole package: scoped to a
    folder, it went on passing over one that no longer held the owner.
  - The two plugin rules above, and `WITNESSES`.

  Gone with what they described: the layer rules, `HARNESS_EDGES`, and the domain
  rule, whose guarantee is `RECORDS` now.

**Not a portability claim.** Kingfisher is an adapter over deepagents, and another
framework is out of scope rather than scheduled. Nothing declares what a runtime is --
`core.session.runtime` is a module -- so a second would begin by discovering that
interface, which is the right order: one derived from one implementation is shaped like
it.

**Where the kinds sit.** Each of the five is a directory under `core/definitions/`: a
file somebody authors, its reader, and a registry of what was found. `DEFINITION_KINDS`
names them, `test_kinds_holds_exactly_the_kinds` holds the folder to it both ways, and
`KINDS_HELPERS` names the loose modules. Where a kind meets deepagents is
`core.agent.kinds`, a module per kind -- except skills, which deepagents reads itself
and `core.definitions.skills.registry` asks, and agents, which are the graph
`core.agent.assembly` builds. `declared_middleware` keeps its prefix so it does not read
as a second `middlewares`. No rule names that direction: `core/definitions/` and
`core/access/` import nothing from `core.agent`, `core.session` or `core.service`
today, and the startup check would refuse a module-scope import of the runtime there
but pass a deferred one. *(Measured 2026-10-17.)*

**The kinds do not share an implementation.** Each resolves `source::name` its own way
-- `split_reference`, `split_qualified` -- so a change for one is not argued past the
others; the shared part is the grant vocabulary in `core.access.capabilities`. The
exception is `core.definitions.documents`, since agent and subagent formats reflow a
prompt identically; `SHARE_A_DOCUMENT_READER` holds it.

**Not taken: a package per concept.** Drawn in full on 2026-09-04 -- fourteen modules,
each kind's format beside its runtime half -- and measured: a concept's files changed
together in 13-29% of their commits, the tightest co-change pair ran across the boundary
it would draw, and eight of the fourteen would have imported deepagents. The new shape
gives the same answer.

**A group shares a subject, not a layer.** `plugins/sessions/sandbox/` is one policy
and the kernels that carry it, where being wrong is a security failure. Before moving
any of it: `tool.ty.overrides` names the two files importing `sandlock` by path, and a
file it no longer matches loses its exemption in silence.

**An import cycle is removed, not reordered.** One resolved by luck of import order
until a cold import failed; `test_no_module_in_the_package_can_reach_itself` refuses
the shape.

**A dot means this folder, a full path another.** A module imports its neighbours as
`from .store import ...`, and anything in another folder, a child folder included, from
`kingfisher.`. Held both ways, because ruff's `TID252` refuses only `..`: `from .child.x`
passes it, nothing asks for the dot where it belongs, and a tree allowed either spelling
grows both. Every lazy table is exempt, a folder table as much as a door, so a stub
names the module its `_EXPORTS` string does.
`test_a_dot_means_this_folder_and_a_full_path_another`.
*(2026-10-19.)*

**A file is split where its history already splits it.** Two definitions belong
together when a commit touched both. Git's default hunk header names any line in column
one, which inside a long class reports every method as the class, so `.gitattributes`
sets `*.py diff=python`. Co-change cannot tell a cohesive file from a tangled one; the
width of one commit can. By this test workspace/fs.py became six modules, and the
backend was refused, then split when commits edited only its guards.

**This round's splits were the maintainer's call, and each cut was counted before it
was made**, as how many commits touching one half touched the other, moves left out.
Taken:

  - `plugins/sessions/backend.py`, its shell to `shell.py`: 6 of 32 crossed.
  - `cli/health.py`, to `health_definitions.py` (0 of 20), `health_isolation.py` (4 of
    17) and the shared `check.py`.
  - `sandbox/confinement.py`, the macOS profile to `sandbox_exec.py`: 10 of 20, half of
    it parameter plumbing.
  - `core/access/source_ids.py`, who reaches what to `reach.py`: 3 of 8, thin evidence.
  - `core/access/source_ids.py` again, its `source_ids.yaml` reader to `policy.py`, the
    module already named for reading it: 1 of 5, none touching the reader alone. Thin
    as well, and taken for what the reader imported: `core.definitions.fields`, which
    imported the vocabulary back, the one cycle in the tree. *(2026-10-19.)*

Left whole:

  - `cli/__main__.py`: parser, `main` and body change together; 14 of 22 crossed.
  - `core/inventory.py`, a hub: 25 of 36 commits on its helpers touched it too.
  - `core/agent/assembly.py`: `build_agent` alone is 473 lines in one cluster.
  - `core/definitions/subagents/spec.py`: a new field lands in three of its lists at
    once, kept together on purpose.

**`core/service.py` is three files, on the maintainer's call.** `Kingfisher` sets up
and builds the agent; `Preparation` in `core/session/preparation.py` is a turn before
its agent runs; `Running` in `core/session/running.py` is `run`, `stream`, their twins
and the settling after. History did not ask for it: 19 of the 27 commits touching the
opening half touched another part, about where `cli/__main__.py` was left whole. Parallel
work decided it: of 11 pairs of pull requests open at once on `service.py`, 6 edited
places apart in one file, and cut this way 9 would have edited separate files. The cost
is that most turn changes open two files, and each mixin declares what it borrows from
`Kingfisher`, which `ty` holds. *(2026-10-17.)*

**Grouped by what a class is, for whoever is looking.** "What does kingfisher wrap an
agent in" is one package, `core/agent/middlewares/`, held through bases by
`test_kingfisher_keeps_its_own_middleware_in_one_package`. It took `HostPathError` from
250ms to 16ms once `host_paths` named no runtime class.

**Not taken: splitting `config.py` by its parts.** It reads as three -- model records,
workspace paths, the deployment record -- but of 36 commits on 2026-09-04, 20 landed
only in `Config`, five only in the model records, and five reached two or three. The
churn is a record gaining settings.

## The architecture rules

**Architecture rules are mutation-tested, not trusted.** All 44 were audited; 43 held
and one had lost its subject. Three of them exist *because* a rule had stopped working
silently. *(2026-08-18, `mutating-the-architecture-rules.md`.)*

**Six guards that could not fail, and one that cannot be made to.** A review mutated
every branch the last ten slices added -- 104, 97 biting -- and the survivors were all
in rules written days earlier, which is where they always are. Each was correct and held
by nothing, and was fixed by reaching its subject:

  - **`bar_chart` scrubs three cells and the rule reached one.** The fixture always put
    the bad character in the *label*, so dropping the *title*'s scrub left 137 tests
    green while the SVG stopped parsing.
  - **Two of the three characters XML admits were reached by nothing**: the rule drove
    the tab alone. All three now, asserted on the file's bytes, because XML normalises
    a carriage return on the way *in*.
  - **`doctor` has to answer on a workspace nothing has laid out.** Removing its
    session scan's guard left the suite green while `examine` raised
    `FileNotFoundError`, which it does not catch.
  - **And the scan's `is_dir` filter was indistinguishable from `True`**, since no
    fixture put a non-directory in `sessions/`.
  - **The traversal refusal was driven on one half.**
    `test_a_path_that_climbs_out_is_an_error_on_the_result` lacked `way`, so `adelete`
    could pass a malformed path through with the suite green.

**The sixth cannot be given a rule, and that is the finding.** `activated_subagents`
lists what the agent the build *opened* delegates to, rather than what every reachable
agent does, because `Capabilities.subagents` may only narrow that agent's declaration.
Collapsing it to the broader answer left 243 tests green, because only `"*"` lets a
request's name survive an agent's ceiling, and `"*"` is every subagent in reach, so the
union equals it; the branch for no agent is unreachable. The narrower answer is kept,
right by the rule rather than by coincidence, and the premise is pinned instead:
`test_a_request_is_refused_a_name_only_by_an_agent_that_offers_every_subagent` drives
all three shapes, and widening the refusal reddens it. A guard that cannot be
distinguished is usually deleted; here recording why beats both deleting it and
pretending a rule exists. *(2026-10-16, from an architecture review.)*

**A rule matching one spelling of a reach matches one spelling.**
`test_only_the_confinement_module_calls_resolve_directly` required an `ast.Name` reading
exactly `confinement`, so it saw two of the six ways to reach that `resolve`: an
aliased import, the dotted chain and `getattr` walked past it. Measured by writing each
into a real production file and asking whether the rule failed -- a matcher can be
right about a string and wrong about the repository. The rule now resolves the module
-- bound names, flattened attribute chains, the direct import, `getattr` with a constant
-- and two parametrized rules beside it pin the six spellings it must see and five near
misses it must not, because the matcher is what is asserted.
`test_an_aliased_import_reads_the_name_it_renames` had taught this already and was not
carried to the neighbour. *(2026-10-12, from an architecture review.)*

**Rejected: a rule against prose naming a module that is not there.** Four stale claims
were found in one session -- among them a refusal naming access.yaml and the
catalogue's docstring describing three modules that had left it -- and three were caught
by a test, so a rule for the fourth looked worth having. Measured, it is not. Of
sixty-one module paths in `src/` prose, twelve do not resolve: **seven illustrative**
(vendor_a/fetch.py is a *workspace's* layout), **three deliberate history**, **two
rot** -- ten false positives to two, separated by tense and intent, which no pattern
sees. And it would not have caught its motivating case: the docstring said "`agents`,
`skills`, `subagents` and `tools` are one module each", bare words that a rule loose
enough to catch would fire on everywhere. `test_no_code_cites_a_document_that_is_not_there`
works because `docs/` holds few paths and none illustrate. Whoever measures again: a
first pass resolved three names out of `.venv`, where deepagents' own skills module
made a skills.py look real. *(2026-09-06.)*

**The prose was swept on 2026-10-12, and what it got wrong is the record of why.**
Fourteen claims, each measured before being changed, with the rejected rule's reasoning
still standing, so the discipline is the only guard. `CLAUDE.md`'s own paragraph on the
sentences worth keeping cited, as its example, a rule that has never existed; two more
named renamed rules and one a retired test module. Of six candidates, two were
*fragments of a wrapped name* that a line-by-line search reads as whole -- a search for
this has to join wrapped lines first. Two claims were dead: `withheld_by_kind`'s
default-axis guard misstated the default and cannot change a report, and stays because
it skips `names_of()`, which reads the catalogue; an unreachable `'none'` fallback went.
Two were behaviour: a shadowing private tool printed twice, and `name_of`'s string guard,
which stops a middleware's name rendering as `<property object at 0x...>`, had no case.
*(2026-10-12, from an architecture review.)*

*Swept again on 2026-10-16, and `LAYOUT_VERSION` is why the discipline alone is thin.*
A change dropped that constant and touched no document, leaving `docs/upgrading.md`
promising a refusal that was gone and an entry here deferring a fix to it -- an entry
deferring to nothing. Measured, `ensure_layout` on an old workspace neither refuses nor
rewrites its marker, worse news than the promise. Four more: `files_for` documented as
refusing an id it now created; `kingfisher seed` said to need only
`KINGFISHER_DEFINITIONS_DIR`, when it refuses without `KINGFISHER_ASSETS` or `--from`;
`system_prompt` called a public export; the contract kit's import cost written as 103
modules against 3,104. A measurement in prose rots one way -- the number that made the
argument is the one nobody re-runs. *(2026-10-16, from an architecture review.)*

*Two stated reasons corrected the same week, both found by mutation.* The recorder's
docstring credited its position first in `tool_guards` with three properties; moved
last, only `test_the_arguments_recorded_are_the_ones_the_model_wrote` notices. Being
inside deepagents' eviction comes from being custom middleware at all, which deepagents
lands after its own, and seeing what a deployment's middleware left comes from that
being appended last, held by `test_what_a_deployments_middleware_left_is_recorded`.
And `_without` in `core.agent.middlewares.builtin_tools` claimed that removing
deepagents' host paths by block made
`test_deepagents_host_paths_reach_only_a_shell_on_this_host` fail on a merged section;
matched by substring instead, it still passes, since absence holds however the section
goes. The premise is sound, so it got the rule it was missing, which bites on all three
half-cuts. *(2026-10-16, from an architecture review.)*

**Two prose rules that do hold, and why they are not that one.** Cutting the prose left
three docstrings promising a list the cut dropped -- *"the last of three parts --"* --
and a comment citing `formats.md` by line number, with every check green. That rejected
rule ran 10 false positives to 2, because "does this module exist" needs tense and
intent. **"Does this sentence end" is syntax**: 2,705 docstrings, *zero* false
positives, all three caught; a line-number citation matched twice in the repository,
both stale. A rule is fragile when it has to read intent, not because it reads prose.
*(2026-09-07.)*

**A prose root is any package, not only a top-level one.** `_prose_roots` read one
directory deep, so a package that moved down a level took its references out of the
pattern rather than into the failures: the rule goes quiet rather than red, the one
failure it cannot report about itself, and the one a move causes.

**The cost is that shorthand is refused.** Eight of the ten references this found were
layer shorthand like *harness.agent*, which resolves for a reader who knows the layer.
Refused deliberately: a name that resolves only by knowing its layer goes quiet when the
layer changes, so spelling it from the package root is what makes it checkable.
*(2026-09-10.)*

**A third rule for the same blind spot: what a class body declares.** The two rules
asking whether anything reads a definition cannot see a bare annotation in a class body,
and three lived there -- among them `Sessions` declaring `_claims: Path` where **nothing
set `_claims`**, so the call it invites type-checks and does not run:

    ty:     All checks passed!
    pytest: 28 failed -- AttributeError: 'Kingfisher' object has no attribute '_claims'

A mixin's declarations state what it needs of its host, so one it does not read is a
requirement that moved or never was. `test_no_mixin_declares_what_it_does_not_read`
asks it of every class whose annotations are a requirement -- not a dataclass's, which
are its constructor, nor a Protocol's, which are its contract.

**What that rule still does not see is a property.** `Surface.ambiguous` had no caller
while the run report asked `Offering.ambiguous` directly; it was deleted rather than
guarded, because a rule would have to decide about properties read only by tests, which
was answered the other way for `GrantedOnlyMiddleware._allowed` on 2026-09-24. The
claim that the backend's guards had never been edited was re-measured and reversed on
2026-09-23. *(2026-09-24, from an architecture review.)*

**And one resolver where there were two.** `model_for(spec, override=...)` only handed
`spec.wanted` to `resolved_model`; the adapter is gone and callers pass `spec.wanted`,
which says where the answer comes from. The rule stays in
`core.definitions.subagents.rules`. *(2026-09-24, same review.)*

**Four guards from the last two weeks could not fail, and each failed the same way: the
fixture could not tell the fix from the bug.**

  - **The skills half of the audience fix.** The subagent declared a skill its agent
    already granted, so "a skill only a subagent declares" did not exist in the fixture;
    one word, `spare` for `routine`, repaired it.
  - **`ls` listing a route's own stand-in row.** The control drove `/outputs`, which is
    not a route; driven on `/inputs` it bites.
  - **A threshold where a pinned set belongs.** `assert len(_readers(shipped)) >= 2`
    against nine, a commit after the same shape was deleted elsewhere.
  - **Four refusal branches on the async path.** `ToolFailureMiddleware`,
    `WorkspaceToolPathsMiddleware`, `GuardedTool._arun` and `PermittedBackend.aread`
    were held on the sync half alone; the `way` fixture now reaches them.

**Mutate the thing the guard guards, and if the suite stays green the fixture is the
suspect, not the rule.** Every one of these had a sibling that bit, which is what made
them look covered. *(2026-10-15, from an architecture review.)*

## How much a comment says

**Reversed: "match the surrounding density".** The convention asked every comment
to explain why, and a new one to match the length of those around it. The second
half compounded the first: a file of long comments stayed long, because a short one
there read as an oversight, and nothing in the rule pointed the other way. Measured
before changing it: 53.6% of every non-blank line in the repository was comment or
docstring -- 27,889 lines of prose against 24,124 of code, and the ports module at
351 prose lines out of 359.

**The rule is a test now, not a quantity.** A comment earns its place only if
removing it would let a competent editor make a change that is wrong. That keeps the
reason a named factory's own exception is deliberately not wrapped -- delete it and
someone helpfully wraps it -- and drops a paragraph restating it at greater length.
That reason lives in `guides/ports.md` now, and `test_named_backend.py` holds the
behaviour.

**Four kinds were named as always-cut**, because a test alone drifts: repo history, a
paragraph restating the one above it, framing that announces an argument instead of
making it, and a contract the signature already states. The history ban is the one
that matters: reversals are recorded *here* so the argument is not re-run, which makes
the copy in a docstring the redundant half.

**What it did not rest on: deduplication.** The first pass assumed source prose
largely restated `docs/`, on the strength of `wiring.py`, whose docstring then shared
near-verbatim paragraphs with *Wiring a store*. Shingled against the whole of `docs/`,
that is 1.4% of source prose -- 288 lines of 21,051. `wiring.py` was the outlier, so
the cut had to be justified as losing information rather than moving it.
*(2026-09-07.)*

**No test guards this, deliberately**, which is the same finding as *Rejected: a rule
against prose naming a module that is not there*. CI cannot see this change at all:
deleting every line of prose in the repository passes `ruff`, `ty` and `pytest`. The
floor is the keep-test applied while editing, not a check afterwards.

**A rule against citing a `_private` or `test_*` name that is gone was declined a second
time**, after sixteen such citations were repaired by hand on 2026-10-15 -- among them
a function renamed in the very module whose comments still named it. This time it
was measured against definition names rather than module paths. 307 citations, 211
distinct: 275 resolve here, 16 resolve upstream in the two places whose subject is
upstream -- `findings.md` and `src/` comments, which name `_tool_error` and
`_compile_spec` because deepagents' shape is why this code has the shape it has -- and
16 are the record deliberately naming something gone, since an entry about a deletion
has to say what was deleted. Sixteen true against sixteen pinned by hand, where the
module-path rule measured two against ten. Against it: the pinned set grows every time
an entry records a removal, so the rule taxes the writing it protects; and resolving
the names costs a walk of `site-packages`, which took one test file from 0.8s to 6.3s.
A rule whose whole yield is a list of things already fixed is not worth that. The
discipline stays the guard.

**Trimmed to the rule, 2026-10-17.** The test above held and the code had not followed
it: `src/` carried 8,447 lines of comment and docstring against 13,237 of code. A pass
over every file kept each reason that stops a wrong change, cut to its shortest --
usually one line -- and took out the history, restatements and cross-references,
leaving 4,734. It was checked by machine rather than by reading: every file's syntax
tree, docstrings removed, equals the one before, so the pass changed no code. What only
a comment recorded -- a measurement, or the evidence behind a decision -- moved to
`findings.md` or beside its decision here, and CLAUDE.md says that a reason needing
more than about three lines goes here.

**What only a comment recorded, kept here.**

- `core/interfaces.py`: by its folder's own clock, a session read 10,000s idle right
  after a turn finished in it, which is why `mark_used` is part of the interface.
- `core/config.py`: in the window `claim_stale_after` closes, a second caller took a
  session while the first turn was still running, and the first turn then finished.
- `core/session/model.py`: before the turn lock, a turn simply vanished -- both callers
  got an answer and a run directory, and the conversation kept no record of one.
- `core/session/methods.py`: listing sessions takes one call, measured at 0.22ms for fifty.
- `core/workspace.py`: no `.gitignore` is written, because a shipped one listed two of the
  five things a workspace holds; `Library/` fell outside it, and `git add -A` in a real
  workspace offered to commit a 21MB pip cache.
- `core/origins.py`: spelled out in full the origins line is about 450 characters;
  relative, about 240, and the relocated entries are the only absolute paths left in it.
- `core/layout.py`: denying the offloads folder and not its pair refused the file tools a
  32-byte result and handed them a 101KB one.
- `plugins/sessions/backend.py`: one file supplied with `--input` came back from a search
  as two matches with one path between them, on every pattern.
- `plugins/sessions/shell.py`: before `shell_env`, the shell read `~/.aws` and
  `~/.config/gh` straight through the fence; a runner of kingfisher's own would be 110
  lines of upstream's truncation, timeout and exit-code shaping, copied and kept in step.
- `plugins/sessions/sandbox/confinement.py`: before `with_supplied_runner`, a runner
  declaring `local = False` ran its command with no wrap, while `Confinement` still
  reported `mechanism='sandbox-exec'` and `confined=True`.

## The size of the test suite

**Rejected: cutting the suite substantially. There is nothing redundant in it to
cut.** Counted on the tree of the date below: 2,431 tests against 89 modules, and
32,724 lines of test against 15,331 of source, which looks like padding twice over.
Six ways of looking for the padding found none. *Structural duplication*, pointing
`audit_duplication.py` at the tests: 13 findings, each a two-statement fixture helper.
*Vacuous tests*: of 282 with no `assert`, 252 are `pytest.raises`, 23 are
call-it-and-do-not-raise, four are genuinely loose. *Mechanical merges*: of 721
single-assert tests only 108, in 32 groups, share a byte-identical arrange.

**Neither coverage nor kill-set can authorise a cut.** 96% line coverage is reproduced
exactly by 353 of the 1,852 tests that execute library code -- a criterion that
green-lights deleting four fifths of the suite is a rubber stamp, and it has no opinion
on the 587 tests that read the tree instead. Mutation kill-set is weaker: across 300
mutants, **89 tests preserve the entire kill-set**. Any invariant used to choose
deletions has to be held out from the choosing, or it is fitted to the answer it was
meant to check.

**What the kill-vector matrix said.** Grouping tests by which of the 300 mutants kill
them gives a surplus of 751 indistinguishable tests -- which looks like redundancy and
is not. 632 of it sits in groups separated by fewer than ten mutants, thin evidence
rather than sameness; the rest were read, and the five in `test_skills_read_only.py`
drive four tools at four targets through one enforcement path -- they die together
because the path is shared, not the assertions. A finer corpus can only split groups,
so 751 is a ceiling that falls as the measurement improves.

**Amplification is dependency-shaped, and is a reporting problem.** The median caught
break turns 9 tests red. One wrong `and` in `layout.py` fails 385, of which 345 are one
exception from one line of the workspace backend, raised on each test's way to an
assertion it never reached. `--tb=line` took that output from 35,495 lines to 1,415
without deleting anything.

**What was done instead** is two splits along seams each file already declared in its
docstring: `kingfisher list` out of the file that also tested `seed`, and what a turn
may destroy out of what a caller can reach. No test was deleted. The gate for a move is
`pytest --collect-only`, not a green suite -- a rule that loses its parametrize data
collects nothing and still prints dots.

**The survivors, separated -- and the measurement was wrong three times in ways that
read as findings about the tests.** It edited the word `and` inside error messages,
which nothing asserts (19 survivors). It read a timeout as "no test failed": breaking
`create_exclusive` so it never reports success spins the turn-id loop forever, and the
most lethal mutant in the corpus was filed as a gap. Corrected: **279 mutants, 260
killed, 19 survivors** -- 6.8% rather than 18%. Then rerunning found it editing prose
again: the skip named `STRING` and `COMMENT`, and since Python 3.12 an f-string is
neither, which is how nearly every message here is written. That third fault has a test
of its own.

**Eleven of the 19 were real gaps** -- among them the run log's `ok` on the
`max_steps` path, `top_p` never reaching the model params, and a tool's own JSON on the
seeding path, where JSON being valid YAML leaves the extension check as the only
separator -- and were closed in twelve tests, each mutation-tested, none changing
`src/`. **Six are not gaps**, `held_for` among them: written down as one, and
reclassified by writing the test and watching it fail against unmutated code, since
every combination that can arrive is refused before that line. **Two need Linux**, and
CI's fence job settles them. One `src/` defect came out anyway: the source-id reader
took a mapping as a requirement and kept only its keys, because a mapping is truthy and
iterates; it is refused now.

**The rate above is of covered lines, and `mutation_report.py` walks more than those.**
It mutates every mutable line, uncovered ones included, which always survive -- so its
rate is higher and the two do not compare; reached this way, "is it asserted" and "does
it run" are one question. It found what the covered corpus could not: 23 of 61
survivors were `frozen=True` off a dataclass. All 58 records in the package were frozen,
one had a test, and its docstring's "frozen, like everything else a caller is handed
here" was held up by nothing. `test_every_record_this_package_hands_out_is_frozen` is
the rule now.

If deletions are ever proposed, the gate is a mutation corpus generated *after* the
deletion list is frozen, for the reason those 89 preserved tests give. *(Measured
2026-09-08.)*

**Asked again from the other end: the suite is not over-coupled either.** Folding
`/runs` and `.tmp` into one `/scratch` changed twelve files under `src/` and fifteen
under `tests/`, which reads as tests that know too much. Its ninety hunks say
otherwise: 19 are the claim itself changing, 12 are deletions, 44 spell a literal and
15 a renamed name.

**The cure that suggests itself prevents nothing.** Making tests import the folder
constants from `layout.py` would have saved *none* of the 44 hunks: the commit renamed
the constant with the thing it named, `RUNS` to `SCRATCH`, so `workspace / RUNS` needed
the same edit as `workspace / "runs"`. Constants buy a one-token rename instead of a
literal hunt -- cheapness, not absence. And the shape barely occurs: of the last sixty
commits, the twelve touching the most test files are ten semantic or additive changes
and one module move. The tests moved because the world moved. One collapse came of it
on its own merits: the parity documents compare in one rule rather than fourteen.
*(Measured 2026-09-21.)*

**Asked a third time, at the words.** Of 2,002 items, 1,249 drive library code; the
rest is refusal text (308), architecture (139), config and CLI (133), shipped assets
(97), prose and docs (60), and the suite's checks on itself (16). Deleting every one of
those categories lands at 1,249, so a target below that is a statement about the
product, not the tests.

**The wording bill is real, and it is only error messages.** Scrambling every word of
every error message in `src` -- placeholders and punctuation kept -- turns 218 items
red. Scrambling every docstring, or every comment, turns none. The prose rules bite on
*shape* -- an unfinished sentence, a cited line number, a module named that is not
there -- and the shipped-asset tests are immune at 1 of 97.

**Rejected: deleting the tests that only assert wording.** 176 candidates, cut to 51
by coverage arcs and by the message each `raises` block saw, and to roughly nothing by
reading the docstrings. A list where a tuple was expected once opened an agent
restricted to somebody else, and the test holding that is indistinguishable by arc from
the one beside it, because a list and a tuple take the same branches; only the
docstring separates them. That is *Neither coverage nor kill-set can authorise a cut*
from the other side. Three candidates had no docstring; those need one, not deleting.

**And the matches cannot be narrowed.** Of 215 prose `match=` patterns, 51 already pin
a quoted name or syntax fragment and survive a reword, five could drop the match, and
159 cannot: `CapabilityError` is raised from 30 places, `ConfigError` from 34,
`SubagentError` from 27, so the sentence is the only thing saying which refusal fired.
The suite matches on prose because prose is the error code. What would free the
wording is a discriminator in `src` that is not a sentence -- narrower classes, or a
code on the error -- across thirty-odd raise sites, not costed here.

**The carve-out, for whoever needs it.** Six message spans reach a model's context, all
from `core/agent/`: `HostPathError` and `UnsafeReferenceError`, plus literals written
inline in `narrowing.py`. Both classes subclass `ValueError` and their tests catch the
base, so a rule keyed on the exception name exempts nothing. Wording is behaviour
there, twice measured: told only the virtual spelling, an agent passed it to `execute`
4 times in 10, and a model that read "None is not a subagent this request may use"
reported the tool as broken and answered around it. *(Measured 2026-09-21.)*

**One fixture for pointing a command at a workspace.** The command reads only the
environment, so a test driving it has to wire it, and those three lines -- workspace,
models file, key -- were written out eighteen times over a `_catalogue` helper that
`test_list` and `test_doctor` held byte-identical copies of. `at_the_command_line` is
those three lines. **The duplication was hiding a disagreement**: that helper's
catalogue named one endpoint and one model where the `FAKE_CATALOGUE` record the `cfg`
fixture holds names two and three, so a test running the command saw a different
deployment from one calling the library. The file is derived from the record now, and
`test_the_two_forms_of_the_test_catalogue_agree` loads it back and compares everything
but the key, which differs by construction. Making them agree cost nothing, measured
first; dropping the second endpoint now fails 33 tests. A test pointing the command at
some *other* workspace still says so itself, because that is a different arrangement.
*(2026-09-23, from an architecture review.)*

**One way to reach a subagent a build made.** "The subagent called X, and its
middleware" was written six times, so `subagent_of` and `middleware_of` are in
`conftest` beside `declared_subagents`.

**The middleware itself was left unanswerable -- the half of the review's suggestion
to decline.** Tests read `GrantedOnlyMiddleware._allowed` at eighteen sites; making it
public would be production API nothing in production reads, the shape
`test_nothing_is_defined_for_tests_alone` refuses -- and would slip past it, since that
walk collects a module's definitions and not a class's. Reading the private name is
also honest: `_filter` narrows on it, so the test asserts on what is enforced. Nothing
stops a seventh copy: tests read `.subagents` 113 times, mostly asking something
different, so a rule against it would be noise around one real duplication.
*(2026-09-24, from an architecture review.)*

## Proposals, and what became of them

What is still being argued is in `docs/design/`, and `docs/README.md` lists it,
held to the folder by `test_the_index_lists_every_document`. Everything that was
argued here and built has its decisions under its own section. Of
`nothing-at-rest-on-this-machine`, the one thing left unbuilt on purpose is admission
control.

**Not taken: splitting the build plan.** An architecture review named `build_agent`
as assembling in one pass with thin seams, and the reading went the other way. It was
657 lines doing four jobs; `prompting` and `core.agent.kinds.subagents` left with a
rule that neither calls back, and what remains is wiring where every statement attaches
one thing to the graph. Four decisions constrain the shape further -- the middleware
registry merged once before either branch reads it, both `wants` sites through one
builder, `test_both_kinds_are_handed_the_same_things_to_want` guarding a second inline
mapping, and one compiled agent per definition because per path is exponential. The
piece that could come out did: `resolve_tools`, whose probe is 7.7ms and is skipped
when nothing needs it -- no definition names a tool, the workspace defines none, and
the request asks for every tool.

The thin seams the review saw were one seam: 21 test files monkeypatched
`create_deep_agent` by string path and read its kwargs. A build returning a record of
what it attached, as `ToolSurface` already does inside it, was declined here for no
behaviour change at the cost of every one of those files and the return type's
callers. *(2026-09-16.)* That half was taken later -- see *Taken after all: a build
says what it attached* below.

**Not taken: one reader for the agent and subagent formats.** The same review found the
two formats reading the same fields through copies kept by hand, and 14 of 23 commits to
the agent spec touching the subagent reader too. Most of what it named was already
shared: who reaches what and the narrowing in `declares` through `narrowed_for`, unknown
keys through `fields.unrecognised`, every per-field rule through `fields.Reader` and
`wanted_model`; seven of the 14 commits were renames, moves or the prose pass. What
stays copied is each format's list of calls -- which rule reads which field, and what
leaving it out means. Merging it would take about fifty lines from each format and add
a shared function of the same size with the list behind a layer of options, and the
list is where the deliberate differences live: the star on `skills` and `subagents`,
`memory`, `bundle`, `build`.

The risk the history does show is a fix reaching one reader: refusing a list where one
model goes reached the subagent format and left the agent's turning it into a string.
`tests/unit/test_format_parity.py` holds the readers to each other instead: it reads the
same documents through both and compares every shared field or the refusal, requires
`["*"]` to part the formats on exactly `skills` and `subagents`, and checks each
format's keys against its spec. A field both formats take goes into both readers and
into a document there, and the test is red until it does. *(2026-09-16.)*

**Left for now: one spec for agents and subagents.** Not one reader but one record. The
two share 14 fields, and the differences are guarantees the types enforce: a subagent
has a prompt or a `build`, exactly one; `memory` is an agent's, and `bundle`, `carried`
and `build` a subagent's; `declares` parts where no audience reaches, which
`test_the_two_kinds_declare_the_unaudienced_axes_differently` exists to catch, since one
shared body would erase it with nothing red. One class would turn each into a check on
a kind field, and an agent with a `build` would become something code can construct.
Nothing downstream asked for it either: `build_agent` reads two things off an
`AgentSpec`, the model functions in `core/agent/kinds/subagents.py` already take either,
and access reads both by attribute name on purpose. What would change the answer is a
product change: one definition file usable both as an agent and as a subagent.
*(2026-09-17.)*

*Measured the next day: all 14 shared fields declare the same type, 13 the same
default, and 12 are read by byte-identical calls -- the other two are `skills` and
`subagents`, where a subagent passes `refuse_all`. Only 4 of the 14 comments could move
to a base unchanged; the other 10 explain the same field for its own kind.
(2026-09-18.)*

**Taken, in the middle form: `SpecBase`, a base holding what both kinds declare
identically.** Two things neither entry above had came out of building it. **`kw_only=True`
is the whole of the mechanism**: a base holding defaulted fields looks as if it forbids
a required `system_prompt` in a subclass (*non-default argument follows default
argument*), and with it the subclass keeps the field required; nothing builds either
spec positionally. **And a shared comment
turned out to be the point rather than the cost**: where the kinds part, the one comment
now says which is which -- an agent may write `["*"]` for `subagents` and a subagent may
not, because for a subagent that set includes itself and always loops. Two comments each
knowing half is how the agent's `wanted` sat stale for months, describing a list form
`wanted_model` had stopped taking; one comment cannot disagree with itself.

**One spec is still refused, on a changed footing.** `_subject` tells the kinds apart by
`isinstance` to name the file a refusal is about, and each kind has an invariant the
other must not run. The argument that merging makes a promptless agent constructible is
spent: `system_prompt` is required on both now. What replaced it is a test:
`test_the_known_set_matches_the_spec_it_builds` asserts *equality* between a format's
`KNOWN` and the non-derived fields of its spec, both ways, which catches a key accepted
and never read. One class makes a spec's fields the union of both kinds', so that check
could only weaken to a subset. A spec built in code saying what no file may say moves to
`memory`, `build`, `bundle` and `carried`, where a subagent could carry a `memory`
nothing reads. The readers are untouched -- *Not taken: one reader* stands. The cost,
accepted: reading a spec is two files, and ten comments true of one kind now have to be
true of both. *(2026-09-18.)*

*`system_prompt` was unified by losing its default rather than sharing one. An agent has
no second shape, so a default would be a second way past the `parse` refusal; a compiled
subagent carries its instruction inside the graph, so its one caller writes
`system_prompt=""`, saying* this one has no prompt*, and `__post_init__` can still tell
"brought a graph" from "said nothing". Putting the default back made a promptless agent
constructible with all 1,959 tests passing -- nothing watched that door, and
`test_neither_kind_can_be_built_without_saying_what_it_instructs_with` is it now. Named
`SpecBase` rather than `Definition`, because a tool, a skill and a middleware are
definitions too, and rather than `AgentDefinition`, which would make a subagent a kind of
agent beside the `AgentSpec` that already names one. (2026-10-17.)*

**Taken: a `ToolSpec` a tool is declared as.** Left for now on 2026-09-18 with its
trigger named: the first field kingfisher wants to read off a tool. That fired when a
tool could say who may use it -- `source_ids` and `datazone` are read off a tool and by
nothing else. Their first form took only a plain function: a `@tool` object, a
`BaseTool` instance or a third-party LangChain tool raised `AttributeError` on
`__name__`, so most tools could carry no lock; and the lock sat on a private attribute
of a `StructuredTool` subclass, read back by name, because the tools kind could not ask
langchain what it held.

`ToolSpec` is a plain frozen record -- the tool, then keyword-only `name`, `description`,
`source_ids` and `datazone` -- and `TOOLS` holds it or a bare tool, which is
`ToolSpec(tool)`. It wraps any form, so the lock holds on every one, and it is read
where the tool is loaded: the loader knows the file to name in a refusal, reads
`source_ids` with the reader a definition's own line goes through, and puts the lock on
the `FoundTool` it makes, never on the tool. `FoundTool` pairs with it: the spec is what
the author wrote, a `FoundTool` what the loader made of it.

`name` and `description` are applied at load, so a bad one is refused at startup.
Measured with a real `create_agent` run: a `BaseTool` copied with `model_copy` and a
plain function made into a `StructuredTool` leave the author's object unchanged, show
the new values in the schema, keep an injected `ToolRuntime` hidden, and dispatch. A
class computing either field -- a property inherited ahead of `BaseTool` -- answers with
its own whatever a copy holds, so the loader checks the copy and refuses one that did
not take. That is why the tools kind imports `langchain_core`, inside the one function
that renames, so the command does not load it before it knows its verb. It carries no
`metadata` or `extras`: the trap the deferral named -- `@tool` refusing `metadata=`, and
`extras=` landing where a reader of `metadata` never looks -- stays shut, because nothing
kingfisher reads is on the tool. *(2026-10-18.)*

**Taken: a `TypedDict` for a package writing `SUBAGENTS`.** A portable definition is a
plain mapping on purpose -- it pins no kingfisher version -- and the want is type
checking, not a different runtime shape. `PortableSubagent` and `CompiledSubagent` are
exported from `kingfisher` and imported under `TYPE_CHECKING`, so a checker refuses a
misspelled or refused key before a run does, and at runtime the entry is the same dict
with nothing of kingfisher's imported. It was deferred until the first package that is
not ours, and taken before that on request: `quarters.py` could not be annotated at all,
because deepagents' `SubAgent` has no `source_ids` or `datazone`.

The types are a fourth statement of a vocabulary `KNOWN`, `PORTABLE` and `NOT_PORTABLE`
already state, so they sit beside those tables in `subagents/spec.py`, and
`test_subagent_typeddicts` holds each type's keys equal to its table and its required
keys to the ones `declared` refuses an entry without. It reads `Required[...]` through
`get_type_hints`, not `__required_keys__`: the module postpones annotations, so the class
is made with the marker still a string and lists every key as optional.
*(2026-09-18; taken 2026-10-18.)*

**Accepting `SubagentSpec` instances is refused rather than deferred.** Six of its
fields are `derived` and the reader fills them: a hand-built spec writing
`tools=("csv_profile::profile",)` gets an empty `tool_sources`, so
`Offering.refuse_moved` checks nothing and a stale path claim a mapping would have
caught passes in silence. Publishing the type would also put a name through the front
door under a rule that has removed eleven. *(2026-09-18.)*

**Taken after all: a build says what it attached.** *Not taken: splitting the build
plan* weighed the cost against "no behaviour change", and was right on that evidence.
The benefit came from elsewhere: six functions defined inside `build_agent` could not
be called by any test, while `declared_middleware`, which left the file under the same
criterion, is called directly by twenty-six. `build_agent` returns `Assembled` now
rather than a graph.

**The cost that entry quoted was an undercount.** Measured before starting: 22 files
reached the spy, 34 call `build_agent`, and 58 of 148 call sites consume the return,
two of them in `src/`. The rest are tests, which made it affordable: they were being
edited anyway, because deleting the spy was the point. That spy patched
`create_deep_agent` by string path and read the kwargs back, making its argument list a
contract nothing declared and that helpers restated by hand. `Assembled` holds every
keyword, not only those a test reads: `checkpointer` is asserted on by nothing, and
`interrupt_on` was asserted on by nothing until the commit that added it -- the case a
record of only-what-is-interesting fails the next time.

**`_graph_for` returns two shapes, by design.** It was settled the other way first --
the service would unwrap, and `Assembled` stay inside the agent build -- and reopened
when three tests proved unwritable under it: one asserts the backend the factory
returned is the one the agent was built on, because *a service that computed the
backend and then dropped it would satisfy every other test in this file*, and two need
the middleware list. All three are about the service's wiring, so a direct `build_agent`
call would delete them rather than weaken them. So a build kingfisher made comes back
as `Assembled` and a graph the deployment supplied comes back as itself; an `Assembled`
for that would assert a build that never ran. `_built_turn` unwraps with `isinstance`,
not `getattr(x, "graph", x)`: a duck test would accept anything with a `graph`
attribute, which is how the backend seam once lost its shell.

*Mutation-tested, and one mutation is weaker than it looks. A record reporting a
backend the graph never got reddens exactly the factory test -- the drift guard
working, since `assemble` builds one mapping and spends it on both the call and the
record. Dropping `interrupt_on` from the record reddens all six gate tests, because its
absence breaks construction for any build that gates: that proves the field is
load-bearing and nothing about the individual assertions. (2026-09-22.)*

