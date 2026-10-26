## The file tools

The file tools ({{file_tools}})
take virtual paths rooted at the workspace, so an input file is `/{{inputs}}/<name>`.

A host path is not a file-tool path. Passing an absolute host path such as
`/tmp/scratch.py` to a file tool is refused, and the error names the virtual path
to use instead.
