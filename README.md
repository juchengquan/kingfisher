# kingfisher

Three packages, developed together and installed apart.

| | install | what it is |
|---|---|---|
| [`packages/kingfisher`](packages/kingfisher) | `pip install kingfisher` | the library: run an agent against a workspace, with the caller deciding what it may do |
| [`packages/service`](packages/service) | `pip install 'kingfisher[service]'` | an HTTP surface over the library |
| [`packages/assets`](packages/assets) | `pip install 'kingfisher[assets]'` | one working example of each thing a request can activate |

They live in one repository because a change to a format and the examples of it
belong in one commit. They install apart because `pip install kingfisher` should
carry no web service and no example content — which is checked against the built
wheel, not asserted.

```bash
uv sync --all-extras --dev    # all three, linked to this checkout
uv run pytest -q              # all three, one command
```

Design notes are in [`docs/design`](docs/design). `main.py` is the development
driver and is not part of any package.
