# The documents, and how to read them

Where two documents disagree, the newest one that mentions a subject wins. That is
the whole convention, and it is load-bearing: `docs/design/` is history and is
not rewritten, so several documents here describe a design that was built and
then taken back out. Reading them in date order is the only way to get the
current answer from the folder alone -- which is what this page exists to save
you.

Where a document was reversed, the entry says so and names the one that
reversed it. Where it was never built, the entry says that too. Everything else
was built and still stands.

**Statuses are as the documents give them**, not re-judged here. Two say
"designed, not implemented" and mean it.

---

## Design

The argument for a change, written before it, with a `**Status:**` line kept
current and any correction recorded at the end rather than edited into the
decision it overturned. Newest first.

| Document | Status | What it settled |
|---|---|---|
| [A folder handed in, a command handed off](design/2026-08-26-a-folder-handed-in-a-command-handed-off.md) | built | Splits `build_backend`, which two queued jobs -- the Landlock fence and an out-of-tree filesystem -- would both have had to edit. |
| [A fence for the shell, and the doors it does not cover](design/2026-08-25-a-fence-for-the-shell.md) | built | Shell confinement on Linux. Before it, `sandbox-exec` was checked for and anything else ran unconfined with a warning, so `auto` and `off` did the same thing there. |
| [Nothing at rest on this machine](design/2026-08-21-nothing-at-rest-on-this-machine.md) | **designed, not implemented** | Session files, results and notes may not live on local disk; where they do live is the deployment's business. Several claims in it are still unmeasured. |
| [Examples are ours, assets are yours](design/2026-08-19-examples-are-ours-assets-are-yours.md) | built | **Reverses D1 of *The definitions ship with the library*.** Definitions leave the wheel, this repository's own set is renamed `examples/`, and where a deployment gets its definitions becomes a setting. |
| [What the catalogue dropped, and the two errors that followed](design/2026-08-18-what-the-catalogue-dropped.md) | built | An endpoint whose `key_env` is unset is dropped as the catalogue loads, and two errors followed from saying nothing about it. |
| [The definitions ship with the library](design/2026-08-18-the-definitions-ship-with-the-library.md) | built, then partly reversed | **Reverses *Assets as packages* and *One folder for the packages*.** Its own D1 was then reversed by *Examples are ours*. |
| [Subagents of subagents, with no cycles](design/2026-08-18-subagents-all-the-way-down.md) | built | Delegation past one level, and the cycle refusal that becomes necessary once a helper may have helpers. |
| [Mutating the architecture rules](design/2026-08-18-mutating-the-architecture-rules.md) | audited | Asks whether the 44 architecture rules actually fire. 43 held; one had lost its subject. |
| [A subagent that is code, and a model chosen rather than named](design/2026-08-18-compiled-subagents.md) | built | `CompiledSubAgent` -- a graph you built yourself -- beside the spec form `subagents/*.yaml` had always described. |
| [The main agent becomes a definition](design/2026-08-18-agents-as-definitions.md) | built | Gives the main agent a file. It was assembled from four places that did not know about each other. |
| [A tool failure is not a crash, unless the workspace wrote the tool](design/2026-08-18-a-tool-failure-is-not-a-crash.md) | **designed, not implemented** | A `FileNotFoundError` from one tool killed a sixteen-call run. Whose fault a tool failure is decides whether it should. |
| [Two subagents called `surveyor`](design/2026-08-17-two-subagents-called-surveyor.md) | built | The last of three name-clash documents, and the place that failed hardest. |
| [Saying where a tool lives, in the file that names it](design/2026-08-17-qualified-tool-references.md) | built | The `where::what` long form. One row landed differently -- see its *Corrections*. |
| [Two tools called `fetch`](design/2026-08-17-two-tools-called-fetch.md) | built | Revisits a conclusion *Skills from several parties* had ruled out: there is one tool dictionary **per agent**, which changes the answer. |
| [Two skills called `lookup`, from two people who never met](design/2026-08-17-skills-from-several-parties.md) | built | Skills arrive from parties who never coordinated names, and that is not a mistake to refuse. |
| [One answer to "which skills does this agent have"](design/2026-08-17-skill-registry.md) | built | Two readers of the skills catalogue disagreed, so a caller could activate a skill the agent was never told about. |
| [Where a rule about a tool name lives](design/2026-08-17-tool-rules-in-the-domain.md) | built | One rule for whether a tool name is valid, in the domain, instead of three implementations. |
| [Where the layer boundary actually is](design/2026-08-17-layer-boundaries.md) | built | Measured the DDD-folders proposal and found the premise mostly true already. |
| [The HTTP service, as its own package](design/2026-08-17-the-service-as-its-own-package.md) | built | `pip install kingfisher` should not put a web service on disk, and it did. |
| [A command worth shipping, and the exports that make it one](design/2026-08-17-a-command-worth-shipping.md) | built | A deployment installed from a wheel had the definitions and no way to put them anywhere. |
| [One folder for the packages](design/2026-08-17-one-folder-for-the-packages.md) | **superseded** | Put three distributions under `packages/`. Undone by *The definitions ship with the library*. |
| [Assets as packages, not as cargo](design/2026-08-17-assets-as-packages.md) | **superseded** | Sent the shipped definitions out to be pip packages of their own. Implemented in full, then reversed -- see its own *Superseded* section. |
| [An HTTP surface for kingfisher](design/2026-08-16-http-surface.md) | built | `kingfisher.presentation`, an ASGI app over the methods `Kingfisher` already had. |
| [Session-scoped workspaces and a system skill catalogue](design/2026-08-16-session-scoped-api.md) | built | Turns a personal agent that owns one directory into something a server can front. Several phases changed shape once measured. |
| [Nested discovery for tools and subagents](design/2026-08-16-nested-discovery.md) | built | Whether definitions may live in folders. Right for one of the three kinds, never examined for the other two. |
| [A model catalogue](design/2026-08-16-model-catalogue.md) | built | Asks whether `PROVIDERS` should move to YAML. Taken literally, no -- and what was worth building instead. |
| [An injectable catalogue](design/2026-08-16-injectable-catalogue.md) | built | Asks whether `infrastructure/` should be classes. Measured answer: none of them. |

## Specs

Older than the design folder's convention and narrower: what one format can and
cannot say, rather than an argument for changing it. All three are built.

| Document | What it covers |
|---|---|
| [A subagent's endpoint and model](specs/2026-08-16-subagent-endpoints.md) | Whether a subagent may carry both a provider and a model. Half the request already worked. |
| [Subagent definitions](specs/2026-08-16-subagent-definitions.md) | What the markdown format covers, and what it does not. |
| [Durable session data](specs/2026-08-16-durable-session-data.md) | Getting a file into a session's `/data`. `--input` exists and is explicitly not this. |

## Plans

Implementation plans, written to be executed task-by-task and carrying
checkboxes for it. They record *how* something was built and in what order,
which the design documents deliberately do not. None carries a status line;
each names the design document it implements.

| Plan | Implements |
|---|---|
| [A subagent brings its own](superpowers/plans/2026-08-19-a-subagent-brings-its-own.md) | Bundled tools and skills that arrive with a delegate. |
| [A folder for the catalogue](superpowers/plans/2026-08-18-a-folder-for-the-catalogue.md) | The catalogue split. Also where **"`docs/design/` is history and is not rewritten"** is written down. |
| [Phase 4: The artifact manifest](superpowers/plans/2026-08-16-phase-4-artifact-manifest.md) | *Session-scoped API*, Decision 2. |
| [Phase 3: Uploaded definitions](superpowers/plans/2026-08-16-phase-3-uploaded-definitions.md) | *Session-scoped API*, Decisions 4, 5, 6. |
| [Phase 2: System catalogue](superpowers/plans/2026-08-16-phase-2-system-catalogue.md) | *Session-scoped API*, Decision 3. |
| [Phase 1: Session-rooted workspace](superpowers/plans/2026-08-16-phase-1-session-rooted-workspace.md) | *Session-scoped API*. |
| [Real streaming](superpowers/plans/2026-08-16-real-streaming.md) | Token streaming through the service. |

## Reference

| Document | What it is |
|---|---|
| [`formats.md`](formats.md) | The definition formats as they are *now* -- agents, subagents, tools, skills. The only document here kept current rather than dated, and the one the README sends people to. |
