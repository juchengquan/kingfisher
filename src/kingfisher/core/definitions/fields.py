"""Reading one decoded field as the value the format meant."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from types import MappingProxyType
from typing import TYPE_CHECKING, NoReturn

from kingfisher.core.access import ALL, SEPARATOR, Selection

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from kingfisher.core.access import Audience

#: How alike two names must look for one to be called a typo of the other. It only ever
#: words a refusal, never decides one: a guess must not change behaviour.
SIMILARITY = 0.7


#: Where one `tools:` or `skills:` entry comes from: `shared` is the workspace's catalogue
#: and what a plain name means; `bundled` is the subagent's own folder, the one place a
#: capability can sit that an agent writing `tools: ["*"]` does not receive.
SHARED = "shared"
BUNDLED = "bundled"
SOURCES: tuple[str, ...] = (SHARED, BUNDLED)


def entry_fields(*extra: str) -> tuple[str, ...]:
    """The keys one long-form entry may write, for a field carrying `extra`."""
    return ("name", *extra)


def _spelled(extra: tuple[str, ...]) -> str:
    return " and/or ".join(repr(one) for one in extra)


#: A key an entry may not write, with where it goes instead. On an entry, a datazone could
#: only repeat or contradict the thing's own, and a contradiction makes it unreachable.
ON_THE_THING: Mapping[str, str] = MappingProxyType(
    {"datazone": "write it in the tool's or subagent's own file, the one place it means one thing"}
)

#: Why a `tools:` or `subagents:` entry says nothing about who may use it: each thing has one
#: lock, in its own file, so who may use it is read in one place and not off every agent.
TOOL_LOCK = (
    "a tool's lock is in its own file, as ToolSpec(tool, source_ids=[...]) in its "
    "TOOLS, and holds wherever the tool is granted"
)
SUBAGENT_LOCK = (
    "a subagent's audience is the source_ids: line in its own file, and holds "
    "wherever it is used"
)


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
    # `str`: decoding stringifies a YAML file's keys, and nothing does a Python entry's.
    near = get_close_matches(str(key), known, n=1, cutoff=SIMILARITY)
    # Parenthesised: `; ` already separates one key's explanation from the next.
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

    #: What a message calls the file -- `probe.yaml`, not its whole path.
    source: str
    #: The format's own exception, so a caller catching `AgentError` is never handed a
    #: `SubagentError`.
    error: type[Exception]

    def refuse_unrecognised(
        self,
        document: Iterable[str],
        *,
        known: Collection[str],
        declined: Mapping[str, str] | None = None,
    ) -> None:
        """Refuse every key the format does not define, saying why for each it declines."""
        complaint = unrecognised(document, known=known, declined=declined)
        if complaint is not None:
            msg = f"{self.source}: {complaint}"
            raise self.error(msg)

    def require(self, document: Mapping[str, object], *names: str) -> None:
        """Refuse a document without each of `names`, present and not blank."""
        for required in names:
            # Absent and blank told apart: "missing" sends a reader hunting for a line they wrote.
            if required not in document:
                msg = f"{self.source}: missing required field {required!r}"
                raise self.error(msg)
            if not text(document[required]):
                msg = f"{self.source}: {required!r} is present but empty"
                raise self.error(msg)

    def one_name(self, value: object, *, key: str) -> str | None:
        """One name, or `None` when the field is absent. A list is refused."""
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            written = ", ".join(str(one) for one in value)
            msg = (
                f"{self.source}: {key} names {len(value)} things ({written}); "
                f"it takes one. There is no fallback, so every name after the first "
                f"would never be tried"
            )
            raise self.error(msg)
        return text(value) or None

    def _no_star(self, key: str, why: str) -> str:
        """What a field that refuses the star says, however the star was spelled."""
        return f"{self.source}: {key} may not be {ALL!r} -- {why}"

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
            if refuse_all is not None:
                # Before the bracket advice below, which would send the reader to write
                # `["*"]` and be refused again.
                raise self.error(self._no_star(key, refuse_all))
            msg = (
                f"{self.source}: {key} is written {value.strip()!r}; write [{ALL!r}] "
                f"instead. Every selection here is a list, so everything is a list too"
            )
            raise self.error(msg)
        if isinstance(value, Mapping):
            # `names` would read a mapping as one name: a built-in called "{'execute': ...}",
            # offered to nobody and reported nowhere.
            msg = (
                f"{self.source}: {key} is a mapping; this field takes a list. A "
                f"mapping says who reaches each entry, and only a skills entry may "
                f"say that -- builtin tools are registered by deepagents, so they "
                f"can be filtered but never left out of a graph"
            )
            raise self.error(msg)

        written = names(value)
        if written is None:
            return absent
        if ALL not in written:
            return written
        if refuse_all is not None:
            raise self.error(self._no_star(key, refuse_all))
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
        if isinstance(value, Mapping):
            # Before `selection`, whose mapping refusal is about who reaches an entry.
            first = next(iter(value), "<name>")
            msg = (
                f"{self.source}: {key} is a mapping; this field takes a list. An "
                f"entry with settings is written long -- "
                f"- {{name: {first}, settings: {{...}}}} -- beside the plain names"
            )
            raise self.error(msg)
        if not isinstance(value, (list, tuple)):
            # A bare `"*"`, one name, or nothing: none can carry a setting.
            return self.selection(value, absent=absent, key=key), MappingProxyType({})

        written, carried = self._entries(value, key=key, extra=("settings",))
        settings = {
            name: self.mapping(entry.get("settings"), key=f"{key} entry '{name}' 'settings'")
            for name, entry in carried.items()
        }

        # Back through `selection`, so the star, mixing and absence are decided in one place.
        return self.selection(written, absent=absent, key=key), MappingProxyType(settings)

    def _entries(
        self,
        value: Sequence[object],
        *,
        key: str,
        extra: tuple[str, ...],
        same: Callable[[str], str] | None = None,
        locked: str | None = None,
    ) -> tuple[list[str], dict[str, Mapping[str, object]]]:
        """The names a list field wrote, and what each entry carried beside one.

        `same` says when two spellings are one entry. Tools pass it, since `a.py::fetch`
        and `fetch` dispatch as one name; skills do not, since `source::name` is a skill's
        identity, and the catalogue is where a bare name's ambiguity can be seen. `locked`
        is why an entry may not say who may use it, for a field whose things say it.
        """
        written: list[str] = []
        carried: dict[str, Mapping[str, object]] = {}
        dispatches = same or (lambda name: name)
        for position, entry in enumerate(value, start=1):
            name = self._entry_name(
                entry, position=position, key=key, extra=extra, locked=locked
            )
            dispatched = dispatches(name)
            clash = next((n for n in written if dispatches(n) == dispatched), None)
            if clash is not None:
                # Both spellings: "names 'fetch' twice" sends a reader hunting a second `fetch`.
                said = f"{name!r}" if clash == name else f"{clash!r} and {name!r}"
                msg = (
                    f"{self.source}: {key} names {said} twice. One name is one "
                    f"thing to build, so a second entry for it is either two "
                    f"answers that cannot both apply or a line that says nothing"
                )
                raise self.error(msg)
            written.append(name)
            if isinstance(entry, Mapping):
                carried[name] = entry
        return written, carried

    def _entry_name(
        self,
        entry: object,
        *,
        position: int,
        key: str,
        extra: tuple[str, ...],
        locked: str | None = None,
    ) -> str:
        """The name one entry carries, whichever way that entry was written."""
        if isinstance(entry, str):
            return entry.strip()
        if not isinstance(entry, Mapping):
            form = f"a name, or a mapping of 'name' and {_spelled(extra)}" if extra else "a name"
            msg = (
                f"{self.source}: {key} entry {position} is neither a name nor a "
                f"mapping (got {type(entry).__name__}); an entry is {form}"
            )
            raise self.error(msg)

        # Before the unknown-key check, which would offer `source` for `source_ids`. With no
        # `extra`, a long entry has nothing to carry but the audience it may not.
        if locked is not None and ("source_ids" in entry or not extra):
            named = text(entry.get("name")) or f"<entry {position}>"
            # What the entry may still carry, so `source: bundled` survives the remedy.
            kept = "".join(f", {k}: {text(v)}" for k, v in entry.items() if k in extra)
            written = f"{{name: {named}{kept}}}" if kept else named
            said = "writes source_ids" if "source_ids" in entry else "is written long"
            msg = (
                f"{self.source}: {key} entry {named!r} {said}, which a {key} entry does "
                f"not take: {locked}. Write the entry as - {written}"
            )
            raise self.error(msg)

        if (
            complaint := unrecognised(
                entry, known=entry_fields(*extra), declined=ON_THE_THING, noun="key"
            )
        ) is not None:
            msg = f"{self.source}: {key} entry {position} has {complaint}"
            raise self.error(msg)
        if "name" not in entry:
            msg = (
                f"{self.source}: {key} entry {position} is a mapping with no "
                f"'name'. Written long, an entry is {{name: <a name>, {extra[0]}: ...}} "
                f"-- what it carries is for the name, so there is nothing to attach "
                f"it to without one"
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
                f"{extra[0]} beside it to be about. Write {key}: [{ALL!r}] on its own "
                f"for all of them, or name the one you meant"
            )
            raise self.error(msg)
        return name

    def datazone(self, value: object, *, key: str = "datazone") -> str | None:
        """The one datazone something is for, or `None` for any."""
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            msg = f"{self.source}: {key} is one name -- write {key}: finance -- got {value!r}"
            raise self.error(msg)
        return value.strip()

    def source_ids(self, value: object, *, key: str = "source_ids") -> Audience:
        """A definition's own audience: who may reach it at all."""
        if value is None:
            return ALL
        return self.audience_list(value, where=f"{self.source}: {key}", lone_name=True)

    def audience_list(self, listed: object, *, where: str, lone_name: bool) -> Audience:
        """A list of source ids, any entry of which may be a set `{a, b}`."""
        if isinstance(listed, str) and lone_name and listed.strip() and listed.strip() != ALL:
            return (listed.strip(),)
        if isinstance(listed, (Mapping, set, frozenset)):
            inner = listed.get("all_of") if isinstance(listed, Mapping) else None
            named = (
                inner
                if isinstance(inner, list)
                else sorted(listed, key=str) if isinstance(listed, (set, frozenset))
                else tuple(listed)
            )
            parts = ", ".join(str(one) for one in named) or "..."
            msg = (
                f"{where}: an audience is a list, so a requirement is one entry "
                f"of it -- write [{{{parts}}}]. On its own it reads as the whole "
                f"audience being a set, which has no `or` to put it in"
            )
            raise self.error(msg)
        if isinstance(listed, str) or not isinstance(listed, (list, tuple)):
            msg = f'{where} is a list of names, or ["{ALL}"] -- got {listed!r}'
            raise self.error(msg)

        written: list[str | frozenset[str]] = []
        for one in listed:
            if isinstance(one, Mapping):
                written.append(self._conjunction(one, where=where))
            elif isinstance(one, (set, frozenset)):
                written.append(self._python_conjunction(one, where=where))
            elif isinstance(one, (list, tuple)):
                # Refused rather than read as "all of". A bracket whose meaning depends
                # on its depth is misread in the direction that widens: `(a, b)` at the
                # top is "a or b", and the same pair one level down would be "a and b".
                parts = ", ".join(str(name) for name in one) or "..."
                msg = (
                    f"{where}: {list(one)!r} is a list inside the audience, which does "
                    f"not say whether it means any or all of its names. A requirement "
                    f"-- every name held at once -- is a set: write {{{parts}}}"
                )
                raise self.error(msg)
            elif name := text(one):
                written.append(name)
        if not written:
            msg = (
                f"{where} is empty, which would mean nobody. Leave the line out "
                f"to inherit the audience around it, or name the source ids it is for"
            )
            raise self.error(msg)
        if ALL in written and len(written) > 1:
            rest = ", ".join(str(n) for n in written if n != ALL)
            msg = f'{where}: ["{ALL}"] is everyone, so it cannot mean both that and {rest}'
            raise self.error(msg)
        return ALL if written == [ALL] else tuple(written)

    def _python_conjunction(self, raw: Collection[object], *, where: str) -> frozenset[str]:
        """A requirement written as a Python set, which YAML has no way to produce."""
        if odd := sorted(repr(one) for one in raw if not isinstance(one, str)):
            msg = f"{where}: a requirement is a set of names, and {', '.join(odd)} is not one"
            raise self.error(msg)
        names = sorted(one for one in raw if isinstance(one, str))
        return self._conjunction(dict.fromkeys(names), where=where)

    def _conjunction(self, raw: Mapping[str, object], *, where: str) -> frozenset[str]:
        """One audience entry that is satisfied only by holding every name in it."""
        if raw.get("all_of") is not None:
            inner = raw["all_of"]
            listed = inner if isinstance(inner, (list, tuple)) else ()
            parts = ", ".join(str(one) for one in listed) or "..."
            msg = (
                f"{where}: 'all_of' is not a key the format takes -- write the "
                f"requirement as the set it is, {{{parts}}}"
            )
            raise self.error(msg)
        # `{finance: senior}` is a mapping with a value rather than a set of two
        # names, and reading it as one would take `finance` and throw away what
        # was written beside it.
        if valued := sorted(str(key) for key, value in raw.items() if value is not None):
            msg = (
                f"{where}: {{{', '.join(valued)}}} has something written after it. "
                f"A requirement is a set of names -- write {{a, b}}, not {{a: b}}"
            )
            raise self.error(msg)
        named = frozenset(name for one in raw if (name := text(one)))
        if not named:
            msg = (
                f"{where}: {{}} requires nothing and so admits everyone. Name the "
                f"source ids a caller must hold together"
            )
            raise self.error(msg)
        if ALL in named:
            msg = f'{where}: "{ALL}" is everyone, so it cannot be part of a requirement'
            raise self.error(msg)
        return named

    def _audience(
        self,
        raw: object,
        *,
        key: str,
        entry: str,
        known: tuple[str, ...],
    ) -> Audience | None:
        """One entry's audience, written `{source_ids: [...]}`, or `None` for none."""
        where = f"{self.source}: {key} entry {entry!r}"
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            written = ", ".join(str(one) for one in raw) or "..."
            msg = (
                f"{where}: an audience is written `source_ids: [{written}]`, not as a "
                f"bare list -- the same word the definition's own line uses, so an "
                f"entry says which fact it is stating and has room for another"
            )
            raise self.error(msg)
        if not isinstance(raw, Mapping):
            msg = f"{where}: write `source_ids: [...]`, or nothing at all -- got {raw!r}"
            raise self.error(msg)
        # `name` is in `known` rather than stripped by `_entry_name`, so this checks the
        # mapping the file contains.
        if complaint := unrecognised(raw, known=set(known), declined=ON_THE_THING, noun="key"):
            msg = f"{where}: {complaint}"
            raise self.error(msg)
        if "source_ids" not in raw:
            return None
        return self.audience_list(raw["source_ids"], where=f"{where}: source_ids", lone_name=False)

    def _refuse_mapping(
        self, value: Mapping[object, object], *, key: str, locked: str | None
    ) -> NoReturn:
        """Refuse a whole field written as a mapping keyed by name.

        Refused rather than read: YAML collapses `{a: X, a: Y}` before any reader runs, so
        one of two audiences for a name would vanish unreported.
        """
        if locked is not None:
            listed = ", ".join(str(one) for one in value) or "<name>"
            msg = (
                f"{self.source}: {key} is a mapping; this field takes a list of names -- "
                f"write {key}: [{listed}]. Nothing beside a name here says who may use "
                f"it: {locked}"
            )
            raise self.error(msg)
        first = next(iter(value), "<name>")
        msg = (
            f"{self.source}: {key} is a mapping; this field takes a list. An "
            f"entry that says who it is for is written long -- "
            f"- {{name: {first}, source_ids: [...]}} -- beside the plain names, "
            f"which is the same shape 'middlewares' takes for its settings"
        )
        raise self.error(msg)

    def names_only(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
        locked: str,
    ) -> Selection:
        """A selection whose entries are names and nothing else; `locked` says why."""
        if isinstance(value, Mapping):
            self._refuse_mapping(value, key=key, locked=locked)
        if not isinstance(value, (list, tuple)):
            return self.selection(value, absent=absent, key=key, refuse_all=refuse_all)
        # Through `_entries`, which refuses an entry written long and a name written twice.
        written, _ = self._entries(value, key=key, extra=(), locked=locked)
        return self.selection(written, absent=absent, key=key, refuse_all=refuse_all)

    def sourced(  # noqa: PLR0913 -- separate questions about one field; an object hides which
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
        refuse_bundled: str | None = None,
        same: Callable[[str], str] | None = None,
        locked: str | None = None,
    ) -> tuple[Selection, Mapping[str, Audience], tuple[str, ...]]:
        """The shared entries and who reaches each, and the bundled entries apart.

        Apart because nothing narrows a bundled entry: in the selection, a grant that
        omits it would read as having removed it. `locked` is why no entry may say who
        reaches it, which leaves the middle answer empty.
        """
        if isinstance(value, Mapping):
            self._refuse_mapping(value, key=key, locked=locked)
        if not isinstance(value, (list, tuple)):
            # A bare `"*"`, one name, or nothing: none can carry anything beside a name.
            return self.selection(value, absent=absent, key=key, refuse_all=refuse_all), {}, ()

        extra = ("source",) if locked is not None else ("source_ids", "source")
        # Both sources through one `_entries`, so a name written shared and bundled is
        # refused as written twice: a subagent dispatches by name.
        written, carried = self._entries(value, key=key, extra=extra, same=same, locked=locked)
        bundled = tuple(
            name for name, entry in carried.items() if self._source(entry, key=key) == BUNDLED
        )
        for name in bundled:
            where = f"{self.source}: {key} entry {name!r}"
            if refuse_bundled is not None:
                msg = f"{where} is source: {BUNDLED} -- {refuse_bundled}"
                raise self.error(msg)
            if "source_ids" in carried[name]:
                msg = (
                    f"{where} is source: {BUNDLED} and says who reaches it, and a "
                    f"bundled entry reaches whoever reaches this subagent. Put the "
                    f"source_ids on the subagent itself"
                )
                raise self.error(msg)
            if SEPARATOR in name:
                msg = (
                    f"{where} is source: {BUNDLED} and says which file it is in, and "
                    f"a bundled entry is always in this subagent's own folder. Write "
                    f"the name alone"
                )
                raise self.error(msg)
        stated = {
            name: self._audience(entry, key=key, entry=name, known=entry_fields(*extra))
            for name, entry in carried.items()
            if name not in bundled
        }
        chosen = self.selection(
            [name for name in written if name not in bundled],
            absent=absent,
            key=key,
            refuse_all=refuse_all,
        )
        return chosen, {n: a for n, a in stated.items() if a is not None}, bundled

    def _source(self, entry: Mapping[str, object], *, key: str) -> str:
        """Where one long-form entry says it comes from, `shared` when it does not say."""
        written = entry.get("source")
        if written is None:
            return SHARED
        if (found := text(written)) not in SOURCES:
            msg = (
                f"{self.source}: {key} entry {text(entry.get('name'))!r} has source "
                f"{written!r}; it takes {SHARED!r}, the catalogue's own {key}/, or "
                f"{BUNDLED!r}, this subagent's own folder subagents/<name>/{key}/"
            )
            raise self.error(msg)
        return found

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


def wanted_model(document: Mapping[str, object], read: Reader) -> str | None:
    """The model a definition names, or `None` for whatever summoned it."""
    return read.one_name(document.get("model"), key="model")
