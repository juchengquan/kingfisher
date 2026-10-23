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

- **One folder for everything the agent makes.** #603 put the scratchpad inside the
  returned folder, as a subfolder that is never returned; it was closed unmerged and its
  branch, `scratchpad-under-derived`, kept. Its other two halves landed apart: a save
  outside every folder is refused (#1066), and the shell is told where it may write (#760,
  #1070). The branch predates `inputs` and `outputs`, so it is a reference rather than a
  rebase. Rebuild, or drop it and delete the branch. *(2026-10-23.)*
- **Stopping deepagents' own writes to `large_tool_results/`.** Its offloading is off,
  but its summariser still writes there on its own over-budget check and when clearing's
  retry fails, as the comment on `OFFLOADS` in `core/layout.py` says. Clearing acting
  before that check would stop it, and needs a grilling of its own. *(2026-10-22.)*
- **A second folder table.** `core/access` has the only one. Whether another folder
  gets one is judged at the next name moved inside `core/access`, by how many importers
  that move had to touch. *(2026-10-19.)*
- **Endpoints whose credential is fetched in code**, with neither `key:` nor
  `key_env:`. Parked for a grilling of its own. *(2026-10-14.)*

## Waiting on a run

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

## Small fixes, not yet made

- **`http_fetch` fails on every HTTPS address under a Python with no certificate
  store**, such as python.org's macOS build, because it uses plain `urllib`; `curl` and
  model calls work, since httpx carries certifi. Proposed: the system store when it has
  certificates, certifi's bundle otherwise, a clear error with neither, and a `doctor`
  check. Deferred by the owner. *(2026-10-17.)*
- **A subagent started by `task()` from inside `eval` is recorded as the agent's own
  work**, with no run: `_running` in `recorder.py` starts a run only for `task`.
  Measured, starting one for `eval` too files it under the `eval` call, in both loops,
  with no change to how the delta is drawn. *(2026-10-17.)*
- **`decisions.md` still says expiry removes deepagents' offloaded copy**, in *Marked
  when the turn's ledger is built, removed by the sweep*. Nothing has been offloaded
  since 2026-10-21, so that sentence describes code that is gone. *(2026-10-23.)*
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
