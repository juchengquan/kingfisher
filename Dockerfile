# Kingfisher, for a deployment that must keep nothing on the machine it runs on.
#
# The container is not a sandbox wrapped around each turn -- it is how the
# service is deployed, and one of them serves every session. What it supplies is
# the boundary `KINGFISHER_SHELL_SANDBOX=external` names: "the runtime already
# confines this process", which is why `confinement.py` is switched off by
# configuration here rather than deleted.
#
# There is no tmpfs in this file, deliberately. A memory filesystem has to be
# sized against the container's *memory limit*, and an image cannot know one --
# see `compose.yaml`, and `kingfisher doctor`, which refuses the arrangements
# that silently swap or die.

FROM python:3.12-slim

# `fuse3` is deliberately absent. It would be needed to expose a virtual
# filesystem as real paths, and this design does not have one: tmpfs is already
# real paths, with a size limit the kernel enforces and no library under every
# turn. See *Sessions: what persists and where* in docs/decisions.md for the
# choice, and *mirage, and why it was not adopted* in docs/findings.md for what
# the alternative measured.
#
# `ca-certificates` because the agent reaches model endpoints over TLS, and
# `git` because a skill may.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies before source, so editing code does not re-resolve them.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# The worked definitions. Not in the wheel -- `KINGFISHER_ASSETS` names a
# directory and this image ships one so `kingfisher seed` has somewhere to point.
COPY assets_examples/ ./assets_examples/

ENV PATH="/app/.venv/bin:${PATH}" \
    KINGFISHER_WORKSPACE=/workspace \
    KINGFISHER_ASSETS=/app/assets_examples \
    # The container is the boundary. Wrapping the shell again inside it would
    # pay twice for one guarantee.
    KINGFISHER_SHELL_SANDBOX=external

# Nothing here creates /workspace. It is a mount, and a directory baked into the
# image would be a silent fallback when the mount is missing -- exactly the
# failure this deployment cannot afford, since it would be a real disk.

ENTRYPOINT ["kingfisher"]
CMD ["doctor"]
