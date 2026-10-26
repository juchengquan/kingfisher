# Memory kept up to date

**Status:** proposed, not built.
**Date:** 2026-10-26.
**Occasion:** the owner asked for memory that behaves like a chat app's: kept current by
something other than the agent's mid-task notes, and in time following a user across
conversations. Settled one question at a time. **Cited by symbol, not by line.** What it
rests on and has not been measured is under *Not measured*.

## How it works today

Memory is off unless `KINGFISHER_MEMORY_ENABLED` is set, and an agent writing
`memory: false` opts out. When on:

- **One file per session**, `/memory/AGENTS.md`. `LocalSessions` writes a starter file
  when the session is made, and nothing in kingfisher writes it after that: only the
  agent does, with `edit_file` or `write_file`, when it decides to.
- **Read once a turn.** deepagents' `MemoryMiddleware` loads the file in `before_agent`
  and keeps that copy for the turn, so an edit made mid-turn reaches the system message
  from the next turn on. The block sits after the cached prompt with a breakpoint of its
  own, in kingfisher's `memory.md`, which forbids saving credentials or details that
  identify the user.
- **Never curated.** Nothing trims, merges or summarises it; it is loaded whole every
  turn. When a long conversation is summarised, the summary goes to the transcript and
  the dropped messages to `/scratchpad/conversation_history/`; memory is untouched.
- **Changes are not reported.** The turn's file list names what is present under
  `/memory`, and the starter file always is.
- **It ends with its session.** *`/memory` lasts as long as its session, and the prompt
  says so* in `decisions.md` leaves memory shared between sessions as "a separate design,
  not made", needing "a tenancy argument". This is that design.

A trial on 2026-10-23 (three real turns, MiniMax-M3) told the agent the user's name,
email, employee id and team: name, email and id stayed out of memory, and the team was
saved anyway, rule notwithstanding.

## Decided

**It solves both problems, in order.** Facts lost when a long conversation is summarised,
then a user's memory carrying across their conversations. The first needs no storage or
tenancy and is built and measured before the second.

### Memory is a list of entries

`/memory/AGENTS.md` holds one entry per line, `- [m3] Reports figures in EUR`, under a
one-line header. Ids are kingfisher's: the agent adds a line without one, and the turn's
end gives it a fresh id, so a hand-edited file stays valid and no two entries collide.
Entries are what make the rest cheap: what changed is entries added, edited or removed by
id; a limit is enforced by entry; one can be forgotten from outside; the identifier check
reads one entry at a time.

### Two writers

The agent still writes when told "remember X", so it takes effect within the
conversation. A **memory writer** curates: one model call given the current entries and
the conversation since its last run, returning the whole revised list -- adding,
rewording, merging and dropping. Memory without curation is the growing file there is
now, so the writer is on whenever memory is, with no switch of its own; `memory: false`
still opts an agent out of both.

- **When.** At the end of a turn, while the session is still claimed, so two turns cannot
  write at once: on a turn where the conversation was summarised (kingfisher already
  reads the summariser's own mark), and otherwise every N turns since the writer last ran.
  N is `KINGFISHER_MEMORY_EVERY`, `memory_every` on `Config`, 2 by default while it is
  measured and to be raised. Not every turn, which costs a call on a "thanks"; not only
  summarised turns, which a short session never reaches. A turn where it runs ends a few
  seconds later; with no background job runner, that is the price.
- **What it reads.** The entries; the user's messages, the agent's replies and the names
  of the tools it called since the last run; on a summarised turn, the dropped messages
  from the history file. **Never** tool results, the system message, or the turn text:
  the turn text is where the user's name and ids come from, and a writer that never sees
  them cannot copy them.
- **Its model.** A `memory_writer:` entry in `models.yaml`, the agent's model when absent:
  curating a short list is an easy job, and a cheaper model the obvious saving.
- **Its instructions.** A shipped `prompts/memory_writer.md`: durable facts, preferences
  and conventions; nothing about the task in hand; nothing identifying the user.
- **Limits and failure.** 40 entries and 6,000 characters, which the writer is told. An
  answer over them, malformed, or a failed call leaves the previous memory as it was; the
  turn succeeds and a `memory_not_updated` event says why. A half-written memory is worse
  than a stale one.

### The identifier check

Before anything is saved, every entry is compared with every value of the turn's
`context` -- ignoring case, values under four characters skipped -- and one containing a
value is dropped and reported. The trial is why: told not to save the team, a model saved
it. `context` arrives with every request and the turn hook can say it every turn, so a
remembered copy adds nothing and goes stale when it changes. **Traded:** a harmless
repeat, "Wants times in Europe/London" beside `context["tz"]`, is dropped too. **Not
taken:** the deployment naming its identifying keys, where a key it forgets is unguarded.

### Changes are reported, and memory can be edited

- A `memory_updated` event, and a `memory` field on `RunResult`, list the entries added,
  changed, removed and dropped by the check, compared between the turn's start and end so
  a write through the shell counts too.
- `Session` gains `memory()` to read the entries and `forget(id)` to drop one, refused
  while a turn holds the session, under the claim `reap` already takes. "Forget this" is
  what makes remembering acceptable.

### Across sessions: one memory, the deployment's store

- **One memory, not two layers.** With a store wired, the session's `/memory` file is a
  working copy of the user's memory: one list, one writer, one set of events. Notes about
  the task in hand belong in `/scratchpad`, not in a layer of their own.
- **A `MemoryStore` plugin** -- `load(key)` and `apply(key, changes)` -- with the key
  chosen by the deployment's code, from `context["user_id"]` say. kingfisher reads no part
  of the key. **Not taken:** keying on the caller's source ids, which say what a caller may
  reach, not who they are; two people with one grant would share a memory.
- **Reloaded every turn.** The start of each turn loads the latest from the store, as it
  already reads the file, so what one chat learnt reaches another on its next turn.
- **Saves merge by entry id.** A session sends the entries it added, edited and removed
  against the list it loaded, and the store applies them to its current one. Two chats of
  one user learning different facts both keep theirs; two editing the same entry end with
  the later edit, as one chat editing it twice would. **Not taken:** the last save
  replacing the list, which loses the other chat's facts; refusing the second save, which
  runs its writer twice.
- **No key in a request** falls back to session memory for that turn and says so in its
  events. Refusing would make memory a requirement of every request.
- **`forget` reaches the store**, or the next turn's reload would bring the entry back --
  a forget that looks as if it worked. Deleting a session leaves the user's memory alone.
- **The command keeps session memory only.** It takes no plugins, and has no way to name
  a key. An example file store keyed on `context["user_id"]` ships in `assets/plugins/`
  for code that builds its own `Kingfisher`.
- **The memory block says what is true.** Without a store it keeps "later turns of this
  conversation"; with one, that the memory carries into this user's later conversations.
  The `decisions.md` entry that keeps "a future session" out of the prompt is revised by
  the slice that makes it true.

## Slices

One pull request each, off main, in this order.

1. **Entries and the writer, on session memory.** The entries format and its ids, the
   triggers and `KINGFISHER_MEMORY_EVERY`, the `memory_writer:` model and
   `prompts/memory_writer.md`, the limits, the identifier check, and `memory_updated`,
   `memory_not_updated` and the `memory` field on `RunResult`.
2. **Reading and forgetting from outside.** `memory()` and `forget(id)` on `Session`, with
   their async twins, under the session's claim.
3. **The user's memory.** The `MemoryStore` port, the per-turn reload, merged saves, the
   fallback with no key, `forget` reaching the store, the memory block's wording, and the
   example store in `assets/plugins/`.

## Not measured

- **How often a real session is summarised.** The writer's main trigger rests on it; if
  sessions rarely reach the summariser, N carries the whole design and its default matters
  more than its name suggests.
- **What one writer call costs**, in tokens and seconds, on the agent's model and on a
  cheaper one; it decides N and whether a background runner is worth building.
- **Whether the writer keeps the limits and the format** without retries, run over the
  trial sessions and a long one.
- **Whether the per-turn reload costs any cache.** By the breakpoints `decisions.md`
  records, a changed memory block costs that block and the conversation and nothing
  before them; nothing has checked it with a store wired.
