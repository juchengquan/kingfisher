# Writing a workspace tool

A `.py` file in `$KINGFISHER_WORKSPACE/tools/` defining `TOOLS` — the list of
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

Nothing wraps it. A kingfisher-flavoured decorator would be a second name for
someone else's rule, and the loader accepts what LangChain accepts — a `BaseTool`
from `@tool`, an instantiated `BaseTool` subclass, or a plain function.

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
def line_count(path: str) -> str:
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

**Note the `()`.** `TOOLS` holds tools, not classes, and the class is the one
mistake here that used to produce a *successful* wrong answer: it loaded, was
offered to the model under the class name `Shout` rather than the `shout` it
declares — on a pydantic model that field is not a class attribute — and calling
it built a new instance and handed that back as the result. It is refused now,
naming what to write:

```
shout.py: TOOLS names the class 'Shout' rather than a tool -- write Shout() to
build one. A class loads and is offered to the model, and calling it returns a
new instance as if it were an answer
```

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
| anything JSON will not take | its `repr` |

Nothing fails in any of those rows, and that is the point. A tool returning the
wrong shape does not raise; it tells the model something unhelpful, in a
transcript nobody reads until the answer is already wrong. The first row is the
only one where you decide what it says.

Two shapes escape the coercion: a `ToolMessage` or a langgraph `Command` is
returned as-is and never wrapped, so a tool can write its own result or update
graph state. The door is open and worth knowing about, but it is LangChain's
door rather than one kingfisher holds. A run event takes its tool name from the
message, so a `Command` writing its own leaves the log saying a tool ran without
saying which — and this harness puts the file tools on a real filesystem, so a
`files` update in graph state reaches nothing that reads it.
[`decisions.md`](../decisions.md) says why that is documented rather than
refused, and [`findings.md`](../findings.md) has the measurement it rests on.

A two-tuple is content-and-artifact only if you asked for it with
`@tool(response_format="content_and_artifact")`, which then *requires* the pair
and raises naming the type it got instead. Without that declaration a tuple is
an ordinary value and arrives JSON-encoded like the rest.

## When a tool fails

Raise. The exception becomes a failed tool result — its type and its message,
with `status="error"` — and the model reads it and tries something else:

```
Error: FileNotFoundError: /data/x.csv
```

Write the exception for that reader. Measured on one deployment before this
existed: the same wrong path cost nothing through `read_file` and killed a
sixteen-call run through `csv_profile`, because a workspace tool's exception was
the one kind nothing converted. Which of the two happened depended on the tool
the model reached for, which a deployment cannot predict.

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

Vendors do not coordinate. Two folders may each define a `fetch`, and both load
— the catalogue used to refuse the pair, which stopped the deployment over a
clash and was unfixable by anyone who owned neither file.

The bare name is then refused wherever it is granted, and a definition names
which file's it means — `vendor_a/fetch.py::fetch`. That half is
[`formats.md`](formats.md#two-tools-with-the-same-name). Renaming one of the two
files is still the better answer for anyone who controls both; this is for when
nobody does.

## What the loader refuses

Seven things, all for the same reason: an agent quietly holding different tools
than the workspace defines is worse than a run that stops.

| Refused | Why |
| --- | --- |
| A module with no `TOOLS` | Scanning for callables would guess at intent |
| An entry that is not a tool | `TOOLS = ["line_count"]` — the *name* of the tool, where the tool goes, by analogy with every other format here, which is data. It was offered to the model under the name `'line_count'`, quotes and all, and the build died later naming no file |
| A class where an instance was meant | `TOOLS = [Shout]` for `[Shout()]`. See [the note above](#or-a-class-when-you-want-to-declare-the-schema) — the one mistake here that produced a *successful* wrong answer |
| A module that will not import | Skipping it gives the agent silently fewer tools |
| Two modules claiming one tool name | `tools_by_name` is a dict; the later would win in silence. Checked across folders, so two people cannot each add a `find_company` |
| A tool named like a built-in | Same, except the thing that vanishes is `read_file` |
| A relative import in a loose file | It has no parent package and never will. The error says to make the folder a package |

Hidden directories and `__pycache__` are not descended into. A virtualenv left
under `tools/` would otherwise be imported, and this directory is imported
rather than read.

## A tool is code, and it runs in the kingfisher process

Not in the agent's sandbox, and not under the filesystem permissions. `tools/`
is deliberately *not* a backend route, so no file tool can reach it; the only
agent that could write one is an agent already holding `execute`, which can run
anything on the host regardless. Treat this directory the way you treat the rest
of your source: it is yours, not the agent's.

## Where the directory lives

`KINGFISHER_TOOLS_DIR` relocates it, the way `KINGFISHER_SKILLS_DIR` does, so a
catalogue of tools can be deployed once and shared by every workspace.
