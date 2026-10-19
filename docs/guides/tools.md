# Writing a workspace tool

A `.py` file in `$KINGFISHER_DEFINITIONS_DIR/tools/` defining `TOOLS` — the list of
tools it contributes. Nothing is inferred: a helper in the same file stays a
helper.

This page is the whole of how one is written. What an agent or subagent
*definition* then says about a tool — granting it, naming which file's when two
files offer one name — is [`formats.md`](formats.md).

**A kingfisher tool is a LangChain tool.** That is the contract, and it is worth
stating plainly because it is a dependency you take on: the import below is
`langchain_core`, not `kingfisher`, and what `@tool` accepts and returns is
LangChain's to define. Kingfisher supports **`langchain-core` 1.x** and declares
that range in its own `pyproject.toml`, so a tool written against 1.x keeps
working across kingfisher upgrades. If a 2.0 arrives it is a breaking change
here too, and this line is where it will be said.

Nothing wraps it, with one exception below. A kingfisher-flavoured decorator would
be a second name for someone else's rule, and the loader accepts what LangChain
accepts — a `BaseTool` from `@tool`, an instantiated `BaseTool` subclass, or a plain
function. The exception is `ToolSpec`, which holds any of the three beside the one
thing LangChain has no field for: who may use the tool.

```python
from langchain_core.tools import tool


@tool
def http_fetch(url: str) -> str:
    """Fetch a URL and return the body as text. Use when a task names a page
    the workspace does not already hold."""
    ...


TOOLS = [http_fetch]
```

Three reasons to write one:

- **Something the built-in set cannot do at all.** The clearest reason, and the
  shape above.
- **Making an existing capability narrower.** `execute` can already reach a
  database, but it can reach everything else too. A tool states the reach in
  code, so a request can activate `sql_query` and *not* the shell.
- **A tool that outgrew one file.** Two tools sharing a notion of what a column
  is, as a package: `tools/csv_profile/__init__.py` defines `TOOLS`, and the
  modules beside it are imported relatively. Only the package is scanned; a
  helper module inside it stays a helper.

The docstring is not decoration — it is what the model reads when deciding
whether to call the tool, exactly like a skill's `description`. Write it as a
trigger condition and say what the arguments mean in a caller's words.

## The decorator is optional

`@tool` makes a `BaseTool`. A plain function works too, and `TOOLS` may hold
either:

```python
from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def line_count(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Count the lines in a text file. Use before reading a file you expect to
    be long, so you can ask `read_file` for the part you want."""
    ...


TOOLS = [line_count]
```

Kingfisher names a tool by `.name` where there is one and by `__name__` where
there is not, so everything downstream — a grant, an allowlist, the failure
guard — keys on the same string either way. The shipped `line_count` is written
this way on purpose.

**You give up none of what the model sees.** The docstring becomes the
description and the annotations become the argument schema, exactly as they do
under the decorator. What `@tool` buys is *control* over those: a name that is
not the function's, a description that is not the docstring, and its other
options.

**What you give up is on your side of the fence.** A plain function is not a
`BaseTool`, so your own tests call it rather than `.invoke` it, and there is no
`.args` to read back. For most tools that is simpler; for one whose schema you
want to assert, reach for the decorator.

## Or a class, when you want to declare the schema

The third shape, and the one to reach for when the arguments deserve describing
one by one, or when the tool needs state or an `_arun`:

```python
class Shout(BaseTool):
    name: str = "shout"
    description: str = "Return the text in capitals. Use when asked to shout."
    args_schema: Type[BaseModel] = ShoutInput

    def _run(self, text: str) -> str:
        return text.upper()


TOOLS = [Shout()]
```

**Note the `()`.** `TOOLS` holds tools, not classes. Naming the class is refused,
because unrefused it produces a *successful* wrong answer: it loads, is offered
to the model as `Shout` rather than the `shout` it declares, and calling it
builds a new instance and hands that back as the result.

```
shout.py: TOOLS names the class 'Shout' rather than a tool -- write Shout() to
build one. A class loads and is offered to the model, and calling it returns a
new instance as if it were an answer
```

## Or a `ToolSpec`, when the tool says who may use it

Not a fourth shape: a `ToolSpec` holds a tool of any of the three, and it is the only
thing here that imports kingfisher. A tool that reads something only some callers may
see can say so itself, rather than relying on every agent that grants it to remember:

```python
from kingfisher import ToolSpec


def sql_query(statement: str) -> str:
    """Run one read-only SQL SELECT and return the rows as CSV."""
    ...


TOOLS = [ToolSpec(sql_query, source_ids=["sales_db"])]
```

An entry of `TOOLS` is a `ToolSpec` or a bare tool, mixed as you like, and a bare tool
is `ToolSpec(tool)`: open to everyone. The tool inside is checked the way a bare one
is, so a class named without its `()` is refused here too.

`source_ids` is written the way a definition writes its own line — any one of the
names is enough, and a set is a requirement: `["admin", {"sales_db", "pii"}]`. Left
out, it is `["*"]`, everyone. `[]` is refused rather than read as nobody, as it is in
a definition. Every name has to be declared in `source_ids.yaml`, or the deployment
does not start. With no `source_ids.yaml` at all, the line has no effect.

It holds **whichever agent grants the tool**, and it is the tool's only lock: a
`tools:` entry is the tool's name and nothing more, and one writing `source_ids` is
refused at load, so who may use `sql_query` is read here and nowhere else. An agent
that should not hand the tool to anyone leaves it off its list. A caller the lock
shuts out does not get the tool, and is not shown it by `kingfisher list` or the run
report. The operator's `kingfisher list`, with no `--as`, prints the line itself, as
`tool sql_query  [sales_db]`. A result the tool produces is recorded in the session's
ledger under the tool's audience joined with the agent's.

`datazone="finance"` says the one datazone a caller must also be in — see
*Datazones* in [`formats.md`](formats.md). Left out, it is any.

The source ids are read off the `ToolSpec` when the directory is read and never put on
the tool: not in `metadata`, which LangChain hands to every callback, so they never
appear in a trace. A line that cannot be read is refused naming the file.

### Another name, or another description

`name=` and `description=` change what the model is shown and what a definition
grants, without touching the tool you wrote:

```python
TOOLS = [
    ToolSpec(Shout(), name="yell"),
    ToolSpec(line_count, description="Count the lines in a text file. Use before ..."),
]
```

They are applied when the directory is read. A `BaseTool`, from `@tool` or a class, is
copied with the new values; a plain function, async or not, is made into a
`StructuredTool` under them. The object in your file keeps its own name, so a test
calling it, or another file importing it, sees what it always did. A grant, an
allowlist and the check for two tools of one name all use the new name.

A class that computes its `name` or `description` — a property inherited ahead of
`BaseTool` does — answers with its own whatever a copy holds, so what you wrote would
never reach the model. That is refused at startup:

```
lookup.py: tool 'lookup' still answers with its own description after its ToolSpec
gave another -- Lookup computes it, as a property does, so a copy cannot change it.
Set it on the class instead
```

With neither, the tool is used exactly as written, and a plain function stays a plain
function.

## What a tool returns

Return a string.

That is the rule, and the reason to state it is what happens when you do not.
Nothing here reads your return annotation — the loader checks that `TOOLS` holds
tools and stops — so a value of any type is accepted, and LangChain alone
decides what the model is shown:

| You return | The model is shown |
| --- | --- |
| `"three columns"` | `three columns`, unchanged |
| `{"rows": 3}` | `{"rows": 3}` — `json.dumps`, so a `set` or a `datetime` is not what you meant |
| `None` | `null` |
| `("three", "columns")`, or the same as a list | `["three", "columns"]` — JSON, like any other list |
| a list or tuple of content blocks: `[{"type": "text", "text": "…"}, {"type": "image", "base64": "…", "mime_type": "image/png"}]` | the blocks themselves — a multimodal result. Every item has to be a block of a type LangChain knows (`text`, `image`, `image_url`, `json`, `search_result`, `custom_tool_call_output`, `document`, `file`). Anything else among them, a plain string included, and the whole value is JSON instead, an image's base64 with it |
| `[]` or `()` | nothing at all — a list of no blocks |
| anything JSON will not take | its `repr` |

Nothing fails in any of those rows, and that is the point. A tool returning the
wrong shape does not raise; it tells the model something unhelpful, in a
transcript nobody reads until the answer is already wrong. A string and a list of
blocks are the only returns where you decide what it says.

Three kinds of file are not shown as returned, from any tool. Each reaches the model
as a line saying what it was instead, and the rest of the result is kept; the call
reads as failed only when nothing else came back.

- **a PDF past what the turn holds**, whatever block carries it: a `file` block, an
  `image` block or a data URL. A turn holds PDFs up to half of where deepagents
  summarises the model's conversation: 249 KB on a model with no profile, 130 to
  159 KB on a 128k window by its `max_tokens`, 1.2 MB on a 1M one. Every PDF already
  open in the turn counts, so ten invoices of 200 KB are not ten passes. Sent whole,
  a PDF goes out again with every model call after it, and can end the turn.
- **an image over 5 MB of base64**, the most Bedrock and Google Cloud take in one
  image, or **past 10 MB of images in the turn**, half the 20 MB Bedrock takes in a
  request. Past the first the provider refuses the request, and every one after it
  while the image stays in the conversation.
- **any other `file` block**, a `.pptx` or `application/octet-stream`: no model here is
  handed one. The line tells the model to use a tool it holds that can read it.

A turn here is one graph's: a subagent counts what it opens, against its own model.
Each turn starts with nothing open: an earlier turn's results come back as their text
alone.

A message a tool writes itself is read the same way, inside a `Command` too. Return a
file that large, or of that kind, as a path, the way
[a tool that creates a file](#a-tool-that-creates-a-file) does.

**`read_file` is not run on audio, video, or a `.ppt` or `.pptx`.** The model is told
the file can't be handed to it here, and to call a tool it holds that can read that
kind of file on the same path. A tool that transcribes audio or pulls the text out of
slides is that tool: it takes the path, and returns text.

**`pdf_text` is a name kingfisher looks for.** When the model holds a tool called
`pdf_text`, the line for a large PDF also tells it to call `pdf_text` and read the
saved text a range at a time. Name a tool of your own that way to get the
recommendation; called anything else, the line advises without naming a tool.

Two shapes escape the coercion: a `ToolMessage` or a langgraph `Command` is
returned as-is and never wrapped, so a tool can write its own result or update
graph state. That is LangChain's door rather than one kingfisher holds, and two
things follow. A run event takes its tool name from the message, so a `Command`
writing its own leaves the log saying a tool ran without saying which; and this
harness puts the file tools on a real filesystem, so a `files` update in graph
state reaches nothing that reads it. [`decisions.md`](../decisions.md) says why
that is documented rather than refused.

A two-tuple is content-and-artifact only if you asked for it with
`@tool(response_format="content_and_artifact")`, which then *requires* the pair
and raises naming the type it got instead. Without that declaration a tuple is
read as a list: JSON, unless every item is a content block.

## When a tool fails

Raise. The exception becomes a failed tool result — its type and its message,
with `status="error"` — and the model reads it and tries something else:

```
Error: FileNotFoundError: /inputs/x.csv
```

Write the exception for that reader. Measured on one deployment before this
existed: the same wrong path cost nothing through `read_file` and killed a
sixteen-call run through `csv_profile`, because a workspace tool's exception was
the one kind nothing converted. Which of the two happened depended on the tool
the model reached for, which a deployment cannot predict.

Raise `ToolException` when the message is already advice for the model: it goes
back as you wrote it, without its type.

## Folders, when one file stops being enough

`tools/` may be as deep as you like, and `__init__.py` decides what a folder is:

```
tools/
├── http_fetch.py                  a module, as always
├── research/
│   ├── find_company.py            organisation — each file independent
│   └── legal/filings/lookup.py    as deep as you want
└── csv_profile/
    ├── __init__.py                a package — one unit
    ├── profile.py                 the tools
    └── columns.py                 a helper, and an ordinary module
```

**A folder without `__init__.py` is organisation.** Each file is loaded on its
own and declares its own `TOOLS`, exactly as a flat one does.

**A folder with `__init__.py` is a package.** It is imported whole, declares
`TOOLS` once, and nothing inside it is scanned separately — so its modules
import from each other normally and a helper is a helper because it is not
exported, not because it is spelled `_columns.py`. Reach for this the moment a
tool grows a second file.

Two things follow, and both are the point:

- **A folder reaches a name only when it has to.** A tool is named by itself,
  so `research/find_company.py` offers `find_company` and a request grants it by
  that name. Moving a file between folders changes nothing a caller types.
  `--list` says where each one came from.

  The exception is two files defining one name — see below. Then the bare name
  is refused and the file is what tells them apart.
- **Skills go one folder deep, not many.** That rule is in
  [`formats.md`](formats.md#skills--skillsnameskillmd-or-one-folder-deep) with the
  rest of what a definition says — the difference is real and worth knowing
  before you try.

## A module with no `TOOLS` is an error

Not a skipped file. Quietly offering fewer tools than the workspace defines is
the failure `CapabilityError` exists to prevent one layer down — a request names
a tool, the name is not there, and nothing says the file that should have
supplied it was read and ignored:

```
research/find_company.py: must define TOOLS, the tools it contributes
```

Files whose names begin with `_` are helpers and are never modules of the
catalogue, which is how a loose file keeps something private without needing a
folder — mostly a way to park one you have not finished. Inside a package you
do not need it, and `SUBAGENTS` works the same way for a Python-declared
subagent.

## Two files may define one name

Vendors do not coordinate. Two folders may each define a `fetch` and both load;
refusing the pair would stop the deployment over a clash unfixable by anyone who
owned neither file.

The bare name is then refused wherever it is granted, and a definition names
which file's it means — `vendor_a/fetch.py::fetch`. That half is
[`formats.md`](formats.md#two-tools-with-the-same-name). Renaming one of the two
files is still the better answer for anyone who controls both.

## What the loader refuses

All for the same reason: an agent quietly holding different tools than the
workspace defines is worse than a run that stops.

| Refused | Why |
| --- | --- |
| A module with no `TOOLS` | Scanning for callables would guess at intent |
| An entry that is not a tool | `TOOLS = ["line_count"]` — the tool's *name* where the tool goes. Unrefused it reaches the model as `'line_count'`, quotes and all, and the build dies later naming no file |
| A class where an instance was meant | `TOOLS = [Shout]` for `[Shout()]`. See [the note above](#or-a-class-when-you-want-to-declare-the-schema) |
| A `ToolSpec` name or description that did not take | The tool computes its own, so the model would never be shown yours. See [above](#another-name-or-another-description) |
| A module that will not import | Skipping it gives the agent silently fewer tools |
| Two modules claiming one tool name | `tools_by_name` is a dict; the later would win in silence. Checked across folders, so two people cannot each add a `find_company` |
| A tool named like a built-in | Same, except the thing that vanishes is `read_file` |
| A relative import in a loose file | It has no parent package and never will. The error says to make the folder a package |

Hidden directories and `__pycache__` are not descended into. A virtualenv left
under `tools/` would otherwise be imported, and this directory is imported
rather than read.

## A file the tool reads is an argument called `path`

The model knows files by the paths the file tools take — `/inputs/report.csv`,
`/outputs/summary.md` — and never by where they sit on the host. Name the
argument `path` and the tool is handed the real file, resolved where the session's
backend keeps it: `/inputs/report.csv` and `/skills/report/template.md` are the files
`read_file` would read. Say in the docstring that it is the same virtual path the
file tools take, because the docstring is what the model reads.

Three things are refused, each as a failed result the model can read:

- **A path the turn may not read**, as `read_file` refuses it. `/.harness` is never
  handed to a tool — a tool runs in kingfisher's own process, outside every sandbox,
  and could otherwise read or rewrite the agent the session is pinned to.
- **A path that resolves out of where it is kept**, through `..` or a link the
  agent made inside the session.
- **A path the backend does not keep on this host.** A backend running somewhere
  else has no real file to hand over; a tool taking
  `runtime: ToolRuntime[ToolContext]`, below, works on any backend. A backend that
  is not kingfisher's can still keep its files here — a directory of its own, a
  network mount — and say so with `host_path`; [`ports.md`](ports.md) is its side.

**Only `path` is translated.** An argument with any other name reaches the tool as
written, so a tool calling its file `input_file` is handed `/inputs/report.csv`
literally and does not find it. One kind of string is refused in every argument: a
host path — under `/Users/`, `/home/`, `/tmp/`, `/proc/` and the other roots a
file tool refuses, or under the directory every session is in --
comes back to the model as a failed tool result instead of reaching the tool.

That refusal is not a boundary, and the section after next is why. A tool is
ordinary Python, and one that opens whatever string it is handed can still be
pointed somewhere by a relative path, or by text that does not look like a path at
all.

## Or the tool is handed the session's filesystem

`path` gives a tool one real file, and only where the backend keeps it on this
host. A tool that wants more — to list a folder, search it, write its result
beside its input, or to work on any backend at all — asks for the filesystem the
file tools themselves use, and works in the paths the model already writes. Every
shipped tool is written this way, because the examples are what gets copied:

```python
from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def first_line(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Return the first line of a text file. `file_path` is the same virtual
    path the file tools take, such as `/inputs/report.csv`."""
    found = runtime.context.backend.read(file_path)
    if found.error:
        raise OSError(found.error)
    return found.file_data["content"].splitlines()[0]


TOOLS = [first_line]
```

`runtime` is not an argument the model sees or sends. langgraph fills it in and
leaves it out of the schema.

**Write it as a function**, decorated or not. langgraph reads which arguments to
fill off the tool's schema, so a class that declares its own `args_schema` — the
reason to write a class at all — is called without `runtime` and fails on
`_run() missing 1 required positional argument: 'runtime'`. Naming it in the
schema is no way out: pydantic cannot build a model holding one, and the file
stops importing.

`runtime.context.backend` is a deepagents `BackendProtocol`: `ls`, `read`, `grep`,
`glob`, `write`, `edit`, `delete`, `upload_files` and `download_files`, and each
again with an `a` in front for an `async` tool. It is this turn's backend — the
one the deployment named, rooted at this session — so `/inputs/report.csv` is the
file `read_file` would read, and nothing in the tool knows where that is on the
host.

**It keeps the rules the file tools keep**, and that is the reason it is a wrapper
rather than the backend itself. deepagents applies a turn's permissions inside
`read_file` and `write_file`, not in the backend under them, so a tool handed the
backend bare would write into `/skills` — the catalogue every session shares — and
read the conversation under `/.harness`, for a model that was refused both. Through the
wrapper the same paths are refused the same way:

| Asked for | Comes back as |
| --- | --- |
| a path the turn may not read or write | the result's `error` — `permission denied for read on /.harness/transcript.jsonl` |
| a path that climbs out with `..` | the result's `error`, the same way |
| `ls`, `glob` or `grep` over a folder | the entries that may be read, and no sign of the rest |
| `delete` on a folder | refused if anything under it may not be written, not only the folder |
| `upload_files` or `download_files` | one response per path, in the order asked; a refused one carries `error="permission_denied"` and the others still go through |
| a path a rule would stop to ask a person about | refused — nothing can pause for an answer inside a tool call |

**A refused path is on the result, not raised.** That is `BackendProtocol`'s
convention rather than kingfisher's, and it means a tool that ignores `.error`
carries on with `file_data` as `None`. Check it and raise, as above: the
exception reaches the model as a failed tool result. The one refusal that arrives
raised is the default backend's own, for a host path — and that reaches the model
as a failed result too, with the virtual path to use instead.

**There is no `execute`.** No permission rule reaches the shell, so a tool holding
it would be the way round every row of that table — and would run commands for a
request that was refused the shell.

**Do not call the argument `path`.** The two ways of handing a tool a file do not
mix. `path` is rewritten to the real file before the tool runs, the backend takes
the virtual path, and handed the rewritten one it refuses it as a host path. Any
other name — `file_path`, `source` — reaches the tool as the model wrote it.

**Write the annotation out as `ToolRuntime[ToolContext]`.** A bare `ToolRuntime`
runs, and declares a context of `None` — so pydantic warns on every call that it
was handed something else.

Two edges. A subagent's tools are handed the same backend as the agent that
started it. And `runtime.context` is `None` when the deployment passed `graph=` to
`Kingfisher`: a graph kingfisher did not build is driven the way its builder
left it, so a tool written this way needs a graph kingfisher assembled.

## A tool that creates a file

A tool whose purpose is a file — a chart, a generated image, an export — saves the
file and returns where it went, never the file itself. Returned, a chart is its whole
markup in the model's context and nothing the caller can open; saved, it is one line.
A result that is only long is a different case and not the tool's to manage:
deepagents already saves one over about 20,000 tokens under
`/scratchpad/large_tool_results/` and hands the model a stub naming it.

There is no kingfisher helper for this. The tool saves through the backend above, in
about ten lines, and keeps to one convention so that every such tool answers alike:

- **An optional `save_as` argument.** Left out, the file goes to
  `/outputs/<a name the tool chooses>`, which is what the caller is handed back when
  the turn ends. Given, it is a full virtual path, such as `/scratchpad/try1.svg` for
  a draft. It may name anywhere the turn may write, and the session's rules refuse
  the rest — `/inputs/`, `/skills/`, `/.harness/`. Say so in the docstring, in a
  sentence: the model learns the argument nowhere else. A file that is only the
  agent's working material, which the caller did not ask for, goes to
  `/scratchpad/` instead, as `pdf_text`'s and `pdf_pages`' do.
- **Not `path`.** That name is [a file the tool reads](#a-file-the-tool-reads-is-an-argument-called-path),
  rewritten to a host path before the tool runs.
- **Bytes, through `upload_files`**, with text encoded as UTF-8 first.
- **The response's `error`, checked in the code.** A refused path is not raised. It
  comes back on the response as `permission_denied` — `invalid_path` for one that
  climbs out with `..`, `is_directory` for a folder — and nothing is written. A tool
  that skips the check tells the model it saved a file that does not exist, and
  nothing anywhere fails. Raise, as [a failing tool does](#when-a-tool-fails).
- **A file already there is replaced without a word.** The agent chose the path.
- **One line per file:** `Saved <name> (<type>, <size>) to <path>`, the size as a
  person reads one, and after a dash a short note the tool writes itself when there
  is something worth saying. A string, as [every result should be](#what-a-tool-returns).

```python
import csv
import io
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def save_table(
    columns: list[str],
    rows: list[list[str]],
    runtime: ToolRuntime[ToolContext],
    save_as: str = "/outputs/table.csv",
) -> str:
    """Save a table as a CSV file, quoted properly. Use to hand over a table you
    have built. `save_as` is where it goes, a full virtual path such as
    `/scratchpad/draft.csv`; leave it out for `/outputs/table.csv`."""
    out = io.StringIO()
    csv.writer(out).writerows([columns, *rows])
    data = out.getvalue().encode("utf-8")

    (sent,) = runtime.context.backend.upload_files([(save_as, data)])
    if sent.error:
        raise OSError(f"not saved to {save_as}: {sent.error}")

    name = PurePosixPath(save_as).name
    return f"Saved {name} (CSV, {_size(len(data))}) to {save_as} — {len(rows)} rows"


def _size(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / 1024 / 1024:.1f} MB"


TOOLS = [save_table]
```

The model reads `Saved table.csv (CSV, 44 B) to /outputs/table.csv — 3 rows`, or,
pointed at `/inputs/`, a failed result:

```
Error: OSError: not saved to /inputs/table.csv: permission_denied
```

The shipped `bar_chart`, `csv_filter`, `pdf_text` and `pdf_pages` are the same recipe
on a file the tool reads first.

**Several files are one call.** `upload_files` takes a list of pairs and answers one
response per pair, in order, and a refused one does not stop the rest — so check
every response, and let the failure say which files did land:

```python
sent = runtime.context.backend.upload_files([(chart_at, svg), (table_at, table)])
failed = [f"{one.path}: {one.error}" for one in sent if one.error]
if failed:
    landed = ", ".join(one.path for one in sent if not one.error) or "nothing"
    raise OSError(f"not saved: {'; '.join(failed)}; saved: {landed}")
```

On success, return one `Saved …` line per file, joined with newlines.

**An image is a path too.** [The table above](#what-a-tool-returns) lets a tool return
image blocks, and one that makes an image should not: a block stays in the
conversation and is sent again with every model call after it, where a path costs a
line. The agent opens the file with `read_file` when it wants to look — a `.png`,
`.jpg`, `.gif` or `.webp` comes back as an image to a model that takes one, and an SVG
as its markup.

## A tool is code, and it runs in the kingfisher process

Not in the agent's sandbox, and not under the filesystem permissions. The backend
above meets those permissions because the wrapper applies them; the tool's own
`open()` meets nothing. `tools/` is deliberately *not* a backend route, so no file
tool can reach it; the only agent that could write one is an agent already holding
`execute`, which can run anything on the host regardless. Treat this directory the
way you treat the rest of your source: it is yours, not the agent's.

## A tool that reads a file

Say how much it will read, and refuse more. Every shipped tool that reads one does,
through the same `_fetched` helper and the same `MAX_INPUT_BYTES` beside it, and a file
past the cap comes back as a failed call naming both sizes -- which the model can act
on, by asking for a smaller file or a part of this one.

Three things add up to needing that bound, and none of them is obvious on its own:

  - **A backend hands a file back whole.** `download_files` is the interface; a backend
    keeping the session elsewhere has no local file to stream from, so the bytes are in
    this process before your tool sees them.
  - **The parse is on the turn's thread**, in the kingfisher process, outside every
    fence the shell has -- the section above says why. Nothing is watching it.
  - **The turn deadline cannot stop it.** `KINGFISHER_TURN_TIMEOUT_S` is read *between*
    stream chunks, so a tool that spends ten minutes inside one call spends them; the
    deadline is noticed when it returns. There is no timeout to reach for, and a thread
    cannot be interrupted mid-parse in Python, so the only bound available is the one
    the tool sets before it starts.

**Bytes are not always the cost.** For a CSV they bound it and do not predict it. 32 MB
of rows peaked at 358 MB of resident memory and 188 MB at 1.8 GB, about nine times the
file -- for *that* row shape, which is the part this said as though it held generally.
The ratio is to the *cells* the sample holds, not to the bytes: the same 32 MB spread
over 4,000 columns peaks at **998 MB**, thirty times the file, measured through
`csv_profile`. The arithmetic is the whole explanation -- 4,000 columns over 4,191 rows
is 16.8 million Python strings, and a `str` costs about 50 bytes before its first
character, so a one-character cell is fifty times its own size in the dictionary that
gathers it. A cap in bytes is worth having, since a gigabyte is survivable where
unbounded is not; what it is not is a prediction of what the tool will spend. For a PDF
the bytes do not even bound it: 1.6 MB of dense pages peaked at 1,655 MB, a thousand times the file, because the
cost is per *page* and the library held every page it had read. `pdf_text` caps pages for
that reason, and releases each page's caches as it goes -- `flush_cache()` and
`get_textmap.cache_clear()`, both, which took the same work to 93 MB. So cap the bytes,
and then measure the thing your tool actually spends.

**And the input cap bounds the answer only if the answer is a fixed size.** An answer
of one line per thing the file names grows with the file by construction, and the input
cap is the wrong order of magnitude to hold it: `csv_profile` writes a line per column,
and a 10 MB header of 5,242,880 columns answered **334,433,239 characters** -- out of a
tool whose docstring said the answer does not grow with the file -- while `csv_columns`
answered the header back, 10,485,759 characters of it. A shape a deployment really
holds costs too: 4,000 columns over 1,250 rows answered 218,913, some 55,000 tokens for
one call asking what shape a file is. So cap what the answer has a line per, and say
the total beside the number kept. That cap is also the only bound on the gathering --
the 1,833 MB those columns peaked at is five million lists and the strings naming them,
and capping the columns took the same file to 185 MB, where the cap in bytes had let
all of it through. What is left unbounded by anything but the bytes is the header row
itself, which `csv.reader` hands back whole.

**One name can be the whole answer, too.** A header cell is uploaded text, and the CSV
reader parses one up to 131,072 characters: 76 columns named that long answered
9,959,656 characters. Cut it, with the count of what was cut in its place -- unlike a
query's row that will not fit, nothing a reader can ask for makes a column's name
shorter. Cut it before anything is appended to the name, as well: the number that tells
two columns sharing a name apart went on the end, and a cut taken after that took the
number off. And count which names repeat *after* the cut, because two names that differ
only past it are one name in the answer, so the lines that told them apart stopped.

## A tool that runs a query

The section above ends in the right place for a file and the wrong one for a database,
because there is no file to cap. What a query spends is time inside the engine and the
width of what it hands back, and a row count bounds neither. `sql_query` holds both,
and the numbers are the argument:

  - **Rows are not characters.** Its `MAX_ROWS` is 200, and two hundred rows of one
    20 MB value obeyed it: a 4,000,000,200-character answer, 30 seconds, 3.3 GB of
    resident memory -- from a 20 MB database, and then the model's whole context. So
    the second cap is on what the answer carries, read per value *before* the
    conversion, because `str()` on that value is where the memory goes. A row at a
    time rather than `fetchmany`, for the same reason: it has every row in this
    process before anything could measure one.
  - **And a cap on characters is a cap at the granularity you check it.** Asked after
    the row was written, it bounded the answer to the width of one row: 20 columns of
    100,000 characters each passed the per-value check and answered 2,000,225
    characters, ten times the cap, under a note saying it had stopped at it. At 2,000
    columns the same query answers 400,011,025. So the room left is measured *before*
    the row is built -- from the values' own lengths, which only quoting can add to --
    and a row that cannot fit at all is refused with its width rather than cut, because
    a reader can ask for the same row narrower. The header is measured too: it is
    written before any row, and `AS "<150,000 characters>"` is a column name.
  - **A note that asserts the bound is worse than no note.** Both of `sql_query`'s say
    the number they stopped at, so the one case nothing else catches is the note itself
    being wrong -- it said `truncated at 200000 characters` on an answer of two million,
    and it counted the row that passed the cap among the rows it had kept.
  - **Time is the cost nothing else bounds.** A three-way join over a 1,000-row table
    takes 49 seconds, and a fourth table is a thousand times that -- about 14 hours
    inside one call, which the turn deadline notices when it returns.

A database is also the exception to what the section above says about threads. A parse
in Python cannot be interrupted, but SQLite calls `set_progress_handler` from inside
its own loop, so a statement already running *can* be stopped: returning non-zero
aborts it with `OperationalError: interrupted`. Measured at 100, 1,000, 10,000 and
100,000 instructions between checks, a million-row join took 0.04 seconds every time --
and 0.04 with no handler at all, so the bound is free. Nothing in that exception says
who asked for it, which is why the handler records that it was the one, and the tool
answers in words naming the bound and what to do instead.

**And a library that offers you a bound may be bounding something else.** `http_fetch`
passes `timeout=` to `urlopen`, which reads as a bound on the call and is a bound on one
socket operation. Measured: a server sending one byte every two seconds was still being
read after ninety, because every byte reset the clock, and the party choosing how slowly
to answer is whoever the URL points at. What was missing is a deadline over the whole
call, checked while the body arrives -- and checking it needs `read1` rather than
`read`, because `read` blocks until it has every byte asked for, so a deadline between
`read` calls never runs. Reading in pieces with `read` measured as no bound at all,
which is the kind of fix that looks right and is not. So two numbers: how long to wait
for the next byte, and how long the whole call may take. The first is also why the
second is not enough on its own -- a connection that answers nothing is the socket
timeout's to end.

**A cut with nothing said is a page the caller reads as whole.** The same tool returned
`body[:MAX_CHARS]`, so 200,000 bytes and 20,000 bytes came back as the same 20,000
characters with nothing in either to tell them apart -- the failure the notes above
exist to prevent, in a tool that had the cap and not the note. Three things it took to
say it honestly. The read goes one byte past its byte cap, because at the cap exactly a
page that ends there and a page that goes on for a megabyte have arrived identically.
The note names whichever bound bit: the byte cap is four bytes a character, UTF-8's
longest, but `iso-2022-jp` shifts into each run and back out and spends nine bytes on
two characters, so 180,000 bytes of it answered 17,779 -- under the character cap, cut
by the byte cap, and a note reading `cut at 20000 characters` would have named a number
that answer never reached. And the note goes *inside* the cap rather than after it,
since the docstring promises at most 20,000 characters and the model plans the call on
that number. A byte cap has one more effect worth knowing: it stops mid-character, and
`errors="replace"` then writes a character the page never sent, which a caller reads as
the page's own corruption.

**And a cap on an answer is not a check on what it holds.** The same tool spent 20,000
characters on a PNG -- 12,283 of them the replacement character, most of the rest
control codes -- which is half of what one call may spend, on nothing a reader can use,
with nothing in the answer to say the body was never text. The check that catches it is
on what arrived rather than on what the server called it: text served as
`application/octet-stream` measured 0.0% unreadable and is worth answering, while a
page declaring `text/plain; charset=utf-8` whose bytes are really UTF-16 measured 55.2%
and is not. So the content type is named in the refusal and does not make it -- and it
is named only if it looks like a type, a header being the server's own writing and a
refusal being one line. What counts as unreadable has to be the replacement character
*and* the control codes that are not whitespace: a PNG is 49.9% replacement, and those
UTF-16 bytes are almost all NUL with no replacement past the first two. The share it
refuses at is a quarter, which is measured rather than round -- the most text-like
binary there is, gzipped text, came to 50.0%, and the least readable thing that is
still text, French written in Latin-1 and declared UTF-8, to 10.3%.

**A pattern over text you did not write is a cost too, and it shows up in neither
bytes nor rows.** `mask_secrets` matched addresses with `[A-Za-z0-9._%+-]+@…`, and `.`,
`-`, `+` and `%` sit in that class while being word characters to nobody -- so each one
starts a fresh `\b` the class can still consume through. On a run with no `@`, every
start position consumed to the end of the run and backtracked the whole way: measured,
one line of 128 KB took 7.25 seconds and 1 MB took 472, clean n², which is about five
and a half days at the 32 MB that tool admits. Its input is file content, and it is the
one shipped tool written for content nobody trusts.

Bound every repetition in a pattern, and take the bound from whatever defines the thing
you are matching rather than picking one -- RFC 5321 gives 64 octets for an address's
local part and 255 for its domain, which made that pattern linear and 440 times faster
at 64 KB with the same matches. Possessive quantifiers look like the answer and are not:
`[A-Za-z0-9.-]++\.` cannot give back the dot the pattern then needs, so it matches
nothing at all, and the cost stays n² regardless, because what repeats is the number of
positions a match is attempted from.

**Cap what the answer lists *and* what the walk holds — they are two numbers.**
`log_levels` caps the minutes it prints at thirty, and held one counter per distinct
minute while it worked: measured, a 19 MB file of 883,011 distinct minutes peaked at
506 MB of resident memory against 199 MB for the same bytes in a single minute, about
350 bytes a key, and the 32 MB it admits reaches a million and a half. `status_codes`
beside it needs no such cap and that is the comparison worth having — it keys on a
three-digit status, so its accumulator cannot exceed a thousand entries however long
the file. The bytes do not tell you which of those two you wrote.

**And the library you parse with has limits of its own.** `csv.field_size_limit()` is
131,072, so four tools that read 32 MB died on a single cell 0.6% of their own cap --
and the model was handed `Error: Error: field larger than field limit (131072)`, naming
no file, no column and no remedy. Catch it and say the three; widening the limit is
process-wide state a tool has no business setting.

Whatever your tool talks to, the shape is the same: find what it actually spends, and
then ask whether the library it spends it in gives you a way to stop.

## Where the directory lives

It is `tools/` in `KINGFISHER_DEFINITIONS_DIR`, beside every other kind, so a
catalogue of tools can be deployed once and shared by every workspace.
