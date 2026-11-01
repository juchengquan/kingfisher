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
  `/.harness/memory_writer.json`, and the user's other sessions never see it. Session
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
  handed, so it is the owner's call. *(2026-10-27.)*
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
- **Telling the client their copied agents load.** What they asked for landed in #1165:
  a copy no longer names skills or subagents it has no tool to use, and it keeps a gate
  on a tool from a subagent's own folder. They will also see those gates in
  `kingfisher list --as`, which did not show them before. The note to them is not
  written yet. *(2026-11-01.)*
- **Whether `pdf_attachments=` stays a keyword on `Kingfisher`.** It is there because the
  hook is handed kingfisher's verdict on each PDF a tool returns: fine, too big, or too
  many open. A deployment's own middleware runs inside kingfisher's and never sees that
  verdict. If the deployment's hook decides without reading it, the same work fits in a
  middleware and the keyword can go. Ask the deployment what its hook reads.
  *(2026-11-01.)*
- **The hooks are listed twice in the tests.** `test_architecture.py` holds their names,
  to cap how many there are, and `test_hook_wiring.py` holds each name with its
  interface. A new hook is written into both, and missing either turns a test red, so
  nothing slips; it is only one place too many. Merging them into one list is a small
  tidy-up whenever someone is next in either file. *(2026-11-01.)*

## Waiting on a run

- **Whether a changed memory block costs Anthropic the whole prompt**, as it costs
  MiniMax: there the turn after the writer changed memory kept about a quarter of its
  input cached. Two turns on an Anthropic model, memory changed between them, settle it.
  And how often a real deployment's sessions are summarised on M2.5, whose summariser
  fires at 170,000 tokens, which needs that deployment's sessions. *(2026-10-27.)*
- **`scripts/session_survey.py` on the client's deployment.** Three questions wait on its
  numbers: whether per-session memory earns its writer runs, what replaying old tool
  output costs in speed and window as a session grows, and what sessions hold on disk. It
  answers the M2.5 one above as well. It reads a sessions folder, and the run log when
  given one, and prints counts only -- nothing anyone said, no session ids -- so its
  output can be sent back as it is. *(2026-11-01.)*
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

## When deepagents moves

It is held at 0.7.22 by the owner's call, which `pyproject.toml` records. 0.7.24 retries
an overflowing summary call, which kingfisher's version does not. 0.7.23 adds
`pinned_skills`, a hook that costs a graph step on every model call while skills are on,
which `NarrowedSkills` would have to drop. *(2026-10-13.)*
