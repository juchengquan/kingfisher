# Group Access Control (`access.yaml`) Implementation Plan

> ## ⚠ REVERSED — DO NOT IMPLEMENT THIS
>
> **Status: built in full on 2026-08-31, then reversed the same day.** Group
> access control ships, but *not* as this plan describes. The central
> `access.yaml` specified below — decisions 6, 7, 9 and 12 — was replaced within
> the same branch, and the file it asks you to write is now **refused by name at
> startup**: an `agents:`, `subagents:` or `tools:` section in `groups.yaml` is a
> load error, not a policy.
>
> **What shipped instead.** The vocabulary is central and the policy is not: who
> reaches what is written in each definition's own `groups:` line, because a
> definition *is* the asset it is about. Read
> [`examples/groups.yaml`](../../../examples/groups.yaml) and
> [`examples/agents/analyst.yaml`](../../../examples/agents/analyst.yaml).
>
> **Why.** Not taste. A central table can name an asset the workspace no longer
> offers, and that stale entry had to be *dropped* rather than reported, because
> the grant it produced reached `Offering.refuse_unknown` and turned every turn
> into a refusal. A definition cannot go stale that way. The full entry is in
> [`decisions.md`](../../decisions.md) under *Reversed: a central `access.yaml`
> listing every asset by name*.
>
> **What survived unchanged**, and is worth reading this for: `for_groups`,
> `UNSCOPED`, the refusal of a call that names no caller, the per-turn re-check
> of a session's pinned agent, and the closed vocabulary.
>
> Kept as a record of an argument that was made and lost, on the day it was
> made. It is not work to do, and the checkboxes below are all long since either
> done or moot. If you are here to build something, `decisions.md` is the file
> you want.

> **For agentic workers:** the sub-skill instruction below applied when this was
> a live plan. It is not one now — see the notice above before acting on
> anything in this document.

**Goal:** Let a deployment declare, in one static YAML file, which user groups may reach which tools, subagents and agents — and have an ungranted asset never reach the graph at all.

**Architecture:** A new domain value `Access` turns a caller's group names into an ordinary `Capabilities`. Everything downstream is unchanged: `Kingfisher` intersects that grant with its deployment-wide `grants` before `build_agent`, so an out-of-reach tool is never attached to the graph and an out-of-reach subagent is never compiled. Group resolution is pure and lives in `domain/`; YAML decoding lives in `infrastructure/`, mirroring how `Models` and `model_catalogue.load` are already split.

**Tech Stack:** Python 3.12, `dataclasses`, `PyYAML` (infrastructure only), pytest, ruff.

**Spec:** This document's "Confirmed Design" section below. It is the spec — 18 decisions taken in an interview on 2026-08-31, confirmed by the user before this plan was written.

---

## Global Constraints

- **Line length is 100.** `[tool.ruff] line-length = 100` in `pyproject.toml`.
- **`domain/` imports only the standard library and `kingfisher.domain`.** Enforced by `tests/unit/test_architecture.py::test_domain_imports_only_the_standard_library_and_itself`. No `yaml`, no `pathlib` file reads.
- **`domain/` touches nothing outside the process.** Enforced by `test_domain_touches_nothing_outside_the_process`. No `open`, `mkdir`, `write_text`, `replace` — note that even `.replace` on a *string* is flagged, because the rule reads names not types (see the comment in `domain/capabilities.py:278`). Use `split`/`join`.
- **Third-party imports are declared per area** in `test_architecture.py::THIRD_PARTY`. `infrastructure` already allows `yaml`; `""` (i.e. `src/kingfisher/config.py`) allows none. Intra-package imports are not restricted by that table.
- **Every `KINGFISHER_*` variable read in `src/kingfisher/application/config.py` must appear in `.env.example`.** Enforced by `tests/unit/test_config.py::test_every_variable_read_is_documented`.
- **Every module named in prose must exist.** Enforced by `test_architecture.py::test_prose_naming_a_module_names_one_that_exists`. Do not reference a module in a docstring before creating it.
- **A key a format does not define is refused, never ignored.** House rule, stated in `domain/subagent/reading.py` and `infrastructure/model_catalogue.py`. Use `domain.fields.unrecognised`.
- **Run the full suite with `pytest`** (testpaths are `["tests", "service/tests"]`).
- **Commit after every task.**

---

## Confirmed Design (the spec)

**Identity**
1. Groups are kingfisher's own closed vocabulary, declared in the YAML. Unknown group names are refused.
2. Kingfisher does not know users. Membership arrives per call as a list of group names.
3. Groups are bound per turn via `kf.for_groups([...])`, which returns a reusable handle. One `Kingfisher` keeps owning the catalogue, session store and locks.
4. Locally the groups are a parameter (CLI `--as A,B`), never a field on `Request`.
5. An unscoped call refuses once a policy exists. `for_groups(UNSCOPED)` is the loud opt-out.

**The file**
6. One central `access.yaml`, keyed by asset, one section per kind.
7. Controls `tools`, `subagents`, `agents`. Not `skills`, not `builtin_tools`.
8. `[A, B]` means any overlap. `["*"]` means everyone.
9. Unlisted means nobody, and a load-time report names every asset no group can reach.
10. `contains` expands one group into others, resolved at load. Cycles refused.
11. Read once at startup into `Config`, mirroring `models.yaml`.
12. A line naming an asset the workspace no longer offers is reported, not fatal.

**Behaviour**
13. One grant, derived from the caller's groups, applied at every level. A subagent has no identity of its own.
14. A subagent reachable but missing some of its tools runs degraded and reports what was withheld.
15. Out of reach reads as *not offered* — filtered from listings and error messages. The real reason goes to the audit log, server-side.
16. The pinned agent is re-checked every turn.
17. Uploaded definitions are unchanged; they cannot escalate.
18. `kingfisher list` unscoped is the operator view with a groups column; `--as` simulates a caller.

**Settled by convention**
- `KINGFISHER_ACCESS_FILE` or `workspace/"access.yaml"`; `Config.access: Access | None`; absent means the feature is off.
- A malformed file refuses to start.
- Composes with the existing deployment-wide `grants` by intersection.
- `for_groups([])` reaches nothing, so every turn refuses.
- A `skills:` or `middleware:` section is refused with its own message, not silently ignored.

**Deliberately out of scope**
- How the HTTP service learns a caller's groups. `service/` is untouched by this plan.
- `skills`, `builtin_tools`, `middleware`, `endpoints`, `models` as controlled kinds.

---

## File Structure

**Create**

| Path | Responsibility |
| --- | --- |
| `src/kingfisher/domain/access.py` | `Audience`, `AccessError`, `UNSCOPED`, `Access`, `AccessReport`, `parse()`. Pure: group names in, `Capabilities` out. No I/O. |
| `src/kingfisher/infrastructure/access_policy.py` | `load(path) -> Access \| None`. YAML decode only, mirroring `infrastructure/model_catalogue.py`. |
| `tests/unit/test_access_format.py` | The document format: what parses, what is refused, what the messages say. |
| `tests/unit/test_access_resolution.py` | Groups to `Capabilities`: expansion, overlap, `"*"`, empty, unknown. |
| `tests/unit/test_access_reports.py` | `reconciled()` and the two halves of the load report. |
| `tests/unit/test_access_wiring.py` | `for_groups`, `UNSCOPED`, the refusal, and that an out-of-reach asset is not on the graph. |

**Modify**

| Path | Change |
| --- | --- |
| `src/kingfisher/config.py` | Add `Config.access: Access \| None = None`. |
| `src/kingfisher/application/config.py` | Read `KINGFISHER_ACCESS_FILE`, default `workspace/"access.yaml"`. |
| `.env.example` | Document `KINGFISHER_ACCESS_FILE`. Required by a test. |
| `src/kingfisher/application/service.py` | `Caller`, `Kingfisher.for_groups`, `_effective_grants`, `groups=` on the four entry points, reconcile in `__init__`, filter `_withheld_by_kind`. |
| `src/kingfisher/application/inventory.py` | Carry the policy and the reach filter on `Inventory`. |
| `src/kingfisher/presentation/cli/listing.py` | Groups column and the report lines. |
| `src/kingfisher/presentation/cli/__main__.py` | `--as` on `list` and `run`. |
| `src/kingfisher/__init__.py` | Export `Access`, `AccessError`, `UNSCOPED`. |
| `docs/formats.md` | A section for `access.yaml`. |

---

# STAGE 1 — tools and subagents

## Task 1: The `Access` value and group expansion

**Files:**
- Create: `src/kingfisher/domain/access.py`
- Test: `tests/unit/test_access_resolution.py`

**Interfaces:**
- Consumes: `kingfisher.domain.capabilities.{ALL, Capabilities, Selection}`
- Produces:
  - `Audience = Literal["*"] | tuple[str, ...]`
  - `AccessError(ValueError)`
  - `UNSCOPED: Final` — a sentinel object, `_Unscoped()`
  - `CONTROLLED: tuple[str, ...] = ("agents", "subagents", "tools")`
  - `Access(groups: Mapping[str, tuple[str, ...]], entries: Mapping[str, Mapping[str, Audience]])`
  - `Access.expand(held: Iterable[str]) -> frozenset[str]`
  - `Access.reachable(kind: str, held: frozenset[str]) -> tuple[str, ...]`
  - `Access.resolve(held: Iterable[str]) -> Capabilities`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_access_resolution.py`:

```python
"""Turning a caller's groups into the grant one turn runs under.

Pure: no file, no workspace, no agent. What is asserted here is the rule --
overlap grants, absence denies, and a group that contains others reaches
whatever they reach.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.access import UNSCOPED, Access, AccessError
from kingfisher.domain.capabilities import ALL, Capabilities


def policy(**entries: dict[str, object]) -> Access:
    """An `Access` with a flat vocabulary inferred from what the entries name."""
    named = {g for audience in entries.values() for a in audience.values()
             if a != ALL for g in a}
    return Access(
        groups={name: (name,) for name in sorted(named)},
        entries={kind: dict(value) for kind, value in entries.items()},
    )


def test_a_caller_reaches_an_asset_their_group_is_listed_on():
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["A"]).tools == ("sql_query",)


def test_any_one_group_is_enough():
    """Decision 8: the list is an OR, not an AND."""
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["B", "C"]).tools == ("sql_query",)


def test_a_caller_with_no_listed_group_reaches_nothing():
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["C"]).tools == ()


def test_an_unlisted_asset_reaches_nobody():
    """Decision 9. `line_count` is absent, so no group has it."""
    access = policy(tools={"sql_query": ("A",)})
    assert "line_count" not in access.resolve(["A"]).tools


def test_a_star_audience_reaches_everyone():
    access = policy(tools={"http_fetch": ALL})
    assert access.resolve(["C"]).tools == ("http_fetch",)


def test_no_groups_at_all_reaches_nothing():
    """`for_groups([])` is a caller who holds nothing, and holds nothing here."""
    access = policy(tools={"http_fetch": ("A",)})
    assert access.resolve([]).tools == ()


def test_a_containing_group_reaches_what_it_contains():
    """Decision 10, and the reason it exists: `admin` is on no asset."""
    access = Access(
        groups={"A": ("A",), "B": ("B",), "admin": ("admin", "A", "B")},
        entries={"tools": {"sql_query": ("A",), "http_fetch": ("B",)}},
    )
    assert access.resolve(["admin"]).tools == ("sql_query", "http_fetch")


def test_an_unknown_group_is_refused_rather_than_ignored():
    """The vocabulary is closed, so a typo is a mistake and not an empty grant."""
    access = policy(tools={"sql_query": ("A",)})
    with pytest.raises(AccessError, match="unknown group"):
        access.resolve(["Q"])


def test_uncontrolled_axes_stay_wide_open():
    """Only three kinds are controlled; the rest must be the identity for
    `intersect`, or resolving would silently revoke what the deployment granted.
    """
    resolved = policy(tools={"sql_query": ("A",)}).resolve(["A"])
    assert resolved.builtin_tools == ALL
    assert resolved.skills == ALL
    assert resolved.middleware == ALL
    assert resolved.endpoints == ALL
    assert resolved.models == ALL
    assert resolved.memory is None


def test_resolving_can_only_narrow_the_deployments_grant():
    """The composition the service performs, asserted as a property."""
    deployment = Capabilities(tools=("sql_query",))
    access = policy(tools={"sql_query": ("A",), "http_fetch": ("A",)})
    assert deployment.intersect(access.resolve(["A"])).tools == ("sql_query",)


def test_unscoped_is_a_sentinel_and_not_a_group_name():
    """It must not be mistakable for a list of groups."""
    assert UNSCOPED is not None
    assert not isinstance(UNSCOPED, (str, tuple, list))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_resolution.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kingfisher.domain.access'`

- [ ] **Step 3: Write minimal implementation**

Create `src/kingfisher/domain/access.py`:

```python
"""Which groups reach which assets, and what one caller's groups grant.

Static, deployment-authored policy, read once and then only asked questions.
The answer it gives is an ordinary `Capabilities` -- which is the whole design:
a group grant is not a second permission system beside the one that exists, it
is a way of *deriving* the one that exists. Everything downstream is unchanged,
including the part that matters most, which is that an ungranted tool is never
attached to the graph and an ungranted subagent is never compiled.

Three kinds are controlled and the rest are deliberately not. `builtin_tools`
is absent because deepagents registers those itself: kingfisher can only filter
them afterwards, so gating them here would buy the weakest form of the
guarantee -- see `infrastructure.harness.narrowing`, which records a live run
where a model called `execute` from memory. `skills` is absent because a skill
is guidance rather than a capability, and the boundary is the tools it names.

Pure, like the rest of `domain/`: this module reads no file. The YAML half is
`infrastructure.access_policy`, the same split `Models` and
`infrastructure.model_catalogue` already have.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from kingfisher.domain.capabilities import ALL, Capabilities

#: The kinds this format controls. Order is the order a report prints them in,
#: coarsest first, because an agent decides the most and a tool the least.
CONTROLLED: Final[tuple[str, ...]] = ("agents", "subagents", "tools")

#: Kinds a reader will reasonably expect and this format deliberately omits,
#: with the reason each is refused rather than accepted and ignored.
DECLINED: Final[Mapping[str, str]] = {
    "skills": (
        "skills are not controlled here: a skill is guidance rather than a "
        "capability, and what bounds it is the tools it names"
    ),
    "builtin_tools": (
        "builtin tools are not controlled here: deepagents registers them "
        "itself, so they can be filtered but never left out of the graph"
    ),
    "middleware": (
        "middleware is not controlled here: it is already granted rather than "
        "inherited, and its names come from the deployment's own registry"
    ),
}

#: Who may reach one asset: `"*"` for everyone, or exactly these groups.
#: No `None`. An asset nobody may reach is written by leaving it out, which is
#: what makes the file a whitelist rather than a whitelist with a hole in it.
Audience = Literal["*"] | tuple[str, ...]


class AccessError(ValueError):
    """The access policy is malformed, or a caller named a group it does not define."""


class _Unscoped:
    """The type of `UNSCOPED`, so that it is not confusable with a group list."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSCOPED"


#: Running with no caller identity at all, said out loud.
#:
#: A sentinel rather than `None`, and that is the point. `None` is what an
#: argument nobody passed looks like, so a handler that forgot to say who is
#: calling would be indistinguishable from one that meant "no policy here".
#: This has to be typed, which means it can be grepped for in a review.
UNSCOPED: Final[_Unscoped] = _Unscoped()


def _reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches an asset with this audience.

    Overlap, not containment: a longer list means *more* people, which is what
    everyone reads an access list as meaning.
    """
    return audience == ALL or bool(held & set(audience))


@dataclass(frozen=True)
class Access:
    """One deployment's policy: the group vocabulary, and who reaches what.

    `groups` maps each declared name to its own transitive closure, itself
    included, worked out when the document was read. Expansion happens once
    rather than on every turn, and a cycle is refused where it is written
    rather than found by a stack overflow on a Tuesday.
    """

    #: Declared name -> that name plus everything it contains, transitively.
    groups: Mapping[str, tuple[str, ...]]
    #: Kind -> asset name -> who reaches it. Kinds are `CONTROLLED`.
    entries: Mapping[str, Mapping[str, Audience]]

    def expand(self, held: Iterable[str]) -> frozenset[str]:
        """Every group a caller effectively holds, following `contains`.

        Refuses a name the vocabulary does not have. That refusal is the reason
        the vocabulary is closed: silently expanding to nothing would turn a
        typo in a caller's group list into a caller who reaches nothing, which
        looks exactly like a caller who was denied.
        """
        wanted = tuple(held)
        if unknown := tuple(name for name in wanted if name not in self.groups):
            known = ", ".join(sorted(self.groups)) or "none"
            msg = (
                f"unknown group(s): {', '.join(sorted(set(unknown)))}; "
                f"this deployment defines {known}"
            )
            raise AccessError(msg)
        return frozenset(one for name in wanted for one in self.groups[name])

    def reachable(self, kind: str, held: frozenset[str]) -> tuple[str, ...]:
        """The names of one kind this caller reaches, in the file's own order."""
        return tuple(
            name for name, audience in self.entries.get(kind, {}).items()
            if _reaches(audience, held)
        )

    def resolve(self, held: Iterable[str]) -> Capabilities:
        """What a caller holding these groups may use, as an ordinary grant.

        Every axis this format does not control is `ALL`, which is the identity
        for `intersect` -- so composing this with a deployment's own grants
        subtracts exactly the three controlled kinds and nothing else. `None`
        there would revoke, silently, whatever the deployment had granted.

        `agents` has no axis on `Capabilities` and is not returned here. It is
        checked where a session is opened; see stage 2 of the plan.
        """
        expanded = self.expand(held)
        return Capabilities(
            builtin_tools=ALL,
            tools=self.reachable("tools", expanded),
            skills=ALL,
            subagents=self.reachable("subagents", expanded),
            middleware=ALL,
            endpoints=ALL,
            models=ALL,
            memory=None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_resolution.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Check the architecture rules still hold**

Run: `pytest tests/unit/test_architecture.py -q`
Expected: PASS. If `test_prose_naming_a_module_names_one_that_exists` fails, it is the docstring's reference to `infrastructure.access_policy`, which does not exist yet — in that case delete that sentence now and restore it in Task 3.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/domain/access.py tests/unit/test_access_resolution.py
git commit -m "feat: derive a request's grant from the caller's groups"
```

---

## Task 2: Parsing the document

**Files:**
- Modify: `src/kingfisher/domain/access.py` (add `parse`, `AccessError` messages)
- Test: `tests/unit/test_access_format.py`

**Interfaces:**
- Consumes: `Access`, `Audience`, `AccessError`, `CONTROLLED`, `DECLINED` from Task 1; `kingfisher.domain.fields.unrecognised`
- Produces: `parse(document: Mapping[str, object], source: str) -> Access`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_access_format.py`:

```python
"""The `access.yaml` document: what it may say, and what it refuses.

Decoded fields in, `Access` out. Reading YAML is `infrastructure`'s job, so
these are plain dicts -- the same seam `domain.agent.parse` sits on.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.access import Access, AccessError, parse
from kingfisher.domain.capabilities import ALL


def test_a_whole_document_parses():
    access = parse(
        {
            "groups": {"A": {}, "B": {}, "admin": {"contains": ["A", "B"]}},
            "agents": {"assistant": ["A", "B"]},
            "subagents": {"reviewer": ["A", "B"]},
            "tools": {"sql_query": ["A"], "http_fetch": ["*"]},
        },
        source="access.yaml",
    )
    assert access.groups["admin"] == ("admin", "A", "B")
    assert access.entries["tools"]["http_fetch"] == ALL
    assert access.entries["tools"]["sql_query"] == ("A",)


def test_groups_may_be_written_as_a_bare_list():
    """The common case has no `contains`, and should not need a mapping."""
    access = parse({"groups": ["A", "B"]}, source="access.yaml")
    assert access.groups == {"A": ("A",), "B": ("B",)}


def test_a_missing_groups_section_is_refused():
    """The vocabulary is what everything else is checked against."""
    with pytest.raises(AccessError, match="groups"):
        parse({"tools": {"sql_query": ["A"]}}, source="access.yaml")


def test_an_asset_naming_an_undeclared_group_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"groups": ["A"], "tools": {"sql_query": ["A", "Q"]}}, source="access.yaml")


def test_contains_naming_an_undeclared_group_is_refused():
    with pytest.raises(AccessError, match="'Q'"):
        parse({"groups": {"A": {"contains": ["Q"]}}}, source="access.yaml")


def test_a_cycle_in_contains_is_refused_naming_the_whole_loop():
    """The message names every link, because one edge does not say which to cut."""
    document = {"groups": {"A": {"contains": ["B"]}, "B": {"contains": ["A"]}}}
    with pytest.raises(AccessError, match="A -> B -> A"):
        parse(document, source="access.yaml")


def test_contains_is_transitive():
    document = {"groups": {"A": {}, "B": {"contains": ["A"]}, "C": {"contains": ["B"]}}}
    assert set(parse(document, source="access.yaml").groups["C"]) == {"A", "B", "C"}


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(AccessError, match="tolls"):
        parse({"groups": ["A"], "tolls": {}}, source="access.yaml")


def test_a_skills_section_is_refused_with_its_own_reason():
    """Not a generic 'unknown key', which reads as 'not supported yet' and
    sends a reader looking for a workaround."""
    with pytest.raises(AccessError, match="guidance rather than a capability"):
        parse({"groups": ["A"], "skills": {"code-review": ["A"]}}, source="access.yaml")


def test_a_builtin_tools_section_is_refused_with_its_own_reason():
    with pytest.raises(AccessError, match="deepagents registers them"):
        parse({"groups": ["A"], "builtin_tools": {"execute": ["A"]}}, source="access.yaml")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    """`sql_query: A` would otherwise become the groups 'A' spelled one letter
    at a time, which is the mistake `capabilities._normalise` also refuses."""
    with pytest.raises(AccessError, match="a list of group names"):
        parse({"groups": ["A"], "tools": {"sql_query": "A"}}, source="access.yaml")


def test_a_star_mixed_with_names_is_refused():
    """It cannot mean both 'everyone' and 'these', and the file should say so."""
    with pytest.raises(AccessError, match="cannot mean both"):
        parse({"groups": ["A"], "tools": {"sql_query": ["*", "A"]}}, source="access.yaml")


def test_an_empty_audience_is_refused():
    """`sql_query: []` reads as 'nobody', which is spelled by leaving it out --
    and is far more likely to be an unfinished edit."""
    with pytest.raises(AccessError, match="leave it out"):
        parse({"groups": ["A"], "tools": {"sql_query": []}}, source="access.yaml")


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AccessError, match="policy.yaml"):
        parse({"tools": {}}, source="policy.yaml")


def test_a_document_with_only_groups_is_valid():
    """A vocabulary and no entries is a policy that grants nothing, which is a
    legitimate starting point and not an error."""
    assert parse({"groups": ["A"]}, source="access.yaml") == Access(
        groups={"A": ("A",)}, entries={"agents": {}, "subagents": {}, "tools": {}}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_format.py -v`
Expected: FAIL, `ImportError: cannot import name 'parse'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/kingfisher/domain/access.py` (and add `from kingfisher.domain import fields` to the imports):

```python
def _vocabulary(raw: object, source: str) -> dict[str, tuple[str, ...]]:
    """The declared groups and what each contains, before expansion.

    Two spellings, because the common case has no `contains` and should not
    have to write an empty mapping to say so. A list is the short form; a
    mapping is the long one. Both produce the same thing.
    """
    if raw is None:
        msg = (
            f"{source}: missing required section 'groups'; it is the closed "
            f"vocabulary every other section is checked against"
        )
        raise AccessError(msg)
    if isinstance(raw, list):
        return {str(name): () for name in raw}
    if not isinstance(raw, Mapping):
        msg = f"{source}: 'groups' is a list of names, or a mapping of name to {{contains: [...]}}"
        raise AccessError(msg)

    declared: dict[str, tuple[str, ...]] = {}
    for name, body in raw.items():
        if body is None or body == {}:
            declared[str(name)] = ()
            continue
        if not isinstance(body, Mapping):
            msg = f"{source}: group {name!r} is {{contains: [...]}}, or empty"
            raise AccessError(msg)
        if complaint := fields.unrecognised(body, known={"contains"}, noun="key"):
            msg = f"{source}: group {name!r}: {complaint}"
            raise AccessError(msg)
        contains = body.get("contains") or ()
        if isinstance(contains, str):
            msg = f"{source}: group {name!r}: 'contains' is a list of group names"
            raise AccessError(msg)
        declared[str(name)] = tuple(str(one) for one in contains)
    return declared


def _closed(declared: Mapping[str, tuple[str, ...]], source: str) -> dict[str, tuple[str, ...]]:
    """Each group's transitive closure, itself included, with cycles refused.

    Depth-first with the path carried, so a cycle is reported as the whole loop
    rather than as one edge of it -- the same reason `subagent.rules` names
    every link: one edge does not tell a reader which to cut, and they may own
    none of the groups involved.
    """
    for name, contains in declared.items():
        for one in contains:
            if one not in declared:
                msg = (
                    f"{source}: group {name!r} contains {one!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)

    closure: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: tuple[str, ...]) -> tuple[str, ...]:
        if name in path:
            loop = " -> ".join((*path[path.index(name):], name))
            msg = (
                f"{source}: groups contain themselves: {loop}. Expansion "
                f"follows every link, so a loop would never finish -- one of "
                f"these has to stop containing the next"
            )
            raise AccessError(msg)
        if name in closure:
            return closure[name]
        reached: list[str] = [name]
        for one in declared[name]:
            reached.extend(n for n in walk(one, (*path, name)) if n not in reached)
        # Not memoised while a cycle is still possible below it: `closure` is
        # only written once the whole subtree returned without raising.
        closure[name] = tuple(reached)
        return closure[name]

    return {name: walk(name, ()) for name in declared}


def _audience(raw: object, *, kind: str, asset: str, known: Mapping[str, object],
              source: str) -> Audience:
    """One asset's group list, checked against the vocabulary."""
    where = f"{source}: {kind} {asset!r}"
    if isinstance(raw, str):
        # A bare string is one name, or a typo for `*`. Iterating its
        # characters -- the default for a `for` over a `str` -- is the worst
        # available answer, and is the mistake `capabilities._normalise`
        # refuses for the same reason.
        msg = f"{where}: a list of group names, or [\"*\"] for everyone -- got {raw!r}"
        raise AccessError(msg)
    if not isinstance(raw, list):
        msg = f"{where}: a list of group names, or [\"*\"] for everyone -- got {raw!r}"
        raise AccessError(msg)
    names = tuple(str(one) for one in raw)
    if not names:
        msg = (
            f"{where}: an empty list would mean nobody, which is what leaving "
            f"the entry out already means -- leave it out, or name the groups"
        )
        raise AccessError(msg)
    if ALL in names and len(names) > 1:
        msg = f"{where}: [\"*\"] is everyone, so it cannot mean both that and {names}"
        raise AccessError(msg)
    if names == (ALL,):
        return ALL
    if unknown := tuple(one for one in names if one not in known):
        msg = (
            f"{where}: names undeclared group(s) {', '.join(repr(u) for u in sorted(set(unknown)))}; "
            f"this file defines {', '.join(sorted(known))}"
        )
        raise AccessError(msg)
    return names


def parse(document: Mapping[str, object], source: str) -> Access:
    """One policy document, from its decoded fields.

    Takes a mapping rather than a path: reading YAML needs a library and this
    is `domain/`. `infrastructure.access_policy` does that half.

    A section this format does not define is refused rather than dropped, for
    the reason every format here gives: a key we ignore is a key the author
    believes took effect. Three sections get their *own* refusal, because a
    generic "unknown key" reads as "not supported yet" and sends someone
    looking for a workaround that does not exist.
    """
    complaint = fields.unrecognised(
        document, known={"groups", *CONTROLLED}, declined=DECLINED, noun="section"
    )
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)

    groups = _closed(_vocabulary(document.get("groups"), source), source)

    entries: dict[str, dict[str, Audience]] = {}
    for kind in CONTROLLED:
        section = document.get(kind) or {}
        if not isinstance(section, Mapping):
            msg = f"{source}: {kind!r} is a mapping of name to a list of groups"
            raise AccessError(msg)
        entries[kind] = {
            str(asset): _audience(raw, kind=kind, asset=str(asset), known=groups, source=source)
            for asset, raw in section.items()
        }
    return Access(groups=groups, entries=entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_format.py tests/unit/test_access_resolution.py -v`
Expected: PASS.

If `test_a_skills_section_is_refused_with_its_own_reason` fails because `fields.unrecognised` puts the declined reason somewhere the regex misses, read `domain/fields.py::_explain` and adjust the test's `match=` to the substring it actually emits — the *reason text* must appear; where it sits in the sentence is `fields`' business, not this format's.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/domain/access.py tests/unit/test_access_format.py
git commit -m "feat: read an access policy document, refusing what it does not define"
```

---

## Task 3: Loading the file

**Files:**
- Create: `src/kingfisher/infrastructure/access_policy.py`
- Test: `tests/unit/test_access_format.py` (append)

**Interfaces:**
- Consumes: `parse`, `AccessError` from Task 2
- Produces: `load(path: Path) -> Access | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_access_format.py`:

```python
def test_an_absent_file_is_no_policy_rather_than_an_error(tmp_path):
    """Decision: absent means the feature is off, so every existing deployment
    is unaffected by the code landing."""
    from kingfisher.infrastructure import access_policy

    assert access_policy.load(tmp_path / "access.yaml") is None


def test_a_present_file_is_read(tmp_path):
    from kingfisher.infrastructure import access_policy

    written = tmp_path / "access.yaml"
    written.write_text("groups: [A, B]\ntools:\n  sql_query: [A]\n", encoding="utf-8")
    access = access_policy.load(written)
    assert access is not None
    assert access.entries["tools"]["sql_query"] == ("A",)


def test_a_malformed_file_refuses_rather_than_starting_open(tmp_path):
    """Fail closed: a policy that will not parse must not become no policy."""
    from kingfisher.infrastructure import access_policy

    written = tmp_path / "access.yaml"
    written.write_text("groups: [A]\ntools: {sql_query: [\n", encoding="utf-8")
    with pytest.raises(AccessError, match="access.yaml"):
        access_policy.load(written)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path):
    from kingfisher.infrastructure import access_policy

    written = tmp_path / "access.yaml"
    written.write_text("- A\n- B\n", encoding="utf-8")
    with pytest.raises(AccessError, match="mapping"):
        access_policy.load(written)


def test_an_empty_file_is_refused_rather_than_read_as_no_policy(tmp_path):
    """A file someone created and has not filled in is not the same as no file,
    and reading it as 'off' is the silent-open failure this area is about."""
    from kingfisher.infrastructure import access_policy

    written = tmp_path / "access.yaml"
    written.write_text("", encoding="utf-8")
    with pytest.raises(AccessError, match="empty"):
        access_policy.load(written)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_format.py -k "file or policy" -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kingfisher.infrastructure.access_policy'`

- [ ] **Step 3: Write minimal implementation**

Create `src/kingfisher/infrastructure/access_policy.py`:

```python
"""Reading `access.yaml`: which groups this deployment has, and who reaches what.

The `infrastructure` half of a split `domain.access` states: that module owns
the rule and may not read a file, this one owns the file and owns no rule. The
same seam `Models` and `model_catalogue` sit on, for the same reason -- a
domain module imports the standard library and `kingfisher.domain`, nothing
else, and a test enforces it.

`safe_load`, for the reason `model_catalogue` gives: this document is
operator-authored rather than caller-supplied, but it is read at startup and
`yaml.load` would let a crafted file construct arbitrary objects before
anything else runs.

**An absent file is no policy; an unreadable one is a refusal.** The two are
not the same and must never collapse into each other: a deployment that never
wrote a policy has none, and a deployment whose policy will not parse has one
it cannot honour. Reading the second as the first is the failure this whole
area exists to avoid -- a server that comes up wide open because a file had a
tab in it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from kingfisher.domain.access import Access, AccessError, parse

if TYPE_CHECKING:
    from pathlib import Path


def load(path: Path) -> Access | None:
    """The policy at `path`, or `None` if there is no file there."""
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path}: is not valid YAML -- {exc}"
        raise AccessError(msg) from exc
    except OSError as exc:
        msg = f"{path}: cannot be read -- {exc}"
        raise AccessError(msg) from exc

    if document is None:
        msg = (
            f"{path}: is empty. A policy file that exists but says nothing is "
            f"not the same as no policy -- delete it, or give it a 'groups' section"
        )
        raise AccessError(msg)
    if not isinstance(document, dict):
        msg = f"{path}: is a mapping of sections, not {type(document).__name__}"
        raise AccessError(msg)
    return parse(document, source=path.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_format.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the layer rules still hold**

Run: `pytest tests/unit/test_architecture.py -q`
Expected: PASS. `infrastructure` already declares `yaml` in `THIRD_PARTY`, so no table change is needed.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/infrastructure/access_policy.py tests/unit/test_access_format.py
git commit -m "feat: load access.yaml, refusing a file that will not parse"
```

---

## Task 4: `Config.access` and the environment variable

**Files:**
- Modify: `src/kingfisher/config.py`
- Modify: `src/kingfisher/application/config.py:133` (beside `models_file`)
- Modify: `.env.example`
- Test: `tests/unit/test_config.py` (append)

**Interfaces:**
- Consumes: `access_policy.load` from Task 3
- Produces: `Config.access: Access | None`; `KINGFISHER_ACCESS_FILE`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_a_workspace_without_a_policy_file_has_no_policy(tmp_path):
    from kingfisher.application.config import from_env

    (tmp_path / "models.yaml").write_text(
        "endpoints:\n  local: {api: openai, base_url: http://x, key_env: K}\n"
        "models:\n  m: {endpoint: local}\ndefault: m\n",
        encoding="utf-8",
    )
    cfg = from_env({"KINGFISHER_WORKSPACE": str(tmp_path), "K": "k"})
    assert cfg.access is None


def test_a_policy_beside_models_yaml_is_read(tmp_path):
    from kingfisher.application.config import from_env

    (tmp_path / "models.yaml").write_text(
        "endpoints:\n  local: {api: openai, base_url: http://x, key_env: K}\n"
        "models:\n  m: {endpoint: local}\ndefault: m\n",
        encoding="utf-8",
    )
    (tmp_path / "access.yaml").write_text("groups: [A]\ntools:\n  t: [A]\n", encoding="utf-8")
    cfg = from_env({"KINGFISHER_WORKSPACE": str(tmp_path), "K": "k"})
    assert cfg.access is not None
    assert cfg.access.entries["tools"] == {"t": ("A",)}


def test_the_policy_file_can_be_relocated(tmp_path):
    """A policy can be deployed once and shared, the way a catalogue can."""
    from kingfisher.application.config import from_env

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "models.yaml").write_text(
        "endpoints:\n  local: {api: openai, base_url: http://x, key_env: K}\n"
        "models:\n  m: {endpoint: local}\ndefault: m\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "policy.yaml"
    elsewhere.write_text("groups: [B]\ntools:\n  t: [B]\n", encoding="utf-8")
    cfg = from_env({
        "KINGFISHER_WORKSPACE": str(workspace),
        "KINGFISHER_ACCESS_FILE": str(elsewhere),
        "K": "k",
    })
    assert cfg.access is not None
    assert cfg.access.groups == {"B": ("B",)}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -k policy -v`
Expected: FAIL, `AttributeError: 'Config' object has no attribute 'access'`

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/config.py`, add the import and the field. The import is of a `domain` module, which the area table permits (`""` restricts third-party imports only):

```python
from kingfisher.domain.access import Access
```

Add to `Config`, immediately after `models: Models`:

```python
    #: Which groups reach which assets, or `None` where no policy is written.
    #:
    #: Beside `models` because it is the same kind of thing: a static,
    #: operator-authored table read once at startup, whose absence is a
    #: legitimate state rather than a missing setting. `None` is the whole of
    #: what "this deployment does not control access by group" means, and every
    #: deployment that existed before this field had exactly that.
    #:
    #: Read once rather than per turn, unlike the catalogue. The catalogue is
    #: re-read because a workspace directory is edited between turns and a
    #: stale view of it is a wrong answer; a policy is a deployment setting, and
    #: a revocation lands on restart the way every other one here does.
    access: Access | None = None
```

In `src/kingfisher/application/config.py`, beside the `models_file` line (~133):

```python
    access_file = _optional_path("KINGFISHER_ACCESS_FILE") or workspace / "access.yaml"
```

and add to the `Config(...)` construction:

```python
        access=access_policy.load(access_file),
```

with `from kingfisher.infrastructure import access_policy` at the top.

In `.env.example`, beside the `KINGFISHER_MODELS_FILE` entry:

```bash
# Which user groups may reach which agents, subagents and tools. Unset, and with
# no `access.yaml` in the workspace, kingfisher controls nothing by group and
# behaves exactly as it did before this file existed.
#
# Once a policy exists, every call must say who is calling -- `--as A,B` on the
# command, `kf.for_groups([...])` in the library -- or be refused. Running with
# no caller at all is spelled `for_groups(UNSCOPED)`, which is deliberate and
# greppable rather than the default.
# KINGFISHER_ACCESS_FILE=/etc/kingfisher/access.yaml
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS, including `test_every_variable_read_is_documented`.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/config.py src/kingfisher/application/config.py .env.example tests/unit/test_config.py
git commit -m "feat: read the access policy into Config, beside the model catalogue"
```

---

## Task 5: Reconciling the policy against the catalogue

**Files:**
- Modify: `src/kingfisher/domain/access.py` (add `AccessReport`, `Access.reconciled`)
- Test: `tests/unit/test_access_reports.py`

**Interfaces:**
- Consumes: `Access` from Task 1
- Produces:
  - `AccessReport(listed_not_offered: tuple[tuple[str, str], ...], offered_unreachable: tuple[tuple[str, str], ...])`
  - `AccessReport.lines() -> tuple[str, ...]`
  - `AccessReport.is_clean -> bool`
  - `Access.reconciled(offered: Mapping[str, Iterable[str]]) -> tuple[Access, AccessReport]`

**Why this exists:** a grant naming a tool the workspace does not offer is *refused* one layer down — `Offering.refuse_unknown` at `infrastructure/harness/agent.py:998`. So a stale policy line would turn every turn into a refusal, which contradicts decision 12. Dropping stale entries here is what makes "report, don't fail" true.

**A name is matched as `workspace_tool_names` writes it.** That function returns *written* forms, not bare ones: where two files define one `fetch`, it yields `vendor_a/fetch.py::fetch` and `vendor_b/fetch.py::fetch`, because a bare `fetch` would name two tools and the loader refuses to pick. So a policy line saying `fetch:` against such a workspace is genuinely stale and is reported as such — which is the right answer, since granting it would have to mean one of the two and there is nothing to say which. Write the policy key exactly as `kingfisher list` prints it. This is the same rule `subagents`' `tools:` field already follows, and the reason `docs/formats.md` tells authors to write the left-hand side as `--list` prints it. Add a test for it:

```python
def test_an_ambiguous_bare_name_is_reported_rather_than_guessed():
    """Two files defining one `fetch` are offered under their written forms, so
    a policy naming the bare name matches neither -- and must say so rather
    than silently grant whichever came first."""
    access = policy(tools={"fetch": ("A",)})
    reconciled, report = access.reconciled(
        {"tools": ["vendor_a/fetch.py::fetch", "vendor_b/fetch.py::fetch"],
         "subagents": [], "agents": []}
    )
    assert report.listed_not_offered == (("tools", "fetch"),)
    assert reconciled.resolve(["A"]).tools == ()
```

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_access_reports.py`:

```python
"""What the policy and the workspace disagree about, said once at startup.

Two halves of one rule, read in both directions: a line naming an asset that is
not there, and an asset there that no line names. Neither is fatal, and both
are the kind of drift that is otherwise discovered by a confused user months
later.
"""

from __future__ import annotations

from kingfisher.domain.access import Access
from kingfisher.domain.capabilities import ALL


def policy(**entries: dict[str, object]) -> Access:
    named = {g for audience in entries.values() for a in audience.values()
             if a != ALL for g in a}
    return Access(
        groups={name: (name,) for name in sorted(named)},
        entries={kind: dict(value) for kind, value in entries.items()},
    )


def test_a_clean_policy_reports_nothing():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled({"tools": ["sql_query"], "subagents": [], "agents": []})
    assert report.is_clean
    assert report.lines() == ()


def test_a_line_naming_an_asset_that_is_gone_is_reported():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled({"tools": ["sql"], "subagents": [], "agents": []})
    assert report.listed_not_offered == (("tools", "sql_query"),)


def test_a_stale_line_is_dropped_so_a_grant_never_names_a_missing_tool():
    """The grant reaches `Offering.refuse_unknown`, which refuses a name the
    workspace does not offer -- so a stale line left in place would turn every
    turn into a refusal rather than a report."""
    access = policy(tools={"sql_query": ("A",), "http_fetch": ("A",)})
    reconciled, _ = access.reconciled(
        {"tools": ["http_fetch"], "subagents": [], "agents": []}
    )
    assert reconciled.resolve(["A"]).tools == ("http_fetch",)


def test_an_asset_no_group_can_reach_is_reported():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(
        {"tools": ["sql_query", "pdf_export"], "subagents": [], "agents": []}
    )
    assert report.offered_unreachable == (("tools", "pdf_export"),)


def test_a_rename_produces_both_halves_at_once():
    """The case the two halves exist to make legible together."""
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled({"tools": ["sql"], "subagents": [], "agents": []})
    assert report.listed_not_offered == (("tools", "sql_query"),)
    assert report.offered_unreachable == (("tools", "sql"),)


def test_the_report_reads_as_sentences():
    access = policy(tools={"sql_query": ("A",)})
    _, report = access.reconciled(
        {"tools": ["pdf_export"], "subagents": [], "agents": []}
    )
    rendered = "\n".join(report.lines())
    assert "listed but not offered" in rendered
    assert "no group can reach" in rendered
    assert "sql_query" in rendered
    assert "pdf_export" in rendered


def test_reconciling_does_not_mutate_the_original():
    access = policy(tools={"sql_query": ("A",)})
    access.reconciled({"tools": [], "subagents": [], "agents": []})
    assert access.entries["tools"] == {"sql_query": ("A",)}


def test_a_star_audience_reaches_everything_so_is_never_unreachable():
    access = policy(tools={"http_fetch": ALL})
    _, report = access.reconciled({"tools": ["http_fetch"], "subagents": [], "agents": []})
    assert report.offered_unreachable == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_reports.py -v`
Expected: FAIL, `AttributeError: 'Access' object has no attribute 'reconciled'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/kingfisher/domain/access.py`:

```python
@dataclass(frozen=True)
class AccessReport:
    """Where the policy and the workspace disagree, as `(kind, name)` pairs.

    Two halves of one rule read in opposite directions, which is the shape
    `withheld` and `all_but` already have in `capabilities`: one turns a grant
    into what it leaves out, the other turns what to leave out into a grant.

    Neither half is fatal. A stale line grants nothing, so it cannot be wrong
    in the dangerous direction, and refusing to start over one would couple a
    policy deploy to a catalogue deploy -- removing a tool would take the
    server down until someone edited a file they may not own. An unreachable
    asset is the whitelist going stale on its own, which is exactly what
    `withheld` exists to say out loud rather than leave for a confused user.
    """

    #: Policy lines naming an asset this workspace does not offer.
    listed_not_offered: tuple[tuple[str, str], ...] = ()
    #: Assets this workspace offers that no group can reach.
    offered_unreachable: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.listed_not_offered and not self.offered_unreachable

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing.

        Lines rather than prints, for the reason `presentation.cli.listing`
        gives: a library that writes to stdout cannot be used by a server, and
        both reach this.
        """
        if self.is_clean:
            return ()
        said: list[str] = ["access:"]
        for heading, pairs in (
            ("listed but not offered", self.listed_not_offered),
            ("offered, no group can reach", self.offered_unreachable),
        ):
            if not pairs:
                continue
            said.append(f"  {heading}:")
            said.extend(f"    {kind[:-1]} {name}" for kind, name in pairs)
        return tuple(said)
```

and the method on `Access`:

```python
    def reconciled(
        self, offered: Mapping[str, Iterable[str]]
    ) -> tuple[Access, AccessReport]:
        """This policy with stale entries dropped, and what the two disagree on.

        Dropping rather than keeping is load-bearing rather than tidy. The
        resolved grant reaches `Offering.refuse_unknown`, which refuses a name
        the workspace does not offer -- so a policy line left pointing at a
        deleted tool would turn every turn into a refusal instead of the report
        this returns.

        `offered` is what the catalogue actually holds, per kind, which is why
        this is called where the catalogue is known rather than where the file
        is read.
        """
        held = {kind: tuple(names) for kind, names in offered.items()}
        kept: dict[str, dict[str, Audience]] = {}
        missing: list[tuple[str, str]] = []
        for kind in CONTROLLED:
            available = set(held.get(kind, ()))
            kept[kind] = {}
            for name, audience in self.entries.get(kind, {}).items():
                if name in available:
                    kept[kind][name] = audience
                else:
                    missing.append((kind, name))

        unreachable = [
            (kind, name)
            for kind in CONTROLLED
            for name in held.get(kind, ())
            if name not in kept[kind]
        ]
        return (
            Access(groups=self.groups, entries=kept),
            AccessReport(
                listed_not_offered=tuple(missing),
                offered_unreachable=tuple(unreachable),
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_reports.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/domain/access.py tests/unit/test_access_reports.py
git commit -m "feat: reconcile the policy against the catalogue, reporting both directions"
```

---

## Task 6: `for_groups`, and the refusal when nobody says who is calling

**Files:**
- Modify: `src/kingfisher/application/service.py`
- Modify: `src/kingfisher/__init__.py`
- Test: `tests/unit/test_access_wiring.py`

**Interfaces:**
- Consumes: `Access`, `AccessError`, `UNSCOPED`, `AccessReport`
- Produces:
  - `Held = tuple[str, ...] | _Unscoped`
  - `Kingfisher.access: Access | None`, `Kingfisher.access_report: AccessReport`
  - `Kingfisher.for_groups(groups: Iterable[str] | _Unscoped) -> Caller`
  - `Kingfisher._effective_grants(groups: Held | None) -> Capabilities`
  - `Caller.run/arun/stream/astream`, same signatures as `Kingfisher`'s
  - `groups: Held | None = None` keyword on `Kingfisher.run/arun/stream/astream`

**Why groups and not a `Capabilities`:** a caller must not be able to hand in a fabricated grant. Taking group *names* means the only thing a caller can supply is an input the policy resolves; there is no spelling of "give me everything" other than `UNSCOPED`, which is explicit.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_access_wiring.py`:

```python
"""The policy reaching a run: who is calling, and what the graph is built from.

The assertion that matters most here is the last one. An ungranted tool is not
merely refused when called -- it is never attached to the graph, so the model
is never told it exists and never spends context on its schema.
"""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError


@pytest.fixture
def policied(workspace_cfg):
    """A deployment with a policy: group A reaches `line_count`, B reaches nothing.

    `workspace_cfg` is the existing fixture in `tests/conftest.py`. If its name
    differs, use whatever fixture the neighbouring tests in
    `tests/unit/test_capability_wiring.py` use to build a `Config` over a
    seeded temporary workspace, and write the policy file into `cfg.workspace`.
    """
    (workspace_cfg.workspace / "access.yaml").write_text(
        "groups: [A, B]\ntools:\n  line_count: [A]\n", encoding="utf-8"
    )
    from kingfisher.application.config import from_env

    return from_env({"KINGFISHER_WORKSPACE": str(workspace_cfg.workspace)})


def test_a_call_that_does_not_say_who_is_calling_is_refused(policied):
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="for_groups"):
        kf.run("anything")


def test_unscoped_runs_without_a_policy_and_says_so_in_the_call(policied):
    """The opt-out is a value someone typed, so it can be found in a review."""
    kf = Kingfisher(policied)
    assert kf.for_groups(UNSCOPED)._grants == kf.grants


def test_a_caller_in_a_group_gets_what_that_group_reaches(policied):
    kf = Kingfisher(policied)
    assert kf.for_groups(["A"])._grants.tools == ("line_count",)


def test_a_caller_in_another_group_gets_nothing(policied):
    kf = Kingfisher(policied)
    assert kf.for_groups(["B"])._grants.tools == ()


def test_an_unknown_group_is_refused(policied):
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="unknown group"):
        kf.for_groups(["Q"])


def test_naming_groups_where_there_is_no_policy_is_refused(workspace_cfg):
    """A caller naming groups against a deployment that controls nothing is
    confused, and silently ignoring them is how they stay confused."""
    kf = Kingfisher(workspace_cfg)
    with pytest.raises(AccessError, match="no access policy"):
        kf.for_groups(["A"])


def test_a_deployment_without_a_policy_is_unchanged(workspace_cfg):
    """Everything that worked before this feature must still work untouched."""
    kf = Kingfisher(workspace_cfg)
    assert kf.for_groups is not None
    assert kf.access is None


def test_the_handle_is_reusable(policied):
    kf = Kingfisher(policied)
    caller = kf.for_groups(["A"])
    assert caller._grants == kf.for_groups(["A"])._grants


def test_the_deployments_own_grants_still_bound_a_caller(policied):
    """Two ceilings, and the lower one wins."""
    from kingfisher.domain.capabilities import Capabilities

    kf = Kingfisher(policied, grants=Capabilities(tools=()))
    assert kf.for_groups(["A"])._grants.tools == ()


def test_an_ungranted_tool_is_not_on_the_graph_at_all(policied, tmp_path):
    """Decision: not-allowed tools are never added to the graph, rather than
    added and then refused. Asserted against the built graph, because that is
    the only honest answer to 'what was offered'."""
    from kingfisher.infrastructure.harness.runtime import registered_tools

    kf = Kingfisher(policied)
    session = tmp_path / "s"
    session.mkdir()
    graph = kf.graph_for(
        __import__("kingfisher").Request(task="t", agent="surveyor"),
        session,
        capabilities=kf.for_groups(["B"])._grants,
        checkpointer=None,
    )
    assert "line_count" not in (registered_tools(graph) or ())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_wiring.py -v`
Expected: FAIL, `AttributeError: 'Kingfisher' object has no attribute 'for_groups'`

Before writing the implementation, run `pytest tests/unit/test_capability_wiring.py --collect-only -q` and read the top of that file to find the real fixture name for a `Config` over a temporary workspace; substitute it for `workspace_cfg` throughout the test above. Do not invent a fixture.

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/application/service.py`, add the imports:

```python
from kingfisher.domain.access import UNSCOPED, Access, AccessError, AccessReport, _Unscoped
```

Add the type alias near the other module-level types:

```python
#: What a caller may say about who they are: the groups they hold, or the
#: explicit refusal to say. `None` is a third thing and means nobody said --
#: which is a mistake once a policy exists, and the reason this is not
#: `tuple[str, ...] | None`.
Held = tuple[str, ...] | _Unscoped
```

Add the `Caller` facade above `class Kingfisher`:

```python
@dataclass(frozen=True)
class Caller:
    """One `Kingfisher`, bound to who is calling.

    A handle rather than a second `Kingfisher`: the catalogue, the session
    store and the per-session locks are shared, because they are properties of
    the deployment and not of the caller. Two handles over one instance is the
    shape a process serving several callers needs, and binding one at the top
    of a script is the shape a person at a terminal needs -- the same object
    does both.

    Holds the resolved grant rather than the group names, so resolution happens
    once. The names are kept beside it for anything that has to report them.
    """

    _kf: Kingfisher
    _held: Held
    _grants: Capabilities

    def run(self, request: str | Request) -> RunResult:
        return self._kf.run(request, groups=self._held)

    async def arun(self, request: str | Request) -> RunResult:
        return await self._kf.arun(request, groups=self._held)

    def stream(self, request: str | Request) -> Iterator[RunEvent]:
        return self._kf.stream(request, groups=self._held)

    def astream(self, request: str | Request) -> AsyncIterator[RunEvent]:
        return self._kf.astream(request, groups=self._held)
```

In `Kingfisher.__init__`, after the catalogue is resolved, reconcile the policy:

```python
        # Reconciled here rather than where the file was read, because "which
        # assets exist" is the catalogue's answer and the catalogue is not
        # known until now. Stale entries are dropped: the grant they would
        # produce reaches `Offering.refuse_unknown`, which refuses a name the
        # workspace does not offer, so a line pointing at a deleted tool would
        # turn every turn into a refusal rather than the report below.
        self.access: Access | None = None
        self.access_report: AccessReport = AccessReport()
        if self.cfg.access is not None:
            self.access, self.access_report = self.cfg.access.reconciled(self._offered_names())
```

Add the helper and the two methods to `Kingfisher`:

```python
    def _offered_names(self) -> dict[str, tuple[str, ...]]:
        """What the catalogue holds, per controlled kind, for reconciliation.

        Subagents and agents come off the catalogue directly. Tools are the
        workspace's own -- built-ins are not controlled here and have no file.
        """
        return {
            "agents": tuple(self.catalogue.agents.specs),
            "subagents": tuple(defined_subagents(self.cfg, None, catalogue=self.catalogue)),
            "tools": tuple(workspace_tool_names(self.cfg, catalogue=self.catalogue)),
        }

    def for_groups(self, groups: Iterable[str] | _Unscoped) -> Caller:
        """This deployment, bound to a caller holding these groups.

        The one place a caller's identity enters. It takes group *names* rather
        than a `Capabilities` on purpose: a name is resolved against a policy
        this deployment wrote, so the only thing a caller can supply is an
        input, never a grant. There is no spelling of "everything" here other
        than `UNSCOPED`, which is a value someone typed and a reviewer can find.
        """
        held: Held = groups if isinstance(groups, _Unscoped) else tuple(groups)
        return Caller(_kf=self, _held=held, _grants=self._effective_grants(held))

    def _effective_grants(self, groups: Held | None) -> Capabilities:
        """The ceiling for one call: this deployment's, narrowed by the caller's.

        Four states, and the third is why this exists. No policy and no groups
        is every deployment that predates this feature. No policy but groups
        named is a caller who thinks access is controlled here and is wrong,
        which is worth saying rather than ignoring. A policy and no groups is a
        call that did not say who was making it -- refused, because the
        alternative is a handler that forgot the boundary granting everything
        in silence. A policy and groups is the ordinary case.
        """
        if self.access is None:
            if groups is not None:
                msg = (
                    "this deployment has no access policy, so naming groups "
                    "means nothing here -- write access.yaml in the workspace, "
                    "or set KINGFISHER_ACCESS_FILE"
                )
                raise AccessError(msg)
            return self.grants
        if groups is None:
            msg = (
                "this deployment has an access policy, so a call must say who "
                "is calling: for_groups([...]) with the caller's groups, or "
                "for_groups(UNSCOPED) to run without one"
            )
            raise AccessError(msg)
        if isinstance(groups, _Unscoped):
            return self.grants
        return self.grants.intersect(self.access.resolve(groups))
```

Add `groups: Held | None = None` as a keyword-only parameter to `run`, `arun`, `stream` and `astream`, and thread it to `_admit`. At `service.py:1140`, replace:

```python
        allowed = self.grants.intersect(request.capabilities).including(
```

with:

```python
        allowed = self._effective_grants(groups).intersect(request.capabilities).including(
```

`_admit` and any private method between the entry points and line 1140 take `groups: Held | None` and pass it down unchanged.

In `src/kingfisher/__init__.py`, export the vocabulary:

```python
from kingfisher.domain.access import UNSCOPED, Access, AccessError, AccessReport
```

and add `"UNSCOPED"`, `"Access"`, `"AccessError"`, `"AccessReport"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_access_wiring.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Run the whole suite — nothing that worked may have stopped**

Run: `pytest -q`
Expected: PASS. Every existing test builds a `Kingfisher` with no policy, so `_effective_grants(None)` returns `self.grants` and behaviour is unchanged. A failure here means the `groups=None` default did not reach one of the four entry points.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/application/service.py src/kingfisher/__init__.py tests/unit/test_access_wiring.py
git commit -m "feat: bind a caller's groups to a run, refusing a call that names none"
```

---

## Task 7: Out of reach reads as not offered

**Files:**
- Modify: `src/kingfisher/application/service.py:222-291` (`_withheld_by_kind`)
- Test: `tests/unit/test_access_wiring.py` (append)

**Interfaces:**
- Consumes: `Access` from Task 1, `Caller` from Task 6
- Produces: `_withheld_by_kind(..., reach: Access | None, held: frozenset[str] | None)`

**Why:** `withheld(granted, offered=...)` reports every offered name a grant leaves out. With a group-derived grant, that list *is* everything the caller's groups denied them — so returning it to the caller re-leaks exactly what decision 15 hides. The offered set must be filtered before the comparison, not after.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_access_wiring.py`:

```python
def test_a_caller_is_not_told_about_assets_their_groups_deny(policied, tmp_path):
    """Decision 15: out of reach reads as not offered. The withheld report is
    where that leaks if it is going to -- it names, by design, every offered
    thing a grant left out."""
    kf = Kingfisher(policied)
    result = kf.for_groups(["B"]).run(
        __import__("kingfisher").Request(task="say hi", agent="surveyor")
    )
    reported = " ".join(
        name for _kind, names in getattr(result, "withheld", ()) for name in names
    )
    assert "line_count" not in reported


def test_a_caller_is_still_told_about_what_they_narrowed_themselves(policied):
    """The report must not go silent altogether: a caller who declined a tool
    they could have had should still hear that they did."""
    from kingfisher import Capabilities, Request

    kf = Kingfisher(policied)
    result = kf.for_groups(["A"]).run(
        Request(task="say hi", agent="surveyor", capabilities=Capabilities(tools=()))
    )
    reported = " ".join(
        name for _kind, names in getattr(result, "withheld", ()) for name in names
    )
    assert "line_count" in reported
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_wiring.py -k "denied or narrowed" -v`
Expected: FAIL on the first — `line_count` appears in the withheld report for a caller in group B.

- [ ] **Step 3: Write minimal implementation**

In `service.py`, give `_withheld_by_kind` the caller's reach and filter each offered set through it. Change the signature to:

```python
def _withheld_by_kind(
    allowed: Capabilities,
    cfg: Config,
    session_dir: Path,
    graph: Any,
    catalogue: Definitions,
    *,
    reach: Access | None = None,
    held: frozenset[str] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
```

Extend the docstring with the reason:

```
    `reach` is what the caller's groups make visible, and it filters "what the
    workspace offers" before the comparison rather than after. That ordering is
    the whole of it: this function names every offered thing a grant left out,
    so measured against the unfiltered catalogue it would hand a caller a list
    of precisely the assets their groups denied them -- re-leaking what the
    filtering elsewhere exists to hide. An asset out of reach is not withheld
    from this caller; as far as they are concerned it is not offered.
```

Add the filter, and apply it at the one place the offered names are read:

```python
    def visible(kind: str, names: tuple[str, ...]) -> tuple[str, ...]:
        """`names`, less anything this caller's groups do not reach."""
        if reach is None or held is None or kind not in reach.entries:
            return names
        within = set(reach.reachable(kind, held))
        return tuple(name for name in names if name in within)
```

and in the loop, replace `names_of()` with the filtered call. The `offered` tuple gains the policy kind each axis maps to — `builtin_tools` and `skills` are uncontrolled and map to `None`:

```python
    found = []
    for what, field, kind, names_of in offered:
        granted = getattr(allowed, field)
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=visible(kind, names_of())):
            found.append((what, left_out))
    return tuple(found)
```

with the `offered` tuple updated so each row carries its policy kind:

```python
        ("builtin tool", "builtin_tools", None, lambda: tuple(...)),
        ("tool", "tools", "tools", lambda: workspace),
        ("skill", "skills", None, lambda: available_skills(...)),
        ("subagent", "subagents", "subagents", lambda: tuple(defined_subagents(...))),
```

At the call site (`service.py:1173`), pass the reach:

```python
            withheld=_withheld_by_kind(
                allowed, cfg, session.directory, graph, self.catalogue,
                reach=self.access,
                held=None if groups is None or isinstance(groups, _Unscoped)
                else self.access.expand(groups) if self.access is not None else None,
            ),
```

If that expression is hard to read at the call site, compute `held` once beside `allowed` and pass the local — it is used twice and reads better named.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_access_wiring.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: PASS. `reach=None` is the default, so every existing caller is unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/application/service.py tests/unit/test_access_wiring.py
git commit -m "fix: measure what was withheld against what the caller can see"
```

---

## Task 8: The command, the listing, and the documentation

**Files:**
- Modify: `src/kingfisher/application/inventory.py`
- Modify: `src/kingfisher/presentation/cli/listing.py`
- Modify: `src/kingfisher/presentation/cli/__main__.py`
- Modify: `docs/formats.md`
- Test: `tests/unit/test_cli.py` (append)

**Interfaces:**
- Consumes: `Access`, `AccessReport`, `UNSCOPED`
- Produces: `--as A,B` on `kingfisher list` and `kingfisher run`; `Inventory.access`, `Inventory.access_report`, `Inventory.held`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cli.py`:

```python
def test_list_unscoped_shows_everything_with_who_reaches_it(policied_workspace, capsys):
    """Decision 18: the unscoped listing is the operator's audit view. It is
    exempt from the refusal that covers a *turn*, because it is read-only and
    the operator can read access.yaml anyway.

    `policied_workspace` is a tmp workspace with an access.yaml. Build it the
    way the neighbouring CLI tests build a workspace; do not invent a fixture.
    """
    from kingfisher.presentation.cli.__main__ import main

    main(["list", "--workspace", str(policied_workspace)])
    shown = capsys.readouterr().out
    assert "line_count" in shown
    assert "[A]" in shown


def test_list_as_a_group_shows_only_what_that_group_reaches(policied_workspace, capsys):
    from kingfisher.presentation.cli.__main__ import main

    main(["list", "--workspace", str(policied_workspace), "--as", "B"])
    shown = capsys.readouterr().out
    assert "line_count" not in shown


def test_list_reports_an_asset_no_group_can_reach(policied_workspace, capsys):
    from kingfisher.presentation.cli.__main__ import main

    main(["list", "--workspace", str(policied_workspace)])
    shown = capsys.readouterr().out
    assert "no group can reach" in shown


def test_run_without_as_is_refused_where_a_policy_exists(policied_workspace):
    from kingfisher.presentation.cli.__main__ import main

    code = main(["run", "--workspace", str(policied_workspace), "hello"])
    assert code != 0


def test_as_accepts_a_comma_separated_list():
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("A,B") == ("A", "B")
    assert _held(" A , B ") == ("A", "B")


def test_as_unscoped_is_spelled_out_rather_than_implied():
    from kingfisher.domain.access import UNSCOPED
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("UNSCOPED") is UNSCOPED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli.py -k "policied or as_" -v`
Expected: FAIL, unrecognised argument `--as`.

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/presentation/cli/__main__.py`, add the parser helper:

```python
def _held(raw: str) -> tuple[str, ...] | _Unscoped:
    """`--as A,B` as the groups it names, or the explicit absence of any.

    `UNSCOPED` is spelled out rather than being what an empty value means: an
    empty `--as` is far more likely to be a shell variable that did not expand
    than a considered decision to run with no caller at all.
    """
    if raw.strip() == "UNSCOPED":
        return UNSCOPED
    return tuple(part.strip() for part in raw.split(",") if part.strip())
```

Add `--as` (destination `held`, `type=_held`, `default=None`) to both the `list` and `run` subparsers, with help text: `who is calling: comma-separated groups, or UNSCOPED to run without a caller`.

For `run`, call `kf.for_groups(args.held).run(...)` when `args.held is not None`, and `kf.run(...)` otherwise — the refusal in `_effective_grants` produces the non-zero exit, so no branch is needed for the policy case.

For `list`, pass the policy and the groups into the `Inventory`, then into `render`.

In `src/kingfisher/application/inventory.py`, add three fields to `Inventory`:

```python
    #: The reconciled policy, or `None` where this deployment has none.
    access: Access | None = None
    #: What the policy and the catalogue disagree about. Empty when clean.
    access_report: AccessReport = field(default_factory=AccessReport)
    #: Whose view this is, or `None` for the operator's view of everything.
    held: frozenset[str] | None = None
```

In `src/kingfisher/presentation/cli/listing.py`, add the audience suffix and the report:

```python
def _audience(found: Inventory, kind: str, name: str) -> str:
    """Who reaches this asset, printed only where a policy says.

    Silent with no policy, so a deployment that controls nothing by group sees
    the listing it always saw. `(no group)` is the one that matters: it is an
    asset present on disk that nobody can use, which is what a whitelist going
    stale looks like from the outside.
    """
    if found.access is None or found.held is not None:
        return ""
    audience = found.access.entries.get(kind, {}).get(name)
    if audience is None:
        return "  (no group)"
    return "  [*]" if audience == ALL else f"  [{', '.join(audience)}]"
```

Call it from `_agents` and from the tools and subagents sections, appended after `_from(...)`. At the end of `render`, emit the report:

```python
    yield from found.access_report.lines()
```

Filtering for `--as` happens where the `Inventory` is built: when `held` is set, each kind's names are narrowed by `access.reachable(kind, held)` before being placed on the record, so `render` needs no branch of its own and the printed view and the runnable view cannot come apart.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli.py tests/unit/test_inventory.py -v`
Expected: PASS.

- [ ] **Step 5: Document the format**

Add a section to `docs/formats.md`, after the tools section and before subagents. It must cover: the file's location and `KINGFISHER_ACCESS_FILE`; that absence means the feature is off; the three controlled kinds and why `skills` and `builtin_tools` are not among them; `["*"]`; unlisted meaning nobody and the report that says so; `contains`; that a stale line is reported rather than fatal; `--as` and `UNSCOPED`; and that the caller's groups bound delegates too, with a worked example of a subagent running degraded.

Worked example to include verbatim:

```yaml
# access.yaml
groups:
  A: {}
  B: {}
  C: {}
  admin: {contains: [A, B, C]}

agents:
  assistant:  [A, B]
  surveyor:   ["*"]
subagents:
  reviewer:   [A, B, C]
  extractor:  [A]
tools:
  sql_query:  [A, B]
  http_fetch: [A, B, C]
  line_count: [B, C]
```

- [ ] **Step 6: Run the whole suite and lint**

Run: `pytest -q && ruff check src tests && ruff format --check src tests`
Expected: PASS. `test_prose_naming_a_module_names_one_that_exists` covers the new docs prose, so any module named in the new section must exist.

- [ ] **Step 7: Commit**

```bash
git add src/kingfisher/application/inventory.py src/kingfisher/presentation/cli/ docs/formats.md tests/unit/test_cli.py
git commit -m "feat: show who reaches what, and let the command say who is calling"
```

---

# STAGE 2 — agents

Stage 1 is useful on its own and stage 2 does not change its shape. Everything below adds a permission axis that `Capabilities` does not have, so it is new machinery rather than new wiring.

## Task 9: An agent a caller's groups cannot reach does not exist

**Files:**
- Modify: `src/kingfisher/application/service.py:913-945` (`agent_named`)
- Modify: `src/kingfisher/application/service.py` (`Caller`)
- Test: `tests/unit/test_access_agents.py` (create)

**Interfaces:**
- Consumes: `Access.reachable("agents", held)`
- Produces: `Kingfisher.agent_named(name, *, groups: Held | None = None)`; `Caller.agent_named`, `Caller.open_session_for`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_access_agents.py`:

```python
"""Which agent a caller may run, and what they are told about the rest.

An agent is not a `Capabilities` axis: a request names one before there is
anything to narrow. So this is checked where a session is opened rather than
where a grant is intersected -- and, because a session pins its agent for life,
on every turn afterwards as well.
"""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher, Request
from kingfisher.domain.access import AccessError
from kingfisher.domain.capabilities import CapabilityError


@pytest.fixture
def two_agents(workspace_cfg):
    """`assistant` for group A only; `surveyor` for everyone.

    Use whatever fixture the neighbouring tests use for a seeded workspace;
    write the policy into `cfg.workspace` and re-read the config so that
    `Config.access` is populated.
    """
    (workspace_cfg.workspace / "access.yaml").write_text(
        "groups: [A, B]\n"
        "agents:\n  assistant: [A]\n  surveyor: ['*']\n"
        "tools:\n  line_count: ['*']\n",
        encoding="utf-8",
    )
    from kingfisher.application.config import from_env

    return from_env({"KINGFISHER_WORKSPACE": str(workspace_cfg.workspace)})


def test_a_caller_reaches_an_agent_their_group_is_listed_on(two_agents):
    kf = Kingfisher(two_agents)
    assert kf.for_groups(["A"]).agent_named("assistant") is not None


def test_an_agent_out_of_reach_reads_as_one_that_does_not_exist(two_agents):
    """Decision 15. The wording matters: 'no agent named' rather than 'not
    permitted', so nothing is learned by guessing a name."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        kf.for_groups(["B"]).agent_named("assistant")


def test_the_listing_in_that_refusal_names_only_reachable_agents(two_agents):
    """The message lists what the workspace offers, and that listing is the
    enumeration this decision closes."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError) as raised:
        kf.for_groups(["B"]).agent_named("assistant")
    assert "surveyor" in str(raised.value)
    assert "assistant" not in str(raised.value).split("offers")[1]


def test_a_caller_who_reaches_no_agent_is_told_so_without_a_catalogue(two_agents):
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError, match="offers none"):
        kf.for_groups([])._kf.agent_named("assistant", groups=())


def test_unscoped_still_reaches_every_agent(two_agents):
    from kingfisher.domain.access import UNSCOPED

    kf = Kingfisher(two_agents)
    assert kf.for_groups(UNSCOPED).agent_named("assistant") is not None


def test_opening_a_session_on_an_unreachable_agent_is_refused(two_agents):
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError):
        kf.for_groups(["B"]).open_session_for(Request(task="t", agent="assistant"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_agents.py -v`
Expected: FAIL, `Caller` has no attribute `agent_named`.

- [ ] **Step 3: Write minimal implementation**

Change `Kingfisher.agent_named` to take the caller and filter the offered set at the top, so both refusals below read from the filtered mapping:

```python
    def agent_named(self, name: str | None, *, groups: Held | None = None) -> AgentSpec | None:
```

Immediately after `offered = self.catalogue.agents.specs`, narrow it:

```python
        # Filtered before the listing is built, not after, so the message a
        # caller reads never names an agent they cannot open. An agent out of
        # reach is spelled the same way an agent that was never written is:
        # anything else lets a caller enumerate the catalogue by guessing.
        if (reach := self.access) is not None and not isinstance(groups, _Unscoped):
            if groups is None:
                msg = (
                    "this deployment has an access policy, so a call must say "
                    "who is calling: for_groups([...]), or for_groups(UNSCOPED)"
                )
                raise AccessError(msg)
            within = set(reach.reachable("agents", reach.expand(groups)))
            offered = {n: spec for n, spec in offered.items() if n in within}
```

The two existing refusals below need no change: they already build `listing` from `offered` and already say `no agent named {name!r}`.

Add to `Caller`:

```python
    def agent_named(self, name: str | None) -> AgentSpec | None:
        return self._kf.agent_named(name, groups=self._held)

    def open_session_for(self, request: Request) -> Session:
        return self._kf.open_session_for(request, groups=self._held)
```

`open_session_for` takes `groups: Held | None = None` and passes it to its `agent_named` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_agents.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/application/service.py tests/unit/test_access_agents.py
git commit -m "feat: an agent out of a caller's reach reads as one that is not there"
```

---

## Task 10: The pinned agent is re-checked every turn

**Files:**
- Modify: `src/kingfisher/application/service.py:884` (`_agent_for`)
- Test: `tests/unit/test_access_agents.py` (append)

**Interfaces:**
- Consumes: Task 9's filtered `agent_named`
- Produces: `_agent_for(request, session_id, *, groups: Held | None = None)`

**Why:** a session id is a bearer credential (`kingfisher_service/access.py:3` says so outright) and a session pins its agent for life. Checking only at open makes the id a durable grant to an agent its holder may not open.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_access_agents.py`:

```python
def test_a_turn_on_a_pinned_agent_out_of_reach_is_refused(two_agents):
    """A session id is a bearer credential, so holding one must not be a way to
    keep running an agent your groups no longer reach."""
    kf = Kingfisher(two_agents)
    opened = kf.for_groups(["A"]).open_session_for(Request(task="t", agent="assistant"))

    with pytest.raises(CapabilityError):
        kf.for_groups(["B"]).run(
            Request(task="again", agent="assistant", session_id=opened.id)
        )


def test_a_turn_on_a_pinned_agent_still_in_reach_runs(two_agents):
    kf = Kingfisher(two_agents)
    opened = kf.for_groups(["A"]).open_session_for(Request(task="t", agent="assistant"))
    assert kf.for_groups(["A"]).run(
        Request(task="again", agent="assistant", session_id=opened.id)
    ) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_access_agents.py -k pinned -v`
Expected: FAIL — the first test runs rather than refusing.

- [ ] **Step 3: Write minimal implementation**

`_agent_for` already resolves the session's remembered agent through `agent_named`. Give it the caller and pass it through:

```python
    def _agent_for(
        self, request: Request, session_id: str, *, groups: Held | None = None
    ) -> AgentSpec | None:
```

Extend its docstring:

```
        Re-resolved every turn against the caller's groups rather than trusted
        from the session record. A session pins its agent for life and a
        session id is a bearer credential, so checking only at the open would
        make holding one a durable grant to an agent its holder may not open --
        and would leave a demoted caller running the agent they had before.
```

Pass `groups=groups` at every call site of `_agent_for`, and thread `groups` from `_admit` into `graph_for`, which is where `_agent_for` is called (`service.py:854`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_access_agents.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q && ruff check src tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kingfisher/application/service.py tests/unit/test_access_agents.py
git commit -m "feat: re-check a session's pinned agent against the caller every turn"
```

---

## Task 11: Agents in the listing, and the documentation for stage 2

**Files:**
- Modify: `src/kingfisher/presentation/cli/listing.py` (`_agents`)
- Modify: `docs/formats.md`
- Test: `tests/unit/test_cli.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cli.py`:

```python
def test_the_agents_section_shows_who_reaches_each_one(policied_workspace, capsys):
    from kingfisher.presentation.cli.__main__ import main

    main(["list", "--workspace", str(policied_workspace)])
    shown = capsys.readouterr().out.split("subagents")[0]
    assert "[A]" in shown or "[*]" in shown


def test_as_a_group_hides_agents_it_cannot_open(policied_workspace, capsys):
    from kingfisher.presentation.cli.__main__ import main

    main(["list", "--workspace", str(policied_workspace), "--as", "B"])
    shown = capsys.readouterr().out
    assert "assistant" not in shown
```

The `policied_workspace` fixture from Task 8 needs an `agents:` section for these; extend it there rather than writing a second fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli.py -k "agents_section or hides_agents" -v`
Expected: FAIL — no audience is printed in the agents block.

- [ ] **Step 3: Write minimal implementation**

In `listing.py::_agents`, append the audience to the agent line:

```python
        yield f"  {name}{_from(source, f'{name}.yaml')}{_audience(found, 'agents', name)} — {described}"
```

`--as` filtering already happens where the `Inventory` is built (Task 8), so extend that narrowing to cover `agents` — the same one line, one more kind.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Document stage 2**

Extend the `access.yaml` section of `docs/formats.md` with: that `agents:` is checked when a session opens *and* on every turn; why (a session pins its agent, a session id is a bearer credential); that an agent out of reach is reported as one that does not exist, and that the listing in that message names only reachable agents; and that a demotion makes an in-flight session unusable, which is the intended behaviour rather than a bug.

- [ ] **Step 6: Run the whole suite and lint**

Run: `pytest -q && ruff check src tests && ruff format --check src tests`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/kingfisher/presentation/cli/listing.py docs/formats.md tests/unit/test_cli.py
git commit -m "docs: say which groups reach which agents, in the listing and the format"
```

---

## Deliberately not in this plan

- **The HTTP service.** `service/` is untouched. `kingfisher_service` currently has no group source, so once a policy exists every route would hit the Task 6 refusal. Before shipping the service against a policied deployment, decide how it learns a caller's groups — a deployment-supplied callable given to `create_app`, a configured header name, or something else. Nothing above depends on that answer.
- **`skills`, `builtin_tools`, `middleware`, `endpoints`, `models` as controlled kinds.** `DECLINED` in `domain/access.py` refuses the first three by name with their reasons; adding one later means moving its key from `DECLINED` to `CONTROLLED` and giving `resolve` a line.
- **Hot reload.** Decision 11 is startup-only, mirroring `models.yaml`. If it changes later, `Access` is frozen and `reconciled` already returns a new one, so swapping the instance is the whole of the work — but something must hold it safely while turns are in flight.

---

## Self-Review

**Spec coverage.** Decisions 1, 2, 8, 9, 10 → Tasks 1–2. Decision 11 → Task 4. Decision 12 → Task 5. Decisions 3, 4, 5, 13 → Task 6. Decision 14 → Task 7 (the withheld report is what "says so" means). Decision 15 → Task 7 for tools and subagents, Task 9 for agents. Decision 6, 7 → Task 2 (`CONTROLLED`, `DECLINED`). Decision 16 → Task 10. Decision 18 → Tasks 8 and 11. Decision 17 requires no code — `including()` is untouched, and Task 6's suite run is what proves it.

**Two gaps found and closed while reviewing.** A grant naming a deleted tool would be *refused* by `Offering.refuse_unknown` rather than reported, contradicting decision 12 — hence `reconciled` dropping stale entries in Task 5, not merely reporting them. And `_withheld_by_kind` would have handed a caller the list of everything their groups denied them, contradicting decision 15 — hence Task 7 filtering the offered set before the comparison rather than after.

**One thing an implementer must check rather than assume.** The fixture names in Tasks 6, 8 and 9 (`workspace_cfg`, `policied_workspace`) are placeholders for whatever `tests/conftest.py` and the neighbouring capability tests actually provide. Each of those tasks says to read the neighbouring file first and substitute. Do not invent a fixture.
