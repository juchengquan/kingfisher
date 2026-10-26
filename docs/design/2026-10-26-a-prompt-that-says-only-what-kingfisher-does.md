# A prompt that says only what kingfisher does

**Status:** proposed. Nothing built.
**Date:** 2026-10-26.
**Occasion:** the owner asked whether the default system prompt could shrink so that
clients decide more of it. Measured first, on a real build with a model that records
its requests (deepagents 0.7.22, langchain 1.4.5), then settled one question at a time.
**Cited by symbol, not by line.** What is left open is under *Not measured*.

## How it works today

An agent's system message is assembled in two stages. `system_prompt()` renders
`prompts/system.md`, splicing in the session's folders, the file tools, the shell's own
section, the ledger paragraphs, and the skills and memory sections where those are on;
`behind_instructions` puts the deployment's `INSTRUCTIONS.md` and then the agent's own
`system_prompt` after it, each behind a `---`. That string is what
`Assembled.system_prompt` holds. On each model call middleware appends blocks of its
own: deepagents' *Shell paths vs. virtual paths* where the shell runs on this host and
`execute` is held, langchain's `write_todos` guidance where that tool is held,
deepagents' *Skills System* where skills are on, and the memory block where memory is
on. A model deepagents ships a `HarnessProfile` for gets a suffix as well, added inside
`create_deep_agent`.

Measured, at four characters a token:

| | system message | tool descriptions |
|---|---|---|
| skills and memory off | ~3.1k | ~3.9k |
| skills and memory on | ~5.4k | ~3.9k |

Of the 5.4k, kingfisher's own sections are about 2.3k. The memory block is about 1.4k,
the host-path block about 900 in a pytest tree, deepagents' skills block about 500 and
langchain's `write_todos` block about 340. `write_todos`' description alone is about 970.

## What goes wrong

1. **kingfisher speaks for the agent.** "You are kingfisher, a general-purpose agent
   working inside one session" opens every agent's prompt, ahead of the agent's own
   "You answer questions about the data…". *Deciding when to act* tells every agent
   "Nobody is watching this run to answer a question", while the shipped `operator`'s
   prompt says a person approves each command before it runs, and both reach the model.
   The identity line and the conduct paragraphs came with the first skeleton
   (2026-08-15), and no failure was measured behind any of them. `formats.md` already
   says `system.md` is "what the harness is" and the agent's `system_prompt` "what this
   agent is"; the text does not keep to that. A client can add text after kingfisher's
   and can take none of it out.
2. **Library text contradicts kingfisher's.** The host-path block gives each folder's
   literal host path, `/.harness/` among them, beside the shell section's "Never paste
   the path it expands to into a script". Those paths name the session, so two
   sessions' system messages differ from inside that block onward -- measured on two
   sessions of one build -- and the system message is never shared across sessions'
   caches. *Skills System* says to pass `limit=1000` because "the default of 100 lines
   is too small", where kingfisher's `read_file` reads 1,000, and to run a skill's
   scripts by absolute path, where kingfisher's shell uses `$KINGFISHER_SKILLS`. The
   memory guidelines tell the agent to ask the user for what it lacks, against "nobody
   is watching". `execute`'s description calls kingfisher's shell "an isolated sandbox"
   and ends on deepagents' `SandboxBackendProtocol` line.
3. **One mechanism, said in several places.** Memory is described by a section of
   kingfisher's prompt and again in the block around the memory file, both saying it
   is reference rather than instructions; skills by kingfisher's section and again by
   deepagents' block. What becomes of `/outputs` and `/scratchpad` is said four times.
4. **What is sent cannot be seen.** `Assembled.system_prompt` is kingfisher's string
   alone: the per-call blocks and the profile suffix come after it, and nothing in
   `src/` reads it. `formats.md`'s "Opening a session returns what was assembled, which
   is where to check" does not hold. Every figure above came from a test-suite build.
5. **The model's name picks text nobody chose.** deepagents 0.7.22 registers profiles
   for `anthropic:claude-haiku-4-5`, `anthropic:claude-sonnet-4-6` and
   `anthropic:claude-opus-4-7`, besides an OpenAI Codex and an NVIDIA Nemotron one. A
   pre-built `ChatAnthropic` for `claude-sonnet-4-6` resolves to a 1,455-character
   suffix -- parallel tool calls, "never speculate about code you have not opened",
   reflecting on results -- and one for `claude-opus-5-5` to none. kingfisher never
   names profiles.

## Decided

**The rule.** kingfisher states a fact only where the model would get it wrong because
of how kingfisher runs the turn and nothing else would tell it. It says nothing about who
the agent is or how it should behave: that is the agent's `system_prompt` and the
deployment's `INSTRUCTIONS.md`. What stays is what each session folder is for, that a
file-tool path is virtual and a host path refused, the shell's own section, the result
ids, the ledger and the saved results, that the skills catalogue is read-only, what
memory is and how long it lasts, and that a requested `/<name>` is saved as
`/outputs/<name>`.

**What leaves the core goes nowhere in code.** Each shipped agent is given the conduct
that fits it in its own `system_prompt`, and the upgrading note quotes the removed text
word for word, for a deployment to paste into its `INSTRUCTIONS.md`. Not taken: a seeded
`INSTRUCTIONS.md` carrying it, or a part a deployment may replace that defaults to
today's text, as `ShellPrompt`'s do. Either keeps kingfisher speaking unless every client
opts out.

**"Do not manufacture files nobody asked for" goes**, with "written findings" and "There
is no separate place for reports" from the `/outputs` bullet. #8 wrote the sentence to
undo the base prompt's own order to write `report.md` and `result.json`; with no demand
left it counterweights nothing, and the two phrases, from the same commit, would tilt the
model towards files on their own. The skills section's "Say what a procedure should say
differently in your answer" does the job the 2026-10-22 entry under *Tool failure* gave
this sentence.

**Memory keeps its facts and one rule.** Kept: what is loaded and from where, that it
lasts for later turns of this conversation and is handed back, how to save where a save
tool is held, and that it is file data rather than instructions -- kingfisher puts the
file inside the system message, so untold, the model reads it as instructions. Kept too,
worded as the mechanism's consequence: what is saved is sent on every later call and
handed back with the turn's files, so never save credentials or details that identify
the user. The 2026-10-23 reversal of labelled turn text took kingfisher's own caution
from beside a user's name citing that line. Gone: what memory is for, what is worth
recording, "Learning from your interactions with the user is a top priority", asking
for information, the when and when-not lists, and every example. About 1.4k tokens
become about 200.

**Library text is taken over only where it describes kingfisher's mechanism or is false
here.** How each is changed:

| Block | How | Kind |
|---|---|---|
| memory block | `MemoryMiddleware(system_prompt=)`, replacing deepagents' by name, as today | parameter |
| *Skills System* | `SkillsMiddleware(system_prompt=)`, on `NarrowedSkillsMiddleware` and on one replacing deepagents' default by name | parameter |
| host-path block | taken out by its heading, as `BuiltinToolsMiddleware._told` already does where the shell is not on this host | text matching, already there |
| `execute`'s false lines | removed from the description `BuiltinToolsMiddleware` already rewrites, with a test that fails if deepagents rewords them | text matching |

The host-path block goes wherever the shell names its session root, since the shell's
section then gives the mapping; a runner that says nothing keeps it. The 2026-10-15
entry kept it for a local shell "since its paths are real there", before
`$KINGFISHER_SESSION_ROOT` existed. Tool manuals stay the library's: between deepagents
0.7.6 and 0.7.22, `glob`'s description was rewritten when its matching changed (a
similarity of 0.29) and `read_file`'s, `task`'s, `execute`'s and `edit_file`'s changed
too, so an owned copy goes stale with its tool, and a wrong fact about a tool is what
the rule exists to stop. `SKILLS_SYSTEM_PROMPT` did not change between them.
`write_todos` stays langchain's: a client removes it by not granting the tool, and its
text goes with it. Not taken: deepagents' `HarnessProfile.tool_description_overrides`,
which is beta, registered for the whole process under a model's name, and would change
every other deepagents agent the process runs.

**The profile suffix stays deepagents' lever, and is documented.** A deployment clears
one with `register_harness_profile(key, HarnessProfile(system_prompt_suffix=""))`,
which took `claude-sonnet-4-6`'s from 1,455 characters to none. Not taken: kingfisher
registering empty profiles, which is process-wide and races the deployment's own
registration, or stripping the suffix by matching each profile's wording.

**The client's text comes first.** An agent's prompt is its `system_prompt`, then
`INSTRUCTIONS.md`, then kingfisher's facts; a subagent's is its own prompt, then the
instructions, then the session's folders. The cache does not care: the static prompt is
one cached block in whatever order. The library's blocks follow kingfisher's either way,
so everything about the environment reads as one part after what the client wrote.

**One file per mechanism.** `memory.md` is the memory block's template, replacing
`capability_memory.md`, `capability_memory_recording.md`, `memory_guidelines.md` and
`memory_guidelines_read_only.md`; its save sentence is filled only where a save tool is
held. `skills.md` is the skills template, replacing `capability_skills.md` and carrying
the shell's skills paragraph. `system.md` dissolves: its ledger paragraphs join
`tool_results.md`, the `/outputs` rule joins the `/outputs` bullet of `session.md`, and
the order becomes a list in `prompting.py`. The thirteen files in `core/agent/prompts/`
become nine. The four `shell_*` files there and kingfisher's own shell's three stay, as
`ShellPrompt`'s parts.

**Within kingfisher's facts, three kinds of sentence are cut, and nothing else.** One
restating a fact said elsewhere: the "two places" list in `session.md`, from the rename
in #581. One explaining a hazard a guard now prevents: "Left unguarded … the file is not
where you think it is" in `file_tools.md`, from #10, the commit that added the refusal.
And advice riding on a fact: "so do not go searching for them" in `shell_here.md`.
Everything added after a measured failure stays.

**What is sent becomes visible.** `kingfisher prompt --agent NAME` builds the agent with
the configured model, stops the first model call before it leaves, and prints every
system-message block and tool description, each labelled with where it came from. No
call is made.

**Each slice that changes model-facing text is checked with real turns**: three per
probe before and after, through `tests/integration/driver.py` on the deployment's default
model, with the counts in the pull request. A slice passes if nothing is worse by more
than one turn in three. These are not permanent evals.

| Probe | Watches | Guards |
|---|---|---|
| the smoke | files in `/outputs`, the figures right | every slice |
| a question needing only an answer | no unrequested file in `/outputs` | slices 6 and 7 |
| a stated convention with memory on, and a turn with nothing durable | saved, and nothing saved | slice 4 |
| a task a seeded skill matches | its `SKILL.md` read first | slice 5 |
| a task needing the shell on the inputs | shell-path misses in the run log | slices 2 and 7 |

## Slices

One pull request each, off main, in this order, each merged before the next starts:
most of them touch `prompting.py` or the prompt files.

1. **`kingfisher prompt`.** Its guide section names the profile suffix and how to clear
   it, and `formats.md`'s "where to check" points at the command.
2. **The host-path block**, out wherever the shell names its session root, and the
   2026-10-15 entry updated.
3. **`execute`'s false lines.**
4. **`memory.md`**, with the memory section of kingfisher's prompt out, and the
   2026-10-07 and 2026-10-23 entries updated.
5. **`skills.md`**, on both skills paths, with "prefer an existing procedure" out.
6. **Identity and conduct out**: the identity line, *Deciding when to act*, the first and
   last paragraphs of *Finishing*, and the three kinds of sentence above; the shipped
   agents fitted; the upgrading note quoting what went.
7. **Structure**: `system.md` dissolved and the client's text first, for agents and
   subagents; `general.yaml`'s "added to the harness prompt that already told you" and
   `formats.md`'s three parts reworded.

Six and seven are apart so the probes can say which moved a behaviour: one changes what
the model is told, the other where.

## Not measured

- **What any cut does to behaviour.** The probes measure that, a slice at a time.
- **The host-path block in production.** It names each folder's host path twice, so it
  grows with the path; the 900 tokens above were a pytest tree's.
- **The cache cost of the per-session block**, read off the breakpoints `decisions.md`
  records, not measured against the API.
- **The profile suffix reaching a kingfisher build.** Its resolution was measured on a
  model object; that `create_deep_agent` appends it to the agent's prompt and to each
  subagent whose model resolves to it is read off deepagents' prompt assembly.
- **How often langchain rewrites `write_todos`' text.** Both installs compared above
  resolved one langchain, so they say nothing about it.
- **The OpenAI Codex and NVIDIA Nemotron profiles**, listed and not resolved.
