The shell may read anywhere in the session. Write from it in `/{{outputs}}`, for
anything that should outlive the run, and in `/{{scratchpad}}`, for anything
throwaway, and never in `/{{inputs}}`, in what kingfisher keeps about the session, or in
the folder the shell starts in, which is the session's top. So never a bare
`> out.csv`, or a `mkdir` with no path in front of it: send it to
`"$KINGFISHER_SESSION_ROOT/{{outputs}}/<name>"` to hand it back, or to
`"$TMPDIR/<name>"` to work on it.
