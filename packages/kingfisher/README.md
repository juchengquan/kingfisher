# kingfisher

A library for running agents against a workspace, with what each request may do
decided by the caller rather than by the code.

```bash
pip install kingfisher
```

```python
from kingfisher import Capabilities, Config, Kingfisher, Request

agent = Kingfisher(Config.from_env())
result = agent.run(Request("Summarise the CSV in /data"), Capabilities(tools=()))
```

`kingfisher list` shows what a workspace offers a request — tools, skills,
subagents — and where each one came from. `kingfisher seed` writes a starting
workspace.

## What ships separately

Two packages extend this one, so that installing it carries neither:

- **`kingfisher[service]`** — an HTTP surface over the library.
- **`kingfisher[assets]`** — one working example of each thing a request can
  activate: tools, skills and subagents.

Both are their own distributions. `pip install kingfisher` puts no web service
and no example content on disk, which is checked on the built wheel rather than
asserted.
