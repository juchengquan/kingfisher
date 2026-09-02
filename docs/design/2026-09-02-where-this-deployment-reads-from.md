# Where this deployment reads from

**Status:** proposed, and built on four branches off `main` -- none of them
merged. It stays here until they land, because from `main` this is still an
argument rather than a description. When they merge, its decisions belong in
`docs/decisions.md` and this file goes, per the rule in `docs/README.md`.
**Date:** 2026-09-02
**Corrected 2026-09-02, while building it.** Four decisions below were wrong or
imprecise as written and are fixed in place; *What building it changed* at the
foot records each one and why. Two of them -- the logger's name and the
reachability of a `doctor` check -- would have shipped a real fault.

Kingfisher reads from about eleven places. Nothing can tell you what they are.

`kingfisher list` prints four of them, `doctor` prints one, the library prints
none, and the three answers are assembled separately by code that does not know
about each other. This proposes one record that answers the question once, and
four surfaces that print it instead of each printing its own subset.

## What is visible today, counted

`kingfisher list` opens with a header of four lines: the workspace, and the
agents, skills and subagents catalogues. **Tools is not among them** -- there is
no `tools_source` on `Inventory` and no line for it in `render`. Three of the
four catalogues are named and the fourth is not, which is not a decision
anybody made; the field was simply never added.

`doctor` names one path, the seed directory, in `_packs`. Its catalogue checks
report counts -- "12 in the workspace, 14 built in" -- and never say which
directory that is. A diagnostic that cannot name a single folder is a strange
thing to run when a folder is what you are unsure about.

The library says nothing at all. There is no summary to print and no attribute
to read, and the obvious move is actively harmful: `Config` is a plain frozen
dataclass and `Endpoint.api_key` is an ordinary field on it, so `print(cfg)`
puts a credential on the terminal.

Two paths are lost rather than merely unprinted:

* **`groups.yaml`.** `access_policy.load` takes the path, passes `path.name` to
  `domain.access.parse` for error-message prefixes, and discards the rest. When
  there is no file, `cfg.access` is `None` and there is nothing anywhere that
  remembers where kingfisher looked. This is the single most useful line the
  record could carry -- a `groups.yaml` written one directory off leaves a
  deployment silently open, and the symptom is nothing at all.
* **A relocated catalogue that is empty.** `resolve_definitions` creates derived
  roots (`path.mkdir(parents=True, exist_ok=True)`), so a mistyped
  `KINGFISHER_SKILLS_DIR` produces the directory it names and an empty
  catalogue. `doctor` then reports `ok  skills  0 loadable`, which is also what
  a correctly configured fresh workspace reports. The two states are
  indistinguishable and the remedies are opposite.

`Models` is the one thing that already does this properly: it carries
`source: Path | None` and `resolve()` uses it to say which file should have
defined a name it could not find.

## The change, stated plainly

One frozen record, `Origins`, built by a classmethod, holding one `Origin` per
place kingfisher reads. `Kingfisher` exposes it, `Inventory` carries it, the
CLI prints it, and the library logs it once at construction.

## Decisions

**O1. One record, covering everything, not just the catalogues.** The four
definition directories, `models.yaml`, `groups.yaml`, the seed directory, the
state and scratch directories, and the session store. The alternative -- naming
only the catalogues -- leaves three surfaces still disagreeing about what is
worth saying, which is the fault being fixed rather than a smaller version of it.

**O2. It reports what was loaded, not what was configured.** `Config` names the
*fallback* catalogue roots; `Kingfisher` may be handed a `Definitions` or a
mapping that overrides them, and `catalogue_root` answers `None` for a
repository with no directory behind it. A record derived from `Config` alone
would be right for the simple deployment and quietly wrong for the one that
moved something, which is the deployment that needs it.

**O3. Each entry says what kind of thing it is, not just a string.** Five kinds:

| kind | prints as | means |
|---|---|---|
| `default` | `./skills` | the **derived** location -- where kingfisher would put it having been told nothing |
| `relocated` | `/opt/shared/skills` | any other configured path |
| `overridden` | `/opt/staged/skills(overridden)` | **not what the config says** -- supplied at construction |
| `supplied` | `<supplied>` | a repository with no directory |
| `unset` | `unset(./groups.yaml)` | not configured, and where it looked |

`default` is decided by comparing against the derived location, *not* by asking
whether an override was set. The difference shows on a deployment that names a
path equal to the default: that is `default`, because it is. The other reading
would fire O11's relocated-and-empty warning on every fresh workspace whose
operator happened to be explicit, which is what would make it noise.

No spelling contains a space. The startup line is `key=value` pairs separated by
spaces, so a space inside one stops `grep tools=` from answering -- which is the
property that made every key worth printing rather than collapsing the ordinary
ones.

A bare string would make `"the catalogue"` -- which `source_of` returns today --
a magic phrase a consumer has to match on, and would leave `unset` and
`supplied` indistinguishable. The service and `--json` both read this, so the
distinction has to survive serialisation.

**O4. `overridden` is decided by comparison, not by a flag.** `Kingfisher` does
not remember whether it was handed a catalogue, and it does not need to: the
resolved root and `cfg.catalogue_roots` are both to hand, and a difference
between them *is* the fact worth reporting. Threading a flag through
`resolve_definitions` would record how the override happened, which nobody
needs, at the cost of a parameter that exists for reporting.

A mapping supplied that happens to match the configuration reads as
`default`/`relocated`. The two are indistinguishable and equivalent, so there is
nothing to report.

**O5. `Config` remembers where it looked for `groups.yaml`.** A new
`access_source: Path | None`, set from the path `application/config.py` already
computes at line 150 and currently discards.

Not on `Groups`, and the reason decides it rather than merely favouring it: when
there is no file, `cfg.access` is `None` and there is no record to hang a path
on -- so option "`Groups.source`" cannot express *the case the field exists for*.
It would also put a filesystem path on a `domain/` record whose own docstring
says "Pure, like the rest of `domain/`: this module reads no file."

`Config` already carries a path for exactly this purpose: `assets` is on both
`Config` and `WorkspacePaths` "because `doctor` is handed a whole `Config` and
has to report on it".

**O6. The library logs one line, at INFO, at construction.** Its first logger,
named `kingfisher.origins`, and that is the whole budget -- one record per
`Kingfisher`, not a habit.

`kingfisher.origins` and deliberately not `kingfisher`, which is what this said
first. The service already has `kingfisher.audit`, and its own comment is the
argument: "unconfigured. Nothing is written until a deployment attaches a
handler, which is how 'may session ids be written here' stays a decision
somebody makes rather than a default they inherit". A logger named `kingfisher`
is that one's *parent*, and O8 has the server raise this one to INFO -- so
asking where the definitions live would have started writing session ids. A
sibling cannot, and a test in the service holds the two apart.

Python logging is silent until an application configures it, so no existing
caller sees new output. This is the only mechanism that satisfies "clearer when
it starts" without breaking the rule stated twice in this codebase: *a library
that writes to stdout cannot be used by a server*. `warnings.warn` is the wrong
instrument -- it is for "your configuration is probably not what you meant", and
a startup summary is not that.

**O7. Every key appears, and paths under the workspace print relative.**

    kingfisher reading from: workspace=/srv/ws agents=./agents skills=./skills
    subagents=./subagents tools=/opt/shared/tools models=./models.yaml
    groups=unset(./groups.yaml) seed=/repo/examples state=./.kingfisher
    scratch=./.kingfisher/tmp sessions=unset

Spelled out in full this is about 450 characters, nine of eleven values sharing
one prefix. Relative brings it to about 240 *and* makes the relocated entries
the only absolute paths on the line, so the eye finds them without reading.

Collapsing the ordinary ones to `catalogues: default` was considered and
rejected: it deletes the path somebody is looking for, and `grep tools=` stops
answering.

Lazy `%` formatting, as `access.py` already does, so nothing is built when the
logger is off.

**O8. The service logs it; it does not serve it.** `kingfisher_service` has no
authentication by design -- "this server does not know who is calling" -- and no
informational endpoint at all. A route handing out filesystem paths would be the
first, on a server whose deployment instructions do not mention needing to block
one. Its `__main__` already raises two loggers to INFO at startup; this adds a
third name.

`kingfisher list --json` still emits the whole record. That is a command run on
the host, which is a different exposure from an open port.

**O9. No JSON from the library.** `audit.py` logs `json.dumps(...)` because it is
machine-consumed by design. A startup line is read by a person, and `--json`
already covers the machine case. Two JSON spellings of one record is the drift
this document exists to remove.

**O10. `Inventory` carries an `Origins` and drops its three loose fields --
and its own `workspace`, and `render`'s `workspace` argument.**
`skills_source`, `subagents_source` and `agents_source` become `found.origins`.
Counted first: two production call sites, both in `presentation/cli/listing.py`
(the header, and `as_json`); four lines in `tests/unit/test_inventory.py`; zero
in the service, which does not import `Inventory`.

This changes `kingfisher list --json`: three top-level keys leave and a nested
`origins` object arrives. The field-for-field test at `test_cli.py:398` still
passes, because it compares document keys to dataclass field names and that
stays true. Nothing external breaks -- the distribution is at `0.1.0` with
`description = "Add your description here"` and has never been published, which
makes this the cheapest moment the change will ever have.

**O11. `doctor` prints the same header, from the same renderer, and gains two
checks -- and `examine` takes the inventory rather than building one.** The record states; the checks judge; neither does the other's job. The
seed check keeps all four of its branches, including the paths in its warnings --
a warning has to read correctly when it is the only line somebody sees.

* **A `relocated` catalogue that is empty** -- warn. Legitimate while staging, and
  the likeliest way to end up with nothing. `default` and empty stays silent:
  that is a fresh workspace, and `SEED_HINT` already covers it.
* **An `overridden` catalogue** -- warn. A deployment where `KINGFISHER_SKILLS_DIR`
  is both set and ignored is one where somebody will edit that variable and
  watch nothing change.

Warnings, not failures, in both: `worst()` already records why a check that
fails on a deliberate choice is a check nobody runs.

The second check is unreachable unless `examine` accepts an `Inventory`. It
built its own, from `cfg` -- and a catalogue resolved from `cfg` agrees with
`cfg` by construction, so nothing it examined could ever be `overridden`. The
deployment being checked was a fresh guess rather than the wiring that is
running. `examine(cfg, found=None)` fixes it, and the command building the
inventory once and handing it on is also what makes the header and the checks
read one object instead of two.

`doctor --json` becomes an object with `origins` and `checks` where it was a
bare list of checks. The two forms of one command must not show different
things, and a script checking a deployment's health wants the paths in the same
answer as the verdicts rather than from a second command.

**O12. `Origins` and `Origin`, not `Sources`.** `Source` is taken, and by the
opposite concept: `seeding.Source` is *where definitions are copied from*, and
this record is mostly about where they are read. `source` is already four things
-- `Models.source`, `Inventory.tool_sources`, `source_of()`, and that protocol --
and a fifth meaning is how a word stops carrying any. `Layout` is taken by
`domain/layout.py`. `Origins` collides with nothing, is the plain-English
register the rest of these names are in, and pairs with `Inventory`: one says
what a workspace offers, the other where it came from.

The log line says `reading from:` rather than `origins:`. A type name and a
sentence are not obliged to be the same word.

## Considered and rejected

**Opening in-code configuration as the first-class path.** `Models`, `Endpoint`
and `ModelProfile` are constructible and the test suite wires a `Config` that
way, but they are not exported and `test_a_consumer_uses_the_library_only_through_its_public_api`
closes that door to consumers. Exporting them was proposed and dropped: setup
stays YAML and directories, and this document is about making that setup legible
rather than replacing it. The reviewability of `models.yaml` -- credentials by
variable name, a file that can go through code review -- is the property that
would have been traded away.

**Merging `WorkspacePaths` into `Config`.** `Config` requires a `Models`, and
`models.yaml` lives inside the workspace, so `config_from_env` raises before the
directory it would seed exists. Merging means making `models` optional, which
takes `Models.resolve()` from total to partial and puts a `None` branch in every
caller. The polymorphism the merge was reaching for is already here and
structural: `seeding.Destination` and `seeding.Source` are satisfied by both
records, by shape.

**Nesting `WorkspacePaths` inside `Config` to end the six duplicated fields.**
Measured: 7 direct `Config(...)` constructions and 38 reads of `cfg.workspace`,
both cheap -- but 88 `replace(cfg, ...)` calls, of which 17 touch a path field
and would become a nested `replace`. Removing a duplication that
`definition_roots_for` already guards, in exchange for the most common mutation
idiom here getting worse at 17 sites, is the wrong direction.

**A `Config.paths` property.** Proposed as the cheap middle ground and dropped
before this was written: with `Origins.of` taking a `Config`, nothing would read
it. It would also be a second spelling of `cfg.workspace` and a copy that goes
stale across a `replace`. `skills_dir`'s own docstring records what happens to
properties kept because the set looked symmetrical.

**A second constructor taking `WorkspacePaths`, so `seed` gets a record.** A
record with `models` and `groups` blank is worse than none -- a reader cannot
tell "unset" from "not loaded yet". `seed` answers a different question anyway:
where it is copying *from*, which it already prints.

## The order to build in

Four slices, each green, each its own pull request off `main`. Sequential, not
stacked -- this repository rebase-merges.

Each depends on the one before it, including slice 3, which was planned as
depending only on slice 1. So they land in order, each rebased onto `main` as
its predecessor merges.

1. **The record and the Python surface.** `application/origins.py`;
   `Config.access_source`; `Kingfisher.origins`; both names exported and added
   to `LIGHT_EXPORTS` in `test_architecture.py`. Nothing prints. New tests cover
   one case per kind, and the two states nothing can express today: a groups file
   that is absent still names where it was looked for, and a catalogue handed in
   at construction reports `overridden`.
2. **The startup log line.** The `kingfisher` logger, the format in O7, and the
   third name in the service's `__main__`. Tested for exactly one record per
   construction, nothing on stdout, and nothing emitted at all when the
   application has not configured logging.
3. **`Inventory` and `kingfisher list`.** The three fields become `origins`; the
   header prints every entry, tools included; `as_json` gains `origins`.
4. **`doctor`.** The header, and the two checks from O11. Both mutation-tested:
   point each at the state it is meant to catch, confirm it fires, restore.

`LIGHT_EXPORTS` in slice 1 is not optional. The record's value is being cheap to
ask for, and `test_importing_kingfisher_does_not_pull_in_deepagents` is what
keeps that true. `infrastructure.catalogue` imports in 40ms with no provider SDK
loaded, so there is room.

## What building it changed

Four things below were written wrong and are corrected in place above. They are
listed rather than tidied away, because two of them would have shipped a fault
and the reason each was wrong is more useful than the corrected text.

**The logger's name (O6).** `kingfisher` would have been the parent of
`kingfisher.audit`, which the service leaves unconfigured so that writing
session ids stays a deployment's decision. Raising the parent to INFO -- which
O8 has the server do -- would have turned the audit trail on in exchange for a
line saying where the skills directory is. Caught by reading the audit module,
not by a failing test; there is one now, in the service suite.

**A `doctor` check that could not fire (O11).** `examine` built its own
inventory, so the `overridden` warning had nothing to warn about. Caught by
asking how a test would reach it.

**What `default` means (O3).** Written as "under the workspace", implemented as
"the derived location". The two differ for a deployment that names a path equal
to the default, and the written version would have fired the relocated-and-empty
warning on every explicit fresh workspace.

**Where the slices sit (below).** Slice 3 was planned as depending only on slice
1, and does not: the header and the startup line share the per-entry spelling,
which lands with slice 2. Found when the tests failed with an `AttributeError`.

Two smaller things went beyond what was written, both because leaving them kept
a second answer alive. `Inventory.workspace` sat beside `Origins.workspace`, and
`render` took a `workspace` argument whose own docstring described it as a way
to print something other than what the configuration says -- which is the
disagreement this record removes. Both are gone. And `Origins` dropped its own
tuple of the four definition kinds for `DEFINITION_KINDS`, which is derived from
the fields of `Definitions`: a hand-written copy that has to match a dataclass is
exactly how a fifth kind would go unreported, which is the bug this record is
about.

## What this does not do

It reports paths and kinds. It never carries a credential, and it does not
report `base_url` -- `doctor` already answers "which endpoints, and are their
keys present" without naming destinations, and a startup line is not the place
to start.

It does not make a fresh install runnable. Five steps still stand between
`pip install` and a first task, the command line still cannot run one, and the
driver that can is still `tests/integration/driver.py` and still not shipped.
That is a separate argument and this document does not make it.
