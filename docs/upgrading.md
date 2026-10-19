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
#617* or later apply to you, and *after #784* names every change since the work that
replaced the four layers began.

## A session keeps the datazone of its first zoned turn, after #936

- **A session a turn in a datazone ran in is reached only from that datazone.** Reading
  it -- `session`, `sessions`, `pending`, `artifact` -- and carrying it on both answer
  as for a session that is not there, to a caller in no datazone or another one.
  `UNSCOPED` still reaches it. A session no zoned turn ran in is reached from every
  datazone, as before.
- **`Kingfisher.session`, `asession`, `sessions` and `asessions` take `datazone=`**,
  beside `source_ids=`, as `run` and `stream` already did. A finance caller reading a
  session a finance turn made has to pass it.
- **`kingfisher artifact` takes `--datazone`**, and `decide` with no answer now passes
  its `--datazone` to the pending calls it lists.
- **A session from before this keeps no datazone**, whatever turns ran in it, and stays
  reached from every datazone until its next zoned turn pins one.

## Both CSV tools stop at the columns an answer carries, after #927

- **`csv_profile` profiles the first 200 columns, and says so**, where it wrote a line
  for every column the file had. Measured: a 10 MB header of 5,242,880 columns answered
  334,433,239 characters and peaked at 1,833 MB resident, out of a tool whose docstring
  says the answer does not grow with the file; a 4,000-column file answered 218,913. The
  first line already carries the file's own column count, and the note beside it now
  says how many were read -- `250 columns, 2 rows (first 200 columns profiled)`.
- **`csv_columns` stops at the same 200 names**, with a last line reading `-- first 200
  of 5242880 columns`. An answer of one name a line cannot say it was cut any other
  way, and the same header had been answered back whole: 10,485,759 characters.
- **A column name longer than 100 characters is cut**, with what was cut said in its
  place: `xxx...(+50 characters)`. The CSV reader parses a cell up to 131,072
  characters and every one of them reached the answer, so 76 columns were 9,959,656 of
  it. If you parse these answers, a name you match on may now be a prefix of the file's.
- **Only the columns profiled are gathered**, so a file wider than the cap no longer
  costs a list per column. Nothing about a file inside the caps changed: the same
  5-column file answers the same 281 characters it did.

## `sql_query` refuses a row it cannot carry, after #918

- **A `SELECT` whose single row passes the answer cap is refused**, with the width in
  the message, where it used to be answered in full under a note claiming it had
  stopped at the cap. Measured: 20 columns of 100,000 characters answered 2,000,225
  characters, and 2,000 columns answered 400,011,025 — on the turn's own thread, in
  kingfisher's process. The remedy is in the message: select fewer columns, or shorten
  them with `substr`.
- **The cut note counts the rows the answer actually holds.** It counted the row that
  passed the cap among them, so a reader parsing the CSV found one row fewer than the
  note claimed.
- **Column names past the cap are refused too**, since the header is written before any
  row: `AS "<150,000 characters>"` is a column name.

If you copied `sql_query.py` into your own catalogue, these are three edits to the copy;
`kingfisher seed --all` takes the shipped one again.

## `seed` reads an audience written in Python, after #917

- **`kingfisher seed` no longer copies `subagents/quarters.py` or `tools/sql_query.py`
  by default.** Both say who reaches them, in Python, which seeding could not see: a
  `datazone:` was invisible in either format, and a definition declared in a module was
  never read at all. A deployment that has adopted source ids and seeded one of these
  had `kingfisher list` and `doctor` failing on a name it had never been told to declare.

  They are reported like every other skipped definition, and `kingfisher seed --all`
  still takes them. Nothing you have already seeded changes.
- **The declaration `seed` prints now has both lines of `source_ids.yaml`**, a
  `datazones:` beside the `source_ids:`, each only where something needs it.
- **The remedy sentence follows what the file asked for**: `Declare the datazone in
  source_ids.yaml` where that is what it named, and `Declare them` where it named both.
  A tool package naming an audience is left behind whole, folder and all, rather than
  copied with the module that names it removed.

## A claim answers a token, and a release names it, after #916

- **`Sessions.claim` returns a token, or `None`, instead of `True` or `False`.** A
  `Sessions` of your own returns a string naming this taker where it returned `True`,
  and `None` where it returned `False`. `aclaim` changes the same way.
- **`Sessions.release(cfg, session_id, token, /)` takes that token**, and gives back the
  claim only if it is the one the token took; anything else is a no-op. `arelease`
  changes the same way. A release that ignores the token fails
  `a_claim_is_released_only_by_its_token` and
  `a_late_release_leaves_the_claim_that_took_over` in `tests/checks/sessions.py`.
- **A takeover must have one winner.** Two callers that judge one claim stale at once
  must not both take it, which delete-then-create allows. `LocalSessions` renames the
  stale claim aside under a name only that claim has; a lock with compare-and-set does
  it directly.
- **A claim an older kingfisher took** carries no token. A new one never releases it,
  and takes it over once it is older than `claim_stale_after`, as any dead turn's.

## A compiled subagent is handed none of the gated tools, after #907

- **A tool the agent gates with `interrupt_on` is kept out of what a compiled
  subagent's `build` receives.** deepagents hands a graph it did not build none of the
  parent's gates, so a call there met nothing and ran unapproved. The subagent calling
  it now gets `Error: <name> is not a valid tool, try one of [...]`, which ends that
  call and not the turn — the same answer `eval` gives a program that asks for a gated
  tool.

  A compiled subagent that needs a tool the agent gates needs its own copy of it, in
  its folder and under another name; otherwise the gate has to go. What you cannot have
  is the gate and the call, because nothing in that graph can pause.
- **`kingfisher list` prints what each agent stops on**, as an `interrupt_on` line under
  the agent, and `--json` carries it as `agent_gates`. A scoped listing filters it the
  way it filters the tools line, so a gated tool a caller's own locks shut out is not
  named under an agent they can open.

## A gate names the tool a call carries, after #903

- **`interrupt_on` no longer takes a `where::what` reference.** An agent file gating
  `vendor_a.py::fetch` is refused at the read, and the message names what to write:

  ```
  operator.yaml: interrupt_on names vendor_a.py::fetch by file, and a gate is keyed by
  the name a call carries -- write fetch. A call carries the name alone, so the gate
  stops every tool answering to it, wherever each one is defined.
  ```

  Nothing is lost by the refusal: that spelling built and then gated nothing, because
  deepagents keys an interrupt by the name on the call. The bare name is what to write,
  where it used to be refused as a name nothing answered to, and it stops a call on
  every tool of that name.
- **A subagent's own bundled tool can be gated**, by its name. No spelling of one was
  accepted before, in the one graph it exists in.
- **A gate on a name only a compiled subagent's folder holds is refused**: deepagents
  hands a graph it did not build no gates at all, so that call would run unapproved.

## A definition naming skills holds `read_file`, after #877

- **A file whose `skills:` names anything is refused at startup unless its
  `builtin_tools` holds `read_file`**, the tool a skill is opened with. That covers an
  agent, a subagent and a Python subagent's portable entry, and names, `["*"]` and
  `source: bundled` entries alike, whether or not `KINGFISHER_SKILLS_ENABLED` is on:

  ```
  redactor.yaml: skills names redaction, and builtin_tools leaves out 'read_file', the
  tool a skill is opened with. Naming one does not grant it, so every time the model
  opened a skill it would be refused. Add read_file to builtin_tools, or write
  builtin_tools: ['*'] for every built-in
  ```

  Add `read_file` to `builtin_tools`, write `builtin_tools: ["*"]`, or drop the skills
  and put what they say in the prompt. A `SubAgent` taken bare from a package has no
  `builtin_tools`, so wrap it: `{**spec, "builtin_tools": ["read_file", "ls"]}`.
- **A request whose `Capabilities.builtin_tools` leaves out `read_file` is told of no
  skills**: no skills index, no skills section in the prompt and no shell paragraph on
  skills, for the agent and for each subagent, its own skills included. The run no
  longer reports skills as withheld from such a request.
- **The shipped `redactor` has no skill.** Its procedure is in its `system_prompt` and
  `subagents/redactor/skills/` is gone, since it holds no `read_file` on purpose. A
  workspace seeded from `assets/` keeps the old `redactor.yaml`, now refused, and the old
  skill folder, which the new file does not list and which is refused too. Seeding copies
  and never deletes, so delete `subagents/redactor/skills/` from the definitions folder,
  then run `kingfisher seed --all`. Carry across any edits to the seeded copies first,
  since seeding overwrites them.

## `kingfisher list` shows what each definition holds, and `--json` writes a grant as an object, after #873

- **`agent_subagents` maps each agent to `{"every": ..., "names": [...]}`**, not to a
  list. `names` is the list it used to hold, and `every` is `true` where the file wrote
  `subagents: ["*"]`, which a bare list could not say. Read
  `document["agent_subagents"]["analyst"]["names"]` where a script read
  `document["agent_subagents"]["analyst"]`. The old shape is not kept beside it.
- **Four keys are new, in the same shape:** `agent_tools`, `agent_builtin_tools`,
  `subagent_tools` and `subagent_builtin_tools`. Every agent and every subagent the
  listing shows has an entry in each, `{"every": false, "names": []}` for a field left out.
  A subagent's star has `every: true` and `names` holding only the subagent's own tools,
  because the rest is whatever the calling agent holds.
- **The text form prints `tools:` and `builtin tools:` lines** under each agent and each
  subagent. An agent's `subagents:` line written `["*"]` reads `* — today auditor,
  quarters` instead of the names alone.
- **`Inventory.agent_subagents` holds a `Grant` per agent**, `every` and `names`, where it
  held a tuple of names, and the four new fields hold the same.

## A tool's lock is in its file, a subagent's on its own line, and only a skill's on an entry, after #868

- **A `tools:` entry may no longer say who may use the tool.** `{name: sql_query,
  source_ids: [A]}`, in an agent, a subagent or a Python subagent's `tools`, is refused
  at load. Write the lock in the tool's file, `TOOLS = [ToolSpec(sql_query,
  source_ids=["A"])]`, and the entry as `- sql_query`. The lock then holds in every agent
  granting the tool. `{name: probe, source: bundled}` still lists a subagent's own tool.
- **A `subagents:` entry may no longer say who reaches the subagent.** `{name: auditor,
  source_ids: [A]}` is refused, and so is a long entry with nothing beside its name.
  Write `- auditor`, and narrow the subagent's own `source_ids:` line if it needs it.
- **A Python-declared subagent's `tools` is a list of names.** The mapping
  `{"sql_query": {"source_ids": ["A"]}}` is refused: write `["sql_query"]` and lock the
  tool in its file.
- **What this gives up is one tool, or one subagent, locked differently in two
  agents.** An agent still decides what it holds by listing it, so leave a tool or a
  subagent off the agents that should not hold it.
- **A `skills:` entry keeps its long form**, `{name: tabular-qa, source_ids: [A]}`, since
  a skill's own file is `SKILL.md`, which the model reads whole and a deployment may not
  own.
- **`kingfisher list` rolls up skills only.** The roll-ups by tool and by subagent are
  gone, a tool's lock stays a line of its own, `tool sql_query  [sales_db]`, and a
  definition in `--json`'s `audiences` carries a `skills` key and never `tools` or
  `subagents`. The startup report of an entry narrowing past its definition names
  skills entries only.
- **A result's ledger stamp is the definition's own audience joined with the tool's
  lock.** An entry's audience used to stand in for the definition's.
- **The shipped `analyst` and `auditor` no longer list `http_fetch`, and `analyst`
  names `auditor` bare**, so a caller holding only `audit_log` now reaches `auditor`
  through `analyst`. `sales_db_pii` stays in `source_ids.yaml` as the example of a
  requirement, named by no shipped definition.

## A tool field left out grants nothing, after #867

- **Leaving out `tools:` or `builtin_tools:` grants none**, as leaving out `skills:`,
  `subagents:` or `middlewares:` already did; `["*"]` is all. In every agent and subagent
  file that leaves either line out, write `tools: ["*"]` or `builtin_tools: ["*"]` to keep
  what it held, or name the tools it should hold.
- **A file naming `subagents:` with `builtin_tools` left out is refused at startup**, since
  it holds no `task`. Those show up loudly; the rest run with fewer tools, quietly.
- **In Python `SUBAGENTS`**, a portable entry gets `"builtin_tools": ["*"]` and a `build`
  entry `"tools": ["*"]` to keep what it held. A spec typed to deepagents' `SubAgent` has
  no word for built-ins, so wrap it: `{**spec, "builtin_tools": ["*"]}`.
- **A caller's `Capabilities` are unchanged**: unset still means no narrowing.

## `kingfisher.tool` is gone: a tool file writes `ToolSpec`, after #866

- **`kingfisher.tool(fn, source_ids=..., datazone=...)` is removed.** Write
  `ToolSpec(fn, source_ids=..., datazone=...)` in `TOOLS` instead, with
  `from kingfisher import ToolSpec`. It holds any tool, a `@tool` object or a `BaseTool`
  instance as well as a plain function, and a Python subagent's carried `tools` take it
  too. Left out, `source_ids` is `["*"]`, which admits whoever the granting agent admits,
  as before.
- **A plain function in a `ToolSpec` stays a plain function** unless it is renamed.
  `kingfisher.tool` returned a `StructuredTool`, so code of yours that called `.invoke`
  or read `.name` on what it returned calls the function instead.
- **`name=` and `description=` are applied when the definitions are read**, to a copy.
  A tool whose class computes either one is refused at startup. `args_schema=` has no
  counterpart: give a `@tool` or a `BaseTool` subclass its schema, and wrap that.

## A turn's budget is said near its end, and a subagent is held to it, after #861

- **From 80% of a turn's steps, each model call ends with a note** saying about how many
  calls are left, or that this one is the last. It is never stored, so no transcript or
  event carries it; a deployment's middleware reading the model's request sees it.
- **A subagent may make `KINGFISHER_RECURSION_LIMIT` ÷ 3 model calls in a turn**, 50 at
  the default, the agent's own budget. It was bounded only by the turn's time limit. It
  is warned the same way, and a subagent still asking for tools on its last allowed call
  hands back its text headed "[Stopped: this subagent reached its limit of N model
  calls…]". The turn goes on.
- **`standing_middleware` takes `step_limit=`**, with no default: pass
  `cfg.recursion_limit`.

## `grep` searches each string of `a|b`, after #858

- **A `grep` pattern of non-empty strings joined by `|` is searched for each string**,
  literally, and answered once with each string's matches under its own line. Every
  graph's model is told so. A pattern with an empty part is searched as written.

## `read_file` reads 1,000 lines unless told otherwise, after #856

- **A `read_file` call naming no `limit` reads up to 1,000 lines**, where deepagents
  reads 100, and every graph's model is told so and shown 1,000 as the default. A limit
  the model names is unchanged. A deployment's middleware reading `read_file`'s
  arguments sees `limit` filled in.

## A model that cannot answer now, after #853

- **A turn can end with `stop_reason: model_unavailable`.** The provider could not serve
  a model call -- a 429, a 5xx, a timeout, a dropped connection -- which escaped `run`
  and `stream` as the SDK's own exception before. `completed` is false, the `cut_short`
  event carries the status and the provider's message, and `kingfisher run` exits 1. A
  caller switching on `stop_reason` meets a value it has not seen.
- **A subagent whose model cannot answer no longer ends the turn.** Its `task` call comes
  back to the parent as a failed tool result, and the parent carries on.

## The backend kit and its check take the layout, after #852

- **`unrouted_denials(backend, layout)`** takes the `Layout` it checks against, with no
  default: left out, it checked the shipped layout's paths, the wrong ones for a renamed
  deployment. Pass `cfg.layout`.
- **Each check in `tests/checks/backend.py` takes a `make` returning `(cfg, backend)`**,
  as the sessions kit's do, and reads the folders from `cfg.layout`. `route_coverage`
  no longer takes `scopes`. A copy of the kit wants the same change.

## The plugin checks are named for what they answer, after #850

- **`not_executing` is `why_no_shell`, and `uncovered` is `unrouted_denials`**, from
  `kingfisher.plugins`. They answer the same questions; the old names are gone.
- **`Definitions.skill_mounts()` and `skill_sources()` return named tuples**,
  `SkillMount(route, directory)` and `SkillSource(route, folder, source)`. Unpacking them
  as pairs and triples still works.
- **The sessions kit in `tests/checks/sessions.py` reads `cfg.layout`**, so a copy of it
  holds a deployment that renamed its folders to its own names.

## A cut answer is asked for again, after #832

- **A turn's graph is streamed with `custom` among its modes** as well as `updates`,
  `values` and `messages`. A graph of your own, passed as `graph=`, is driven that way
  too; a langgraph graph streams whatever its nodes write there, and kingfisher ignores
  what it does not recognise.
- **A `model_call` event can describe a reply the conversation never holds**: a final
  answer cut off at the output cap, which kingfisher asked for again. It carries
  `cut_off` and the call's usage, and no tools.

## A reply cut off at the output cap, after #830

- **A turn can end with `stop_reason: max_tokens`.** The agent's last reply stopped at
  its model's `max_tokens`, so the answer is cut off. `completed` is false, so
  `run(delete_session=True)` keeps the session, and `kingfisher run` exits 1 as it does
  for `max_steps`. A caller switching on `stop_reason` meets a value it has not seen.
- **`RunEvent` has `cut_off`**, true on a `model_call` whose reply was cut, and the
  `model_call` run event carries it as well.
- **`tool_guards` takes `subagent=`**, with no default: what it does with an agent it
  stops is the one way the agent's guards and a subagent's differ.

## Sessions, plugins and instructions, after #784

The core-and-plugins work renamed what a deployment names and moved what the core does
not read out of `Config`. Every change refuses the old spelling rather than reading it.

| Before | Now |
|---|---|
| `SessionBackends`, `DefaultBackends`, `default_backends` | `Sessions`, `LocalSessions`, `LocalSessions()` |
| `backends=` on `Kingfisher`, `run()` and `stream()` | `sessions=`, required on all three, with a graph or without |
| `claim(name, ...)`, `release(name)`, `held(name, ...)` on the backend `open` returns | `claim(cfg, session_id, ...)`, `release(cfg, session_id)`, `held(cfg, session_id, ...)` on your `Sessions`, one lock per session |
| `KINGFISHER_SESSION_BACKENDS_FACTORY` | `KINGFISHER_SESSIONS` |
| No way to hand the command its run events, conversation store or middleware | `KINGFISHER_PLUGINS`, naming something that returns a `kingfisher.plugins.Plugins` |
| `Adapter`, `ADAPTERS`, `Endpoint.adapter`, `KINGFISHER_ADAPTERS_FACTORY` | `WireFormat`, `WIRE_FORMATS`, `Endpoint.wire_format`, `KINGFISHER_WIRE_FORMATS` |
| `Config.deployment_prompt`, and `PROMPT.md` in the workspace | `INSTRUCTIONS.md` beside the definitions, or `instructions=` on `Kingfisher`: an object with `text(cfg) -> str` |
| The repository Protocols in `core/interfaces.py` | Gone. Definitions are read from a directory; stage yours into one |
| `Config.shell_sandbox`, `Config.shell_path_extra` | `LocalSessions(shell=ShellSettings(sandbox=..., path_extra=(...)))`, with `ShellSettings` from `kingfisher.plugins`. `LocalSessions()` reads `KINGFISHER_SHELL_SANDBOX` and `KINGFISHER_SHELL_PATH_EXTRA` itself, so a deployment that sets them changes nothing |
| `Config.sessions_factory`, `Config.plugins_factory` | Gone: only the command read them, and it reads `KINGFISHER_SESSIONS` and `KINGFISHER_PLUGINS` itself |
| `inventory(cfg)` answering `<supplied>` where `KINGFISHER_SESSIONS` was set | `inventory(cfg, sessions=...)`: the origins line asks the sessions it is handed, and with none answers the workspace's own |
| `catalogue=` and `grants=` on `Kingfisher` | `Config.definitions`, pointed at the folder you staged, and `Config.grants` |
| `COMMAND_RUNNER_CONTRACT`, `BACKEND_CONTRACT`, `SESSION_BACKENDS_CONTRACT` from `kingfisher` | Gone from the package. The same checks are in this repository's `tests/checks/`, to copy; the promises they hold are on each interface's docstring |
| `kf.pending(id)`, `kf.artifact(id, name)`, `kf.files_for(id)`, `kf.session_size(id)`, `kf.delete_session(id)` and their `a` twins; `SessionInfo` | On the `Session` that `kf.session(id, source_ids=...)` hands out: `.pending()`, `.artifact(name)`, `.files()`, `.size()` and `.delete()`, each with an `a` twin. `kf.session` answers `None` for a session that is not there or not this caller's, and `create=True` makes one, which is what `files_for` did. `kf.sessions(source_ids=...)` lists only what a caller reaches. `Session` replaces `SessionInfo`, with the same `id` and `last_used` |
| `Kingfisher.held_for`, `Kingfisher.agent_named` | Private: they were the service's own helpers |
| `threads=` on `Kingfisher`, `checkpointer=` on `run()` and `stream()`, `Plugins.threads`, `SweepResult.orphans` | Gone, with nothing in their place: drop the argument. The conversation is the session's transcript, and every turn runs on a store of kingfisher's own that holds nothing after it. A store of your own showed the model every earlier exchange twice, crashed a turn that stopped for approval unless it was an `InMemorySaver`, and on a shared one copied every session into the paused one's folder |
| `LocalSessions`, `backend_at`, `Plugins`, `ShellPrompt`, `CommandResult`, `HostPathError` and `WireFormat` from `kingfisher` | From `kingfisher.plugins`, the address for writing a plugin. `Sessions`, `RunEvents`, `Instructions`, `CommandRunner`, `JsonlRunEvents`, `InstructionsFile` and `WIRE_FORMATS` are public there too, where they were not public anywhere |

## deepagents' host paths only where they are this host's, after #760

- **deepagents' "Shell paths vs. virtual paths" section is removed where the shell does
  not run on this host**: for a runner whose `local` is false, and for a sandbox of your
  own running `execute`. It listed each routed folder's path on this machine. Say what
  your shell sees in its `ShellPrompt`; nothing else now speaks for it.
- **`tool_guards` takes the turn's backend** in place of `shell=`, and reads what it
  needs off it.

## The shell describes itself, after #759

- **A shell kingfisher does not run is described neutrally.** The prompt and
  `execute`'s description told every agent that the shell runs on the host in the
  session root, with `$TMPDIR` the scratchpad and `$KINGFISHER_SKILLS` the catalogue.
  That is told now only of kingfisher's own shell, which `LocalSessions` builds. A
  deployment with `LocalSessions(runner=...)`, or with a sandbox of its own running
  `execute`, gets wording that claims none of it, and no correction when a command
  misses a file-tool path.
- **Say what yours sees with a `ShellPrompt`**, from `kingfisher`: beside the runner as
  `LocalSessions(runner=..., shell_prompt=...)`, or as a `shell_prompt` attribute on
  your sandbox. `ports.md` has its three parts and who reads each. Passing one without
  a runner is refused.
- **`render_system_prompt` and `system_prompt` take `shell=`**, and without it describe a
  shell that said nothing. `shell_paths.RULE` is gone; `execute_line(kingfishers_shell())`
  in `kingfisher.core.agent.prompting` is the same line.

## The session is named in the request, after #739

- **`KINGFISHER_SESSION_ID` is gone, and refused at startup if still set.** A request
  names its session -- `kingfisher run --session ID`, `Request(session_id=...)` -- and
  one naming none runs in `default`, at `sessions/default/`. Delete the line from
  `.env`; a deployment that set it to keep one conversation gets the same from
  `default`, or names its id on every request.
- **A request naming an id nobody has used starts that session**, where it was refused
  as one nobody issued. Kingfisher no longer issues ids, so a service must derive the
  ids it sends and never forward a user's. The access policy still stops a caller at a
  session whose pinned agent it does not reach.
- **A request naming no session no longer starts a new one each time.** Every such
  request carries on `default`. A caller that wanted a fresh session per call names a
  fresh id.
- **`files_for` makes a session no turn has made yet**, so inputs can be placed before
  the first turn, and `files=` on a request naming none is accepted as `default`'s.
- **Reading a session nobody has made still answers "no session" and makes nothing**:
  `pending`, `artifact` and `decide`, and `delete_session` does nothing. The message is
  `no session '<id>'`.
- **An id may not start with `.`**. It is refused with `UnknownSessionError` ("cannot
  name a session"), and a dot-named folder under `sessions/` is not listed.
- **In Python**, `Config.session_id` is gone, `REMOVED_STORE_SETTINGS` is
  `REMOVED_SETTINGS` (a mapping from each name to what replaces it),
  `is_one_path_segment` is `names_a_session`, `session_dir` takes no `only=`, and
  `LocalSessionDirs.used_at` is gone. `DEFAULT_SESSION` names the default.

## Every session in a folder of its own, after #738

*The setting this section describes went after #739: see The session is named in the
request.*

- **`KINGFISHER_SESSION_ID` keeps its session in `sessions/<id>/`**, like any other,
  where it was `sessions/` itself. It pins a process to one session rather than a
  workspace: other processes, each serving its own, may keep theirs in the same
  workspace, and each lists and reaps its own alone.
- **A workspace laid out the old way is neither converted nor refused.** Its session's
  folders sit directly in `sessions/` and nothing reads them. Clear `sessions/`, with
  nothing running, before starting.
- **Nothing is refused for having been run the other way.** A workspace that ran with
  the setting starts without it, and its session is listed and reaped with the rest. A
  changed id starts a session of its own beside the old one. `.harness/session-id` is
  neither written nor read.
- **Deleting the one session removes its folder**, where it emptied `sessions/`.
- **`doctor`'s `sessions apart` warns with the setting on too**, on macOS and wherever
  nothing confines the shell, and so does the warning an unconfined run prints: other
  processes may share the workspace.
- **The macOS profile is `.kingfisher/shell.<digest>.sb`**, named for its rules, where
  it was `.kingfisher/shell.sb`, so processes configured differently that share a
  workspace each have their own. `Confinement.profile` names the one a confinement
  writes, and `profile_path(workspace, rules)` takes the rules. A `shell.sb` left from
  before is read by nothing; delete it.
- **In Python**, `check_sessions`, `record_session_id`, `SESSION_ID_RECORD` and
  `LocalSessionDirs.empty` are gone. None was exported from `kingfisher`.

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
  that kind. `kingfisher seed` makes and fills one, and needs somewhere to fill it
  from as well: `KINGFISHER_ASSETS`, or `--from DIR`. Given neither, it lays the empty
  folder out and then refuses.
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
  shown all name the new one. In `kingfisher.core.layout`, `RESULTS` and `RESULTS_ROUTE`
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

- **Built on `backend_at`**, or on `LocalSessions` returning what it opened, there is
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

*Undone after #738: see Every session in a folder of its own.*

A workspace holding one session records the id it was laid out for, in
`sessions/.harness/session-id`, and refuses to start under any other (#675). Changing
`KINGFISHER_SESSION_ID` used to carry the old session's conversation, agent and memory
on under the new id. Set it back, or clear `sessions/` to start the new one afresh. A
workspace laid out before the record is let through once and given one on its next
open. Unsetting the setting is refused as before, and the refusal now names the id to
set it back to.

## One session per workspace, after #671

*Its layout and its refusals undone after #738: see Every session in a folder of its own.*

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
- **Sessions of your own** are handed the same `cfg`, and a request naming
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
| `place_data`, `DataPlacement` and `DataError` in `kingfisher.core.session.files`; `DataBackend` in `kingfisher.core.agent.backend`; `protect_data` and `writable_data` in `kingfisher.infrastructure.workspace` | `place_inputs`, `InputsPlacement`, `InputsError`, `InputsBackend`, `protect_inputs` and `writable_inputs`, in the same modules. None was ever exported from `kingfisher`. |

- **If you catch `DataError`** around a turn, for a file that is missing, named twice
  or refused by the backend, catch `InputsError`. It is still a `ValueError`.

## Session folders named for which way files go, after #667

A session's `data/` is `inputs/` and its `derived/` is `outputs/`, on disk and in
the paths the agent is given. `memory/` keeps its name.

| Before | Now |
|---|---|
| `sessions/<id>/data/`, which the agent reads as `/data/<name>` | `sessions/<id>/inputs/`, read as `/inputs/<name>` |
| `sessions/<id>/derived/`, which the agent writes as `/derived/<name>` | `sessions/<id>/outputs/`, written as `/outputs/<name>` |
| Artifact names such as `derived/report.md`, in `RunResult.artifacts` and for `Kingfisher.artifact` and `kingfisher artifact` | `outputs/report.md`. A name under `derived/` is refused as not an artifact. |
| Agent and subagent prompts, skills and tool descriptions that say `/data/...` or `/derived/...` | Say `/inputs/...` and `/outputs/...`. Nothing translates the old paths: a read under `/data/` finds nothing, and a file written under `/derived/` is not returned to the caller. |

- **Delete what an older workspace holds before starting on it. Nothing checks.**
  The workspace marker carried a layout number and a start refused a workspace laid out
  by an older one, naming what to delete; #756 dropped both, so a workspace still
  holding sessions from before this rename now starts without complaint and keeps
  whatever line its marker already has. Delete everything in `sessions/`, and whichever
  of `.kingfisher/agents`, `runs`, `claims` and `tmp` an older workspace still has --
  nothing reads any of it. Or point `KINGFISHER_WORKSPACE` at a new workspace. There is
  no migration, so copy anything you want to keep out of a session's `derived/` first.
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
| `LAYOUT_DIRS` in `kingfisher.core.layout` named the five kinds beside `sessions` and `.kingfisher` | Names only `sessions` and `.kingfisher`, which are always in the workspace. |
| `STARTER_AGENT` in `kingfisher.infrastructure.workspace` | `starter_agent(agents_dir)`, which names the directory it is given. Neither was exported from `kingfisher`. |

- **A folder an earlier start made in the workspace stays**, empty or not. If a
  setting moves that kind elsewhere, nothing reads it, so delete it.
- **Messages name where a kind is read from.** A workspace with no agents says to
  write one in the directory agents are read from, not in `agents/`. The help for
  `run --agent` and the refusal of an unknown `source:` no longer call a kind's
  folder the workspace's.

## Session ids refused unless they are one path segment, after #664

`LocalSessions` refuses a session id that is not one path segment (empty, `.`,
`..`, or holding `/` or NUL) wherever it builds a path from one: `open`,
`mark_used`, `size` and `delete` (#665). The refusal is the `UnknownSessionError` an
id nobody issued gets. Such an id used to act on whatever path it formed:
`files_for("..")` laid a session out in the workspace's own folder, an absolute id
laid one out wherever it pointed, and `LocalSessions.delete(cfg, "")` removed every
session in the workspace and reported success.

- **Every id kingfisher issued passes**, as does every folder under `sessions/`.
- **Sessions of your own are not checked for you**, unless they subclass
  `LocalSessions` and call through to it. Kingfisher hands them only ids it issued
  or they listed, except `Kingfisher.session_size`, which passes on the id it is given.

## `files_for` opens only a session a turn issued, after #663

*Undone after #739: see The session is named in the request.*

`Kingfisher.files_for(id)` and `afiles_for` refuse an id your sessions don't
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

## Sessions always given, after #639

Sessions are always passed now, so what kingfisher used to work out from a
session's directory on this host comes from them instead.

| Before | Now |
|---|---|
| `Kingfisher(cfg, graph=my_graph)` | `Kingfisher(cfg, graph=my_graph, sessions=LocalSessions())`, or the sessions that reach where your graph's backend keeps a session. A graph without `sessions=` is **refused**. |
| `run(...)`, `stream(...)`, with or without `graph=` | Name `sessions=` here too. Neither fills it in. |
| `Kingfisher(cfg, backends=default_backends, runner=my_runner)` | `Kingfisher(cfg, sessions=LocalSessions(runner=my_runner))`. `runner=` is gone from `Kingfisher`; `my_runner` is still a callable given the session's directory. |
| `open(cfg, session_id, /, *, catalogue=None, runner=None)` and `aopen` on your `SessionBackends` | Drop `runner`. A subclass of `LocalSessions` gets the runner it was built with from `super().open(...)`; sessions of your own decide how their commands run themselves. |
| A factory passed as `threads=`, called with the session's directory, `<workspace>/sessions/<id>` | Called with the session's id. A factory that kept a file per session on this host builds that path itself, from its own configuration. A checkpointer passed as `threads=` rather than a factory is unaffected. |
| `Kingfisher(cfg)` with no `backends=` | **Refused** by Python: `sessions` has no default on the constructor (#657), nor on `run()` and `stream()`. Pass `sessions=LocalSessions()`. |

Every session's backend is now checked when it is opened, beside a pre-built graph
too: it has to run commands as deepagents recognises them, and route every path the
agent is given. A backend that fails used to get as far as `claim` and stop there
with an `AttributeError` naming neither it nor your sessions; now opening the
session raises a `ConfigError` that says which check it failed.

## Renamed after #617

The object that opens each session's backend was called a backend, and its setting
a backend factory. It is a `Sessions`, so the names say so now, and the
method that opens a session is named rather than being the object's `__call__`.

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=...)`, and `backend=` on `run()` and `stream()` | `sessions=` |
| `default_backend`, `DefaultBackend` | `LocalSessions()`, `LocalSessions` |
| `__call__(cfg, session_id, /, *, catalogue=None, runner=None)` on your `SessionBackends`, called as `backends(cfg, session_id)` | `open(...)`, with the same arguments, called as `backends.open(cfg, session_id)` |
| `KINGFISHER_BACKEND_FACTORY` | `KINGFISHER_SESSIONS`. The old name is **refused**: startup fails with `KINGFISHER_BACKEND_FACTORY was renamed KINGFISHER_SESSIONS; set that instead`, and so is `KINGFISHER_SESSION_BACKENDS_FACTORY`, the name in between. |
| `Config.backend_factory`, `configured_backend()` | `Config.session_backends_factory`, `configured_sessions()` |

## Async reads after #617

`Kingfisher` has `asession`, `apending`, `aartifact`, `adelete_session` and
`afiles_for`, the async twins of `session`, `pending`, `artifact`, `delete_session` and
`files_for`, for a caller already on an event loop. They reach a session through new methods on `Sessions` --
`aopen`, `asessions`, `adelete`, and for a turn on `astream`, `amark_used` and `asize`
and for a turn, `aclaim` and `arelease`:

- **If your sessions subclass `LocalSessions` or `Sessions`**, there
  is nothing to do. All seven are inherited, each running its sync method on
  kingfisher's thread pool. Override one where it is a round trip you can await.
- **If yours subclass neither**, they are refused at construction until they have all
  seven. Subclassing `Sessions` is the shortest fix.
- `SESSION_BACKENDS_CONTRACT` has seven more checks, one for each async twin:
  `aopen_reaches_the_session_open_does`, `asessions_lists_what_sessions_does`,
  `adelete_removes_the_session`, `aclaim_and_claim_exclude_each_other`,
  `arelease_gives_back_what_claim_took`, `asize_counts_what_size_does` and `amark_used_moves_the_session_on`, which waits a
  second so a coarse filesystem clock still sees the session move on.
- **A checkpointer passed as `threads=`** is deleted from with its own `adelete_thread`
  on the async path, or on kingfisher's thread pool where it has none or raises
  `NotImplementedError` as langgraph's base saver does. `arun(delete_session=True)`
  deletes this way too. `kingfisher.core.interfaces.ThreadStore` is gone; `threads=` is
  typed `Any`, and is still a langgraph checkpointer.

## Checklist

1. **Remove `KINGFISHER_SESSION_STORE` and `KINGFISHER_SESSION_STORE_FACTORY`** if
   you set them. Kingfisher now refuses to start while either is set.
2. **Run `kingfisher doctor` and read its `session files` row.** Nothing signs what
   a session keeps under `/.harness`, so the row says whether anything it can see
   keeps the agent's shell from rewriting it. With your own backend, that is the
   backend's `shell_denied` check (see *For backend authors*).
3. **If you call `Kingfisher(...)` yourself**, pass `sessions=` — it is required now —
   and check the constructor changes in *Sessions always given* and below.
4. **If you read `RunResult.session_dir` or `RunResult.log_path`**, switch to
   `artifacts` with `Kingfisher.artifact`, and to `RunEvents`.
5. **If you wrote your own backend**, it now has to be a `Sessions`. See
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
| `KINGFISHER_SESSIONS` | **New, for the command line only.** `module:name` of something callable with no arguments that returns your `Sessions`. | Set it (the name since *Renamed after #617*) if yours keep sessions anywhere but `<workspace>/sessions`, so `kingfisher sessions`, `reap`, `artifact` and `decide` see them. |

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
| `Kingfisher(cfg, backend=my_factory)` where `my_factory(cfg, session_dir, ...)` is a function | `sessions=` takes a **`Sessions`**. To build on the default, subclass `LocalSessions` and override `open(cfg, session_id, /, *, catalogue=None)`. A plain function is refused with a `TypeError` saying so. |
| `default_backend(cfg, session_dir)` called directly | `LocalSessions().open(cfg, session_id)` takes a session id now. For a folder of your choosing, call `backend_at(cfg, directory)`. |
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
  are gone from `kingfisher.core.interfaces`.
- **Added:** `LocalSessions`, `backend_at`, `SESSION_BACKENDS_CONTRACT`,
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
  `KINGFISHER_SESSIONS` when it's set. `decide --session ID` with no decisions
  now finds a session a backend keeps elsewhere, and asks as the caller `--as` names:
  in a workspace with source ids it needs `--as`, and a caller who can't reach the
  session is told it doesn't exist. `artifact` takes `--as` the same way, and
  `Kingfisher.pending` and `Kingfisher.artifact` take `source_ids=` for the same check.
  `doctor` gains a `session files` row, saying whether anything keeps the agent's
  shell out of `.harness`, and reports `KINGFISHER_SESSION_KEY` as read by nothing. Its
  advice for a memory-backed workspace points at a durable mount or
  `KINGFISHER_SESSIONS`.

## For backend authors

A custom backend is now a **`Sessions`**: one object that opens a session's
backend per turn, and also answers for every session.

- `open(cfg, session_id, /, *, catalogue=None)` returns that
  session's backend, creating the session if it's new.
- `sessions(cfg)` returns `(session_id, last_used)` pairs. `mark_used(cfg, id)`,
  `size(cfg, id)` and `delete(cfg, id)` do what they say.
- **The sessions hold the turn lock, one per session:** `claim(cfg, session_id, /, *,
  stale_after)` (which must fail while a live claim holds that session),
  `release(cfg, session_id)` and `held(cfg, session_id, *, stale_after)`.
  `LocalSessions` implements these over a folder under the session's `.harness`.
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
| `UnknownSessionError: '…' cannot name a session: a session id is one name, …` | An id that is not one visible path segment: `..`, the empty string, one holding `/`, or one starting with `.` | Name the session in one segment; see *Session ids refused unless they are one path segment*. An id nobody has used is no longer refused — it starts that session |
| `HostPathError: '…' is a host path, and file tools take virtual paths rooted at the workspace — …` | A file tool given an absolute path under the workspace but outside this session, such as another session's file or `models.yaml` | Use a virtual path, or the shell for host paths; see *Host paths refused anywhere under the workspace* |
| `KINGFISHER_SESSION_STORE was removed: …` | The old store setting is still set | Unset it; see *Settings* |
| `KINGFISHER_SESSION_ID was removed: name the session in each request instead; …` | The setting is still set | Delete it; see *The session is named in the request* |
| `KINGFISHER_DEFINITIONS_DIR is required but not set` | A deployment from before the definitions had a folder of their own | Move them into one and set it; see *Definitions in a folder of their own* |
| `KINGFISHER_SKILLS_DIR was removed: skills is read from KINGFISHER_DEFINITIONS_DIR now, …` (or any of its six siblings) | A setting that moved one kind is still set | Move what it named into the definitions folder and unset it; see *Definitions in a folder of their own* |
| `KINGFISHER_DEFINITIONS_DIR is …, which is not a directory` | The folder is not there, or its mount failed | Run `kingfisher seed`, or fix the mount; a start never makes it |
| `… neither may be inside the other …` | The definitions folder and the workspace are one folder, or one holds the other | Give each a folder of its own; see *Definitions in a folder of their own* |
| `TypeError: sessions= takes a Sessions …` | A plain factory function, or one backend, passed as `sessions=` | Subclass `LocalSessions` |
| `TypeError: … missing 1 required keyword-only argument: 'sessions'` | `Kingfisher(...)`, `run()` or `stream()` called without `sessions=`, with or without a graph | Pass `sessions=LocalSessions()`, or sessions of your own; see *Sessions always given* |
| `ConfigError: … is not recognised by deepagents as running commands …` or `… routes nothing covering …` | A session backend your `open` returned has no shell, or leaves a path unrouted | Subclass `SandboxBackendProtocol`, or route the path; see *For backend authors* |
| `KINGFISHER_BACKEND_FACTORY was renamed …` | The setting's old name is still set | Rename it; see *Renamed after #617* |
| `… is not kept on this host by this session's backend …` | A `path` tool on a backend without local files | Read through `ToolContext`, or give the backend a `host_path` |
| `subagent '…': …/tools/ holds …, which tools: does not list` | A file in a subagent's folder the definition doesn't list | Add it as `{name: …, source: bundled}`, or move it out; see *Subagent bundles* |
| `… lists entries as source: bundled and owns no folder to take them from` | The definition or its folder was renamed, so they no longer pair | Make the folder name and the `name:` match |
| `'bundle' is not a field of this format -- a subagent's own tools and skills are listed where the rest are, …` | A YAML or compiled definition still writes `bundle:` | List the entries with `source: bundled` instead |
| `'bundle' is not a field of this format -- a portable entry carries its own under the plain fields` | A portable `SUBAGENTS` entry still writes `bundle` | Move its contents to `tools` and `skills` |
