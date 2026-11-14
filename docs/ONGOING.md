# Ongoing

Work that was started or agreed and is not finished, and what each item waits on.

Each entry ends with the day it was last checked. Check it against the code before
building on it: the rule [`README.md`](README.md) gives `design/` holds here too.

An entry leaves when its work lands or is dropped. What was decided goes to
`decisions.md`; this page keeps no history.

Not repeated here: proposals in `design/`, which `README.md` lists, and what the ledger
left unbuilt, under *Not built, and still open* in `decisions.md`.

## Waiting on a decision

- **Keeping the cache through a memory change on MiniMax.** The breakpoint ahead of the
  memory block saves little there, so each change the writer makes costs the next turn
  most of its cache; `KINGFISHER_MEMORY_EVERY` went to 4 to pay it half as often. Moving
  the block, or asking MiniMax how it matches a prefix, needs a grilling of its own.
  *(2026-10-27.)*
- **"Remember X" waits for the memory writer, and is lost only with a `MemoryStore`.** In
  the trial behind `KINGFISHER_MEMORY_EVERY=4`, the agent never saved what the user asked
  it to remember; the writer did, at its next run. The agent is told how to save but not
  when. A store session that ends before that run leaves the request in its own
  `/.harness/memory_writer.jsonl`, and the user's other sessions never see it. Session
  memory loses nothing: the conversation carries the request until the writer reads it,
  four turns on or on a summarised turn. Agreed for when per-user memory is planned:
  where memory is the user's and the agent holds a tool to save with, one sentence ahead
  of the rule on identifying details -- "What the user asks you to remember or to forget
  reaches their later conversations only once this file says so, so change it in the
  turn they ask." An agent holding neither, as `operator.yaml` does, is left to the
  writer, and the guide says so. Kept if, on MiniMax-M3 over one-turn sessions, ten
  before and ten after, an asked "remember" is saved and a "forget" carried out at least
  8 times in 10, and a passing remark saved no more than 2 in 10 above before. Still
  open: what the user never asked to keep, in a store session that ends before the
  writer runs. Running the writer on a session's first turn as well, or when the caller
  says the conversation is over, both keep every four turns, and which pays depends on
  how long a deployment's sessions are; carrying the leftovers into the user's next
  session would put raw conversation in the store. *(2026-11-01.)*
- **The memory writer keeps some of the task in hand.** In the same trial M3's writer
  once kept a definition the user had asked for and a one-off exchange rate, though
  `memory_writer.md` says to leave the task out. Tighten the prompt and measure again, or
  accept it within the 40-entry limit. *(2026-10-30.)*
- **Who the user is, in `/outputs`.** Three turns on MiniMax-M3 with `--tell` naming the
  user, on 2026-10-26 before the writer existed: memory kept the name, email address and
  id out but saved the team, which the identifier check now drops; one report in
  `/outputs` opened "Hi Ana —". Nothing checks `/outputs`. It goes back to the caller,
  not into later sessions, so whether that matters is the owner's call. *(2026-10-30.)*
- **One folder for everything the agent makes.** #603 put the scratchpad inside the
  returned folder, as a subfolder that is never returned; it was closed unmerged, and its
  branch is gone but GitHub keeps it: `git fetch origin pull/603/head`. Its other two
  halves landed apart: a save outside every folder is refused (#1066), and the shell is
  told where it may write (#760, #1070). It predates `inputs` and `outputs`, so it is a
  reference rather than a rebase. Rebuild, or drop it. *(2026-10-27.)*
- **A second folder table.** `core/access` has the only one. Whether another folder
  gets one is judged at the next name moved inside `core/access`, by how many importers
  that move had to touch. *(2026-10-19.)*
- **Endpoints whose credential is fetched in code**, with neither `key:` nor
  `key_env:`. Parked for a grilling of its own. *(2026-10-14.)*
- **A tool the agent and the grant spell differently is dropped.** `Capabilities.intersect`
  compares names as written, before anything spells them, so an agent declaring
  `pay.py::line_count` under a grant of `line_count`, or the other way round, runs
  without `line_count` and nothing refuses. Measured through `build_agent`; one spelling
  on both sides keeps it. Spelling both before the intersect changes what a run is
  handed, so it is the owner's call. The withheld report in `core/reporting.py` compares
  a skills grant with `registry.names` as written too, so a request granting
  `catalogue::review` for a unique `review` is told `review` was not granted though it
  holds it; it waits on the same decision. *(2026-11-11.)*
- **The suite at the floors, in the `floors` job.** The job loads every public name and
  runs no tests, so a floor that installs and loads but behaves wrongly passes it:
  `langchain-anthropic` 1.7.5 did, with a window five times too large, and only a local
  run of the suite at the floors found it. Since #1128 the whole suite passes there.
  Running it in the job means a PR whose test relies on a newer release must raise that
  floor in the same PR, which is the catch, and costs one more suite run per PR.
  *(2026-10-29.)*
- **A gate on a tool the agent can never reach.** An agent may gate a tool that lives in
  a subagent's own folder. Startup accepts that for any subagent in the definitions,
  including one the agent does not name. Such an agent loads, but the gate never stops
  anything, because nothing the agent calls holds the tool. Startup already refuses a
  gate on a name no tool has, for the same reason. Checking only the subagents the agent
  names would close this, and could refuse a deployment that loads today.
  *(2026-11-01.)*
- **Telling the client what #1165 and #1162 change.** #1165 is what they asked for: a
  copied agent no longer names skills or subagents it has no tool to use, and it keeps a
  gate on a tool from a subagent's own folder, which `kingfisher list --as` now shows.
  #1162 they must hear before upgrading: an agent writing `source_ids` on a `skills:`
  entry stops kingfisher starting until the lock moves into `skill_locks.yaml`, and the
  refusal writes the lines out when only one agent locked the skill. One skill can no
  longer be locked two ways in two agents; the "access — by skill" part of `kingfisher
  list` on their current version shows whether any of theirs is, so ask for it first.
  The note is not written yet. *(2026-11-02.)*
- **Whether `pdf_attachments=` stays a keyword on `Kingfisher`.** It is there because the
  hook is handed kingfisher's verdict on each PDF a tool returns: fine, too big, or too
  many open. A deployment's own middleware runs inside kingfisher's and never sees that
  verdict. If the deployment's hook decides without reading it, the same work fits in a
  middleware and the keyword can go. Ask the deployment what its hook reads.
  *(2026-11-01.)*
- **Every definition read per turn, from a source that may be remote.** Tools, agents,
  subagents, skills and middleware fetched from an API, or from a wrapper over today's
  folder. Agreed: a session keeps only its agent's name and reads the definition fresh,
  so widening an agent's `source_ids` opens its old sessions' results to the new callers,
  and a model change sends old history to the new model; a session whose agent is gone
  reaches no scoped caller. A turn runs on one whole checked version, kept to its end and
  reused while unchanged, and a version failing its check is never swapped in and is
  reported once. The source refuses an invalid version at publish with kingfisher's own
  checker, and kingfisher checks each new one again, about 85 ms. A version is files laid
  out as `assets/` is, so publishing one is running code as the deployment, and
  `skill_locks.yaml` is one of them. A paused turn is refused on resume when anything its
  agent uses changed. A session's datazones stay pinned, and a current version that fails
  at startup still stops it. Built, in four slices: a turn takes its definitions once,
  from `core/versions.py`; a pause records the files its agent was built from; a new
  version per turn, skills still read from the folder as the turn runs; and the session
  keeps only its agent's name. Left: the source a deployment supplies, which waits for a
  real API and gives each version a folder of its own, its checker published as a
  function the API runs at publish.
  *(2026-11-02.)*
- **A skills registry that holds each skill's lock.** Grilled part of the way. The
  owner's skills are to come from outside, written to a standard with an access field
  of its own, and a registry service would keep those rules in place of
  `skill_locks.yaml`. Agreed so far: the registry hands kingfisher the rules only, a
  skill's name and its `source_ids`, converting its own field into them; the skill files
  stay on disk, a vendor's pack mounted with `KINGFISHER_SKILLS_MOUNTS` and locked as
  `acme::ledger-qa`, which works on 29d9de2a. Kingfisher asks about the skills it has,
  so a rule for one it lacks is never read. Open: whether it asks once at startup, where
  the catalogue and the names a caller may hold are read today, or per turn, which the
  entry above would bring, and with it perhaps the registry as that source rather than
  a hook of its own; what the field looks like, since an all-of requirement has no
  spelling in a lock (*Not taken: `sources` and `data_zone`* in `decisions.md`); and
  where the registry is chosen. A keyword on `Kingfisher` would not reach `kingfisher
  list`, `doctor`, or the places that read the definitions from the folder themselves,
  and each would read the missing file as every skill open. A lock still says only who
  is told of a skill: the shell reads every one. *(2026-11-02.)*
- **A second agent framework beside deepagents.** Parked by the owner, for more thought;
  `decisions.md` still leaves another framework out of scope (*The core and its
  plugins*). Where the grilling stopped: Pydantic AI, chosen once per deployment by a
  setting, with one definition format and deepagents' file layer on both. Probes with
  scripted models found a pause inside a subagent resuming in another process, grants,
  and LangChain tools through a short bridge all working, and `wrap_model_request`
  calling its handler only once, which the re-ask in `ModelReplyMiddleware` would have
  to change. Not reached: middleware, and subagents that build their own graph, both
  LangGraph's. *(2026-10-31.)*

## Waiting on a run

- **Whether a changed memory block costs Anthropic the whole prompt**, as it costs
  MiniMax: there the turn after the writer changed memory kept about a quarter of its
  input cached. Two turns on an Anthropic model, memory changed between them, settle it.
  And how often a real deployment's sessions are summarised on M2.5, whose summariser
  fires at 170,000 tokens, which needs that deployment's sessions. *(2026-10-27.)*
- **`scripts/session_survey.py` on the client's deployment.** Three questions wait on its
  numbers: whether per-session memory earns its writer runs, what replaying old tool
  output costs in speed and window as a session grows, and what sessions hold on disk.
  It answers the M2.5 one above as well. It reads a sessions folder, and
  the run log when given one, and prints counts only -- nothing anyone said, no session
  ids -- so its output can be sent back as it is. *(2026-11-09.)*
- **`scripts/turn_deadline_survey.py` on the client's deployment.** Whether to stop a
  turn's work at its limit, which #1201 left open, waits on how often real programs and
  subagents run near or past it. Measured with test models under a 2s limit: a program's
  tool calls kept starting after it, 4 of 6, built-in ones too; a subagent ran all its
  calls though the turn was reported cut short, and with #1188 merged in its tool calls
  stopped but 4 of its 7 model calls still came after the limit. A refusal in
  `GuardedTool` stopped the offered tools and not `write_file` from the same program.
  Since 2026-11-08 an `execute` from a program is refused a `timeout` past
  `KINGFISHER_EXECUTION_TIMEOUT_S`, as a direct one is; that it can still start after the
  limit is unchanged. The survey reads the run log and prints counts only -- nothing anyone said, no ids, no
  offered tool's name -- so its output can be sent back as it is. *(2026-11-04.)*
- **What slots cost, on a real deployment.** A slot keyed on a name no tool lock uses
  gives two grants with one tool list two system prompts and a cache entry each; a
  subagent's prompt grows with every slot its grant fills. On the shipped assets neither
  happens: six grants opening `analyst` give three cached prefixes, all from `task`'s
  description, the `figures` slot adds 218 characters for an EMEA caller, and no shipped
  subagent fills a slot. Count both on one deployment's real grants. *(2026-10-30.)*
- **The four `tm-*` clearing trials, re-run** now that clearing counts with the
  summariser's own counter. That re-run was the acceptance agreed for the change.
  *(2026-10-22.)*
- **The fence-check service on a real node.** `compose.fence-check.yaml` has only ever
  run its refusing paths: `FENCE HELD` needs Landlock ABI 6 or later inside the
  container, CI's runner has it but never runs the container, and Docker Desktop has no
  Landlock. A green CI is not this proof. *(2026-09-22.)*
- **One `kingfisher list` on the client's next redeploy.** Lock names are collected from
  the definitions now, so a misspelt one opens nothing rather than being refused at
  startup; the listing shows every name in use and the files using it. *(2026-10-20.)*
- **The client's answers on the overflow request.** Three questions went with the note on
  what landed: whether their gateway's window is smaller than the `max_input_tokens`
  their models give, whether what they saw was a refusal or an unexpected summary, and
  which model rows ran. A summary on a single batch reopens clearing acting before the
  summariser's own check, which *A refusal for length is clearing's to answer first* in
  `decisions.md` measured and left accepted. *(2026-10-23.)*
- **The client's answers on turns that stop or die.** Two questions, each changing what
  is built next. Does it cancel turns, or run `astream` under a web framework that
  cancels on a disconnect? Since #1188 a cancelled turn waits up to
  `KINGFISHER_CANCEL_WAIT_S` for the calls it has on threads, and its session stays busy
  until they end, so a deployment that cancels often feels that wait and the refusals
  after it. And what does it do on `SessionBusyError`, and how often do its pods die? A
  dead turn holds its session until its claim lapses, the turn limit plus the longest
  model or command bound, a little over an hour by default, so frequent deaths are felt
  first as that lock. Only a turn taking over the dead one's claim is told it never
  finished (#1288), so a deployment opening a new session on `SessionBusyError` never
  hears it. *(2026-11-10.)*
- **What a turn that never finished had done.** #1288 tells the next turn only that the
  one before it never finished, not what it was asked nor which tools it started. Saying
  that needs a record written as a turn goes, a write per turn and per batch of calls on
  every turn for the rare one that dies, and *A turn after one that never finished is
  told so* in `decisions.md` holds it until a deployment shows its turns die often and it
  retries in the same session, which the entry above asks. Unmeasured: whether naming
  the calls stops an agent with no tool to check from repeating them. The batch behind
  #1288 tried the plain line and one telling the agent to ask the user, and both sent the
  reminder again 6 times in 6. *(2026-11-10.)*

## When deepagents moves

It is held at 0.7.32 by the owner's call, which `pyproject.toml` records. Past it,
measured at 0.7.38: langchain's refusal check, which 0.7.38 wires in, reads every reply's
`content_blocks`, so a reply whose reasoning langchain cannot read fails the model call
and the turn with it, where at 0.7.32 only its log line lost the block;
`test_a_reply_whose_reasoning_cannot_be_read_still_logs_its_turn` goes red there.
*(2026-11-14.)*

At 0.7.32, not at 0.7.22, every run ends on a step of deepagents' own, `_ParentNotifierMiddleware`'s
`after_agent`, which `StepBudget` does not reserve: a turn that answers on the call it was
told left it one more ends `max_steps` with its answer kept, as one answering on its last
already did. Whether to warn a step sooner is open. *(2026-11-14, deepagents 0.7.32.)*
