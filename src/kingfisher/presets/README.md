# Presets

One working definition of each thing a request can activate — a skill, a
subagent, a tool — for you to copy and edit. Nothing here is loaded
automatically: a preset does nothing until it is in a workspace.

    uv run main.py --seed-presets     # all of them, into $KINGFISHER_WORKSPACE

They ship *inside* the package, so that works from an installed kingfisher and
not only from a checkout. This is not the package growing domain content: a
preset demonstrates a **format** and is rewritten on first contact with a real
task, where domain content would presume what your project is about. Kingfisher
ships no skills of its own for the same reason its base prompt carries no
domain instructions — a general agent should read the same whatever the project
is.

To take one rather than all of them, copy it:

    cp -r "$(python -c 'import kingfisher.presets as p; print(p.__path__[0])')/skills/code-review" \
          "$KINGFISHER_WORKSPACE/skills/"

A request then names them:

```python
from kingfisher import Capabilities, Request, run

run(Request(
    task="Review the diff in /data/change.patch",
    capabilities=Capabilities(
        tools=("read_file", "ls", "glob", "grep", "execute", "task"),
        skills=("code-review",),
        subagents=("reviewer",),
    ),
))
```

Leaving a field unset means *no opinion* — everything the workspace offers.
Passing an empty tuple means *none*. Naming something that does not exist raises
`CapabilityError` at build time rather than running with quietly less than you
asked for.

---

## Tools — `/tools/<module>.py`

Two kinds. The **built-in set** comes with the agent and you select from it by
name. **Workspace tools** are Python you write, imported from the workspace's
`tools/` directory and added to that set.

### The built-in set

As of deepagents 0.7.6:

| Tool | What it does |
| --- | --- |
| `read_file` | Read a file, optionally a line range |
| `write_file` | Create or overwrite a file |
| `edit_file` | Replace an exact string in an existing file |
| `delete` | Delete a file |
| `ls` | List a directory |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `execute` | Run a shell command |
| `task` | Delegate to a subagent |
| `write_todos` | Track a multi-step plan |

Three things worth knowing before you restrict this list:

- **`read_file` is close to mandatory.** The filesystem tooling assumes it. An
  allowlist without it produces an agent that cannot see its own workspace.
- **Omitting `task` disables subagents** however many you activate — `task` is
  how delegation happens.
- **`execute` is the one that matters for isolation.** It bypasses the
  filesystem permission layer entirely, so a request that activates the shell
  can reach anything the process can, including skills it did not activate. Deny
  rules are a real boundary only for requests without it.

### Workspace tools

One `.py` file per module in `$KINGFISHER_WORKSPACE/tools/`, each defining
`TOOLS` — the list of tools it contributes. Nothing is inferred: a helper in the
same file stays a helper. Modules starting with `_` are skipped, so a tool can
be split across files.

- [`http_fetch.py`](tools/http_fetch.py) — **something the built-in set cannot
  do at all.** The clearest reason to write one.
- [`sql_query.py`](tools/sql_query.py) — **making an existing capability
  narrower.** `execute` could already reach the database, but it could reach
  everything else too. A tool states the reach in code, so a request can
  activate `sql_query` and *not* the shell.

The docstring is not decoration — it is what the model reads when deciding
whether to call the tool, exactly like a skill's `description`. Write it as a
trigger condition and say what the arguments mean in a caller's words.

Four things the loader will refuse, all for the same reason: an agent quietly
holding different tools than the workspace defines is worse than a run that
stops.

| Refused | Why |
| --- | --- |
| A module with no `TOOLS` | Scanning for callables would guess at intent |
| A module that will not import | Skipping it gives the agent silently fewer tools |
| Two modules claiming one tool name | `tools_by_name` is a dict; the later would win in silence |
| A tool named like a built-in | Same, except the thing that vanishes is `read_file` |

**A tool is code, and it runs in the kingfisher process** — not in the agent's
sandbox, and not under the filesystem permissions. `tools/` is deliberately
*not* a backend route, so no file tool can reach it; the only agent that could
write one is an agent already holding `execute`, which can run anything on the
host regardless. Treat this directory the way you treat the rest of your
source: it is yours, not the agent's.

`KINGFISHER_TOOLS_DIR` relocates it, the way `KINGFISHER_SKILLS_DIR` does, so a
catalogue of tools can be deployed once and shared by every workspace.

---

## Subagents — `/subagents/<name>.yaml`

A YAML document. It was markdown with a YAML header until the header had
grown into everything but the prompt, at which point the body was one field
pretending to be a format.

| Field | | |
| --- | --- | --- |
| `name` | required | What a request activates it by. Authoritative — the filename is not |
| `description` | required | Single line. This is what the parent agent sees when deciding whether to delegate, so write it as a trigger, not a title |
| `system_prompt` | required | The delegate's whole instruction, as a `\|2` block scalar. The `2` pins where the block starts: without it YAML infers the column from the first line, so a prompt opening with an indented example fails to load. Indentation inside the prompt is kept; the outer edges are trimmed |
| `tools` | optional | `[read_file, grep]` or a block list. Unset inherits the parent's tools |
| `skills` | optional | Which procedures it is told about. Unset grants **none** — the opposite of `tools`, because its body is already its procedure |
| `middleware` | optional | Names entries from a registry the deployment supplies. The one field that selects *code*, so it is granted, never inherited |
| `provider` | optional | Which endpoint it runs against, by style. Moves together with `model` |
| `model` | optional | Must be a model your gateway serves. This is where per-role cost routing goes |

Two reasons to reach for one, one example each:

- [`reviewer.md`](subagents/reviewer.md) — **independence.** A second agent that
  recomputes a claim without seeing how the first one got there catches errors
  that re-reading your own work does not.
- [`extractor.md`](subagents/extractor.md) — **context isolation.** It reads a
  large pile of files and returns a short answer; the bulk stays in its context
  rather than yours. Note the narrower `tools` and the cheaper `model`.

### What loads, and what does not

```yaml
system_prompt: |2          # ✅ the ordinary case
  You verify claims.
  Be terse.

system_prompt: |2          # ✅ indentation inside the prompt is kept
  1. Recompute.
     Do not reuse their script.

system_prompt: |2          # ✅ a prompt opening with an indented example
      ls -la /data
  Then report.

system_prompt: |           # ❌ without the 2, YAML takes the margin from the
      ls -la /data         #    first line and the next one is left of it
  Then report.

system_prompt: |2          # ❌ nothing is indented, so nothing is in the block
You verify claims.

system_prompt: |2          # ❌ 'system_prompt' is present but empty

system_prompt: >          # ❌ refused: `>` joins consecutive lines, so
  1. Recompute.           #    "1. Recompute. 2. Say which definition"
  2. Say which definition. #   reaches the delegate as one line

system_prompt: Recompute. # ❌ refused: same damage, without a marker to notice
```

`>` is refused for the prompt and allowed everywhere else — a `description`
*is* one paragraph, and `>-` is how anyone writes one longer than a line. A
prompt is structured text, and folding destroys exactly the structure while
leaving the file valid and the definition looking correct.

`tools`, `skills` and `middleware` take either form:

```yaml
tools: [read_file, grep]   # ✅
tools:                     # ✅ the same thing
  - read_file
  - grep
```

An unlisted field is refused rather than ignored — `tolls:` is answered with
*did you mean 'tools'?*, and `permissions:` with the reason this format declines
it.

The frontmatter is real YAML, parsed with `yaml.safe_load` — block lists,
folded scalars and typed values all work, and a skill and a subagent are read
by the same parser so the two formats cannot drift.

**A field not in that table is an error, not a field that gets ignored.**
Ignoring one is indistinguishable from honouring it: `tolls:` used to give a
delegate *every* tool its parent had, because unset `tools` means inherit. A
near miss is named (`did you mean 'tools'?`), and the fields deepagents knows
but this format declines — `permissions`, `subagents`, `interrupt_on`,
`response_format` — each say why, because "unknown field" reads as an
omission worth working around when the answer is that honouring it would be
wrong.

Skills are the opposite and deliberately so: kingfisher does not own that
format, so an unrecognised key there is left alone.

---

## Skills — `/skills/<name>/SKILL.md`

deepagents' format, unchanged. A directory per skill, `SKILL.md` with `name` and
`description` in frontmatter and the procedure in the body.

The mechanism is progressive disclosure: **only the name and description are in
context by default.** The body is read when the agent decides the skill applies.
So the description does the work — it is a trigger condition, not a summary. Say
when to reach for this, in the words a task would use.

- [`code-review/`](skills/code-review/) — single file. The common shape
- [`release-notes/`](skills/release-notes/) — a `reference/` file the body
  points to, read on demand. Use this shape when the detail is long and most
  tasks will not need it: `SKILL.md` stays short enough that reading it is cheap

A skill the agent declines to read is not a failure. If the task did not warrant
it, not loading it is the mechanism working.
