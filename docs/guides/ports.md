# Writing your own

Kingfisher reaches the world through the Protocols in
[`core/interfaces.py`](../../src/kingfisher/core/interfaces.py) — what runs a shell
command, and where each turn's record goes — and through the `sessions` every
deployment names. The Protocols have defaults; the sessions have none to fall back
on, and kingfisher's own, `LocalSessions`, works on one host with its own disk. This
page is for a deployment that needs one of them to be something else — a sandbox, a
bucket, another machine.

A different question from *what can I set?* — [`configuration.md`](configuration.md)
lists the variables, and `.env.example` argues each one where it is set. This
page is about writing the thing a setting names.

Satisfaction is by shape. An interface is a Protocol, not a base class, so yours
implements the methods and inherits nothing — a test satisfies most of them with
a dict.

## The two ways to supply one

**As a constructor argument**, which every port accepts:

```python
kingfisher = Kingfisher(cfg, sessions=LocalSessions(runner=my_runner), run_events=my_sink)
```

**As a setting**, for the command line only. A constructor argument reaches the
construction site you control, and the `kingfisher` command builds its own with
nowhere to point it. `KINGFISHER_SESSIONS` is where every verb but `seed` is told
the sessions, and the only port the command takes: it runs every other one as
kingfisher's own, and code that needs another builds its own `Kingfisher`.

```
KINGFISHER_SESSIONS=mycompany.sessions:build_sessions
```

It names `module:name` — something **callable with no arguments** that returns
yours. Zero arguments is the whole convention: kingfisher does not know
whether yours wants a bucket, a region, a DSN or a pool, so it asks for none of
them and your factory reads its own configuration. A class with a no-argument
`__init__` satisfies it as readily as a function.

Kingfisher checks the **name**, not the building. A spec that will not parse, a
module that will not import, an attribute that is not there, a result of the
wrong shape — each is a `ConfigError` naming the setting. A factory that raises
its own exception is left alone: that is your code failing at your job, its type
may be one your own error handling knows, and the traceback already says which
setting reached it.

## Which there are

Beside `sessions`, `Kingfisher` takes these, each optional and left to kingfisher's own
when left out:

| What it changes | Keyword | Where |
|---|---|---|
| **Prompt text** | `instructions` | [`formats.md`](formats.md#the-prompt-is-added-to-not-replaced) |
| | `turn_instructions` | [below](#turninstructions--text-for-one-turn) |
| **What a turn may hand over or run** | `pdf_attachments` | [below](#pdfattachments--what-the-model-is-handed-in-place-of-a-pdf) |
| | `scratch_run_limit` | [below](#scratchrunlimit--which-graphs-tools-may-run-commands) |
| | `subagent_middlewares` | [below](#subagentmiddlewares--what-a-portable-subagent-runs) |
| **After the turn** | `run_events` | [below](#runevents--where-each-turns-record-goes) |
| | `unused_results` | [below](#unusedresults--what-becomes-of-a-saved-result-nothing-names) |

Something kingfisher does that you want done differently is usually middleware, which a
definition names. One of these exists where only kingfisher knows what the change needs
-- a verdict, which graph is built, the end of a turn -- and it has to hold for every
agent with no definition able to forget it. `docs/decisions.md` has the rules a new one
is admitted by.

## What yours has to promise

Each interface says what an implementation has to keep, in its own docstring, and
this page says why each promise matters where it describes the interface. Two of them
kingfisher checks itself, on every backend a session opens; see *Checking what you
returned* below. The rest are yours to test.

Kingfisher holds its own implementations to every promise with plain functions that
raise `AssertionError`, in this repository's `tests/checks/`: `runner.py` for a
`CommandRunner`, `sessions.py` for a `Sessions`, and `backend.py` for what its `open`
returns. They ship with no wheel, and copying them is the shortest way to hold yours to
the same. A sessions check and a backend check each take a `make` returning a fresh
`(Config, thing)` pair, because what they hold you to is that deployment's layout. Most
do more than read: a backend is checked by writing files through it, and a runner by
running commands, one of which waits a second for a timeout. Run those where you would
run an integration test.

They are worth running even if yours looks obviously correct. Sessions that hand
every session the same storage pass every functional test you are likely to write,
while each caller reads every other caller's files as their own.

## `CommandRunner` — what runs a shell command

`run(command, timeout=None)` returning a `CommandResult`. For a deployment that
runs commands somewhere else, or as another user, or with resource limits. Kingfisher's
own shell always hands it a `timeout`, `KINGFISHER_EXECUTION_TIMEOUT_S` at most.

Supplied to kingfisher's own sessions as a **callable taking the session
directory**, not an instance: `LocalSessions(runner=lambda session_dir:
MyRunner(session_dir))`, passed as `sessions=`. A runner is built for one session —
kingfisher's own Landlock fence is, because its policy is generated from the session
— and a shared instance could not know which session it was running for. It goes on
the sessions rather than on `Kingfisher` because only they know where a
session is on this host; sessions of your own run their commands however
their `open` builds the backend.

Held by `tests/checks/runner.py`, whose checks run commands, two of them through a
timeout. Things to know:

- **`local` decides whether the fence is applied.** The command arrives already
  confined when `local` is True, which is the default and what you get by not
  saying. A runner that ships the command elsewhere must set `local = False`,
  because the confinement names paths on *this* host — a `sandbox-exec -f
  /Users/.../shell.<digest>.sb` sent to another machine fails looking like a broken remote
  shell rather than a wrong prefix.
- **Setting `local = False` when you are local loses the fence**, silently. The
  default is True so that forgetting the flag yields more confinement than
  needed, never less.
- **What a command starts goes with it**, when it exits and when its time runs out.
  Start it in a process group of its own and kill the group, not only the shell: a
  process left behind writes into the session after the turn has let go of it.
  `a_command_leaves_nothing_running` looks for the mark such a process would leave in
  `$TMPDIR`, and refuses to pass a shell that cannot write there.
- **A runner is handed the command and nothing else** — no working directory and no
  environment, local or not. Its shell starts wherever the runner starts it, and
  `$TMPDIR` and `$KINGFISHER_SKILLS` are whatever they are there. So the agent is not
  told what kingfisher tells it about its own shell, which says all three; it is
  told, neutrally, that the shell may not see the session's files. Say what yours
  sees with `shell_prompt=` beside the runner: see
  [`ShellPrompt`](#shellprompt--what-the-agent-is-told-about-your-shell).
- **Say where your shell sees the session with `session_root=`**, a function of the
  session folder like `runner`, and have the runner put the same value in its shell's
  environment as `KINGFISHER_SESSION_ROOT`. One function can feed both:

  ```python
  where = lambda session_dir: f"/home/runner/job-{session_dir.name}"
  LocalSessions(runner=lambda session_dir: SshRunner(root=where(session_dir)), session_root=where)
  ```

  The agent is then taught to spell a session path from the variable,
  `"$KINGFISHER_SESSION_ROOT/inputs/data.csv"`, in its prompt and in `execute`'s
  description, and a command that misses a file-tool path is answered with that
  spelling. A remote runner gives its own side's path. The command still reaches `run`
  exactly as the model wrote it: kingfisher puts nothing in front of it, so exporting
  the variable is the runner's job. `session_root=` without a runner is refused, since
  kingfisher's own shell exports its session folder itself; without `session_root=`,
  the agent is told nothing of the variable.
- **A subclass of `LocalSessions` keeps the runner.** `super().open(...)` builds
  the backend with the runner the instance was given, so an `open` that adjusts what
  it built still runs its commands where you chose. See
  [`sessions`](#sessions--the-filesystem-the-agent-runs-against).
- **A timeout is a result, not an exception**: `exit_code` 124, the shell's own,
  with output saying so. Raising would make your failure the model's problem
  rather than a tool result it can read and retry. This is the one the checks
  exist for: every timeout API in Python raises, `subprocess.run(timeout=...)`
  included, so the obvious implementation gets it wrong and nothing else would
  say so.

`CommandResult` is exported from `kingfisher.plugins` — you return one of these, so
you need it. It carries `output`, `exit_code` and `truncated`; `exit_code` is not
optional, because a caller deciding whether a command worked has nothing to do
with `None` but guess.

Only *running* the command is delegated. File access is not, and deliberately:
the shell backend is also the filesystem for every unrouted path, so handing over
"the shell" would hand over `/outputs` with it.

## `ShellPrompt` — what the agent is told about your shell

Kingfisher can say how a shell sees the session's files only of the shell it runs
itself: started in the session root and exporting it as `$KINGFISHER_SESSION_ROOT`,
so `/inputs/data.csv` is `"$KINGFISHER_SESSION_ROOT/inputs/data.csv"` there, with
`$TMPDIR` the scratchpad and `$KINGFISHER_SKILLS` the catalogue. Any
other shell is described neutrally — it may not see a file-tool path under that name
or any other, so check before relying on one — until it says what is true of it:

```python
from kingfisher.plugins import LocalSessions, ShellPrompt

SANDBOX = ShellPrompt(
    prompt="""## The shell

The shell (`execute`) runs in a sandbox holding this session at `/workspace`, so
`/{{inputs}}/<name>` is `/workspace/{{inputs}}/<name>` there.""",
    execute=(
        "- The shell holds this session at `/workspace`: `/{{inputs}}/data.csv` is "
        "`/workspace/{{inputs}}/data.csv`. Use the optional timeout to override the default."
    ),
)

backends = LocalSessions(runner=lambda session_dir: MyRunner(session_dir), shell_prompt=SANDBOX)
```

A shell of your own carries it instead — the object your backend runs `execute` on,
which is the composite's `default` where you return a `CompositeBackend` — and carries
`session_root` beside it where it exports `KINGFISHER_SESSION_ROOT`, as a `str`:

```python
class MySandbox(BaseSandbox):
    shell_prompt = SANDBOX
    session_root = "/workspace"
```

Things to know:

- **Three parts, because three readers see different ones.** `prompt` is the agent's
  section on the shell, in place of kingfisher's. `skills` goes in the skills index, so
  it is shown only to a graph holding skills, subagents included. `execute` replaces
  deepagents' "Use absolute paths…" line in the tool's description, which every graph
  holding the shell reads: a subagent is handed the system prompt's session section and
  none of its shell section. A graph not granted `execute` is told none of the three. Kingfisher's own are
  `src/kingfisher/plugins/sessions/prompts/shell_here.md`, `shell_here_skills.md` and
  `shell_here_execute.md`.
- **A part left `None` is said the neutral way**, claiming only what holds wherever a
  shell runs — for `execute`, deepagents' own line stays — and `""` says nothing there.
- **`{{inputs}}`, `{{outputs}}`, `{{memory}}`, `{{scratchpad}}` and `{{tool_results}}`
  are filled from `Config.layout`**, as kingfisher's own wording is, so renaming a
  folder cannot leave your sentence naming the old one. Any other `{{name}}` is refused
  when the `ShellPrompt` is made.
- **It is read off the shell, not off your `LocalSessions` subclass.** A subclass
  inherits everything the class has, so one that swapped in a sandbox would have
  inherited kingfisher's wording and its agents been told of a shell they do not have.
  A sandbox that says nothing is described neutrally.
- **Kingfisher's own shell describes itself**, so `shell_prompt=` without a runner is
  refused. Standing instructions for every agent go in `INSTRUCTIONS.md` beside the
  definitions, or `instructions=` on `Kingfisher`.
- **Where the shell has a session root, the agent is told the variable after your
  wording.** A sentence teaching `$KINGFISHER_SESSION_ROOT` follows your `prompt` and
  `execute` text, or the neutral wording where a part is `None`; for `execute` it takes
  the place of deepagents' line. A part set to `""` stays silent, and a part already
  naming `KINGFISHER_SESSION_ROOT` is left as you wrote it, so you can word it yourself.
  A command that fails on a file-tool path is answered with the path spelt from the
  variable, on such a shell only. A `session_root` that is not a `str` is refused,
  naming your shell's class.
- **deepagents' list of this host's paths is kept only for a shell on this host.**
  deepagents adds a "Shell paths vs. virtual paths" section giving each routed folder's
  path here. It stays for kingfisher's own shell and for a runner whose `local` is
  true, and is removed for a runner that runs elsewhere and for a shell of your own,
  so your `ShellPrompt` is the only thing the agent is told about where files are. An
  agent or subagent not granted `execute` is never sent it.

## `RunEvents` — where each turn's record goes

What a turn did — its start and end, every model call with its token counts, every
tool call — as one event at a time, while it happens:

```python
class ShipToCollector:
    def record(self, event):
        collector.send(dict(event))

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), run_events=ShipToCollector())
```

Each event is a flat mapping. `event` names it — `run_start`, `run_end`,
`model_call`, `tool_start`, `tool_end`, `tool_error`, `model_error` — and `ts`,
`session_id`, `turn_id`, `model` and `endpoint` ride on every one — on `model_call`
and `model_error` the last two are the model that made the call, a subagent's own
where it runs one, and elsewhere the main agent's. `model_call` carries `input_tokens`,
`output_tokens` and `cache_read`, which is what a bill is totalled from, and `cut_off`,
true for a reply that stopped at the model's output cap.

**Wire nothing and they go to the `kingfisher.run` logger**, one JSON line per event
at `INFO`, with the mapping itself on the record as `run_event` for a handler that
ships structured logs. Python's default configuration drops `INFO`, so a
deployment that wants them either configures logging or passes a sink — and
`kingfisher run` and `decide` keep none unless given `--log FILE`, which appends one
JSON line per event.

**Not session state.** They used to be a file in the session's `.harness`, deleted
with it. They outlive the session now, because a log is most wanted for the turn
that went wrong, and keeping them is your retention policy rather than
kingfisher's. `session_id` and `turn_id` are how one session's are found again.

**A sink that raises does not fail the turn.** The failure is logged as a warning
and the turn goes on — so a sink that is failing is heard from only in your logs.
It is called once per event, and not always from the thread running the turn: tool
calls a model makes together run at once, on an executor's threads, and on the
async path langchain hands each event to a worker thread. So the events of tool
calls made together can interleave, though each call's start comes before its end,
and a sink that keeps state between events wants a lock.

## `UnusedResults` — what becomes of a saved result nothing names

Every result kingfisher records — not a file tool's, nor the checklist's — is saved under
`/scratchpad/tool_results/`, and the session's ledger names its file. Two things leave a
file there that no row names: a turn that died before it wrote its ledger, and a result
older than `KINGFISHER_KEEP_RESULTS_ROUNDS` keeps, once its row is expired. Kingfisher's
own plugin removes them; yours decides instead — keep them in an archive, count them,
hold them for an audit:

```python
from kingfisher.plugins import UnusedFiles

class ArchiveUnusedResults:
    def __init__(self, archive):
        self.archive = archive

    def settle(self, found: UnusedFiles) -> None:
        for one in found:
            self.archive.put(found.session_id, one.path, one.read())
            one.remove()

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), unused_results=ArchiveUnusedResults(archive))
```

The command does not take it. Left out, it is
`DeleteUnusedResults`, which removes every file it is handed.

`settle` is asked at the end of a turn, once the ledger and the conversation are
written, and only when there is something to hand over: a turn that leaves no unused
file does not call it. What it is handed is an `UnusedFiles`, which has the
`session_id` they are in and iterates as one `UnusedFile` per file, each with `path`
(as the agent's file tools name it), `size` (in bytes as the backend listed it, or
`None` where the listing did not say), `read()` and `remove()`. That is all of it.
There is no way through it to any other file in the session: a path not among those
handed over is refused, and the session's backend is not on offer.

Every file handed over has already been judged safe to drop: no row names it, and its
name starts with a result's id, as the recorder names it, so a file the agent's shell
wrote into the folder under a name of its own is never among them. Nothing outside that
folder is ever handed over: it is the one place a result is saved.

Things to know:

- **A file you keep is handed over again every turn**, since nothing names it and
  nothing remembers that you kept it. It counts toward `KINGFISHER_SESSION_MAX_BYTES`
  for as long as it is there.
- **The same file can come twice.** A `settle` that failed part way is handed what it
  left next turn, so archiving one again has to be harmless.
- **A file may already be gone.** `remove()` of one is not an error; `read()` of one
  raises `FileNotFoundError`. `remove()` asks the backend and does not check its
  answer: a file it would not remove is handed over by the next turn.
- **It runs while the session is claimed**, so no other turn in it starts until
  `settle` returns. Be quick: put slow work, such as an upload, on a queue of your
  own, having read what it needs, and return. It is a plain method: on the async path
  it is called on kingfisher's thread pool, off the event loop.
- **An exception is logged and the turn still finishes**, with its answer. The warning
  names the session and how many files were left, and they are handed over again next
  turn.
- **A path is unique within a session, not across them.** Key what you keep by
  `found.session_id` as well, as the example does.
- **Deleting a whole session does not go through it.** `Session.delete`, `reap` and
  `delete_session=True` remove everything through the sessions plugin's `delete`, so a
  deployment that archives results archives a session it is about to delete there.

## `TurnInstructions` — text for one turn

`INSTRUCTIONS.md`, or `instructions=`, is said to every session alike, inside the part
of the system prompt Anthropic caches. Text that differs from one turn or session to the
next — today's date, who the user is, which case this conversation is about — goes here
instead, and is sent after that part, so every session still shares its cache:

```python
from zoneinfo import ZoneInfo


class DeskInstructions:
    def text(self, cfg, session_id: str, caller) -> str:
        here = caller.started.astimezone(ZoneInfo(caller.context.get("tz", "UTC")))
        return f"Today is {here:%A %-d %B %Y}. You are working with {caller.context['name']}."

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), turn_instructions=DeskInstructions())
```

The command does not take it: `kingfisher run --tell TEXT`, and `decide --tell TEXT`
for a resumed turn, send TEXT the same way. Left out, no turn is told anything of
its own.

Things to know:

- **`caller` is the turn's `Caller`**, the one tools read on `runtime.context.caller`:
  the request's `context`, the caller's source ids and datazone, and `started`, when the
  turn started in UTC ([`tools.md`](tools.md#who-the-turn-is-for)).
- **Every graph the turn builds is sent the same text**: the agent, each subagent it
  declares and their helpers, and `general-purpose`. A compiled subagent is not sent it,
  as it is not sent `INSTRUCTIONS.md`: its `build` writes its prompt. No definition says
  anything about it, so a definition works the same whatever the deployment's hook says.
- **Write any caution into the text yourself.** Kingfisher adds nothing to it. The
  memory guidelines already tell the model never to store details identifying the user.
- **It is asked once per turn**, as a plain method: on the async path it is called on
  kingfisher's thread pool, off the event loop. Nothing it says is stored.
- **Where it goes**: after the agent's own prompt and every section kingfisher and
  deepagents add to it, and before the memory block. It takes no cache breakpoint of
  its own: with memory on, Anthropic's four are already spent on the tools, the static
  prompt, the memory block and the conversation. So text that changes between one turn
  and the next costs the memory block and the conversation their cache, and nothing
  before them.
- **An empty string sends what a deployment without the plugin sends.**
- **Every model is sent it.** It goes where Anthropic's prompt caching does, and is
  sent to a model that caching does nothing for all the same. A helper, which deepagents
  gives no prompt caching, is sent it with no breakpoint, as it had none.
- **It is sent from deepagents' `AnthropicPromptCachingMiddleware` slot**, so a
  deployment that replaces that middleware and also wires this plugin is refused when
  the agent is built — the replacement would take the slot, and the text would never
  be sent. A replacement written to add text after the cached part is what this plugin
  is for: drop it.

## `PdfAttachments` — what the model is handed in place of a PDF

Kingfisher judges a PDF a tool returns by its size: past what the turn holds, the model
is handed a line saying so instead ([`tools.md`](tools.md#what-a-tool-returns) has the
limits). Yours is asked about every PDF once kingfisher has judged it, and may hand over
something else in its place: a page limit, a summary, a refusal for a kind of document.
A page limit, counted with pypdf:

```python
import io

from pypdf import PdfReader

from kingfisher.plugins import ReturnedPdf

class AtMostTwentyPages:
    def replacement(self, pdf: ReturnedPdf) -> str | None:
        data = pdf.read()
        # Kingfisher's own line for one it refused, and nothing to count for a reference.
        if pdf.verdict != "passes" or data is None:
            return None
        pages = len(PdfReader(io.BytesIO(data)).pages)
        if pages <= 20:
            return None
        where = pdf.path or f"{pdf.tool}'s result"
        return f"{where} is a {pages}-page PDF. Work from its text, a range of pages at a time."

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), pdf_attachments=AtMostTwentyPages())
```

The command does not take it. Left out, every PDF is judged by its size
alone. pypdf is yours to install: this repository's `agent` group has it for the shipped
PDF tools, and the package does not depend on it.

`replacement` is handed a `ReturnedPdf`: kingfisher's `verdict` — `"passes"`,
`"too_large"` for one larger than a turn holds on its own, `"turn_full"` for one that
fits alone but not beside the PDFs already open — with the PDF's `size` in bytes, the
bytes the turn already `held`, the `allowance` one turn of that graph holds, the `tool`
and `call_id` whose result carries it, the `path` `read_file` read it from (`None` from
any other tool), and `read()` for the PDF itself (`None` for one the provider fetches by
reference). It returns `None` to leave the verdict as it is, or what to hand over
instead: a string, or content blocks, as a tool may return.

Things to know:

- **It is asked of every PDF**, from `read_file` and offered tools alike, inside a
  `Command` too, on the agent, every subagent a definition declares, and
  `general-purpose`. Not on a compiled subagent, which deepagents runs as it was built.
- **What it returns is judged again**, by the same limits, and it is never asked about
  its own answer: a PDF handed back past what the turn holds is still refused. What it
  hands over counts toward the turn as what it is, so text in place of a PDF leaves room
  for the next one.
- **Anything it hands over counts as the call succeeding.** The result is recorded, and
  gets an id, as any other; a call whose only answer is kingfisher's own line is a failed
  call, with no row.
- **Nothing is not a replacement.** `""`, no blocks, or blank text alone is logged and
  read as `None`, so kingfisher's verdict stands: handed over, it reached the model as an
  empty failed result with no word why. To withhold a PDF, hand over text saying so.
- **The calls of one graph are asked one at a time**, since each PDF is judged beside
  what the one before became. Be quick. It is a plain method: on the async path it is
  called on kingfisher's thread pool, off the event loop.
- **An exception is logged and the verdict stands**, so a failing plugin costs neither
  the limit nor a PDF that passes.
- **It runs in the kingfisher process**, on whatever a tool returned. Parse with a
  library you would trust with a file a stranger sent.

## `SubagentMiddlewares` — what a portable subagent runs

A [portable subagent](formats.md#a-subagent-that-travels--a-subagents-entry-with-no-build)
may not name middleware: the names are this deployment's, and a definition written to
travel has never seen them. Left out, each one runs the agent's own, the names the
agent's file wrote, in its order, with the settings it wrote for each. Yours decides
otherwise, for one subagent or every one. Turning it off:

```python
from collections.abc import Sequence

class NoneOfTheAgents:
    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Sequence[str]:
        return ()

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), subagent_middlewares=NoneOfTheAgents())
```

The command does not take it. `names` is told the subagent's name,
the agent's, and the agent's middleware, and returns which of them the subagent runs.
Kingfisher's own, `AgentsMiddlewares`, returns `middlewares`.

Things to know:

- **Only a portable subagent is asked about.** A subagent document, a YAML file,
  writes its own `middlewares:` line, and a compiled one is given no middleware at all;
  neither changes. A `SUBAGENTS` entry with no `build` is portable wherever it is
  written, your definitions folder included.
- **It is told the agent's names at any depth.** A portable helper another subagent
  consults is told the main agent's, not its summoner's: the agent's line is the ceiling
  everything beneath it is held to, and a compiled summoner has no line.
- **A name outside `middlewares` is refused** when the agent is built, as one a
  definition names past its ceiling is, and the error says your `SubagentMiddlewares`
  chose it. It is not dropped: a subagent running without the audit hook you meant it
  to have is the failure the refusal exists for.
- **The settings are the agent's, the model is the subagent's.** Each name is built
  with what the agent's file wrote for it; a middleware that `wants` a model is handed
  the one the subagent runs.
- **It is asked once for each portable subagent built**, a helper included, when the
  agent is built: once a turn. With no agent resolved there is nothing to inherit, and
  it is not asked.

## `ScratchRunLimit` — which graphs' tools may run commands

An offered tool is handed
[`runtime.context.run`](tools.md#a-tool-that-runs-a-command) where the agent or subagent
calling it holds `execute`, ungated. Yours may refuse it to some of those graphs: a
subagent whose tools should never start a process, say, though it may use the shell
itself.

```python
from typing import Literal

from kingfisher.plugins import ScratchRunLimit

class NoRunsForResearch:
    def allows(self, name: str, kind: Literal["agent", "subagent", "general-purpose"]) -> bool:
        return name != "researcher"

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), scratch_run_limit=NoRunsForResearch())
```

The command does not take it. Left out, it is `NoScratchRunLimit`,
which allows every graph kingfisher would hand a run.

`allows` is told the graph's `name` and its `kind`: `"agent"` with the agent's name,
empty for a request naming none; `"subagent"` with the name a definition gives it; or
`"general-purpose"`, by that name, for deepagents' own, which is handed the agent's
tools.

Things to know:

- **It can only take away.** A run is bound where kingfisher allows it *and* yours does.
  Answering `True` for a graph refused `execute` hands it nothing: a request refused the
  shell stays refused through its tools.
- **Kingfisher's own refusals stand whatever it answers.** A gated `execute` refuses,
  a tool called from `eval`'s code is never handed a run, and `run_in_scratch` refuses
  what it refuses.
- **It is asked once per graph built**, when the agent is built for a turn, and only of
  a graph holding offered tools. Not of a compiled subagent, whose tools are never
  handed a run.
- **An exception it raises refuses that graph's runs, not the turn.** It is logged as a
  warning, once per graph built, and each run a tool on that graph asks for comes back
  with an `error` naming it -- `refused: this deployment's ScratchRunLimit raised
  KeyError: 'researcher'`. The other graphs keep what they would have had. An error is
  never taken as allowing a run.
- **Only `True` allows, and `allows` is not `async`.** It is called as the graph is built,
  not awaited, so an `async def allows` answers a coroutine. That, or any other answer but
  `True` or `False` -- `1`, `None` -- refuses the graph's runs the way a raise does, logged
  and named: `refused: this deployment's ScratchRunLimit answered coroutine, not True or
  False`. Look a policy up before the service is built, not inside `allows`.

## The rest

**The conversation is not for replacing either.** Kingfisher keeps it in the session,
as the transcript each turn is handed, and runs every turn on a store of its own that
holds nothing once the turn ends; a turn that stops for approval is written into the
session and read back by the answer. There is no `threads=` to pass a langgraph saver
of your own: one that remembered earlier turns showed the model each of them twice.

Definitions are not covered here, because how they are read is not for replacing:
kingfisher reads each kind from a directory. Definitions kept somewhere else are staged
into one first, and `KINGFISHER_DEFINITIONS_DIR` in [configuration](configuration.md)
says which.

## `sessions` — the filesystem the agent runs against

Not optional. Every `Kingfisher` names them:

```python
from kingfisher import Kingfisher
from kingfisher.plugins import LocalSessions

kingfisher = Kingfisher(cfg, sessions=LocalSessions())
```

Most deployments write exactly that and read no further. `LocalSessions` opens the
backend kingfisher used to build for you without asking, unchanged — naming it
costs you nothing and buys the rest of this section a reader.

**Why it is the one thing on this page you cannot leave out.** The backend is the
sandbox. It wraps every shell command in `sandbox-exec` or Landlock. It refuses a
host path handed to a file tool. Its route table is what makes `/inputs` read-only
legal at all — deepagents refuses read-only rules outright on a backend that
executes unless every rule sits under a route. And it is the filesystem for every
unrouted path, which is why the model can be told, in a table it reads every turn,
that *nothing in the workspace is out of the shell's reach*.

None of which makes the old silence unsafe: the default was always the strict
option and still is, and requiring the parameter makes no deployment safer on its
own. What it does is make sure nobody wires kingfisher without finding out there
is a boundary here at all.

**What it is: a `Sessions`.** One object whose `open` is called per turn
with the id of the session it is for, and returns that session's backend — and which
answers the questions
only something that sees every session can: `sessions(cfg)` lists them with when
each was last used, `mark_used`, `size` and `delete` do what they say. `reap`,
`kingfisher sessions` and the session quota are answered by it. `LocalSessions`
keeps each session as a directory under `<workspace>/sessions`, named for its id, and
refuses with `UnknownSessionError` an id that cannot name one there — empty, starting
with `.`, or holding `/` or NUL. Which session a turn runs in is the request's to say:
any id names a session, the first turn naming one starts it, and a request naming
none runs in `default`. So `open` is handed ids nobody has used before, and makes
them; your own sessions are too. That is why a service must derive the ids
it sends itself and never forward a user's: an id is no longer something kingfisher
handed out, and naming one is reaching it, unless the access policy stops the
caller at the session's pinned agent.

**Try a mount before replacing it.** Sessions that must outlive the machine, or
never touch its disk, get durable or memory-backed storage mounted at
`<workspace>/sessions`, and cost you none of the four jobs above.

A symlink on `<workspace>/sessions` is the same move, lighter, and the macOS profile
follows it: its rules are built from the real path that link resolves to, so a session
on another volume is writable and its `/inputs` and `/.harness` are still refused. One
link deep is as far as that reaches. A *single* session that is itself a link lands
where the profile names nothing -- it is one static text per workspace, written before
any session exists -- and that session is then unusable, every command in it failing at
`cd` with a message about the shell. `kingfisher doctor` names one when it sees it, and
the Linux fences are unaffected either way, being built from the session directory
itself.

**Replace it when your callers may not share storage.** That is the case a mount
does not cover, and the reason this seam is open. A mount is established once,
outside the process, before any session exists — so a session created at runtime
cannot be given one of its own, and every session ends up on one mount separated
by a path prefix and nothing else. A deployment that forbids one caller's session
from reaching another's needs the separation in the wiring instead.

**Build on the default rather than from nothing.** Subclass `LocalSessions`,
override `open`, and change the one thing you came to change. Host-path
refusal, the route table, the confinement and the housekeeping all survive without
your thinking about them:

```python
from kingfisher.plugins import LocalSessions

class MySessions(LocalSessions):
    def open(self, cfg, session_id, /, *, catalogue=None):
        mine = super().open(cfg, session_id, catalogue=catalogue)
        return MyScoped(default=MySandbox(session=session_id), routes=mine.routes)

kingfisher = Kingfisher(cfg, sessions=MySessions())
```

Where `MyScoped` is a `CompositeBackend` of your own, hand it
`artifacts_root=mine.artifacts_root` beside the routes, or deepagents writes its
own files at the session root — see *deepagents' own files* below.

`backend_at(cfg, directory)` is the same thing for a directory of your choosing.
A plain function is refused with a `TypeError` that says to do this: it can build a
backend, and cannot say which sessions there are.

**The async path asks for each method's `a`-prefixed twin.** `Kingfisher.asession`
and `asessions` list sessions with `asessions`, and open one with `aopen` where a
caller is named; a `Session`'s `apending`, `aartifact` and `afiles` open it with
`aopen`, `asize` asks `asize`, and `adelete` deletes it with `adelete`; and a turn on
`astream` also records the session used with `amark_used` and checks its quota with
`asize`.
`Sessions` carries all five, running the sync method on kingfisher's thread
pool, so a class that subclasses it — `LocalSessions` does — has them already.
Override one where it is a round trip you can await: the async path then holds no
thread while it waits. A class that does not subclass `Sessions` has to write
all five, because the type check asks for every method. The backend `open` returns is read through its own async methods,
`adownload_files` and the rest, which deepagents' `BackendProtocol` already has.

Starting from nothing instead is allowed and is yours to get right — which is a
thing to do deliberately, not to discover. Two of the six contract checks below
run on every backend kingfisher resolves and will tell you about the two failures
that otherwise report nothing.

**Take `catalogue` even if you ignore it.** It is what lets a session see the skills
your bundles ship, and an `open` that quietly drops it gets a backend that is
perfectly well-formed and sees none of them. It is a `Definitions`, from
`kingfisher.plugins`, and `catalogue.skill_mounts()` lists where your backend has to
serve its skills: each a `route` under `/skills/` and the `directory` behind it, one for
each subagent's bundle and each mount. `catalogue.skill_sources()` adds the catalogue's
own folders, as every place the agent is given a skills index, each a `route`, a
`folder` and the `source` in it deepagents lists, and `list_skills(backend,
path)` lists one the way deepagents will: listing each through your backend and through
the folder itself shows whether you serve what kingfisher read. Nothing at runtime can see that mistake,
which is why `Sessions` is a typed protocol rather than a line of prose —
write the signature out and your type checker catches it.

**What `open` returns is one session's, and only one.** A backend shared between
sessions is one filesystem for every caller — usually the thing you are replacing
the backend to avoid. Passing a backend itself is a `TypeError`, and
`two_sessions_are_kept_apart` in `tests/checks/sessions.py` catches the subtler version: every
id handed the same storage.

**The turn lock is on the sessions.** They have `claim(cfg, session_id, /, *,
stale_after, now=None)`, `release(cfg, session_id, token, /)` and `held(cfg, session_id,
/, *, stale_after, now=None)`: a claim that fails while another is live, which a `write`
cannot be, because a `write` overwrites and two turns would both take it. One lock per
session, not one for the object: a turn in one session must not refuse a turn in
another. `claim` answers a token naming its taker, or `None` when a live turn holds the
session, and `release` gives back only the claim that token took: a turn that outlived
`stale_after` has had its claim taken over, and its release must leave the new one. A
claim older than `stale_after` is taken over by one caller however many judged it stale
at once. `LocalSessions` does it with `mkdir` under the session's `.harness`, the token
in a file inside, and a takeover that renames the stale claim aside under a name only it
has, so a second taker's rename fails. Subclass
`Sessions` and `aclaim` and `arelease` run them on kingfisher's pool; override them
where your lock is a round trip you can await. Two turns in one session share a
conversation and the last write wins, so this is what refuses the second. The backend
`open` returns carries none of this.

**A pre-built graph names its sessions too.** `Kingfisher(cfg, graph=...)`
runs its agent on the backend the graph was compiled with, but kingfisher still
places a request's inputs, collects what a turn left and takes the turn lock through
the sessions you pass — so a graph is refused without them, and one built on
kingfisher's own passes `sessions=LocalSessions()`. Pass the ones that reach the
sessions your graph's backend keeps: nothing can check that from outside a compiled
graph. Whatever saver it was compiled with, each turn runs a copy of it on kingfisher's
own, empty one: the conversation comes from the transcript, as it does for every agent. `run()` and `stream()` take `sessions=` too, and refuse to run without it,
with a graph or without.

**What you return is also what an offered tool is handed.** A tool that asks for
it reaches this backend's file methods directly — behind the turn's permissions,
and without `execute`; [`tools.md`](tools.md#or-the-tool-is-handed-the-sessions-filesystem)
is the tool's side of it. So every file method of `BackendProtocol` may be called
on yours, `delete` and the two batch ones included, whichever of them the
built-in tools happen to use. A pre-built graph is the exception: kingfisher
drives it with no context, and such a tool finds `runtime.context` is `None`.

**Kingfisher reaches the session through it too.** A request's `inputs` are placed
with `upload_files` under `/inputs/`, what a turn left is listed with `glob` under
`/outputs/` and `/memory/`, and `Session.artifact` fetches one with
`download_files`. Each tool result a turn records is saved with `upload_files` under
`/scratchpad/tool_results/`, outside the file tools' permissions, which deny that folder
to them, though your backend's own refusals still apply; at
the end of the turn that folder is listed with `ls`, and a result file no row of the
ledger names is handed to
[`UnusedResults`](#unusedresults--what-becomes-of-a-saved-result-nothing-names), whose
default removes it with `delete`. So all of those
work on a backend that keeps the session somewhere other than the directory it was
handed. `/inputs` has to take that upload
while refusing the agent's own writes: `LocalSessions` routes it to
`InputsBackend`, which lifts the permission bits for kingfisher's upload alone, and
a backend of yours meets the same promise its own way.

**Those are the default names.** A `Config` may rename a session's folders, and what
yours has to serve is what the `Config` it is handed says: `cfg.layout`, a `Layout`
from `kingfisher.plugins`, with each folder's name and a route for each --
`inputs_route`, `outputs_route`, `scratch_route`, `skills_route`, `harness_route` and
the rest. `DEFAULT_LAYOUT` is the one a `Config` has unless told. Two folders of the
workspace are on the `Config` too: `cfg.sessions_root`, where kingfisher's own sessions
are kept and against which the origins line tells them from yours, and
`cfg.kingfisher_dir`, kingfisher's own folder, where `LocalSessions` keeps what its
shell must not be able to change.

**Open it once if you use it around a turn.** `kf.session(session_id).files()`
builds a session's backend the way a turn does, through your sessions, and `run`,
`stream`, `arun` and `astream` take it back as `files=` and run the turn on it rather
than opening another. Where each `open` is a sandbox, that is one sandbox rather than
two -- and the `Session` keeps the one it opened, so asking it again opens nothing.
`afiles` is the same for a caller on an event loop, opening it with `aopen`. The
request has to run in the session they were opened for -- a request naming none runs
in `default` -- and one naming another is refused with `UnknownSessionError` before
the turn claims or writes anything. A backend kingfisher did not open is taken at
your word. `kf.session(session_id, create=True)` makes a session no turn has made
yet, so a caller can place its inputs before the first turn. The caller named to
`kf.session` is checked there, once; hand what `files()` returns only to code entitled
to that session, and the turn still checks the caller it is given.

**And what it keeps about the session, under `/.harness`.** The agent the session is
pinned to, its conversation, and a turn paused at an approval gate are all written
and read through your backend — which means your agent's shell can reach them too,
unless your backend keeps it out. Nothing signs them, so what stands between the
agent and rewriting its own pinned agent is that fence, and it is yours to get
right: **`shell_denied`**, in `tests/checks/backend.py`, drives `execute` at `/.harness` and
`/inputs` and fails if the shell can write either. `kingfisher doctor` warns when
`KINGFISHER_SESSIONS` names sessions of yours, because it
cannot look inside them.

Nothing else is read from a directory on this host. A backend that keeps its
sessions somewhere else keeps all of them there.

**Saying a file is on this host: `host_path`.** An offered tool that takes a `path`
is handed a real file to open — see [`tools.md`](tools.md). Kingfisher asks the
backend where that file lives here: it follows your routes, and a
`FilesystemBackend` answers from its own root, so a backend built from those needs
nothing more. A backend whose files are on this host by some other means — a
network mount, a cache — says so with an optional method on what your `open`
returns:

```python
class MountedFiles(MySandbox):
    def host_path(self, virtual):
        return Path("/mnt/sessions") / self.session_id / virtual.lstrip("/")
```

Return `None` for a path that is not on this host; the tool is refused with a
message saying to read through `runtime.context.backend`, which works anywhere.
What you return is taken at your word: it is handed to a tool running in
kingfisher's own process, outside every sandbox. So it must never name another
session's file — `a_host_path_stays_in_its_session` in `tests/checks/sessions.py` writes a file
into two sessions and reads back what your answer names. The turn's rules still
apply first: a path the file tools may not read, `/.harness` among them, is
refused before your method is asked. `host_path(backend, virtual)`, from
`kingfisher.plugins`, answers as kingfisher will: `(the root it must stay in, the
path)`, or `None`.

**Running a command for an offered tool: `run_in_scratch`.** An offered tool whose
agent may use the shell can run a program over files it hands in —
`runtime.context.run`, in [`tools.md`](tools.md) — and kingfisher asks your backend to
do it. Kingfisher's own backend makes a throwaway folder in the session's scratchpad and
runs the program through the session's fence. A backend whose shell is elsewhere supplies
its own, as an optional method on what your `open` returns:

```python
from kingfisher.plugins import ScratchRun


class MySandbox(...):
    def run_in_scratch(self, argv, files, *, outputs, timeout=None, stop=None):
        # Make a new empty folder, write `files` into it, run `argv` there, and read
        # back the names in `outputs`.
        return ScratchRun(output=..., exit_code=..., files={...}, missing={...})
```

Without it a tool's run is refused, saying so; kingfisher builds no stand-in from
`upload_files`, `execute` and `download_files`, which could promise none of this:

- `argv` is a program and its arguments, run with no shell, in a folder of its own that
  is gone afterwards.
- The command sees an environment of its own, not the shell's and not this process's.
- `files` and `missing`: each output that came back, and why each other one did not. An
  output that is a link is not followed; the bytes are read wherever no fence stands.
- `timeout` and `stop` end the command and everything it started: `timeout` in seconds,
  `stop` a `threading.Event` that `arun` sets when its caller is cancelled.
- A refusal is `ScratchRun(error=...)`, never an exception.

Kingfisher has already checked the names (plain, no folder), what the files come to, and
the call's deadline, before your method is asked. `scratch_runs_of(backend)`, from
`kingfisher.plugins`, finds the method as kingfisher will: yours, else your composite's
default's. **`scratch_runs`**, in `tests/checks/backend.py`, holds a backend that has one
to the list above, and asks nothing of one without.

**Three more extras are read where you declare them.** `unprotected` on the backend,
a tuple of what it could not make read-only, which a turn reports to its caller as
`protect_failed`. `runs_here` on the shell, `True` when its commands run on this host,
which keeps deepagents' own note of each folder's path here; a shell that does not say
is taken to run elsewhere. And `where(cfg)` on your sessions, the folder they are kept
in on this host, which the origins line reports; without it they are `<supplied>`.

**The fence comes from the environment unless you say.** `LocalSessions()` reads
`KINGFISHER_SHELL_SANDBOX` and `KINGFISHER_SHELL_PATH_EXTRA` as it is built, so the
command and your code fence the same way from one `.env`. To decide in code, hand it the
settings, and neither variable is read:

```python
from kingfisher.plugins import LocalSessions, ShellSettings

sessions = LocalSessions(shell=ShellSettings(sandbox="external", path_extra=("/opt/tools/bin",)))
```

`kingfisher doctor` builds its sessions from the environment, so it reports a fence chosen
this way only where the two agree.

For the command line, name it with `KINGFISHER_SESSIONS`; without it, every verb
but `seed` runs on `LocalSessions`.

### Checking what you returned

**Two of the six run on their own**, against every backend kingfisher resolves,
and raise `ConfigError` rather than letting a turn find out. Both of them only
look — an `isinstance` and a scan of your routes, no I/O — so they cost nothing
and there is nothing to switch on:

**deepagents decides what a backend is with `isinstance` against its own abstract
base class**, not by the methods present. Implement every method of
`SandboxBackendProtocol` correctly while inheriting nothing and the shell tool is
dropped from the agent's roster — the model is told execution is unavailable if
it reaches for it, and nothing would have told you. Inherit `BaseSandbox`, which
implements the file operations in terms of `execute`, or register with the ABC.

**The paths kingfisher denies writes under must sit under something you route**,
or the graph will not build: deepagents refuses `permissions=` outright on a
backend that executes unless every rule is scoped to a route.

Both are yours to ask before kingfisher does: `why_no_shell(backend)` and
`unrouted_denials(backend, cfg.layout)`, from `kingfisher.plugins`, each say why a backend
would be refused, or answer `None`.

**The other four stay yours**, in `tests/checks/backend.py`: that the backend reads
back what it was given, keeps deepagents' own files in the scratchpad, keeps the shell
out of `/.harness` and `/inputs`, and refuses a host path. Three write files and run
shell commands, and a library should not do either at a build. The fourth only looks,
and finds a folder in the wrong place rather than an agent that cannot work, which is
not worth refusing a turn over.

Run all six. The automatic pair costs nothing twice, and your own suite is a better
place to read a failure than a turn is. And hold the object that makes them to
`tests/checks/sessions.py`: that two
sessions are kept apart, that a session is there on the next turn, that the claim
is exclusive and released only by its token, a late one included, that what is listed
and deleted is what exists, that where you say
a file is on this host it is that session's file, that a session serves under
`/skills/` the skills kingfisher read, and that every async twin —
`aopen`, `asessions`, `adelete`, `aclaim`, `arelease`, `asize` and `amark_used` —
answers as its sync one does.

**A session has to serve under `/skills/` what kingfisher read.** Grants and the
read-deny rules come from kingfisher's own reading of the catalogue, each mount and
each subagent's skills, and the agent reads skills through your backend. Serve one
from anywhere else and it is either granted and missing, or readable however a request
narrowed, and nothing at runtime says which. `a_session_serves_the_skills_kingfisher_read`
lists each through one session and compares names and paths.

**What the agent is told about your shell has to be true of it**, and this is the
one left that reports nothing on its own. Your shell is described neutrally unless it
carries a [`ShellPrompt`](#shellprompt--what-the-agent-is-told-about-your-shell), and
whatever that says is taken at its word. Say a virtual path becomes a shell path by
dropping its leading slash, and route one somewhere the shell cannot follow, and the
agent can read its inputs and run nothing over them, with a confused model as the
only symptom.

**deepagents' own files have to land under `/scratchpad/`.** The summarizer saves
the conversation it replaces under the composite's `artifacts_root`, as
`conversation_history/`, and the prompt tells the agent that is `/scratchpad/`. A tool
result too long to show whole is kingfisher's to save, under `tool_results/`, whatever
`artifacts_root` says. The backend `backend_at` builds has it set, and so does a
subclass of its class that calls its `__init__`. A `CompositeBackend` of your own
defaults to `/` and puts that folder at the session root: pass
`artifacts_root="/scratchpad/"`. deepagents reads it only off a
`CompositeBackend`, so on anything else it is `/` whatever you set.
`offloads_in_the_scratchpad` reads it the same way.

**The shell may not write under `/.harness` or `/inputs`.** `shell_denied` writes a
file there through the backend, then has the shell try to overwrite it and to create
another beside it. `LocalSessions` passes on the strength of its sandbox; with the
sandbox off it fails on `/.harness`, which is exactly the deployment it is there
to catch.

**And the kept records, which the kit does not ask about.** `/scratchpad/tool_results/`,
`/scratchpad/conversation_history/` and the two folders beside them hold what happened
rather than what the agent made, and the prompt sends the model to read both of the first
two back — so a shell that can write them lets the agent forge a result or a conversation
for a later turn to find. The file tools are refused by the rules kingfisher builds from
`Layout.kept_records`, whatever backend is underneath; the shell is yours to keep out,
and `Layout.shell_read_only` lists the folders. It is not in `shell_denied` because
kingfisher's own Landlock fence cannot satisfy it — Landlock grants, so a read-only hole
inside a scratchpad it grants whole cannot be expressed, and a kit kingfisher's own
backend fails would be a wrong kit. *Every folder of kept records is denied, not only
the ones a row names* in `decisions.md` has the measurements.

The last is ordinary: if you refuse host paths, refuse them with `HostPathError`
(`from kingfisher.plugins import HostPathError`),
because that is the type `GuardedCallsMiddleware` turns into a correction the model can act
on. `reject_host_path`, beside it, is the refusal kingfisher's own backend makes:
call it from your file operations to refuse the same paths with the same message. Refusing them at all is optional — inside a sandbox of your own, `/etc/passwd`
is a file, and refusing it would be refusing your own filesystem.
