"""What a turn's caller can use, as a tool is handed it: the definitions they reach under
this turn's grant, and none of the policy that decided it."""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .access import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    approved_settings,
    narrowed,
)

if TYPE_CHECKING:
    from .config import Config
    from .definitions.catalogue import Definitions
    from .inventory import Grant, Inventory


#: What a refusal of a draft names, which has no file yet.
DRAFT = Path("draft.yaml")

#: The lines that say who may use something.
_LOCKS = ("source_ids", "datazone")

#: What a drafted agent may be called. Its file takes the name, and these are spelt one way
#: on every disk. Startup loads any name: a draft alone is held to this.
DRAFT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DRAFT_NAME_MAX = 32


@dataclass(frozen=True)
class Listing:
    """What one caller can use this turn, kind by kind, each name with a line about it."""

    agents: Mapping[str, str]
    #: deepagents' own tools, which describe themselves to whatever holds them.
    builtin_tools: tuple[str, ...]
    tools: Mapping[str, str]
    skills: Mapping[str, str]
    subagents: Mapping[str, str]
    middlewares: Mapping[str, str]
    models: tuple[str, ...]
    #: What an agent naming no `model:` runs, where this turn may reach it.
    default_model: str | None


def _line(text: str | None) -> str:
    """The first line of what something says about itself."""
    return next((one.strip() for one in (text or "").splitlines() if one.strip()), "")


def _within(names: Iterable[str], granted: Selection, *, same: Any = None) -> tuple[str, ...]:
    """The names this turn's grant keeps, each compared as `same` spells it."""
    if granted == ALL:
        return tuple(names)
    spell = same or (lambda one: one)
    kept = {spell(one) for one in granted or ()}
    return tuple(one for one in names if spell(one) in kept)


def _written(grant: Grant, today: Iterable[str]) -> list[str]:
    """One grant field as a copy writes it: a star stays one, names keep to `today`."""
    if grant.every:
        return [ALL]
    kept = set(today)
    return [one for one in grant.names if one in kept]


def _locks_written(document: Mapping[str, object]) -> tuple[str, ...]:
    """Where a draft says who may use something: every place a definition can.

    Not every key at every depth: `metadata` is the author's own, and may well use the words.
    """
    where = ["the top level"] if any(key in document for key in _LOCKS) else []
    for field in ("tools", "skills", "subagents", "middlewares"):
        entries = document.get(field)
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, Mapping) and any(key in entry for key in _LOCKS):
                where.append(f"the {field} entry {entry.get('name')!r}")
    prompt = document.get("system_prompt")
    slots = prompt.get("slots") if isinstance(prompt, Mapping) else None
    for slot, entries in slots.items() if isinstance(slots, Mapping) else ():
        if any(
            isinstance(entry, Mapping) and any(key in entry for key in _LOCKS)
            for entry in (entries if isinstance(entries, list) else ())
        ):
            where.append(f"the slot {slot!r}")
    return tuple(where)


def _unusable(field: str, name: str) -> str:
    """One name a draft may not write, worded alike whether it is out of reach or nowhere."""
    return f"{field}: {name!r} is not one this user can use"


class Reachable:
    """The definitions this turn's caller reaches, under this turn's grant.

    Worked out when a tool first asks, from the one walk `kingfisher list --as` makes:
    a second answer to what a caller reaches is how a name they cannot reach leaks.
    Carries no source ids, audiences or slots, since those are the deployment's policy.
    """

    # Not a dataclass: pydantic resolves a dataclass's fields when a tool naming
    # `ToolContext` is bound, which would load the catalogue's readers into every tool file.
    def __init__(
        self,
        *,
        cfg: Config,
        catalogue: Definitions,
        granted: Capabilities,
        held: frozenset[str] | None,
        middlewares: Mapping[str, Any],
    ) -> None:
        self.cfg = cfg
        self.catalogue = catalogue
        #: The deployment's grant under the request's, never an agent's own: what is shown
        #: is what this caller may use, whichever agent the tool runs under.
        self.granted = granted
        self.held = held
        #: Every middleware a definition may name: the folder's and the deployment's code.
        self.middlewares = middlewares

    @cached_property
    def _found(self) -> Inventory:
        from .inventory import inventory_for  # noqa: PLC0415

        return inventory_for(self.cfg, catalogue=self.catalogue, held=self.held)

    @cached_property
    def _listing(self) -> Listing:
        from .definitions.tools.spec import Offering, placed  # noqa: PLC0415

        found = self._found
        granted = self.granted
        offered = set(found.tools)
        tools = _within(found.tools, granted.tools, same=lambda one: placed(one, offered))
        registry = self.catalogue.registry
        skills = _within(found.skills, granted.skills, same=registry.identity)
        among = Offering.of(self.catalogue.tools.found)
        told = {
            spelt: _line(getattr(one.tool, "description", None) or inspect.getdoc(one.tool))
            for spelt, one in zip(among.offered, self.catalogue.tools.found, strict=True)
        }
        models = tuple(
            name
            for name, profile in self.cfg.models.models.items()
            if granted.endpoints == ALL or profile.endpoint in (granted.endpoints or ())
        )
        return Listing(
            agents=dict(found.agents),
            builtin_tools=tuple(narrowed(found.builtin_tools, by=granted.builtin_tools) or ()),
            tools={name: told.get(name, "") for name in tools},
            skills={name: found.skills[name] or "" for name in skills},
            subagents={
                name: found.subagents[name]
                for name in _within(found.subagents, granted.subagents)
            },
            middlewares={
                name: _line(inspect.getdoc(self.middlewares[name]))
                for name in _within(sorted(self.middlewares), granted.middlewares)
            },
            models=models,
            default_model=self.cfg.models.default if self.cfg.models.default in models else None,
        )

    def listing(self) -> Listing:
        """Everything this caller can use this turn, by kind."""
        return self._listing

    def agent(self, name: str) -> dict[str, object] | None:
        """One agent's file as this caller gets it, or `None` for one they cannot reach.

        Ready to write back out: every list cut to this listing, a star kept as a star,
        and the prompt with its slots filled for this caller. Out of reach reads exactly as
        a name nothing defines.
        """
        listing = self._listing
        if name not in listing.agents:
            return None
        found = self._found
        spec = self.catalogue.agents.specs[name]
        registry = self.catalogue.registry
        builtin = _written(found.agent_builtin_tools[name], listing.builtin_tools)
        tools = _written(found.agent_tools[name], listing.tools)
        reached = spec.declares(self.held).skills
        listed = {registry.identity(one) for one in listing.skills}
        skills = (
            [ALL]
            if reached == ALL
            else [one for one in reached or () if registry.identity(one) in listed]
        )
        middlewares: list[object] = []
        if spec.middlewares == ALL:
            middlewares = [ALL]
        for one in () if spec.middlewares == ALL else spec.middlewares or ():
            if one not in listing.middlewares:
                continue
            settings = spec.middleware_settings.get(one)
            middlewares.append({"name": one, "settings": dict(settings)} if settings else one)
        # Not to the copy's own tools: a gate may stop a tool one of its subagents holds.
        reachable_tools = {*listing.builtin_tools, *listing.tools}
        copied: dict[str, object] = {"name": spec.name, "description": spec.description}
        if spec.wanted in listing.models:
            copied["model"] = spec.wanted
        for key, value in (
            ("builtin_tools", builtin),
            ("tools", tools),
            ("skills", skills),
            ("subagents", _written(found.agent_subagents[name], listing.subagents)),
            ("middlewares", middlewares),
            ("interrupt_on", [one for one in found.agent_gates[name] if one in reachable_tools]),
        ):
            if value:
                copied[key] = value
        if spec.memory is not None:
            copied["memory"] = spec.memory
        if spec.metadata:
            copied["metadata"] = dict(spec.metadata)
        copied["system_prompt"] = spec.prompt_for(self.held)
        return copied

    def check_agent(self, text: str) -> tuple[str, ...]:
        """What is wrong with `text` as an agent file this caller drafted, or nothing.

        Read by the reader startup uses, then every name checked against this listing, so
        one out of reach and one nothing defines are refused in the same words.
        """
        # Here, not at the top: a tool file loads this module for its annotation.
        from .definitions import documents  # noqa: PLC0415
        from .definitions.agents import reading  # noqa: PLC0415
        from .definitions.agents.spec import AgentError  # noqa: PLC0415
        document = documents.decode(text)
        # Before anything reads them, and never against the deployment's: an answer that
        # told a real id from a made-up one would let a caller find the real ones by guessing.
        if isinstance(document, dict) and (where := _locks_written(document)):
            return (
                "who may use this agent is decided by whoever installs it: remove "
                f"`source_ids:` and `datazone:` from {', '.join(where)}",
            )
        try:
            spec = reading.read(documents.DefinitionText(text, DRAFT))
        except (AgentError, CapabilityError) as exc:
            return (str(exc),)

        listing = self._listing
        problems: list[str] = []
        if len(spec.name) > DRAFT_NAME_MAX or not DRAFT_NAME.fullmatch(spec.name):
            problems.append(
                f"name {spec.name!r}: write lowercase letters and digits, with single hyphens "
                f"between them, at most {DRAFT_NAME_MAX} characters, such as 'invoice-helper'"
            )
        # Capitals aside: on a disk that ignores them, one file replaces the other.
        taken = {one.casefold(): one for one in listing.agents}
        if (other := taken.get(spec.name.casefold())) is not None:
            problems.append(f"an agent called {other!r} already exists: choose another name")
        builtin, offered = set(listing.builtin_tools), set(listing.tools)
        if isinstance(spec.builtin_tools, tuple):
            problems += [
                _unusable("builtin_tools", one)
                for one in spec.builtin_tools
                if one not in builtin
            ]
        # As the listing spells it, not resolved: a `where::what` naming the wrong file
        # loads, and the agent runs without the tool.
        if isinstance(spec.tools, tuple):
            problems += [_unusable("tools", one) for one in spec.tools if one not in offered]
        registry = self.catalogue.registry
        listed = {registry.identity(one) for one in listing.skills}
        if isinstance(spec.skills, tuple):
            problems += [
                _unusable("skills", one)
                for one in spec.skills
                if registry.identity(one) not in listed
            ]
        if isinstance(spec.subagents, tuple):
            problems += [
                _unusable("subagents", one)
                for one in spec.subagents
                if one not in listing.subagents
            ]
        for one in spec.middlewares if isinstance(spec.middlewares, tuple) else ():
            if one not in listing.middlewares:
                problems.append(_unusable("middlewares", one))
                continue
            try:
                approved_settings(
                    spec.middleware_settings.get(one) or {},
                    settable=getattr(self.middlewares[one], "yaml_settable", None) or (),
                    subject="this draft",
                    registered_as=one,
                )
            except CapabilityError as exc:
                problems.append(str(exc))
        if spec.wanted is not None and spec.wanted not in listing.models:
            problems.append(_unusable("model", spec.wanted))
        problems += [
            _unusable("interrupt_on", one)
            for one in spec.interrupt_on
            if one not in builtin and one not in offered
        ]
        return tuple(problems)
