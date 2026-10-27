# kingfisher

A library for running agents against a workspace, where what each request may do is
decided by the caller rather than by the code.

## Language

### How the package is put together

**Core**:
The engine every deployment runs, whatever it has swapped out: what a request, a turn, a
session and a grant are, and the interfaces its plugins satisfy.
_Avoid_: domain, application layer, kernel

**Plugin**:
A part of kingfisher a deployment may replace with its own, of which kingfisher ships an
implementation.
_Avoid_: port, adapter, module, extension

**Interface**:
What a plugin must be for the core to use it, stated once by the core.
_Avoid_: port, protocol, contract

**Default**:
The implementation kingfisher uses for an interface when a deployment names none. Sessions
have none: a deployment always names them, kingfisher's own included.
_Avoid_: reference implementation, built-in

### Sessions and turns

**Session**:
One continuing conversation and the files it keeps, named by its id. Whoever holds the id
may continue it.
_Avoid_: thread, conversation id

**Turn**:
One request carried out in one session, from start to finish. A session runs one turn at a
time, and a turn may stop partway to wait for an approval.
_Avoid_: run, step, call

**Note**:
Text a caller adds to one turn's request, sent after the task in that turn's message and
kept with it, so later turns see it as it was sent. A turn that answers an approval adds
no message, and so carries no note.
_Avoid_: hint, turn context, preamble, per-turn prompt

**Workspace**:
The folder for what sessions make: the sessions themselves, kept there by kingfisher's own
Sessions, and kingfisher's own state beside them. Not where definitions are, which people
write and kingfisher only reads: that is the definitions folder.
_Avoid_: state folder, data folder, root

### The plugins

**Sessions**:
The plugin that keeps every session: opens the backend a turn runs on, and lists, sizes and
deletes sessions.
_Avoid_: session backends, session store

**Backend**:
One session's filesystem and shell, as the agent sees them.
_Avoid_: session backend, store, sandbox

**Run events**:
The record of what each turn did, one event at a time, and the plugin that keeps it.
_Avoid_: run log, telemetry

**Wire format**:
How one family of model endpoints is spoken to: which client speaks it and what that client
must be told.
_Avoid_: adapter, provider

**Standing middleware**:
Middleware kingfisher puts around every agent it builds because it keeps a promise kingfisher
makes to every caller; no definition can name it or take it out, save the filesystem
middleware and the prompt caching, each of which a deployment's own of that name replaces.
_Avoid_: built-in middleware, guards, kingfisher's middleware

**Offered middleware**:
Middleware a deployment makes available for its definitions to name.
_Avoid_: middleware registry, deployment middleware

**Instructions**:
A deployment's standing text for every agent and subagent but one that arrives already
built, which wrote its own prompt, read after kingfisher's own prompt and before the
agent's, and the plugin that supplies it.
_Avoid_: deployment prompt, house rules, user prompt, PROMPT.md

**Session instructions**:
A deployment's text for one session, for every agent and subagent its turns run but one
that arrives already built, read after the prompt they share with every other session,
and the plugin that supplies it. Not a note, which a caller adds to one turn.
_Avoid_: session prompt, conversation prompt, dynamic prompt, per-session system text

### Definitions

**Portable subagent**:
A subagent written to travel between deployments, usually in a package, that kingfisher
builds from its description. It cannot name anything only one deployment knows: its own
middleware, its helpers, or its model. Not one that arrives already built, and not a
subagent document, which is a YAML file. Its shape decides, not where it is written: one
typed into a deployment's own definitions folder is portable too.
_Avoid_: packaged subagent, Python subagent, external subagent

### Access

**Source id**:
A name a caller holds and an audience asks for. It names nothing else: not a path, a file or
a database.
_Avoid_: source, sources, group

**Audience**:
The source ids that may reach a definition or a tool, or be told of a skill a definition
offers: any one of several, each either a single source id or a set that must be held together.
_Avoid_: required sources, permissions, ACL

**Vocabulary**:
The source ids and datazones a deployment's definitions use. Collected from the definitions;
nobody writes it down.
_Avoid_: access file, access policy, source_ids.yaml

**Datazone**:
A label a caller is in at most one of, and a definition may require. Checked beside source ids,
never as one of them.
_Avoid_: zone, data zone, region

**Opens**:
Of the names a caller holds, one in an audience entry they satisfy, so it is a reason they
reach something. Several names may each open the same thing on their own.
_Avoid_: needs, grants, permits, unlocks

**Would open**:
A name a caller does not hold that is the one name missing from an entry of an audience they
do not reach, so holding it alone would open something.
_Avoid_: missing, required, suggested

### Tools

**Built-in tool**:
A tool that comes with the agent, for a definition to grant or leave out: the file tools, the
shell, the checklist, and handing work to a subagent. Not one a deployment supplies, which is
an offered tool.
_Avoid_: core tool, native tool, standard tool

**Offered tool**:
A tool a deployment supplies itself rather than a built-in one, shared by its definitions or
kept by one subagent as its own.
_Avoid_: workspace tool, custom tool, user tool, plugin tool

**Attachment**:
A file a tool's result carries in a block of its own rather than as text: a PDF, an image,
audio or video, or any other kind. Not the result's text, even where that names a file, and
not a file a caller sends with a request.
_Avoid_: file block, media, binary

**Tool result**:
What one tool call hands back. Not a file the agent wrote, even where the result names one,
and not anything said in the conversation around it.
_Avoid_: tool output, tool response, observation

**Kept record**:
A file under the scratchpad that says what happened rather than what the agent made: a
recorded result, the conversation summarised away, an attachment's payload. Written for
the agent to read and never by it, so a write there is forgery rather than editing.
_Avoid_: archive, log, artifact, history

**Recorded result**:
A tool result kingfisher keeps its own copy of, so that a later turn can read back what a
call answered. Not every result is one: a file tool's read or write is already a file, and
the checklist is one list the conversation carries.
_Avoid_: saved result, cached result, logged result

**Result id**:
The name a recorded result is known by for the rest of its session, such as `A-0042`. It is
taken when the call starts, not when the result is recorded, so a call that then fails, or
a delegation paused for an approval and run again, leaves a number no result has; and an
id is never given to a second result. A call that is never recorded takes none.
_Avoid_: entry number, row id, ledger id

**Preview**:
What stands in for a recorded result too long to show the model whole: its beginning and
end, and where the whole of it is kept, or that it was not. The result itself is
unchanged.
_Avoid_: stub, truncation, excerpt, elision

**Cleared result**:
A recorded result swapped in the conversation for a short handle, because the conversation
has grown rather than because the result was long. What it points at is the recorded copy.
_Avoid_: evicted result, dropped result, compacted result

**Conversation history**:
What is kept of the conversation itself rather than of any tool's answer: a message too
long to carry, and the record a summary leaves behind. Not tool results, and kept apart
from them.
_Avoid_: transcript, message log, chat history
