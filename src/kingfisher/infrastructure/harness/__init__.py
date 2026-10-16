"""Where deepagents is spoken to.

Not quite the only place, and `THIRD_PARTY` in `test_architecture.py` is the list rather
than this sentence. Two kinds reach the runtime to register themselves -- `core.definitions.skills`
hands a repository to the lister that reads it, `core.definitions.middlewares` refuses a class that
is not an `AgentMiddleware` as the directory is read -- and neither can be done from
outside. Everything else that names deepagents, langchain or langgraph is here.
"""
