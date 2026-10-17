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

**Offered middleware**:
Middleware a deployment makes available for its definitions to name.
_Avoid_: middleware registry, deployment middleware

**Instructions**:
A deployment's standing text for every agent and subagent, read after kingfisher's own prompt
and before the agent's, and the plugin that supplies it.
_Avoid_: deployment prompt, house rules, user prompt, PROMPT.md

### Access

**Source id**:
A name a caller holds and an audience asks for, declared in the deployment's vocabulary. It
names nothing else: not a path, a file or a database.
_Avoid_: source, sources, group

**Audience**:
The source ids that may reach a definition, or one thing a definition holds: any one of
several, each either a single source id or a set that must be held together.
_Avoid_: required sources, permissions, ACL

**Vocabulary**:
The source ids a deployment declares, and which of them cover or require others. It holds no
audiences.
_Avoid_: access file, access policy

**Datazone**:
A label a caller is in at most one of, and a definition may require. Checked beside source ids,
never as one of them.
_Avoid_: zone, data zone, region
