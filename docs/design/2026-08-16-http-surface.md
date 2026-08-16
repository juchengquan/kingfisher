# An HTTP surface for kingfisher

**Status:** phases 1–3 shipped. Capabilities, files, the error totality test
and packaging remain.
**Date:** 2026-08-16

The tenancy work of the previous rounds existed to make the package safe to put
a server in front of. This is that server: `kingfisher.server`, an ASGI
application that maps HTTP onto the methods `Kingfisher` already has, and knows
nothing about who is calling.

Read the decisions with the measurements. Several of these were settled by
running something rather than by argument, and where a measurement overturned
what was about to be built, it is recorded next to the decision rather than
quietly folded in.

## The shape

The package stays a library. The server is a fourth directory beside `domain`,
`application` and `infrastructure`, shipped in the same distribution behind a
`[server]` extra, and separated from the library by an import rule rather than
by good intentions:

> **The server may import `kingfisher` and nothing deeper.** Not
> `kingfisher.domain.*`, not `kingfisher.application.*`, not
> `kingfisher.infrastructure.*`. And nothing in those three may import
> `kingfisher.server`.

That makes the server the first real consumer of the public API, on the same
footing as anyone outside it. When the server needs something the package does
not export, the answer is to export it deliberately.

It earned its keep before a line was written. Three gaps in the public surface
were found by asking what the server would be allowed to touch:

| Gap | Why it matters |
|---|---|
| Four caller-facing errors are private | `UnknownSessionError`, `SessionBusyError`, `QuotaExceededError` and `CapabilityError` are the ones a caller must distinguish, and no consumer could catch them by name. Only `ConfigError` was public. |
| `async_checkpointer` is private | `astream` requires it — a sync saver "does not merely block the loop, it refuses" — so the async path was unreachable from outside the package. |
| No way to send a file | `Request.inputs`/`data` are host paths. A remote caller has none. |

## Endpoints

```
POST   /sessions                 -> {"session_id": ...}
GET    /sessions/{id}            -> {"session_id":..., "last_used":...} or 404
DELETE /sessions/{id}
POST   /sessions/{id}/turns      -> SSE stream, ending with the result
POST   /turns                    -> one-shot: mints a session, streams, returns its id
```

Session id in the path, not the body, because a Service in front has to
authorise "may this caller touch session X" — a gateway that must parse a JSON
body to make an access decision is a gateway that gets rewritten.

`POST /turns` exists because `session_id=None` is a capability the library has
and the path form cannot express. A stateless caller asking one question is the
common case for this API, and making that two round trips to preserve URL
symmetry is the wrong trade.

`reap` is deliberately absent, even as an admin route. It deletes other
people's sessions, and putting retention on the request path is what made it a
tenancy bug the first time.

`GET /sessions` — the collection — is also absent, and that is a correction. It
was in the design, with a `limit`, until the limit was examined: it bounds the
response body and nothing else. `dirs.listing` reads the whole directory either
way, so the 22 ms at five thousand sessions is paid regardless, and a session
id "is a bearer credential" by `UnknownSessionError`'s own words. A collection
endpoint hands out every credential on the box; a limit means it hands out a
hundred of them instead of five thousand. The Service that knows which sessions
belong to whom calls `sessions()` on the library in-process, which is who that
method was added for. `GET /sessions/{id}` stays, because a caller who already
holds the id learns nothing they could not learn by using it.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Transport only. The server never interprets identity.** | T1 put the tenancy boundary outside kingfisher. A server that authenticates is the thing T1 said should live outside, and building it here reopens that decision. Auth, caller→session mapping and per-caller quotas stay with whatever sits in front. |
| D2 | **`kingfisher/server/`, split from the library by an import rule.** | A directory is a convention; an AST test is a boundary. The rule makes the server a consumer of the public API rather than a privileged insider, and it found three missing exports before implementation began. |
| D3 | **One request per turn, streamed. No result persistence.** | Persisting a turn's result would make dropped answers recoverable and `turn_id` genuinely idempotent, but it adds durable state to a library whose direction is to have less. Deferred, not rejected — see *Still undecided*. |
| D4 | **Files arrive as ids resolved by a `FileStore` port.** | The same decision `DefinitionStore` made one phase earlier for the same reason. It is also the only option that keeps the library out of receiving and holding payloads. A local adapter ships so a deployment is not blocked on writing one. |
| D5 | **FastAPI, with responses hand-built from the dataclasses.** | FastAPI for inbound validation and OpenAPI. Responses are *not* mirrored as pydantic models: `RunResult` deliberately keeps `run_dir` and `log_path` as `Path` so `json.dumps` raises, and a mirror is a second home for that rule — one where adding a `Path` serialiser makes the error go away and ships exactly the leak the original refuses. |
| D6 | **Stop the turn on disconnect. No cancel endpoint.** | Measured: closing the stream runs `astream`'s `finally`, releases the claim, and the next turn on that session is accepted immediately. Disconnect *is* cancellation; a `DELETE /turns/{id}` would need a handle on a running turn, which is the process state ruled out by D1's statelessness. |
| D7 | **An explicit error→status map, with a test that it is total.** | Ten of eleven error types are `ValueError`, so catching `ValueError` would also catch `Request`'s empty-task check and any incidental one from a dependency, turning a bug into a 400. The totality test is the `LIGHT_EXPORTS \| HEAVY_EXPORTS` idiom, which has already caught an omission in this codebase. |
| D8 | **Named SSE events, with the kind set pinned by a test.** | The kind list in `RunEvent`'s docstring — the closest thing to a wire contract — was wrong in both directions. An API author would have published it verbatim. |
| D9 | **A heartbeat during silence.** | Proxies kill idle connections, and a tool call runs for minutes. The better reason: disconnect is only noticed when the server next tries to send, so the heartbeat is what bounds model spend after a client hangs up. |
| D10 | **Capabilities cross the wire with a sentinel default.** | Over HTTP the lattice has four states, not three: `"*"`, an array, `null`, and *absent*. Absent means the deployment's default; `null` means nothing. Pydantic collapses them unless the default is a sentinel. |
| D11 | **An app factory taking a ready `Kingfisher`.** | It is the substitution point every existing test already uses. A server that constructs its own instance at import time pushes tests back toward patching `create_deep_agent`, which this repo forbids because three live bugs got through that way. |
| D12 | **`ServerConfig`, separate from `Config`.** | Bind address, concurrency cap, heartbeat interval and body limit are none of the library's business. Keeping them out of `Config` is the library/service split held in the one place it would otherwise blur first. |
| D13 | **A shared `within(root, ref)` rule in the domain.** | A ref is caller-supplied, so both `FileStore` and `DefinitionStore` adapters need the same traversal guard. Writing it once is cheaper than finding the second copy in the duplication audit. |
| D14 | **The API does not accept `turn_id`.** | The library's `turn_id` reuses the *directory* and then runs the turn again in full. Over HTTP a field of that name reads as an idempotency key, and every convention says a repeat returns the first result rather than doing the work twice and charging for it. Correlation is served by `turn_id` in the `finished` event — the id the work got, not the one the caller hoped for. Additive later, once D3 is revisited. |

### Errors

Seven public, four not. The four that stay private mean the deployment is
wrong rather than the caller, and become 500: `ConfigError`, `ToolError`,
`DataError`, and `HostPathError` — the last being the backend refusing a host
path the *agent* produced mid-turn, which is not a request-time fault at all.

| Error | Status | |
|---|---|---|
| `UnknownSessionError` | 404 | the id names nothing |
| `SessionBusyError` | 409 | a genuine conflict with current state |
| `QuotaExceededError` | 429 | follows gRPC's `RESOURCE_EXHAUSTED`, which covers disk quota |
| `CapabilityError` | 403 | asked for something not granted |
| `UploadError` | 400 | refs that do not resolve |
| `SkillError` / `SubagentError` | 400 | a supplied definition is malformed |

429 for quota is the arguable one. It is the Google and gRPC convention, but it
is about storage rather than rate, so a client's `Retry-After` reflex is wrong —
retrying will not help until they delete something. 507 says the right thing and
is 5xx, which makes generic clients retry it, which is worse. The body carries
a stable machine-readable code from the same map, so a client that wants to be
precise can be.

**Status codes only work if the refusal happens before the response starts.**
`astream`'s first statement is `_prepare`, and nothing is yielded before it, so
every refusal is available before the first event exists. The server must pull
that first event *before* returning the response and map any exception there. A
`StreamingResponse` returned first puts 200 on the wire and buries every
refusal in the stream.

## Measurements

| | |
|---|---|
| claim held mid-turn, released on hangup, next turn accepted | verified — D6 needs no library change |
| `GET /sessions` at 50 / 500 / 5000 sessions | 0.24 / 2.17 / 22.25 ms, 4 / 41 / 414 KB |
| pydantic already installed (transitively, via langchain) | 2.13.4 — weight was not the argument in D5 |
| `_prepare` already offloaded with `asyncio.to_thread` | the async path is ASGI-safe by construction |
| a supplied `turn_id` | reuses the directory, re-runs the turn — D14 |
| event kinds emitted vs documented | wrong in both directions and by more than expected: `swept` and `sweep_failed` never fire, and *five* were missing — `cut_short` plus the four warnings a turn can open with (`protect_failed`, `withheld`, `indistinct`, `data_placed`). Ten kinds, not six. Now `KINDS`, pinned by a test — D8 |

## Sequenced plans

Each produces working, testable software on its own.

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | Public surface: export the seven caller-facing errors and `async_checkpointer`; classify every error by who caused it, and every export as light or heavy. No server code. | — |
| **2** | `kingfisher/server/` with the import rule and the sync-method rule in `test_architecture.py`; `create_app`; `ServerConfig`; the session endpoints (`POST`, `GET /{id}`, `DELETE`). | 1 |
| **3** | Turns: `POST /sessions/{id}/turns` and `POST /turns` as SSE, named events, the heartbeat, first-event-before-response, and the kind-pinning test. Carries the error map too — a turn endpoint cannot be correct without one, and writing a partial map now and a total one later would be writing it twice. | 2 |
| **4** | Errors: the totality test over every error class, and one body shape for every refusal. | 3 |
| **5** | Capabilities on the wire: sentinel default, all four states per axis tested. | 3 |
| **6** | Files: `within()` in the domain, the `FileStore` port, `input_refs`/`data_refs` on `Request`, resolution in `_admit`, writers beside `place_inputs`/`place_data`, and the local adapter. | 3 |
| **7** | Packaging: the `[server]` extra, the console entry point, request logging. | 3 |

Phase 1 is load-bearing and touches only the library. Phases 4, 5 and 6 are
independent of each other. Phase 6 is the largest and the only one that changes
the domain, which is why it is last rather than first: everything before it can
ship against a deployment that sends no files.

## Still undecided

- **Result persistence.** Deferred in D3. Writing a turn's result into its
  directory would make a dropped answer recoverable by a plain GET from any
  replica — shared storage, not process state — and would make `turn_id` a real
  idempotency key, closing D14. The objection is that it adds durable state to a
  library whose direction is to have less. The shape most likely to survive
  that objection is the Service recording what it streamed, since durability
  then sits where identity already is. Worth revisiting once there is a Service
  to put it in.
- **Authentication and per-caller quotas.** Outside, by D1 and T1. Nothing here
  knows who is calling, so nothing here can stop one caller opening unbounded
  sessions and starving the others.
- **Rate limiting.** Same reason. The concurrency cap in `ServerConfig` bounds
  the *process*, not a caller.
- **Cancellation of a quiet turn.** D6 stops a turn when the server next tries
  to send, and the heartbeat bounds that. A turn that produces nothing for a
  long time inside one tool call still runs until the next heartbeat; the turn
  deadline is the only other backstop.
