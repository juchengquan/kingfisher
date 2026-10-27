The shell may read anywhere in the session. It writes in `/{{outputs}}`, for anything
that should outlive the run, and in `/{{scratchpad}}`, for anything throwaway. It is
refused in `/{{inputs}}`, in what kingfisher keeps about the session, and in the folder
the shell starts in, which is the session's top. So a bare `> out.csv`, or a `mkdir`
with no path in front of it, writes where nothing may be written; send it to
`"$KINGFISHER_SESSION_ROOT/{{outputs}}/<name>"` to hand it back, or to
`"$TMPDIR/<name>"` to work on it.
