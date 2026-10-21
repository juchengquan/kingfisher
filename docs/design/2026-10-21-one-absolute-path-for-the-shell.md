# One absolute path for the shell

**Status:** proposed. Part A landed with this document; part B is not built.
**Date:** 2026-10-21.
**Occasion:** a deployment saw commands miss session files because they used relative
paths. The owner settled the questions below one at a time. **Cited by symbol, not by
line.**

## How it works today

Only the shell (`execute`) reaches session files by path. `eval` has no filesystem and
calls tools, which take file-tool paths; a tool's `run` works in a sealed folder holding
only what the tool passed in.

Kingfisher's own shell starts in the session folder, and its prompt (`shell_here.md`,
`shell_here_execute.md`, `shell_here_skills.md`) teaches "drop the leading slash":
`/inputs/data.csv` is `inputs/data.csv` in the shell. `hint` in
`core/agent/middlewares/builtin_tools.py` repeats that after a missed path. A relative
path breaks once anything moves the working directory: a `cd` inside the command, a
script calling `os.chdir`, or a deployment's runner that starts elsewhere.

## Decided

- **One variable, `KINGFISHER_SESSION_ROOT`, the session folder's absolute path.** The
  rule is to put it in front of a file-tool path, slash and all:
  `"$KINGFISHER_SESSION_ROOT/inputs/data.csv"`. One root rather than one per folder, so
  a renamed or added folder needs nothing new. Named for the "session root" the prompt
  already says, not "workspace", which is every session's folder, and not a bare
  `KINGFISHER_SESSION`, which reads as an id beside the retired `KINGFISHER_SESSION_ID`.
- **It replaces "drop the leading slash" entirely.** One taught form, since two correct
  answers is how one script ends up mixing them. The working directory stays the session
  folder, so a relative path still works where nothing moved it; it is not taught.
- **A script reads it from the environment**, `os.environ["KINGFISHER_SESSION_ROOT"]`,
  never a pasted path: a kept script must survive the session moving, and a pasted path
  writes the host layout into what the caller gets back.
- **Every shell that can vouch for it has it, and kingfisher never edits a command.**
  Kingfisher's own shell sets it from the environment it already builds. A deployment
  using `LocalSessions(runner=...)` says where its shell sees the session with a
  parameter, `LocalSessions(session_root=...)`, a function of the session folder like
  `runner`, and its runner puts the same value in its shell's environment; a remote one
  gives its own side's path. A deployment with its own sessions plugin has its shell
  carry `session_root`, read off the shell as `shell_prompt` is. One saying nothing gets
  nothing, and its agent keeps "check with `pwd` and `ls`". Not taken: an attribute on
  the runner, which hides a setting inside an object, or handing the value to `run` or
  to the runner's builder, which changes the call every existing runner implements. Not taken: prefixing `export ...;` to the command, which assumes the runner's
  shell is `sh`, and shows a deployment's logs and allow-lists a command the model did
  not write.
- **Kingfisher says it wherever `session_root` is set**, after a deployment's own
  `ShellPrompt` text or the neutral one. A deployment silences that part with `""`, as
  it does any part today.
- **The `/skills` exception stays.** The catalogue is shared and is not under the
  session root, so `$KINGFISHER_SKILLS` is still how a skill's script is reached.

## Open, for now

**A tool's `run` does not get the variable.** Its command stays in the sealed folder, and
the tool decides what goes in and what comes out: handed the session root, a renderer fed
a crafted PDF could read the caller's inputs or write into `/outputs` behind the tool's
back. The model never writes those commands, so the prompt change would not reach them.
The owner took this for now and asked for it to be noted. Revisit when a real tool needs
to reach session files rather than be handed them.

## The plan: two pull requests, the second cut after the first merges

Sequential, not parallel: both change the shell prompt and `hint`.

**A. Kingfisher's own shell**, the fix for the failures seen.

1. `shell_env` in `plugins/sessions/shell.py` exports `KINGFISHER_SESSION_ROOT`, the
   session folder, beside `TMPDIR`, so every fence (macOS, bubblewrap, Landlock, off)
   hands it over. Bubblewrap binds the session at its own path, so the value is right
   inside it too.
2. The three `shell_here*.md` texts teach the prefix rule and the `os.environ` line, and
   drop "avoid `cd`". The warning that `/inputs` names the host's root stays.
3. `hint` suggests `"$KINGFISHER_SESSION_ROOT/<path>"`.
4. `decisions.md`: an entry saying why the shell's spelling moved from relative to the
   variable, and the "dropping the slash" sentence in *What the agent is told about the
   shell is said by the shell* updated. `upgrading.md`: agents are taught differently.

Tests, each mutation-tested: a real command that does `cd /` first still reads
`"$KINGFISHER_SESSION_ROOT/inputs/x"`, under every confinement setting this platform
runs, beside a relative read that fails after the same `cd` as the control; a script
reading `os.environ` finds the file; the hint names the variable; the prompt no longer
teaches the relative form.

**B. A deployment's own shell.**

1. `session_root_of(backend)` in `core/agent/backend.py`, read off the shell as
   `shell_prompt_of` reads its wording: a `str` or nothing, refused if anything else.
   `LocalSessions` and `backend_at` take `session_root=`, called with the session folder
   and refused without a runner, as `shell_prompt=` is; `ConfinedLocalShellBackend` holds
   its answer, and its own session folder where no runner is supplied.
2. The variable's sentence is added to the deployment's `ShellPrompt` text or the
   neutral `shell_elsewhere.md` where `session_root` is set. The hint fires on it rather
   than on `starts_in_session`.
3. `ports.md` shows one function feeding both `runner=` and `session_root=`, and a
   deployment's own shell carrying `session_root`; `upgrading.md` and `decisions.md` say
   how a deployment opts in. `CommandRunner` is unchanged.

Tests: a runner given `session_root=` gets the sentence and the hint, one without gets
neither, a deployment's `""` silences it, and `session_root=` without a runner is refused; the command string reaching a runner is
byte-for-byte what the model wrote; a non-`str` `session_root` is refused naming the
class.
