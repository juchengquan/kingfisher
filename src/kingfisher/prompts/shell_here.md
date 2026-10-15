## The shell

The shell (`execute`) runs on the host, starting in the session root — the same
directory virtual `/` names. So a virtual path becomes a shell path by dropping the
leading slash, and nothing in the workspace is out of the shell's reach:

| virtual | from the shell |
| --- | --- |
| `/{{inputs}}/<name>` | `{{inputs}}/<name>` |
| `/{{outputs}}/<name>` | `{{outputs}}/<name>` |
| `/{{scratchpad}}/<name>` | `{{scratchpad}}/<name>` |

These already exist when a turn starts, so do not go searching for them — `find`
will not locate one any faster than dropping the slash will.

A virtual path is not a shell path. Passing `/{{inputs}}/<name>` to `execute`, or
writing it into a script the shell runs, addresses the host's root directory, not
the workspace. Where these instructions give host path mappings for particular
mounts, use those with the shell when you need an absolute path.

The shell exports `/{{scratchpad}}` as `$TMPDIR`, so both halves of your toolkit write
the same place. Write `"$TMPDIR/name.py"`, never a literal `/tmp/name.py`: `$TMPDIR` is
set per session, so a hardcoded `/tmp` scatters files somewhere nothing will clean up
and nothing will find.
