"""Reading one decoded field as the value the format meant."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from types import MappingProxyType
from typing import TYPE_CHECKING

from kingfisher.domain.capabilities import ALL, Selection

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    # Type-only, and it has to be: `domain.access` imports this module to read
    # its vocabulary document, so a runtime import here would close the loop.
    # `from __future__ import annotations` is what makes that affordable.
    from kingfisher.domain.access import Audience

#: How alike two names must look before one is called a typo of the other. Only
#: ever used to *word* a refusal, never to decide one: a guess that changed
#: behaviour would be the silent-drop bug wearing a spellchecker.
SIMILARITY = 0.7


def entry_fields(extra: str) -> tuple[str, ...]:
    """The keys one long-form entry may write, for a field carrying `extra`."""
    return ("name", extra)


def unrecognised(
    document: Iterable[str],
    *,
    known: Collection[str],
    declined: Mapping[str, str] | None = None,
    noun: str = "field",
) -> str | None:
    """The complaint about every key a format does not define, or `None`."""
    problems = [
        _explain(key, known=known, declined=declined or {}, noun=noun)
        for key in document
        if key not in known
    ]
    if not problems:
        return None
    return f"{'; '.join(problems)} (this format defines: {', '.join(sorted(known))})"


def _explain(
    key: str, *, known: Collection[str], declined: Mapping[str, str], noun: str
) -> str:
    """Why this one key is not accepted, in the terms that fit it."""
    if (reason := declined.get(key)) is not None:
        return f"{key!r} is not a {noun} of this format -- {reason}"
    near = get_close_matches(key, known, n=1, cutoff=SIMILARITY)
    # Parenthesised, not `; `-joined: that separates one key's explanation from
    # the next, and a hint using it too would blur where each ends.
    hint = f" (did you mean {near[0]!r}?)" if near else ""
    return f"unknown {noun} {key!r}{hint}"


def text(value: object) -> str:
    """One field as a string, however YAML typed it."""
    return "" if value is None else str(value).strip()


def names(value: object) -> tuple[str, ...] | None:
    """A field naming several things, written either way YAML allows."""
    if value is None:
        return None
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(text(item) for item in value if text(item))
    return (text(value),)


@dataclass(frozen=True)
class Reader:
    """One format's field readers, bound to the file and the error it raises."""

    #: What a message calls the file -- `reviewer.yaml`, not its whole path.
    source: str
    #: The format's own exception. This is the one thing that cannot be shared:
    #: a subagent's mistakes are `SubagentError` and an agent's are `AgentError`,
    #: and a caller catching one should not be handed the other.
    error: type[Exception]

    def one_name(self, value: object, *, key: str) -> str | None:
        """One name, or `None` when the field is absent. A list is refused.

        Measured before this: a definition writing `model: [gpt-5, claude-4]` was
        read as a model of that spelling and refused a request later, by `resolve`,
        with `no model "['gpt-5', 'claude-4']" defined in models.yaml` -- which sends
        its reader off to define one.
        """
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            written = ", ".join(str(one) for one in value)
            msg = (
                f"{self.source}: {key} names {len(value)} things ({written}); "
                f"it takes one. A list was legal while an unbound alias could be "
                f"passed over and the next tried -- nothing is passed over now, "
                f"so every name after the first was unreachable"
            )
            raise self.error(msg)
        return text(value) or None

    def selection(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
    ) -> Selection:
        """One name-list field, or what its absence means for that field."""
        if isinstance(value, str) and value.strip() == ALL:
            msg = (
                f"{self.source}: {key} is written {value.strip()!r}; write [{ALL!r}] "
                f"instead. Every selection here is a list, so everything is a list too"
            )
            raise self.error(msg)
        if isinstance(value, Mapping):
            # `names` would fall through to `(text(value),)` and read the whole mapping
            # as one name -- `builtin_tools: {execute: {groups: [A]}}` became a built-in
            # called "{'execute': ...}", offered to nobody and reported nowhere.
            msg = (
                f"{self.source}: {key} is a mapping; this field takes a list. A "
                f"mapping says who reaches each entry, and only tools, subagents "
                f"and skills may say that -- builtin tools are registered by "
                f"deepagents, so they can be filtered but never left out of a graph"
            )
            raise self.error(msg)

        written = names(value)
        if written is None:
            return absent
        if ALL not in written:
            return written
        if refuse_all is not None:
            msg = f"{self.source}: {key} may not be [{ALL!r}] -- {refuse_all}"
            raise self.error(msg)
        if len(written) > 1:
            others = ", ".join(n for n in written if n != ALL)
            msg = (
                f"{self.source}: {key} mixes [{ALL!r}] with {others}; "
                f"[{ALL!r}] is everything, so naming anything beside it means "
                f"one of the two was not meant"
            )
            raise self.error(msg)
        return ALL

    def selection_with_settings(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
    ) -> tuple[Selection, Mapping[str, Mapping[str, object]]]:
        """One name-list field whose entries may also carry settings."""
        if not isinstance(value, (list, tuple)):
            # A bare `"*"`, one unbracketed name, or nothing at all. None of
            # the three can carry a setting, and `selection` already has the
            # answer or the refusal for each.
            return self.selection(value, absent=absent, key=key), MappingProxyType({})

        written, carried = self._entries(value, key=key, extra="settings")
        settings = {
            # Absent and empty both land as `{}`, which is the same answer: this
            # entry wrote the long form and asked for nothing by it.
            name: self.mapping(entry.get("settings"), key=f"{key} entry '{name}' 'settings'")
            for name, entry in carried.items()
        }

        # Back through `selection`, so `["*"]`, the mixing refusal and the
        # meaning of an absent field are all decided in exactly one place.
        return self.selection(written, absent=absent, key=key), MappingProxyType(settings)

    def _entries(
        self, value: Sequence[object], *, key: str, extra: str
    ) -> tuple[list[str], dict[str, Mapping[str, object]]]:
        """The names a list field wrote, and what each entry carried beside one."""
        written: list[str] = []
        carried: dict[str, Mapping[str, object]] = {}
        for position, entry in enumerate(value, start=1):
            name = self._entry_name(entry, position=position, key=key, extra=extra)
            if name in written:
                msg = (
                    f"{self.source}: {key} names {name!r} twice. One name is one "
                    f"thing to build, so a second entry for it is either two "
                    f"answers that cannot both apply or a line that says nothing"
                )
                raise self.error(msg)
            written.append(name)
            if isinstance(entry, Mapping):
                carried[name] = entry
        return written, carried

    def _entry_name(self, entry: object, *, position: int, key: str, extra: str) -> str:
        """The name one entry carries, whichever way that entry was written."""
        if isinstance(entry, str):
            return entry.strip()
        if not isinstance(entry, Mapping):
            msg = (
                f"{self.source}: {key} entry {position} is neither a name nor a "
                f"mapping (got {type(entry).__name__}); an entry is a name, or a "
                f"mapping of 'name' and {extra!r}"
            )
            raise self.error(msg)

        if (complaint := unrecognised(entry, known=entry_fields(extra), noun="key")) is not None:
            msg = f"{self.source}: {key} entry {position} has {complaint}"
            raise self.error(msg)
        if "name" not in entry:
            msg = (
                f"{self.source}: {key} entry {position} is a mapping with no "
                f"'name'. Written long, an entry is {{name: <a name>, {extra}: ...}} "
                f"-- the {extra} are for the name, so there is nothing to attach "
                f"them to without one"
            )
            raise self.error(msg)

        name = text(entry["name"])
        if not name:
            msg = f"{self.source}: {key} entry {position} has an empty 'name'"
            raise self.error(msg)
        if name == ALL:
            msg = (
                f"{self.source}: {key} entry {position} writes name {ALL!r}, which "
                f"the long form does not take. {ALL!r} says something about the "
                f"whole field rather than about an entry, so there is nothing for "
                f"{extra} beside it to be about. Write {key}: [{ALL!r}] on its own "
                f"for all of them, or name the one you meant"
            )
            raise self.error(msg)
        return name

    def groups(self, value: object, *, key: str = "groups") -> Audience:
        """A definition's own audience: who may reach it at all."""
        if value is None:
            return ALL
        return self.audience_list(value, where=f"{self.source}: {key}", lone_name=True)

    def audience_list(self, listed: object, *, where: str, lone_name: bool) -> Audience:
        """A list of group names, any entry of which may be `{all_of: [...]}`."""
        if isinstance(listed, str) and lone_name and listed.strip() and listed.strip() != ALL:
            return (listed.strip(),)
        if isinstance(listed, Mapping):
            inner = listed.get("all_of")
            parts = ", ".join(str(one) for one in inner) if isinstance(inner, list) else "..."
            said = f"[{{all_of: [{parts}]}}]"
            msg = (
                f"{where}: an audience is a list, so a conjunction is one entry "
                f"of it -- write {said}. On its own it reads as the whole "
                f"audience being a mapping, which has no `or` to put it in"
            )
            raise self.error(msg)
        if isinstance(listed, str) or not isinstance(listed, (list, tuple)):
            msg = f'{where} is a list of names, or ["{ALL}"] -- got {listed!r}'
            raise self.error(msg)

        written: list[str | frozenset[str]] = []
        for one in listed:
            if isinstance(one, Mapping):
                written.append(self._conjunction(one, where=where))
            elif name := text(one):
                written.append(name)
        if not written:
            msg = (
                f"{where} is empty, which would mean nobody. Leave the line out "
                f"to inherit the audience around it, or name the groups it is for"
            )
            raise self.error(msg)
        if ALL in written and len(written) > 1:
            rest = ", ".join(str(n) for n in written if n != ALL)
            msg = f'{where}: ["{ALL}"] is everyone, so it cannot mean both that and {rest}'
            raise self.error(msg)
        return ALL if written == [ALL] else tuple(written)

    def _conjunction(self, raw: Mapping[str, object], *, where: str) -> frozenset[str]:
        """One audience entry that is satisfied only by holding every name in it."""
        if complaint := unrecognised(raw, known={"all_of"}, noun="key"):
            msg = f"{where}: {complaint}"
            raise self.error(msg)
        parts = raw.get("all_of")
        if isinstance(parts, str) or not isinstance(parts, (list, tuple)):
            msg = f"{where}: 'all_of' is a list of group names -- got {parts!r}"
            raise self.error(msg)
        named = frozenset(name for one in parts if (name := text(one)))
        if not named:
            msg = (
                f"{where}: 'all_of' is empty, which would require nothing and so "
                f"admit everyone. Name the groups a caller must hold together"
            )
            raise self.error(msg)
        if ALL in named:
            msg = f'{where}: "{ALL}" is everyone, so it cannot be part of a requirement'
            raise self.error(msg)
        return named

    def _audience(self, raw: object, *, key: str, entry: str) -> Audience | None:
        """One entry's audience, written `{groups: [...]}`, or `None` for none."""
        where = f"{self.source}: {key} entry {entry!r}"
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            written = ", ".join(str(one) for one in raw) or "..."
            msg = (
                f"{where}: an audience is written `groups: [{written}]`, not as a "
                f"bare list -- the same word the definition's own line uses, so an "
                f"entry says which fact it is stating and has room for another"
            )
            raise self.error(msg)
        if not isinstance(raw, Mapping):
            msg = f"{where}: write `groups: [...]`, or nothing at all -- got {raw!r}"
            raise self.error(msg)
        # `name` is the entry's own, checked and consumed by `_entry_name`
        # before this sees it. Named here rather than stripped there, because
        # stripping would hand this a mapping the file does not contain and put
        # the two readers one edit apart from disagreeing about which keys exist.
        if complaint := unrecognised(raw, known=set(entry_fields("groups")), noun="key"):
            msg = f"{where}: {complaint}"
            raise self.error(msg)
        if "groups" not in raw:
            return None
        return self.audience_list(raw["groups"], where=f"{where}: groups", lone_name=False)

    def audienced(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
    ) -> tuple[Selection, Mapping[str, Audience]]:
        """A selection, and who reaches each entry of it."""
        if isinstance(value, Mapping):
            # Refused rather than read: a field-level mapping cannot see a name
            # written twice, because YAML collapses `{a: X, a: Y}` before any reader
            # here runs -- so one of the two audiences would be gone with nothing
            # able to refuse or report it. An access restriction that vanishes
            # quietly is the failure this format exists to prevent.
            first = next(iter(value), "<name>")
            msg = (
                f"{self.source}: {key} is a mapping; this field takes a list. An "
                f"entry that says who it is for is written long -- "
                f"- {{name: {first}, groups: [...]}} -- beside the plain names, "
                f"which is the same shape 'middleware' takes for its settings"
            )
            raise self.error(msg)
        if not isinstance(value, (list, tuple)):
            # A bare `"*"`, one unbracketed name, or nothing. None can carry an
            # audience, and `selection` already has the answer or the refusal.
            return self.selection(value, absent=absent, key=key, refuse_all=refuse_all), {}

        written, carried = self._entries(value, key=key, extra="groups")
        stated = {
            name: self._audience(entry, key=key, entry=name) for name, entry in carried.items()
        }
        # Back through `selection`, so `["*"]`, the mixing refusal and the
        # meaning of an absent field are all decided in exactly one place.
        chosen = self.selection(written, absent=absent, key=key, refuse_all=refuse_all)
        # Every name is selected; only the ones that stated an audience carry
        # one. An entry that wrote the long form and left `groups` out falls
        # back to the definition's own in `access.reaching`, which is the same
        # fallback a plain name gets -- so the two spellings agree about an
        # unrestricted name.
        return chosen, {n: a for n, a in stated.items() if a is not None}

    def flag(self, value: object, *, key: str) -> bool:
        """A yes/no field, refusing the spellings YAML would quietly accept."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        msg = (
            f"{self.source}: {key} is {value!r}; write true or false. A quoted "
            f"{str(value)!r} reads as text, and every non-empty text is true -- "
            f"including {'false'!r}"
        )
        raise self.error(msg)

    def mapping(self, value: object, *, key: str) -> Mapping[str, object]:
        """A field of the caller's own keys, carried and never interpreted."""
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = (
                f"{self.source}: {key} must be a mapping of your own keys, "
                f"got {type(value).__name__}"
            )
            raise self.error(msg)
        return dict(value)


#: The `model:` line, which is one field and two formats -- so it belongs to
#: neither of them.

def wanted_model(document: Mapping[str, object], read: Reader) -> str | None:
    """The model a definition names, or `None` for whatever summoned it."""
    return read.one_name(document.get("model"), key="model")
