# House format for release notes

Loaded on demand by `/skills/release-notes/SKILL.md`. It lives in a separate
file because most tasks never need it — that is the whole point of a skill's
directory: `SKILL.md` stays short enough to be worth reading, and the detail is
one `read_file` away when it is actually wanted.

## Section order

Fixed. Omit any section with no entries; never reorder them.

1. **Breaking changes**
2. **Added**
3. **Changed**
4. **Fixed**
5. **Deprecated**

## Line format

    - <what changed, from the reader's side>. <what they must do, if anything>

Present tense, no trailing period on the first clause. No commit hashes, no
author names, no issue numbers unless the issue is public and adds context a
reader cannot get from the line itself.

## Worked example

    ## Breaking changes

    - `run()` now takes a `Request` rather than three positional arguments. Pass
      `Request(task=..., session_id=...)`; the old signature is gone
    - The default workspace moved out of the package directory. Set
      `KINGFISHER_WORKSPACE` explicitly

    ## Added

    - Requests can restrict which tools, skills and subagents a turn may use

    ## Fixed

    - A run that was interrupted mid-turn no longer leaves its turn directory
      behind for the next sweep to inherit

## What not to write

- "Various bug fixes and improvements" — say which, or omit the section
- "Refactored the internals" — invisible to the reader, so it is not a note
- One line per commit — group them
