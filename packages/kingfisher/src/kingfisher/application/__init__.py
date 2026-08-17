"""The application layer: what a turn does, in order.

Read the environment, prepare a session, build the graph for this request, run
it, record what happened. It orchestrates and decides nothing about the harness
— it speaks `Request`, `RunEvent` and `RunResult`, never `AIMessage`, and
reaches deepagents only through `infrastructure/`.

`run.py` and `runlog.py` each once carried their own copy of LangChain's
usage-metadata shape, kept in sync by nobody. That is the failure the rule
exists to prevent, and `tests/test_architecture.py` enforces it.
"""
