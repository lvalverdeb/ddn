# syntax=docker/dockerfile:1.7
#
# DDN in a container. Two stages, so the build tooling and the compiler stay
# out of the image that ships.
#
# `pyproject.toml` pins the platform by git tag from a public repository, so
# the clone needs no credential. This file used to carry a BuildKit secret and
# an ssh fallback for it, on the strength of a README line that said otherwise.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# git for the pinned dependency; build-essential because pyvrp compiles.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./

# The dependencies alone, so that editing source does not re-resolve them.
RUN uv sync --frozen --no-install-project --extra api

# `models/` sits beside `ddn/` on purpose: `contract.load_model` reads the
# delivery models by path relative to the package, because this repository
# ships them and knows where they are.
#
# The suite is not here. It runs on the host in about a second with no daemon,
# which is the promise CLAUDE.md makes; copying `tests/` and `docs/` in would
# put a test suite and a 500-line specification in a production image to
# duplicate something cheaper elsewhere.
COPY ddn/ ./ddn/
COPY models/ ./models/
RUN uv sync --frozen --extra api --no-editable

# The suite is not in this image, so nothing at runtime would notice `models/`
# missing until the first routing run failed. `contract.load_model` reads it by
# path relative to the package, which is exactly the kind of thing a COPY
# rearranges silently -- so the build refuses to produce an image that cannot
# load it. One line, and it fails where it is cheap to fix.
RUN .venv/bin/python -c "\
from ddn.contract import load_model; \
from ddn.returns import load_model as returns_model; \
from ddn.solver_adapter.problem import load_pickup_model; \
load_model(); returns_model(); load_pickup_model()"


FROM python:3.13-slim-bookworm AS runtime

# Not root: nothing here needs it, and the API is the process most exposed.
RUN useradd --create-home --uid 10001 ddn
WORKDIR /app

COPY --from=build --chown=ddn:ddn /app/.venv /app/.venv
COPY --from=build --chown=ddn:ddn /app/ddn /app/ddn
COPY --from=build --chown=ddn:ddn /app/models /app/models

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER ddn

EXPOSE 8000
CMD ["uvicorn", "ddn.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
