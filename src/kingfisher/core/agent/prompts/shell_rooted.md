This shell exports the session root — the directory the file tools call `/` — as
`$KINGFISHER_SESSION_ROOT`. Put it in front of a file-tool path, slash included, to
reach that file from the shell: `/{{inputs}}/data.csv` is
`"$KINGFISHER_SESSION_ROOT/{{inputs}}/data.csv"`. A script reads it from its
environment, as `os.environ["KINGFISHER_SESSION_ROOT"]` in Python; never paste the path
it expands to into a script.
