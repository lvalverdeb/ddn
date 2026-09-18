# syntax=docker/dockerfile:1.7
#
# DDN in a container. Two stages, because the build needs credentials the
# runtime must never see.
#
# **This image cannot be built without access to a second private repository.**
# `pyproject.toml` pins the platform by git tag:
#   vrp-platform[pyvrp] @ git+https://github.com/lvalverdeb/osrm-microservice
# so `uv sync` clones it. That is the first thing a new person hits, and it
# fails with a git authentication error that names neither repository.
#
# The token is passed as a BuildKit secret and read into git's environment for
# one RUN, never written to a file. `git config --global` would have put it in
# a layer, where `docker history` would show it.

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
RUN --mount=type=secret,id=gh_token,required=false \
    --mount=type=ssh \
    if [ -s /run/secrets/gh_token ]; then \
        GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/gh_token)@github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/" \
        uv sync --frozen --no-install-project --extra api; \
    else \
        GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.git@github.com:.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/" \
        uv sync --frozen --no-install-project --extra api; \
    fi

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
RUN --mount=type=secret,id=gh_token,required=false \
    uv sync --frozen --extra api --no-editable


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
