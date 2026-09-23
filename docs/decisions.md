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

Ordered outside-in: what a deployment writes, what a request may do with it, what
a run then meets, the surfaces it is reached through, and last the shape of the
code itself. Arrival order was what put two sections about sessions four hundred
lines apart.

| | |
|---|---|
| **What a deployment authors** | [The definition format](#the-definition-format) · [The catalogue](#the-catalogue) · [Agents and delegation](#agents-and-delegation) · [Packaging](#packaging-where-the-definitions-live) |
| **What a request may do** | [Capabilities](#capabilities) · [Source-id access](#source-id-access) · [Models and endpoints](#models-and-endpoints) |
| **What a run meets** | [What a tool returns](#what-a-tool-returns) · [Tool failure](#tool-failure) · [Confining the shell](#confining-the-shell) · [Sessions: what persists](#sessions-what-persists-and-where) · [Wiring a store](#wiring-a-store) |
| **The surfaces** | [The command line](#the-command-line) · [What doctor promises](#what-doctor-promises) · [Where a deployment reads from](#where-a-deployment-reads-from) · [The HTTP service](#the-http-service) · [The front door](#the-front-door) |
| **The codebase itself** | [Layering](#layering) · [Splitting a file](#splitting-a-file) · [The architecture rules](#the-architecture-rules) · [How much a comment says](#how-much-a-comment-says) · [The size of the test suite](#the-size-of-the-test-suite) |
| | [Proposals, and what became of them](#proposals-and-what-became-of-them) |

*Sessions* and *Wiring a store* sit together and are not one section: the first is
what survives a turn, the second is how a deployment names a storage port. It
covered `FileStore` as well until that port was removed, which is why it is not
called *Sessions and storage*.

---

## The definition format

**An entry is a name, or a mapping of `name` and what that field lets a name
carry.** `source_ids` for `tools`, `skills` and `subagents`, with `source` beside it
on `tools` and `skills`; `settings` for `middlewares`. One long form, whichever field it belongs to, read through one
loop -- so a reader who has met one has met the other.

It replaced a field-level mapping keyed by name, and not for tidiness. **That
shape could not see a name written twice.** YAML collapses `{a: X, a: Y}` before
any reader runs, so a tool named twice with two audiences lost one of them
silently, with nothing able to refuse or report it -- an access restriction that
disappears without a word, which is the failure this format exists to prevent.
The list form refuses a duplicate by name, and for both fields at once because
the check is in the shared loop.

The other half of the argument was already written in the code that got it
right first: *"a format where the whole list changed shape as soon as one entry
wanted a setting would make the common case pay for the rare one."* The mapping
form did exactly that -- adding one audience rewrote every entry in the field.

Refused rather than dropped, and the message names the entry to write.
*(2026-09-03. The `all_of` spelling nested one level deeper under it, which was the
one place the old form read better and was accepted knowingly.)* `all_of` is refused
now, and a requirement is written as the set it is -- *Source-id access* has why.

## The catalogue

**Tools and subagents nest; skills stay flat.** A folder is organisation and never
enters a name; a folder with `__init__.py` is one package and the scan stops
there. Skills nest one level and no further, because deepagents lists a skills
source one level deep: a folder under the skills root is a source of its own, and a
skill below that is reported as misplaced. Nesting skills to any depth was measured
and rejected; one level of grouping was accepted the next day, when several parties
had to ship skills into one catalogue. *(2026-08-16, `nested-discovery.md`;
2026-08-17, `skills-from-several-parties.md`.)*

**The catalogue holds paths, not content, under three named roots.** A plain
mapping rather than an object, and library-only -- no environment variable.
Derived roots are created; roots the caller supplied must already exist. A
`CatalogueSource` protocol and its adapter were designed in full and cut before
building, on the grounds that one implementation is not a seam.
*(2026-08-16, `injectable-catalogue.md`.)*

**Two definitions of one name coexist, and the refusal moves to construction.**
The catalogue keeps both, and what an agent holding two of a name gets depends on
the kind: two subagents are refused, and two tools leave it with neither, which the
run reports.
Refusing at load would stop a deployment over a clash no single agent would ever
see, unfixable by anyone who does not own both files. This was worked out three
times over -- skills got sources, tools got references, subagents came last and
failed hardest.
*(2026-08-17, `skills-from-several-parties.md`, `two-tools-called-fetch.md`,
`two-subagents-called-surveyor.md`.)*

**A reference is a selector, and a checked label too.** `vendor_a/fetch.py::fetch`
resolves to one tool, and where a name is unique the path is still checked: a
subagent naming a tool at a path it has moved from stops startup and a delegate's
build, while an agent naming one is reported by `doctor` and `kingfisher list` and
runs without the tool. The model never sees a reference -- it is given a flat
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

**Only `kinds.skills.registry` calls it.** The lister and its async twin were called
from there and from `infrastructure.harness.narrowing`, so an upgrade that moved
either meant the same edit in two files, and the async one was never pinned.
`listed` and `alisted` are the only calls now; `test_skill_registry` pins both
names and fails if another module names either. A listing becomes a `Listed` where
it arrives, so `name`, `path` and `description` are read off deepagents' metadata in
one function rather than by key wherever an entry is used. The skills index hands
deepagents' own dictionaries back to it, because its formatting reads them.
*(2026-09-16.)*

**And the repository stopped answering `names`.** The second reader that decision
removed from the deciding path was still there beside it, satisfying a port that
promises "every definition held, by the name a request grants it". It listed the
directories one level under the root, and measured on a catalogue of four it got both
halves wrong at once: it missed both skills in a source folder and advertised one
whose file does not parse. Nothing in production depended on the answer -- `warm`
forced it two lines above forcing the registry, and a bundle's skills were already the
registry's -- so three tests carried comments steering readers off it and an
architecture rule's failure message recommended it by name.

Listing more deeply was the obvious repair and is not available: it fixes the missing
skills and can never fix the advertised one, because only deepagents' parse decides
what loads. Any answer here is the disagreement, so there is no answer here.
`SkillRepository` is the one repository that is not an `AssetRepository`; it keeps
`root` and `misplaced`, which are facts about the directory rather than claims about
what the agent will be told. `reachable` is what a caller asking "is anything here?"
uses -- seeding's own tests do -- and the registry is what a caller asking for names
uses. *(2026-09-21.)*

**Middleware is a definition kind.** `middlewares/*.py` declaring `MIDDLEWARES`,
read like `tools/`, seeded like everything else, and named from a definition in
the long form that already existed. It was the one kind a workspace could not
offer, on the argument that a middleware read out of the workspace would be code
the agent can edit wrapped around the agent that edited it -- true while the
shell could write into a definition root, and false the moment `protected` was
widened to every one of them. The premise moved, so this followed.

It relocates like `tools/` as well, and that arrived late enough to be worth
recording: `definition_roots_for` took a `middlewares_root` from the day the
kind existed, under a comment promising each root "separately relocatable by its
own environment variable", and no caller ever passed it -- no variable read, no
field on `WorkspacePaths` or `Config`, and neither call site forwarding. Nothing
went red, because nothing ran from the kinds to the settings that move them.
`KINGFISHER_MIDDLEWARES_DIR` closes it, and
`test_every_definition_kind_relocates_by_its_own_variable` drives all five so a
sixth kind cannot arrive the same way.

**The name comes from the class**, the way a tool's comes from the tool.
`AgentMiddleware.name` is a *property* answering the class's own name, so
`getattr(cls, "name")` is a `str` exactly when a subclass overrode it with one --
checked rather than trusted, or the selector would stringify into `<property
object at 0x...>`. The shipped examples now declare `name = "call-cap-strict"`
and its two siblings, which is what keeps every existing definition valid and
makes the wiring block's keys the strings the classes answer to.

**Classes only in a file, where a registry may also hold a zero-argument
factory.** A file is imported once, so an object in `MIDDLEWARES` would be built
once and shared by every graph in the process -- the state leak that makes a cap
stop capping. A class in a file does everything a factory does and takes
settings besides, so the second shape would be a spelling with no capability
behind it. The registry keeps the factory because a factory can close over a
live object, which a file cannot.

**Both sources are merged once, and a name in both is refused.** `build_agent`
resolves the registry before either branch reads it, so an agent and its
delegates select from one mapping; merging twice would be a second chance to
disagree. A clash is refused rather than resolved because both sources belong to
the same deployment and renaming is available -- unlike a tool clash between two
vendors, which is why that one is qualified instead.

**`middlewares` is outside `STAGED_KINDS`, like `agents`**, and it was the test of
whether that rule held. Added to `Definitions` and left in, every supplied
catalogue in the tree stopped loading at once -- *"catalogue is missing
middlewares"* -- which is the breakage the `agents` exclusion was written to
prevent, arriving on the first kind added after it. `seeding.destinations`
now skips a kind the destination does not name, for the same reason: a
`Destination` is satisfied by shape, so one written when there were four kinds
hands over four roots.

**A deferred `langchain` import**, because the module-level one made nine light
exports heavy at once -- `seed`, `inventory`, `Origins` and the rest pulling the
agent runtime for names that never reach it. The trade `models.py` already makes
by naming its chat classes as strings.

**The shipped pairing had never been built, and did not work.** `researcher`
granted `call-cap-strict` and `tool-note` while its delegate `sweeper` named
`call-cap-generous` -- and an agent's `middlewares:` is the ceiling its delegates
are clamped by, so the parent refused it. Invisible for as long as the example
existed: with nothing registered anywhere it failed earlier for want of a
registry, and every test that touched the pair either replaced the spec or
supplied its own registry. `researcher` grants all three now, and runs under both
caps with the stricter deciding, which the file says out loud.
`test_the_middleware_pairing_builds_from_the_workspace_alone` builds it with no
registry at all. *(2026-09-07.)*

**The kind is `middlewares`, in the plural its four siblings were already in.**
`agents/`, `skills/`, `subagents/` and `tools/` are named for what they hold and
this one was not. The folder name was never free to differ: `seeding` walks
`DEFINITION_KINDS` on both sides, so the directory has to spell the field on
`Definitions`, which `test_what_the_catalogue_loads_is_accounted_for` holds equal
to the axis on `Capabilities` -- which is the line a definition writes and the
field a request sends. One name across five surfaces, and renaming any one of
them alone leaves a folder nothing looks in.

So this was taken as the breaking change it is, not as tidying: `middlewares:` in
an agent or subagent file, `middlewares` in a `CapabilitiesBody` that forbids
unknown fields, `Kingfisher(middlewares=...)`, `MIDDLEWARES` in a workspace file,
`middlewares/` in a workspace. The last one is the quiet one -- `middlewares/` is
outside `LAYOUT_DIRS`, and the marker's `LAYOUT_VERSION` is a number compared rather
than a list of directories checked, so it cannot refuse an old layout here the way
it refuses one inside a session. A workspace that
keeps a populated `middleware/` gets an empty `middlewares/` made beside it and
offers nothing, with nothing said.

**The mass noun stays wherever it is still a mass noun.** `middlewares` names the
kind -- a directory, a field, a key, a table row. `MiddlewareRepository`,
`approved_middleware`, `harness/middleware.py` and every sentence about *a
middleware* keep the singular, because `ToolRepository` already sits behind a
kind called `tools`: naming the type for one of the things is what the other four
kinds do. *(2026-09-15.)*

**A class may ask the harness for what only the harness knows.** `wants` is a
third class attribute beside `defaults` and `yaml_settable`, naming kwargs filled
in where the agent is assembled: `model`, `backend`, `definition`. It was argued
as "no asset can depend on a deployment object", and that framing was wrong and
worth correcting here, because the next person will know it is. A registered
class has always been able to close over one -- `guides/middleware.md` documents
the pattern, and a deployment can build a model in its own program and capture it.
What could not be reached was narrower and sharper: *this build's* answer, which
is decided per agent and per delegate and cannot be closed over at startup, and
the model from a `middlewares/` file, which is imported once when `Kingfisher`
reads its catalogue -- before any agent has been built, so there is no model yet to
capture.

**The set is open and lives at the two call sites.** No constant enumerates it.
Each site in `build_agent` builds a mapping from what it holds, `_instantiate`
pulls out the keys a class named, and a want nothing answers to is refused with
the site's own listing -- which is the listing to read precisely because there is
no table to read instead. Both sites go through one builder, so they cannot offer
different keys by accident; `test_both_kinds_are_handed_the_same_things_to_want`
is what catches a second mapping written inline at one of them.

**A wanted key is a name in yaml and an object in Python.** `model` may also
appear in `yaml_settable`, and what a definition writes there is resolved -- the
catalogue lookup, the endpoint check, the instance -- rather than passed through.
That keeps the vocabulary of "which model" the one every other file already uses
and puts `refuse_ungranted_endpoint` on this path for free, in `model_named`,
which `model_object` now calls as well: two copies of those three steps, one of
them forgetting the middle one, is a run sent where the caller refused with
nothing said. A want with no such resolver -- `backend`, `definition` -- has no
name a file could carry, so a value written for one is refused rather than
interpreted, on the class rather than on the write.

**A delegate is handed its own model, not its agent's.** The proposal said both
call sites had the built model in scope; only one did. A delegate's is not built
until `as_subagent`'s last line, so what is in scope where its middleware is made
is the *agent's* -- and handing that over is wrong in the direction that costs
money, since a delegate pinned to the cheap model would be compacted by the
expensive one. `_with_helpers` already knew the answer and now passes it down.
The same mistake had been made once before for a helper's inherited model.

**The example is `compaction.py`, and it runs.** A `SummarizationMiddleware`
subclass that wants all three: six lines of declaration, a note written to
`/derived` on both the sync and async paths, and `researcher.yaml` names it. It is
the motivating case rather than an illustration of one -- handed `model: "gpt-5"`
as a string, langchain's own class passes it to `init_chat_model`, which infers a
provider and reads credentials from the environment, around the catalogue and the
endpoint's `base_url` entirely. Two things it teaches were measured rather than
assumed: an unset `trigger` normalises to no clauses and never fires, and a
backend reports a failed write by returning one rather than raising.
*(2026-09-15.)*

**The shipped `assistant` names its middleware, and does not write the star.**
`middlewares: ["*"]` was argued as the one form a shipped file could carry: a name
is refused on a deployment that registered nothing, and a star resolves to nothing
there. *Middleware is a definition kind* is what made that false, and nobody read
the two together -- a file in the workspace's `middlewares/` counts as registered,
and `seed` copies the examples in. So a seeded workspace wrapped the agent a reader
runs first in every one of them, on the agent and on its general-purpose delegate,
with neither its file nor its prompt saying so. Measured by building it. The test
for the star checked it against an empty registry, which is the one registry a
seeded workspace never has.

It names all four examples instead, so what it runs under is written in its file
and its prompt can say so -- a twenty-call budget, a note on every tool result, a
summariser past sixty messages -- and a middleware added later for another agent
does not arrive here unasked. The price is `seed`'s rule against named middleware:
a plain `kingfisher seed` now leaves `assistant` behind alongside `researcher`, and
`--all` is how it arrives. Seeding `middlewares/` only under `--all`, and a star
that skipped workspace files, were weighed and not taken: the first undoes half of
*Middleware is a definition kind* for one line of one file, the second makes the
two sources of one registry mean different things to one field.
*(2026-09-16.)*

**The definition repositories are internal, and each declares what is read from
it.** `ports.md` listed four of them as ports to replace when a catalogue is not a
directory, and nothing outside this repository could: neither `Definitions` nor the
interfaces are exported. The repositories with no directory were test fakes, a
delegate's carried tools, and the empty middleware default. So the interfaces declared the basics, the local repositories carried the
rest, and thirteen reads went through `getattr` with a default -- one of them under
a comment saying a repository that is not the local one answers nothing rather than
raising. Two of those answers were behaviour, and running an in-memory repository
through them showed it: without `documents` a session was never pinned to its agent,
so a later turn asking the same session for another agent was served instead of
refused; without `bundles` a delegate's carried tools were dropped.

Settled the way `CatalogueSource` was, above: one implementation is not a seam. The
rows left `ports.md`, every member a consumer reads is on its interface -- `root`
too, as `Path | None`, which `CarriedTools`, `NoMiddleware` and the fakes answer --
and `test_nothing_reads_a_repository_member_with_a_default` holds it. It exempts one
read by name, because it is not of a repository: a `SessionStore`'s `root`, which is
a port a deployment does implement.

Left as it was, and taken out the next day: skills could still be mounted from a
repository with no directory, through `files`, and nothing in production did that
any more. *(2026-09-16.)*

**Removed with it: skills mounted from a store.** `kinds.skills` could fill an
in-memory store from any repository's `files` and mount it read-only, so a skill
catalogue need not be a directory. Once the repositories were internal, the only
repositories with no directory were two test fakes. So the store module, `files` on
the port and the local repository, and the branches for a catalogue with no
directory are gone, and `SkillRepository` declares `root` as a `Path`. The shell is
always told `$KINGFISHER_SKILLS`, since there is always a directory to name.
`kinds/skills` imports `deepagents` alone now, deferred in the registry, and no kind
module loads a provider SDK at import. *(2026-09-17.)*

**One envelope for what a Python module declares.** Three kinds read modules out
of the workspace -- `TOOLS`, `SUBAGENTS`, `MIDDLEWARES` -- and each catalogue wrote
out the same load, the same `getattr`, and the same two refusals. The copies had
drifted where a copy drifts first: middleware told an author the type it got and
stopped, where tools and subagents also said what to write instead. Nothing was
red, and no duplication rule finds it, because the three say it about different
nouns.

`kinds/importing.Export` carries the four things that differ -- the name, the
kind's own error, what the list holds, and one entry spelled as an author would
write it -- and `exported_from` is the envelope. A catalogue declares its
`DECLARES` once beside its error class, and the loop body is one line.

`test_refusals.py` is where this showed up as more than tidying. Its table names
every refusal in the catalogue-reading code, keyed by function and counted against
the parsed source, so moving two refusals out of three functions made five tests go
red at once and the table now says the refusal lives in one place. The counter
itself needed widening: it already followed an error class *handed in* as a
parameter, and an envelope hands one in as a field of a parameter, which it read as
no refusal at all. *(2026-09-18.)*

**One walk for both definition roots.** `NEAR_MISS` and `SUFFIX` moved to
`documents` so the agent catalogue would stop importing the subagent one for them;
the walk that used them stayed behind, written out twice. The two copies differed
by the error class and by one `continue`, and the `.yml` refusal in the middle of
each was byte-identical -- which is a copy that has not drifted yet rather than one
that cannot.

`documents_in` takes the error class, the way `require_literal_prompt` beside it
already did, and a `skipping` set of folder names. That set is the asymmetry and
the reason this is not simply a shared function: `tools/` and `skills/` under
`subagents/` hold a bundle's own assets and are kept out of the scan, and under
`agents/` they are ordinary folder names. Only the subagent caller passes them.
Handing the walk that list by default, or passing it at both call sites, loses an
agent filed under `agents/tools/` with nothing red, so
`test_a_folder_called_tools_is_organisation_here_too` is what holds the other side
of it.

`documents` now reads `kinds.importing.skipped`, so the Layering entry saying four
kind catalogues are that module's only readers names one reader short.
*(2026-09-18.)*

**A delegate lists what it takes from its own folder, and only that arrives.** Each
`tools:` and `skills:` entry may say `source: shared` (the catalogue, which is what a
plain name means) or `source: bundled` (the subagent's own folder). The folder used
to grant by itself: every file in `subagents/<name>/tools/` reached the delegate
with no line naming it, and an optional `bundle:` key could describe the folder and
be checked against it while granting nothing.

That had two silent failures and one misleading key. A file dropped into the folder
was granted with nothing in the definition changing; renaming the folder or the
`name:` took everything away, reported only as a warning; and `bundle:` sat among
fields that grant, granted nothing, and needed a paragraph in the guide explaining
why it was nested. Listing as the grant removes all three: the list is the claim, so
there is nothing separate to keep in step.

**`bundled` names where the file is, not who may use it.** The alternative was a
label on a file in the shared `tools/`. That makes privacy something any
definition can put on or take off, and two subagents claiming one tool would need a
rule of their own. With the location reading, a tool is private because of where
its file sits.

**An unlisted file in a delegate's folder is refused, not ignored.** Every
deployment upgrading has exactly these files, granted by being there, and ignoring
them would lose each one silently -- the failure the change exists to remove. It is
the old `bundle:` check, now always on: `rules.miscounted` compares the list and the
folder in both directions, `warm` refuses, and `doctor` fails on `bundled entries`.
The build filters to the list as well, so a caller that skipped `warm` is still
handed only what is listed. A compiled delegate cannot list skills, so a `skills/`
folder beside one is refused by the same rule rather than warned about.

**Subagents only.** `source: bundled` in an agent file is refused: an agent has no
folder, and the folder exists so a delegate can hold something its caller cannot.
A bundled entry also takes no `source_ids` -- it reaches whoever reaches the
delegate -- and no `file::name` path, since the folder is where it is.

**What was kept.** The shadowing rule stays: a delegate inheriting the catalogue
with `"*"` and listing its own `fetch` gets its own, and `list` still marks it,
because the catalogue's `fetch` is still dropped for that delegate. A folder named
after no definition stays a warning; the renamed definition that left it is refused
from its own side when it listed anything. A portable `SUBAGENTS` entry carries
objects instead, in its plain `tools` and `skills`, since it has no folder for
`bundled` to name.
*(2026-09-23.)*

## Agents and delegation

**The main agent is a definition.** It used to be assembled from four places that
did not know about each other. The agent file is the baseline and `Capabilities`
only ever narrows it; the prompt is appended to the harness prompt, never
replaces it; naming an agent is required; the agent is fixed when the session
starts and snapshotted into it. *(2026-08-18, `agents-as-definitions.md`.)*

**Omission means different things on different axes, deliberately.** The tool
fields inherit everything available; `skills`, `subagents` and `middlewares` omit
to none. Tools are what an agent needs to *act*; the others are what it needs to
know and to ask. *(2026-08-18, `agents-as-definitions.md`.)*

**A delegate names its skills; `["*"]` is refused there and taken on an agent.**
The star resolves to whatever the request granted, and a delegate is handed a
skills index only where its skills are *named* -- so under the ordinary request,
which grants every skill, `skills: ["*"]` read as all of them and arrived as
none. It worked only when the caller happened to narrow skills, which is a
meaning no author of the file can see and the opposite of the one they wrote.
Refused at the read, beside the `subagents` star and for the same reason: the
habit comes from a request, where the star is the ordinary way to say
everything. The two tool axes still take it, where it means what it says.

**An agent grants the helpers its delegates consult, and the docs said otherwise
for months.** *Helpers arrive with the delegate that wants them* promised that an
agent naming `reviewer` got whatever `reviewer` named; `kingfisher list` printed
that chain, computed by `inventory.reached`. The build never did it:
`AgentSpec.declares` returns the agent's own `subagents:`, and `subagent_helpers`
narrows a delegate's helpers by it, so an agent naming only the parent built a
delegate with no `task` tool at all. Measured both ways before changing anything --
naming both works, naming one drops the helper silently.

The code kept its rule and the prose moved to it, because the alternative widens
one axis alone: `middlewares:` already makes an agent's line the ceiling its
delegates are clamped by -- *the shipped pairing had never been built* is the entry
where that cost `researcher` a name it does not use itself -- and a `subagents:`
that granted what it did not name would be the only field where naming one thing
grants another. It would also compile a graph per turn for a helper nobody asked
for.

So the listing prints what an agent *activates*, `reached` is `activated` and no
longer walks the chain, and the guide says a delegate's helper needs naming on the
agent too. What did not change: a *request* naming `reviewer` alone still gets a
reviewer without its helper, dropped rather than refused, which is the half the
guide always had right. *(2026-09-17.)*

**Delegation is unbounded in depth and is a DAG, not a tree.** A definition may
appear in several places, each is compiled once and its runnable shared, and a
cycle anywhere in the catalogue is refused, whether or not the agent being built
reaches it. Compiling per *path* is exponential -- 15 definitions naming three each
is 6,872 compilations and seven seconds. *(2026-08-18, `subagents-all-the-way-down.md`.)*

"At load" meant when a build read the catalogue, which was the only time it was
read. The catalogue has been read at startup since, and a cycle is still not
refused there: `kingfisher list` reports one, and the first build of an agent with
delegates refuses it. *(2026-09-17.)*

**A subagent may be a compiled graph rather than a spec**, told apart by
extension. `SUBAGENTS` is declared and never inferred; name and description are
static text and only the graph comes from a function; the function receives the
model and tools rather than choosing them. A `.yml` file there is refused as the
`.yaml` it was meant to be; anything else is left alone. *(2026-08-18,
`compiled-subagents.md`.)*

That read "an unrecognised extension in `subagents/` is an error" until now, though
it was reversed the day it was written: a folder there may be a Python package, and
a package may hold a fixture or a prompt beside its `__init__.py`, so the one real
confusion is named rather than every unfamiliar suffix refused. *(2026-09-17.)*

**One stack for every graph that holds the workspace tools.** Three graphs hold
them -- the agent, a delegate, and the `general-purpose` delegate deepagents supplies
-- and each composed its own middleware where it was built. The one built last got
none of it: deepagents fills a delegate spec that names no tools from the parent, so
`general-purpose` held the agent's own tool objects with no host-path guard, no error
guard and no path translation. A tool call through it reached the tool with the paths
the model wrote, which is exactly the leak translation exists to close, and a failing
tool raised instead of answering. `tool_guards` builds the three now and all three
sites call it, so a fourth cannot be written half-right.

**A compiled delegate's tools are wrapped, because it is the fourth graph.**
`tool_guards` reaches a graph through its middleware, and a compiled delegate has
none -- deepagents runs it as given. The shipped `scribe` is what showed it: three
path-taking tools handed to `show-your-work`, and the first call died on
`log_levels('/data/api.log')` with `FileNotFoundError`, the run over. `GuardedTool`
carries the translation and the error conversion on the tool objects, which is the
one thing kingfisher still owns for a graph it did not build.

This reverses, for that path only, the reasoning `WorkspaceToolPaths` records --
rewrite the call rather than wrap each tool, because the tools are not alike. They
still are not, so the wrapper normalises: a plain function becomes the tool
`create_agent` would have made of it anyway, refusing a missing docstring exactly
where that already failed, and a `BaseTool` keeps its own `args_schema`, since a
wrapper advertising `**kwargs` tells the model the wrong arguments and nothing
raises. A refusal is returned rather than raised, because inside a graph kingfisher
did not build nothing catches one -- measured for `ToolException`, `ValueError` and
`FileNotFoundError` alike, each ending the run -- so `handle_tool_error` converts it
before it leaves the tool. That also keeps `ToolMessage.status` true, which
`show_your_work` reads to report a call as failed. *(2026-09-18.)*

**The skills switch is the deployment's, for delegates too.** `cfg.skills_enabled`
says what is wired and a request says what it wants of that. The delegate branch asked
only the request, so a workspace with skills switched off still handed a delegate an
index of the catalogue while the agent got none and `doctor` reported that none would
be offered. Read once now, beside the request's own ceiling. A delegate's *own* bundle
stays outside the switch: it is the delegate's folder rather than the catalogue the
switch is about, and the backend mounts its route either way, so withholding the index
would leave those files reachable and unnamed. *(2026-09-18, from an architecture
review; both were found by reading the three stacks side by side.)*

**A definition names at most one model**, and a subagent naming none runs its
caller's. A list there is refused rather than read. `indistinct` reports a
delegate that named a model and did not end up anywhere different.
*(2026-08-18, `compiled-subagents.md`, `agents-as-definitions.md`.)*

**Reversed on 2026-08-19: a list of models, and `distinct: true`.** `model` took a
list tried in order, and `alias` was the only thing that could use one, so the two
went together. `distinct: true` turned `indistinct`'s report into a refusal, and
`second-opinion` was its only user in the shipped catalogue, so both went; the field
can come back if that delegate does. This entry kept describing both until
2026-09-17.

**A turn resolves its agent once.** Admission asked `_agent_for` twice, and down
different branches of the same function: the first resolved from the catalogue and
wrote the session's pin, the second read that pin back and parsed it. Counted rather
than reasoned about, because both answers agreed and nothing was ever wrong --
measured at two calls, two snapshot reads and two parses per turn under a policy,
one of each without.

`_admitted` resolves it and hands it to both readers, so `_graph_for` takes the spec
rather than deriving it, along with the source ids it was also resolving a second
time. Both are required keywords: this method has one caller in `src/`, and a
default meaning "work it out yourself" would have been a branch alive only in tests.

**It is resolved only where something will read it**, and that guard is behaviour
rather than economy. A deployment that supplied its own graph and declares no policy
has no reader for the spec: the build never asks, and the withheld report only
filters by an agent's audience where a vocabulary is in force. Resolving anyway
would make such a session start refusing a request that names a different agent
mid-conversation, which today it does not. Both halves of that condition are driven
-- a supplied graph with no policy reads the snapshot never, and one under a policy
reads it once, because the report still wants what the agent declares.
*(2026-09-23, from an architecture review, whose count of three reads was two.)*

## Packaging: where the definitions live

This reversed twice. The current answer is the third one.

**Definitions live outside the wheel, and where a deployment gets them is a
setting.** `KINGFISHER_ASSETS` names one path, read in `application/config.py`;
`seed(into, source)` takes it as a required argument with no `None` branch. This
repository's own set is `assets_examples/`, which is *not* shipped and exists to
be read and copied. `assets/` is committed holding only a `README.md` and ignores
everything else, because it is where a deployment puts content it did not write.
*(2026-08-19, `examples-are-ours-assets-are-yours.md`.)*

**A subagent can travel, and kingfisher discovers nothing.** A `SUBAGENTS` entry
with no `build` is a declaration kingfisher assembles, restricted to what a
definition can answer without seeing the deployment it lands in: `name`,
`description`, `system_prompt`, `builtin_tools`, `tools`, `skills`, `metadata`.
`tools` holds the tool objects and `skills` an absolute directory, never names. The
four it may not write -- `subagents`, `middlewares`, `model`, `source_ids` -- each
name something only one deployment knows, and each is refused with that reason
rather than as an unknown key.

**What it carries goes in the plain fields, not under `bundle`.** It was written
`bundle: {tools: [...], skills: ...}`, the same key a document used to describe its
folder, so one word meant a claim in one format and the goods in the other. The
nesting only said *these are not names*, which the objects already say. A string or a `{name: ...}` entry in
a portable `tools` is refused as a name, and `bundle` is refused with the two plain
fields in the message. *(2026-09-23.)*

**This is not the entry above, and the difference is who does the finding.** That
reversal was about *discovery*: assets leaving the repository and being found
through entry points kingfisher named none of. Here kingfisher finds nothing.
A deployment writes `subagents/acme.py` holding `from acme_agents import
SUBAGENTS`, and that file is the whole of the opt-in -- in the workspace, beside
every other definition, where `Origins` and `doctor` already report from. The
mechanism needed no new code: the catalogue has always imported `subagents/*.py`
and read `SUBAGENTS`, and the only thing stopping a package import was `declared`
demanding a `build`.

**The rules hang on the shape, not on the origin.** A re-export hands over
mappings indistinguishable from ones typed into the same file, so nothing can ask
whether an entry was imported. `build` present means the compiled rules, absent
means the portable ones. The alternative was a second export name or a
self-declared marker, and both would have been a rule about where an entry came
from that any local file could claim.

**What it carries is its own, and `builtin_tools` is the exception.** Carried
tools reach that delegate and nothing else -- they enter no catalogue, so nothing
can grant or narrow them, which is what atomic means. Built-ins are the host's
rather than the definition's, so they stay narrowed by the request: otherwise
`pip install` would be a way to put back a shell a deployment had turned off.

**`Holdings` gained a second backing rather than the spec gaining contents.**
`SubagentSpec.bundle` stays the claim it was -- names, checked against a folder --
and `carried` beside it holds what a definition brought instead, refused together
by `__post_init__` since a delegate owns one or the other. Putting imported tool
objects on the spec for folder bundles too would have made *parsing* a definition
import its bundle's Python, where now the import happens when a bundle's tools are
asked for. `kingfisher list` asks, to report them, and so does `warm`.
*(2026-09-16.)*

**Considered and not taken: `own_subagents`, private nested helpers.** The key was
reserved when `subagents:` was refused, so that a portable definition wanting a
helper could get one later without any file changing meaning. Costed on 2026-09-17
and not built.

The case for it is one thing, because the other two reasons to delegate are already
unavailable to a portable definition: it cannot give a helper a different tool
surface, since its carried bundle is one set with nothing to split it between, and
it cannot give one a different model, since `model` is refused as unportable. What
is left is context isolation -- a delegate burning its own context and handing back
an answer -- and `build` already answers that. A compiled entry composes whatever
graph it likes and closes over its own tools.

The cost was not small either. Nested specs inside specs means recursion in the
reader; a cycle check over a shape where `entry["own_subagents"] = [entry]` is
constructible in Python; a compilation memo keyed on `(name, nested, id(inherited))`
that two packages each shipping a private `checker` would collide in; and decisions
about whether a request may narrow a private helper away, and how `list` shows one.

So the refusal stays and the key stays reserved, and what changed instead was the
advice. It used to say *fold the step into this one's prompt*, which is wrong in the
one case somebody would be reading it: a prompt cannot buy a fresh context, which is
the whole reason to want a helper. It now names the two real answers -- ship the
helper as a delegate of its own, or write `build` -- and `formats.md` says what each
costs. `test_the_helper_refusal_points_at_a_key_that_exists` holds the advice to the
format, so a renamed `build` cannot leave it pointing at nothing.

Not measured, and said so plainly: nobody has hit the refusal yet. Build it when
somebody does, and their case will say which of the decisions above to take.
*(2026-09-17.)*

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

**Seeding lays the workspace out itself, and its parameter says what it is.**
`seed(into: Destination, source: Path) -> Seeded`. `into` rather than `cfg`,
because `Destination` is a Protocol precisely so seeding can run before a
`Config` exists -- the model catalogue is a file inside the workspace, so
reading one raises before the directory does -- and naming the parameter after
the type it deliberately does not take undid that where a reader most needed it.
`Seeded` rather than `Seeding`, because it is a record of something finished.

And it calls `ensure_layout` first. Seeding into a workspace that was never laid
out used to succeed, report every definition written, and leave no
`models.yaml.example` -- the dead end that write was moved into `ensure_layout`
to avoid. The CLI had the ordering and a docstring explaining it; a library
caller reading the signature had neither. Not a new responsibility so much as
the rest of one seeding already had: it was already creating the four catalogue
directories and omitting only the file that makes the result usable.
*(2026-09-01.)*

**`config_from_env`, not `from_env`.** It returns a `Config` and the bare name
said none of that -- imported at package level, which is how most calls read, it
could have returned anything, and it sat beside a qualified `paths_from_env`.
Inside `application/config.py` the old name read well because the module
qualified it, and that one call site now stutters; fifty-two others got clearer.
*(2026-09-01.)*

## Capabilities

**One refusal, with the caller naming itself.** Tool-name rules live in a value
object, `Offering`, beside `Found` -- not a `Tool` entity. Both were in the domain
when this was written and are in `kinds.tools.spec` now, with the tools format. Offered
names and sources are stored; grants are derived. There is a test that fails when
a function has no caller outside tests.
*(2026-08-17, `tool-rules-in-the-domain.md`.)*

**Capabilities narrow and never widen.** There was one exception: an upload could
widen skills and subagents, because those were the caller's own text, and a
middleware name got no such exemption because it selects code the deployment wrote.
*(2026-08-18, `agents-as-definitions.md`, and the middleware work of 2026-08-31.)*
Uploads were removed on 2026-09-16, and nothing widens now.

**And a turn is built inside the ceiling, which nothing checked.** `_admitted`
narrows the deployment's grants by what the request asked for and hands the result
to the build. Handing the request's own capabilities instead -- one line -- let a
caller reach a workspace tool the deployment never granted, and the whole suite
stayed green: 2,083 tests, because every fixture but two leaves the deployment
unrestricted, and there the two are equal.

`_graph_for` took `capabilities` as an optional keyword defaulting to
`request.capabilities`, which is that escalation written into a signature. It is
required now, so there is no way to build a turn's graph without saying what it may
use, and `test_a_turn_is_built_inside_the_deployments_ceiling` drives a real turn on
a deployment that grants one tool against a request asking for two. It reads what
the build was handed at the seam rather than computing the intersection and
comparing -- a test that works out the expected answer the same way the code does
agrees with itself and checks nothing, which is how this stayed uncovered.
*(2026-09-23, from an architecture review, whose own card was about something
else.)*

**The axes are read off the dataclass.** They were written three times -- as fields,
again in `__post_init__` to be normalised, again in `intersect` to be narrowed --
with nothing holding the three lists together. Measured, by adding a ninth axis and
changing nothing else: it kept whatever list it arrived as, breaking the contract
that a `Selection` is `ALL` or a tuple, and `intersect` answered `'*'` for it, so a
grant of one name narrowed by a request asking two returned everything. A narrowing
that widens is the one thing this object promises not to do, and the whole suite,
ruff and ty stayed green.

Both loops now read `SELECTIONS`, which is `fields(Capabilities)` minus the one field
marked `SWITCH`. A field says which of the two it is in its own declaration and
nowhere else. The marker is on the exception rather than on the axes, so the
defaulting fails closed: a field that is neither a `Selection` nor marked reaches
`_normalise`, which refuses a value that is not a selection at the first
construction, rather than being quietly left out of the narrowing.

`intersect` builds its answer with `replace` rather than a fresh `Capabilities`, and
that is worth stating because it changes what a mistake looks like: a dropped axis
keeps the grant whole instead of resetting to the class default. Safer, and the
reason the obvious test did not bite -- a grant of one name against a request asking
two answers the grant either way. The rule drives each axis with two sets that
overlap without either containing the other. *(2026-09-18.)*

## Source-id access

**An audience lives in the definition it is about.** An agent or subagent writes
`source_ids:` for who may reach it, and an entry of `tools:`, `subagents:` or
`skills:` may be written long -- `{name: X, source_ids: [...]}` -- for who reaches
that one. One central
file, `source_ids.yaml`, holds the vocabulary -- names only, no policy. It held
`contains` as well, which is a retired spelling now and refused.
An audience resolves into an ordinary `Capabilities`, so nothing downstream
changed: an ungranted tool is never attached to the graph and an ungranted
subagent is never compiled. *(2026-08-31.)*

The rule that makes it safe to add to an existing definition: **`source_ids:` is the
default audience for everything the definition holds, and the ceiling on what any
entry may say.** So an omitted or plain-list `tools:` keeps its exact meaning, and
an entry is always an *and* with the definition's own line -- the only way to
reach an entry is through the definition holding it, so naming a source id from
outside that line adds a second requirement rather than replacing the first. A definition with no `source_ids:` line is reachable by everyone, and the report of
every such definition is built at startup -- default-open must not also be silent.
It is `kingfisher list` that prints it; a plain start says nothing.

**Reversed: a central `access.yaml` listing every asset by name.** Built in full
-- vocabulary, per-asset audiences, reconciliation against the catalogue, two
load reports -- then replaced within the same branch. What killed it was not
taste. A central table can name an asset the workspace no longer offers, and that
stale entry had to be *dropped* rather than merely reported, because the grant it
produced reached `Offering.refuse_unknown` and turned every turn into a refusal.
A definition *is* the asset it is about, so that failure has no shape in the
current design: the reconciliation, both its reports and the whole class of bug
went with it. What survived unchanged: `UNSCOPED`, the refusal of a call that
names no caller, the per-turn re-check of a session's pinned agent, and the
closed vocabulary. `for_groups` survived that reversal too and did not survive
the next one -- see below. *(2026-08-31, reversed the same day.)*

**Reversed: `for_groups` and the `Caller` handle.** A caller said who they were
once and reused the handle; now every call takes `source_ids=`. Counted before
removing: zero production callers. The service -- then the only consumer that served
several callers, which is the case the handle was built for -- resolved source ids
per request from a header and passed `source_ids=` at all seven of its call sites.
Two of the handle's three stated benefits did not survive checking either: the
grant it resolved once measures **0.81 microseconds**, because the transitive
closure is computed when `source_ids.yaml` loads; and refusing an unknown source id "at
the boundary rather than at the first turn" is a gap of one call, since `expand`
still refuses before any agent is built. What was real was the script
ergonomics, and one thing that was not a benefit at all: `held_for` tested
`isinstance(source_ids, tuple)` and read anything else as *no opinion*, so the
coercion inside `for_groups` was the only reason `["A"]` ever narrowed. That
moved into `held_for`, which now takes any sequence and refuses a bare string.
*(2026-09-03.)*

**An audience list is an `or`, and an entry of it may be an `and`.** A
requirement is satisfied only by holding several source ids at once, and is
written two ways deliberately: named in `source_ids.yaml` for anything reused,
inline as one entry of a list for a one-off. The same shape both places, so the
named form is literally a *name for* the inline one rather than a second
mechanism -- the argument against two spellings was drift, and using one form
with one evaluation is what answers it. The result is or-of-ands, which is the
shape access rules take, out of one field with no rule about how two fields
combine. *(2026-09-01.)*

`expand` does the work, which is what kept `reaches` nearly unchanged: covering
closes first, then a compound joins the held set once its parts are held, so a
named compound is an ordinary held name by the time any audience is asked. Two
consequences follow rather than being chosen. **Covering satisfies a
requirement**, because expansion runs first -- the alternative is an `admin` who
covers both parts yet is weaker than the sum of what they reach. And **nesting
works for free**, since a compound whose parts are held is held.

**A compound is derived, and nothing may hand it over directly.** A caller may
not present one: it is what holding the parts adds up to, not something to
claim, and accepting it would let one assertion stand in for the two that the
requirement exists to demand. The refusal names the parts. Over HTTP this
surfaced as the `misconfigured` 500 a drifted vocabulary already got, which is
exactly what a gateway emitting a derived name was.

A covers list may not hand one over either, and that is the same rule rather
than a second one. `admin: [finance-senior]` was legal for one commit and gave
an admin the compound while they held neither part -- the requirement defeated
by the file declaring it, which is the caller's move made one level up. Refusing
it in only one of the two places was the inconsistency. Naming the parts reaches
the same people and is visible, since the listing prints what a compound
requires and never prints a covers list.

**The two operations are told apart by shape, not by a keyword.** `contains` and
`all_of` were the spelling until the format was asked why a YAML file needed
English words for two things YAML already has brackets for. A list body is what
a name covers and a set body is what it requires:

```yaml
source_ids:
  sales_db:
  warehouse: [sales_db, audit_log]      # covers
  sales_db_pii: {sales_db, pii}         # requires
```

`{a, b}` is a mapping of null values, which is what makes it available as a set
at all. Four things fell out, and only the first was the goal:

  - The rule that a name may not write both operations stopped needing to exist.
    A body is a list or it is a mapping, so a name that grants and costs at once
    is not a thing the format can express and not a refusal anybody maintains.
  - The listing stopped inventing a third spelling. It printed `finance_db+pii`
    for something no file could write that way, and `+` is legal inside a source
    id, so the display was ambiguous as well as unique to itself. It prints
    `{finance_db, pii}` now, and `--json` still nests -- a script should not have
    to parse a set out of a string.
  - The inline and named forms became the same characters rather than the same
    word, which is a stronger version of what the 2026-09-01 entry above wanted.
  - One new refusal was needed, and it is the cost of the change rather than a
    bonus. A set inside a covers list -- `warehouse: [{sales_db, pii}, audit_log]`
    -- reads as perfectly legal and is the bypass above with no name to look up,
    so it is refused structurally where it is written. The named half was a
    lookup; this half cannot be.

Both retired words are refused rather than ignored, on the same reasoning the
retired sections get: a deployment upgrading has a file full of policy that would
otherwise be read and dropped in silence. `A: {}` is refused with them, since an
empty set requires nothing and admits everyone -- it means the plain name it used
to spell, and `A:` is how that is written now.

**The refusal is for the file, not the name**, and the difference is what makes
it a migration rather than a wall. It began as one message per source id, which
is how every other refusal in this format works and is wrong here: a realistic
old vocabulary earns its first refusal on a `{}` line, says nothing about the
`contains` further down, and costs a restart per line. The scan runs over the
whole document before any name is read and prints each line beside the line that
replaces it, built from what the file actually says -- so the remedy is the
message rather than something to work out from it. Same argument as the seeding
message under *Reversed: `groups.yaml.example`* below, reached the same way: by
reading what an upgrade actually looks like instead of what one refusal does.

A set whose names happen to be `contains` and `all_of` is legal and stays legal.
The value is what marks a keyword, which is the same test the parser uses one
line down. *(2026-09-15.)*

**Reversed: `refuse_dead`, the rule that an entry audience must overlap its
definition's.** It moved off `parse` onto `SourceIds` first, which was a real fix
-- a definition `[reviewers]` with an entry `[analysts]` is alive when
`reviewers` contains `analysts`, and comparing raw names at parse called it
dead. Then measuring it settled the larger question: it had no true positives
left, and one class of false ones. *(2026-09-01, both the same day.)*

An entry audience is already an **and** with the definition's, because the only
way to reach an entry is through the definition holding it -- `agent_named`
refuses a caller who cannot open the agent, and nothing else hands out a spec.
So `[senior]` under `[analysts, auditors]` has always evaluated as "opens this
agent, and is senior", which is a perfectly good second requirement. The refusal
blocked writing it, and the fault it meant to catch -- `[auditors]` written
under `[analysts]` by somebody trying to widen -- is the same shape, so no rule
can separate them.

What survived is the looking. It is `SourceIds.narrowing_in` now, and feeds
`AccessReport.narrowed`, on the same reasoning as the `unrestricted` line beside
it: a thing worth noticing, said once, where an operator sees it. The typos it
was really catching are `refuse_undeclared`'s, which refuses them by name.

One consequence worth keeping: **the ordering question went with it.** A
misspelling used to trip both checks, and `never reaches anyone` explained it as
a reachability problem without mentioning the spelling. There is now one refusal
on that path.

**`source_ids.yaml` now holds vocabulary with a rule in it**, and the file's pitch
was "a dictionary, not a policy". A compound sits on that line: it still answers
"what does this name mean", but answers it with a condition. Judged to stay on
the right side -- what it cannot do is say who reaches what, which is the
property that made the central design fail. Recorded because it is cheap to
disagree with now and expensive later.

The `--json` listing's `access` key grew from a name-to-closure mapping into
`{names, requires}`. A shape change for scripts, taken because a compound has no
honest place in the old shape and the alternative was dropping the second fact.
Source-id access was two days old at the time.

**`builtin_tools` takes no audience, and that is not an omission.** deepagents
registers its own tools, so kingfisher can filter them but never leave them out
of a graph -- a live run was measured where a model called `execute` from memory. Gating them here would promise a boundary it cannot keep.
What gates them is which *agents* a source id may open, since an agent declaring a
read-only builtin set cannot yield the shell to anyone.

**Out of reach reads as not offered.** An asset a caller's source ids do not reach is
absent from listings, from the "this workspace offers ..." in a refusal, and from
the report of what a run withheld -- so nothing lets a caller enumerate the
catalogue by guessing. The withheld report hides only what source-id narrowing
removed, never what the agent simply never declared: the second is a fact about
the agent and has always been reported.

It did not hold for skills until 2026-09-17. The report's skills row was written when
a skill took no audience and was left unfiltered when it gained one, so a caller was
told a run had withheld the very skill their source ids hide. Skills are filtered now,
and compared by the one skill each spelling means, because an audience written
`catalogue::audit` has to hide `audit` too.

**The HTTP surface asks who is calling; it still authenticates nobody.**
`create_app(source_ids_from=...)` takes a callable given the request and returning
source ids, and `from_header` is shipped but never defaulted -- the header is an
argument so that trusting one is a line somebody wrote rather than what happens
when nobody decides. A deployment whose policy and identity disagree refuses at
startup, in both directions: a vocabulary with no source cannot serve a single
request, and a source with no vocabulary is a server somebody believes is locked
down and is not. *(2026-09-01.)*

A source may return source ids and nothing else. **`UNSCOPED` is unreachable
over HTTP**, because it exists to be a value a person types at a call site they
can see -- reachable from a request it is a value a *bug* can produce, and one
returned on a parse failure hands every caller everything at once. "Reaches
everything" is already a source id that contains the others, which is declared and
visible in the listing where an unscoped run would never appear.

**The caller gets a code, the log gets the reason**, and it is one rule at every
refusal. A session whose pinned agent a caller cannot reach answers 404
`unknown_session` -- the same code a wrong id gets, so holding a real one teaches
nothing. A source id the vocabulary does not declare answers 500 `misconfigured`
with a body naming no source id, while the message that lists them all goes to the
service logger. Reading and deleting a session are checked like running one: a
session you cannot run is one you cannot touch.

*The four entries above were the HTTP surface's, removed with it on 2026-09-15, and
stay as the record.* What they decided that the library still does: every call
names its caller with `source_ids=`; a caller presenting a compound is refused with
an `AccessError` naming the parts; `UNSCOPED` is a value a caller passes on purpose,
which `kingfisher run --as UNSCOPED` accepts; and a session whose agent a caller
cannot reach raises the same `UnknownSessionError` a wrong id does. Reading a
session asks who is calling. Deleting one does not -- `delete_session` is the
operator's, and the checks it made over HTTP went with the surface.

**A turn checks who is calling before it touches the session.** The entry
reversing `access.yaml` lists "the per-turn re-check of a session's pinned agent"
among what survived, and nothing performed it. A session's first turn asked
whether the caller could open the agent it named; every later turn returned the
pinned agent without asking, so a caller holding another's session id ran in it
with everything that agent grants, while reading the same session answered that
it did not exist. The test named for the case never pinned an agent, and passed on
the first-turn check.

It is the first thing a turn does, before the session is marked used, claimed or
written to. Refused at the agent -- the obvious place, and one line -- the
caller's files were already in the session's `/data` and the session marked as
used, which was measured rather than reasoned about; the refusal of a call that
names nobody sat equally late and moved up with it. The rule is the one reading
a session already used, now written once for both, and the refusal is word for
word the one an id nobody issued gets, so holding a real id teaches nothing: not
whose the session is, not which agent it runs, not whether a turn is running in
it. *(2026-09-15.)*

**One reader of what a caller says.** `held_for` was where a list stopped being
read as nobody in particular, and two readers never asked it: naming an agent and
reading a session each tested for a tuple, so `["B"]` opened an agent restricted
to `A` and was shown a session pinned to it, and `"B"` got through both.
**One walk of the definitions, for three questions.** `audit` reports what a
policy leaves open, `undeclared_in` returns the first definition naming a source
id nobody declared, and a listing prints what each one says. All three began by
asking `stated` for the same spec, so the specs -- a directory read -- were walked
three times per listing and twice at every startup, where the comment beside it
already claimed one walk. `access.walked` yields `(kind, name, said)` and the
three questions consume it.

Shared and not merged, which is the whole of the care here: a report that stopped
at the first fault would be a refusal, and a refusal that carried on would be a
report. `Kingfisher.__init__` still calls them in order -- refusals first, because
a typo makes a line both undeclared and narrowing and reporting it as a narrowing
explains the wrong fault. *(2026-09-16.)*

`held_by` in `application/access.py` is now the only place the shape of
`source_ids` is read -- `held_for`, the turn, naming an agent, reading a session
and the listing all ask it -- and a rule holds `application/` to that, because a
second reader is how this happened. *(2026-09-15.)*

**One reader of what a definition says, and one narrowing of it.** The sibling of
the entry above, found the same way: `Stated` was built from a spec in two places
with the same pair of `getattr` calls, and the rule that narrows a definition's
audienced fields to one caller was written out six times -- three fields, in each
of the two `declares` bodies, with the delegate listing its own fields a second
time for the no-caller case. `AUDIENCED` already named those three fields and
already drove five other loops, so the enumeration was the duplicated part rather
than the rule. `domain.access.stated` and `narrowed_for` are the one of each now,
and they live beside `reaching` because a kind may not hold a helper the kinds
share.

`narrowed_for` returns the three selections and never a `Capabilities`, which is
the part worth keeping deliberate: an agent widens `models` to everything where a
delegate leaves it unset, and `memory` is an agent's to state. Those axes carry no
audience, so they stay with the definition that has an opinion about them -- a
shared body returning the whole record would have flattened a difference no test
was watching. One is now. *(2026-09-16.)*

**`errors.STATUS` stayed exactly the caller-facing set.** A deployment error that
still deserved a name went in `DEPLOYMENT_STATUS` beside it, disjoint and tested
as such -- the first table's value was that it was checkable in both directions,
and an entry a caller cannot cause would be a status nobody decided on. Both tables
were the HTTP service's, and went with it.

**Reversed: `groups.yaml.example`, shipped in the package and placed by
`ensure_layout`.** The reasoning was that `seed` names `source_ids.yaml` in a skip
message, and that message named a file no example of existed anywhere an
installed deployment could reach. True, and the example still did not help --
which only became visible by running it. An example ships *one* vocabulary and a
workspace needs whichever names its own definitions ask for, so seeding this
repository's set said `declare analysts, auditors, senior-analysts` and put a
file beside it declaring `readers, writers, staff, senior, senior-writers`. Five
names, none of them the three. Following the pointer led away from the answer.

So the remedy travels in the message instead, where it can be built from the
names actually missing -- one line, after the list, holding the union across
every skipped definition, because a copy per definition prints overlapping
partial lists and whoever pastes the first is skipped again on the second. The
flat form only: what a name covers and what it requires are a deployment's
choices about its own organisation, and nothing can infer which a name wants.

`assets_examples/source_ids.yaml` is now the only source ids example, and it is named
the thing the message points at rather than a `.example` beside it.

**`models.yaml.example` stays, and the asymmetry is the point.** The two were
never alike: `models.yaml` is required with no fallback, its example is a
hundred lines of annotation about endpoints and keys, and nothing else anywhere
carries that -- where deleting the source ids example leaves
`assets_examples/source_ids.yaml` standing. Its error message already prints a
minimal working catalogue inline, so the getting-started path exists and the
file is the reference beside it.

Still not seeded, which is unchanged. `source_ids` is not a definition kind, and
copying a policy would make adopting access control something a deployment
inherits rather than does. *(2026-09-02, reversed 2026-09-04.)*

**"Beside it" means beside the file, not inside the workspace.** Both files
relocate -- `KINGFISHER_MODELS_FILE` points a fleet at one reviewed catalogue,
which is what `compose.yaml` ships -- and the layout wrote each example into the
workspace regardless. So the deployment most likely to need the annotated
catalogue was the one guaranteed not to get it: `kingfisher seed` wrote it into
the workspace, the container read `/config/models.yaml`, and the error for the
missing file said seed would write the example next to it. It had, next to the
other one, and nothing anywhere said so.

`WorkspacePaths` carries the two overrides now and `authored_files_for` resolves
them, which is `definition_roots_for` again and for the same reason: seeding
answers "where does this go?" before a catalogue can be read, and a second copy
of `models_file or workspace / "models.yaml"` is how the two records drift.
`ensure_layout` takes the resolved mapping, so a caller holding only a directory
still gets the old behaviour -- that caller is saying the files are read from the
workspace, which for nearly every deployment is true.

Best-effort, and that is a deliberate second choice. A shared catalogue is often
mounted read-only, and a layout that raised there would take `kingfisher seed`
down for exactly the deployment this fixes; the example falls back to the
workspace, which is where it went before it could follow the file at all.
*(2026-09-03.)*

**`groups` is `source_ids`.** The concept did not move: an audience, resolved by
`reaches` against a closed vocabulary, with `contains`, `all_of`, the ceiling
rule and default-open all untouched. What changed is the word, and it changed
everywhere it appeared -- the definitions' key, `source_ids.yaml` and the section
inside it, `KINGFISHER_SOURCE_IDS_FILE`, `run(source_ids=)`, `SourceIds`,
`Stated.source_ids`, the `--as` metavar, both `list --json` keys,
`create_app(source_ids_from=)`, and the header this service's own examples spell
`X-Kf-Source-Ids`. Renaming the definitions' key alone was the first shape of it
and the wrong one: it leaves a reader two words for one thing and an Access
chapter obliged to teach both.

**The prose noun is "source id", never bare "source".** That word is taken here
-- `definitions_source`, `access_source`, `skill_sources`, `models.source`, and
197 bare uses -- and it means *where a definition was read from*. A message
reading `source_ids.yaml: source 'sales_db' contains ...` would put both meanings
in one sentence with a file path as the prefix. `formats.md` says the same thing
once, positively, in the Access chapter: a source id is a name in the vocabulary
and nothing else, not a path a definition came from and not a data file a run is
handed.

**The shipped example's vocabulary moved with the key.** `analysts`, `auditors`,
`reviewers`, `senior` and `senior-analysts` are now `sales_db`, `audit_log`,
`warehouse`, `pii` and `sales_db_pii`. A key called `source_ids` whose worked
example lists job titles teaches the wrong reading of the line it is there to
explain, and `assets_examples/` is built and run by the suite rather than
eyeballed, so the sentences around those names had to move too.

No compatibility window, on the precedent the export table set at 0.1.0. Most of
the old spelling fails loudly -- `run(groups=)` is a `TypeError`, a definition
still writing `groups:` is refused as an unknown field. One does not, and it is
worth stating rather than discovering: a workspace whose vocabulary file is still
named `groups.yaml` finds no file, and no file has always meant *no policy*, so
it comes up reachable by everyone with nothing red. That is the documented
meaning of an absent vocabulary, and it was accepted here rather than guarded.

Entries above still say `for_groups` and `groups.yaml.example`. Those name things
that were deleted, and `for_source_ids` describes a function that never existed.
*(2026-09-15.)*

**One place asks who is calling.** A call that named nobody where a policy is in
force was refused at two of the three doors that take `source_ids=`. Reading one
session took the argument, ignored its absence and answered -- because `held_by`
returns `None` for three different things -- no vocabulary, nobody named,
`UNSCOPED` -- and every reach check reads `None` as reaching everything. Right for
the first and the third, wrong for the second: a deployment with a policy was
asked something on a caller's behalf and nobody said whose behalf, and the answer
named the agent someone else's session is pinned to.

`caller_holds` is the refusal and the lookup together, and the three doors --
`_effective_grants`, `agent_named` and `Sessions.session` -- go through it. The
refusal was written out at two of them and forgotten at the third, which is what
one function removes rather than a third copy of the sentence.

The two housekeeping calls take no caller and stay that way: `sessions()` lists
the workspace and `delete_session` removes one, both the operator's, so there is
nothing to forget. `kingfisher reap` passes `source_ids=UNSCOPED` where it reads a
session, which is what a caller who means no caller writes. `kingfisher list`
stays exempt for the reason recorded beside it -- read-only, run by whoever has
the policy file in front of them. *(2026-09-18.)*

## Models and endpoints

**Endpoints and models are separate concepts in one file.** `models.yaml` holds
both; a model names an endpoint. Model parameters live there and a definition
names a model and nothing more. An endpoint whose `key_env` is unset is dropped as
the catalogue loads, with a warning; `Models` keeps the models that named it, and
`doctor` warns about those rather than staying silent -- silence made a typo in
`key_env` look identical to a shared catalogue naming an endpoint this machine
cannot reach. An endpoint no model names is dropped with the loader's warning alone.
*(2026-08-16, `model-catalogue.md`; 2026-08-18, `what-the-catalogue-dropped.md`.)*

**The shipped definitions name no models.** A vendor's model id is portable
nowhere, and a file shipped inside a wheel cannot name one.
*(2026-08-16, `model-catalogue.md`.)*

**A wire format is named after what it speaks, and an unbuildable one is
refused as the file loads.** The Responses-API row was called `openai`, which
promised the thing every gateway speaks and delivered the one almost none of
them do -- `/v1/responses`. Written the obvious way for a gateway, `api: openai`
loaded, built, and failed inside the first turn with an error from somebody
else's server. It is `openai_responses` now, and `model_catalogue.load` refuses
an `api` that names no adapter, quoting what was written and listing what can be
built.

The two halves only work together: the check alone catches typos, which were
already cheap to find, and the rename alone is a breaking change with nothing
enforcing it. Together they turn the one dangerous line in this file into a
refusal at startup that names its own fix.

Refused rather than dropped, unlike an endpoint whose `key_env` is unset, and
checked *before* that drop. A missing key is a fact about one machine, which is
why a shared catalogue survives it; an unbuildable `api` is a fact about the
file, and checking it second would make the same catalogue load here and fail on
the machine that holds the key.

**No Chat-Completions row was added**, though it is the wire format those
gateways actually speak. Nothing needs one: MiniMax and every gateway worth
pointing at publish an Anthropic-compatible endpoint, which is the recommended
path and where the example sends them. Adding a row means an adapter, a
`LANDING_SITES` entry and a release, and the table is built to take one the day
something measures the need. *(2026-09-04.)*

**`doctor` answers "why will this not start?" and nothing else.** It never makes a
model call: no probe, and it points at the caller's own task as the end-to-end
test. *(2026-08-18, `what-the-catalogue-dropped.md`; 2026-08-17,
`a-command-worth-shipping.md`.)*

## What a tool returns

**A workspace tool's return type is langchain's rule, and kingfisher adds none.**
The loader checks that `TOOLS` holds tools and stops; nothing reads a return
annotation, and `WorkspaceToolErrors` passes the value through untouched because
it catches exceptions and nothing else. A dict therefore reaches the model as
`json.dumps`, and a `ToolMessage` or a langgraph `Command` is not wrapped at all
-- the graph applies it.

**Refusing the `Command` was considered and rejected**, which is the half worth
recording, because it will be proposed again. The wrapper that would do it
already exists and the check is five lines. What stops it is what the loader's
other refusals have in common: each catches a *near miss* that produces a
successful wrong answer -- `TOOLS = [Shout]` for `[Shout()]` is one character,
loads, and answers with the repr of a new instance. Returning a `Command` takes
an import of `langgraph.types` and means it. A rule there would make kingfisher a
second name for someone else's contract, which is the thing `guides/tools.md`
refuses to be in its opening lines.

Documented instead, with the cost stated where an author reads it: a `Command`
that writes its own message leaves its streamed `tool_result` event naming no tool,
and a `files`
update reaches nothing, because this harness puts the file tools on a real
filesystem rather than in graph state. `findings.md` has the measurement and
`tests/unit/test_tool_returns.py` pins the behaviour, so a langchain change lands
there rather than at a deployment's first tool call. *(2026-09-04.)*

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
already enforces on shipped tools; a tool calling it `input_file` is missed, and the
virtual path the model writes fails visibly on the first call.

**Corrected: an argument with another name could reach outside the session.** This
entry said it could not, and a host path showed otherwise: a workspace tool taking
`input_file`, handed the real path of another session's file, returned
`TENANT-A-PRIVATE`. Nothing translates such an argument, so it reached the tool as
written, and a tool is Python in kingfisher's own process with nothing fencing it.
Every other string argument of a workspace tool call is now refused when it names a
host path -- the roots the file tools refuse, `/root/`, `/proc/`, `/sys/` and `/dev/`,
or the directory this session's neighbours are in, which is what catches
`/workspace/sessions/...` in a container where no host root would. Defence rather
than a boundary: a relative path, or text a tool turns into a path, still reaches
it. `tools.md` now says that only `path` is translated, which it had never told
anyone writing a tool. *(2026-09-17.)*

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

## Confining the shell

**The two fences share a toolchain and not a workspace**, and the asymmetry is
the design rather than an oversight. `sandbox-exec` is `(allow default)` with
denies: it takes away the operator's home and hands back what has to stay
reachable inside it, so `readable_roots` names the workspace. Landlock and
bubblewrap are allow-lists: they take away everything and hand back one session,
so naming the workspace there would return every *other* tenant's session, which
is the exact read the fence was built after -- measured before it existed, tenant
B ran `cat ../<A>/derived/secret.txt` and got `TENANT-A-PRIVATE` with exit 0.

So `toolchain_roots` is the shared half and the workspace is not. It is worth
recording because the tidy-looking move is to have `_fence_for` call
`readable_roots` -- one function, both platforms, three lines shorter -- and that
change would close a bug and silently reopen a hole, which is the shape of thing
this file exists to stop.

**What went wrong without it:** the Linux side named only the catalogue, so the
venv `shell_env` puts first on the agent's `PATH` was granted by nothing. An
allow-list does not refuse an ungranted `PATH` entry, it skips it -- the shell
walked on to `/usr/local/bin/python3` and ran a different interpreter, without
the libraries this project installs for the agent and unable to read the venv's
`site-packages` besides. Invisible on macOS, where the profile is default-allow
and `readable_roots` had granted the same roots since denying the home broke
Python there.

The guard is stated as the property rather than as the fix:
`test_every_directory_on_the_agent_s_path_is_reachable` asserts every existing
entry of `shell_env`'s `PATH` falls inside something the fence grants, so a path
added later by another route is caught by having been added. *(2026-09-04.)*

**A definition is not the agent's to edit.** Every directory in
`catalogue_roots` is denied to the confined shell, not only `skills/`. It used to
be only `skills/` -- one call site, one value -- on the reasoning that a skill is
prompt text the agent follows. The premise was always broader than the rule.

`writable_roots` returns the whole workspace, so `tools/` was writable by the
shell, and `LocalToolRepository` *executes* its modules to read them. The catalogue
is read each time a `Kingfisher` is constructed -- once per process, and once per
`kingfisher run` -- so a `.py` file the shell wrote was imported and run, in this
process and outside this profile, the next time one was. Measured rather than reasoned
about: a write to `skills/` was denied and a write to `tools/` succeeded under the
profile a deployment actually builds, and a fresh repository ran the module-level
code of a file that had not been there.

The other three roots decided rather than executed when this was written, and are
denied for the same reason one step along. An agent that edits its own
`agents/*.yaml` strikes out the `source_ids:` line saying who may reach it, and that
line is read when the catalogue loads -- the same next construction. `subagents/`
and `middlewares/` have since come to hold Python too, which is one more reason
rather than a different one.

Half of this was already recorded under *Wiring a store*, which quotes the same
`writable_roots` sentence to argue that a store must be named by an environment
variable because *"a file the agent could edit is not a boundary"*. That entry
reasoned about integrity; the same premise carries execution, and it even named
the neighbour -- *"it is the middleware decision again, one object further in."*

**Nothing relied on the hole**, checked four ways before closing it, because a
capability somebody uses is a different argument from a side effect nobody asked
for. A caller could not add tools: uploads layered skills and subagents and there
was no `LayeredTools`. The agent's file tools cannot write to a catalogue root --
the backend is rooted at the session, and the routes that do address one, the
skills catalogue and each bundle's skills, are read-only -- so the shell was the only
path. The prompt never mentions `tools/`. And `guides/tools.md` is written
throughout to a person authoring before a run.

**Writes, not reads**, which is what `skills/` already did: an agent reading the
skill it was told to follow is ordinary, and `execute` runs scripts the catalogue
ships. The cheap way to pass the write test is to deny the directory outright, so
the read control is parametrised beside it.

**An absent root is still named.** The profile is written when the confinement
resolves, which is each time a backend is built, and `kingfisher seed` can run
between one build and the next, so filtering directories that do not exist would
leave a workspace seeded in between with a protection nobody removed and nothing
applied.

**Stated as the property, not the four names.**
`test_every_definition_root_is_protected` walks `catalogue_roots` rather than a
list, so a fifth kind is covered the day it exists rather than the day somebody
remembers. That test is what makes *Middleware as a definition kind* buildable at
all.

**`models.yaml` and `source_ids.yaml` are deliberately not included.** Both are
workspace files and both are writable, but `config_from_env` runs once when
`Kingfisher` is constructed, so an edit lands at the next restart rather than the
next request. A different shape, and its own argument about who writes
`source_ids.yaml` and when. *(2026-09-07.)*

**Reversed: they are protected too.** The shape was not different -- the definitions
are read at construction as well, so an edit to any of them lands at the same next
start -- and the argument about who writes `source_ids.yaml` comes out one way: not
the agent, since it is who may reach what, and a shell that could edit it could grant
itself anything from the next start on. They are denied by exact `path` rather than
`subpath`, because they sit in a workspace that has to stay writable, and by where the
configuration reads them from, so a relocated file is covered and one that does not
exist yet cannot be created. Only the macOS profile needed it: Landlock and bubblewrap
grant writes inside the session and nowhere else, and `off` and `external` apply
nothing, as before. *(2026-09-17.)*

**The profile was not the agent's to edit either, and was.** The rule above
applied one object further in than anybody had looked: `shell.sb` sat inside the
region its own rules declared writable. `state_dir` defaulted to
`<workspace>/.kingfisher`, `writable_roots` returned the whole workspace, and
`protected_roots` named only the definition roots -- so two commands in one turn
were enough. Write `(allow default)` over the profile; run under it.
`sandbox-exec -f` re-reads the file for every command while `resolve` rewrites it
only per backend build.

Measured through `confinement.profile` at the path `resolve` writes: the second
command read the operator's home and created `skills/PWNED.md`, both refused by
the profile a moment earlier. macOS `auto` only -- bwrap reads no profile, and
`external` and `off` have nothing to defeat.

The entry under *Wiring a store* had already stated the rule and attributed it to
`confinement.resolve` -- *"host-side configuration, and a file the agent could
edit is not a boundary"*. **That attribution was stale**: `f99d2ce` trimmed the
sentence out of the docstring, so this page cited a docstring that no longer said
it, about a state directory that *was* the workspace by default. The rule
survived in prose and had never been true in the code.

`profile` now takes the path it will be written to and denies writes to it, by
`path` rather than `subpath`, after every other rule but the one refusing each
session's `.harness`. Required rather than defaulted: a caller who
forgets it gets no boundary, which is the failure being fixed. Four ways are
covered rather than the obvious one -- overwrite, append, unlink and rename-over
are all `file-write*` against that name. The profile is also replaced rather than
truncated in place, so a turn cannot bind itself to half a profile while another
rewrites it; the content cannot differ between turns, every input coming from one
`Config`, so that is a torn read rather than a disagreement.

`.kingfisher` is protected as a directory as well, which is what the single-path
rule became once `TMPDIR` moved out of it. It could not have been a `subpath`
deny before: the denies are written after the allows and would have covered the
one directory in there the shell had to be able to write.

**A session's `.harness` is denied to the shell too, by a pattern.** One static
profile covers every session -- `(deny file-write* (regex #"^…/sessions/[^/]+/\.harness…"))`
-- because `shell.sb` has one fixed path, so a profile naming the sessions that
existed when it was written would have to be rewritten as sessions arrive, and
two concurrent turns would then race to write different bytes to the file each is
bound by. On Linux, bubblewrap binds the directory read-only over the session it
has already mounted. *(2026-09-08.)*

**Landlock cannot take back what it has granted, and the Linux job is what
established that.** The first version of this granted the session writable and
`<session>/.harness` readable, on the reading that the kernel resolves a path by
its most nested matching rule. It does not. A Landlock rule only ever grants, and
a write walks up from the file until one of them answers -- so the read-only rule
was stepped over and the session's writable rule answered in its place. On the
first run of `tests/linux/test_fence_escapes.py` the fenced shell overwrote the
agent definition its own session was pinned to.

`policy_for` grants a session's directories one at a time instead, and never the
session, which leaves that walk nothing to find. The price is that the shell
cannot write into the directory it starts in on a Landlock host, where it can
under bubblewrap and on macOS -- both of which can express a carve-out and both of
which still do. `data`, `derived`, `memory`, `runs`, `.home` and `.tmp` stay
writable -- and `skills/uploaded` did, until uploads went -- so what stops working is
scratch dropped straight
into the session; the alternative was leaving the pinned agent, the conversation,
the turn lock and the run log writable by the shell they belong to. *(2026-09-08.)*

**The two Linux fences share one front door.** They differ in the middle -- Landlock
confines between `fork` and `exec`, bubblewrap prepends a sandbox to the argv -- and
were identical on both sides of it. `sandbox/linux.py` holds what is neither
mechanism's: `present`, which drops a path the host does not have, and `outcome`,
which is the three ways a fenced command comes back. The launch stays in each runner,
as a callable, because that is the only half worth writing twice.

`MAX_OUTPUT_BYTES` moved with them. It had lived in `fence.py` with `bubblewrap.py`
importing it from there, which reads as bubblewrap depending on Landlock for a number
that is really `LocalShellBackend`'s. Each fence keeps its own path list: bubblewrap
binds no `/proc` and builds a fresh `/dev`, and one list serving both is how a
mechanism gains a path the other meant to deny.

The duplication was hiding a gap rather than only costing lines. `test_fence.py`
stubs `sandlock` so the Landlock runner's shaping, timeout and failure paths run on
any host; `test_bubblewrap.py` drove `argv_for` and one thing about the runner. So
the copy nobody tested was the one on the kernels Landlock cannot reach. Four
mutations that used to fail one test each now fail two.

**A mechanism is named, not spelled out.** `LANDLOCK`, `BUBBLEWRAP` and
`SANDBOX_EXEC` are a vocabulary of their own, and `MODES` is a different one that
overlaps it on a single word: a mode is what a deployment asks for, a mechanism is
what ended up confining the command, and `auto` is never a mechanism. Three files
outside `confinement` branched on the spellings -- `_fence_for` twice, `doctor` once
-- so a mechanism renamed in one place would have gone on reading as *not a fence* at
each of them, which is a shell running unfenced beside a `Confinement` reporting one.
`LINUX_FENCES` is the pair `_fence_for` builds a runner for, and a fourth mechanism
has to say which half it is in. *(2026-09-20.)*

## Sessions: what persists and where

These began as decisions in *Nothing at rest on this machine* and were built
without the rest of it, which is what that document's N2 asked for -- the parts
that need no memory-backed filesystem, first and separately.

**`/runs` and `.tmp` became one `/scratch`, and a turn stopped being a place.**
A session held two scratch directories with one purpose. `.tmp` was the shell's
`TMPDIR` and the agent was never told about it; `runs/<turn>` was named in every
task message and reached by the file tools. Neither was returned to a caller, and
only one of them was ever swept -- a turn's directory stayed for the life of the
session, counted against its quota, holding files whose own docstring said they
"arrive fresh each round and leave with the turn". Nothing implemented that.

**The folders were also the turn counter**, which is the part that made this more
than a tidy-up. `allocate_turn` listed `runs/`, took the number after the highest
and claimed the next with `create_exclusive`. So a session restored on another host
-- where nothing restores `runs/`, because only artifacts, the transcript and the
agent snapshot are kept -- began again at `t001`. Asked who reads the sequence, the
answer was nobody: the id reaches a printed line, the run log and `RunResult`, and
none of them compares two. It is generated now, a caller's own id still wins, and
what that no longer buys is de-duplication on a retry -- which was never the id, it
was `ensure` on the same directory.

**A request's files go to `/data` with the session's.** `Request.inputs` and
`Request.data` were two fields for one operation once the turn had nowhere of its
own; `data` survived, because it names where they land and `place_data` is the
half that re-hardens the directory and reports what it replaced. Two turns sending
one name no longer keep both -- the second replaces it, visibly, which is the cost
of the merge and is stated here rather than discovered.

**What a reader should expect and not find:** `RunResult.virtual_dir`, which named
a directory that no longer exists, and `run_dir`, which is `session_dir` now
because that is what it points at. `kingfisher run --input` went with the field.

**The three lifetimes are told apart by destination now**, which is what the split
had been doing by convention: `/data` is the caller's and read-only, `/scratch` is
the agent's and returned to nobody, `/derived` and `/memory` are what the caller
gets back. The dot came off `.tmp` because its only reason was that the agent never
named it -- `SESSION_DIRS` is "the names the agent addresses", and it is one now.

The measured warning about path spellings survives unchanged and is worth keeping
in view: told only the virtual form, the agent passed it to `execute` 4 times in 10,
each failing and costing about three times the whole task. `/scratch` and `scratch`
correspond the way every other route does, which is why the directory was renamed
rather than given a prettier virtual name. *(2026-09-21.)*

**A session's history is kingfisher's own records, not a framework's.**
`domain/transcript.py` holds it, and it keeps what the agent *did* as well as
what it said -- tool calls and results, not only human and assistant text, since
an agent that cannot see what it already did will do it again. Portable on
purpose: the next turn may not be run by this harness.

**The checkpointer is in-memory, and the transcript is what survives.** So a
turn's *working* state does not cross a turn boundary even though its
conversation does -- an agent resuming a session does not find a half-finished
`TodoListMiddleware` checklist it has no memory of writing, for a task the caller
may have dropped. That is structural rather than enforced, so it is asserted:
a deployment injecting a persistent `threads` factory takes it back.

That is a change in what a checkpointer is *for* here rather than a cheaper way
to do the same job: a checkpoint preserves resumable graph state, and kingfisher never
resumes a graph -- no `checkpoint_id`, no `interrupt()` anywhere. What is left
for a saver is one turn's supersteps.

**Narrowed once, for approval gates.** kingfisher resumes a graph now, in exactly
one case: a turn that stopped at an `interrupt_on` gate writes what its saver
holds into `.harness/paused.state`, and a `Resume` reads it back and continues
that graph. Everything above still holds for every other turn -- nothing is kept
unless a turn stopped to ask, and it is dropped the moment one is answered or
superseded, so an ordinary turn still starts with empty channels and no agent
resumes into a checklist it does not remember writing.

What that file is, is a consequence of where it lives. `.harness` is refused to
the file tools, but `execute` bypasses those entirely and what refuses the shell
is `confinement._harness_denial` -- a macOS profile, on one platform, that a
deployment can switch off. So the checkpoint is msgpack through langgraph's own
serialiser, which neither writes nor reads a pickle. That is also why it is not
`InMemorySaver(factory=PersistentDict)`, the persistence seam langgraph ships
for this and the obvious thing to reach for: it is unconditionally pickle, and a
tampered pickle read back is arbitrary code at resume time where a tampered
msgpack is only bad graph state.

The pending writes go with the checkpoint, which is the half worth writing down.
A turn can stop on one gated call while a sibling in the same superstep has
already run; drop those writes and the sibling runs *again* on resume, which for
an approval gate means a tool firing twice for one decision. Both paths end in
identical state, so nothing but a side effect can tell them apart. *(2026-09-22.)*

The three things the old per-session sqlite bought all survive by another route,
which was measured rather than assumed. A conversation deleted with its directory
(one workspace held 132 orphaned threads after every session had been reaped), a
conversation the quota can see, and no cross-session contention (at 32 concurrent
writers the slowest went from 363ms on a shared file to 80ms on its own). The
transcript is a file in the session, so the first two hold; nothing is shared, so
the third has nothing to contend for. What is genuinely gone is ~20KB of empty
database per session, which was the cost rather than the benefit.

**Sqlite is gone entirely, and so are the two dependencies that carried it.**
`langgraph-checkpoint-sqlite` and `aiosqlite` were what N13 asked to drop, and
the transcript work shipped without them because two exported builders still
needed them: `build_checkpointer` and `async_checkpointer`, one database per
workspace, for a deployment that wanted one shared file on purpose.

The reason they were public expired. `async_checkpointer` was exported because
`astream` refused to run without an async saver -- `SqliteSaver` raises on
`aget_tuple` -- and the server, the first consumer outside the package, opened
one for the life of the process. `InMemorySaver` implements both halves, so the
server stopped opening one, and what was left was two builders whose only
callers in this repository were their own tests, plus the shape the library had
deliberately moved away from: one database shared by every session.

Removed rather than kept for symmetry. A deployment that wants durable graph
state passes `threads=` its own saver, which is three lines it controls against
a hundred kingfisher carries and two dependencies every install pays for. That
it is a breaking change to a public surface is why it is written down here, and
`0.1.0` is when such a change costs least. *(2026-09-04. The service stopped
opening one first, separately, so the removal landed on a tree where nothing
used it.)*

**`doctor` checks whether a memory-backed workspace can be filled safely.**
`workspace/backing.py` reads the filesystem type, its size, the cgroup limit
and
whether swap is permitted; `presentation/cli/health.py` reports on them. The
danger it names is specific: a memory filesystem *larger* than the container's
limit does not fail when it fills -- the kernel swaps its pages out, which is
data at rest, arrived at silently, with the write succeeding and no error
anywhere. Only a filesystem smaller than the limit gives a clean `ENOSPC`.

**The run log never crosses the wire.** `run_dir` and `log_path` are typed `Path`
in the domain so `json.dumps` raises rather than quietly stringifying them, and
`service/payloads.py` was the one place that knew to leave them behind. A
mirrored pydantic model would have been a second home for that rule, and the kind
that gets it wrong helpfully -- adding a `Path` serialiser makes the error go away
and ships exactly the leak. The service and its payloads went on 2026-09-15; the
fields are still `Path`, so a caller serialising a result meets the same refusal.

**The session quota is checked between turns and never during one.** This reverses
what *Nothing at rest* argued: N11 said the bound could be metered on the
tool-call hook because kingfisher already wraps every tool call. It cannot.
`execute` writes without any file tool seeing it, so a turn already running can
exceed the bound and only a filesystem quota underneath could stop it. What the
check prevents is the next turn making it worse.
**A session's files reach a store the deployment wired, and its directory is a
port too.** `SessionStore` -- `fetch`, `save`, `knows`, `forget` -- is the missing
half of a symmetry this package then had: bytes arrived through a door
(`FileStore`, `DefinitionStore`, both since removed) and left through a host path,
which only ever worked for a caller sharing that host. `SessionRoot` is the other half, handing
back the one directory a turn addresses everything from, as a context manager so
that a mount is released when the turn ends however it ended. `LocalSessionRoot`
is the default root; there is no store unless one is named, and
`KINGFISHER_SESSION_STORE` naming a directory gets a `LocalSessionStore`. **A local
directory is a perfectly good implementation of either** -- what the constraint forbids is kingfisher
*assuming* a disk, not a deployment choosing one.

**A session owns what it costs: everything per-session lives in the session.**
Four things did not. The agent a session opened with, its conversation, the lock
a turn holds and its run log sat under `state_dir`, one directory per kind keyed
by session id, and the shell's `TMPDIR` was one directory shared by the whole
workspace. Two properties this page already states about single files -- deleted
with the session, counted by `session_bytes` -- were true of none of them.

Nothing swept them. `delete_session` removes the directory, the thread and the
store's copy; `reap` sweeps expired sessions; neither ever touched
`<state_dir>/agents/<id>.yaml` or `<state_dir>/runs/<id>.jsonl`, so a workspace
kept one of each per session that had ever existed. The same shape as the 132
orphaned threads above, arrived at by a different route.

And shared scratch was a channel between tenants: both fences granted it
writable, so what one caller's agent derived sat where another caller's agent
could read it. `prepare_scratch`'s `0700` check was written about other Unix
users on the host and had nothing to say about other sessions.

**Uniform rather than by size**, which is the decision worth recording. Moving
the large things and leaving the small ones -- the pin is a kilobyte, a claim is
an empty directory -- buys the same two properties for a smaller diff, and is
where the reasoning would have gone to hide: the pin is small, and it is also the
file that decides which endpoint a session's prompts reach and whose credentials
pay. A layout whose rule is "unless it is small" cannot be checked by reading it.

**Two enforcement points, because one is bypassable.** `.harness` carries read
*and* write denies for the file tools and a rule in the sandbox for the shell.
The read deny is what makes it invisible rather than visibly forbidden: a deny
filters entries out of `ls`, `glob` and `grep` instead of erroring, so the model
never sees a directory it would then try to open.

**The pin crosses a machine now, which closed a hole rather than tidying one.**
It lived where `SessionStore` never saw it, so a session resumed on another host
found none, re-pinned from *that* host's catalogue, and accepted whatever agent
the request named -- *"a session is fixed to the agent it opened with"* held on
one machine and quietly failed across two. `keep_from` carries it beside the
transcript. The claim and the run log stay behind: a restored claim would make
the session look busy for `claim_stale_after` -- minutes -- and the log is
diagnostics that would be re-uploaded whole every turn.

**The claim moving in deleted `_discard_dead_claims` outright.** It existed
because a claim could outlive the session it named; one inside that session
cannot. `busy` is a stat per session rather than a listing of a shared directory,
which is the cost. `domain.session.claim` takes the slot's path rather than the
root every slot sat in, because where inside a session is a layout question and
the domain does not import `layout` -- the reason `layout.py` left `domain/`.

**No migration and no fallback reader; the marker carries a layout version.**
The failure a fallback would paper over is silence rather than breakage: a pin
the new code cannot find means a session re-pins and may change agent
mid-conversation, and a transcript read from the new path means the conversation
comes back empty. A fallback also has no forcing function to be removed -- the
day the old paths are gone, nothing says so. `.kingfisher/WORKSPACE` had always
held one line nothing ever read, so the version cost nothing and the next layout
change inherits the check.

**`KINGFISHER_STATE_DIR` and `KINGFISHER_SCRATCH_DIR` are gone.** Scratch first:
per-session and relocatable are not both expressible, because `session_bytes`
counts one directory and anywhere else is a cost the quota cannot see. Then
state, which was left holding one generated file. Both were documented and
neither was set anywhere -- `.env.example` had them commented out. *(2026-09-08,
in four slices. `docs/design/2026-09-08-a-session-owns-what-it-costs.md` argued
it and is removed.)*

*Not set anywhere, but still read in one place.* The one generated file state held
was the smoke report the integration driver copied there, and the driver went on
reading `cfg.state_dir` after the field was gone -- on the last lines of a smoke
run, which only a live model call reaches, in a tree where `ty` ignores unresolved
attributes under `tests/**`. So every smoke run finished its turn and then raised.
The copy was dropped rather than moved: nothing read it, the run already prints
where the report is, and the pass/fail signal was always `check_result`.
`test_a_smoke_run_reaches_its_end` drives those lines without a model.
*(2026-09-16.)*

The override was then narrowed to test modules. None of the 55 findings it hid
were outside one, so the driver, `conftest.py` and the helper scripts under
`tests/` lost nothing by being checked, and with the narrower glob `ty` reports
this bug directly. `evals/`, which the driver imports, turned out to be outside
both `ty` and CI's `ruff` and is in both now. Two rules keep it so:
`test_ty_exempts_test_modules_and_nothing_else_under_tests` and
`test_every_directory_holding_python_is_type_checked`. *(2026-09-16.)*

**Containerise and use a sized tmpfs; do not adopt mirage for the filesystem.**
That was *Nothing at rest*'s closing recommendation and it is what shipped. A
tmpfs inside a container gives memory-backed files that are *real paths* -- any
program opens them, any package reads them -- with a size limit the kernel
enforces, and no library, no driver and no version risk. Mirage was measured
rather than argued about, and `findings.md` carries what it does. What it would
still be good for is the question that exploration opened with and set aside: S3,
Drive and Postgres mounted as paths, which kingfisher has no story for and which
is a far smaller change than replacing the filesystem.
*(N11, N13 to N16, N20 and N22 built through 2026-09-01; the store and the session
root on 2026-08-26, `A session that survives the machine it ran on`. All from
`nothing-at-rest-on-this-machine.md`, removed 2026-09-04.)*

**Deleting a session reaches the store whether or not this workspace holds a
directory for it.** `delete_session` returned as soon as it found no directory
under `<workspace>/sessions/`, which under a root of the deployment's own is every
session -- so the store kept its copy, `knows` still answered for the id, and a
session reported deleted could be resumed. It now forgets the store's copy either
way, and, like `reap`, not after a directory that refused to go: that directory
still needs the history behind it. An id that names nothing is still not an
error, so a retried delete need not care whether the first one landed.
*(2026-09-15.)*

**Eviction is deletion that keeps the store's copy, and only when asked.**
`delete_session` and `reap` take `forget=False`, which removes a session from
this machine and leaves it in the store, so it resumes here or on another host.
This qualifies the entry above rather than reversing it: forgetting stays the
default, because a caller who asked for a deletion and got a resumable session is
the bug that entry fixed. The thread goes either way -- the next turn is rebuilt
from the transcript, which the store carries. *(2026-09-18.)*

**`run(delete_session=True)` reports a deletion that failed, on the result.** It
called `delete_session` and threw away what came back, so a caller got the answer
and no sign the session was still there. `RunResult.deletion_failure` carries the
reason: on the result rather than logged, because the caller asked for the
deletion in the same call and the result is where it already looks for how the
turn went; and rather than raised, because the turn finished and its answer is
worth keeping. A turn stopped at a bound keeps its session on purpose and leaves
the field empty -- `stop_reason` says why. *(2026-09-15.)*

## Wiring a store

**The session directory is the backend root**, `/data` is materialised once at
session creation, writes come back as a manifest, and processes are stateless
while the service is stateful. `sweep()` came off the request path.
*(2026-08-16, `session-scoped-api.md`, `durable-session-data.md`.)* Two of those
have since changed: `/data` is placed on every turn that brings files, replacing any
of the same name, and the service is gone.

**A deployment names its store in a setting; it does not pass an object.**
`Config.session_store` could only be a directory and its comment said why -- *"a
deployment reaching for that passes an object rather than a path"* -- and both
halves were wrong. An environment variable can name a factory, which is how
`models.yaml` has always reached a chat class; and passing an object only reaches
the one construction site a deployment controls, which is none of the ones
kingfisher ships. `presentation/cli/__main__.py` builds its own `Kingfisher` and
there is nowhere to point it. A setting resolved inside `Kingfisher.__init__` is
inherited by every entry point at once.

**A zero-argument factory, not a class and not an instance.** `module:name`, the
same string `Adapter.chat_class` uses. Kingfisher does not know whether a store
wants a bucket, a DSN or a pool, so it asks for none of them and the factory
reads its own configuration; inventing a URL grammar for stores it knows nothing
about is the version that ages worst, and a ready-made instance moves
construction to import time, where "cannot reach the bucket" arrives as an
`ImportError` from a module nobody was reading.

**What is checked is the name, not the building.** A spec that will not parse, a
module that will not import, an attribute that is not there, a result of the
wrong shape: `ConfigError`, naming the setting. A factory raising its *own*
exception passes through untouched -- that is the deployment's code failing at
the deployment's job, its type may be one their handling knows, and `store_named`
is already on the traceback saying which setting reached it.

**Naming a store twice is refused at startup, not resolved by precedence**, and
on the record rather than in the reader, so a config assembled in Python obeys
the same rule. Preferring one silently leaves a deployment's sessions in the
directory it stopped meaning to use, and nothing says so until somebody goes
looking.

**Environment variables, never a workspace file.** Measured rather than assumed:
`confinement.writable_roots` returned the whole workspace plus scratch, carving
out only `skills/`, so `models.yaml`, `agents/`, `subagents/` and `tools/` were
writable by the agent's shell. The rule was quoted from `confinement.resolve` --
*"host-side configuration, and a file the agent could edit is not a boundary"* --
though *Confining the shell* found that sentence already gone. The definition
roots have been protected since 2026-09-07 and `models.yaml` since 2026-09-17, but
only under the fences kingfisher applies -- under `off` or `external` a workspace file
is whatever the deployment makes it -- so the rule still holds. This is why a store as a workspace *asset* is closed
rather than deferred: it is the middleware decision again, one object further in.
The agent cannot reach environment variables at all -- its shell gets an
allowlist of five plus the skills directory.

**A port a deployment can name gets a runnable contract.** `SESSION_STORE_CONTRACT`
in `kingfisher.testing` -- and `FILE_STORE_CONTRACT` beside it until that port went
-- are the checks
`tests/unit/` runs, exported so a deployment runs them against its own adapter. A
setting inviting somebody to write an implementation without a way to check it is
worse than no setting, and the parts easiest to get wrong are the ones that
matter: extracting the session kit found `knows()` -- the method the port calls a
security question -- with no test anywhere, and a store answering `True` for
every id passes 2,255 other tests while letting a caller resume a session they
invented.

**The kit imports no test framework**, which is what lets it live in the library
rather than a second wheel: `pip install kingfisher` gains a module and no test
dependency. Each check raises `AssertionError` with the whole story, because
pytest's assertion rewriting does not reach an imported library and `python -O`
strips a bare `assert` -- a conformance kit passing while checking nothing is
worse than no kit.

**The two kits took different arguments, because the ports differed.**
`SessionStore` writes, so its checks are handed a factory and fill their own.
`FileStore` was one method and that method read: kingfisher never wrote to a
file store, so a check could not plant the file it then fetched, and the deployment
handed over a `Planted` describing what it planted. Both went with `FileStore`; the
kits left, for the session store, the session root and the command runner, all take
a factory.

**`FileStore`'s setting was the service's, `SessionStore`'s is the library's.** Both
the port and its setting are gone (*The HTTP service*); the asymmetry was kept
deliberately while they existed: a `FileStore` resolves *refs*, the vocabulary of a
caller with no host paths, and `kingfisher run` takes `--input` as a path on this
machine and neither builds one nor could use one. The setting belongs where the
port is used. Flagged while proposed as the decision most likely to be wrong, and
it survived being built.

**The backend stays kingfisher's, and a store as a workspace asset is refused.**
*(The first half was reversed on 2026-09-15 -- a deployment names the filesystem its
agents run on, below. The refusal of a store as a workspace asset stands.)*
The backend is not "where files live": it wraps every shell command in
`sandbox-exec`, bubblewrap or Landlock, it is what refuses a host path, and its route
table is what makes `read_only_permissions` legal at all -- deepagents refuses
`permissions=` outright on a backend that executes unless every rule is
route-scoped. Routing `/data` to a store would also break the promise
`prompts/system.md` makes the model in a table -- *"nothing in the workspace is
out of the shell's reach"* -- leaving the agent able to read its inputs and unable
to run anything over them. Object storage reaches a session as a mount
(`SessionRoot`) or by being copied in and out, and both work today.

**What the agent addresses is a table in `kingfisher.layout`.** One entry per path,
saying whether the composite mounts it, which scope denies writes under it, and
whether its members are generated per catalogue. `infrastructure.harness.backend`
turns the entries into mounts and `infrastructure.harness.agent` turns the scopes
into deny rules. Policy here, the runtime's objects there -- the division this
module's first line already describes. *(2026-09-07.)*

**It exists because one route was three facts in two modules.** The path was a
constant in `harness/backend.py`, the mount was a dict literal inside
`default_backend`, and whether it was writable was a `FilesystemPermission` in
`harness/agent.py` whose `paths=["/data/**"]` was a string typed a second time
with nothing tying it to `DATA_ROUTE = "/data/"`. They have to agree --
`FilesystemMiddleware` refuses `permissions=` outright unless every rule is
scoped to a route -- and nothing made them agree except care, with the failure
arriving at a turn rather than at the definition.

**The route constants were a second spelling of names this file already owned.**
`SESSION_DIRS` held `"data"` and `"memory"`; `backend.py` held `"/data/"` and
`"/memory/"`; `ARTIFACT_DIRS` spelled two of them a third time. The leaves are
named once now and everything is composed from them, which is what this module
already recorded doing for `UPLOADED_SKILLS`: *"a second spelling is how the two
halves of this layout drifted apart in the first place."* That constant went with
uploads, and the quotation with it.

**Writability hangs on a scope, not on a route.** `/skills/**` is one rule
covering the catalogue and every bundle's mount, and covered a session's uploads
too while they existed. A rule per
route would make a deployment's permission list grow with the number of bundles
its catalogue ships, protecting nothing more, and it would have stopped this
being a refactor that changes nothing observable -- which is the property that
made it safe to land.

**Agreed as "a test ties them" and built one step stronger, because a rule
refused the agreed form.** `routed_paths` would have been read by nothing but a
test, which `test_nothing_is_defined_for_tests_alone` catches -- and that would
have left `routed=False` on `/derived` as pure documentation. So `default_backend`
keys its dict off the table: a route declared with nothing to back it now raises
where it is declared. The harness still decides *what* backs each path; only the
keys moved.

**Unrouted paths are in the table.** `/derived` and `/runs` reach the default
backend, which is the shell's, and before this a reader learned that by not
finding them in a dict literal. An absence is not a record.

**Still not opened: the seam.** The entry below defers a backend factory on
`Kingfisher` and that stands -- execution is swapped through `CommandRunner` and
storage through `SessionRoot`, both of which exist and neither of which this
touches. What changed is that "routes only, never the default slot" is now a
thing someone could implement without first untangling three files.
*(Opened on 2026-09-14; see below.)*

**Considered and rejected with it:** a backend factory on `Kingfisher` -- deferred
then, opened on 2026-09-14 for a reason this had not considered, and not on the
terms reserved here; kingfisher shipping
an S3 store behind a closed table like `ADAPTERS`, which would put this package in
the business of owning every backing store anyone asks for; and a builder
parameter on `create_app`, designed and dropped once the resolution point moved
inside `Kingfisher.__init__` and left it nothing to do.
*(2026-09-04 to 2026-09-05, `a-store-a-deployment-can-name.md`, built in four
slices. Its one correction is worth keeping: an argument about `create_app`
needing a checkpointer held open for the process was true when written and false
a day later, `48cd457` having made the default `InMemorySaver`. The conclusion
did not rest on it.)*

**The seam is open, and the reason is tenancy rather than capability.** What kept
it shut was that a remote runtime is already served by two ports: `CommandRunner`
ships the command elsewhere, `SessionRoot` says where the session's files are.
Both hold. What neither survives is a deployment that forbids one caller's session
from reaching another's. `SessionRoot` yields a *path*, so a remote filesystem
reaches it as a mount -- and a mount is established once, outside the process,
before any session exists. A session created at runtime cannot be given one of its
own: making mounts needs privileges, and granting them was measured to cost more
than it buys, since a container holding `CAP_SYS_ADMIN` is one command from the
agent's own shell remounting its workspace executable and nothing in the process
can take that back. So every session sits on one mount, separated by a path prefix
and nothing else. That is exactly the shared storage a per-caller route was
already declined on, for want of a tenancy argument. A deployment that forbids the
sharing *is* the argument, and it comes out the other way: `backend_from` is
called per turn with the session it is for, so each caller can be handed its own
filesystem, and the separation is in the wiring rather than in a naming
convention.

**Past "routes only, never the default slot", and that condition was the thing
wrong with the deferral.** The default slot is the shell and the filesystem for
every unrouted path, so reserving it ruled out the only deployment that needed the
seam at all. What stands in its place is the shape rather than a boundary:
`backend_from` is handed the backend kingfisher built, so a deployment wanting one
thing different returns what it was given with one thing different, and keeps
refusing host paths, keeps the route table that makes a read-only rule legal, and
keeps the confinement, by doing the least work available. Losing any of them takes
a deliberate return of something else.
*(This paragraph alone is superseded on 2026-09-15; see below. The rest of this
entry stands -- the tenancy argument, the callable, the graph refusal and the
setting left unopened are all unchanged by it.)*

**A callable, and the reason is sharper here than for a runner.** A backend is
rooted at a session directory, so one instance handed in at construction is one
filesystem for every caller -- which is the leak a deployment replacing the
backend is usually replacing it to avoid, written at a call site where nothing
looks wrong. The parameter is named for where a backend comes from rather than for
the backend so that passing an instance is not the obvious thing to try, and
refused with a type error when it is tried anyway.

**Both a pre-built graph and a function is refused at construction.** They are two
answers to what filesystem a turn runs against, and a pre-built graph already
carries one, so either would be silently discarded. Refused where the wiring is
written, which is the last moment it is cheap to say so.

*(The parameter was renamed `backend` on 2026-09-15, below, so its name no longer
does the work the paragraph before last describes; the type error for an instance
does.)*

**Not opened with it: a setting.** A deployment needing this builds `Kingfisher`
itself, and `create_app` took one ready-made, so the service reached the seam
without a name to resolve. The command line does not, and would need one --
left until something wants it, because a setting is permanent and nothing has
asked yet.
*(2026-09-14.)*

**A deployment names the filesystem its agents run on, and kingfisher no longer
picks one.** `backend_from` was optional and was handed the backend kingfisher had
already built. It is now `backend`, required, and handed the ingredients instead: a
`BackendFactory` called per turn with the config, the session, the catalogue and the
runner, which is exactly what `default_backend` takes. A deployment keeping what it
always had writes `Kingfisher(cfg, backend=default_backend)`.

**The reason is that the backend is the security boundary, and the honest version of
that is narrower than it sounds.** The default was never the lax option -- it is
`sandbox-exec` or Landlock around every command, host-path refusal, and the route
table a read-only rule is only legal against -- so requiring the parameter makes no
deployment safer by itself, and there was no way to weaken it by accident. What it
buys is that nobody can wire the whole service without learning the boundary is
there. The cost is named rather than glossed: a parameter you must type is a
parameter you notice you can change, which cuts the other way, and that was judged
worth it. Written down because the argument is tempting to overstate and a later
reader checking it against the code would find the default strict and wonder what
this entry meant.

**What the old shape was protecting, and what replaces it.** Being handed the
default meant a deployment kept the route table and the confinement by doing the
least work available. A factory starts from nothing, so the cheap path stops being
the safe one -- and the answer is not prose but `refuse_unusable_backend`, which
runs `execution_support` and `route_coverage` on every backend the harness resolves.
Both only look, so they cost a turn nothing; the other two write files and run
commands and stay a deployment's to call. *(Landed a day earlier, on its own merits:
it improves the old seam too.)* The typed `BackendFactory` covers the one failure a
runtime check cannot see -- a factory written without `runner` drops the
`CommandRunner` the deployment wired, and the backend that comes back is well-formed
and merely runs its commands in the wrong place.

**Required, except that a pre-built graph is an answer too.** `graph=` carries its
own backend and `_graph_for` returns it before the factory is reached, so demanding
one beside it would force 72 call sites to name something provably discarded --
making the mistake the old `graph=`/`backend_from=` refusal existed to catch into the
mandatory spelling. So the rule is exactly one of the two, both refusals at
construction. That is why `backend` keeps a `None` default in the signature: the
requirement is a pairing, not a parameter.

**`run` and `stream` keep a default, and `Kingfisher` does not.** They are
documented as conveniences over a *default* `Kingfisher`, and `formats.md` teaches
`run(Request(...))` as the first thing a reader writes. The default is in their
signature rather than their body so it can be seen and replaced without dropping to
the constructor.

**`default_backend` is one of the eleven names, back through the front door.** The
rule they left under -- a caller means a caller outside this wheel -- is unchanged;
what changed is that every such caller now has to name this one. Its witness was
`service` rather than `document`, because the service imported it and that half of
the table was read off the real imports and could not rot. It is `document` now,
since the service is gone and the guides write it out.

**Considered and rejected:** requiring `backend_from` unchanged, which would have
been ceremony with the default still built inside; a narrower `(session_dir) ->
backend` factory, which makes the deployment build the closure and worsens the
dropped-runner problem; keeping both a required base factory and the optional
adjuster, two parameters answering one question; and a service setting naming a
factory, which stayed unopened for the reason the entry above gives -- the service
reached the seam without a name to resolve, and the parameter becoming required is
not the same as something asking for one.
*(2026-09-15, in three slices: the rename, the check, then the parameter.)*

## The command line

**The command is a consumer of the library, not an insider.** Seeding and the
inventory became public API to make that true. The library answers "what does
this workspace offer" with a record rather than a list of names. Bare
`kingfisher` prints help. Publishing was deferred, deliberately, and asked
twice. *(2026-08-17, `a-command-worth-shipping.md`.)*

**Reversed: the command is for what the library cannot do for itself.** That
rule was written above the console script and kept the surface at `seed` and
`list`; running a task was `kingfisher.run` and therefore not the command's job.
It measures the library's completeness rather than the user's -- "the library
can already do it, in Python" is true of every command-line tool ever written,
and here it meant an editor, four lines, and knowing a bare task string is
refused because a request must name an agent. Replaced by *what does a person at
a terminal need to do*, under which `run` is the first verb and the others exist
to get somebody to it.

**Housekeeping is a verb, and it goes in through the front of the library.**
`kingfisher sessions` lists what a workspace holds -- id, how long idle, what it
costs -- and `kingfisher reap` deletes what it is finished with: the TTL by
default, `--older-than` for one run, `--session` for one by name whatever its
age. Both build a whole `Kingfisher`, at 1.3s and a refusal on a workspace with
no `models.yaml`. A lighter path needing only the directory was the obvious
alternative and is the wrong one: a session is four things in four places, one
of them whichever `SessionStore` a setting names, and a second reader of those
settings buys a fast cleanup that silently leaves the store's copy behind --
the shape of the 132 orphaned threads *Sessions* already records.

**Both defaults are about which mistake is cheap.** `--older-than` refuses a
bare number and takes `30m`, `12h`, `7d` -- or `0`, the one age that means the
same in every unit. Seconds would have matched the setting beside it and would
have made `--older-than 7`, from somebody who meant a week, sweep every session
no turn is running in. And bare `reap` sweeps at the TTL rather than sweeping
everything, so the least-typed invocation is not the most destructive one; when
it removes nothing it names what decided, because a sweep that deletes nothing
and prints nothing is one whose next user deletes the directory by hand --
leaving the conversation, the claim and the store's copy exactly where they
were. *(2026-09-14.)*

**A run can be told to take its session with it, and only a finished turn is.**
`kingfisher run --delete-session` and `Kingfisher.run(delete_session=True)`
dispose of the session once the turn ends. A turn stopped at a bound keeps all
of it and says how to pick it up or remove it: that ending is the one whose
leftovers are worth something -- the partial work is real, the conversation is
what a retry on the same session is rebuilt from, and the line printed beside it
already promises both. The files a deletion takes are named on the way out,
which is the only place this command prints `artifacts` at all, and a deletion
that fails does not change the exit code -- those three say how the *turn*
ended, and `1` already means the answer above was cut short.

**Offered on `run` and not on `stream`,** which is not the asymmetry it looks
like: a generator has no *after the turn* that this library controls. Past the
final yield never runs for a caller who stops reading at the answer, and a
`finally` fires on `GeneratorExit` too -- so a session would go because somebody
closed a loop early, which is the shape `_stream_turn` already carries a comment
about. The command drains `stream` itself and so disposes after `show` returns,
which leaves the two surfaces sharing no code path: the rule they both need
lives in `RunResult.completed`, and
`test_no_surface_decides_for_itself_what_a_finished_turn_is` is what keeps a
second copy of it from being written. *(2026-09-14.)*

**Rejected: sweeping at the start of a run.** With `reap` a verb and
`--delete-session` a flag, the obvious third move is for `kingfisher run` to
sweep whatever the TTL calls expired before it does anything else -- so that
`KINGFISHER_SESSION_TTL_S` finally decides something without anybody typing a
command.

Not rejected on cost, which was measured first and is nothing: a sweep finding
nothing expired is 1.65ms at fifty sessions, 6.3ms at two hundred and 33ms at a
thousand, against a turn of 1.5-1.9s. It is rejected on shape. A command that
deletes as a side effect of being asked to do something else has a blast radius
nobody reads, because the person reading `kingfisher run --help` is asking how
to run a task. The janitor already has a door -- `reap` is it -- and a
deployment wanting this on a schedule has cron and a `reap()` to call from it.

What that leaves standing is that nothing enforces the TTL, which is why the
row in `configuration.md` says so rather than letting the setting read as
automatic. Anyone re-proposing this should know the measurement was never the
objection. *(2026-09-15.)*

The half that survives: a bare invocation of the driver spends real money on the
smoke, which is a fine default for a driver and a wrong one for a stranger's
first command. A verb with a required task argument cannot be reached by
accident. *(2026-09-04, `the-verb-that-runs-a-task.md`.)*

**`run` takes six flags, and the eight that narrow capabilities are not among
them.** `task`, `--agent`, `--session`, `--input`, `--data`, `--as` -- and
`--delete-session` since 2026-09-14, which says what happens to the session
afterwards rather than what the run may do. Narrowing what a request may activate
is a deployment's concern with two better homes -- `Kingfisher(grants=...)`
clamps, an agent file declares what it holds -- and
`--without-*` freezes what the workspace offers *now*, which is a subtlety for
somebody wiring a service rather than running a first task. The cost is that you
cannot say "without the shell" from the command line; write an agent that
declares it.

`--data` was never a candidate for cutting: `/data` is read-only to the agent,
so it is the only supported way to hand one a file at all. Nor was `--as`,
measured rather than assumed -- on a workspace declaring source ids, a run that
names nobody is refused by the library, so a `run` without it would be broken on
exactly the deployments that took access control seriously. Unlike `list --as`,
an absent one is not the operator's view: a listing is read-only, and a turn
acts. *(2026-09-04, same document.)*

**The answer goes to stdout and everything watched goes to stderr**, so
`kingfisher run ... > answer.md` keeps the answer alone and `2>/dev/null` keeps
the quiet. A `--quiet` flag was rejected: it asks the caller for correct
behaviour and does nothing for whoever forgets it.

That split settled something the design had not reached. **A delegate's prose is
progress, not answer.** On one stream the speaker tag is what keeps two voices
apart; on two, the streams do it better, and an extractor's working notes are
not what anybody redirected stdout for. `Progress` moved out of the unshipped
driver rather than being copied -- two renderers would have disagreed about a
new event kind the first time one was added. *(2026-09-04, same document.)*

**The exit code carries `stop_reason`, because prose on stdout leaves nowhere
else to put it.** `0` finished, `1` ran and stopped at a bound, `2` never ran.
The case `1` exists for is `kingfisher run ... > report.md && publish
report.md`, which must not publish a report that stopped halfway. A code per
reason was rejected: it encodes in the exit status what one stderr line already
says, in a vocabulary that grows every time `STOP_REASONS` does.

Nine errors that could only ever have reached a stranger as a traceback are
reported instead, all as `2`. `SessionBusyError` keeps its own branch: it is the
one that is not the caller's mistake, and "wait" is different advice from "fix
something". *(2026-09-04, same document.)*

**`help` went, and `--seed`, `--from` and `--all` went off the driver.**
`kingfisher help seed` was byte-for-byte `kingfisher seed --help`; its only
unique contribution was naming the valid words for a mistyped verb, which
argparse already does. The driver's seeding flags duplicated `kingfisher seed`
and were kept on the argument that it is "the driver you already have open" --
true while nothing else could run a task.

**`--list` stayed on the driver, and not as a listing.** The plan said all four
go; building it found the fourth does a second job nothing else there does -- it
is the only way to reach the driver's `main` and have it return without calling
a model, which six tests covering workspace creation and first-run seeding are
built on. Written into the flag, because from outside it looks exactly as
removable as the three that went. *(2026-09-04, same document.)*

**`Seeded` says what it left behind, because three callers said it and two were
wrong.** `seed` leaves a definition alone for one of two reasons, and the remedies
are opposite: middleware is registered in code, a source id is declared in a file.
That is why the command keeps `UNCONSULTED` and `REMEDY` and a rule holding them
total against the kinds seeding can report. The integration driver and
`seed_example.py` each wrote their own line instead, and both told a reader to
*register* a source id -- the one mistake the two tables exist to prevent. Neither
offered the `source_ids.yaml` that would unblock the run, and the example script is
the one people copy.

The lines are on the record now. `Seeded.report()` is every line a person is owed
about a seeding, in order, and all three callers print it. On the record rather than
in the command because the command was never the only caller; the tables moved with
it, so what a skip means and what to do about it are in the module that decided to
skip. `test_only_the_record_says_what_a_skipped_definition_needs` is what stops a
fourth copy: nothing outside `seeding.py` composes a `skipped …` line, and the
driver, the example and `evals/` are searched along with the package. *(2026-09-23.)*

## What doctor promises

**`kingfisher doctor` exiting zero means nothing in the catalogue will break** --
at startup or on some later request, whether or not anybody ran it. It did not
mean that, and the gap was not one thing: five refusals escaped it at once, each
differently. An agent file it never looked at. A middleware directory nothing
read at all. A workspace tool wearing a built-in's name, which made the command
itself exit with a traceback over a deployment that starts perfectly well. And
two definitions naming a tool by a path it had moved from, of which the agent
half was refused *nowhere* -- it started, ran, and quietly did not have the tool
its author granted. *(2026-09-14.)*

**Checking was already separate; it was the separation that leaked.** The first
proposal was to split validation out of the runtime and lazy-load at `run`.
Measured, that premise did not hold twice over. `warm()` is not doing validation
and running -- every `_ = ...` in it forces a read a run needs anyway, and a
broken file happens to raise while being read, so there is nothing to split.
And the reading costs 17.5ms for a real workspace's seven tools once deepagents
is loaded, against ~1,000ms for the runtime itself. The checking step already
existed and already ran without starting a deployment: it was `doctor`, and what
it lacked was coverage, not a home.

**Lazy tool loading was rejected on a structural blocker, not the cost.** A
tool's name lives inside the module, so which file defines `sql_tables` is
unknowable without importing it -- three of eight shipped names are not derivable
from their filename. "Import only what is granted" needs a name-to-module map
that can only be built by importing everything. A tool whose work needs a heavy
library should import it inside the function, which gets 98% of the saving with
no manifest to go stale: 89 modules and 65ms become 1 and 1ms.

**Startup got stricter where the failure had nowhere else to surface.** A broken
middleware module and a shadowed built-in now stop `Kingfisher(...)`, because
otherwise they waited for a request. Together that is about 11ms once -- and the
shadow probe only runs when the workspace defines tools, since nothing else can
shadow one, which is what keeps it off the 179 tests that build a service with an
empty catalogue.

**An agent naming a moved tool fails `doctor` and does not stop startup.**
Deliberately asymmetric with the subagent case, which does stop it. Refusing the
agent at startup would stop a deployment that runs today over a file nobody has
touched, and the promise being made here is that `doctor` tells you -- not that
every mistake becomes fatal. `test_doctor_sees_an_agents_moved_tool_that_nothing_else_would`
asserts both halves so neither can drift.

**`REFUSALS` is what stops the next one.** One entry per function in the
catalogue-reading code that can refuse a definition, each naming a broken
catalogue that reaches it or saying why no file on disk can. It was 43 refusals in
21 functions when this was written, and the count is the table's to keep rather
than this page's. Deny by default in both directions, with the raise count
checked so a refusal added to a function already listed cannot inherit an entry
written about a different one.

Two things it found that reading would not have. The first version scanned for
`raise <Kind>Error` and missed `importing.load` and `documents.require_literal_prompt`,
which raise the error class they were *handed* -- so the rule counts a raise of a
name the function was given as well as one it names. The second: driving the
table turned up a sixth escape nobody had looked for, a bundle folder holding two
definitions, which took `doctor` down the same way the shadowed built-in had. The
filing is checked rather than trusted -- each defect is run and the frame that
actually raised is compared against the key it is filed under.

**`doctor` asks the fence the questions a run asks.** Its advice for an unconfined
shell kept its own copy of the ABI a full ruleset needs and its own reading of the
kernel, and told a kernel that could already be fenced to wait "until that is
wired" -- beside a warning, from the fence, naming the one package that was
missing. The advice now reads `landlock_abi`, `landlock_ready` and
`REQUIRED_LANDLOCK_ABI` through the confinement module, the way the fence does, so
the two cannot disagree and a test fakes the kernel once for both.
*(2026-09-15.)*

**The inventory answers by kind, so a consumer walks rather than lists.** Every
reader of it spelled the kinds out: four near-identical blocks in `doctor`, the
errors again in the listing's `failed`, and the two remaining reads by
`getattr(found, kind)` and `getattr(found.origins, kind)`, where a checker cannot
follow them. That is a hand-written list of a set that keeps growing, and this file
already recorded it going wrong twice -- `doctor` read three of the five kinds, then
four, and `middlewares_error` was computed on every walk and read by nothing for a
release.

`Inventory.by_kind()` answers a `Catalogued` per kind: its name, its error, how many
it holds, where it was read from, and whether one of them is a file or a module --
which is the whole of what those consumers were spelling out, including the noun in
"fix or remove the *module* it names". The list is written out rather than derived,
so the attributes stay ones a checker follows, and
`test_the_per_kind_view_covers_every_kind_there_is` holds it total against
`DEFINITION_KINDS` -- the half a reader cannot check.

Two things stay outside it and say so. `skills` has no single error, because a skill
fails one at a time and `doctor` gives them a check of their own; `bundles_error` is
not a kind at all, a bundle being one delegate's folder rather than a catalogue.
Each `*_error` the record carries is now driven one at a time through `failed`,
which is how one goes missing: a kind gains an error, every other check keeps
passing, and the deployment that breaks on it is the one nobody told.

Named `Catalogued` rather than `Kind` because `origins.Kind` is already a word in
this layer and means what sort of *place* something was read from. *(2026-09-23.)*

## Where a deployment reads from

**The capability flags are `KINGFISHER_*_ENABLED`, and the old names are read
by nothing.** `KINGFISHER_SKILLS` answered two questions at once: whether
a deployment wired skills, and -- exported into the agent's shell by `shell_env`
-- *where* the catalogue is, which is how a skill's own scripts reach their
neighbours. A deployment writing the path, which is what the name means
everywhere the agent can see it, set a flag to a value no parser recognises. The
flag reads `1`, `true`, `yes`, `on` and treats everything else as false, so
skills went off with no error and nothing logged. Measured rather than reasoned
about: `'/workspace/skills' -> skills enabled: False`.

All four were renamed, not only the one that collided. Four flags that read
identically should not need a reader to remember which one carries a suffix, and
`_ENABLED` beside `KINGFISHER_SKILLS_DIR` says plainly that "whether" and
"where" are different questions.

Both names were read for a deprecation, the new one winning and the old one
warning once -- the arrangement `kingfisher_service` had already made when its
prefix changed. `.env.example` lists them under an arrow rather than as
assignments, so the file does not re-advertise the spelling being replaced.

**The deprecation is over, and what replaced it is `doctor` rather than a
refusal.** Both shims are gone: `RENAMED` here and `KINGFISHER_SERVER_*` in the
service, along with the two readers that weighed one name against another. A
reader now looks up the name it wants and stops.

That leaves the failure the shims existed to prevent, and it is real: renaming an
environment variable is the one rename that fails in silence, where a moved
import stops the program and says which. A deployment upgrading with
`KINGFISHER_SERVER_PORT` still set got port 8000 and no explanation, while there
was a server to get it. Refusing to
start was the other candidate and was not taken -- a stale line somebody forgot
to delete is untidy rather than broken, and stopping a deployment over it makes
an upgrade look like a failure. `health.RETIRED` and `RETIRED_PREFIXES` carry the
dead names and `doctor` warns about any that are set, which is a place to find
out rather than a thing that happens to you.

Two rules keep that list honest, because a list of names nothing reads is exactly
what nothing else can check: `test_no_message_names_a_variable_nothing_reads`
allows a retired name in a message and then asserts the complement -- a name is
retired or it is read, never both -- and `test_every_retired_name_is_one_the_file_lists`
holds it against `.env.example`, which is the case a typo falls into. A misspelled
key warns about a variable nobody has and stays quiet about the one they do, and
nothing else in the tree would notice.

The prefix is matched rather than enumerated. Listing the service's seven
suffixes in the base package would be its table written down twice, and the copy
would stop covering whichever setting the service gains next.
*(2026-09-10.)*

**The service's own prefix joined the one it replaced.** With the service gone,
`KINGFISHER_SERVICE_*` is read by nothing either, so `doctor` reports both
prefixes and names nothing to use instead. A deployment still carrying
`KINGFISHER_SERVICE_PORT` gets no server and no error -- the silent kind of rename
this section is about -- and `doctor` is the one place that can say so.
*(2026-09-15.)*

**Not solved by inferring the flag from the catalogue**, which was the obvious
alternative and is worse in four ways: the skills section lives in the system
prompt, which is the cached prefix, so a prompt derived from directory contents
changes when a file appears; `KINGFISHER_SKILLS_DIR` exists so several
deployments can share one reviewed catalogue, and inference would opt every one
of them in; the failure mode inverts from "I set it and it is off" to "I set
nothing and it turned on", which leaves nothing to grep; and a capability would
be derived from data rather than from configuration. *(2026-09-05.)*

**One record, `Origins`, and every surface prints it.** Kingfisher reads from
eleven places and nothing could say what they were: `kingfisher list` named four,
`doctor` named one, and the library named none. `tools` was in no answer at all,
because the listing header was three hand-written lines and `Inventory` carried
three loose strings -- a fourth of each is a thing somebody has to remember, and
nobody did. `Inventory` carries one of these now and both commands print it, so a
place added to the record appears in both without either being touched.
*(2026-09-02, `where-this-deployment-reads-from.md`.)*

**It reports what was loaded, not what was configured.**
`Config.catalogue_roots` is the fallback, not the answer -- a `Kingfisher` may be
handed a mapping or a `Definitions` of its own -- so a report derived from
configuration alone is right for the simple deployment and quietly wrong for the
one that moved something. `Origins.of` does not call `resolve_definitions`, which
creates derived roots: a report must not bring into being what it reports on.

**Each entry carries a kind, not a formatted string.** `default` is the derived
location, decided by comparing against it rather than by asking whether an
override was set -- so a deployment naming the default path explicitly is
`default`, which is what it is. `relocated` is any other configured path,
`overridden` means the configuration is not what is being read, `supplied` is a
repository with no directory, and `unset` carries where it looked. `--json` and
the line `Kingfisher` logs at construction read this -- and the service did -- so
"nothing is configured" and "you handed me a store"
must not arrive as two spellings a consumer has to match on.

**`Config` remembers where it looked for `source_ids.yaml`.** The path was read,
used for error-message prefixes and discarded. It sits on `Config` rather than on
`SourceIds` because of the absent case: with no file there is no record to hang a
path on, and "not set, and here is where I looked" is the one line that makes a
policy written one directory off visible at all -- otherwise the deployment comes
up reachable by everyone and says nothing.

**The library's first logger is `kingfisher.origins`, and not `kingfisher`.**
One INFO record per construction, and that is the whole budget. `print` is not an
option -- a library that writes to stdout cannot be used by a server -- and
`warnings.warn` means "this is probably not what you meant", which a summary is
not. The name was the load-bearing part: `kingfisher.audit` was left unconfigured
so that writing session ids stayed a deployment's decision, a logger named
`kingfisher` is its *parent*, and the server raised this one to INFO -- so asking
where the definitions live would have turned the audit trail on. A test in the
service held the two apart. The audit logger and the server went with the service,
and nothing in the library writes to `kingfisher.audit` now.

**`doctor` gained two checks a path alone could not express.** An empty
catalogue at a path somebody typed is not an empty workspace: resolving one
*creates* the directory it was pointed at rather than refusing an absent one, so
a mistyped root yields a real empty one and `ok  subagents  0 defined` is what a
correct fresh workspace says too. And a configuration that is being ignored is
said out loud, or somebody edits the setting and watches nothing change. The
second needed `examine` to take the inventory rather than build one -- a
catalogue it resolves from `cfg` agrees with `cfg` by construction, so nothing it
examined could ever be overriding it.

**Rejected along the way, and each for its own reason.** Opening in-code
configuration as a first-class path -- `Models`, `Endpoint` and `ModelProfile`
are constructible and the test suite wires a `Config` that way, but setup stays
YAML and directories and this was about making that legible, not replacing it.
Merging `WorkspacePaths` into `Config` -- `Config` requires a `Models` and
`models.yaml` lives inside the workspace, so merging means making it optional and
taking `Models.resolve()` from total to partial. Nesting one inside the other to
end the six duplicated fields -- measured at 7 constructions and 38 reads, both
cheap, against 17 `replace(cfg, ...)` calls that would become nested. Serving the
record over the HTTP surface, which authenticates nobody. And a `Config.paths`
property, which nothing would have read.

## The HTTP service

**Reversed: an HTTP service in this repository.** `kingfisher-service` was removed
because nobody ran it. It was already its own distribution, imported nothing past
the front door, and no container or compose file started it, so it came out as a
deletion rather than a migration. The entries below are what it decided while it
existed and stay as the record; the code is in the commit that removed `service/`.

What it leaves is named rather than swept into the same change. Several library
features had it as their only reader here -- opening a session without a turn,
fixing its agent before one, files passed by id through `FileStore`, the async
turn path -- and each is a decision about callers this repository cannot see, to
be taken one at a time after counting what still uses it. `file_store_named`
could not wait: it turned the service's own setting into a store, and with the
setting gone it had no caller and no honest witness. *(2026-09-15.)*

**Taken: the async turn path goes.** `astream` and `arun` existed so turns could
overlap on one event loop, which a server needs and nothing left here is; with the
service gone their only callers were their own tests and one spike. They were also
the second copy of the turn -- `_astream_turn` repeated `_stream_turn` down to its
cleanup, and mutation testing had already found a flag the copy set by hand. A
caller wanting turns to overlap runs `run` on threads. *(2026-09-15. **Half of
this was reversed** -- see *Reversed in half* below, which is where a caller
should start: `astream` is back, and turns overlap on one loop without the
threads. What stayed removed is the second copy of the turn.)*

**They do overlap, and it took two checks to say so.** The sentence above ended
"which should overlap as well since a turn is almost all waiting on the model --
reasoned, not measured", and an entry that names a measurable claim and declines
to measure it reads as an open question. Eight turns in eight sessions: **26.14s
one after another, 5.27s together, 4.96x** on the live gateway
(`spikes/concurrent_turns.py`, 2026-09-18). Not 8x, and the shortfall is the part
of a turn that is not waiting -- `service.py` records construction as CPU-bound
and unhelped by threads.

`test_turns_in_separate_sessions_are_in_flight_at_once` is the half a gateway
cannot answer: a barrier where the model call goes, so the check is driven rather
than timed and goes red if anything later serialises a turn. Its control is the
half worth insisting on -- without it the check passes against a barrier one turn
fills. The spike is the half a fake model cannot answer, since an endpoint free to
serve one request at a time would make the threads pointless however good the
library is.

**The measurement found a bug rather than confirming a number.** Eight concurrent
turns crashed four times over on macOS: the sandbox profile's scratch file was
named after the process, and every thread of a process shares a pid. The advice in
this entry was unrunnable on the platform it is developed on for as long as it has
stood. Fixed where the mistake was, in `_write_atomically`. *(2026-09-18.)*

**Reversed in half: `astream` and `arun` are back, the second copy of the turn is
not.** What the removal missed is an asymmetry it never mentioned. A caller on an
event loop who wants an *answer* writes `asyncio.to_thread(kf.run, ...)` and is
done; a caller who wants the events as they arrive has to run the turn on a
thread and hand each event across through a queue, which is twenty lines, easy to
get subtly wrong, and described on no page here. Streaming is what an async
caller wants -- a bot showing tokens, a route streaming a response -- so the
workaround was fiddliest exactly where it was needed most.

**What is not back is the second copy of the turn.** `stream` drives
`graph.stream` and `astream` drives `graph.astream`, and those eight lines are
the whole of the difference: every bound, translation and release is in
`_turn_lifecycle`, which both share, and the state they write is one `_Turn`
record. That is what the 2026-09-15 removal was actually about -- `_astream_turn`
repeated `_stream_turn` down to its cleanup and set a flag by hand -- so the
lifecycle came out first and the loops are only loops.

**Driving the graph for real is what makes cancelling immediate.** Measured on a
ten-second model call: **0.00s** against **9.71s**. A cancelled `await` abandons
the request; a thread has to be waited out, which is what the first version of
this did, and the turn's cleanup still runs inside that instant so the session is
free before the caller continues. The 120-second worst case this entry used to
document was a property of that shape rather than of the problem.

**What the async path asks of a deployment, and the sync path does not.** The
`a`-prefixed middleware hook is the one that runs there, so a middleware written
only as `wrap_model_call` raises the first time an `astream` turn reaches it --
loudly, measured against langchain's own machinery, rather than being skipped.
A saver passed as `threads=` needs `aget_tuple` and `aput`, which langgraph
calls; `InMemorySaver` has both and `SqliteSaver` does not. Neither refusal can
reach a caller of `stream`, whose behaviour is unchanged, and the async pair had
no callers at all when this landed -- so this is a requirement of a new API
rather than a break in an old one. `guides/middleware.md` says it where a
deployment reads it.

**Two things that had to be closed by hand.** `_prepare` goes through
`asyncio.to_thread`: it is 15-46ms of CPU-bound construction, and on the loop it
would be 15-46ms every other turn waits through. And `astream` closes
`_astream_turn` itself, because an async generator dropped by another one is
finalised by the event loop's `shutdown_asyncgens` rather than when it goes out of
scope -- `yield from` closes a nested *sync* generator for free and there is no
async spelling of that. Without it a caller who stopped reading left the turn's
`finally` unrun and its session claimed until the loop ended;
`test_a_cancelled_turn_does_not_keep_running_behind_the_caller` fails with
`SessionBusyError` when it is removed.

**The first version of this was thread-backed, and the record of why is worth
keeping.** It stepped the sync turn through `asyncio.to_thread` -- the
`BaseLoader.alazy_load` shape -- because a middleware with only sync hooks raises
under a native async graph, and that looked like a reason to avoid the native
path rather than a requirement to document. It also hand-rolled a thread and a
queue first, which passed every behaviour test while silently dropping the
caller's context, since a bare `threading.Thread` starts with an empty one.
`findings.md` keeps what was measured about both.

**A thread per turn in flight, not many turns on one loop.** That is the honest
limit, and it is affordable for the reason the entry above now records with a
number: turns overlap, 4.96x across eight, because a turn is almost all waiting.
The one resource that does not come free is the sandbox -- a turn that calls
`eval` holds its own QuickJS runtime, so eight concurrent such turns hold eight.

**Cancelling waits.** A thread cannot be interrupted, so a cancelled `astream`
asks the turn to stop at its next event and returns once it has -- at worst one
model call or one shell command. Returning sooner would leave a window in which
the session answers `SessionBusyError` to a retry for reasons the caller cannot
see, which is a worse thing to be handed than a slow cancel.
`test_a_cancelled_turn_does_not_keep_running_behind_the_caller` fails with
exactly that error when the wait is removed.

**Methods on `Kingfisher`, and no module-level pair.** `run` and `stream` have
one-line conveniences over a default service; these do not, because a new name in
`__all__` needs a witness and the honest witness today is that no caller outside
this wheel has asked. The day one does, the convenience is four lines -- which is
how `default_backend` came back. *(2026-09-18.)*

**Asked and declined: making a turn a langchain `Runnable`.** The question is
reasonable -- `Runnable` is the interface that ecosystem's callers already know,
and it would bring `batch`, `astream_events` and LCEL composition with it. Three
reasons not to, and the first is the one that surprised us:

It removes none of the work. `Runnable.astream`'s default does not iterate the
sync `stream` -- it yields one chunk from `ainvoke` -- so the bridge above would
still have to be written, and the interface would sit on top of it rather than
instead of it.

It cannot live where the turn lives. `THIRD_PARTY` grants `application` nothing,
and that is not an oversight to edit around: the runtime's types belong behind
`infrastructure/harness`, which is where `subagents.py` and `tools.py` went for
this same reason. A `Runnable` adapter there is a perfectly good idea the day
somebody wants one, and it needs a witness first.

It adds a second vocabulary for what a request may do. `RunnableConfig` is
langchain's answer to "how should this run"; `Capabilities` is kingfisher's answer
to "what may this request reach", and it narrows and never widens. There is
nowhere in the first for the second to live, so the two would sit side by side
meaning different things, permanently.

What was actually worth having out of that ecosystem -- a caller's context, and
the tracing hanging off it, reaching the turn -- arrived with the bridge above
and needed no interface at all. *(2026-09-18.)*

**Taken: files passed by id go.** `Request.input_refs` and `data_refs` let a
caller with no host paths name files for a `FileStore` the deployment wired to
resolve -- the service's vocabulary, which is why that store's setting was the
service's. With the service gone nothing built a request carrying one; every use
was in the feature's own tests. The port goes with them, as do `LocalFileStore`,
its contract kit and `Planted`, and the `contents=` path the placement writers
kept for fetched bytes. A caller whose files are elsewhere copies them to this
host and passes paths, which is what `kingfisher run --input` and `--data`
already do. *(2026-09-16.)*

**Taken: opening a session without a turn goes.** `start_session`, the
`Kingfisher.remember_agent` method and `open_session_for` minted a session, fixed its agent and
handed back an id before any turn ran -- what a server does between the request
that opens a session and the first one to use it. Nothing here does that: the
command continues a session it was given and mints one by running a turn, and the
only callers left were tests. A session now begins with its first turn, so an id
is only ever issued, and `_admit` is handed the session its caller is already
holding rather than opening one itself. The pre-turn route had cost a data-loss
bug twice over -- a stub session laid out under the workspace whatever
`session_root` answered, swept by `reap` out of its own store, and a pin written
where a custom root's turn would not look -- and what is left cannot produce
either. *(2026-09-16.)*

**Taken: definitions passed by id go.** `Request.skill_refs` and
`subagent_refs` let a caller send catalogue ids for skills and subagents of its
own, which `provision` fetched through a `DefinitionStore` and unpacked into the
session. That is a route for a caller with no filesystem here, which is what the
service was; with it gone nothing built such a request, and every use was in the
feature's own tests. The port goes, with `uploads.py`, `UploadError` and
`Kingfisher(definitions=)` -- and so does `Capabilities.including`, which existed
for the single exception to *Capabilities narrow and never widen* above: an
upload widening skills and subagents. Nothing widens now, so the exception is
gone rather than merely unused. What fed on it -- `/skills/uploaded`, the overlay
in `layered.py`, and the write the shell was granted there -- reads empty
directories until the change after this one removes it. *(2026-09-16.)*

**Taken: a session contributes no definitions.** With nothing able to put one
there, the half that read them goes: the overlay `layered.py` built per turn,
`read_uploaded` and the registry merge behind it, the `/skills/uploaded` route
and its mount, and the entry in `SESSION_PLUMBING` that made the directory in
every session. That entry is also what granted the shell write access to it, so
the fence now grants `data`, `derived`, `memory`, `runs`, `.home` and `.tmp` and
nothing more -- the list under *Landlock cannot take back what it has granted*,
above, names one place fewer. The `session_dir` that threaded through
`available_skills`, `defined_subagents`, `indistinct_delegates` and
`withheld_by_kind` went with it: what a request may activate is what the
catalogue holds, and a turn reads no definitions of its own. *(2026-09-16.)*

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

## The front door

**A caller means a caller outside this wheel.** `kingfisher.__all__` is a promise
to somebody holding `pip install kingfisher` and nothing else. The command ships
in the same distribution, so it is family: it reaches a name the door does not
carry at the module defining it, and still comes through the door for every name
that is there. The service was its own distribution and kept the strict rule.
*(2026-09-06, `the-front-door-is-for-outsiders.md`. Three slices, all landed
the same day.)* With the service gone, the command is the only consumer
`CONSUMERS` holds.

**It started as a proposal to move the CLI into its own wheel, and the
measurement reversed it.** The stated reason was keeping the library's import
surface clean, and the split would have made that strictly worse: `CONSUMERS` in
`test_architecture.py` then held `kingfisher_service` -- a separate wheel --
to the front door, so a `kingfisher-cli` would be the second out-of-tree consumer
and every name it reaches would be locked public permanently, as a
cross-distribution promise rather than an in-tree convention. The packaging
argument does not carry either: the service left because a library caller should
not pay 2.7MB of fastapi and uvicorn to import `Request`, and the CLI's whole
foreign cost is `python-dotenv` at 100KB, never imported by `import kingfisher`.

What the measurement found instead: of 57 exported names, 25 were reached by the
CLI and no other shipped consumer, and the export table's own comments recorded
five of them as forced public by a consumer reaching. `doctor` wanting a sandbox
probe had made the probe a promise to everybody.

**Enforced by name, not by file.** The line could have run between CLI modules --
`__main__.py` and `progress.py` held, `health.py` and `listing.py` free, which is
where the claim in `cli/__init__.py` then lived; its docstring is one line now. Rejected: those two files
also use `Config`, `inventory`, `Origins` and `Inventory`, so freeing the files
lets them drift to deep imports for real public names with nothing going red. The
rule is that the door is mandatory for anything on it, which is self-maintaining
-- put a name back on the list and every deep import of it goes red without
anyone remembering to move a file between buckets.

**Every public name carries a witness, and the service's half is read rather
than claimed.** *(Four kinds of witness when this was written; `command` and
`service` have gone since, as the entries below record, and `document` and `embedder`
remain.)* `WITNESSES` says who asked for each name: `service` (verified by
parsing the service's imports, both directions), `document` (a page tells a
reader to write it -- checked by a person, because `offered` gets five hits in
the guides and `run` thirty-one, all of them the English word), `embedder` (kept
deliberately, with the reason), and `command` (nobody outside asked; the work
that remains). Deny by default: a name in `_EXPORTS` and not in the table fails.

It earned itself before it landed. `ConfigError` was written down as `embedder`
-- "raised by `config_from_env`, so a caller that builds a `Config` must be able
to catch it" -- and the service picked it up while this branch was open. The
rule reads the service rather than the label, so it said so, on the direction
that is easy to leave out: not a claimed witness that is false, but a real one
that arrived after somebody wrote a weaker reason down.

It is the fifth list of these names, in a file one of whose rules said, when this
was written, that a second table is what it exists to distrust. Accepted on the precedent beside it --
`LIGHT_EXPORTS | HEAVY_EXPORTS` is a second table made safe by a test that it is
*total*, and this one is held the same way.

**The light/heavy partition is re-keyed on consumers, as a union.** It watches
what a consumer pays to import, and was standing on `__all__`, which stopped
being the same set the moment a name left it -- `Confinement` is classified light
*because* `doctor` reaches it, and going private did not make `doctor` stop. The
union rather than a swap is deliberate: nine public names are imported by
neither consumer and one of them is heavy, so replacing would have quietly
dropped them out of a guard they were already inside.

**Sixteen names came off, in three slices.** `Confinement` (light) and
`unrunnable_delegates` (heavy) landed with the rules, so each new guard had a
live case in both branches -- a rule with no cases passes whatever it says, which
this suite has shipped twice. Then `doctor`'s six -- `bubblewrap_available`,
`landlock_abi`, `shell_confinement`, `memory_backing`, `destination_hint`,
`DEFINITION_KINDS` -- and then `list`'s eight: `ALL`, `AUDIENCED`, `Audience`,
`SEED_HINT`, `SKILL_LAYOUT`, `offered`, `spell`, `split_reference`.

What that leaves is the shape worth keeping. `health.py` and `listing.py` each
reach for every name they render or probe with, and each takes from `kingfisher`
only the names that are answers the library gives anybody -- `Config`,
`inventory`, `Inventory`, `Origins`, `kinds_at`. The two kinds of name are told
apart by where the import points, in the two files that used to cost the door
most. 57 became 41.

**The fourth witness went with the last of them.** `command` was never a witness:
it was the eviction list wearing the table's shape, so that the work remaining
lived in the code rather than only in a proposal, and so a mislabelled entry --
the service importing one, or the command not -- was refused. Sixteen names
carried it and none does now, so it is gone rather than kept as a value that is
always an error. What stops the list growing back is the deny-by-default rule and
nothing else, which is enough: a name whose only caller is the command can be
given none of the three remaining witnesses without somebody writing down a
reason that is false, and the reason is the part a reader can check.
*(Slices two and three, 2026-09-06.)*

**The `service` witness went with the service.** It was the one kind read off
real imports rather than claimed, and its names were witnessed again one at a
time: `document` where a guide tells a reader to write the name, `embedder` where
it is what a documented call takes, returns or raises. `file_store_named` had no
second reader and left the door. *(2026-09-15.)*

**The stub block is a third listing of these names, and is bound to the other
two.** `__all__` and `_EXPORTS` were held to each other; the `if TYPE_CHECKING:`
re-exports were held to nothing, and had already drifted -- `spell` and
`SessionInfo` exported with no entry. Measured rather than assumed: `__getattr__`
returns `Any`, so a name with no stub imports fine, passes every test, and is
simply untyped. `reveal_type` says `<class 'Capabilities'>` for a name with a
stub and `Any` for one without, and the drifted one was `SessionInfo`, which the
service imports.

The module string is compared as well as the name, for the reason the layer and
the root are: two tables agreeing a name exists while disagreeing where it comes
from would type-check against one object and import another. So is the redundant
`X as X`, which under PEP 484 is what marks a name as re-exported at all -- and
which reads like something to tidy away. Both packages with a lazy table are
covered, `kingfisher` and `kingfisher.application`. *(2026-09-06.)*

**What it costs, stated rather than discovered.** A deployment embedding
kingfisher and wanting its own health endpoint loses the promise on the sandbox
probes; the names still work, at addresses that may move. There is no measured
caller who wants that today, which is what makes it defensible -- and *Where a
deployment reads from* declined to serve `Origins` over HTTP because the service
"authenticates nobody" rather than because nobody wanted the answer, so that is
the entry to re-read if one appears. No deprecation window, on the precedent the
export table set for the eleven names that left before these: at 0.1.0 an outside
caller on the old spelling changes one import line.

**`kinds_at` stays**, on `test_the_whole_job_is_reachable_through_the_front_door`
in `test_inventory.py`, which is the written form of the claim and names it as
part of the job. Whether it belongs there is an argument about the proof, not
about the door.

**Reversed: "a kind owns its route to the runtime."** A kind owns its format and
its walk over the disk, and nothing below either. `kinds/subagents/harness.py` is
`infrastructure/harness/subagents.py`, `kinds/tools/harness.py` is
`infrastructure/harness/tools.py`, and `test_no_kind_names_a_layer` is what keeps it
that way. *(2026-09-10.)*

**The entry it reverses was right about ownership and wrong about direction.** *An
asset kind owns its own registration* said the kinds "do not answer to a layer", and
five imports said otherwise: the subagent runtime half named three modules under
`infrastructure/harness/` and `infrastructure.prompting`, and the tool surface named
`infrastructure.catalogue`. Nothing was checking. `THIRD_PARTY` watches foreign
packages, `HARNESS_EDGES` watches the layers reaching *in*, and the direction between
`kinds/` and the layers was written in prose and enforced nowhere -- which is how a
sentence in `CLAUDE.md` about `infrastructure/harness/` had already been false for
six days before anyone read it against the table.

**Three more edges were found by writing the rule, not by planning it.** All three
`reading.py` imported the YAML step, which was a flat module under `infrastructure/`,
and the graph the plan was built on had missed them: it recorded
`from kingfisher.infrastructure import documents` as an import of the package root
rather than of the layer. Worth writing down because the
lesson is not about `documents` -- a measurement that collapses a path loses exactly
the edges a layering argument is about, and it does it silently.

**`documents.py` split along the two audiences it already had.** *Not moved:
`documents.py`* argued it was not a kinds file because `decode` and
`require_literal_prompt` serve the kind readers while `source_ids_named` and
`middleware_named` serve workspace seeding. That is still the reading; what changed is
that the file could not stay whole once a kind may not name a layer. So the first half
is `kinds/documents.py`, beside `kinds/importing.py` in `KINDS_HELPERS`, and the second
is in `infrastructure/workspace/seeding.py`, which was its only reader. Neither half
has two audiences any more, and there is no module called `documents` in two places.

**The cost is one grant and one edge, both named.** `THIRD_PARTY["kinds"]` gains
`yaml`, so a sixth kind arriving without an entry of its own would inherit a parser
where it used to inherit nothing -- guarded upstream, since
`test_kinds_holds_exactly_the_kinds` refuses a directory `DEFINITION_KINDS` does not
name. And `HARNESS_EDGES` gains `tools` for `inventory` and for `reporting`, which is
the edge the *previous* move recorded closing. Nothing about that coupling changed
either time: `reporting` has always read the roster off a compiled graph. What changed
is whether the table could see it, and a table that cannot see an edge is worse than
one that lists it.

**What it buys, stated so it can be checked.** The swap boundary is two areas rather
than four: `infrastructure/harness/` and `kinds/skills`, plus `kinds/middlewares` for
one `isinstance`. `kinds.subagents` and `kinds.tools` grant nothing at all now, and
every kind import is 5-6ms and 65 modules. The skills store mount was then the one
kind module that still reached the runtime at import, and *the swap boundary* entry
above is where the argument for leaving it there lived; it has since been removed,
under *The catalogue*.

## Layering


**`infrastructure/harness/` holds every module that imports deepagents, langchain
or langgraph**, and only that package may. Registries and DTOs did *not* move to
`application/`, and the package root did not change.
*(2026-08-17, `layer-boundaries.md`.)* Two kinds may since: `kinds/skills`, which
asks deepagents which skills an agent has, and `kinds/middlewares`, which refuses a
class that is not an `AgentMiddleware` -- *The cost is the swap boundary*, below.

**The layout is not domain vocabulary, and moved to the package root.**
`kingfisher.layout` was `domain/layout.py`, and it left for the reason `Config`
left before it: **no domain rule reads it.** Every reader was in
`infrastructure/`, `skills/`, `tools/` or `subagents/` -- now `infrastructure/`
and `application/disposal.py`, for the claim's filename -- and it sat in the
innermost layer so those could share it without depending on each other -- which
is `config.py`'s own words, *"reasoning about import direction, not
modelling"*.

The counter is real and does not change the test. `/data` and `/derived` are the
vocabulary the prompt teaches the model, so this reads far more like the domain
than `base_url` ever did. But the test the `Config` move applied is not "does it
sound like vocabulary", it is "does a domain rule read it" -- and vocabulary no
domain rule uses is a record held for the outer layers.

**Rejected: `infrastructure/harness/`**, which is where this started. Twelve
readers across five packages and four of them are nowhere near deepagents, so
the move would have pulled `skills/`, `tools/`, `subagents/` and `workspace/`
into importing the one package the layering rule quarantines. A file that
imports no framework does not belong in the package defined by importing one.

**Rejected: splitting the route table out to the harness.** Every field of it is
shaped by deepagents -- `routed` exists because `CompositeBackend` has a default
slot, `deny_write_under` is a glob because `FilesystemMiddleware` takes globs,
the trailing slash exists because matching is by prefix -- and only
`infrastructure.harness.backend` and `infrastructure.harness.agent` read it in
code, with the backend contract kit beside them. That is a real argument, and the entry above is one day older than it:
pulling the table apart would undo the joining that entry exists to record. The
root belongs to no layer, so framework-shaped data there costs much less than in
the innermost one.

**The move activated a comment that had been wrong for some time.**
`domain/ports.py` attributed `within` to the layout module; it is in
`domain.references`.
Prose roots are read off the tree, so `layout` only became a resolvable root
when the file arrived at one -- and the rule caught it on the first run after.
*(2026-09-07.)*

**An asset kind owns its own registration.** `tools/`, `skills/` and
`subagents/` are modules at the package root, each holding what its definitions
say, how they are found on disk, and how they reach the runtime. Kingfisher
fetches from them; they do not answer to a layer. *(2026-09-04.)*

**Reversed: "at the package root."** The five kinds are `kinds/agents`,
`kinds/skills`, `kinds/subagents`, `kinds/tools` and `kinds/middlewares`. Everything
else in the entry above stands -- a kind still owns its format and its walk over
the disk, and still answers to no layer. It owned its route to the runtime too when
this was written; `tools` and `subagents` have since handed theirs to `harness/`. What changed is
where the five sit relative to each other, which that entry never argued for.

**The reason is a rule, and the tidier root listing is not it.** Read honestly, the
case put first was presentation: nine packages at the top level, five of them a
family, and no way to say so. Presentation is what *"a second subpackage would
advertise a distinction no test could hold"* was written against, and the answer
that entry settled on -- the modules have to share a subject -- is met here as
squarely as `sandbox/` meets it. `DEFINITION_KINDS` already names the subject, and
`test_kinds_holds_exactly_the_kinds` holds the directory to it in both directions.

The second direction is the part that could not exist before.
`test_the_catalogue_holds_one_module_per_kind` walks the constant and asks whether
each name has a reader, so a sixth directory carrying a `spec` and a `catalogue`
passed it by never being asked about. Flat at the root there was nothing to compare
against: the top level legitimately holds four layers and three loose modules too.

**Measured against the alternative, because the alternative is cheap.** The same
check is writable today without moving anything -- the nine packages at the root are
five kinds and four layers, so a four-name `LAYERS` constant and one assertion gets
it. That is six lines against seventy files, and both versions keep a hand-written
constant: this one keeps `KINDS_HELPERS`, two names then and three since
`documents` joined. So the check is not what pays
for the move, and saying otherwise would be reverse-engineering a reason. The move
is taken on the presentation case with the rule as a consequence, and the numbers
are here so the next reader is not told a better story than the one that happened.

**`infrastructure/importing.py` came too, and `documents.py` did not** -- until
2026-09-11, when the seeding scans had gone to `seeding` and what was left served
only the kind readers, and it moved into `kinds/` as well. Four kind
catalogues are `kinds.importing`'s only readers and it imports nothing from
kingfisher at all. `documents` serves two audiences -- `decode` and
`require_literal_prompt` for the kind readers, `source_ids_named` and `middleware_named`
for workspace seeding -- so it is not a kinds file, and *"Two modules came up a
directory, and an import cycle is why"* is why moving it again would need its own
argument. The cycle that entry records cannot recur here: `kinds/__init__` imports
nothing, which is also what keeps `kinds.skills` at 6ms rather than the 1,033ms
a re-export reaching a module that imports deepagents would cost every kind import.

**`THIRD_PARTY` gained an entry rather than losing four.** Collapsing the five kinds
into one `kinds` key would hand `kinds/agents` -- the only kind whose set is empty,
and empty because its runtime half stayed in `harness/` -- the union of the other
four. The five stay, re-keyed, and `kinds` itself is named with an empty set so a
kind added without an entry inherits nothing rather than the package root's answer.
*(Since then `kinds/tools` and `kinds/subagents` are empty too, and `kinds` grants
`yaml` for `documents`, so a kind added without an entry inherits that.)*

**Found on the way past: `CLAUDE.md` had been wrong since 2026-09-04.** It said only
`infrastructure/harness/` may import deepagents, langchain or langgraph, which
stopped being true the day `tools` and `subagents` became packages. No test reads
that file against `THIRD_PARTY`, and nothing else would have said so.
*(2026-09-04, reversed in part 2026-09-10.)*

**A kind reads its own documents.** `kinds.subagents.reading.read`,
`kinds.skills.reading.name_from`, and `kinds.agents.reading.read`. Each was two functions in two packages: an envelope opener in
`infrastructure` and the format's own parser, with the opener calling straight
back into the module it was called from. *(2026-09-05.)*

**The split was left by a constraint that had already lapsed.** The domain
imports the standard library and `kingfisher.domain` and nothing else, so when
these formats lived in `domain/` the `yaml.safe_load` had to sit elsewhere and
hand fields back. Two of the three formats stopped being in `domain/` when they
became modules, and the wrapper stayed. Each half had exactly one caller, and
`decisions.md` already cut a seam on that ground once -- a `CatalogueSource`
protocol, "designed in full and cut before building, on the grounds that one
implementation is not a seam".

**What could not follow them stayed, and it is not much.** `decode` and
`require_literal_prompt` are shared by all three: a scalar's style is a fact
about a document rather than about what any kind means, and the agent and
subagent formats reflow a prompt identically. `documents` is now that, plus two
scans that read a document without parsing it, and it names no kind at all --
one kingfisher import where it had five. *(The skill reader went on 2026-09-16, so
the agent and subagent readers are its only users; the scans have gone to `seeding`,
and it imports nothing from kingfisher.)*

**`skills` gained a `reading` beside its `spec`, and the reason is a rule.**
*(That `reading` went on 2026-09-16 with the upload path, its only caller.)* A
domain module may name a kind's `spec` and nothing else of it, on the stated
grounds that a spec is format vocabulary with no adapter behind it. Reading a
document needs `yaml`, so folding the reader into `kinds.skills.spec` would have made
that sentence false while leaving it written down -- and the architecture test
checks direct imports, so nothing would have said so.

**Two modules came up a directory, and an import cycle is why.**
`catalogue/__init__` imports three kind modules; `documents` and `importing`
sat inside that package and were imported *by* those kinds. The loop resolved by
luck of import ordering and stopped resolving when the readers moved -- a cold
`import kingfisher.kinds.subagents.catalogue` failed outright. Both files are
generic:
`importing` imports nothing from kingfisher whatsoever, and `documents` now
imports one thing. `infrastructure/__init__` imports nothing, so moving them up
removes the edge instead of reordering around it.

**Not moved: `layered`.** *(Removed on 2026-09-16, when a session stopped bringing
definitions of its own.)* `LayeredSkills` and `LayeredSubagents` stay in one
file because it exists so that two *differing* merge rules can be read against
each other -- a sorted set union for one, a right-wins `dict |` for the other.
They were once two inline expressions in `agent.py` that silently did different
things, and putting them in separate packages for symmetry would restore exactly
the arrangement that let that happen. "Kinds do not share" is about shared
implementation; a deliberate comparison is the opposite.

**Three things were in the wrong place, and the move found them.** `NEAR_MISS`
-- the `.yml` a `.yaml` gets mistaken for -- sat in the subagent *catalogue*
while `SUFFIX` sat in `reading`, so the agent repository had to import a
catalogue to get the second one, which is the edge that closed the loop. The
`documents` docstring described sitting inside `catalogue/` and gave that as the
reason for its own name, while sitting flat in `infrastructure/`. And two of the
skill reader's refusals -- a missing `---` header, a name that is a path --
turned out to be exercised by no test at all: mutating either left the whole
suite green. That gap is older than the move and is now covered.

**They deliberately do not share.** All three resolve a `source::name` and all
three do it their own way -- `kinds.tools.spec.split_reference` and
`kinds.skills.registry.split_qualified` differ today by one call that strips a
trailing slash. Written once and shared, a change for one kind would have to be
argued past the other two. *(Subagents have since taken the tools' `split_reference`,
so two ways remain.)* The duplication is the price of each kind changing on
its own, and it is the point rather than an oversight. What stays shared is the
vocabulary of *grants* -- `SEPARATOR` and `_bare` in `domain.capabilities` --
because a grant is spelled the same way whatever it names.

**Reversed to get there: "the domain imports only the standard library and
itself."** Four domain modules needed an asset kind's `spec`: `ports` names
`Found` and `SubagentSpec` because a port cannot name a type it has no word for,
and the definition readers parsed a tool reference because an agent definition
*writes* `csv_profile::csv_profile` in its `tools:` list. The readers have since
moved into the kinds, and two remain: `ports`, and `request` for `RunOn`. That is the format
referring to itself, not the domain reaching for a layer.

The rule was refused as a wall first and then measured, which is the order that
was wrong. Importing `domain.ports` and `kinds.agents.spec` with those edges takes
39ms and loads 101 modules with no part of the agent runtime among them -- the
same 39ms recorded below as the good case against 888ms. The direction was
protecting two operational properties and this costs neither, so what stopped
the work was DDD purity in a codebase that had already decided it is an adapter
over deepagents rather than a domain model.

The exception is narrow and stated where it is enforced: the domain may name a
kind's `spec`, never its `catalogue` or its `harness` -- and no kind has a `harness`
any more, both having moved into `infrastructure/harness/`. A spec is format
vocabulary with no adapter behind it; the other two walk the disk and reach the
runtime, and a test asserts the predicate at each of those edges.

**The cost is the swap boundary.** `THIRD_PARTY` now names four areas that may
import the agent runtime rather than one. An upgrade is still a list of files
rather than a search -- which is what that rule was ever for -- but the list
spans `infrastructure/harness/`, `tools/`, `skills/` and `subagents/`. *(Three
areas now: `infrastructure/harness/`, `kinds/skills` and `kinds/middlewares`, once
the tools and subagents halves moved into `harness/`.)*

**Three things were in the wrong place and only the move said so.** Each had
lived quietly because the two halves shared a directory and nothing had to
choose: `SKILLS` and `UPLOADED_SKILL_DIR` were declared by the skill format and
used by `kingfisher.layout`, which *is* the layout; `ceiling` sat in the tool module
and touches no registry, so it went to `domain.capabilities` with the rest of
that arithmetic; and `wanted_model` was in the subagent format while
`kinds.agents.spec` imported it to read its own `model:` line, so it went to
`domain.fields`, which is the field readers. A helper two formats need belongs
to neither of them.

**When a kind should be a module, and it is not "always".** `skills` fitted
first and easiest because kingfisher does not own that format -- deepagents
reads a `SKILL.md` and decides what it means, so `spec` is 65 lines and nothing
else depended on it. `tools` and `subagents` own formats that *reference each
other*, which is why both dragged shared vocabulary into the question. Nothing
here was insurmountable, but a fourth kind with an interlocking format should
expect the same three-way negotiation rather than a rename.

**Reversed in part: "not a module: agents."** The entry read *an agent is
selected by name, one per request, and is the thing the graph* is *rather than
something the graph holds*, and drew from that: *assembling a graph out of the
three kinds is kingfisher's own job, not any kind's*. That conclusion is right
and `harness/agent.py` stays exactly where it was. It reaches one of the three
files the entry moved to keep, and a format's parser is not an assembly.

`agents/` is the fourth kind module: `spec` the format, `reading` the parser,
`catalogue` the walk and the repository -- `subagents`' shape minus `rules`,
whose one cross-definition question is answered where it is found, and minus
`harness`, for the reason above -- which no kind has now.

**The test applied is the neighbouring entry's.** *Not a module: middleware* is
what says what earns a package -- *a file somebody authors, a reader for it, and
a registry built from what was found* -- and an agent has all three where
middleware then had none. Agents was the only kind that met that test and lacked the
package, and this entry refused it on a different ground: what an agent *is* at
runtime, which is a fact about the graph rather than about where a YAML parser
lives.

**It costs nothing at the boundary, which is the other half of the middleware
argument.** That entry's closing objection was that a `middleware/` package
would be *the fifth area in `THIRD_PARTY` reaching the agent runtime*. This one
reaches none: `THIRD_PARTY["agents"]` is empty, the only kind whose set is, and
it is empty precisely because the runtime half stayed in `harness/`.

**One rule got smaller.** `test_the_catalogue_holds_one_module_per_kind` looked
in two places while three kinds had left `catalogue/` and one had not. With the
fourth gone that branch is unreachable, so it is one place again -- a rule with
a branch nothing can reach is half a rule. `catalogue/` kept `__init__` and
`layered`, which was `Definitions` and the per-session merge, and held no
per-kind module at all -- `__init__` alone since `layered` went.
*(2026-09-04, reversed in part 2026-09-07.)*

**Not a module: middleware.** (Reversed on 2026-09-07 by *Middleware is a
definition kind*: `middlewares/` is a directory with a reader and a registry, and
`kinds/middlewares` is on `THIRD_PARTY`. The entry stays for the page it asked
for.) It has a field in both definition formats and a
name in `Capabilities`, which is what makes it look like a fourth kind. What it
does not have is the thing the other three earned their packages with: a format
of its own and a walk over the disk. `tools`, `skills` and `subagents` each own
a file somebody authors, a reader for it, and a registry built from what was
found; middleware has a name a definition writes and a dict the deployment
passed in, and nothing in between for a package to hold.

The pieces are where they are for reasons that survive being asked again.
`approved_middleware` and `approved_settings` are in `domain/capabilities.py`
because the rule is expressible in kingfisher's vocabulary -- both fields are
name lists -- while turning a name into an object is not. `declared_middleware`
is in `harness/` because it imports `langchain`, which is the same rule that
keeps `kinds.skills.registry` where it is. A
`kingfisher/middleware/` package would be the fifth area in `THIRD_PARTY`
reaching the agent runtime, and the last three entries there each carry a note
apologising for widening a boundary that used to be one directory.

What was actually missing was a page. Registering middleware is deployment code
like a port adapter is, and `guides/` had `ports.md` and `tools.md` and nothing
for this -- so the deployment's half lived in three docstrings inside
`middleware.py` and two example files. `guides/middleware.md` is that page, and
it links the examples rather than restating them: the copy in a docstring is the
one a test exercises. Measured on the way past, and worth recording because it
was the argument for a package that did not survive it -- a deployment writing
middleware imports **nothing** from kingfisher. Both examples import
`langchain` alone, and `MiddlewareFactory` is an annotation this package uses on
itself. *(2026-09-07. There are three examples now, and two import `langchain_core`
as well -- still nothing from kingfisher.)*

**Reversed: "the rest of the layer stays flat."** The clause read *a second
subpackage would advertise a distinction no test could hold*, and by the time
anyone looked there were three of them -- `catalogue/` had arrived without the
sentence being revisited, which is the drift the sentence was written to
prevent. `sandbox/` and `workspace/` joined it deliberately.
*(2026-08-17, reversed 2026-09-04.)*

What makes a group rather than a folder, since the original worry was real: the
modules have to share a *subject*, not a layer. `confinement`, `fence` and
`bubblewrap` are one policy and the two kernels that carry it -- the only corner
of `infrastructure/` where being wrong is a security failure rather than a bug.
`fs`, `seeding`, `uploads` and `files` all wrote into one directory, and three
of them had a rule about not destroying what another put there. *(`fs` has since
been split, and `uploads` and `files` removed; `workspace/` holds `backing`,
`layout`, `permissions`, `placement`, `seeding`, `sessions` and `snapshots`.)*

And the harness rule is what bounds them. The backend that *applies* a
confinement, and the registry `uploads` asked about a name, both import
deepagents -- so they stayed in `harness/`, until the skill registry moved to
`kinds/skills` -- and the groups are "everything about
this subject that does not need the agent runtime". A grouping that had pulled
them in would have traded an enforced rule for a tidier directory.

Two mechanical facts worth knowing before the next one. `HARNESS_EDGES` is keyed
by a module's path below its layer, so moving a file into a subpackage renames
its entry and the table goes red until told -- which is the table working.
And `tool.ty.overrides` lists files by path: the block ignoring the optional
Linux-only `sandlock` import named `fence.py` and `confinement.py`, and left
behind would have stopped matching and gone silently useless.

**Rejected with it: inverting the nesting to `function/layer.py`.** Drawn in
full -- fourteen modules, every file placed -- and measured rather than argued.
The asset kinds are 26% of the tree; a concept's files change together in only
13-29% of the commits that touch them; and the tightest co-change pair in the
repository, `subagent` with `harness/agent.py` at 18, runs straight across the
boundary such a slice would draw. It would have put a deepagents import in 8 of
14 modules, turning "only this package" into a list of fourteen filenames, and
`application/config.py` -- which must not pull the harness in, at 39ms against
888ms -- would have sat beside a sibling that does. *(2026-09-04.)*

**The rule is not a portability claim, and used to read like one.** It said the
rule "states the swap boundary: replace the harness and exactly those files are
rewritten", which is true and was the wrong thing to lead with -- it invites the
next reader to plan around a second harness. Kingfisher is an adapter over
deepagents. Supporting another framework is *out of scope*, not scheduled, and
recorded here so the question is not re-opened as though it were open.

What the rule buys, today and repeatedly, is the blast radius of an *upgrade*.
Not swapping deepagents is not the same as not upgrading it, and `pyproject.toml`
says what that costs: it "is beta and says so at every construction ... this one
has moved through 0.1, 0.2 and 0.3 in under two months". A minor bump rewrites
the same files a swap would, and the rule is what makes that a list of ten
modules instead of a search. It also keeps the harness off the paths that never
needed it, which is what lets `kingfisher seed` cost 20ms rather than importing
three provider SDKs.

Worth knowing before that scope changes: **nothing declares what a harness is.**
The twelve Protocols in `domain/ports.py` abstract storage and the OS -- stores,
repositories, a command runner -- and not one of them abstracts the runtime;
`application/` reaches it as `infrastructure.harness.runtime`, a module rather
than a port. So a second harness does not begin by rewriting ten files, it begins
by discovering the interface, which is not written down anywhere. That is the
right order: an interface derived from a single implementation comes out shaped
like that implementation. *(2026-09-04.)*

**A layer may answer for its own names, lazily.** `from kingfisher.application
import Kingfisher` works alongside the root import, and resolves through a
`__getattr__` table rather than plain imports at the top of the file.

That is not a style preference. A package's `__init__` runs before any of its
submodules, so nine eager imports there would make `application.config` -- which
needs no harness at all -- pay for `service`, which imports deepagents, which
imports three provider SDKs at module level. Measured both ways: 39ms lazy,
888ms eager, and the eager form pulls deepagents into a process that only wanted
to read environment variables. `kingfisher seed` is 20ms today and would have
become fifty times slower while staying correct.

The cost is a second table, and two tables that can disagree is what this
repository distrusts everywhere else -- so they are held to each other in both
directions, on the module string as well as the name. Only `application/` has
one, because only it was asked for. *(2026-09-03.)*

`infrastructure/workspace/` has one since, asked for so its callers name the
package rather than six modules, and lazy for the same kind of cost: `seeding`
loads `yaml` and the catalogue, which a caller wanting only `permissions` should
not pay for. It joined `LAZY_TABLES`, so the same three-way agreement holds it.
*(2026-09-23.)*

## Splitting a file


**`workspace/fs.py` became six modules; four other long files did not.**
`layout` makes the tree and places the furniture that ships in it, `sessions` is
one session's directory and the ports over it, `permissions` owns the write bits
on `/data`, `placement` copies a caller's files in, `snapshots` keeps the agent a
session opened with, and `backing` reads what the workspace sits on. *(2026-09-04.)*

**Chosen by the history rather than by reading the source.** The lesson of the
`config.py` entry below is that a count of definitions describes the file's
shape and says nothing about the work, so this time the commits drew the
boundaries: two definitions are joined when a commit touched both, and whatever
stays disconnected is a seam already being kept. `fs.py` came back as three
clusters and fifteen lone definitions, and the clusters landed on the regions
the source already read as. The callers agreed -- of 25 files importing from it,
19 need exactly one of the six, and the only one reaching five is
`application/service.py`, which is the composition root.

Git's default hunk-header pattern matches any line starting in column one, which
inside a long class reports every method as the class. `core.attributesFile`
pointing at `*.py diff=python` is what makes the measurement see methods, and
without it `service.py` looks like one definition touched 232 times.

**The same test said no to the other four, including one that looks obvious.**
`service.py` is a single cluster of 40, `capabilities.py` one of 16, and
`__main__.py` one containing `build_parser`, `main` and every command -- adding a
command touches the parser, `main` and the command together, so splitting the
commands into files would make those commits open *more* files. `backend.py`
reads like a hub plus three independent `AgentMiddleware` classes, and those
three do not appear in its history at all: they have never been edited since
they landed. Moving them relocates 250 lines nobody touches and leaves
`default_backend`, at 25 commits, exactly where it was.

**Reversed for `backend.py` two weeks later, by the same test.** *(2026-09-23.)*
The premise was that the guards were never edited, and #507, #516 and #529 then
edited nothing else in the file. Measured over its 42 commits, with each hunk
mapped to the definitions it lands in: 29 touch only the backend, 6 only the
guards, 1 only the host-path refusal, and none does real work on both. The one
cluster the whole file still forms is glued by the two commits that moved it and
the repository-wide comment cut -- the hunk-header trap again, since `git log`
names `default_backend` on #507 and the hunks never touch it.

So it is three modules. `backend` builds backends. `tool_guards` is what every
graph holding workspace tools is wrapped in. `host_paths` is `reject_host_path`
and `HostPathGuard`, which were already written down as one mechanism -- the one
raises what the other catches -- and are the only thing both of the others need,
so neither has to import the other. `HostPathError` is exported from the root,
because `ports.md` tells an adapter to raise it and the address it had given was
a module that has stopped being where it lives; `backend` still re-exports it for
anybody who copied that address.

**A cluster that size means two different things and this one is the harmless
one.** Everything joined to everything is what a cohesive unit and a rippling
one both produce, so the co-change test alone does not settle `service.py` --
which the paragraph above stated as though it did. What separates them is how
*wide* one commit has to be: a cohesive unit is edited a method at a time and
merely covers the same ground repeatedly, and a tangled one cannot be touched
anywhere without being touched in six places. `service.py` averages 2.9
definitions per commit, and 44 of its 68 commits touch one or two. The wide ones
are all features -- source-id access at 17, per-session thread databases at 13, a
session surviving its machine at 10 -- and the hub is `__init__`, which is the
composition root's constructor and is what every new dependency arrives through.

Splitting it would not narrow any of those. A feature reaching 17 methods reaches
17 methods across four files instead, and `application/` is already split five
ways: of 86 commits touching that package, 59 open exactly one file and none
opens more than four.

**The split cost one invariant its structure.** *Nothing outside this module
should ever chmod `/data`* was a fact about a file while `place_data` and the
`chmod` were the same 700 lines. It is now a rule --
`test_only_one_workspace_module_changes_a_mode` -- because the code that copies
into `/data` sits one import from the code that lifts the write bits, and the
unlock that is allowed puts them back in a `finally` an open-coded chmod would
not have.

**And the move found a comment that had been lying.** Two `#:` lines above
`class LocalSessionDirs` described "what a catalogue is made of ... the three",
an orphan left by a constant that went to `kingfisher.layout`. It had been read past
for as long as it had been there, which is what a comment attached to the wrong
definition does; deciding which of six files it belonged in is what finally
asked the question.

**Considered and rejected: splitting `config.py` by its parts.** It is 648 lines
and reads as three -- the model records (`Endpoint`, `ModelProfile`, `Models`),
the workspace paths (`definition_roots_for`, `authored_files_for`,
`WorkspacePaths`), and the deployment record itself -- with the second-highest
churn in the package behind it. That was enough to recommend the split, and the
recommendation was made on a count of top-level definitions and a histogram of
who imports what, neither of which is evidence about the work.

The hunk headers are. Of the 36 commits that have touched this file, 20 land
only in `Config`, which is the part that would stay -- and at ~285 lines it would
be exactly as long afterwards. Five land only in the model records. Five reach
two or three of the regions at once, so those commits would open more files than
they do today. The churn here is a configuration record gaining settings, and a
new setting lands on `Config` whatever else has moved out from under it.

The costs are on top of a benefit that was not there. `ConfigError` is raised by
`Models` and imported by twelve modules across every layer, `domain/` included:
it is this package's *configuration is wrong*, not a model error, so it cannot
travel with the records. That forces a `config/` package rather than a sibling
module, and a package has to re-export to keep `from kingfisher.config import
Config` working in the 40 files that write it. No subpackage here re-exported --
the root `__init__` has a table, and that is there to defer an import cost, not
to spare callers a module name. (`application/__init__` has one since, for the
same cost -- *A layer may answer for its own names, lazily*.) Moving only the records out to a top-level
`models.py` avoids all of that and buys a third thing named "models", beside
`infrastructure/harness/models.py` and `infrastructure/model_catalogue.py`.

What the file already did instead is the thing worth repeating: `Models` exists
because five fields on `Config` had invariants between them that siblings of
`shell_sandbox` could not express. That split ran along a rule, not along a size,
and it happened inside the file. *(Measured 2026-09-04.)*

## The architecture rules


**Architecture rules are mutation-tested, not trusted.** All 44 were audited;
43 held and one had lost its subject. Three of them exist *because* a rule had
stopped working silently. *(2026-08-18, `mutating-the-architecture-rules.md`.)*

**Rejected: a rule against prose naming a module that is not there.** Four stale
claims were found in one session -- the CLI's charter, a refusal naming
`access.yaml`, `pyproject.toml` citing a rule it had reversed, and
`catalogue/`'s docstring describing three modules that had left it. Three were
caught by a test and the fourth by reading, so a rule for the fourth looked
worth having. Measured before building, and it is not.

Sixty-one module paths appear in `src/` prose. Resolved the way a reader does --
sibling first, then the package root, then the tree -- twelve do not exist:
**seven are illustrative** (`vendor_a/fetch.py` is a *workspace's* layout, not
kingfisher's), **three are deliberate history** ("it *was* `subagent_middleware`
in `delegation.py`"), and **two are rot**. Ten false positives to two true ones,
separated by tense and intent, which no pattern sees. `test_no_code_cites_a_document_that_is_not_there`
works because `docs/` holds few paths and none of them illustrate anything.

What settles it: the rule would not have caught the case that motivated it.
`catalogue/`'s docstring said "`agents`, `skills`, `subagents` and `tools` are
one module each" -- backticked bare words, no `.py`, invisible to anything
matching a path. Loose enough to catch those, it would fire on every ordinary
use of the words this codebase is *about*.

A caution for whoever measures this again: a first pass counted 40 of 45
unresolved, and three of the resolutions it did find came out of `.venv` --
`deepagents/middleware/skills.py` made `skills.py` look real. *(2026-09-06.)*

**Two prose rules that do hold, and why they are not that one.** Cutting the
repository's prose introduced four defects and every check stayed green: three
docstrings left promising a list the cut had dropped -- *"the last of three parts
--"*, naming none -- and one comment citing `formats.md` by line number, which
went stale the first time that page was edited and was found four merges later by
accident.

Both shapes are now guarded, and the numbers are the whole argument for why the
rejection above does not extend to them. That rule ran 10 false positives to 2
true ones, because "does this module exist" needs tense and intent -- illustrative
paths, deliberate history. **"Does this sentence end" is syntax.** Measured
against the tree before the cutting: 2,705 docstrings, *zero* false positives,
and it catches all three. `at line N` matched twice in the entire repository, both
stale. A rule is fragile when it has to read intent, not because it reads prose.

*(2026-09-07.)*

**A prose root is any package, not only a top-level one.** `_prose_roots` read one
directory deep, so a package that moved down a level took its references out of the
pattern instead of into the failure list. Once `tools/` is not a top-level directory,
a comment saying *tools.spec* stops looking like a module path at all: the rule goes
quiet rather than red, which is the one failure it cannot report about itself -- and it
is the failure a move causes, which is when the rule is worth most.

Written the day before `kinds/` arrived, this paragraph used that example in backticks,
where it resolved. The move falsified it and the widened rule caught it on the first run
after -- the entry demonstrating itself, which is the only reason it is worth noting.

**The cost is that shorthand is refused**, and the entry above is what to read it
against. Eight of the ten references this turned up are not rot: written *harness.agent*
they resolve for any reader who knows which layer that is, and by that entry's
accounting they are false positives rather than finds. The trade is taken deliberately.
A name that resolves only by knowing which layer it sits in is the same name that goes
quiet when the layer changes, so spelling it from the package root is what makes it
checkable at all. Of the other two, one was genuine history -- *harness.skill_registry*,
named in a `HARNESS_EDGES` note saying which edge a move closed, and excused in
`PROSE_GONE` beside the deliberate mentions already there -- and one named an attribute
path rather than a module, which no rooting could resolve. *(2026-09-10.)*

## How much a comment says

**Reversed: "match the surrounding density".** The convention asked every comment
to explain why, and asked a new one to match the length of the ones around it.
The second half compounded the first. A file whose comments were long stayed
long, because writing a short one there read as an oversight, and nothing in the
rule ever pointed the other way. Measured before changing it: 53.6% of every
non-blank line in the repository was comment or docstring -- 27,889 lines of
prose against 24,124 of code, and `domain/ports.py` at 351 prose lines out of 359.

**The rule is a test now, not a quantity.** A comment earns its place only if
removing it would let a competent editor make a change that is wrong. That kept
the paragraph in `wiring.py` explaining why a factory's own exception is
deliberately not wrapped -- delete it and someone helpfully wraps it -- and dropped
the paragraph after it that says the same thing again at greater length. *(The
pass removed both in the end; the reason lives in `guides/ports.md` now, and
`test_named_store.py` holds the behaviour.)*

**Four kinds were named as always-cut**, because a test alone drifts: repo
history, a paragraph restating the one above it, framing that announces an
argument instead of making it, and a contract the signature already states. The
history ban is the one that matters, and it is the one this file already implied
-- reversals are recorded *here* so the argument is not re-run, which makes the
copy in a docstring the redundant half.

**What it did not rest on: deduplication.** The first pass assumed source prose
was largely restating `docs/`, on the strength of `wiring.py`, whose docstring
shared near-verbatim paragraphs with *Wiring a store* above -- it is one line now. Shingled against the
whole of `docs/`, that is 1.4% of source prose -- 288 lines of 21,051.
`wiring.py` is the outlier, not the pattern, and the cut had to be justified as
losing information rather than moving it. *(2026-09-07.)*

**No test guards this, deliberately**, which is the same finding as *Rejected: a
rule against prose naming a module that is not there* one section up. CI cannot
see this change at all: deleting every line of prose in the repository passes
`ruff`, `ty` and `pytest`. The floor is the keep-test applied while editing, not
a check afterwards.

## The size of the test suite

**Rejected: cutting the suite substantially. There is nothing redundant in it to
cut.** Every count below is of the tree on the date at the end, which held 2,431
tests against 89 modules -- and 32,724 lines of test against 15,331 of source,
which looks like padding twice over. Six ways of looking for the padding found
none.

*Structural duplication*, by pointing `audit_duplication.py` at the test tree
instead of `src/`: 13 findings, every one a two-statement fixture helper.
*Vacuous tests*: of the 282 with no `assert`, 252 are `pytest.raises`, 23 are
call-it-and-do-not-raise, four are genuinely loose. *Mechanical merges*, where
the 721 single-assert tests are the prize: only 108 of them, in 32 groups, share
a byte-identical arrange. The rest is hand work with judgement in it.

**Neither coverage nor kill-set can authorise a cut.** 96% line coverage is
reproduced exactly by 353 of the 1,852 tests that execute library code -- a
criterion that green-lights deleting four fifths of the suite is a rubber stamp,
and it has no opinion at all on the 587 tests that execute no library code
because they read the tree instead. Mutation kill-set is weaker: across a corpus
of 300 mutants, **89 tests preserve the entire kill-set**. Any invariant used to
choose deletions has to be held out from the choosing, or it is fitted to the
answer it was meant to check.

**What the kill-vector matrix said.** 300 mutants over covered lines, six
worktrees, 246 killed and 54 survived. Grouping tests by which mutants kill them,
with parametrize variants collapsed to the function they came from, gives 209
groups of mutually indistinguishable tests and a surplus of 751 -- the number
that looks like redundancy and is not. 632 of that surplus sits in groups
separated by fewer than ten mutants, which is thin evidence rather than sameness.
The 119 resting on wider vectors were read rather than counted: the five in
`test_skills_read_only.py` drive four different tools at four different targets
through one enforcement path. They die together because the path is shared, not
because the assertions are.

**And the evidence only gets weaker.** A finer corpus -- constants, off-by-one,
message text -- splits those groups apart; it cannot merge them. 751 is a ceiling
that falls as the measurement improves.

**Amplification is dependency-shaped, and is a reporting problem.** The median
caught break turns 9 tests red. The wide ones come from a few foundation lines:
one wrong `and` in `layout.py` fails 385 tests, of which 345 are a single
exception from a single line of the workspace backend, raised on each test's way
to an assertion it never reached. A second mutant turned 265 tests red carrying
four distinct signals. That is what `--tb=line` is for, and it took 35,495 lines
of output down to 1,415 without deleting anything.

**What was done instead** is two splits, along the seam each file already
declared in its own docstring: `kingfisher list` out of the file that also tested
`seed`, and what a turn may destroy out of what a caller can reach. No test was
deleted. The gate for a move is `pytest --collect-only`, not a green suite -- a
rule that loses its parametrize data collects nothing and still prints dots.

**The survivors, separated.** Most of those survivors were the measurement's own
fault, and finding that out took two corrections to it. It had been editing the
word `and` inside error messages, which nothing asserts and which accounted for
19 of them. And it read a timeout as "no test failed", which is backwards:
breaking `create_exclusive` so it never reports success spins the turn-id loop
forever, and the most lethal mutant in the corpus was filed as a gap in the
suite. Corrected and re-run: **279 mutants, 260 killed, 19 survivors** -- 6.8%
rather than 18%.

**Eleven of the 19 are real gaps.** The run log's `ok` on the `max_steps` path,
whose staying true is explained in a comment and checked by nothing. `top_p`
never reaching the model params, with `temperature` beside it in the same shape
and a test written for one of them. The *everything or only what was named*
branch, twice. A stray entry in a session's `runs`. A tool's own JSON on the
seeding path, which matters because JSON is valid YAML and the extension check
is what separates them. `all_of` given a mapping. The nothing-here line for
agents, whose siblings for skills and subagents were both covered.
`ensure_ascii`. The `*`-loop clause that its own comment says the message has to
carry. And `subgraphs`, on both loops.

**Six are not gaps**: one is `# pragma: no cover`, one is refused by a
`ConfigError` above it, one is a branch its comment says no definition can
reach, and two are defensive keywords that show only on a second call. The sixth
is `held_for`, which was written down as a gap and is not: a caller naming source ids
without a vocabulary, and a caller naming none with one, are both refused before
that line is reached, so no combination that can arrive behaves differently. It
was reclassified by writing the test and watching it fail against unmutated
code, which is the only way that answer comes out. **Two need Linux**, where the
fence is reachable at all, and CI's fence job is where they get settled.

The eleven are closed, in twelve tests -- two pairs share a shape, and one more
came out of the reading: the `max_steps` flag is set twice, and the async copy
sits three lines from the sampled one and was written by hand. Each was
mutation-tested rather than assumed. None of them changed `src/`: every one was
a test that was missing, not a behaviour that was wrong.

One `src/` change came out of it anyway, from pointing a test at the wrong
parser: `source_ids.yaml` read `all_of: {finance: senior}` as the single name
`finance` and discarded the rest, because a mapping is truthy and iterates. That
one is a defect, and it is fixed.

**The rate above is of covered lines, and the report walks more than those.**
The corpus behind these numbers drew only from lines the tests execute, so a
survivor meant a change nothing asserted. `mutation_report.py` walks every
mutable line in the package instead, uncovered ones included, and those always
survive -- so its rate is higher and the two do not compare. Reached this way it
is one question rather than two: whether a line is asserted, and whether it runs
at all.

**Rerunning it found the report editing prose again.** The skip named `STRING`
and `COMMENT`, and since 3.12 an f-string is neither -- it is a start, its
literal pieces, and an end -- so the fix that removed 19 survivors covered plain
strings and let every f-string through, which is how nearly every message here is
written. Third time this measurement has been wrong in a way that read as a
finding about the tests, and the first with a test of its own to stop the fourth.

**And it found something the covered-lines corpus could not.** 23 of 61
survivors were one edit: `frozen=True` off a dataclass. All 58 records in the
package are frozen, one had a test, and its docstring said "frozen, like
everything else a caller is handed here" -- true, and held up by nothing.
`test_every_record_this_package_hands_out_is_frozen` is the rule now; the
behavioural test stays as the half that watches a real refusal. *(2026-09-08.)*

If deletions are ever proposed, the gate is a mutation corpus generated *after*
the deletion list is frozen, for the reason those 89 preserved tests give. *(Measured
2026-09-08.)*

**Asked again from the other end: the suite is not over-coupled either.** The
second complaint was not the count but the blast radius -- folding `/runs` and
`.tmp` into one `/scratch` changed twelve files under `src/` and fifteen under
`tests/`, which reads as tests that know too much. Its ninety hunks say otherwise:
19 are the claim itself changing and 12 are deletions, while 44 spell a literal and
15 a renamed name. Ten of the fifteen files moved for spelling alone.

**The cure that suggests itself prevents nothing, and the number is zero.**
`layout.py` already exported `RUNS`, `AGENT_TMP`, `RUNS_ROUTE` and `SESSION_DIRS`
while about sixty test files spelled those strings by hand, so the obvious move is
to make the tests import them. It would have saved *none* of the 44 hunks: that
commit renamed the constant along with the thing it named, `RUNS` to `SCRATCH`, so
`workspace / RUNS` needed the same edit as `workspace / "runs"`. What constants buy
here is a one-token rename instead of a literal hunt -- cheapness, not absence.

**And the shape barely occurs.** Of the last sixty commits, the twelve touching the
most test files are ten semantic or additive changes -- a capability removed, an
execution path deleted, a lifecycle contract altered -- and one module move. The
tests moved because the world moved. The move is the only cosmetic one, and 13 of
its 23 hunks are `import` lines that no constant reaches; the remaining 8 are module
paths written as strings, the one place here a constant could exist and does not.

So a change costing fifteen test files is the suite reporting a blast radius, not a
suite built wrong, and the entry above is why cutting it is not the answer either.
What came of the pass is one collapse worth taking on its own merits: the parity
documents compare in one rule now rather than fourteen. *(Measured 2026-09-21.)*

**Asked a third time, at the words.** With the count still reading as too high and
the blast radius healthy, the question became which of the suite's categories earn
their place. Of 2,002 items, 1,249 drive library code; the rest is refusal text
(308), architecture (139), config and CLI (133), shipped assets (97), prose and
docs (60), and the suite's checks on itself (16). Deleting every one of those
categories lands at 1,249, so a target below that is a statement about the product
rather than about the tests.

**The wording bill is real, and it is only error messages.** Scrambling every word
of every error message in `src` -- placeholders and punctuation kept, so behaviour
is unchanged -- turns 218 items red. Scrambling every docstring turns none.
Scrambling every comment turns none. The prose rules bite on *shape* -- an
unfinished sentence, a cited line number, a module named that is not there -- not on
wording, and the shipped-asset tests are immune at 1 of 97.

**Rejected: deleting the tests that only assert wording.** 176 candidates, cut to
51 by coverage arcs and by the concrete message each `raises` block saw, and to
roughly nothing by reading the docstrings. A list where a tuple was expected once
opened an agent restricted to somebody else, and the test holding that is
indistinguishable by arc from the one beside it, because a list and a tuple take
the same branches. The docstring is the only thing that separates them. The rule
that a test's docstring names the failure it catches is what makes an automated
deletion criterion unusable here, which is the same finding as *Neither coverage
nor kill-set can authorise a cut* above, arrived at from the other side. Three
candidates carried no docstring; those need one written, not deleting.

**And the matches cannot be narrowed, for a reason worth keeping.** Of 215 prose
`match=` patterns, 51 already pin a quoted name or a syntax fragment and survive a
reword; five belong to a class raised in one place and could drop the match. The
other 159 cannot. `CapabilityError` is raised from 30 places, `ConfigError` from
34, `SubagentError` from 27 -- so the sentence is the only thing saying which
refusal fired. The suite matches on prose because prose is the error code.

**What would actually free the wording** is a discriminator in `src` that is not a
sentence: narrower classes, or a code carried on the error, after which a test pins
the code and the message is free to improve. That is a change across thirty-odd
raise sites and it is not costed here.

**The carve-out, for whoever needs it.** Six message spans reach a model's context,
all through `infrastructure/harness/`: `HostPathError` and `UnsafeReferenceError`,
plus three literals written inline in `narrowing.py`. Both classes subclass
`ValueError` and the tests guarding them catch the base, so a rule keyed on the
exception name exempts nothing. Wording is behaviour there, twice measured: told
only the virtual spelling, an agent passed it to `execute` 4 times in 10, and a
model that read "None is not a delegate this request may use" reported the tool as
broken and answered around it. *(Measured 2026-09-21.)*

**One fixture for pointing a command at a workspace.** The command reads the
environment and nothing else, so a test that drives it has to wire it, and three
lines -- the workspace, the models file, the key -- were written out eighteen times.
Each called a `_catalogue` helper that `test_list` and `test_doctor` held
byte-identical copies of. `at_the_command_line` is those three lines, and the two
copies are gone.

**The duplication was hiding a disagreement**, which is why this is more than
tidying. That helper wrote a catalogue naming one endpoint and one model, where the
`FAKE_CATALOGUE` record the `cfg` fixture holds names two and three -- so a test
that ran the command saw a different deployment from one that called the library,
and a model added to the fixture reached only half the suite. The file is derived
from the record now, and `test_the_two_forms_of_the_test_catalogue_agree` loads it
back and compares: everything but the key itself, which differs by construction
because a file names the variable to read and a record holds what was read from it.

Making them agree cost nothing -- measured before building anything, by writing the
full catalogue into the old helper and running both files green. What it would have
cost later is the question: dropping the second endpoint again now fails 33 tests.

The fixture covers that arrangement and no other. A test pointing the command at
some *other* workspace -- a fresh one, a relocated catalogue -- still says so
itself, because that is a different arrangement rather than this one written out.
*(2026-09-23, from an architecture review.)*

## Proposals, and what became of them

What is still being argued is in `docs/design/`, and `docs/README.md` lists it.
Everything else that argued here was built and its decisions moved up.

That sentence used to say how many were open and whether the folder was empty,
and it is written this way now because both were wrong for four commits -- see
below.

The heading said *Still proposed, not built* while listing only things that
were, which is the drift this page exists to catch, on the page itself.

*And it caught it again on this very section, twice over. The front-door
proposal was added to `docs/design/` on 2026-09-06 while these lines went on
saying the folder was empty, and the schema proposal arrived four commits later
and made it wronger. Nothing checks a claim like that against the directory, and
the rule that would -- grepping prose for the word "empty" -- is more fragile
than the thing it guards. So the claim went instead. The paragraph above points
at the folder and at the index, and the index is held to the folder by
`test_the_index_lists_every_document`: a sentence that cannot go stale beats a
sentence that needs a test.*

*A second one was there until 2026-08-31. `a-tool-failure-is-not-a-crash` had
shipped -- `WorkspaceToolErrors` and `tests/unit/test_workspace_tool_errors.py` --
and its status line had never been changed to say so. Its decisions are under
*Tool failure* above.*

*A fourth, `the-verb-that-runs-a-task`, was written on 2026-09-04 and removed the
same day, having been built in three slices. Its decisions are under *The command
line* above. One of them did not survive contact: it said the driver's `--list`
would go with the other three, and building it found that flag is the only way to
reach the driver's `main` without calling a model.*

*A third, `where-this-deployment-reads-from`, was written on 2026-09-02 and
removed the day after, having been built in four slices. Its decisions are under
*Where a deployment reads from* above. It corrected itself once while being
built, and that half is worth keeping in git rather than here: two of its
decisions were wrong in ways that would have shipped a fault, and the commit that
fixed them says how each was caught.*

**Not taken: splitting the build plan.** An architecture review named
`build_agent` as assembling in one pass with thin seams, and the reading went the
other way. The pass is deliberate and already recorded at the function: it was 657
lines doing four jobs, `prompting` and `infrastructure.harness.subagents` left with
a rule that neither calls back, and what remains is wiring where every statement
attaches one thing to the graph. Four decisions above constrain the shape further --
the middleware registry merged once before either branch reads it, both `wants`
sites through one builder, `test_both_kinds_are_handed_the_same_things_to_want`
guarding a second inline mapping, and one compiled agent per definition because per
path is exponential. The piece that could come out already did: `_resolve_tools`,
whose probe is 7.7ms and is skipped when nothing needs it -- no definition names a
tool, the workspace defines none, and the request asks for every tool. A workspace
with tools of its own runs it on every build.

What the review saw as thin seams is real and is one seam: 21 test files reach what
a build produced by monkeypatching `create_deep_agent` by string path and reading
its kwargs -- 16 assertions on `middleware`, 9 on `subagents`, 5 on `permissions`.
The improvement would be for the build to return a record of what it attached, the
way `_ToolSurface` already does for the tool picture inside it. Not taken because
the cost is every one of those files plus a return type 29 call sites consume, for
no behaviour change -- and because `_ToolSurface` is precedent in code with no entry
here, so this would be a new decision rather than an extension of one. Recorded so
the next review reads this instead of re-raising it. *(2026-09-16.)*

**Not taken: one reader for the agent and subagent formats.** The same review found
the two formats reading the same fields through copies kept by hand, and 14 of the 23
commits to `kinds/agents/spec.py` touching `kinds/subagents/reading.py` too. By the
time anybody read the code, most of what it named was already shared: who reaches
what and the narrowing in `declares` through `narrowed_for`, unknown keys through
`fields.unrecognised`, and every per-field rule through `fields.Reader` and
`wanted_model`. Seven of the 14 commits were renames, moves or the prose pass rather
than one change made twice.

What stays copied is each format's list of calls -- which rule reads which field,
and what leaving it out means -- with the required-field check and the fields both
specs declare. Merging that would take roughly fifty lines out of each format and
add a shared function of about the same size, with the list behind a layer of
options -- and the list is where the deliberate differences live: the star on
`skills` and `subagents`, `memory`, `bundle`, `build`.

The risk the history does show is a fix reaching one reader: refusing a list where
one model goes reached the subagent format and left the agent's turning the list into
a string. `tests/unit/test_format_parity.py` holds the readers to each other instead.
It reads the same documents through both and compares every shared field or the
refusal, tries `["*"]` on every shared key and requires the two to part on those two
fields and no others, and checks each format's keys against its spec. The agent's had
never been checked, which is why `AgentSpec` now marks its derived fields. So a field
both formats take goes into both readers and into a document there, and the test is
red until it does. *(2026-09-16.)*

**Left for now: one spec for agents and subagents.** Asked straight after the entry
above, and a different question: not one reader but one record. The two share 14
fields, and the differences are guarantees the types enforce rather than
declarations kept twice. An agent's `system_prompt` is required with no default, so no
code builds an agent without one; a delegate has a prompt or a `build`, exactly one.
`memory` is an agent's, and `bundle`, `carried` and `build` a delegate's. `declares`
parts where no audience reaches: an agent opens `models` and `endpoints` and carries
`memory`, a delegate leaves all three unset, and
`test_the_two_kinds_declare_the_unaudienced_axes_differently` exists because one
shared body would erase that with nothing going red. One class would turn each of
those into a check on a kind field, and an agent with a `build` would become
something code can construct.

Nothing downstream was asking for it either. `build_agent` reads two things off an
`AgentSpec`, the model functions in `harness/subagents.py` already take either in four
signatures, and access reads both by attribute name on purpose. The middle option --
a base class in `domain/` for the shared fields -- would write the declarations once
and put one name in place of the union. It was judged a wash: reading `AgentSpec`
would mean two files, comments that give a delegate's reasons would go neutral, and
it catches no drift `test_format_parity.py` does not already catch.

What would change the answer is a product change rather than tidying: one definition
file usable both as an agent and as a delegate. *(2026-09-17.)*

*Asked again the next day and measured rather than argued, because the entry above
asserts what the two share without counting it. Of the 14 shared fields: **all 14
declare the same type**, 13 of 14 the same default -- `system_prompt` is the one, and
it is the guarantee the entry names -- and **12 of 14 are read by byte-identical
calls**. The two that are not are `skills` and `subagents`, where a delegate passes
`refuse_all` and an agent does not, which is the star the one-reader entry above
already named as where the formats part.*

*So the mechanics do not decline it, and one of them looks like it does. A shared base
holding the defaulted fields appears to force `AgentSpec` to default `system_prompt`
too -- `non-default argument follows default argument` -- and `kw_only=True` removes
that entirely: the base compiles, the agent keeps `system_prompt` required, and
construction still fails without one. Nothing in `src/` or `tests/` builds either spec
positionally, so nothing would break. Written down because the next person to ask this
will find `kw_only` and think they have found the unlock.*

*What declines it is the prose, and that is measurable too: only 4 of the 14 comments
could move to a base unchanged -- `name`, `description` and `tools` are bare in both
and `audiences` is identical. The other 10 are the same field explained for its own
kind. `source_ids` is "who may open a session on this agent" against "who may reach
this delegate, wherever it is used"; `wanted` is the deployment's default against
whatever summoned it. A base would put 12 declarations in one file and 10 of their
reasons in another, which is the fault* Write the reason where the mistake is *names.
The measurement also found one of those comments stale -- the agent's `wanted` still
described the list form, which `wanted_model` stopped taking -- and a shared field
whose two comments disagree is the case for keeping both, not for merging them.
*(2026-09-18.)*

**Taken, in the middle form: `Definition`, a base holding what both kinds declare
identically.** The entry above judged that option a wash and the measurement after it
did not overturn that. What changed is two things neither had, and both came out of
building it rather than arguing about it.

**`kw_only=True` is the whole of the mechanism.** A base holding defaulted fields makes
a required one in a subclass illegal -- *non-default argument follows default argument*
-- which reads like the structural objection to all of this and is not one. With it,
`AgentSpec` keeps `system_prompt` required and constructing one without a prompt still
fails. Nothing builds either spec positionally, so nothing had to change to allow it.

**And a shared comment turned out to be the point rather than the cost.** The objection
was that ten of the fourteen comments are kind-specific and would go neutral. They do
not: where the kinds part, the one comment now says which is which -- an agent may write
`["*"]` for `subagents` and a delegate may not, because for a delegate that set includes
itself and is always a loop. A reader meets that difference in one place for the first
time. Two comments each knowing half is how the agent's `wanted` sat stale for months
with nothing red, and a merged comment cannot drift because there is one of it.

**One spec is still refused, and the reason for it changed in this same work.** Two of
the three hold: `_subject` tells the kinds apart by `isinstance` to name the file a
refusal is about, and each kind has an invariant the other must not run. The third is
void -- `system_prompt` is required on both now, so merging no longer makes a promptless
agent constructible, and anyone re-asking this should know that argument has been spent.

What replaced it is stronger and is a test rather than a judgement.
`test_the_known_set_matches_the_spec_it_builds` asserts *equality* between a format's
`KNOWN` and the non-derived fields of the spec it builds, in both directions, and its
docstring says what that catches: a key accepted and never read. One class makes the
spec's fields the union of both kinds', so the equality fails for each and the check can
only weaken to a subset -- which stops catching the thing it exists for. The argument
about a spec built in code saying what no file may say does not go away with
`system_prompt` either; it moves to `memory`, `build`, `bundle` and `carried`, where a
delegate could carry a `memory` nothing reads.

A base keeps all of that; one class turns each into a check on a kind field. The readers
are untouched -- that is *Not taken: one reader* above, and nothing here reopens it.

**What it costs, accepted rather than argued away**: reading a spec is two files, and
ten comments that were true of one kind now have to be true of both. `system_prompt` is
declared in both subclasses at first, and joined them once the question "why do the
defaults differ?" was asked rather than worked around. *(2026-09-18.)*

*Fourteen, not thirteen: `system_prompt` unified by losing its default rather than by
sharing one. The two arrived at their defaults from opposite directions -- an agent has
no second shape, so `parse` refuses a definition without a prompt and a default would
be the second way in that field exists to refuse; a delegate does, because a compiled
one carries its instruction inside the graph. Required on both is the stricter reading
of each: the one caller that builds a compiled delegate now writes `system_prompt=""`,
which says* this one has no prompt *instead of leaving it to a default nobody reads, and
`__post_init__` can still tell "brought a graph" from "said nothing". What counts as
acceptable stays per kind.*

*Doing it found the guarantee untested. Putting the default back on the base makes a
promptless agent constructible and all 1,959 tests pass -- `parse` refuses a document
that omits it and always did, and nothing was watching the other door.
`test_neither_kind_can_be_built_without_saying_what_it_instructs_with` is that door,
and it is the one part of this work that would have been worth doing on its own.*

**Left for now: a `ToolSpec` a tool could be declared as.** Asked after the entry
above, about the kind that has no spec at all. A tool is exported as an object and
`Found` pairs it with where it came from; agents, subagents and skills each read a
document into one. Feasible was checked by running it rather than reasoned about:
`StructuredTool.from_function(fn, name=, description=, metadata=, args_schema=)`
builds a real `BaseTool`, so a declared form translates in one call.

It has nothing to carry. `BaseTool` already holds `name`, `description`,
`args_schema`, `tags` and `metadata`, so a spec would restate the object. The one
thing it could hold that the object cannot is an audience, and that is settled the
other way: a tool's audience is a property of the use rather than of the tool, with
the cost named in `docs/guides/formats.md` and mitigated by the roll-up in
`kingfisher list`. What is left is `name`, `description` and the callable, which
`@tool` handles.

It is not a rename of `Found` either. `Found.source` is the one fact the object
cannot know, and it is the whole of what `Found` adds.

The ergonomics are genuinely bad and that is the argument for building it later:
`@tool` refuses `metadata=`, `extras=` lands on a different attribute and leaves
`metadata` as `None`, and the working route is assigning after the decorator. The
first time kingfisher reads `.metadata`, a workspace that wrote `extras` gets no
error and no value.

**The trigger is the first field kingfisher wants to read off a tool** -- whether one
writes rather than reads, what it costs, a timeout of its own. None is designed and
none is asked for. Before that, a spec is a second way to spell `@tool` and every
`Found` consumer pays for it; after it, the need says what shape the field is and
there is a caller. Whoever builds it should pick one of `metadata` and `extras` and
refuse the other, so the trap is decided once rather than by every workspace.
*(2026-09-18.)*

**Left for now: a `TypedDict` for a package writing `SUBAGENTS`.** A portable
definition is a plain mapping on purpose -- it pins no kingfisher version -- and the
want behind this is type checking, not a different runtime shape. A `PortableSubagent`
and a `CompiledSubagent` published under `TYPE_CHECKING` would catch a misspelled key
before a run does, with no runtime dependency.

Not built, and **the trigger is the first package that is not ours**: nothing outside
this repository exports `SUBAGENTS`, so there is no author to help and no usage to
check the shape against. The cost is also the one *Keep a subagent's vocabulary beside the spec*
is about -- the fields would be a fourth statement of a vocabulary `KNOWN`, `PORTABLE`
and `NOT_PORTABLE` already state three times, so it needs a line in the drift test to
stay honest.

**Accepting `SubagentSpec` instances is refused rather than deferred**, which is the
difference between this entry and the `ToolSpec` one above it. Six of its fields are `derived`
and the reader fills them: a hand-built spec writing `tools=("csv_profile::profile",)`
gets an empty `tool_sources`, so `Offering.refuse_moved` checks nothing and a stale
path claim that a mapping would have caught passes in silence. Publishing the type
would also put a name through the front door under a rule that has removed eleven.
*(2026-09-18.)*

**Taken after all: a build says what it attached.** *Not taken: splitting the build
plan* above declined this, and the decline was right on the evidence it had. What it
lacked was a benefit: it weighed the cost against "no behaviour change", and a
rearrangement that changes no behaviour is not worth 21 files. The benefit arrived
from the other direction. Six functions were defined inside `build_agent` and no test
could call any of them, while `declared_middleware` -- which left this file under the
same criterion -- is called directly by twenty-six. Three of the six came out first;
this is the other half, and `build_agent` returns `Assembled` rather than a graph.

**The cost that entry quoted was an undercount, and the corrected figure is the
useful part of this one.** It said 21 test files and a return type 29 call sites
consume. Measured before starting: 22 files reach the spy, 34 files call
`build_agent` at all, and 58 of 148 call sites consume the return -- of which two are
in `src/`. The rest is tests, which is the shape that made it affordable: the tests
were being edited anyway, because deleting the spy is the point.

**What `capture_build` was is why it had to go.** It monkeypatched
`create_deep_agent` by string path and read the kwargs back, which made
`create_deep_agent`'s argument list a contract that nothing declared and that three
separate helpers restated by hand -- `_shipped_provisions`, and the hand-built
mappings in `test_middleware_settings`. `Assembled` holds every keyword, not the ten
a test happens to read: `checkpointer` is asserted on by nothing, and `interrupt_on`
was asserted on by nothing until the commit that added it, which is exactly the case
a record of only-what-is-interesting fails on the next time.

**`_graph_for` returns two shapes, and that is the design rather than an oversight.**
This was settled the other way first -- the service would unwrap and `Assembled` would
stay inside the harness -- and it was reopened during the work, when three tests
turned out to be unwritable under it. One asserts that what the backend factory
returned is what the agent was built on, and says in its own docstring that it reads
the arguments because *a service that computed the backend and then dropped it would
satisfy every other test in this file*; the other two need the middleware list. All
three are about the service's wiring, so reproducing a direct `build_agent` call in
them would not weaken the tests, it would delete them. So a build kingfisher made
comes back as `Assembled` and a graph the deployment supplied comes back as itself.
Returning the record for both would mean inventing one with every field empty -- a
record asserting a build that never ran, and one that would then be asserted on.
`_admitted` is the only caller in `src/` and unwraps with `isinstance`, not
`getattr(x, "graph", x)`: a duck test there would accept anything carrying a `graph`
attribute, which is how the backend seam lost its shell once already.

*Mutation-tested, and one of the two mutations is weaker than it looks. Making the
record report a backend the graph never got reddens exactly one test -- the factory
one above -- which is the drift guard working: `assemble` builds one mapping and
spends it twice, on the call and on the record, so the two cannot disagree. Dropping
`interrupt_on` from the record reddens all six gate tests rather than the two that
assert on it, because the field's absence breaks construction for any build that
gates. That proves the field is load-bearing and proves nothing about the individual
assertions, which is worth saying rather than counting it as specificity it does not
have. (2026-09-22.)*

*`nothing-at-rest-on-this-machine` went on 2026-09-04 -- several more have gone
since, and `docs/README.md` names them -- and its removal is the sharpest example
this file has of why a status line is not evidence. It was audited decision by decision on 2026-09-01 and still reported
"no store port in `domain/ports.py`" -- six days after `SessionStore` and
`SessionRoot` shipped in #254. It was describing a codebase that had moved
underneath it. Its decisions are under *Sessions: what persists and where* above
and its measurements in `findings.md`; what stayed unbuilt is what it deferred on
purpose -- admission control, the run log ceasing to be a file -- plus mirage,
which it recommended against.*

