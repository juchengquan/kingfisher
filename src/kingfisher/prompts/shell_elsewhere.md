## The shell

The shell (`execute`) runs wherever this deployment runs it, which need not be this
session's directory, or this machine. A file-tool path may name nothing there, with its
leading slash or without it, so check what the shell sees — `pwd`, `ls` — before a
command relies on a file. The file tools always reach this session's files.
