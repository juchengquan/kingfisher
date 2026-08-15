# Examples

Copyable definitions for the three things a request can activate. Nothing here
is loaded by kingfisher — the package ships no domain content on purpose, so
these live in the repo and you copy what you want into your workspace:

    cp -r examples/subagents/reviewer.md   "$KINGFISHER_WORKSPACE/subagents/"
    cp -r examples/skills/code-review      "$KINGFISHER_WORKSPACE/skills/"

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

## Tools — not files

Tools are the fixed set the agent is built with; there is no markdown to write,
only names to select from. As of deepagents 0.7.6:

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

---

## Subagents — `/subagents/<name>.md`

YAML frontmatter, then a body that *is* the subagent's system prompt.

| Field | | |
| --- | --- | --- |
| `name` | required | What a request activates it by. Authoritative — the filename is not |
| `description` | required | Single line. This is what the parent agent sees when deciding whether to delegate, so write it as a trigger, not a title |
| `tools` | optional | Inline list, `[read_file, grep]`. Unset inherits the parent's tools |
| `model` | optional | Must be a model your gateway serves. This is where per-role cost routing goes |

Two reasons to reach for one, one example each:

- [`reviewer.md`](subagents/reviewer.md) — **independence.** A second agent that
  recomputes a claim without seeing how the first one got there catches errors
  that re-reading your own work does not.
- [`extractor.md`](subagents/extractor.md) — **context isolation.** It reads a
  large pile of files and returns a short answer; the bulk stays in its context
  rather than yours. Note the narrower `tools` and the cheaper `model`.

The parser is deliberately small — single-line values, inline `[a, b]` lists. It
is not full YAML, and it will tell you so rather than guessing.

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
