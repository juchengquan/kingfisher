"""Where deepagents is spoken to.

Not quite the only place, and `THIRD_PARTY` in `test_architecture.py` is the list rather
than this sentence. Two kinds reach the runtime to register themselves -- `kinds.skills`
hands a repository to the lister that reads it, `kinds.middleware` refuses a class that
is not an `AgentMiddleware` as the directory is read -- and neither can be done from
outside. Everything else that names deepagents, langchain or langgraph is here.
"""
