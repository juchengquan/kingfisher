# Ongoing

Work that was started or agreed and is not finished, and what each item waits on.

Each entry ends with the day it was last checked. Check it against the code before
building on it: the rule [`README.md`](README.md) gives `design/` holds here too.

An entry leaves when its work lands or is dropped. What was decided goes to
`decisions.md`; this page keeps no history.

Not repeated here: proposals in `design/`, which `README.md` lists, and what the ledger
left unbuilt, under *Not built, and still open* in `decisions.md`.

## In review

- **The security findings and bugs from the review of #1039–#1068**, in #1071.
  *(2026-10-23.)*

## Waiting on a decision

- **Keeping the cache through a memory change on MiniMax.** The breakpoint ahead of the
  memory block saves little there, so each change the writer makes costs the next turn
  most of its cache; `KINGFISHER_MEMORY_EVERY` went to 4 to pay it half as often. Moving
  the block, or asking MiniMax how it matches a prefix, needs a grilling of its own.
  *(2026-10-27.)*
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

## Waiting on a run

- **Whether a changed memory block costs Anthropic the whole prompt**, as it costs
  MiniMax: there the turn after the writer changed memory kept about a quarter of its
  input cached. Two turns on an Anthropic model, memory changed between them, settle it.
  And how often a real deployment's sessions are summarised on M2.5, whose summariser
  fires at 170,000 tokens, which needs that deployment's sessions. *(2026-10-27.)*
- **What slots and turn text cost, on a real deployment.** A slot keyed on a name no
  tool lock uses gives two grants with one tool list two system prompts and a cache entry
  each; a subagent's prompt grows with every slot its grant fills. Count both on one
  deployment's real grants. And one trial of whether the model keeps quiet about who
  the user is: a user's name in the turn text, a task inviting a report, and a look at
  `/outputs` and `/memory/AGENTS.md` after. *(2026-10-23.)*
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

## Small fixes, not yet made

- **`http_fetch` fails on every HTTPS address under a Python with no certificate
  store**, such as python.org's macOS build, because it uses plain `urllib`; `curl` and
  model calls work, since httpx carries certifi. Proposed: the system store when it has
  certificates, certifi's bundle otherwise, a clear error with neither, and a `doctor`
  check. Deferred by the owner. *(2026-10-17.)*
- **`decisions.md` still says expiry removes deepagents' offloaded copy**, in *Marked
  when the turn's ledger is built, removed by the sweep*. Nothing has been offloaded
  since 2026-10-21, so that sentence describes code that is gone. *(2026-10-23.)*
- **The PDF allowance counts four characters a token.** `pdf_allowance` in
  `attachments.py` gives a turn half the summariser's trigger at four characters of
  base64 a token, but langchain counts a PDF block by its `repr` at the summariser's
  own rate, 3.3 on an Anthropic class, scaled up to 1.25 by reported usage. So a turn's
  PDFs fill some 60% of the trigger rather than half, before any scaling. The same
  mismatch clearing had; reading the rate off `summarizer_count` would close it.
  *(2026-10-23.)*
- **Names that still call the definitions folder a workspace.** #1086 changed the
  wording but left identifiers: `defined_in_a_workspace` in `core/definitions/importing.py`,
  and three tests whose messages no longer say "workspace" --
  `test_a_caller_who_reaches_no_agent_is_told_the_workspace_offers_none`,
  `test_a_workspace_with_no_agents_is_told_how_to_write_one` and
  `test_a_workspace_with_no_agents_says_so_and_says_what_to_do`. *(2026-10-23.)*
- **The "In review" entry for #1071 is stale**: #1071 has merged, so the entry and its
  heading go. *(2026-10-23.)*

## When deepagents moves

It is held at 0.7.22 by the owner's call, which `pyproject.toml` records. 0.7.24 retries
an overflowing summary call, which kingfisher's version does not. 0.7.23 adds
`pinned_skills`, a hook that costs a graph step on every model call while skills are on,
which `NarrowedSkills` would have to drop. *(2026-10-13.)*

## Branches kept

Every other merged branch was deleted, here and on GitHub, on 2026-10-27.

- **`clearing-counts-like-summariser`**, merged. Its worktree,
  `.claude/worktrees/clearing-counts-like-summariser`, is locked by a Claude session still
  running; remove the worktree and the branch once that session ends. *(2026-10-27.)*
