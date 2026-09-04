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
`seed(into, source)` takes it as a required argument with no `None` branch. This
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

## The definition format

**An entry is a name, or a mapping of `name` and the one thing that field lets a
name carry.** `groups` for `tools`, `skills` and `subagents`; `settings` for
`middleware`. One long form, whichever field it belongs to, read through one
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
*(2026-09-03. The `all_of` spelling nests one level deeper under it, which is the
one place the old form read better and was accepted knowingly.)*

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

## Group access

**An audience lives in the definition it is about.** An agent or subagent writes
`groups:` for who may reach it, and an entry of `tools:`, `subagents:` or
`skills:` may be written long -- `{name: X, groups: [...]}` -- for who reaches
that one. One central
file, `groups.yaml`, holds the vocabulary and `contains` -- names only, no policy.
An audience resolves into an ordinary `Capabilities`, so nothing downstream
changed: an ungranted tool is never attached to the graph and an ungranted
subagent is never compiled. *(2026-08-31.)*

The rule that makes it safe to add to an existing definition: **`groups:` is the
default audience for everything the definition holds, and the ceiling on what any
entry may say.** So an omitted or plain-list `tools:` keeps its exact meaning, and
an entry is always an *and* with the definition's own line -- the only way to
reach an entry is through the definition holding it, so naming a group from
outside that line adds a second requirement rather than replacing the first. A definition with no `groups:` line is reachable by everyone, and startup
names every such definition -- default-open must not also be silent.

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
once and reused the handle; now every call takes `groups=`. Counted before
removing: zero production callers. The service -- the only consumer that serves
several callers, which is the case the handle was built for -- resolves groups
per request from a header and passed `groups=` at all seven of its call sites.
Two of the handle's three stated benefits did not survive checking either: the
grant it resolved once measures **0.81 microseconds**, because the transitive
closure is computed when `groups.yaml` loads; and refusing an unknown group "at
the boundary rather than at the first turn" is a gap of one call, since `expand`
still refuses before any agent is built. What was real was the script
ergonomics, and one thing that was not a benefit at all: `held_for` tested
`isinstance(groups, tuple)` and read anything else as *no opinion*, so the
coercion inside `for_groups` was the only reason `["A"]` ever narrowed. That
moved into `held_for`, which now takes any sequence and refuses a bare string.
*(2026-09-03.)*

**An audience list is an `or`, and an entry of it may be an `and`.** `all_of`
requires a caller to hold several groups at once, and is written two ways
deliberately: named in `groups.yaml` for anything reused, inline as one entry of
a list for a one-off. The same word both places, so the named form is literally a
*name for* the inline one rather than a second mechanism -- the argument against
two spellings was drift, and using one word with one evaluation is what answers
it. The result is or-of-ands, which is the shape access rules take, out of one
field with no rule about how two fields combine. *(2026-09-01.)*

`expand` does the work, which is what kept `reaches` nearly unchanged: `contains`
closes first, then a compound joins the held set once its parts are held, so a
named compound is an ordinary held name by the time any audience is asked. Two
consequences follow rather than being chosen. **`contains` satisfies `all_of`**,
because expansion runs first -- the alternative is an `admin` who contains both
parts yet is weaker than the sum of what they reach. And **nesting works for
free**, since a compound whose parts are held is held.

**A compound is derived, and nothing may hand it over directly.** A caller may
not present one: it is what holding the parts adds up to, not something to
claim, and accepting it would let one assertion stand in for the two that
`all_of` exists to require. The refusal names the parts. Over HTTP this surfaces
as the `misconfigured` 500 a drifted vocabulary already gets, which is exactly
what a gateway emitting a derived name is.

`contains` may not hand one over either, and that is the same rule rather than a
second one. `admin: {contains: [finance-senior]}` was legal for one commit and
gave an admin the compound while they held neither part -- the requirement
defeated by the file declaring it, which is the caller's move made one level up.
Refusing it in only one of the two places was the inconsistency. Naming the
parts reaches the same people and is visible, since the listing prints what a
compound requires and never prints `contains`.

**Reversed: `refuse_dead`, the rule that an entry audience must overlap its
definition's.** It moved off `parse` onto `Groups` first, which was a real fix
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

What survived is the looking. It is `Groups.narrowing_in` now, and feeds
`AccessReport.narrowed`, on the same reasoning as the `unrestricted` line beside
it: a thing worth noticing, said once, where an operator sees it. The typos it
was really catching are `refuse_undeclared`'s, which refuses them by name.

One consequence worth keeping: **the ordering question went with it.** A
misspelling used to trip both checks, and `never reaches anyone` explained it as
a reachability problem without mentioning the spelling. There is now one refusal
on that path.

**`groups.yaml` now holds vocabulary with a rule in it**, and the file's pitch
was "a dictionary, not a policy". A compound sits on that line: it still answers
"what does this name mean", but answers it with a condition. Judged to stay on
the right side -- what it cannot do is say who reaches what, which is the
property that made the central design fail. Recorded because it is cheap to
disagree with now and expensive later.

The `--json` listing's `access` key grew from a name-to-closure mapping into
`{names, requires}`. A shape change for scripts, taken because a compound has no
honest place in the old shape and the alternative was dropping the second fact.
Group access was two days old at the time.

**`builtin_tools` takes no audience, and that is not an omission.** deepagents
registers its own tools, so kingfisher can filter them but never leave them out
of a graph -- `harness/narrowing.py` records a live run where a model called
`execute` from memory. Gating them here would promise a boundary it cannot keep.
What gates them is which *agents* a group may open, since an agent declaring a
read-only builtin set cannot yield the shell to anyone.

**Out of reach reads as not offered.** An asset a caller's groups do not reach is
absent from listings, from the "this workspace offers ..." in a refusal, and from
the report of what a run withheld -- so nothing lets a caller enumerate the
catalogue by guessing. The withheld report hides only what group narrowing
removed, never what the agent simply never declared: the second is a fact about
the agent and has always been reported.

**The HTTP surface asks who is calling; it still authenticates nobody.**
`create_app(groups_from=...)` takes a callable given the request and returning
group names, and `from_header` is shipped but never defaulted -- the header is an
argument so that trusting one is a line somebody wrote rather than what happens
when nobody decides. A deployment whose policy and identity disagree refuses at
startup, in both directions: a vocabulary with no source cannot serve a single
request, and a source with no vocabulary is a server somebody believes is locked
down and is not. *(2026-09-01.)*

A source may return group names and nothing else. **`UNSCOPED` is unreachable
over HTTP**, because it exists to be a value a person types at a call site they
can see -- reachable from a request it is a value a *bug* can produce, and one
returned on a parse failure hands every caller everything at once. "Reaches
everything" is already a group that contains the others, which is declared and
visible in the listing where an unscoped run would never appear.

**The caller gets a code, the log gets the reason**, and it is one rule at every
refusal. A session whose pinned agent a caller cannot reach answers 404
`unknown_session` -- the same code a wrong id gets, so holding a real one teaches
nothing. A group the vocabulary does not declare answers 500 `misconfigured`
with a body naming no group, while the message that lists them all goes to the
service logger. Reading and deleting a session are checked like running one: a
session you cannot run is one you cannot touch.

**`errors.STATUS` stays exactly the caller-facing set.** A deployment error that
still deserves a name goes in `DEPLOYMENT_STATUS` beside it, disjoint and tested
as such -- the first table's value is that it is checkable in both directions,
and an entry a caller cannot cause would be a status nobody decided on.

**The file `seed` tells you to write has an example beside it.**
`groups.yaml.example` ships in the package and `ensure_layout` places it, the
same as `models.yaml.example`. The reasons differ and the code says so rather
than implying the files are alike: `models.yaml` is required with no fallback,
while `groups.yaml` is optional -- with no policy file kingfisher controls
nothing by group, which is the right default because adopting access control
should be a thing a deployment does rather than one it inherits. What makes the
example furniture is not that the file is required but that **`seed` names it**:
a definition asking for an undeclared group is skipped with a message saying to
declare it, and that message used to name a file no example of existed anywhere
an installed deployment could reach. `examples/groups.yaml` is the worked set
for the shipped agents and lives outside the wheel.

Still not seeded. `groups` is not a definition kind and `.example` never becomes
`groups.yaml`. *(2026-09-02.)*

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

The half that survives: a bare invocation of the driver spends real money on the
smoke, which is a fine default for a driver and a wrong one for a stranger's
first command. A verb with a required task argument cannot be reached by
accident. *(2026-09-04, `the-verb-that-runs-a-task.md`.)*

**`run` takes six flags, and the eight that narrow capabilities are not among
them.** `task`, `--agent`, `--session`, `--input`, `--data`, `--as`. Narrowing
what a request may activate is a deployment's concern with two better homes --
the service clamps with `grants`, an agent file declares what it holds -- and
`--without-*` freezes what the workspace offers *now*, which is a subtlety for
somebody wiring a service rather than running a first task. The cost is that you
cannot say "without the shell" from the command line; write an agent that
declares it.

`--data` was never a candidate for cutting: `/data` is read-only to the agent,
so it is the only supported way to hand one a file at all. Nor was `--as`,
measured rather than assumed -- on a workspace declaring groups, a run that
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

## Where a deployment reads from

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
the service read this, so "nothing is configured" and "you handed me a store"
must not arrive as two spellings a consumer has to match on.

**`Config` remembers where it looked for `groups.yaml`.** The path was read,
used for error-message prefixes and discarded. It sits on `Config` rather than on
`Groups` because of the absent case: with no file there is no record to hang a
path on, and "not set, and here is where I looked" is the one line that makes a
policy written one directory off visible at all -- otherwise the deployment comes
up reachable by everyone and says nothing.

**The library's first logger is `kingfisher.origins`, and not `kingfisher`.**
One INFO record per construction, and that is the whole budget. `print` is not an
option -- a library that writes to stdout cannot be used by a server -- and
`warnings.warn` means "this is probably not what you meant", which a summary is
not. The name is the load-bearing part: `kingfisher.audit` is left unconfigured
so that writing session ids stays a deployment's decision, a logger named
`kingfisher` is its *parent*, and the server raises this one to INFO -- so asking
where the definitions live would have turned the audit trail on. A test in the
service holds the two apart.

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

## Sessions: what persists and where

These began as decisions in *Nothing at rest on this machine* and were built
without the rest of it, which is what that document's N2 asked for -- the parts
that need no memory-backed filesystem, first and separately.

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

The three things the old per-session sqlite bought all survive by another route,
which was measured rather than assumed. A conversation deleted with its directory
(one workspace held 132 orphaned threads after every session had been reaped), a
conversation the quota can see, and no cross-session contention (at 32 concurrent
writers the slowest went from 363ms on a shared file to 80ms on its own). The
transcript is a file in the session, so the first two hold; nothing is shared, so
the third has nothing to contend for. What is genuinely gone is ~20KB of empty
database per session, which was the cost rather than the benefit.

**`doctor` checks whether a memory-backed workspace can be filled safely.**
`workspace_fs.py` reads the filesystem type, its size, the cgroup limit and
whether swap is permitted; `presentation/cli/health.py` reports on them. The
danger it names is specific: a memory filesystem *larger* than the container's
limit does not fail when it fills -- the kernel swaps its pages out, which is
data at rest, arrived at silently, with the write succeeding and no error
anywhere. Only a filesystem smaller than the limit gives a clean `ENOSPC`.

**The run log never crosses the wire.** `run_dir` and `log_path` are typed `Path`
in the domain so `json.dumps` raises rather than quietly stringifying them, and
`service/payloads.py` is the one place that knows to leave them behind. A
mirrored pydantic model would be a second home for that rule, and the kind that
gets it wrong helpfully -- adding a `Path` serialiser makes the error go away and
ships exactly the leak.

**The session quota is checked between turns and never during one.** This reverses
what *Nothing at rest* argued: N11 said the bound could be metered on the
tool-call hook because kingfisher already wraps every tool call. It cannot.
`execute` writes without any file tool seeing it, so a turn already running can
exceed the bound and only a filesystem quota underneath could stop it. What the
check prevents is the next turn making it worse.
*(All 2026-08-21 as `nothing-at-rest-on-this-machine.md`, N11 and N13 to N16, N20
and N22; built separately from the rest of that document, which is still a
proposal.)*

## Layering

**`infrastructure/harness/` holds every module that imports deepagents, langchain
or langgraph**, and only that package may. The rest of the layer stays flat -- a
second subpackage would advertise a distinction no test could hold. (This said
"the other thirteen modules"; it is eleven now, and the count was never the
point.)
Registries and DTOs did *not* move to `application/`, and the package root did
not change. *(2026-08-17, `layer-boundaries.md`.)*

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

**Architecture rules are mutation-tested, not trusted.** All 44 were audited;
43 held and one had lost its subject. Three of them exist *because* a rule had
stopped working silently. *(2026-08-18, `mutating-the-architecture-rules.md`.)*

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

- [**Nothing at rest on this machine**](design/2026-08-21-nothing-at-rest-on-this-machine.md)
  -- no session data on local disk, reached through a door kingfisher does not
  look behind. Several of its claims are still unmeasured, and it says so.
