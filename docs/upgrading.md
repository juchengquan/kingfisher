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

## Redacted thinking kept as `v1` goes back only to its signer too, after #9999

- **A reply kept under `output_version: v1` loses another signer's redacted thinking from
  the request.** `v1` wraps Anthropic's `redacted_thinking` as a `non_standard` block, which
  #1048's strip did not look inside, so a session that moved from MiniMax to Claude with
  `v1` set sent MiniMax's redacted thinking on. Nothing to change; the conversation keeps
  the block.

## A `ScratchRunLimit` grants a run only by answering `True`, after #9999

- **An answer that is neither `True` nor `False` refuses that graph's runs.** It was read by
  its truth, so an `async def allows` -- a coroutine, which is truthy -- handed every graph
  a run, with only a "never awaited" warning to show for it, and `1` or `"yes"` did the
  same. Now such an answer is treated as a raise is: logged once per graph, and each run
  a tool asks for answers `refused: this deployment's ScratchRunLimit answered coroutine,
  not True or False`, the type named. A coroutine is closed. `None` from a limit that
  forgot to return is refused as before, now with that line instead of the generic one.
- **If yours is `async`, make it synchronous**: look the policy up before the service is
  built.

## The shell may not write the workspace's retired `PROMPT.md`, after #9999

- **On macOS the profile denies `<workspace>/PROMPT.md`**, beside `models.yaml` and the keys
  file. A start that finds that file refuses, so one session's shell writing it --
  `echo x > "$KINGFISHER_SESSION_ROOT/../../PROMPT.md"`, which the profile allowed --
  refused every later start of the deployment. Such a command now gets `Operation not
  permitted`. The Linux fences grant nothing above the session and needed no change.
- **`Config.retired_prompt` is new**: that path, which the start's own check reads too.

## The memory scaffold writes through no link, after #9999

- **`memory/AGENTS.md` is filled only when it is missing or an empty regular file.** It was
  written through whatever stood there, so a link the shell left made or filled a file
  outside the session, or in `.harness`, from kingfisher's own process. A link, a folder or
  anything else there is left as it is, and the session opens.
- **A folder there no longer stops a session opening.** `read_text` on it raised
  `IsADirectoryError` at every open, and the file tools alone can make one. With memory on,
  each turn still fails, in deepagents' memory load, naming the file: move the folder aside
  to use memory again.

## A link out of a route is the operation's error, after #9999

- **`read_file`, `write_file`, `edit_file`, `delete` and `ls` on a link out of a route's
  folder now answer an error** -- `… it is or runs through a link out of its folder, and
  the file tools follow none` -- where the turn used to end with a `ValueError` naming
  the host's paths. That covers `/memory`, the kept records, `/inputs`, `/skills` and each
  bundle's skills. A link that stays inside its route is followed, as before.
- **Every route `backend_at` builds is a `RootedBackend`**
  (`kingfisher.plugins.sessions.links`), a `FilesystemBackend` with only that change. A
  deployment that reads a route's class sees the new one.

## A session's folder spelled in other capitals is refused, after #9999

- **A file tool's path that spells one of the session's folders in other capitals is
  refused**, naming the spelling that is: `/.HARNESS/x`, `/Inputs/x` or
  `/scratchpad/TOOL_RESULTS/x` is answered `… spells a folder of this session in other
  capitals, and a path here is matched exactly as written: it is /.harness/x.` On macOS's
  default disk such a path used to reach the real folder past every deny rule. A tool
  handed `runtime.context.backend` is refused the same paths, `invalid_path` in a batch,
  and `on_this_host` raises `UnsafeReferenceError`.
- **`/Inputs/x` is no longer the save outside every folder.** It names a folder in other
  capitals, so it gets the new refusal; a top folder the layout does not name at all
  still gets #1066's.
- **A file or folder the agent names inside its own folders is unchanged**, whatever its
  capitals: `/outputs/Report.csv` is its own spelling.

## The shell says where its writes go, and a stray one is told so, after #1070

- **The shell's section of the prompt names the two folders it may write**, `/outputs`
  and `/scratchpad`, and says everywhere else is refused -- `/inputs`, what kingfisher
  keeps, and the folder the shell starts in, which is the session's top. It said how to
  spell a path and nothing about which folders would take one, so a bare `> out.csv`
  wrote where every fence refuses. This is what kingfisher's own shell says of itself; a
  deployment supplying its own `ShellPrompt` says its own.
- **A refused command whose path no folder of the session holds gains a note**, in the
  words a kept folder's refusal has had since #1065:

  ```
  `/out.csv` is not yours to write: the shell is refused there, as the file tools are.
  Save what you make under `/outputs/<name>` to hand it back, or under
  `/scratchpad/<name>` to work on it.
  ```

  A path under a folder your layout does name is left alone, since a fence allows those
  and a refusal there is something else; so is a path outside the session; and so is a
  bare file name, `/bin/sh: out.csv: ...`, which has no slash to tell it from a word. If
  you assert on `execute` results' text, one more refusal carries a note.
- **`kept_note` is joined by `stray_note`, and `kept_under` by `strayed_under`**, both
  in `builtin_tools`. The notes share their sentence. `strayed_under` reads a refused path
  against the folders a file tool may save in -- your layout's, plus what your backend
  mounts -- rather than the folders a fence keeps read-only.

## A batch's results are numbered as the model wrote them, after #1069

- **Which id a call's result takes has changed**, where one message asked for more than
  one call. A message's calls run at once, and each took its number as it reached the
  recorder, so `A-0001` could name the second call's result; it now follows the call's
  place in the message. Nothing else about a row moved. If you assert on the ids a batch
  leaves, or on the file names they make -- `/scratchpad/tool_results/A-0001-<tool>.txt`
  -- they read in the message's order now, as `<ledger_delta>` lists them.
- **A batch's numbers are all taken when its first call starts.** A turn cut off part
  way through a batch leaves the rest unused, as a call that fails already did, and the
  next turn numbers past them.
- **`Ledger.reserve` takes the call, its batch and the reply it was written in.** An
  internal address, so this is for a deployment driving a recorder of its own:
  `reserve(call_id, batch, message=message_id)`, where `batch` is the recorded calls of
  that reply in the order the model wrote them. `made_together` in `recorder` reads both
  off the graph's state.

## Every refusal of a write offers somewhere to write in one wording, after #1068

- **The three refusals that offer somewhere to write now say it one way.** A deny rule's
  refusal (#1063), the note on a shell command the fence refused (#1065), and a save
  outside the session's folders (#1066) each worded the offer themselves, the last two
  on the same day. All three now close with `what_you_make_goes`, in `prompting`, which
  is #1066's wording:

  ```
  Save what you make under `/outputs/<name>` to hand it back, or under
  `/scratchpad/<name>` to work on it.
  ```

  in your layout's names -- it says what each folder is *for*, which is what choosing
  between them needs. The three sections below quote what each refusal said before, so
  if you assert on a refused write's text, re-read it.
- **A path that climbed out of the session reads as it did.** That refusal answers with
  the shape a tool path has, and names an inputs path as a spelling rather than a
  destination, so it is not a fourth: offering it as somewhere to write would make it
  wrong.

## A save outside every folder of the session is refused, after #1066

- **`write_file` and `edit_file` refuse a path whose top folder your layout does not
  name**, such as `/report.md`, or `/outputs/x` where you renamed `outputs`. The error
  names your outputs and scratchpad folders. A folder your backend mounts as a route of
  its own is still saved in. If your instructions send the agent to save anywhere else,
  point them at the outputs folder or mount the folder.
- **A tool handed `runtime.context.backend` is refused the same saves.** `write` and
  `edit` return the message as `error`, and `upload_files` returns
  `error="permission_denied"` for that path.
- **The shell cannot write at a session's top** on macOS or under bubblewrap, as it
  already could not under Landlock. A command that writes into its working directory,
  such as `python script.py > out.csv` run from the session, now fails. Write under the
  outputs folder or `$TMPDIR` instead. An unfenced shell is unchanged.

## A refused shell command says which folder is kept, after #1065

- **A shell result the fence refused gains a note.** Where a command was refused for
  writing under a folder kingfisher keeps -- the caller's inputs, `.harness`, or any of
  the kept records -- the result now ends with:

  ```
  `/inputs/` is not yours to write: the shell is refused there, as the file tools are.
  What you make goes under `/outputs/<name>` or `/scratchpad/<name>`.
  ```

  in your layout's names. Nothing is appended to a command that ran, to one refused for a
  path outside the session, or to one whose path was spelt the file tools' way, which
  keeps the correction it already got. If you assert on `execute` results' text, they are
  not the same.
- **`BuiltinToolsMiddleware` takes `root=` instead of `rooted=`.** The session root as the
  shell sees it, which is what `rooted=True` was standing in for; `standing_middleware`
  passes `session_root_of(backend)`, as it already did to compute the flag. A deployment
  building the middleware itself passes the root, and `None` keeps both corrections off.
- **Nothing a fence allows or refuses has moved.** The profiles are the same and the
  folders are the same; only what the model is told afterwards changed.

## A long result your backend would not keep is previewed, after #1064

- **The model is shown a preview, not the whole result**, when your backend refuses or
  raises on a save under `/scratchpad/tool_results/`. #1048 handed such a result over
  whole, and one can be any size, so it could overflow the window. The preview is cut as
  a kept one is, and its last line is `full result not kept; call <tool> again for less`
  where a kept one's is `full result: <path>`; a deployment that parses previews reads
  both. The row still says it was not kept.

## A summarizer a helper's definition names runs where deepagents' would, after #1064

- **It now runs outside the standing middleware**, where a helper's default summarizer
  runs and where deepagents puts one a declared subagent names. It ran innermost, inside
  clearing: it counted each call after clearing had, and a call refused for length
  reached it before clearing could answer it (#1056), so it answered the refusal itself,
  copying the latest batch under `/scratchpad/large_tool_results/` and summarizing where
  there was something to summarize. A helper naming none, and every other graph, is
  unchanged.

## A switch sends no reasoning kept aside, and no reply left empty, after #1064

- **A Responses reply kept as `v0` loses its reasoning on a switch too.** A model built
  with `output_version="v0"`, through `extra` or `LC_OUTPUT_VERSION`, keeps its reasoning
  in `additional_kwargs["reasoning"]`, and `ChatOpenAI` sent it to whichever endpoint came
  next. It now goes back only to the endpoint and model that produced it, as thinking in
  the content has since #1048. The conversation keeps it.
- **A reply that was only another signer's thinking is left out of the request.**
  Stripped, it went to `ChatAnthropic` empty, which sent the user turns either side of it
  as two in a row, or, last, sent it as an empty prefill the current Claude models refuse.
  A reply with tool calls still goes, as its calls. The conversation keeps it.

## `seed` reads an audience held in a name, and holds back one it cannot read, after #1064

- **A Python definition whose `source_ids=` or `datazone=` is a name is held back now.**
  Seed read only what was written out, so `ToolSpec(t, source_ids=IDS)` was copied as a
  tool naming nobody, and switched on access control where it landed. A name the module
  binds once at its top level is read through, and the line names what it holds.
- **Anything else seed cannot read is held back too**, named as the file writes it:
  `skipped tools/x.py — names an audience seed cannot read without running it (t:
  source_ids=IDS), ...`. That is an imported name, one bound more than once or inside a
  function, and a computed value such as `['A'] + MORE` or `zone()`. `--all` copies it.
- **A tool module that passes `source_ids=` to a call of its own** with a value from a
  parameter is held back the same way, since seed cannot tell it from a `ToolSpec`.
- **`Skipped.unread`** is new: what a held-back file computes, one entry per value.

## A refused write says where a write may go, after #1063

- **A refused write's message is longer.** A write, edit or delete a deny rule refuses
  now reads:

  ```
  Error: permission denied for write on /inputs/x.txt -- that path is not yours to
  write. What you make goes under `/outputs/<name>` or `/scratchpad/<name>`.
  ```

  in your layout's names, where before it was deepagents' code and the path alone. If you
  assert on a refused tool result's text, or diff tool messages between runs, they are not
  the same.
- **The skills section of the prompt gained a sentence**, saying the catalogue is shared
  and read-only and that what a procedure should say differently belongs in the answer.
  It appears only where skills are wired. If you cache the system prompt or diff it, the
  text has changed.
- **Nothing a rule allows or refuses has moved.** The scopes are the same, the deny rules
  are the same, and a refusal is still a failed result the turn goes on past.
## A session is made with `scratchpad/large_tool_results/`, after #1062

- **Every session has the folder, empty, from the next time it is opened.** deepagents'
  summariser writes there part-way through a turn, on a call refused for length that
  clearing could not answer (#1056), and bubblewrap binds a folder read-only only if it
  is there when the turn's fence is built, so the shell could rewrite what it wrote until
  the turn ended. `ls /scratchpad` names the folder now.
- **A deployment that calls `backend_at` on a session directory itself** calls
  `ensure_session_layout` on one made before this first, or is refused with a message
  saying so. `LocalSessions.open` does that on every open.
- **`Layout.offloads` is new**: the folder as a path inside the session, beside
  `Layout.history`.

## A caller's datazone of `"*"` is refused, after #1060

- **`datazone="*"` is a `CallerError`**, on every door that takes one, `--datazone`
  included. It was held as a datazone named `*`, which no definition may require since
  a definition's `"*"` is refused (#1048), so it reached nothing zoned and pinned the
  session a turn ran in to `*` for good. A caller names the one zone it is in, or passes
  none, which still means none. A session already pinned to `*` keeps the pin, which no
  caller can name now; `UNSCOPED` still reaches it.

## Every subagent is told what the session's folders are for, after #1058

- **A subagent's prompt now opens with the session section.** Every declared subagent,
  helper and `general-purpose` is sent what `/inputs`, `/outputs` and `/scratchpad` are
  for, in your layout's names, then your instructions, then its own `system_prompt`.
  Before, its prompt began with your instructions. If you cache a subagent's prompt, diff
  it, or tell subagents apart by how their prompt opens, the text is not the same.
- **The agent's prompt is unchanged.** The section moved to `prompts/session.md`, and
  `system.md` includes it where it was.
- **A compiled subagent is untouched.** Its `build` still writes its whole prompt.

## The prompt says whose the kept records are, after #1057

- **Two sentences of the system prompt have changed.** The clause #1036 added to the
  `/scratchpad` bullet had no subject, so it read as a rule against writing the
  scratchpad itself; it now names the folder it is about, and the results folder is said
  to be read-only where the prompt sends the model to read a result back. If you cache
  the prompt yourself, or diff it, the text is not the same.
- **Nothing a deployment does changes.** The rules were already in force since #1036;
  only what the model is told about them is new.
- **`ports.md` now says the shell must stay out of the kept records.** `shell_denied`
  still does not ask for it, for the reason the guide gives, so a backend of your own
  that lets its shell write `/scratchpad/tool_results/` or
  `/scratchpad/conversation_history/` passes the kit and lets the agent forge a result.

## A call refused for length is answered by clearing, after #1056

- **A refusal for length is asked once more, with everything clearable cleared.** It
  used to reach deepagents' summariser, which summarised the conversation and wrote the
  latest batch to `scratchpad/large_tool_results/` under each call's id. Now clearing
  answers it first, by the summariser's own test for one, the latest batch and the three
  most recent results included, and the summariser sees the refusal only if the retry is
  refused too or there was nothing to clear. No setting turns it on or off.
- **A handle can read two new ways**, for a result in the batch the refused call was to
  show: `(N bytes, cleared before it was shown, the conversation being too long for it:
  full result: <path>, read it a part at a time)`, and for a file tool's `... cleared
  before it was shown, ...; call read_file again with offset and limit`, or `again
  narrowed` for a search. A deployment that parses handles should read these too.
- **A turn that ran into the refusal before** may still find
  `scratchpad/large_tool_results/` in its session; it is read only through the stub
  that names it.
- **The summariser still summarises a request its own count puts over the input
  budget**, before the call is sent, and clearing never sees that. A counter tuned to
  your gateway's counts makes that the usual path for a very large batch.

## A `ScratchRunLimit` that raises refuses runs instead of failing the turn, after #1054

- **An exception from `allows` no longer fails the build.** It is logged as a warning by
  `kingfisher.core.agent.middlewares.standing`, and that graph's tools are refused every
  run with an `error` naming it: `refused: this deployment's ScratchRunLimit raised
  KeyError: 'auditor'`. Every other graph in the turn keeps what it would have had. A
  deployment that relied on a raise to stop a turn should refuse the request itself.

## A failure names the path the call named, after #1053

- **A failed file-tool call now answers with the path the model wrote.** Under a route it
  used to answer with that route stripped off -- `/inputs/nope.txt` came back as
  `File '/nope.txt' not found` -- and a refused write answered with the host path the
  `OSError` carried. A deployment matching on the text of a failure, or testing against
  it, sees the virtual path now.
- **An offered tool's failure says it too.** Its `path` argument is still made real
  before the tool runs; only what a failure *says* changes, so a tool whose own
  `open()` fails no longer hands the model the host spelling.
- **Nothing else is rewritten.** Only a result marked `status="error"`, so file contents
  are untouched, and a command's output is untouched with them -- a command that fails
  is a tool call that succeeded.
- **`as_asked` is new in `kingfisher.core.agent.session_paths`**, beside `refusal_text`.
  Neither is exported; a deployment calling `GuardedTool._failed` directly passes the
  call's own arguments as its second argument.
## A clearing mark is held to the summarizer the graph runs, after #1051

- **A `clear_results_at` past deepagents' own trigger is accepted under a summarizer of
  yours that fires later.** It was refused when the model was built, against deepagents'
  170,000 on a model with no window, though a summarizer a definition names runs in its
  place. A graph left with deepagents' own still refuses it, in the same words, now
  when the agent is built rather than the model, which is the same moment for a turn.
- **`build_model` no longer refuses one.** It is not exported, so this matters only to a
  deployment that called it anyway.

## A caller's datazone is read as a definition's is, after #1051

- **A blank datazone is refused, and a padded one is the name inside it.**
  `datazone=""` or `"  "`, on any door that takes one (`run`, `stream`, `session`,
  `sessions`, `inventory` and `--datazone`), was held as a datazone no definition could
  require, and a session a turn ran in was pinned to it for good. It is now a
  `CallerError`, as a definition's blank datazone is refused at load. `" finance"` is
  `finance`: it reached nothing zoned finance before, and pinned its session to
  `' finance'`, which no finance caller could reach again. A session already pinned that
  way keeps the pin, which no caller can name now; `UNSCOPED` still reaches it.

## `seed` sees a requirement written `frozenset(...)`, after #1051

- **A Python definition whose requirement is `frozenset({...})` or `set(...)` is left
  behind by `seed`, as a set literal is.** The loader read either as the requirement it
  is, but seed saw only the braces, so it copied the tool as an open one, and the copy
  switched on access control in the workspace it landed in. Seed again with `--all` to
  copy one anyway.

## A PDF plugin that hands over nothing leaves kingfisher's verdict, after #1051

- **`""`, no blocks, or blank text alone from `PdfAttachments.replacement` is read as
  `None`**, and logged. It was taken as a replacement that became nothing, so the model
  was handed an empty result marked failed, with no line saying why. Now a PDF that
  passes is handed over and one past the limit is refused with kingfisher's reason. A
  plugin that meant to withhold a PDF should hand over text saying so.

## The prompt no longer sends the model to `grep` for a cleared result, after #1051

- **The sentence on a cleared result names only what its handle offers.** An agent
  holding `grep` was told to "`grep` for the id where the handle says to", which no handle
  has said since #1018. One holding `read_file` is told to read the file or call the tool
  again; any other, to call the tool again. Anything comparing the prompt against the old
  wording sees it change; the paragraph on searching saved results with `grep` stands.

## `doctor` names `KINGFISHER_SOURCE_IDS_FILE` as retired, after #1051

- **A deployment still setting it is told so.** #983 stopped reading it and stopped
  refusing it, and nothing said either: `doctor`'s *retired settings* row now names it,
  as a warning, and `.env.example` lists it there instead of among the settings a start
  refuses. A `source_ids.yaml` left in the definitions folder is still read by nothing
  and still not reported; delete it, as #983's section says.

## Doctor's `shell` row warns for sessions it cannot see into, after #1051

- **With sessions of your own, the `shell` row is a warning naming them.** It reported
  the fence kingfisher's own sessions would have run -- "confined by sandbox-exec" --
  and with `KINGFISHER_SHELL_SANDBOX` set to a value it does not take, it failed and
  dropped every row that asks the fence. Your sessions fence their shell themselves and
  read the variable only if built on `backend_at`, so a bad value is now said in the
  warning, and `doctor` no longer exits 1 on it. Kingfisher's own sessions refuse it as
  before.

## A `ScratchRunLimit` is asked about the agent once, after #1051

- **The agent's question comes once a build, as the interface always said.** A stack
  built only to learn the agent's tools, and thrown away, asked your limit about the
  agent too, so a limit that counts or logs saw the agent twice and its first answer
  was never used. Subagents and `general-purpose` were already asked once each.

## A `run_on` is refused where no subagent is granted, after #1051

- **A request naming a subagent in `run_on` is refused when it was granted none.** The
  check that refuses a name the request did not activate ran only where some subagent
  was granted, so with an agent whose file has no `subagents:` line, or a request
  passing `subagents=None`, the override was accepted and ignored -- a model the caller
  may not use included. It is now the `CapabilityError` the same name gets beside a
  granted subagent: drop the `run_on`, or grant the subagent.

## A deployment's shell can say where it sees the session, after #1049

- **`LocalSessions(session_root=...)` teaches your runner's agent
  `$KINGFISHER_SESSION_ROOT`.** A function of the session folder, as `runner` is; your
  runner must export the same value in its shell's environment. Leaving it out changes
  nothing: the agent is described neutrally and a missed path is not corrected. It is
  refused without a runner. `backend_at` takes `session_root=` too, as the value.
- **A shell of your own opts in with a `session_root` attribute**, a `str`, beside
  `shell_prompt`. Anything else is refused when the agent is built.
- **`ShellPrompt.starts_in_session` is gone.** It turned on the correction after a
  missed path, which named a variable the flag did not make anyone export; the
  correction now follows `session_root`. A `ShellPrompt(..., starts_in_session=True)`
  fails with a `TypeError`: drop the argument and set `session_root` instead.
  `BuiltinToolsMiddleware` takes `rooted=` for the same thing, which matters only to
  code that builds it itself.
- **Wording of your own that does not name `KINGFISHER_SESSION_ROOT` is followed by
  kingfisher's sentence on it** wherever `session_root` is set; `""` still says
  nothing.

## A preview shows a result's ends, and names only a file that is there, after #1048

- **A long result on one line is previewed by its start and its end.** The preview kept
  whole lines only, so minified JSON, or a result whose every line was wider than half
  the preview, showed the model `... [1 lines truncated] ...` and the path and nothing
  else. A line too long to show at all is now cut and marked `... [N characters
  truncated] ...`; results whose lines fit read as before.
- **A long result your backend would not keep is handed to the model whole.** It was
  previewed with a `full result:` line naming the file the save had just failed to
  write. A backend that refuses or raises on a save under `/scratchpad/tool_results/`
  now sends the model the whole result, and its row still says it was not kept.

## A lone `datazone=` name is refused like a listed one, after #1048

- **`source_ids: datazone=finance`, written without brackets, no longer loads.** The list
  form was refused and the lone name was not, so an agent written that way required a
  datazone through the one line that cannot say one, and only callers in `finance`
  reached it. `ToolSpec(tool, source_ids="datazone=finance")` took the same path. Write
  `datazone: finance` on a subagent, or `ToolSpec(tool, datazone="finance")`; an agent
  takes none.

## A datazone written `"*"` is refused, after #1048

- **`datazone: "*"` no longer loads.** It was read as a datazone named `*`, which no
  caller is in, so the subagent or tool was shut to everyone and access control was
  switched on; `seed` meanwhile took it for "any" and copied the file. Leave
  `datazone` out for any datazone, which is what `"*"` was meant to say. The same goes
  for `"datazone": "*"` in Python and `ToolSpec(..., datazone="*")`.

## A skill written bare and qualified is refused, after #1048

- **`skills: [audit, catalogue::audit]` no longer loads.** Both entries loaded and
  meant one skill, so the one saying no `source_ids` reached every caller the other
  kept out, with nothing in the access report. Startup now refuses the definition,
  naming both spellings, and `doctor` fails on its `skill entries` row; keep one entry,
  the one carrying the audience you mean.

## A helper may run your own `FilesystemMiddleware`, after #1048

- **A helper whose stack holds a middleware answering to `FilesystemMiddleware` now
  builds.** It failed on "Please remove duplicate middleware instances", because
  kingfisher put its own beside yours and a helper's stack, unlike every other, is never
  merged by name. Since #1033 a portable helper runs the agent's middleware line, so an
  agent naming one failed every turn that reached such a helper. Yours now takes the
  helper's first place, where deepagents puts it in every other stack, and kingfisher
  adds none beside it. It must still enforce `permissions` itself, as
  `docs/guides/middleware.md` says.

## A switch of model or endpoint sends no earlier thinking, after #1048

- **A reply's thinking goes back only to the endpoint and model that produced it.** A
  session moved to another model, or to another endpoint -- one renamed, or one whose
  `base_url` changed under the same name -- sends its earlier replies' text and tool
  calls as before, without their thinking. A MiniMax reply's thinking reached Claude
  signed by MiniMax, which Claude cannot check; between two models of one vendor the
  cost is the earlier reasoning, on that switch alone. A model you pass in rather than
  name in `models.yaml` is judged by its model name.
- **Each reply records who served it**, in `response_metadata["kingfisher_served_by"]`:
  the endpoint's name, its `base_url` and the model. Neither chat class sends it on, and
  the transcript does not keep it; the conversation kingfisher keeps between turns does.
- **`standing_middleware` takes `models=`**, the catalogue. It is not exported, so this
  matters only to a deployment that called it anyway.

## Kingfisher's shell exports `KINGFISHER_SESSION_ROOT`, after #1047

- **The agent is taught a different spelling for a session path in the shell.** It was
  told to drop a file-tool path's leading slash, `inputs/data.csv`; it is now told to
  put the session root in front of it, `"$KINGFISHER_SESSION_ROOT/inputs/data.csv"`,
  and to read it in a script as `os.environ["KINGFISHER_SESSION_ROOT"]`. That reaches
  the file after a `cd` or from a script run elsewhere, where the relative path missed.
  The shell still starts in the session root, so a relative path still works where
  nothing moved it. Anything comparing the prompt or `execute`'s description, or a
  recorded shell result's correction, against the old wording sees it change.
- **Kingfisher's own shell exports the variable**, the session folder's absolute path,
  under every `KINGFISHER_SHELL_SANDBOX` setting. A tool's `run` does not get it.
- **A shell of your own whose `ShellPrompt` says `starts_in_session=True`** now has a
  missed path answered with `$KINGFISHER_SESSION_ROOT`, so it must export that variable
  as its session folder too, or set `starts_in_session` false. A runner handed to
  `LocalSessions(runner=...)` is unchanged: it is described neutrally and not corrected.

## The step note leaves the cache breakpoint where it is read, after #1045

- **A call past 80% of a turn's steps now reads the cache the call before it wrote.**
  The step budget's note, sent last, took the conversation's breakpoint, so each such
  call paid to cache an entry no later call read. The breakpoint now goes on the last
  block the conversation keeps, and the same for a cut answer asked again; the count is
  still four with memory on.
- **deepagents' caching slot is kingfisher's on every graph that has one**, not only
  where `session_instructions=` is wired: a stack listing shows
  `PromptCachingMiddleware` there. A middleware of your own in that slot keeps it, as
  before, and is still refused beside `session_instructions=`.

## A helper summarizes, after #1044

- **A helper another subagent consults now runs deepagents' summarizer**, as the agent,
  each declared subagent and `general-purpose` already did. Its conversation was never
  summarized: a long job grew until the model refused the request, while its clearing
  mark and PDF allowance were sized as if a summarizer ran. It is now summarized where
  deepagents' own fires on the model it runs, and what it evicts goes to a history file
  of its own under `/scratchpad/conversation_history/`.
- **A summarizer the helper's definition names runs instead**, as in every other graph;
  a portable helper's comes from the agent's line. A middleware of yours on a helper that
  reads earlier messages may now find a summary where they were, as on any other graph.

## A refused request fixes nothing in its session, after #1043

- **A request refused at the build no longer pins its agent or its datazone.** Both
  were written before the build's refusals had run, so a session opened with
  `create=True` whose first request was refused there -- a stray `run_on`, say -- was
  fixed to that agent, and a refused finance request narrowed a session no zoned turn
  had run in, taking it from every caller in no datazone. Both are now written beside
  the inputs, once the build has passed and before any tool runs.
- **A backend refusing the upload leaves no `run_start` in your `RunEvents`.** The
  upload moved after the build in #913, which logged `run_start`, so that refusal left
  a turn start with no `run_end`. `run_start` is now the last thing setup does.

## A link where a kept record would be refuses the session, after #1042

- **A session with a link at `scratchpad/blobs` or `scratchpad/large_tool_results` no
  longer opens.** #1036 mounted both, and nothing makes either folder, so a link the
  shell left there rooted the next turn's route wherever it pointed: `read_file` read the
  host. `LocalSessions` and `backend_at` now refuse it, as they refuse a linked `memory`;
  remove the link to use the session. Nothing is deleted, since the link's target may be
  someone's data.
- **`Layout.unlinked_dirs` names the folders checked.** A backend of your own that mounts
  a route inside a session can hold its folder to the same rule by checking these.

## A stopped turn answers only with what the model said, after #1041

- **`RunResult.answer` of a turn that stopped is the model's words, or `""`.** A turn
  whose first model call failed (`model_unavailable`) answered with the caller's own
  message, kingfisher's lines after it included, and one stopped just after a tool ran,
  at `max_steps` or `model_unavailable`, answered with that tool's output. Each now
  answers with what the model last said, and `""` where it said nothing. A caller that
  showed a stopped turn's answer shows nothing where it showed the task back.
- **`kingfisher run` says so on stderr.** Over an empty answer the ending reads "it
  stopped before the model answered" in place of "the answer above is what was reached".

## A listing names only the folders that are there, after #1038

- **`ls` no longer names a route whose folder nothing has made.** #1036 mounted
  `/scratchpad/blobs/` and `/scratchpad/large_tool_results/`, and the listing named both
  although neither existed, so the model opening one was told `path_not_found`. If your
  own backend mounts a route whose folder is made later, the model will not see it until
  something writes there; `/skills/` and the rest are unaffected, since deepagents names
  every route at `/` itself.
- **`_listing` takes no routes.** It is private to `plugins.sessions.backend`, so this
  matters only to a deployment that called it anyway.
## Clearing counts as the summarizer does, after #1037

- **Clearing starts sooner on an Anthropic-format model.** It counted four characters a
  token; it now counts as the graph's summarizer does, which on `ChatAnthropic` --
  MiniMax-M3 and -M2.5 included -- is 3.3, scaled by up to a quarter more by the usage
  the provider last reported. The default mark is still 64,000, so a pass starts at
  between a sixth and a third fewer characters. A `clear_results_at` you set is read in
  the same tokens: raise it by that much to keep where it cleared.
- **A `token_counter` on your summarizer now moves clearing too.** Calibrated against
  your gateway, the trigger and the marks follow it together. `docs/guides/middleware.md`
  says what a counter costs.
- **`result_clearing` and `ResultClearingMiddleware` take `count=`**, a
  `SummarizerCount` from `summarizer_count(model, middleware)`. Neither is exported;
  code building either by hand passes the count of the summarizer that graph runs.

## The agent may not write a kept record, after #1036

- **`/scratchpad/conversation_history/`, `/scratchpad/blobs/` and
  `/scratchpad/large_tool_results/` are read-only to the agent**, as
  `/scratchpad/tool_results/` already was: the file tools refuse a write, an edit and a
  delete there, and so does an offered tool handed `runtime.context.backend`, which keeps
  the same rules. Reads are untouched, and the rest of the scratchpad stays the agent's.
  Nothing shipped wrote to those folders; a tool of your own that saved there needs
  another path, `/outputs/` or the scratchpad itself.
- **The middleware that keeps a record still writes.** The rules bind the tools, not a
  backend call, so deepagents' summarizer and its long-message eviction are unaffected,
  and so is your own middleware.
- **A session is made with `scratchpad/conversation_history/` in it**, and `backend_at`
  refuses a session directory without it, as it does for the folders it already required.
  A session made by an older kingfisher gets the folder from `ensure_session_layout`; one
  opened without it raises, naming it. The other two folders are not made, since nothing
  writes them.
- **`Layout` gains `kept_records`, `kept_record_routes` and `history`**, and
  `shell_read_only` now lists the kept records rather than only `saved_results`.
  `saved_results` is unchanged -- it is still the folder a ledger row names, which the
  unused results walk. A deployment that renamed its scratchpad or results leaf gets the
  new paths derived from its own names; the three deepagents leaves are fixed, as before.
- **Both fences refuse the shell the history too**, on macOS by the regex it writes for
  `.harness` and under bubblewrap by a read-only bind. Landlock still cannot, inside a
  scratchpad it grants whole, so no boundary is claimed there -- unchanged from the saved
  results.
- **The system prompt gains a clause**: the history is the model's to read and not to
  write. If you cache the prompt yourself, its text has changed.

## A deployment may refuse a graph's tools their runs, with a plugin, after #1034

- **`ScratchRunLimit` and `NoScratchRunLimit` are new on `kingfisher.plugins`**, and
  `Kingfisher` takes `scratch_run_limit=`, as `Plugins` does for the command: an object
  with `allows(name, kind) -> bool`, asked once per graph holding offered tools whether
  their `runtime.context.run` is bound. Left out, nothing changes. It can refuse a run
  kingfisher would bind, never bind one kingfisher refuses. `docs/guides/ports.md` has
  the section.
- **`standing_middleware` takes `name=`, and `ScratchRunsMiddleware` `limit_allows=`**,
  each with no default: the graph's name, as the limit is asked about it, and its
  answer. Pass the agent's name, empty for none; the subagent's; or `"general-purpose"`.
  Neither is exported; code importing them from `kingfisher.core` needs the keyword.

## A portable subagent runs the agent's middleware, after #1033

- **This changes what an existing deployment runs, with nothing written.** A portable
  subagent, a `SUBAGENTS` entry with no `build`, ran under kingfisher's standing
  middleware alone. It now runs the middleware the main agent's file names, in its order
  and with the settings written there for each, a summarizer included; a helper another
  subagent consults gets the agent's, not its summoner's. A subagent document (YAML or
  Markdown) and a compiled one are unchanged; a `SUBAGENTS` entry with no `build` typed
  into your definitions folder is portable, and changes too.
- **To keep the old behaviour, wire a `SubagentMiddlewares` answering nothing**:
  `names(subagent, agent, middlewares)` returning `()`, passed as
  `Kingfisher(subagent_middlewares=...)` or `Plugins(subagent_middlewares=...)`.
  `SubagentMiddlewares` and the default, `AgentsMiddlewares`, are new on
  `kingfisher.plugins`; `docs/guides/ports.md` has the section.
- **A name it answers outside the agent's line is refused** with a `CapabilityError`
  when the agent is built, naming `SubagentMiddlewares`, as a definition naming one is.

## `AccessError` and `Kingfisher.access_report` are gone, after #1030

- **`from kingfisher import AccessError` fails.** Since the vocabulary is collected from
  the definitions, every refusal it made was of a caller named wrong, raised as
  `CallerError`, so the two names caught the same thing. Catch `CallerError`.
  `kingfisher run` prints such a refusal as `CallerError: ...`, where it said `access
  error: ...`; the exit code is still `2`.
- **`Kingfisher.access_report` is gone.** It was built at every start and read by
  nothing. What it said is `inventory(cfg).access_report`, the report `kingfisher list`
  prints.

## A subagent's results are cleared too, after #1029

- **A subagent's and `general-purpose`'s older results are cleared to handles, as the
  agent's are.** Once a subagent's own conversation passes its mark, its oldest recorded
  results, file reads and written text become handles saying where each still is: the
  file under its row to one holding `read_file`, the call to make again to any other. A
  middleware of yours on a subagent that reads earlier `ToolMessage`s may now find a
  handle where a result was. A helper a subagent consults clears too; a compiled
  subagent is untouched.
- **Each subagent clears by the mark beside the model it runs.** A subagent naming a
  model of its own takes that entry's `clear_results_at`, one naming none its
  summoner's, and `general-purpose` the agent's. A mark at or past where a subagent's
  own summarizer fires is now refused when the agent is built, naming the subagent;
  before, nothing cleared there for it to be wrong about.

## Text for one session is a plugin, after #1028

- **`SessionInstructions` is new on `kingfisher.plugins`**, and `Kingfisher` takes
  `session_instructions=`, as `Plugins` does for the command: an object with
  `text(cfg, session_id) -> str`, asked once per turn. Left out, nothing changes.
  `docs/guides/ports.md` has the section.
- **If you replace `AnthropicPromptCachingMiddleware` to send each session text after
  the cached part, drop the replacement and return that text from the plugin.** It is
  sent from the same slot, after the static prompt's breakpoint and before the memory
  block, with no breakpoint of its own, to every graph kingfisher builds. Keeping the
  replacement beside the plugin is refused with a `ConfigError` when the agent is
  built; without the plugin it is only warned about, as before.

## Sized by the summarizer each graph runs, after #1027

- **A summarizer of yours that fires sooner now shrinks what kingfisher keeps below
  it.** Where a definition names middleware answering to `SummarizationMiddleware`, the
  PDFs a turn may hold open and the default clearing mark are half of where *that*
  summarizer fires, not deepagents' own. On a model with no profile, one firing at
  100,000 tokens holds a turn to about 146 KB of PDF where 249 KB went before, and
  starts clearing at 50,000 tokens rather than 64,000. Each graph reads its own: a
  subagent the summarizer its definition names, `general-purpose` the agent's.
  `summarize-by-count` fires at deepagents' own size, so an agent naming only it sees
  no change.
- **A `clear_results_at` at or past that point is refused when the agent is built.** It
  was refused only past deepagents' own trigger, when the model was built, and one
  between the two never cleared anything. The error names the agent and the trigger.
- **A summarizer kingfisher cannot read is sized as deepagents' own**, as everything
  was before: a class that is neither deepagents' summarizer nor langchain's, or a
  trigger of message counts alone. The notice a replacement of deepagents' summarizer
  is built with now says so.
- **`high_mark` takes the trigger, not the model, and `summarizer_trigger` the graph's
  middleware too.** Neither is exported. Code importing them from `kingfisher.core`
  calls `high_mark(trigger, written, subject=...)`; `summarizer_trigger(model)` still
  reads deepagents' own, and `summarizer_trigger(model, middleware)` the one that runs.

## A deployment's say over a PDF is a plugin, after #1026

- **`PdfAttachments` and `ReturnedPdf` are new on `kingfisher.plugins`**, and
  `Kingfisher` takes `pdf_attachments=`, as `Plugins` does for the command. Left out,
  nothing changes: every PDF is judged by its size, as before. `docs/guides/ports.md`
  has the section.
- **If you copied `AttachmentsMiddleware`'s tally** — `_held`, `_asked_by`, `_passed`
  — to judge PDFs from a middleware of your own, move the judgement into the plugin. It
  is told the verdict, the bytes the turn already holds and its allowance, which is what
  those three computed. They stay private, and may change without a note here.
- **There is no page-limit setting.** A page limit is a few lines of your own in the
  plugin; the section's example counts pages with pypdf.
- **A PDF the plugin replaces is a call that succeeded**, recorded with an id. One
  kingfisher replaces with its own line is still a failed call with no row.

## A result's id is taken as its call starts, after #1025

- **Ids have gaps now.** A recorded call takes its id before it runs, so one that fails
  leaves a number no result has, and so does a `task` paused at a gate and run again on
  its resume. Nothing that reads ids should expect them to follow on one from the next,
  or read a missing one as a lost result. None is ever given to a second result.
- **A `task` answer is numbered before the work inside it**, not after, and a turn's
  rows in `.harness/ledger.jsonl` are in id order rather than the order results came
  back. A closing line may carry `"reserved"`, the turn's highest id where no row holds
  it.
- **A middleware may want `result_id`**, a function returning the id the call running
  now will be recorded under, or `None`. A deployment that numbered its own files to
  match kingfisher's can ask for this instead. `docs/guides/middleware.md` has where such
  a file may go.

## A request may carry a note for its turn, after #1024

- **`Request` has a `note` field**, text for that turn alone. It is sent right after
  the task in the turn's message and kept there, so later turns replay it as sent.
  Left empty, the message is exactly what it was. `docs/guides/formats.md` has the
  section.
- **If you add per-turn text yourself**, appended to the task or placed by a
  middleware of your own, `note` is the place for it. Moved out of the task, it leaves
  the `run_start` event, which logs the task alone.

## A subagent's calls name its model, and `extra` refuses a class's own names, after #1022

- **A `model_call` or `model_error` made by a subagent carries that subagent's
  `model` and `endpoint`**, where it runs a model of its own or one a request routed
  with `run_on`. Both were the main agent's, so a bill totalled by model priced a
  subagent's tokens on the main agent's. The other events keep the main agent's.
- **A `models.yaml` that loaded may now be refused at startup**, in `doctor` too: an
  `extra:` naming the chat class's own field or alias for a setting kingfisher fills
  in -- `openai_api_key`, `openai_api_base`, `model_name` or `request_timeout` on
  `openai_responses`, `anthropic_api_key`, `anthropic_api_url`, `model_name` or
  `default_request_timeout` on `anthropic`, and the like on a wire format of your own.
  Most were ignored without a word, so the model ran on the endpoint's key, URL and
  timeout regardless; the rest were refused only once a model was built. Delete the
  line, or write the value where the refusal says: the endpoint's `base_url` and `key`
  or `key_env`, or the entry's own `timeout_s` and `max_tokens`.

## Every tool result is saved in one folder, after #1018

- **`scratchpad/large_tool_results/` is gone.** A result too long to show the model
  whole is saved beside every other one, under `scratchpad/<tool_results>/`, and it is
  shown its head and tail with the last line naming the file. deepagents offloaded it
  before, to a folder kingfisher did not name. That offloading is off, but deepagents'
  summariser still writes there when it answers a call refused for length, which it
  does now only where clearing could not (*after #1056*). A leftover one is read only
  through the stub that names it.
- **The `offloads_route` and `offloads` properties left `Layout`**, and its
  `saved_results` holds one path rather than two. A deployment that read either to find
  a result should read `tool_results_route` instead, which has not moved.
- **`large_tool_results` is still a name a layout may not take.** deepagents'
  summariser writes it on a refusal for length, and a deployment that replaces
  `FilesystemMiddleware` can turn offloading back on; a layout holding the name would
  overwrite the folder either way.
- **If you replace `FilesystemMiddleware`**, leave `tool_token_limit_before_evict` unset
  or `None`. kingfisher saves every result itself, and a replacement that offloads
  writes a folder nothing names and no prompt sends the agent to. The warning such a
  replacement already prints now says so.
- **The prompt no longer teaches a second way to find a result.** It said to `glob` for
  the row's id and, for an offloaded one, to `grep` for the `[A-0042]` line the file
  ended in. Every file is named after its id now, so the `glob` is the only route; an
  agent holding `grep` but not `glob` is told to call the tool again, as before.


## A summarised conversation is kept summarised, after #1017

- **`.harness/transcript.jsonl` is the conversation as the model last saw it.** Once a
  turn ends summarised, it opens on the summary and holds only the messages after the
  summary's cut; the rest is in `scratchpad/conversation_history/`, which the summary
  names. Anything reading the transcript as every word said reads less after a summary.
- **`Message` has a `summary` field**, true only on that summary, and written as
  `"summary": true` in the transcript line.

## What becomes of an unused result is a plugin, after #1012

- **`UnusedResults`, `DeleteUnusedResults`, `UnusedFiles` and `UnusedFile` are new
  on `kingfisher.plugins`**, and `Kingfisher` takes `unused_results=`, as `Plugins`
  does for the command. Left out, nothing changes: every result file no row names is
  removed, as before. `docs/guides/ports.md` has the section.
- **A backend that raises while one is removed no longer fails the end of the turn.**
  It is logged as a warning naming the session, and the file is offered again next
  turn.
- **`sweep_results` takes `unused_results=` and `session_id=` as well.** The id is
  handed on as `UnusedFiles.session_id`, since a result's path repeats across sessions.

## Result files are swept after the transcript, after #1011

- **`keep_ledger` no longer sweeps and no longer takes `tool_results_route=`.** The sweep
  is `sweep_results(files, ledger, entries, tool_results_route=...)`, beside it in
  `kingfisher.core.session.ledger`, and runs once the turn's transcript is written.

## The shipped middleware is one summarizer, after #1009

- **`call-cap-strict`, `call-cap-generous` and `tool-note` are gone**, with the
  subagent `sweeper` that named two of them. `docs/guides/middleware.md` keeps the
  argument the caps made, under *A limit a definition can set is no limit*.
- **`compact` is `summarize-by-count`**: the class `SummarizeByCount`, in
  `assets/middlewares/summarize_by_count.py`. It now takes `messages`, the count it
  summarizes at, which defaults to sixty and which a definition may set under
  `settings:`.
- **A workspace that seeded them keeps its copies, and they still load.** Seeding
  again brings the new file and removes nothing, so delete `middlewares/call_cap.py`,
  `middlewares/tool_note.py`, `middlewares/compaction.py` and `subagents/sweeper.yaml`
  yourself, and change any definition naming `compact` to name `summarize-by-count`.
  A registry in your own program importing `CallCap`, `CallCapGenerous`, `ToolNote` or
  `Compact` from `assets.middlewares` needs the same change.

## A workspace tool is an offered tool, after #1006

A tool a deployment supplies itself is now called an *offered tool*, beside the offered
middleware a deployment makes available. Nothing behaves differently; these names moved.

- **`WorkspaceToolFailureMiddleware` is `OfferedToolFailureMiddleware`**, in
  `kingfisher.core.agent.middlewares.guarded_calls`.
- **`build_agent` and `builtin_tool_names` take `offered_tools=`** in place of
  `workspace_tools=`, both in `kingfisher.core.agent.assembly`.
- **`workspace_tool_names` is `offered_tool_names`**, in `kingfisher.core.agent.kinds.tools`,
  and `ToolSurface.granted_workspace` there is `ToolSurface.offered_grant`.
- **`Offering.workspace` is `Offering.offered`**, as a field and as a keyword, in
  `kingfisher.core.definitions.tools.spec`.
- **`interpreter` takes `offered=`** in place of `workspace=`, in
  `kingfisher.core.agent.interpreter`.
- **`kingfisher list` heads the section `offered tools — grant with tools: in an agent
  file`**, where it said `workspace tools`. A script reading the listing's text for that
  heading must look for the new one; `--json` is unchanged.
- **The refusal of a tool wearing a built-in's name reads `offered tool(s) … would replace
  a built-in`**, and `kingfisher doctor`'s warning reads `the offered tool catalogue did
  not load`.
- **The glossary's term is *Offered tool***, and the guides use it throughout.

## A workspace tool is handed no link, after #1003

- **A workspace tool's `path` that is or runs through a link is refused**, as a failed
  tool result reading "… is or runs through a link, and a tool is handed none", where
  it was handed the link's target whenever that stayed in the session. The shell can
  make a link, so `/scratchpad/link` pointing at `.harness/agent.yaml` handed a tool the
  pinned agent. The same holds on every route, `/skills/` included: name the file the
  link points to. A backend with its own `host_path` is not affected.

## A model call's cost in steps is counted off the agent's graph, after #1000

- **A subagent may make `KINGFISHER_RECURSION_LIMIT` ÷ the steps one of the agent's model
  calls costs**, where it was ÷ 3 whatever the stack. A call costs two steps and one more
  for each `before_model` or `after_model` hook on the agent's graph: three on the default
  stack, so 50 calls at the default limit as before; four with an approval gate; five
  with a gate and a middleware of yours holding a model hook, so 30. The agent's own
  note on what is left counts the same way.
- **`standing_middleware` takes `budget=`** in place of `step_limit=`: pass
  `StepBudget(cfg.recursion_limit)`, from `kingfisher.core.agent.middlewares.step_budget`,
  and call its `measure(graph)` once the agent's graph is compiled. Unmeasured, it
  assumes three steps a call.

## An overload inside a stream stops the turn, after #999

- **A turn can end with `stop_reason: model_unavailable` on an error Anthropic sends
  after a response has begun**: an `overloaded_error`, `api_error` or
  `rate_limit_error` that escaped `run` and `stream` as the SDK's `APIStatusError`
  with status 200. The `cut_short` event names the type and the provider's message.
  Any other type sent that way still raises.

## A provider's 4xx raises `ConfigError`, after #995

- **`Kingfisher.run` and `stream` raise `ConfigError` for a provider's 4xx**, as they
  already did for a 401: a 404 for a model name the endpoint does not serve, a 400 for a
  request it will not take. The message names the endpoint, its URL and the model, and
  the provider's error is its `__cause__`. These escaped as the SDK's own error, so an
  `except anthropic.NotFoundError` around a turn no longer fires; catch `ConfigError`,
  or read `__cause__`. A 429 and the 5xx range still end the turn as a stop, and
  anything else still raises as itself. `kingfisher run` reports it with exit 2.
- **`kingfisher run` prints only `RunResult.answer` on stdout**, once, at the end. Every
  token the agent streams goes to stderr with the rest of what you watch: with a
  streaming model, stdout had all of its prose, the narration before each tool call
  included, where the answer is only its last reply.

## Definitions refuse what would have loaded wrong, after #994

- **A key written twice in one YAML mapping is refused**, at any depth, in an agent or
  subagent file and in `source_ids.yaml`. YAML kept the last, so a second
  `source_ids:` replaced the first without a word. Delete the line you did not mean.
- **A module listing one subagent or middleware twice is refused** with "defined twice
  in this file", as a tool already was; the subagent's second entry used to replace
  the first.
- **A skills folder called `catalogue` is refused**: it is the label of the skills
  directly in the root, so its skills were described while the root's ran. Rename it.
- **`name`, `description` and `system_prompt` are refused as a list or a mapping**,
  which were stringified -- a list prompt reached the model as a Python repr.
- **`with_datazone(None, zone)` raises `CallerError`**, and so does a run, session or
  listing given a datazone and no source ids: the zone alone was read as a caller,
  slipping past the refusal of nobody named under a policy. A source id that is not a
  string, `[1]` or `[None]`, raises `CallerError` too, where it raised `TypeError`.

## A `Config`'s parts are on `kingfisher`, and wrong settings are refused as read, after #990

- **`from kingfisher import Endpoint, ModelProfile, Models`** names what a `Config`
  built in code is made of. `configuration.md` tells you to write
  `Endpoint(api_key=...)` for a key no file holds, but the three were only in
  `kingfisher.core.config`, which is private and may move. Import them from
  `kingfisher` instead.
- **An `extra:` in `models.yaml` naming a keyword kingfisher passes itself is refused at
  startup**: `model`, `base_url`, `api_key`, `timeout`, `max_tokens`, `max_retries`,
  `temperature`, `top_p`, and any the endpoint's wire format sets, such as
  `use_responses_api` on `openai_responses`. Such a file loaded, and then every build
  raised `TypeError: got multiple values for keyword argument`, in `doctor`, `list` and
  every turn. Write `timeout_s` and the others as the entry's own keys, and a key in the
  keys file or under `key_env`.
- **The limit settings refuse a value below their floor**, naming the setting, and
  `doctor` reports it as a configuration error: `KINGFISHER_EXECUTION_TIMEOUT_S`,
  `KINGFISHER_TURN_TIMEOUT_S`, `KINGFISHER_RECURSION_LIMIT` and
  `KINGFISHER_THREAD_POOL_SIZE` take 1 or more, `KINGFISHER_SESSION_TTL_S` and
  `KINGFISHER_SESSION_MAX_BYTES` 0 or more. An execution timeout of 0 passed `doctor`
  and then failed every session open.
- **A skills mount labelled `.` or `..` is refused**, as one containing `/` already was.
- **`Layout` refuses two names differing only in case**, such as `inputs="data"` and
  `outputs="DATA"`, which on macOS are one folder; a name that differs from a reserved
  one only in case, such as `Skills`; and a name ending in a newline.

## A request's endpoints hold the default model too, after #988

- **`Capabilities(endpoints=(...))` now refuses an agent that names no model** when the
  deployment's default resolves to an endpoint outside the list, and so does a built
  subagent naming none. Only a named model was checked, so naming none ran the default
  wherever it lives while naming the same model was refused. The refusal is the same
  `CapabilityError`, naming "the default model". If you restrict endpoints, include the
  default's, or have the agent name a model on one you list.
- **The `indistinct` event compares a subagent with the main agent's own model**, not
  the deployment's default, and says "the same host as the main agent's" where it said
  "the same host as the default". Only an agent naming a model of its own reports
  differently.

## The vocabulary is collected from the definitions, after #983

- **`source_ids.yaml` is read by nothing.** Delete it. The names a deployment uses are
  the ones its `source_ids:` lines, `ToolSpec`s and `skills:` entries write, and its
  datazones the ones its subagents and tools require. `KINGFISHER_SOURCE_IDS_FILE` is
  read by nothing either.
- **`from kingfisher import SourceIds` is gone**, with `Config(access=...)` and
  `Config.access_source`. The vocabulary is read off the definitions, so there is
  nothing for a deployment to build or hand in; `Kingfisher.vocabulary` holds what the
  walk found. #990 put the name on the front door for the field this removes.
- **One lock anywhere switches checking on.** A deployment that wrote `source_ids:`
  lines and no file ran them inert; now every call must pass `source_ids=` -- or
  `UNSCOPED`, to mean no caller -- and `--as` on the command line. With no lock anywhere
  nothing changes, and naming a caller is refused as before.
- **A misspelling is not refused.** `source_ids: [sales_bd]` starts, and locks its
  definition shut to `sales_db` callers. `kingfisher list` prints every name in use beside
  the definitions using it: read it after editing a lock.
- **A caller name or datazone no definition uses is accepted**, and opens nothing a lock
  shuts, where it used to be refused as unknown.
- **`AccessError` is raised only as `CallerError`.** With no file to break and no
  undeclared name to refuse, nothing of the deployment's own raises it; an
  `except AccessError` still catches every refusal of a caller named wrong.
- **`Config.access` and `Config.access_source` are gone**, and `Kingfisher.access` is
  `Kingfisher.vocabulary`, collected at construction.
- **`kingfisher list --json`** carries `"vocabulary": {"source_ids": {name: [who]},
  "datazones": {name: [who]}}` in place of `"access"`, and the origins no longer have a
  `source_ids` entry.

## A caller named wrong raises `CallerError`, after #980

- **`from kingfisher import CallerError`** names what a call raises when it says who
  is calling in a way the deployment cannot take: a source id or datazone
  `source_ids.yaml` does not define, a bare string, or nobody where a policy is in
  force. These raised `AccessError`, which is also what a broken
  `source_ids.yaml`, or a definition naming an undeclared source id, raises at
  startup, so a caller could not catch its own mistake alone.
- **`CallerError` is an `AccessError`**, so an `except AccessError` still catches
  both. To answer a caller's mistake differently from the deployment's, catch
  `CallerError` first. `kingfisher run --as` still reports either with exit 2.

## `ShellSettings` refuses a `path_extra` entry that is not absolute, after #978

- **`ShellSettings(path_extra=(...))` raises `ConfigError`** for an entry that is not
  an absolute path, `""` and `"."` among them, and so does `LocalSessions()` reading
  one from `KINGFISHER_SHELL_PATH_EXTRA`. Such an entry resolved against the server's
  working directory, which the shell's fence then let it read. Empty parts of the
  variable, as `a::b` leaves, are still dropped. If you passed a relative directory,
  pass it absolute.

## A session stays claimed for a shell command's bound too, after #977

- **`Config.claim_stale_after` grew, and nothing you wrote has to change.** It was the
  turn deadline plus the longest model timeout, and it is now the turn deadline plus the
  longest bound on one superstep — whichever of `execution_timeout_s` and the models'
  `timeout_s` is larger. At the defaults, where both are 120s, the number is the same
  3,720s it was.
- **If you raised `KINGFISHER_EXECUTION_TIMEOUT_S`** — for a build, a test suite,
  anything a shell command may take minutes over — a session now stays claimed longer
  after a turn dies, by however much that bound exceeds your longest model timeout. That
  is the fix: a turn out of time finishes the step it is inside before it stops, so the
  old window let a second caller take a session whose turn was still running. Measured,
  a 6s command under a 1s turn and a 1s model timeout let one in 3.0s into a turn that
  ran to 6.0s.
- **A backend that implements `claim` reads the number it is handed**, as before. If
  yours derives its own staleness instead of using `stale_after`, derive it the same
  way or it keeps the old overlap.

## A source id stands only for itself, after #972

- **`source_ids.yaml` lists names and nothing else**: `source_ids: [sales_db, audit_log,
  pii]`, with `datazones:` as before. A name written with a body -- `warehouse:
  [sales_db, audit_log]`, `sales_db_pii: {sales_db, pii}`, or the mapping form
  `sales_db:` -- is refused at startup, saying where each went. So is a name listed
  twice, as a datazone listed twice already was.
- **A role that covered others is the caller's to spell out.** A caller you sent as
  `warehouse` now sends `["sales_db", "audit_log"]`, or `--as sales_db,audit_log`, from
  the code that decides who your callers are.
- **A named requirement is written where it is needed**, as the set it named:
  `source_ids: [{sales_db, pii}]` in each definition that named `sales_db_pii`, or
  `ToolSpec(tool, source_ids=[{"sales_db", "pii"}])` in a tool's file.
- **`kingfisher list --json`** carries `"access": {"names": [...]}`: a sorted list, with
  no `requires` and no mapping of each name to what it covered.
- **A hand-built `Config(access=SourceIds(...))`** passes `names` as a set of names;
  `compounds=` is gone.

## `InputsError` is on `kingfisher`, after #970

- **`from kingfisher import InputsError`** names what `Kingfisher.run`, `stream`,
  `arun` and `astream` raise when `Request.inputs` names a file that is not there, one
  with no filename, or two files under one filename. It was only in
  `kingfisher.core.session.files`, which is private and may move, so a caller could
  catch it only as `ValueError`, beside any bug. It is still a `ValueError`; if you
  imported it from there, import it from `kingfisher` instead.

## `KINDS` and `STOP_REASONS` are on `kingfisher`, after #962

- **`from kingfisher import KINDS, STOP_REASONS`** gives every value `RunEvent.kind`
  and `RunResult.stop_reason` can hold. They were only in `kingfisher.core.result`,
  which is private and may move; if you imported them from there, import them from
  `kingfisher` instead.
- **Both lists grow.** A value you do not know is one to ignore rather than fail on,
  and `RunResult.completed` is the question to ask about a turn finishing.

## `UnsafeReferenceError` leaves `kingfisher`, after #955

- **`from kingfisher import UnsafeReferenceError` fails.** Nothing raised it at a
  caller: a tool handed a path its turn may not reach gets the refusal as a failed
  tool result the model corrects from, as with `HostPathError`, so an `except` naming
  it never fired. Delete the `except`. The class is still
  `kingfisher.core.agent.references.UnsafeReferenceError`, which may move.
- **`kingfisher run` with two `--input`s of one filename exits `2`**, saying
  `InputsError: <name>: supplied twice`. It printed a traceback and exited `1`, the
  code for a turn that ran and stopped at a bound.

## `http_fetch` refuses a body that does not read as text, after #942

- **A body more than a quarter unreadable comes back as a refusal** naming the share
  and the declared type: `refused: image/png: 12642 of the 20488 characters at <url>
  do not read as text -- ask for a URL that answers text, which is what this tool
  answers with`. Measured, a PNG had been answered as 20,000 characters of which
  12,283 were the replacement character and most of the rest control codes, and a PDF
  the same. If you call this tool on anything but a text URL, you now get a refusal
  where you got mojibake.
- **The declared type does not decide it.** Text served as `application/octet-stream`
  is answered exactly as before, measuring 0.0% unreadable; a body declaring
  `text/plain; charset=utf-8` whose bytes are really UTF-16 is refused, at 55.2%.
- **A page the wrong encoding only dents is still answered.** French prose written in
  Latin-1 and declared UTF-8 measures 10.3% and comes back as it did, accents replaced.
- **A content type the server invented is not printed back**: only one shaped like
  `name/name`, at most 40 characters either side, reaches the refusal.

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

## `http_fetch` says where it cut a page, after #935

- **A cut answer ends in a line naming the bound that cut it**, where it used to end
  mid-page with nothing said: `-- cut at 20000 characters, which is as much as one
  answer carries`. Measured, 200,000 bytes and 20,000 bytes came back as the same
  20,000 characters, so a caller could not tell a page it had all of from a tenth of
  one. If you parse this answer, the last line may now be that note.
- **The note is inside the 20,000 characters, not added past them**, because that
  number is what the docstring promises and what a caller plans around. A cut answer
  therefore holds slightly less of the page than it did.
- **A page whose encoding spends more than four bytes a character says so differently**
  -- `-- cut at 80000 bytes, which is as much as one fetch reads` -- and comes back
  under 20,000 characters. `iso-2022-jp` shifts in and out of each run, nine bytes for
  two characters, so 180,000 bytes of it answered 17,779.
- **The replacement character a cut mid-character invented is no longer in the answer.**
  `errors="replace"` wrote one the page never sent, and a caller read it as the page's
  own corruption.

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
