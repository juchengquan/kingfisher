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
makes to every caller; no definition can name it or take it out.
_Avoid_: built-in middleware, guards, kingfisher's middleware

**Offered middleware**:
Middleware a deployment makes available for its definitions to name.
_Avoid_: middleware registry, deployment middleware

**Instructions**:
A deployment's standing text for every agent and subagent, read after kingfisher's own prompt
and before the agent's, and the plugin that supplies it.
_Avoid_: deployment prompt, house rules, user prompt, PROMPT.md

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
