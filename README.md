# kingfisher

A library for running agents against a workspace, with what each request may do
decided by the caller rather than by the code.

```bash
pip install kingfisher
```

```python
from kingfisher import definitions_source, ensure_definitions, paths_from_env, seed

paths = paths_from_env()
ensure_definitions(paths.definitions)

done = seed(paths.definitions, definitions_source(paths.assets))
for line in done.report():
    print(line)
if (closing := done.nothing_seeded()) is not None:
    print(closing)
```

A deployment keeps two folders apart. `KINGFISHER_DEFINITIONS_DIR` is what people
write — agents, middlewares, skills, subagents, tools, and `models.yaml` beside
them — and several deployments can point at one.
`KINGFISHER_WORKSPACE` is what sessions make. Seeding writes into the first and
needs nothing else; a start only reads it, and never creates it.

Four calls, and the second is belt-and-braces rather than an ordering you have to
get right. `seed` lays the definitions folder out itself, so `models.yaml.example`
arrives whether or not you call `ensure_definitions` — a deployment told to write
`models.yaml` and given no example of one is a dead end, and the library closes it
from the inside.

What the explicit call still buys is the path `seed` never reaches.
`definitions_source` refuses when a deployment has named no assets, and it
refuses *before* seeding starts — so without the line above, that deployment gets
no folder at all instead of a laid-out one and an error explaining itself. Cheap,
idempotent, and it only shows up on the day something is misconfigured.

`seed` requires a directory to copy from and will not invent one;
`definitions_source` is what turns configuration into that directory, reading
`KINGFISHER_ASSETS` and taking a path that overrides it. On the command, the
same two:

    kingfisher seed                          # from KINGFISHER_ASSETS
    kingfisher seed --from ./my-definitions  # from here instead

**Read `skipped`, or your deployment will quietly be missing agents.** A
definition naming middleware this deployment has not registered is refused when it
is built, so seeding one produces a file that cannot run; one carrying a lock -- a
source id or a datazone -- switches on access control in the definitions folder it lands in.
`seed` leaves both behind and says which names each carries;
`seed(..., everything=True)` and `kingfisher seed --all` take them once you have
registered the middleware, or want the locks. The shipped `assets/` has several, `general`
among them, and ships the middleware they name beside them -- so seed that set
with `--all`.

`seed` takes the definitions folder's path rather than a whole `Config` --
`paths.definitions` above. That is what lets it run on a fresh folder, before
there is a `models.yaml` inside it to read.

The formats — agents, skills, subagents, and the catalogue they live in — are
documented in [`docs/guides/formats.md`](docs/guides/formats.md). Tools and
middleware are code rather than documents, so each has its own page:
[`docs/guides/tools.md`](docs/guides/tools.md) and
[`docs/guides/middleware.md`](docs/guides/middleware.md). The rest of
[`docs/`](docs/README.md) says why the code is shaped the way it is and what
deepagents actually does underneath it, and [`GLOSSARY.md`](GLOSSARY.md) names
the words the code and the docs use for its parts, and the ones they avoid.

`kingfisher list` shows what the definitions offer a request, kind by kind, and
where each one came from. `kingfisher prompt --agent NAME` shows what that agent's
model is sent, block by block and named for what wrote each, without calling it --
`kf.sent(request)` from Python. `kingfisher seed` writes a starting
definitions folder. `kingfisher sessions` shows what the workspace is holding — one line
per session, how long it has been idle and what it costs — and `kingfisher reap`
deletes one with `--session ID`, or with `--all` every one no turn is running in.
`kingfisher artifact --session ID
outputs/report.html` fetches one file a turn produced, by the name the run
reported — `kf.session(session_id).artifact(name)` from Python. What the turn's tools
returned is on `RunResult.ledger`, one entry a result, a subagent's own calls
included, each naming the file in the session's scratchpad once it is filed there --
when it leaves the conversation, or at once if it is too long to show whole.
`kingfisher result --session ID A-0002` prints one by its id wherever it is kept --
`kf.session(session_id).result("A-0002")` from Python. A run
that should leave nothing behind can say so at the time: `kingfisher run
--delete-session`, or `Kingfisher.run(..., delete_session=True)` from Python.

`kingfisher turns --session ID` lists the turns a session kept, oldest first, with a `*`
on the path to where it is now, and when each started and how long it ran --
`kf.session(session_id).turns()` from Python.
`kingfisher rewind --session ID --to TURN` puts the session back where that turn ended:
its conversation and the files in `/outputs` and `/memory`. `--to-start` goes back to
before the first turn, and `--only files` or `--only conversation` takes back one and
leaves the other -- `kf.session(session_id).rewind(turn_id, only="files")` from Python,
with `START` for the start. The turns after it are kept, so a rewind to one of them
returns. A file with no copy of that version stays as it is, named in
`Rewound.not_restored`; `KINGFISHER_REWIND_MAX_BYTES` caps what the copies may hold, and
`KINGFISHER_REWIND_ENABLED=false` keeps none and refuses every rewind.

An agent can be written to stop before a tool runs and wait for you — see
*Approval gates* in [`formats.md`](docs/guides/formats.md), and
`assets/agents/operator.yaml` for one that does. A turn that stops says
what it is holding and prints the command that answers it; `kingfisher decide
--session ID` on its own says the same thing again for anyone who has lost the
output.

## What ships separately

**The definitions ship nowhere.** They were their own distribution once, found
through an entry point so anyone could publish a pack, and then a set inside
this wheel. Both are gone: where a deployment gets its definitions is a setting,
`KINGFISHER_ASSETS`, and a directory needs no wheel, no metadata and no publish
step.

The cost is real and is not hidden. A fresh install seeds nothing, and since a
request must name an agent, it cannot run until definitions arrive from
somewhere. This repository ships its own in `assets/` — every kind the
formats define, held to working by the suite — and definitions from another
project belong outside the checkout, seeded with a second `--from`.
