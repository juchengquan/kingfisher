# Upgrading: a session is its backend

This note is for a deployment built on kingfisher `main` before #608. Between #608
and #617 a session stopped being a folder on the host and became whatever the
session's backend keeps. Most of what follows exists so that a backend which keeps
sessions somewhere else, such as a remote sandbox, works end to end. Some of it
changes what every deployment writes.

It also covers the earlier change to subagent bundles (#566, #568), because a
deployment from before #608 may well be from before that too. See *Subagent bundles*.

**If you run the default backend on one machine** and don't use `SessionStore`,
`SessionRoot`, `RunResult.log_path` or `RunResult.session_dir`, you probably need
only the first two items of the checklist, and the sections titled *after #667* and
*after #668*, whose renames reach every deployment.

**If you are already on #617 or later**, only the sections whose titles say *after
#617* or later apply to you.

## Definitions in a folder of their own, after #734

- **`KINGFISHER_DEFINITIONS_DIR` is required**, beside `KINGFISHER_WORKSPACE`. It holds
  `agents/`, `middlewares/`, `skills/`, `subagents/`, `tools/`, `models.yaml` and
  `source_ids.yaml`, under those names. Move them out of the workspace into a folder of
  their own, with neither folder inside the other, and set it. The workspace keeps
  `sessions/` and `.kingfisher/`.
- **Seven settings are refused if still set:** `KINGFISHER_AGENTS_DIR`,
  `KINGFISHER_MIDDLEWARES_DIR`, `KINGFISHER_SKILLS_DIR`, `KINGFISHER_SUBAGENTS_DIR`,
  `KINGFISHER_TOOLS_DIR`, `KINGFISHER_MODELS_FILE` and `KINGFISHER_SOURCE_IDS_FILE`.
  What each one named goes under its own name in the definitions folder.
- **A start reads the definitions folder and writes nothing there.** One that does not
  exist is refused rather than made, and a kind folder missing inside it means none of
  that kind. `kingfisher seed` makes and fills one, and needs only
  `KINGFISHER_DEFINITIONS_DIR`.
- **Definitions left in a workspace are read by nothing**, and nothing checks for them.
  Delete them once they are moved.
- **In Python**, `Config` takes `definitions=` (keyword, required) and has lost
  `skills_root`, `subagents_root`, `tools_root`, `agents_root` and `middlewares_root`.
  `WorkspacePaths` takes `definitions` first, its `workspace` is optional, and it has
  lost the same five and `models_file` and `source_ids_file`. `ensure_layout(workspace)`
  takes the workspace alone and lays out only that; `ensure_definitions(definitions)`
  lays out a definitions folder, and is on the package root where `ensure_layout` was.
  A seeding destination needs only `.definitions`. `Kingfisher(catalogue=...)` takes a
  folder laid out like the definitions folder, or a `Definitions`, where it took one
  folder per kind.
- **`kingfisher list` and `doctor`** print a `definitions` line under `workspace` and
  spell a path inside it `definitions/<name>`; `--json` carries `"definitions"`.

## The shell's path rule is in `execute`'s description, after #731

- **`execute`'s description is no longer deepagents' word for word.** Its "Use absolute
  paths and avoid `cd`" line is replaced by kingfisher's rule: the shell starts in the
  session root, and a file-tool path without its leading slash is the shell's. Every
  graph kingfisher builds that holds the shell gets it, subagents included. Anything
  comparing the tools a model is offered against deepagents' sees it change.
- **A shell result that misses a session folder spelt the file tools' way** ends in a
  line saying so and giving the shell's spelling, for example after
  `cat /inputs/data.csv`. That line is part of what the model is handed, so a recorded
  result keeps it too.

## Recorded results are kept under `/scratchpad/tool_results/`, after #724

- **The folder is `/scratchpad/tool_results/`**, where it was `/scratchpad/results/`.
  An entry's `path`, the rows of `.harness/ledger.jsonl` and the lists the model is
  shown all name the new one. In `kingfisher.layout`, `RESULTS` and `RESULTS_ROUTE`
  are now `TOOL_RESULTS` and `TOOL_RESULTS_ROUTE`.
- **A backend of your own** is asked for the same `upload_files`, `ls` and `delete`,
  under the new folder. Built on `backend_at`, there is nothing to do.
- **Older sessions** go on working. Their rows still name files in `results/`, which
  stay where they are, and the next turn saves into `tool_results/`. The end-of-turn
  sweep now lists only the new folder. There is nothing to migrate, and reaping a
  session removes both.

## A request naming a subagent out of a caller's reach is refused as a typo is, after #712

For a deployment with a `source_ids.yaml`, where the agent writes `subagents: ["*"]`
and the deployment grants every subagent, so a request's names reach the build as
written:

- **A request naming a subagent whose own `source_ids:` misses the caller is refused**,
  with the message a name nothing defines gets:
  `this request names unknown subagent(s): insider; offered: (...)`. It used to run
  without it and say nothing. A request naming one of each lists both.
- **Two subagents of one name, of which the caller reaches one**, read as one. `"*"`
  is no longer refused for activating two, a bare `x` activates the one the caller
  reaches instead of being refused as offered by more than one source, and the other's
  qualified spelling is refused as unknown. A caller reaching both is refused as before.

An agent listing its subagents by name, or a deployment granting a list of them, still
narrows a request's names silently, out of reach or not.

For every deployment:

- **An agent's own mistake is refused in its name.** A subagent nothing defines reads
  `agent 'boss' names unknown subagent(s): ghost; offered: (...)`, where it read
  `this request names ...`, and two of a name reads `agent 'boss' activates 2
  subagents called 'x'`.
- **An agent's names are checked as its file wrote them.** A typo in an entry whose
  `source_ids:` misses the caller is refused for that caller too, where the turn ran
  without it.

A caller who reaches every subagent, and a deployment with no `source_ids.yaml`, are
refused for the same requests as before, and read the same `offered:` lists.

## A refusal of an unknown subagent lists only those a caller reaches, after #711

For a deployment with a `source_ids.yaml`. When an agent's file, a request or a
subagent's file names a subagent nothing defines, the refusal's `offered:` list leaves
out each subagent whose own `source_ids:` misses the caller. The refusal is still raised
for every caller. A caller who reaches every subagent, and a deployment with no
`source_ids.yaml`, read the same message as before.

## The model is shown an index once a conversation is summarised, after #710

Once deepagents has summarised a conversation, the user message lists everything the
ledger holds rather than only what is new.

- **A closing line in `.harness/ledger.jsonl` can say `"summarised": true`**, for a turn
  whose final state holds deepagents' summary, and a resume of such a turn says it too.
  Other turns' lines are unchanged. A backend of your own has nothing new to do.
- **The user message carries `<ledger_index>` in place of `<ledger_delta>`** when a
  turn since the previous user message summarised. The last five exchanges that
  recorded anything are listed as the delta lists them, and each older one is a line
  such as `A-0001..A-0012: 5 web_search, 5 http_fetch, 2 task`: its range of ids, then
  how many results each tool returned, most first. An exchange is a turn that carried a
  message and the resumes after it. A session that never recorded anything gets no
  block, as before.
- **`system.md`'s *What your tools returned* changed.** The id is now something the
  model *can* cite rather than is told to, a paragraph explains the index, and a
  sentence says how to find an older result's file by its id. Its text is still the
  same every turn. Anything that compares the rendered system prompt against a copy
  sees it change.
- **Signatures.** `turn_message` takes the `KeptLedger` a turn read, or `None`, in place
  of the entries to list. `KeptLedger` holds `exchanges` and `summarised`, and `since`
  is now a property of `exchanges`. `keep_ledger` takes `summarised=`, which it requires,
  and `write_ledger` takes it too. `ledger_index` and `ledger_shown` are new beside
  `ledger_delta`.

## The model sees what its tools returned, after #709

What the ledger records is now shown to the model, as well as to the caller.

- **A recorded result ends in its id.** The model is handed it with a last line such
  as `[A-0001]`, and a result made of content blocks gains a text block holding
  `\n[A-0001]`. So do the `tool_result` events a caller streams and the transcript,
  which hold what the model was handed, and so does `RunResult.answer` for a turn a
  bound cut short just after a tool ran, which was that tool's result before too. A
  file tool's result, a failed call's and anything from a graph you supplied carry
  none. The result file and `LedgerEntry.size` are the result without it. deepagents'
  copy of an offloaded result, where such an entry's `path` points, ends in it.
- **The user message can end in a `<ledger_delta>` block**, after the scratchpad line
  and the new-files line: a line for each entry recorded since the previous user
  message, with what a subagent's tools returned indented under its answer. A message
  after a turn that recorded nothing has none, and a resume adds no message to carry
  one.
- **A resume's closing line in `.harness/ledger.jsonl` is
  `{"turn": ..., "resumed": true}`.** An asked turn's is unchanged. A backend of your
  own has nothing new to do.
- **`turn_message` takes a third argument**, the entries to list, and lists none
  without it. `KeptLedger.since` holds them for a turn, and `Ledger` and
  `write_ledger` take `resumed=`.
- **`system.md` has a section more, *What your tools returned***, explaining the id,
  the delta and where each result's file is. Its text is the same every turn, so the
  cached prefix holds. Anything that compares the rendered system prompt against a
  copy sees it change.

## A turn records what its tools returned, after #708

Every turn kingfisher builds the graph for now records each tool result the model was
handed. A deployment that supplies its own graph records nothing.

- **`RunResult.ledger` is new**: this turn's entries, a `LedgerEntry` each, in the order
  they were numbered. Each holds an id such as `A-0001`, the turn and the call, the
  tool and its arguments as the model wrote them, the audience it was offered under,
  its size, where it is kept, and a one-line summary. A subagent's own calls are
  `working` entries under the `task` call that started them, and its answer is a
  `main` one. The built-in file tools and failed calls are not recorded. It serialises
  to JSON with the rest of the result.
- **Each result is kept in the session, under `/scratchpad/results/`**, as
  `A-0001-<tool>.txt`, or `.json` where the text parses as JSON. A result deepagents
  offloaded points at its copy in `/scratchpad/large_tool_results/` instead. A file
  the backend refuses leaves its entry with no `path` and a warning in the log, and
  the call is not failed for it.
- **`.harness/ledger.jsonl` is new**: every entry the session has recorded, one JSON
  row each, and a closing line `{"turn": ...}` for each turn, a turn that recorded
  nothing included. It is written at the end of every turn, one that paused for a
  decision included, before the transcript and the paused state.
- **A backend of your own is asked for three more things**: `upload_files` under
  `/scratchpad/results/` as each result arrives, `ls` of that folder and of the offload
  folder at the end of a turn, and `delete` of a result file no row names. Built on
  `backend_at`, there is nothing to do.
- **Older sessions** number from `A-0001` on their next turn. There is nothing to
  migrate.

## A run and a listing name only the subagents a caller reaches, after #707

What a caller is told about subagents now matches what is built for them.

- **The withheld report leaves out a subagent whose own `source_ids:` misses the
  caller.** A request narrowing `subagents:` was told such a subagent was not granted.
- **`kingfisher list --as` prints an agent's `subagents:` chain for that caller**, in
  the text form and as `agent_subagents` in `--json`. A subagent whose own line misses
  them, or that the agent's entry keeps from them, is left out of the chain.
- **The `indistinct` event names only subagents this run activated.** It used to read
  the request's grant, so a request leaving `subagents` at `"*"` heard about every
  subagent in the catalogue, including ones the agent never names. This applies to
  every deployment, with or without a `source_ids.yaml`.

## A subagent's own `source_ids` narrow what it is built with, after #705

For a deployment with a `source_ids.yaml`. A subagent's own `source_ids:` line, and the
audiences on its `tools:`, `skills:` and `subagents:` entries, now narrow it for each
caller, as `formats.md` always said. Until now only `kingfisher list` read them.

- **A subagent whose own `source_ids:` does not reach the caller is no longer built**,
  where an agent names it or as another subagent's helper. It was built for every caller
  the agent admitted.
- **A subagent holds only the tools, skills and helpers its own entries give this
  caller.** A compiled subagent's `build` is handed only the tools its per-tool audiences
  admit.
- Nothing changes where the agent's own audiences already narrow the same names, as the
  shipped `analyst` does for `auditor`, nor in a deployment with no `source_ids.yaml`.
- Prompts should cope with the shorter list, as `auditor`'s does: the caller is not told
  what a subagent lacks.

## A definition naming subagents holds `task`, after #699

An agent or subagent whose `subagents:` names anything, and whose `builtin_tools` is a
list without `task`, is now refused at startup, and `doctor` fails on it. It could
never call them: its own allowlist refused `task` at every call.

- Add `task` to that `builtin_tools`, or leave the line out for every built-in.
- **A `researcher.yaml` or `analyst.yaml` seeded from the shipped set** has this shape
  and stops the workspace starting until it holds `task`. Neither had ever reached
  `sweeper` or `auditor`.

## A subagent listing `eval` is refused, after #699

A subagent whose `builtin_tools` lists `eval` is now refused at startup, in a YAML file
or a Python declaration alike. With `KINGFISHER_INTERPRETER_ENABLED` on it loaded and
the subagent never held `eval`; with it off, the build refused it as an unknown tool.
Take `eval` off the list. An agent listing it is unaffected.

## Calls from `eval` keep the guards, after #694

For a deployment with `KINGFISHER_INTERPRETER_ENABLED` on. A program `eval` runs now
meets what a direct call meets.

- **`tools.<name>()` for a workspace tool reaches the guarded one.** A `path` argument
  is translated into the session, and a host path or another session's path under any
  other argument is refused, as a direct call's is.
- **A tool that refuses or raises hands the program its error text**, `Error: <type>:
  <message>`, as an ordinary value. A program that caught the failure with `try` now
  has to look at the value instead, and one that did not no longer ends the turn.
- **A tool the agent gates with `interrupt_on` is absent from `tools.*`**, and a gate on
  `task` takes `task()` out of the program, an unrestricted request's too. Both are
  still callable directly, where they pause.
- **A helper a subagent consults has no `eval`**, as a top-level subagent never had.
  Calling it answers that `eval` is not a valid tool.

## The shipped `reviewer` holds `task`, after #694

`task` is how `reviewer` consults `profiler`, and its `builtin_tools` left it out, so
every consultation was refused. A `reviewer.yaml` seeded before this still refuses them:
add `task` to its `builtin_tools`.

## A subagent's skills read flat, after #690

A subagent's own skills sit directly in its folder: `skills/<name>/SKILL.md`. A
skill in a subfolder of that folder was never shown to the subagent, and is no longer
granted to it either.

- `kingfisher list` prints `! <path>/ sits too deep to load` under the subagent, and
  `doctor` warns on a `subagent skills` row. `list --json` carries them as
  `bundled_misplaced`.
- **A definition listing such a skill as `source: bundled` is refused at startup**,
  with a message saying it sits in a subfolder. Move the skill up to sit directly in
  the folder.

## deepagents' own files kept in the scratchpad, after #689

The files deepagents writes on its own have moved under `/scratchpad/` (#690). A tool
result too long for a message is now saved to `/scratchpad/large_tool_results/`, and the
summarizer's history, with any user message too long to send, to
`/scratchpad/conversation_history/`. Both used to sit at the session root, as
`/large_tool_results/` and `/conversation_history/`. The system prompt names the new
folders.

- **Built on `backend_at`**, or on `DefaultBackends` returning what it opened, there is
  nothing to do.
- **A `CompositeBackend` of your own** still has deepagents write both folders at `/`
  until you pass `artifacts_root="/scratchpad/"`. `BACKEND_CONTRACT` has a sixth check,
  `offloads_in_the_scratchpad`, that says so. Nothing refuses it at runtime.
- **A prompt, skill or middleware of yours that names `/conversation_history/` or
  `/large_tool_results/`** should say `/scratchpad/conversation_history/` or
  `/scratchpad/large_tool_results/`. A stub still names its own path, so following one
  is unaffected.
- **Older sessions** keep any of these folders at their root until they are reaped.
  There is no migration.
- **Under Landlock** the agent's shell can now edit or delete these files, as it can
  anything else in `/scratchpad/`.

## The noun is `subagent`, after #686

`delegate` as a noun is gone from kingfisher, in favour of deepagents' `subagent`.
Four things a caller or a script can read changed with it, and no old spelling is kept:

- The warning kind `delegate_only` is `subagent_only`.
- `kingfisher list --json` keys an agent's subagents as `agent_subagents`, not
  `agent_delegates`.
- The `doctor` rows `delegate tools` and `delegate bundles` are `subagent tools` and
  `subagent bundles`.
- `kingfisher list` prints `subagents:` under each agent where it printed `delegates:`.

Messages that said `delegate` say `subagent`. Nothing a deployment writes or sets
changes.

## A session's skills checked against what kingfisher read, after #680

`SESSION_BACKENDS_CONTRACT` has one more check,
`a_session_serves_the_skills_kingfisher_read` (#681). Kingfisher builds grants and the
read-deny rules from the skills it reads itself. The check opens a session through
your backends and compares what it serves under `/skills/` -- the catalogue, each
mount and each subagent's own skills -- with the folders kingfisher read.

- **Built on `backend_at` and keeping its routes**, there is nothing to do: kingfisher's
  own routes pass.
- **Routing `/skills/` yourself**, serve the folders kingfisher reads: the catalogue at
  `/skills/`, each mount at `/skills/<label>/`, and each subagent's skills under
  `/skills/subagents/`. A skill served from anywhere else is either granted and
  missing, or readable however a request narrowed.
- Nothing changes at runtime. The check runs where you run the kit.

## A lone session's id recorded, after #674

A workspace holding one session records the id it was laid out for, in
`sessions/.harness/session-id`, and refuses to start under any other (#675). Changing
`KINGFISHER_SESSION_ID` used to carry the old session's conversation, agent and memory
on under the new id. Set it back, or clear `sessions/` to start the new one afresh. A
workspace laid out before the record is let through once and given one on its next
open. Unsetting the setting is refused as before, and the refusal now names the id to
set it back to.

## One session per workspace, after #671

A workspace can hold exactly one session (#672). Nothing changes unless you set
`KINGFISHER_SESSION_ID`; set it, and the workspace holds that one session, laid out
in `sessions/` itself rather than in `sessions/<id>/`.

- **A request naming no session runs in that one**, and its result carries the
  configured id. A request naming any other id is refused as an unissued one is.
  `delete_session` and `reap` empty `sessions/` rather than removing it, and the next
  turn starts a fresh conversation under the same id.
- **A workspace is used one way or the other.** Turning the setting on for a
  workspace whose `sessions/` holds sessions of their own is refused at startup, and
  so is turning it off for one laid out by it. Clear `sessions/`, or use another
  workspace.
- **Session backends of your own** are handed the same `cfg`, and a request naming
  no id runs under `cfg.session_id`. List that id from `sessions(cfg)` if a first turn
  may name it, or the turn is refused. `SESSION_BACKENDS_CONTRACT` opens several ids,
  so it cannot pass against backends serving this mode.
- **On macOS, a shell in a shared workspace can no longer make `sessions/.harness`.**
  Its presence is how a workspace holding one session is told apart, so a shell able
  to make it could lock the workspace. Nothing legitimate wrote there.

## A caller's files named for `inputs/`, after #668

The names for the files a caller hands a turn follow the folder they land in. The
old names are gone rather than kept beside the new ones, so each of these fails
until it is changed.

| Before | Now |
|---|---|
| `kingfisher run --data PATH`, repeatable | `kingfisher run --input PATH`, repeatable. `--data` is refused as an unrecognised argument. |
| `Request(..., data=(path, ...))`, and reading `request.data` | `Request(..., inputs=(path, ...))` and `request.inputs`. `data=` raises `TypeError`. |
| A `RunEvent` of kind `data_placed`, which `kingfisher run` prints as `[data_placed] ...` | `inputs_placed`, with the same text. A consumer switching on `data_placed` never sees one again. |
| `place_data`, `DataPlacement` and `DataError` in `kingfisher.infrastructure.session_files`; `DataBackend` in `kingfisher.infrastructure.harness.backend`; `protect_data` and `writable_data` in `kingfisher.infrastructure.workspace` | `place_inputs`, `InputsPlacement`, `InputsError`, `InputsBackend`, `protect_inputs` and `writable_inputs`, in the same modules. None was ever exported from `kingfisher`. |

- **If you catch `DataError`** around a turn, for a file that is missing, named twice
  or refused by the backend, catch `InputsError`. It is still a `ValueError`.

## Session folders named for which way files go, after #667

A session's `data/` is `inputs/` and its `derived/` is `outputs/`, on disk and in
the paths the agent is given. `memory/` keeps its name. The workspace layout is 3.

| Before | Now |
|---|---|
| `sessions/<id>/data/`, which the agent reads as `/data/<name>` | `sessions/<id>/inputs/`, read as `/inputs/<name>` |
| `sessions/<id>/derived/`, which the agent writes as `/derived/<name>` | `sessions/<id>/outputs/`, written as `/outputs/<name>` |
| Artifact names such as `derived/report.md`, in `RunResult.artifacts` and for `Kingfisher.artifact` and `kingfisher artifact` | `outputs/report.md`. A name under `derived/` is refused as not an artifact. |
| Agent and subagent prompts, skills and tool descriptions that say `/data/...` or `/derived/...` | Say `/inputs/...` and `/outputs/...`. Nothing translates the old paths: a read under `/data/` finds nothing, and a file written under `/derived/` is not returned to the caller. |

- **Delete what an older workspace holds before starting on it.** A workspace with
  sessions in it from layout 2 or earlier is refused at startup, and the message
  lists exactly what to delete: everything in `sessions/`, and for a workspace from
  layout 1, whichever of `.kingfisher/agents`, `runs`, `claims` and `tmp` are still
  there. The marker is brought up to date on the next start. Or point
  `KINGFISHER_WORKSPACE` at a new workspace. There is no migration, so copy anything
  you want to keep out of a session's `derived/` first.
- **If you wrote your own backend**, route `/inputs/` where you routed `/data/`.
  `route_coverage` in `BACKEND_CONTRACT` names a route your backend leaves out, and
  `shell_denied` now checks that the shell cannot write under `/inputs`.
- **What stayed for this change:** `kingfisher run --data`, `Request.data`, the
  `data_placed` event, and `protect_data`, `writable_data`, `place_data`,
  `DataBackend`, `DataError` and `DataPlacement`. They are renamed in the section
  above, *A caller's files named for `inputs/`*.

## Memory guidelines that say memory lasts its session, after #666

With memory on, the model is given guidelines for using it after kingfisher's own
memory section. They are kingfisher's now (#667), `prompts/memory_guidelines.md`,
where they were deepagents' `MEMORY_SYSTEM_PROMPT`: the same template, except that
five lines which promised memory to future conversations say later turns of this
conversation, because `/memory` is deleted with its session. Kingfisher's own memory
section has said the same since #663. Nothing has to change, and two things follow:

- **A deepagents upgrade no longer changes what the model is told about memory.**
  Kingfisher's copy is not re-synced from the library's.
- **A middleware of yours whose class is named `MemoryMiddleware`** still takes the
  memory slot, as it took deepagents' before, so the model gets what yours sends and
  not kingfisher's guidelines.

## Definition folders made only where they are read, after #665

Each kind of definition gets its folder where it is read from, and nowhere else
(#666). A kind moved with `KINGFISHER_SKILLS_DIR` or one of its siblings used to get
an empty folder in the workspace too, which looked like the place to put a definition
and was never read.

| Before | Now |
|---|---|
| `ensure_layout(workspace, authored=...)` made `agents`, `middlewares`, `skills`, `subagents` and `tools` in the workspace | It still does without `catalogue_roots=`. Pass `catalogue_roots=paths.catalogue_roots`, as the README now does, and each is made where it is read from. A `Config` has the same property, and `seed` passes it already. |
| `Kingfisher(cfg, ...)` made all five in the workspace | Makes each where `cfg` reads it from, and none when given `catalogue=`: whoever staged that catalogue made its folders. |
| `LAYOUT_DIRS` in `kingfisher.layout` named the five kinds beside `sessions` and `.kingfisher` | Names only `sessions` and `.kingfisher`, which are always in the workspace. |
| `STARTER_AGENT` in `kingfisher.infrastructure.workspace` | `starter_agent(agents_dir)`, which names the directory it is given. Neither was exported from `kingfisher`. |

- **A folder an earlier start made in the workspace stays**, empty or not. If a
  setting moves that kind elsewhere, nothing reads it, so delete it.
- **Messages name where a kind is read from.** A workspace with no agents says to
  write one in the directory agents are read from, not in `agents/`. The help for
  `run --agent` and the refusal of an unknown `source:` no longer call a kind's
  folder the workspace's.

## Session ids refused unless they are one path segment, after #664

`DefaultBackends` refuses a session id that is not one path segment (empty, `.`,
`..`, or holding `/` or NUL) wherever it builds a path from one: `open`,
`mark_used`, `size` and `delete` (#665). The refusal is the `UnknownSessionError` an
id nobody issued gets. Such an id used to act on whatever path it formed:
`files_for("..")` laid a session out in the workspace's own folder, an absolute id
laid one out wherever it pointed, and `DefaultBackends.delete(cfg, "")` removed every
session in the workspace and reported success.

- **Every id kingfisher issued passes**, as does every folder under `sessions/`.
- **Session backends of your own are not checked for you**, unless they subclass
  `DefaultBackends` and call through to it. Kingfisher hands them only ids it issued
  or they listed, except `Kingfisher.session_size`, which passes on the id it is given.

## `files_for` opens only a session a turn issued, after #663

`Kingfisher.files_for(id)` and `afiles_for` refuse an id your session backends don't
list, with the `UnknownSessionError` a turn naming it gets, and open nothing (#664).
They used to open it through `open`, which makes a session it is asked for, so an id
the caller made up became a session a later turn accepted.

- **If you called `files_for` to start a session under an id of your choosing**, run
  the session's first turn with no `session_id` and no `files=`, then call
  `files_for` with the `session_id` its result carries. A session's id is only ever
  one kingfisher issued.

## Host paths refused anywhere under the workspace, after #658

A file tool handed a host path is refused with `HostPathError` wherever under the
workspace it points (#659). It used to be refused only inside the turn's own session.
Another session's file, a skill, `models.yaml` or an agent definition named by its
host path was accepted wherever the workspace sits outside the host roots kingfisher
lists -- at `/workspace`, which the `Dockerfile` sets, for one -- and a write to it
was mirrored inside the session rather than reaching the path named.

- **A path inside the session** is refused as before, with the virtual path to use.
- **Any other path under the workspace** gets the general refusal, because it has no
  virtual path to offer. An agent or tool of yours that handed the file tools
  absolute paths into the workspace should use virtual paths, or the shell for host
  paths.

## Session backends always given, after #639

Session backends are always passed now, so what kingfisher used to work out from a
session's directory on this host comes from them instead.

| Before | Now |
|---|---|
| `Kingfisher(cfg, graph=my_graph)` | `Kingfisher(cfg, graph=my_graph, backends=default_backends)`, or the session backends that reach where your graph's backend keeps a session. A graph without `backends=` is **refused**. |
| `run(..., graph=my_graph)`, `stream(..., graph=my_graph)` | Name `backends=` beside `graph=` here too. Without a graph, `backends` still defaults to `default_backends`. |
| `Kingfisher(cfg, backends=default_backends, runner=my_runner)` | `Kingfisher(cfg, backends=DefaultBackends(runner=my_runner))`. `runner=` is gone from `Kingfisher`; `my_runner` is still a callable given the session's directory. |
| `open(cfg, session_id, /, *, catalogue=None, runner=None)` and `aopen` on your `SessionBackends` | Drop `runner`. A subclass of `DefaultBackends` gets the runner it was built with from `super().open(...)`; session backends of your own decide how their commands run themselves. |
| A factory passed as `threads=`, called with the session's directory, `<workspace>/sessions/<id>` | Called with the session's id. A factory that kept a file per session on this host builds that path itself, from its own configuration. A checkpointer passed as `threads=` rather than a factory is unaffected. |
| `Kingfisher(cfg)` with no `backends=` | **Refused** by Python: `backends` has no default on the constructor (#657). Pass `backends=default_backends`. Only `run()` and `stream()` still fill it in, and only without a graph. |

Every session's backend is now checked when it is opened, beside a pre-built graph
too: it has to run commands as deepagents recognises them, and route every path the
agent is given. A backend that fails used to get as far as `claim` and stop there
with an `AttributeError` naming neither it nor your session backends; now opening the
session raises a `ConfigError` that says which check it failed.

## Renamed after #617

The object that opens each session's backend was called a backend, and its setting
a backend factory. It is a `SessionBackends`, so the names say so now, and the
method that opens a session is named rather than being the object's `__call__`.

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=...)`, and `backend=` on `run()` and `stream()` | `backends=` |
| `default_backend`, `DefaultBackend` | `default_backends`, `DefaultBackends` |
| `__call__(cfg, session_id, /, *, catalogue=None, runner=None)` on your `SessionBackends`, called as `backends(cfg, session_id)` | `open(...)`, with the same arguments, called as `backends.open(cfg, session_id)` |
| `KINGFISHER_BACKEND_FACTORY` | `KINGFISHER_SESSION_BACKENDS_FACTORY`. The old name is **refused**: startup fails with `KINGFISHER_BACKEND_FACTORY was renamed KINGFISHER_SESSION_BACKENDS_FACTORY; set that instead`. |
| `Config.backend_factory`, `configured_backend()` | `Config.session_backends_factory`, `configured_backends()` |

## Async reads after #617

`Kingfisher` has `asession`, `apending`, `aartifact`, `adelete_session` and
`afiles_for`, the async twins of `session`, `pending`, `artifact`, `delete_session` and
`files_for`, for a caller already on an event loop. They reach a session through new methods on `SessionBackends` --
`aopen`, `asessions`, `adelete`, and for a turn on `astream`, `amark_used` and `asize`
-- and through `aclaim` on the backend `open` returns:

- **If your session backends subclass `DefaultBackends` or `SessionBackends`**, there
  is nothing to do. All five are inherited, each running its sync method on
  kingfisher's thread pool. Override one where it is a round trip you can await.
- **If yours subclass neither**, they are refused at construction until they have all
  five. Subclassing `SessionBackends` is the shortest fix.
- **The backend `open` returns needs `aclaim` and `arelease`** beside `claim` and
  `release`. One built on `SessionClaims`, as kingfisher's are, has them already.
- `SESSION_BACKENDS_CONTRACT` has seven more checks, one for each async twin:
  `aopen_reaches_the_session_open_does`, `asessions_lists_what_sessions_does`,
  `adelete_removes_the_session`, `aclaim_and_claim_exclude_each_other`,
  `arelease_gives_back_what_claim_took`, `asize_counts_what_size_does` and `amark_used_moves_the_session_on`, which waits a
  second so a coarse filesystem clock still sees the session move on.
- **A checkpointer passed as `threads=`** is deleted from with its own `adelete_thread`
  on the async path, or on kingfisher's thread pool where it has none or raises
  `NotImplementedError` as langgraph's base saver does. `arun(delete_session=True)`
  deletes this way too. `kingfisher.domain.ports.ThreadStore` is gone; `threads=` is
  typed `Any`, and is still a langgraph checkpointer.

## Checklist

1. **Remove `KINGFISHER_SESSION_STORE` and `KINGFISHER_SESSION_STORE_FACTORY`** if
   you set them. Kingfisher now refuses to start while either is set.
2. **Run `kingfisher doctor` and read its `session files` row.** Nothing signs what
   a session keeps under `/.harness`, so the row says whether anything it can see
   keeps the agent's shell from rewriting it. With your own backend, that is the
   backend's `shell_denied` check (see *For backend authors*).
3. **If you call `Kingfisher(...)` yourself**, pass `backends=` — it is required now —
   and check the constructor changes in *Session backends always given* and below.
4. **If you read `RunResult.session_dir` or `RunResult.log_path`**, switch to
   `artifacts` with `Kingfisher.artifact`, and to `RunEvents`.
5. **If you wrote your own backend**, it now has to be a `SessionBackends`. See
   *For backend authors*.
6. **If you copied the shipped tools**, decide whether to re-seed them (see
   *Workspace tools*).
7. **If a subagent has its own `tools/` or `skills/` folder, or writes `bundle`**,
   list what it takes from the folder (see *Subagent bundles*). Kingfisher refuses to
   start until you do.

## Settings

| Setting | What changed | What to do |
|---|---|---|
| `KINGFISHER_SESSION_STORE`, `KINGFISHER_SESSION_STORE_FACTORY` | **Removed and refused.** Startup fails with `KINGFISHER_SESSION_STORE was removed: a session's backend keeps it now…` | Unset them. For sessions that outlive the machine, mount durable storage at `<workspace>/sessions`, or use a backend that keeps sessions itself. |
| `KINGFISHER_SESSION_KEY` | **Gone.** It was on `main` for a day after #610, signing the pin, the conversation and a paused turn; nothing signs them now. | Unset it if you set it; `kingfisher doctor` reports it as read by nothing. |
| `KINGFISHER_SESSION_BACKENDS_FACTORY` | **New, for the command line only.** `module:name` of something callable with no arguments that returns your `SessionBackends`. | Set it (the name since *Renamed after #617*) if your session backends keep sessions anywhere but `<workspace>/sessions`, so `kingfisher sessions`, `reap`, `artifact` and `decide` see them. |

## Existing sessions

- **Sessions that a `SessionStore` kept** are not read any more. Copy any you need into
  `<workspace>/sessions/<id>/` before upgrading, or move them into whatever your
  backend now keeps.
- **A session written while a key was set** keeps `.sig` files beside its `.harness`
  files. Nothing reads them, and they go when the session is reaped.
- **The old run log**, `.harness/runlog.jsonl`, is no longer written or read. It's
  safe to delete.

## `Kingfisher(...)` and the one-liners

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=my_factory)` where `my_factory(cfg, session_dir, ...)` is a function | `backends=` takes a **`SessionBackends`**. To build on the default, subclass `DefaultBackends` and override `open(cfg, session_id, /, *, catalogue=None)`. A plain function is refused with a `TypeError` saying so. |
| `default_backend(cfg, session_dir)` called directly | `default_backends.open(cfg, session_id)` takes a session id now. For a folder of your choosing, call `backend_at(cfg, directory)`. |
| `dirs=`, `sessions=`, `session_root=` | **Removed.** A session's backend answers all three: where it is, what it holds, and the housekeeping. |
| `run_events=` | **New.** A sink with `record(event)`. With none given, events go to the `kingfisher.run` logger at INFO. |
| `delete_session(id, forget=...)`, `reap(..., forget=...)` | `forget=` is **removed**: there is no store to keep a copy in. |
| `run(...)` / `stream(...)` with `dirs=` | `dirs=` is **removed**; `run_events=` is added. |

## `RunResult`

- **`log_path` is removed.** What a turn did now goes to a `RunEvents` sink: one
  `record(event)` call per model call, tool call, start and end, each carrying
  `session_id` and `turn_id`. `kingfisher run` and `decide` keep no log file unless
  given `--log FILE`, which appends one JSON line per event.
- **`session_dir` is removed.** `artifacts` lists what a turn left, as names
  relative to the session (`outputs/report.md`). Fetch one with
  `Kingfisher.artifact(session_id, name)` or `kingfisher artifact --session ID NAME`.
- **A whole `RunResult` now serialises to JSON**, because nothing in it is a host
  path.

## Public names

- **Removed from `kingfisher`:** `LocalSessionStore`, `SESSION_STORE_CONTRACT`,
  `SESSION_ROOT_CONTRACT`. The `SessionRoot`, `SessionStore` and `SessionDirs` ports
  are gone from `kingfisher.domain.ports`.
- **Added:** `DefaultBackends`, `backend_at`, `SESSION_BACKENDS_CONTRACT`,
  `ArtifactError`, and `Kingfisher.artifact` and
  `Kingfisher.pending` on the service.

## Workspace tools

- **A tool argument named `path` now resolves where the session's backend keeps the
  file.** Three behaviour changes follow:
  - `/skills/...` now reaches the real catalogue file. Before, it pointed at a file
    that didn't exist.
  - `/.harness/...`, and `/memory/...` when a request declined memory, are refused.
    A tool could previously be handed those files.
  - On a backend that doesn't keep the file on this host, the tool gets a readable
    refusal instead of `FileNotFoundError`. To work on any backend, a tool takes
    `runtime: ToolRuntime[ToolContext]` and reads through `runtime.context.backend`
    (see `docs/guides/tools.md`).
- **The six shipped file tools changed signature:** `line_count`, `csv_profile`,
  `csv_columns`, `log_levels`, `status_codes` and the redactor's `mask_secrets` take
  `file_path` and a runtime, and read through the backend. Copies already seeded into
  a workspace keep their old `path` form and keep working on the default backend.
  Re-seed them (`kingfisher seed`) if you move to a backend that keeps sessions
  elsewhere. Seeding overwrites a copy you edited, so carry any edits across first.
  If you call them directly, pass `file_path=` and a `ToolRuntime`.
- **A compiled subagent with a blank `name` or `description` is now refused** (#601).
  The other three readers already refused one.

## Subagent bundles

A subagent's own folder, `subagents/<name>/tools/` and `skills/`, used to grant
everything in it automatically, and an optional `bundle:` key could describe it.
Now the definition lists what it takes from the folder, and only that arrives:

```yaml
tools:
  - "*"                     # keep every catalogue tool, if it had them before
  - name: mask_secrets
    source: bundled         # subagents/<name>/tools/
skills:
  - name: redaction
    source: bundled         # subagents/<name>/skills/
```

- **List every file in the folder.** An unlisted file stops the catalogue loading
  rather than being silently dropped, so you find out on the first start.
- **Add `"*"` if the definition had no `tools:` line.** That used to mean every
  catalogue tool *plus* the folder. A list that names only bundled entries now means
  the folder alone.
- **Remove `bundle:`.** It's refused in YAML and in compiled (`build`) definitions.
- **A compiled subagent can't keep a `skills/` folder.** A compiled graph is never told
  about skills, so the folder is refused. Move the skill to the shared catalogue, or
  read it inside the graph.
- **A portable `SUBAGENTS` entry** (no `build`, often from an installed package) moves
  what it carried out of `bundle` and into the plain fields: `"tools": [the tool
  objects]` and `"skills": an absolute path`. Names aren't accepted there.
- **Agents** may write `source: shared` but not `source: bundled`, since an agent has
  no folder of its own.

`kingfisher doctor` reports any mismatch on its `bundled entries` row before startup
does. `docs/guides/formats.md`, under *Tools and skills of its own*, has the details.

## Command line

- **New:** `kingfisher artifact --session ID NAME [--out PATH]` fetches a file a turn
  produced.
- **Changed:** `sessions`, `reap`, `artifact` and `decide` run on
  `KINGFISHER_SESSION_BACKENDS_FACTORY` when it's set. `decide --session ID` with no decisions
  now finds a session a backend keeps elsewhere, and asks as the caller `--as` names:
  in a workspace with source ids it needs `--as`, and a caller who can't reach the
  session is told it doesn't exist. `artifact` takes `--as` the same way, and
  `Kingfisher.pending` and `Kingfisher.artifact` take `source_ids=` for the same check.
  `doctor` gains a `session files` row, saying whether anything keeps the agent's
  shell out of `.harness`, and reports `KINGFISHER_SESSION_KEY` as read by nothing. Its
  advice for a memory-backed workspace points at a durable mount or
  `KINGFISHER_SESSION_BACKENDS_FACTORY`.

## For backend authors

A custom backend is now a **`SessionBackends`**: one object that opens a session's
backend per turn, and also answers for every session.

- `open(cfg, session_id, /, *, catalogue=None)` returns that
  session's backend, creating the session if it's new.
- `sessions(cfg)` returns `(session_id, last_used)` pairs. `mark_used(cfg, id)`,
  `size(cfg, id)` and `delete(cfg, id)` do what they say.
- **The backend you return holds the turn lock:** `claim(name, *, stale_after)`
  (which must fail while a live claim holds the name), `release(name)` and
  `held(name, *, stale_after)`. `SessionClaims` implements these over a local folder.
- **Kingfisher reads and writes the session through it:** it places `/inputs` with
  `upload_files`, lists `/outputs` and `/memory` with `glob`, fetches artifacts with
  `download_files`, and reads and writes `/.harness` for the pin, conversation and
  pauses. `/inputs` has to accept that upload while staying read-only to the agent.
- **Optional:** `host_path(virtual) -> Path | None`, if your files are on this host
  by some means Kingfisher can't see, such as a network mount. It must never name
  another session's file.
- **Check it** with `BACKEND_CONTRACT` (now five checks, including `shell_denied`:
  the shell may not write `/.harness` or `/inputs`) and the new
  `SESSION_BACKENDS_CONTRACT` (sessions kept apart, persistence across turns, an
  exclusive claim, listing and deleting, and `host_path` staying in its session).

`docs/guides/ports.md` has the details. `docs/decisions.md`, under *A session is its
backend* and *A tool's path is the backend's path*, explains why.

## Errors you may see after upgrading

| Message | Cause | Fix |
|---|---|---|
| `… was laid out by kingfisher layout 2, and this is layout 3 …` | The workspace still holds sessions from before `data/` and `derived/` were renamed | Delete what the message lists, or use a new workspace; see *Session folders named for which way files go* |
| `UnknownSessionError: no session '…'; omit session_id to start one`, from `files_for` | An id no turn issued, such as one made up to start a session, or one that is not a single path segment | Run the session's first turn without `files=`, then open the id its result carries; see *`files_for` opens only a session a turn issued* |
| `HostPathError: '…' is a host path, and file tools take virtual paths rooted at the workspace — …` | A file tool given an absolute path under the workspace but outside this session, such as another session's file or `models.yaml` | Use a virtual path, or the shell for host paths; see *Host paths refused anywhere under the workspace* |
| `… holds one session laid out directly in it -- .harness/ is there -- …` | `KINGFISHER_SESSION_ID` is unset on a workspace that was run with it | Set it to the id the message names, clear `sessions/`, or use another workspace; see *One session per workspace* |
| `KINGFISHER_SESSION_ID is '…', and … holds the session laid out for '…'` | The setting was changed on a workspace already holding a session | Set it back to the id laid out, clear `sessions/` to start the new one afresh, or use another workspace; see *One session per workspace* |
| `KINGFISHER_SESSION_ID is set, and … holds sessions of their own -- …` | The setting was turned on for a workspace that already holds sessions | Clear `sessions/`, unset it, or use another workspace; see *One session per workspace* |
| `KINGFISHER_SESSION_STORE was removed: …` | The old store setting is still set | Unset it; see *Settings* |
| `KINGFISHER_DEFINITIONS_DIR is required but not set` | A deployment from before the definitions had a folder of their own | Move them into one and set it; see *Definitions in a folder of their own* |
| `KINGFISHER_SKILLS_DIR was removed: skills is read from KINGFISHER_DEFINITIONS_DIR now, …` (or any of its six siblings) | A setting that moved one kind is still set | Move what it named into the definitions folder and unset it; see *Definitions in a folder of their own* |
| `KINGFISHER_DEFINITIONS_DIR is …, which is not a directory` | The folder is not there, or its mount failed | Run `kingfisher seed`, or fix the mount; a start never makes it |
| `… neither may be inside the other …` | The definitions folder and the workspace are one folder, or one holds the other | Give each a folder of its own; see *Definitions in a folder of their own* |
| `TypeError: backends= takes a SessionBackends …` | A plain factory function, or one backend, passed as `backends=` | Subclass `DefaultBackends` |
| `TypeError: … missing 1 required keyword-only argument: 'backends'` | `Kingfisher(...)` called without `backends=`, with or without a graph | Pass `backends=default_backends`, or session backends of your own; see *Session backends always given* |
| `a pre-built graph needs session backends named beside it …` | `run()` or `stream()` given `graph=` without `backends=` | Name `backends=` beside the graph |
| `ConfigError: … is not recognised by deepagents as running commands …` or `… routes nothing covering …` | A session backend your `open` returned has no shell, or leaves a path unrouted | Subclass `SandboxBackendProtocol`, or route the path; see *For backend authors* |
| `KINGFISHER_BACKEND_FACTORY was renamed …` | The setting's old name is still set | Rename it; see *Renamed after #617* |
| `… is not kept on this host by this session's backend …` | A `path` tool on a backend without local files | Read through `ToolContext`, or give the backend a `host_path` |
| `subagent '…': …/tools/ holds …, which tools: does not list` | A file in a subagent's folder the definition doesn't list | Add it as `{name: …, source: bundled}`, or move it out; see *Subagent bundles* |
| `… lists entries as source: bundled and owns no folder to take them from` | The definition or its folder was renamed, so they no longer pair | Make the folder name and the `name:` match |
| `'bundle' is not a field of this format` | A YAML or compiled definition still writes `bundle:` | List the entries with `source: bundled` instead |
| `'bundle' -- a portable entry carries its own under the plain fields` | A portable `SUBAGENTS` entry still writes `bundle` | Move its contents to `tools` and `skills` |
