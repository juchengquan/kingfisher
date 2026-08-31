# Group Access In-Band Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move group audiences out of a central `access.yaml` and into the agent and subagent documents that own them, keeping only the group vocabulary central.

**Architecture:** Each definition carries `groups:` (its own audience) and may write its `tools`/`subagents`/`skills` selections as a mapping of name to audience. Those resolve against the caller's groups into the same `Capabilities` the shipped design produced, so everything downstream — graph exclusion, the withheld report, `for_groups`, the per-turn agent re-check — is unchanged. A short `groups.yaml` keeps the closed vocabulary and `contains`.

**Tech Stack:** Python 3.12, `dataclasses`, `PyYAML` (infrastructure only), pytest, ruff, ty.

**Spec:** The "Confirmed Design" section below — 9 decisions taken 2026-08-31, confirmed before this plan was written, superseding the central-file decisions of the earlier interview.

---

## Global Constraints

- **Line length 100.** `[tool.ruff] line-length = 100`.
- **`domain/` imports only the standard library and `kingfisher.domain`**, and touches nothing outside the process. Enforced by `tests/unit/test_architecture.py`.
- **`presentation/` reaches the library only through `kingfisher.__all__`.** Enforced by `test_a_consumer_uses_the_library_only_through_its_public_api`.
- **New exports must be classified** in `LIGHT_EXPORTS`/`HEAVY_EXPORTS`, and new error types in `CALLER_FACING_ERRORS`/`DEPLOYMENT_ERRORS`.
- **Every `KINGFISHER_*` read in `application/config.py` must appear in `.env.example`.**
- **Every module named in prose must exist** (`test_prose_naming_a_module_names_one_that_exists`).
- **A key a format does not define is refused, never ignored.** Use `domain.fields.unrecognised`.
- **CI gates are `uv run ruff check src/ tests/ service/ examples/`, `uv run ty check`, `uv run pytest -q`.** `ruff format` is *not* a gate — the repo has pre-existing drift.
- **Commit after every task.** Work on branch `group-access-control`; the final push force-updates PR #283.

---

## Confirmed Design (the spec)

1. **Definitions are deployment-owned**, so group names in them are as local as `model:`.
2. **Audiences live in the definitions**, as a dict, **per use-site**. `reviewer` may restrict `sql_query` to `[A]` while `analyst` opens it to `[A, B]`; both are correct.
3. **A definition's `groups:` is the default audience and the ceiling** for its entries. Omitted `tools:` = all tools at that audience; plain list = those at that audience; dict = per entry. An entry naming a group outside the definition's own is refused as dead policy.
4. **`groups.yaml` holds the vocabulary and `contains`, nothing else.**
5. **Dict form on `tools`, `subagents`, `skills`.** Not `builtin_tools`.
6. **No `groups:` line means everyone**, with startup naming every definition carrying none.
7. **Compiled subagents**: `groups` enforced; per-tool audiences narrow what is handed to `build`, under the caveat already documented for `tools:` there.
8. **`kingfisher list` gains two views** — by definition, and a roll-up by asset.
9. **Rework PR #283 in place.**

**Carried over unchanged:** closed vocabulary · membership per request · `for_groups()` per turn · `--as`, never a `Request` field · unscoped calls refused, `UNSCOPED` the loud opt-out · any-overlap, `["*"]` everyone · out of reach reads as not offered · pinned agent re-checked every turn · uploads unchanged · read once at startup · composes with deployment `grants` by intersection · malformed file refuses to start · HTTP deferred.

**Consequences, not decisions:** reconciliation disappears (`listed_not_offered` has no meaning when the definition *is* the asset, and a bad tool name is already refused by `Offering.refuse_unknown`); inheritance works on resolved sets; two ceilings intersect; `AgentSpec.declares` becomes group-aware; `KINGFISHER_ACCESS_FILE` becomes `KINGFISHER_GROUPS_FILE`.

**Worked example:**

```yaml
# groups.yaml
groups:
  A: {}
  B: {}
  C: {}
  admin: {contains: [A, B, C]}
```

```yaml
# agents/assistant.yaml
name: assistant
description: ...
system_prompt: |
  ...
groups: [A, B]
tools:
  sql_query: [A]
  http_fetch: [A, B]
subagents:
  reviewer: [A]
skills: [code-review]
```

---

## File Structure

**Modify**

| Path | Change |
| --- | --- |
| `src/kingfisher/domain/access.py` | Strip to vocabulary: keep `Audience`, `AccessError`, `UNSCOPED`, `Held`, `expand`, `reaches`. Delete `entries`, `reconciled`, `CONTROLLED`. Add `AUDIENCED`, `audience_of`, and a `Groups` value replacing `Access`. |
| `src/kingfisher/domain/fields.py` | `Reader.audienced()` — reads a selection that may be a mapping, returning `(Selection, Mapping[str, Audience])`. |
| `src/kingfisher/domain/agent.py` | `KNOWN` gains `groups`; `AgentSpec` gains `groups` and `audiences`; `declares` becomes `declares(held)`. |
| `src/kingfisher/domain/subagent/__init__.py` | `SubagentSpec` gains `groups` and `audiences`. |
| `src/kingfisher/domain/subagent/reading.py` | `KNOWN` and `DECLARED` gain `groups`; both readers populate the new fields. |
| `src/kingfisher/infrastructure/access_policy.py` | Loads `groups.yaml` into `Groups`. |
| `src/kingfisher/config.py` | `Config.access: Groups | None`. |
| `src/kingfisher/application/config.py` | `KINGFISHER_GROUPS_FILE`, default `<workspace>/groups.yaml`. |
| `src/kingfisher/application/service.py` | `_effective_grants` resolves from the *agent's* spec; `agent_named` filters on `spec.groups`; the unreachable report becomes "definitions with no groups". |
| `src/kingfisher/infrastructure/harness/agent.py` | Delegate audiences applied where `as_subagent` is called. |
| `src/kingfisher/application/inventory.py` | Carries per-definition audiences and the roll-up. |
| `src/kingfisher/presentation/cli/listing.py` | Two views. |
| `.env.example`, `docs/formats.md` | Renamed variable; rewritten Access section. |

**Delete**

`tests/unit/test_access_reports.py` — reconciliation is gone. Its one surviving idea (an asset nobody can reach) moves to the no-groups report.

---

## Task 1: The vocabulary, and `Access` becomes `Groups`

**Files:**
- Modify: `src/kingfisher/domain/access.py`
- Modify: `tests/unit/test_access_format.py`, `tests/unit/test_access_resolution.py`
- Delete: `tests/unit/test_access_reports.py`

**Interfaces:**
- Produces: `Groups(names: Mapping[str, tuple[str, ...]])`, `Groups.expand(held) -> frozenset[str]`, `Groups.declared(name) -> bool`, `reaches(audience, held) -> bool`, `AUDIENCED: tuple[str, ...] = ("tools", "subagents", "skills")`, `parse(document, source) -> Groups`
- Keeps: `Audience`, `AccessError`, `UNSCOPED`, `_Unscoped`, `Held`

- [ ] **Step 1: Write the failing test**

Replace `tests/unit/test_access_resolution.py` with tests for the vocabulary alone — expansion, cycles, unknown names — and drop everything asserting `entries`/`resolve`. Keep `test_access_format.py`'s vocabulary cases and delete its asset-section cases.

```python
def test_a_containing_group_expands_transitively():
    groups = parse({"groups": {"A": {}, "B": {"contains": ["A"]}}}, source="groups.yaml")
    assert set(groups.expand(["B"])) == {"A", "B"}


def test_an_unknown_group_is_refused():
    groups = parse({"groups": ["A"]}, source="groups.yaml")
    with pytest.raises(AccessError, match="unknown group"):
        groups.expand(["Q"])


def test_an_asset_section_is_refused_now_that_audiences_live_in_definitions():
    """The old shape must not parse silently: a deployment upgrading has a file
    full of policy that would otherwise be read and ignored."""
    with pytest.raises(AccessError, match="lives in the definition"):
        parse({"groups": ["A"], "tools": {"sql_query": ["A"]}}, source="groups.yaml")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_access_resolution.py -q` — FAIL on `Groups` not existing.

- [ ] **Step 3: Implement**

Rename `Access` to `Groups`, keeping `groups` as `names`. Delete `entries`, `resolve`, `reachable`, `reconciled`, `AccessReport.listed_not_offered`, `CONTROLLED`. Add:

```python
#: The selection fields that may be written as a mapping of name to audience.
#: `builtin_tools` is absent: deepagents registers those itself, so they can be
#: filtered but never left out of a graph -- see `harness.narrowing`.
AUDIENCED: Final[tuple[str, ...]] = ("tools", "subagents", "skills")

#: Sections the old central format defined and this one does not, with where
#: each has gone. Refused rather than ignored, because a deployment upgrading
#: has a file full of policy that would otherwise be read and dropped in
#: silence -- which is the one failure this whole area is about.
MOVED: Final[Mapping[str, str]] = {
    kind: (
        f"{kind} audiences live in the definition now: write `groups:` in the "
        f"file, and a mapping under `tools:`, `subagents:` or `skills:` to "
        f"narrow one entry further"
    )
    for kind in ("agents", "subagents", "tools")
}


def reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches something with this audience.

    Public now, because three formats and the listing all ask it. Overlap, not
    containment: a longer list means *more* people.
    """
    return audience == ALL or bool(held & set(audience))
```

`parse` refuses `MOVED` keys via `fields.unrecognised(..., declined=MOVED)`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_access_resolution.py tests/unit/test_access_format.py -q`

- [ ] **Step 5: Delete the reconciliation tests**

```bash
git rm tests/unit/test_access_reports.py
```

- [ ] **Step 6: Commit**

```bash
git add -A src/kingfisher/domain/access.py tests/unit/
git commit -m "Keep the vocabulary central and nothing else"
```

---

## Task 2: Reading an audienced selection

**Files:**
- Modify: `src/kingfisher/domain/fields.py`
- Test: `tests/unit/test_access_fields.py` (create)

**Interfaces:**
- Consumes: `Audience`, `reaches` from Task 1
- Produces: `Reader.audienced(value, *, absent, key, refuse_all=None) -> tuple[Selection, Mapping[str, Audience]]`

**Why a pair rather than a new type:** `spec.tools` stays a `Selection`, so every existing consumer — `narrowed`, `Offering`, `declares`, `as_subagent` — is untouched. The audiences travel beside it and are consulted only when resolving for a caller.

- [ ] **Step 1: Write the failing test**

```python
"""Reading a selection that may carry an audience per entry."""

from __future__ import annotations

import pytest

from kingfisher.domain.agent import AgentError
from kingfisher.domain.capabilities import ALL
from kingfisher.domain.fields import Reader

read = Reader(source="x.yaml", error=AgentError)


def test_a_list_is_a_selection_with_no_audiences():
    assert read.audienced(["a", "b"], absent=ALL, key="tools") == (("a", "b"), {})


def test_omitted_is_the_absent_value_with_no_audiences():
    assert read.audienced(None, absent=ALL, key="tools") == (ALL, {})


def test_star_still_means_everything():
    assert read.audienced(["*"], absent=ALL, key="tools") == (ALL, {})


def test_a_mapping_selects_its_keys_and_carries_its_values():
    selected, audiences = read.audienced(
        {"sql_query": ["A"], "http_fetch": ["*"]}, absent=ALL, key="tools"
    )
    assert selected == ("sql_query", "http_fetch")
    assert audiences == {"sql_query": ("A",), "http_fetch": ALL}


def test_an_empty_mapping_is_refused():
    """`tools: {}` reads as nothing, which is spelled `[]` -- and is far more
    likely to be an unfinished edit."""
    with pytest.raises(AgentError, match="write \\[\\] "):
        read.audienced({}, absent=ALL, key="tools")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    with pytest.raises(AgentError, match="a list of group names"):
        read.audienced({"sql_query": "A"}, absent=ALL, key="tools")


def test_an_empty_audience_is_refused():
    with pytest.raises(AgentError, match="leave the entry out"):
        read.audienced({"sql_query": []}, absent=ALL, key="tools")


def test_a_star_mixed_with_names_is_refused():
    with pytest.raises(AgentError, match="cannot mean both"):
        read.audienced({"sql_query": ["*", "A"]}, absent=ALL, key="tools")


def test_a_mapping_may_not_be_starred():
    """`{'*': [A]}` is a name that is not a name. The star belongs to the list
    form, where it means the whole field."""
    with pytest.raises(AgentError, match="not a name"):
        read.audienced({"*": ["A"]}, absent=ALL, key="tools")
```

- [ ] **Step 2: Run to verify it fails** — `AttributeError: 'Reader' object has no attribute 'audienced'`

- [ ] **Step 3: Implement**

```python
    def audienced(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
    ) -> tuple[Selection, Mapping[str, Audience]]:
        """A selection, and who reaches each entry of it.

        Two spellings of one field, and the second is a strict extension of the
        first: a list selects, a mapping selects *and* says who for. Written as
        a pair rather than a richer type so that `spec.tools` stays the
        `Selection` every consumer already reads -- `narrowed`, `Offering`,
        `as_subagent` -- and the audiences travel beside it, consulted only
        where a caller's groups are known.

        The star is a property of the field, not of an entry, so it belongs to
        the list form. `{"*": [...]}` is refused rather than read as a name.
        """
        if not isinstance(value, Mapping):
            return self.selection(value, absent=absent, key=key, refuse_all=refuse_all), {}
        if not value:
            msg = (
                f"{self.source}: {key} is an empty mapping, which reads as nothing "
                f"-- write [] if that is what you mean, or name what it holds"
            )
            raise self.error(msg)
        audiences = {
            str(name): self._audience(raw, key=key, entry=str(name))
            for name, raw in value.items()
        }
        if ALL in audiences:
            msg = (
                f"{self.source}: {key} names {ALL!r} as an entry, which is not a "
                f"name -- {ALL!r} says something about the whole field, so write "
                f"it as [{ALL!r}]"
            )
            raise self.error(msg)
        return tuple(audiences), audiences
```

with a private `_audience` mirroring the checks `domain.access._audience` had (bare string, empty list, star mixed with names), raising `self.error` and naming `self.source`.

- [ ] **Step 4: Run to verify it passes**

- [ ] **Step 5: Commit**

```bash
git commit -m "Read a selection that says who each entry is for"
```

---

## Task 3: `groups:` on an agent

**Files:**
- Modify: `src/kingfisher/domain/agent.py`
- Test: `tests/unit/test_agent_format.py` (append)

**Interfaces:**
- Produces: `AgentSpec.groups: Audience`, `AgentSpec.audiences: Mapping[str, Mapping[str, Audience]]`, `AgentSpec.declares(held: frozenset[str] | None) -> Capabilities`

- [ ] **Step 1: Write the failing test**

```python
def test_an_agent_may_say_who_reaches_it():
    spec = parse({**MINIMAL, "groups": ["A", "B"]}, Path("a.yaml"))
    assert spec.groups == ("A", "B")


def test_an_agent_without_groups_is_reachable_by_everyone():
    """Decision 6: an absent optional field means no restriction, which is what
    it means everywhere else in this format."""
    assert parse(MINIMAL, Path("a.yaml")).groups == ALL


def test_a_tool_may_carry_its_own_audience():
    spec = parse(
        {**MINIMAL, "groups": ["A", "B"], "tools": {"sql_query": ["A"]}}, Path("a.yaml")
    )
    assert spec.tools == ("sql_query",)
    assert spec.audiences["tools"] == {"sql_query": ("A",)}


def test_an_entry_audience_outside_the_definitions_own_is_refused():
    """Dead policy: nobody reaching this agent is ever in C, so the line can
    never grant anything and is a mistake rather than a narrowing."""
    with pytest.raises(AgentError, match="never reaches"):
        parse(
            {**MINIMAL, "groups": ["A", "B"], "tools": {"sql_query": ["C"]}},
            Path("a.yaml"),
        )


def test_declares_without_groups_is_what_it_always_was():
    """A deployment with no policy, or an UNSCOPED call, must get exactly the
    grant this returned before any of this existed."""
    spec = parse({**MINIMAL, "tools": ["sql_query"]}, Path("a.yaml"))
    assert spec.declares(None).tools == ("sql_query",)


def test_declares_drops_an_entry_the_caller_does_not_reach():
    spec = parse(
        {**MINIMAL, "groups": ["A", "B"], "tools": {"sql_query": ["A"], "http": ["B"]}},
        Path("a.yaml"),
    )
    assert spec.declares(frozenset({"B"})).tools == ("http",)


def test_an_entry_with_no_audience_inherits_the_definitions(): 
    """A plain list under a policied definition means 'these, at my audience'."""
    spec = parse({**MINIMAL, "groups": ["A"], "tools": ["sql_query"]}, Path("a.yaml"))
    assert spec.declares(frozenset({"A"})).tools == ("sql_query",)
    assert spec.declares(frozenset({"B"})).tools == ()
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

Add `"groups"` to `KNOWN`. On `AgentSpec`:

```python
    #: Who may open a session on this agent. `ALL` when the file says nothing,
    #: because an absent optional field means no restriction everywhere else in
    #: this format -- and reading it as "nobody" would make adding a vocabulary
    #: file stop every unannotated definition working.
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry here. Only the fields
    #: in `AUDIENCED`. Empty for a definition written as plain lists, which is
    #: every definition that predates this.
    audiences: Mapping[str, Mapping[str, Audience]] = field(default_factory=dict)
```

`declares` becomes:

```python
    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this agent holds, said as the narrowing a request is clamped by.

        `held` is the caller's expanded groups, or `None` where this deployment
        has no policy or the call is `UNSCOPED`. `None` returns exactly what
        this returned before groups existed, which is what keeps every
        unpolicied deployment unchanged.

        An entry with no audience of its own inherits the definition's, so a
        plain list under a policied definition means "these, at my audience".
        """
        if held is None:
            return self._unrestricted()
        return replace(
            self._unrestricted(),
            tools=self._reaching("tools", self.tools, held),
            skills=self._reaching("skills", self.skills, held),
            subagents=self._reaching("subagents", self.subagents, held),
        )
```

with `_reaching(field, selection, held)` returning the selection unchanged when it is `ALL` or `None`, and otherwise keeping names whose audience — `self.audiences[field].get(name, self.groups)` — `reaches(held)`.

`parse` reads `groups` with `read.selection(..., absent=ALL, key="groups")`, reads the three audienced fields with `read.audienced`, and refuses a dead entry:

```python
    for field_name, entries in audiences.items():
        for entry, audience in entries.items():
            if spec_groups != ALL and audience != ALL and not (set(audience) & set(spec_groups)):
                msg = (
                    f"{source.name}: {field_name} entry {entry!r} is for "
                    f"{', '.join(audience)}, but this definition is only reachable "
                    f"by {', '.join(spec_groups)} -- so that line never reaches anyone"
                )
                raise AgentError(msg)
```

- [ ] **Step 4: Run to verify it passes**

- [ ] **Step 5: Fix every `declares` call site**

`declares` was a property. Run `grep -rn "\.declares" src/ tests/` and give each caller the held set or `None`.

- [ ] **Step 6: Run the full suite** — `uv run pytest -q`

- [ ] **Step 7: Commit**

```bash
git commit -m "Let an agent say who reaches it, and who reaches each thing it holds"
```

---

## Task 4: `groups:` on a subagent, both formats

**Files:**
- Modify: `src/kingfisher/domain/subagent/__init__.py`, `src/kingfisher/domain/subagent/reading.py`
- Test: `tests/unit/test_subagent_format.py` (append)

**Interfaces:** `SubagentSpec.groups`, `SubagentSpec.audiences`, `SubagentSpec.declares(held)` — the same three as `AgentSpec`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_subagent_may_say_who_reaches_it():
    spec = read_subagent({**MINIMAL, "groups": ["A", "B", "C"]})
    assert spec.groups == ("A", "B", "C")


def test_a_delegate_runs_degraded_when_a_tool_is_out_of_reach():
    """The compounding case, expressed in one file."""
    spec = read_subagent(
        {**MINIMAL, "groups": ["A", "B", "C"],
         "tools": {"sql_query": ["A", "B"], "http_fetch": ["A", "B", "C"]}}
    )
    assert spec.declares(frozenset({"C"})).tools == ("http_fetch",)


def test_a_compiled_subagent_may_carry_groups():
    """Reachability is kingfisher's decision -- an unreachable compiled delegate
    is never built -- so this one is a real boundary."""
    spec = declared({**COMPILED, "groups": ["A"]}, source="m.py")
    assert spec.groups == ("A",)


def test_a_compiled_subagent_may_carry_tool_audiences():
    """Narrows what is handed to `build`, under the caveat `tools:` already has
    there: a graph kingfisher did not build gets no allowlist."""
    spec = declared({**COMPILED, "groups": ["A", "B"], "tools": {"t": ["A"]}}, source="m.py")
    assert spec.audiences["tools"] == {"t": ("A",)}
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

Add `"groups"` to both `KNOWN` and `DECLARED`. Add the two fields to `SubagentSpec` and the same `declares(held)`. Both `parse` and `declared` read them the same way, and both apply the dead-entry check. `skills` and `subagents` stay in `NOT_COMPILED` for a Python declaration, so only `tools` is audienced there.

**Lift the shared half.** `declares`, `_reaching` and the dead-entry check are identical on both specs. Put them in `domain/access.py` as free functions taking `(groups, audiences, selection, held)` and call them from each spec, rather than writing the rule twice — the same argument `narrowed` was made public for.

- [ ] **Step 4: Run to verify it passes**

- [ ] **Step 5: Commit**

```bash
git commit -m "Let a delegate say who reaches it, in both of its formats"
```

---

## Task 5: Resolving a caller's grant from the definitions

**Files:**
- Modify: `src/kingfisher/application/service.py`
- Modify: `src/kingfisher/infrastructure/harness/agent.py`
- Test: `tests/unit/test_access_wiring.py` (rework)

**Interfaces:** `Kingfisher._effective_grants(groups)` resolves from `Config.access` (now `Groups`) plus the agent spec; `Caller.grants` is unchanged in meaning.

**The shape change:** the shipped `_effective_grants` asked `Access.resolve(groups)` for a whole-workspace grant. There is no such thing now — the audiences are per definition. So the caller's *held groups* are what travel, and the grant is produced where the agent is known.

- [ ] **Step 1: Write the failing test**

Rework `test_access_wiring.py`: `policied` writes the audiences into `agents/surveyor.yaml` rather than into `access.yaml`, and `groups.yaml` holds only the vocabulary. Keep every existing assertion — the refusal, `UNSCOPED`, the reusable handle, the deployment ceiling, and both graph-membership tests. Add:

```python
def test_the_agents_own_audience_bounds_its_tools(policied):
    """A plain list under a policied agent means 'these, at my audience'."""
    kf = Kingfisher(policied)
    assert kf.for_groups(["A"]).grants.tools == ("line_count",)
    assert kf.for_groups(["B"]).grants.tools == ()
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

`Kingfisher` keeps `self.access: Groups | None`. `_effective_grants(groups)` keeps its four states, but returns `self.grants` narrowed by *nothing* on its own — the per-definition narrowing happens in `graph_for`, where the spec is known:

```python
    def _held_for(self, groups: Held | None) -> frozenset[str] | None:
        """The caller's expanded groups, or `None` for no policy / UNSCOPED.

        One place that turns what a call said into what the specs are asked.
        """
        if self.access is None or not isinstance(groups, tuple):
            return None
        return self.access.expand(groups)
```

and `build_agent` receives `held` and calls `agent.declares(held)` instead of reading the property. `as_subagent` does the same for each delegate, so a delegate's own audiences bind it as well as its parent's entry for it.

- [ ] **Step 4: Run to verify it passes; then the full suite**

- [ ] **Step 5: Commit**

```bash
git commit -m "Resolve a caller's grant from the definitions that declare it"
```

---

## Task 6: Agent reachability, and the no-groups report

**Files:**
- Modify: `src/kingfisher/application/service.py`
- Test: `tests/unit/test_access_agents.py` (rework), `tests/unit/test_access_wiring.py` (append)

- [ ] **Step 1: Write the failing test**

Rework `test_access_agents.py` so the audience comes from `agents/*.yaml`. Every existing assertion stays — out of reach reads as absent, the listing in the refusal is filtered, `UNSCOPED` reaches everything, the pinned agent is re-checked per turn. Add the report:

```python
def test_definitions_with_no_groups_line_are_named_at_startup(cfg):
    """Decision 6's other half: default-open must not also be silent."""
    an_agent(cfg, "assistant")
    (cfg.workspace / "groups.yaml").write_text("groups: [A]\n", encoding="utf-8")
    kf = Kingfisher(from_env_for(cfg))
    assert ("agent", "assistant") in kf.access_report.unrestricted
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

`agent_named` filters on `reaches(spec.groups, held)` rather than on a central table. `AccessReport` loses `listed_not_offered`/`offered_unreachable` and gains:

```python
    #: Definitions carrying no `groups:` line, so reachable by everyone. Named
    #: because default-open must not also be silent: this is the whole of what
    #: stands between "we have not restricted that yet" and nobody noticing.
    unrestricted: tuple[tuple[str, str], ...] = ()
```

built in `Kingfisher.__init__` by walking the agent and subagent specs for `groups == ALL`.

- [ ] **Step 4: Run to verify it passes; then the full suite**

- [ ] **Step 5: Commit**

```bash
git commit -m "Name the definitions that restrict nobody"
```

---

## Task 7: Two views in the listing

**Files:**
- Modify: `src/kingfisher/application/inventory.py`, `src/kingfisher/presentation/cli/listing.py`
- Test: `tests/unit/test_cli.py` (rework the access cases)

- [ ] **Step 1: Write the failing test**

```python
def test_the_operator_sees_audiences_per_definition(policied, capsys):
    assert main(["list"]) == 0
    shown = capsys.readouterr().out
    assert "[A]" in shown


def test_the_operator_sees_a_roll_up_by_asset(policied, capsys):
    """The question the files can no longer answer on their own."""
    assert main(["list"]) == 0
    shown = capsys.readouterr().out
    assert "by tool" in shown
    assert "line_count" in shown


def test_a_roll_up_shows_a_tool_used_at_two_audiences(cfg, monkeypatch, capsys):
    """The case the roll-up exists for: a call site quietly wider than another."""
    ...
    assert main(["list"]) == 0
    section = capsys.readouterr().out.split("by tool", 1)[1]
    assert "narrow" in section and "wide" in section


def test_a_callers_view_carries_no_audiences(policied, capsys):
    assert main(["list", "--as", "A"]) == 0
    assert "by tool" not in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

`Inventory` drops `access`/`access_report`'s old shape and carries `audiences: Mapping[str, Mapping[str, Mapping[str, Audience]]]` — definition kind and name, then field, then entry. `listing._access` prints the per-definition view, then a roll-up inverted from it. Both are omitted under `--as`, exactly as before.

- [ ] **Step 4: Run to verify it passes; then the full suite**

- [ ] **Step 5: Commit**

```bash
git commit -m "Answer who reaches this tool, now that no one line does"
```

---

## Task 8: The variable, and the documentation

**Files:**
- Modify: `src/kingfisher/config.py`, `src/kingfisher/application/config.py`, `.env.example`, `docs/formats.md`

- [ ] **Step 1: Write the failing test**

```python
def test_the_vocabulary_file_can_be_relocated(env, tmp_path):
    elsewhere = tmp_path / "vocab.yaml"
    elsewhere.write_text("groups: [B]\n", encoding="utf-8")
    cfg = from_env({**env, "KINGFISHER_GROUPS_FILE": str(elsewhere)})
    assert cfg.access is not None
    assert set(cfg.access.names) == {"B"}
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

`access_file` becomes `KINGFISHER_GROUPS_FILE or workspace / "groups.yaml"`. Update `.env.example` — the old name is *not* kept as a fallback, because it named a file with a different format: a deployment that still has `access.yaml` should be told, not silently read.

Rewrite `docs/formats.md`'s Access section for the in-band shape: `groups.yaml`; `groups:` on a definition; the three audienced fields and the one that is not; the default-and-ceiling rule with all four spellings; dead-entry refusal; no-groups meaning everyone plus the report; the compounding example; compiled subagents and their caveat; `--as` and both listing views; uploads unchanged; and the two enforcement facts (out of reach is never on the graph; agents re-checked every turn).

- [ ] **Step 4: Full gates**

`uv run pytest -q && uv run ruff check src/ tests/ service/ examples/ && uv run ty check`

- [ ] **Step 5: Verify against a real workspace**

Build a demo workspace under the scratchpad with two agents at different audiences and a tool used at two audiences. Confirm: the operator listing shows both views, `--as` narrows, `admin` reaches through `contains`, an unknown group is a clean exit 2, and a caller reaching a delegate gets the degraded tool set.

- [ ] **Step 6: Commit and force-push**

```bash
git commit -m "Say where an audience lives, and what it means where it is"
git push --force-with-lease origin group-access-control
```

Then update PR #283's title and body to describe the in-band design.

---

## Self-Review

**Spec coverage.** Decision 1 → the design as a whole. 2 → Tasks 2–4. 3 → Task 3 (`_reaching` default, dead-entry refusal). 4 → Task 1. 5 → `AUDIENCED` in Task 1, applied in 3 and 4. 6 → Task 3 (`groups: Audience = ALL`) and Task 6 (the report). 7 → Task 4. 8 → Task 7. 9 → Task 8.

**Risk found while writing this.** `declares` is currently a `@property` and is read in at least `agent.py` and `service.py`. Turning it into a method is a silent failure if any call site is missed — a bare `spec.declares` becomes a truthy bound method rather than a `Capabilities`, and `intersect` on it would raise somewhere unhelpful. Task 3 Step 5 greps for every call site, and the full-suite run is the gate.

**Second risk.** `test_capabilities_on_the_wire.py` in `service/` asserts `CapabilitiesBody` matches `Capabilities` field for field. Nothing here adds a `Capabilities` axis, so that stays green — but if a later change is tempted to add one, that test is where it will complain.

**Placeholder scan.** None: every step names its file, its test and its code.
