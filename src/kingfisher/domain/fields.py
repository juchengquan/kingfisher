"""Reading one decoded field as the value the format meant.

Both definition formats reach here, and neither is the reason this module
exists any more. It was `frontmatter`, named for the `---`-delimited header the
two shared — until subagents became a whole YAML document and stopped having a
header at all. What survived the change is the part that was never about
envelopes: turning whatever YAML produced into the string or the tuple of names
the format asked for.

Splitting a markdown header off a body lives with the format that still has one
— `domain.skill.split` — because it is deepagents' rule about deepagents'
document, not a thing both formats do.

What does *not* live here is the YAML decode, which needs a third-party
library. `infrastructure.catalogue.documents` owns that, because a domain module
imports the standard library and `kingfisher.domain`, nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from kingfisher.domain.capabilities import ALL, Selection

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

#: How alike two names must look before one is called a typo of the other. Only
#: ever used to *word* a refusal, never to decide one: a guess that changed
#: behaviour would be the silent-drop bug wearing a spellchecker.
SIMILARITY = 0.7

#: What one entry of a `selection_with_settings` list may write when it is
#: written long. Two keys, and deliberately no third: an entry says which thing
#: and what to pass it, and anything else it wanted to say belongs to the field
#: as a whole rather than to one name in it.
ENTRY_FIELDS = ("name", "settings")


def unrecognised(
    document: Iterable[str],
    *,
    known: Collection[str],
    declined: Mapping[str, str] | None = None,
    noun: str = "field",
) -> str | None:
    """The complaint about every key a format does not define, or `None`.

    Two formats refuse unknown keys and both give the same reason for it: a key
    we ignore is a key the author believes took effect. Subagent definitions had
    the careful version of the message and `models.yaml` had a plainer one, so
    the file a deployment writes *first* -- and the only one that decides where
    prompts go -- was the one that would not tell you `defualt:` was a typo.

    Returns the complaint rather than raising it. The two callers raise
    different types on purpose, `SubagentError` against `ConfigError`, and each
    prefixes the source in its own way; what they share is which keys are
    wrong, what they might have meant, and what the format does define.

    All of them at once, not the first. Two typos used to take two runs to find,
    and the second only after fixing the first.

    `declined` names keys refused for a *specific* reason -- a field another
    library defines that this format deliberately does not -- where the generic
    message would read as an omission worth working around.
    """
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
    """One field as a string, however YAML typed it.

    `model: 4` is a number to YAML and a model name to us; a folded description
    is already joined. Both become the string the format meant.
    """
    return "" if value is None else str(value).strip()


def names(value: object) -> tuple[str, ...] | None:
    """A field naming several things, written either way YAML allows.

    `[read_file, grep]` and a block list both mean the same thing, and a single
    unbracketed name is accepted because someone will write it.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(text(item) for item in value if text(item))
    return (text(value),)


@dataclass(frozen=True)
class Reader:
    """One format's field readers, bound to the file and the error it raises.

    The two formats read the same *fields* and raise different exceptions about
    the same *file*, and both of those travel with every single call. Written as
    free functions they showed up as `source=source.name, error=SubagentError`
    on every line, which is the pair asking to be bound once.

    `unrecognised` above stays a free function deliberately: it returns the
    complaint instead of raising, so it needs neither half of this.
    """

    #: What a message calls the file -- `reviewer.yaml`, not its whole path.
    source: str
    #: The format's own exception. This is the one thing that cannot be shared:
    #: a subagent's mistakes are `SubagentError` and an agent's are `AgentError`,
    #: and a caller catching one should not be handed the other.
    error: type[Exception]

    def one_name(self, value: object, *, key: str) -> str | None:
        """One name, or `None` when the field is absent. A list is refused.

        The counterpart of `selection` below, for a field that names a single
        thing. `text` cannot do this itself: it takes a value and no error type,
        because it is what turns `model: 4` into `"4"` for every format at once
        -- and it is that same `str()` which turns `[gpt-5, claude-4]` into the
        name `"['gpt-5', 'claude-4']"`, brackets and quotes included.

        Measured before this: a definition writing `model: [gpt-5, claude-4]`
        was read as a model of that spelling and refused a request later, by
        `resolve`, with `no model "['gpt-5', 'claude-4']" defined in
        models.yaml` -- which sends its reader off to define one. `model:` did
        take a list once, while an `alias:` beside it could be passed over for
        being unbound; nothing passes a candidate over now, so the shape means
        nothing and saying so here beats saying something else later.
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
        """One name-list field, or what its absence means for that field.

        `absent` differs per field and that is the point: omitting `tools`
        inherits everything available, omitting `skills` grants none. Both
        formats draw that distinction and neither should own the reading of it.

        `["*"]` is everything. A list, because every one of these fields is a
        list and a field whose type changes with its value is one more thing to
        know. The bare `"*"` is refused by name rather than read as a name --
        the same trade `system_prompt` makes by accepting one block style and
        naming the others -- because a request spells this `"*"` and someone
        will carry the habit across.

        Mixing is refused too. `["*", read_file]` has no reading that is not a
        guess, and it used to have the worst one: `*` matched no tool, so the
        star silently contributed nothing.

        `refuse_all` is for a field where everything is not a coherent answer,
        and it carries the reason rather than a flag so the message can say it.
        `subagents` in a *subagent* file is the case: everything there includes
        the definition doing the asking. In an agent file it does not, which is
        why this is an argument and not a rule.
        """
        if isinstance(value, str) and value.strip() == ALL:
            msg = (
                f"{self.source}: {key} is written {value.strip()!r}; write [{ALL!r}] "
                f"instead. Every selection here is a list, so everything is a list too"
            )
            raise self.error(msg)
        if isinstance(value, Mapping):
            # `names` would fall through to `(text(value),)` and read the whole
            # mapping as one name -- `builtin_tools: {execute: {groups: [A]}}`
            # became a built-in called "{'execute': ...}", offered to nobody and
            # reported nowhere. Worth refusing by name now that three sibling
            # fields *do* take a mapping, because writing one here is the
            # reasonable mistake rather than a strange one.
            #
            # The three are spelled out rather than imported: `domain.access`
            # imports this module, so naming `AUDIENCED` here would be a cycle.
            # `test_only_the_audienced_fields_take_a_mapping` pins the pair.
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
        """One name-list field whose entries may also carry settings.

        `selection` above reads the same field. What this adds is a second way
        to write one entry of it:

            middleware:
              - call-cap-strict          # a name, as it always was
              - name: audit-hook         # the same name, with values beside it
                settings: {level: INFO}

        Both spellings in one list, because they say the same kind of thing: an
        entry is a name, and the mapping is that name with values attached. A
        format where the whole list changed shape as soon as one entry wanted a
        setting would make the common case pay for the rare one, and every
        definition that never writes a setting keeps the list it already has.

        Returns the two halves apart, which is the point. The names are a
        `Selection` like every other field -- granting, narrowing and refusing
        are all operations on names and none of them has heard of a setting --
        and the settings are a sibling mapping keyed by name. That is
        `tool_sources` beside `tools` and for the same reason: they are asked
        for one name at a time, by whoever is building that one thing.

        What a setting *means* is not decided here and cannot be. The keys a
        name will accept are declared by the class the deployment registered
        under it, and this layer has never seen a registry -- so this reads the
        shape and `approved_settings` refuses the contents, the same split
        `approved_middleware` already makes between a name and the code behind
        it.

        `"*"` is refused in the mapping form. It resolves to whatever the
        deployment registered, so settings written beside it would be settings
        for classes this file has never heard of; there is no reading of that
        which is not a guess. The plain `["*"]` is untouched and still means
        everything.
        """
        if not isinstance(value, (list, tuple)):
            # A bare `"*"`, one unbracketed name, or nothing at all. None of
            # the three can carry a setting, and `selection` already has the
            # answer or the refusal for each.
            return self.selection(value, absent=absent, key=key), MappingProxyType({})

        written: list[str] = []
        settings: dict[str, Mapping[str, object]] = {}
        for position, entry in enumerate(value, start=1):
            name = self._entry_name(entry, position=position, key=key)
            if name in written:
                msg = (
                    f"{self.source}: {key} names {name!r} twice. One name is one "
                    f"thing to build, so a second entry for it is either settings "
                    f"that cannot both apply or a line that says nothing"
                )
                raise self.error(msg)
            written.append(name)
            if isinstance(entry, Mapping):
                # Absent and empty both land as `{}`, which is the same answer:
                # this entry wrote the long form and asked for nothing by it.
                settings[name] = self.mapping(
                    entry.get("settings"), key=f"{key} entry {position} 'settings'"
                )

        # Back through `selection`, so `["*"]`, the mixing refusal and the
        # meaning of an absent field are all decided in exactly one place.
        return self.selection(written, absent=absent, key=key), MappingProxyType(settings)

    def _entry_name(self, entry: object, *, position: int, key: str) -> str:
        """The name one entry carries, whichever way that entry was written.

        Positional in the message rather than named, because a name is the
        thing that might be missing -- "entry 2" is findable in a file where
        "the entry called nothing" is not.
        """
        if isinstance(entry, str):
            return entry.strip()
        if not isinstance(entry, Mapping):
            msg = (
                f"{self.source}: {key} entry {position} is neither a name nor a "
                f"mapping (got {type(entry).__name__}); an entry is a name, or a "
                f"mapping of 'name' and 'settings'"
            )
            raise self.error(msg)

        if (complaint := unrecognised(entry, known=ENTRY_FIELDS, noun="key")) is not None:
            msg = f"{self.source}: {key} entry {position} has {complaint}"
            raise self.error(msg)
        if "name" not in entry:
            msg = (
                f"{self.source}: {key} entry {position} is a mapping with no "
                f"'name'. Written long, an entry is {{name: <a registered name>, "
                f"settings: {{...}}}} -- the settings are for the name, so there "
                f"is nothing to attach them to without one"
            )
            raise self.error(msg)

        name = text(entry["name"])
        if not name:
            msg = f"{self.source}: {key} entry {position} has an empty 'name'"
            raise self.error(msg)
        if name == ALL:
            msg = (
                f"{self.source}: {key} entry {position} writes name {ALL!r}, which "
                f"the mapping form does not take. {ALL!r} is whatever this "
                f"deployment registered, so a setting written beside it is a "
                f"setting for classes this file has never seen. Write "
                f"{key}: [{ALL!r}] on its own for all of them, or name the one "
                f"you meant to configure"
            )
            raise self.error(msg)
        return name

    def groups(self, value: object, *, key: str = "groups") -> tuple[str, ...] | Literal["*"]:
        """A definition's own audience: who may reach it at all.

        Its own reader rather than `selection`, and not only for the type. A
        selection names things the *workspace* offers and may be `None` for
        "none of them"; this names groups, and there is no "none" -- a
        definition nobody may reach is written by giving it a group nobody
        holds. Absent means everyone, which is what an absent optional field
        means everywhere else in these formats.
        """
        if value is None:
            return ALL
        written = self.selection(value, absent=ALL, key=key)
        # `selection` cannot answer `None` with `absent=ALL`; this narrows the
        # type rather than guarding against a case that can happen.
        return written if written is not None else ALL

    def _audience(
        self, raw: object, *, key: str, entry: str
    ) -> tuple[str, ...] | Literal["*"] | None:
        """One entry's audience, written `{groups: [...]}`, or `None` for none.

        `None` is what makes the mapping form usable at all. Only the entries
        you actually restrict carry a `groups:` line; the rest say nothing and
        inherit the definition's own, so restricting one tool does not mean
        writing an audience for every other tool beside it:

            groups: [A, B]
            tools:
              sql_query:
                groups: [A]        # this one is narrower
              http_fetch:          # this one is not, and says so by saying nothing

        Absent and empty are the same answer -- the entry wrote the long form
        and asked for nothing by it -- which is the reading
        `selection_with_settings` already makes of its own `settings`.

        A mapping rather than a bare list where an audience *is* stated, so that
        an entry says which fact it is stating, has somewhere to put a second
        one later, and can have a mistyped key refused: `{grops: [A]}` is caught
        here, where `[A]` alone has no key to check.
        """
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
        if complaint := unrecognised(raw, known={"groups"}, noun="key"):
            msg = f"{where}: {complaint}"
            raise self.error(msg)
        if "groups" not in raw:
            return None

        listed = raw["groups"]
        if isinstance(listed, str) or not isinstance(listed, (list, tuple)):
            msg = f'{where}: groups is a list of names, or ["{ALL}"] -- got {listed!r}'
            raise self.error(msg)
        written = tuple(text(one) for one in listed if text(one))
        if not written:
            msg = (
                f"{where}: groups is empty, which would mean nobody. Leave the "
                f"line out to inherit this definition's own audience, or name "
                f"the groups this entry is for"
            )
            raise self.error(msg)
        if ALL in written and len(written) > 1:
            msg = (
                f'{where}: ["{ALL}"] is everyone, so it cannot mean both that '
                f"and {', '.join(n for n in written if n != ALL)}"
            )
            raise self.error(msg)
        return ALL if written == (ALL,) else written

    def audienced(
        self,
        value: object,
        *,
        absent: Selection,
        key: str,
        refuse_all: str | None = None,
    ) -> tuple[Selection, Mapping[str, tuple[str, ...] | Literal["*"]]]:
        """A selection, and who reaches each entry of it.

        Two spellings of one field, and the second is a strict extension of the
        first: a list selects, a mapping selects *and* says who for. Every file
        written before audiences existed reads identically through this.

        Returned as a pair rather than as a richer type, so that `spec.tools`
        stays the `Selection` every consumer already reads -- `narrowed`,
        `Offering`, `as_subagent`, the allowlist -- and the audiences travel
        beside it, consulted only where a caller's groups are known. A new type
        here would mean touching every one of those to unwrap it.

        The same pair `selection_with_settings` returns, arrived at separately
        and for the same reason: names are what granting and narrowing operate
        on, and whatever rides beside a name is asked for one name at a time.

        The star belongs to the list form, because it says something about the
        whole field rather than about an entry. `{"*": ...}` is a name that is
        not a name, and is refused rather than read as one.
        """
        if not isinstance(value, Mapping):
            return self.selection(value, absent=absent, key=key, refuse_all=refuse_all), {}
        if not value:
            msg = (
                f"{self.source}: {key} is an empty mapping, which reads as nothing "
                f"-- write [] if that is what you mean, or name what it holds"
            )
            raise self.error(msg)
        stated = {
            text(name): self._audience(raw, key=key, entry=text(name))
            for name, raw in value.items()
        }
        if ALL in stated:
            msg = (
                f"{self.source}: {key} names {ALL!r} as an entry, which is not a "
                f"name -- {ALL!r} says something about the whole field, so write "
                f"it as the list [{ALL!r}]"
            )
            raise self.error(msg)
        # `refuse_all` is deliberately not consulted here. It refuses `["*"]`,
        # which is a statement about the whole field -- and the star cannot be
        # written in a mapping at all, refused two lines above as a name that is
        # not a name. There is nothing left for it to catch.
        #
        # Every key is selected; only the ones that stated an audience carry
        # one. An absent entry falls back to the definition's own in
        # `access.reaching`, which is the same fallback a plain list gets --
        # so the two spellings agree about an unrestricted name.
        return tuple(stated), {n: a for n, a in stated.items() if a is not None}

    def flag(self, value: object, *, key: str) -> bool:
        """A yes/no field, refusing the spellings YAML would quietly accept.

        `memory: "false"` is a non-empty string and truthy in Python, which is
        the reading that says the opposite of what the file says. YAML already
        turns `true`, `yes` and `on` into `True` before this sees them, so what
        is left here arrived as something other than a bool -- and there is no
        reading of it that is not a guess.
        """
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
        """A field of the caller's own keys, carried and never interpreted.

        A mapping or nothing. `metadata: gold` is refused rather than wrapped,
        because a bag with no shape cannot be looked up by key and looking up a
        key is the only thing anyone will do with it.

        Absent and empty both become `{}`, which saves every reader a `None`
        check for a field whose whole meaning is "nothing extra".
        """
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = (
                f"{self.source}: {key} must be a mapping of your own keys, "
                f"got {type(value).__name__}"
            )
            raise self.error(msg)
        return dict(value)
