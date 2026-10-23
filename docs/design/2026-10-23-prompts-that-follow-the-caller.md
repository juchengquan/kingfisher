# Prompts that follow the caller

**Status:** proposed; its first slice, slots, has landed.
**Date:** 2026-10-23.
**Occasion:** a deployment needs to tell its agents three things kingfisher gives it no
place for: today's date, who the user is, and wording that depends on which sources the
caller selected. The owner settled the design below one question at a time. **Cited by
symbol, not by line.**

## How it works today

Four things reach the model besides kingfisher's own harness text, and none of them can
carry all three.

- **`Request.note`** is the caller's text for one turn. It is sent after the task in the
  turn's own message, so it is kept with that message and replayed on every later call,
  and it reaches the agent alone: a subagent hears only what the agent writes in its
  `task` call.
- **`SessionInstructions.text(cfg, session_id)`** is a plugin's text for one session,
  asked once per turn and sent by `SessionInstructionsMiddleware` after the static
  prompt's cache breakpoint, to every graph but a compiled subagent. It is never stored.
  It is handed only the session id: not the request, not the caller's source ids, not
  the datazone.
- **`Instructions`** (`INSTRUCTIONS.md` by default) is the same for every session, inside
  the cached prefix.
- **The agent's `system_prompt`** is one fixed string. `require_literal_prompt` refuses
  one not written as a `|` block, looking only at that top-level scalar.

The caller's `source_ids` and `datazone` reach `stream()` as arguments, decide through
`with_datazone` and `held_by` which tools, skills and subagents a turn may reach, and are
named in no prompt text. `ToolContext` carries the backend and the scratch runner, so a
tool that must filter by the user has only what the model passes it.

## Decided

**What the deployment wants said splits three ways, by how long it holds and who may
see it.**

| Fact | Holds for | Sent | Stored |
|---|---|---|---|
| Wording keyed on the selected sources and datazone | the grant | in the cached prompt | no |
| Today's date | the turn | after the breakpoint, every call | no |
| Who the user is | the session, read from each request | after the breakpoint, every call | no |
| A user id a tool filters by | the turn | never to the model | no |

The date is volatile rather than secret. The user's details are the model's to read and
nobody's to keep, which rules out `note`: it is stored with its message.

### Selected sources: slots in `system_prompt`

`system_prompt` takes a short form, the `|` text it is today, or a long form:

```yaml
system_prompt:
  text: |
    You answer questions from the data.

    {{figures}}

    Always cite the quarter, and say which table each figure came from.

    {{caveats}}
  slots:
    figures:
      - source_ids: [sales_db, sales_archive]
        text: |
          Revenue is booked net of returns; quarters end on the last Friday.
      - source_ids: [hr_db]
        text: |
          Headcount is in FTE, not people.
      - text: |
          Give figures as the source states them.
    caveats:
      - datazone: finance
        text: |
          Figures are in EUR; never mix in USD from the archive.
  receives: [user]
```

A caller holding `[sales_db]` in `finance` is sent the base text with the sales line in
`figures` and the EUR line in `caveats`. One holding `[audit_log]` and no datazone gets
the default in `figures`, and `caveats` disappears. The same shape as a `skills:` entry
and `middlewares:` settings: a short form, and one long form to learn.

- **The caller's grant, for the agent and every subagent.** Not what a subagent can reach
  through its own tools: data reaches a subagent as files the agent made, and a CSV the
  agent queried from `sales_db` is still net of returns when `extractor`, holding no
  database tool, reads it. Reach through a tool's lock is also not computed anywhere.
- **Matching follows the access rules.** Within an entry, a list of `source_ids` is any of
  them, as a definition's `source_ids:` line is, and `source_ids` beside `datazone` is
  both, as a `skills:` entry is with its definition. Every matching entry is used, in
  file order; the entry naming neither is the default, used only when none matched. Two
  matching entries are both true -- they are about different sources -- so taking the
  first would drop one silently.
- **An empty slot leaves nothing.** Its line and the blank lines around it collapse, as
  `system.md`'s empty markers do.
- **`text` replaces `when`.** Text added after the agent's own is a slot on its last line,
  so there is one mechanism, not two.
- **In the cached prompt.** Slot text depends only on the grant. The tool list sent ahead
  of the system prompt already varies with the grant, so for most grants this costs no
  cache entry of its own; *Not measured* has the exception.

**Refused at load:** a `{{name}}` no slot defines; a slot defined and never placed;
braces inside a slot's `text`, since slots do not nest; any `text`, at either level, not
written as a `|` block; and a slot entry keyed on a source id or datazone no lock in the
catalogue uses. A slot key is not a use, or a misspelt one would make itself legal.

`{{…}}` is reserved in an author's `system_prompt` from then on. No definition in
`assets/` uses it. There is no escape for a literal `{{` until a definition needs one.

**Why every `text` is a `|` block.** Measured with PyYAML, which `require_literal_prompt`
composes with: inside a `|` block `{{figures}}` is text wherever it stands, and quoted it
is too. Unquoted, a value *beginning* `{` is read as an inline mapping and fails to load,
`text: Note: revenue is net` fails on its `: `, and `text: Revenue #1 is net` loads as
`Revenue`, the rest taken for a comment with nothing said. The last is the reason the
rule covers one-line slot entries too.

### Today and the user: `TurnInstructions`

`SessionInstructions` becomes `TurnInstructions`, asked once per turn and handed the
turn's caller rather than its session id alone:

```python
@dataclass(frozen=True)
class Caller:
    source_ids: tuple[str, ...]
    datazone: str | None
    context: Mapping[str, str]   # the request's new context, as the caller sent it
    today: date                  # read by kingfisher when the turn starts


class TurnInstructions(Protocol):
    labels: tuple[str, ...]      # what a definition's `receives` may name

    def text(self, cfg: Config, session_id: str, caller: Caller) -> Told: ...


@dataclass(frozen=True)
class Told:
    everyone: str = ""
    labelled: Mapping[str, str] = field(default_factory=dict)
```

A deployment's:

```python
class DeskInstructions:
    labels = ("user",)

    def text(self, cfg, session_id, caller):
        return Told(
            everyone=f"Today is {caller.today:%A %-d %B %Y}, {caller.context['tz']}.",
            labelled={"user": f"You are working for {caller.context['name']}, "
                              f"{caller.context['team']}."},
        )
```

- **A request's new `context` is opaque.** A `Mapping[str, str]` kingfisher passes on and never
  reads. Kingfisher holds no model of the deployment's identities -- the reason
  `source_ids.yaml` went -- so it learns no `user_name` field. The caller sends the
  details; the hook may reshape or check them.
- **`today` is kingfisher's.** Read from the clock, so no request sets the date. A
  deployment that wants another takes it from `context` in its own hook.
- **Labels, not agent names.** Each definition lists the labelled pieces it wants in
  `system_prompt.receives`, the agent included, and gets `everyone` either way. The hook
  names no agent, so renaming a subagent cannot silently stop its text. A label the hook
  does not offer is refused at startup.
- **`general-purpose` gets `everyone`** and the house rules and session section it gets
  today, having no definition to say `receives`. A compiled subagent gets none of it, as
  with every other channel.
- **Never a slot.** A `receives` label cannot key a slot: per-turn text in the cached
  prompt would miss the cache on every turn.
- **Told not to repeat it.** Kingfisher puts a fixed sentence ahead of the labelled text
  asking the model not to repeat or record it unless asked, so no deployment has to
  remember to. Wording, not enforcement: scrubbing known values out of what is saved
  would be matching free text, and is not proposed.

Sent where `SessionInstructionsMiddleware` sends today: after the static prompt's
breakpoint, before the memory block, on every call, stored nowhere. Its entry in
`decisions.md` keeps its reasoning under the new name.

### Tools: the caller on `ToolContext`

`ToolContext` gains the turn's `Caller`, read-only. A tool filters by
`runtime.context.caller.context["user_id"]` without the model seeing the id or being able
to pass another.

## Slices

One pull request each, off main. 1 and 2 both touch the long form; the second to land
adds its key to it.

1. **Slots.** Built: `core/definitions/prompts.py`, with the record in `decisions.md`
   under *`system_prompt` written long*. `system_prompt`'s long form with `text` and `slots`, matched on the caller's
   grant for the agent and its declared subagents; `require_literal_prompt` walking the
   long form; the refusals above. The build already has the caller's names as `held`,
   the datazone among them as `datazone=<zone>`, and passes it to `activated_subagents`,
   so matching needs no new argument.
2. **`TurnInstructions`.** Replaces `SessionInstructions` and its middleware's input;
   `Caller`, `Told`, the request's `context`, `today`, `receives` and its startup check; the
   sentence ahead of labelled text. `ports.md`'s section is rewritten.
3. **The caller on `ToolContext`.** `tools.md` gains how a tool reads it.
4. **Memory wording.** `memory_guidelines.md`'s first example saves a user's email address,
   which the sentence in slice 2 tells the model not to do. Replace it with an example of
   a preference.

## Not measured

- **The cache cost of a slot keyed on a name no tool lock uses.** Two grants with the same
  tools share a tool list; a datazone-only slot gives them different system prompts and
  so a cache entry each. Count it on one deployment's real grants before calling it free.
- **What slot text does to a subagent's prompt size.** A subagent's prompt was 1,902
  characters when last measured; one with many slots grows with every grant.
- **Whether the model honours "do not repeat".** One trial: a user's name in `labelled`,
  a task inviting a report, and a look at `/outputs` and `/memory/AGENTS.md` after.
