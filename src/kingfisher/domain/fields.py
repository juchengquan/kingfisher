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
from typing import TYPE_CHECKING

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
