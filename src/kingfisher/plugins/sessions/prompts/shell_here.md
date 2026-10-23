## The shell

The shell (`execute`) runs on the host, and exports the session root — the same
directory virtual `/` names — as `$KINGFISHER_SESSION_ROOT`. So a virtual path becomes
a shell path by putting that variable in front of it, slash included, and nothing in
the workspace is out of the shell's reach:

| virtual | from the shell |
| --- | --- |
| `/{{inputs}}/<name>` | `"$KINGFISHER_SESSION_ROOT/{{inputs}}/<name>"` |
| `/{{outputs}}/<name>` | `"$KINGFISHER_SESSION_ROOT/{{outputs}}/<name>"` |
| `/{{scratchpad}}/<name>` | `"$KINGFISHER_SESSION_ROOT/{{scratchpad}}/<name>"` |

Spelt that way, a path stays right after a `cd`, and in a script run from anywhere.
These folders already exist when a turn starts, so do not go searching for them —
`find` will not locate one any faster than the variable will.

The shell may read anywhere in the session, and write in two of those folders:
`/{{outputs}}` for anything that should outlive the run, `/{{scratchpad}}` for anything
throwaway. Everywhere else is refused — `/{{inputs}}`, what kingfisher keeps about the
session, and the folder the shell starts in, which is the session's top. So a bare
`> out.csv`, or a `mkdir` with no path in front of it, writes where nothing may be
written; send it to `"$KINGFISHER_SESSION_ROOT/{{outputs}}/<name>"` to hand it back, or
to `"$TMPDIR/<name>"` to work on it.

A script reads the root from its environment, as `os.environ["KINGFISHER_SESSION_ROOT"]`
in Python. Never paste the path it expands to into a script: a pasted path is wrong
once the session moves.

A virtual path is not a shell path. Passing `/{{inputs}}/<name>` to `execute`, or
writing it into a script the shell runs, addresses the host's root directory, not
the workspace.

The shell exports `/{{scratchpad}}` as `$TMPDIR`, so both halves of your toolkit write
the same place. Write `"$TMPDIR/name.py"`, never a literal `/tmp/name.py`: `$TMPDIR` is
set per session, so a hardcoded `/tmp` scatters files somewhere nothing will clean up
and nothing will find.
