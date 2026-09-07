"""Where deepagents is spoken to, and the only place it may be.

Some of `infrastructure/` adapts the agent runtime -- deepagents, LangChain,
LangGraph. The rest adapts the disk, the OS and the process environment. Both are
legitimately infrastructure, so no rule caught the mixture until this package drew
the line.
"""
