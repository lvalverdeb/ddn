# DDN — the front door.
#
# `make` on its own lists what there is. `make bootstrap` gets the §13 service
# running; `make test` needs nothing but Python.
#
# Two flows, deliberately separate. The suite runs on the host with no
# services at all — no Redis, no gateway, no routing data — because that
# promise is what makes this repository cheap to pick up. Docker is for
# running the *service*, which does need a queue.

COMPOSE ?= docker compose
UV      ?= uv
PORT    ?= 8000

# Which daemon to build and run on. Both are Docker's own variables; naming
# them here means `make` passes them on and `make where` can say which one it
# will use, rather than leaving you to infer it from a connection error.
#
#   make bootstrap DOCKER_HOST=ssh://ops@buildbox
#   DOCKER_HOST=tcp://10.211.55.28:2375 make up
#   make up DOCKER_CONTEXT=colima
#
# Unset, Docker uses the current context — usually the local socket. Only
# exported when set, because an empty DOCKER_HOST is not the same as no
# DOCKER_HOST to every version of the CLI.
DOCKER_HOST ?=
DOCKER_CONTEXT ?=
ifneq ($(strip $(DOCKER_HOST)),)
export DOCKER_HOST
endif
ifneq ($(strip $(DOCKER_CONTEXT)),)
export DOCKER_CONTEXT
endif

.DEFAULT_GOAL := help
.PHONY: help install test lint fmt check bootstrap up down restart logs ps \
	shell api worker redis test-docker rebuild clean where \
	guard-token guard-docker

help:  ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- on the host

install:  ## Install everything, including the API extras
	@command -v $(UV) >/dev/null || { echo "uv is not installed: https://docs.astral.sh/uv/"; exit 1; }
	$(UV) sync --extra dev --extra api

test: install  ## Run the suite (no services needed)
	$(UV) run pytest tests/ -q

lint: install  ## Ruff
	$(UV) run ruff check .

fmt: install  ## Ruff, fixing what it can
	$(UV) run ruff check --fix .

check: lint test  ## What CI would run, if there were CI

# ------------------------------------------------------------------- services

bootstrap: guard-docker guard-token  ## Build the images and start the stack
	$(COMPOSE) build
	$(COMPOSE) up -d
	@echo
	@echo "  API      http://localhost:$(PORT)"
	@echo "  OpenAPI  http://localhost:$(PORT)/docs   (§13.2's twenty-three endpoints)"
	@echo "  logs     make logs"
	@echo

up: guard-docker guard-token  ## Start the stack without rebuilding
	$(COMPOSE) up -d

down:  ## Stop the stack, keeping the queue's data
	$(COMPOSE) down

restart: down up  ## Stop and start

logs:  ## Follow the logs
	$(COMPOSE) logs -f

ps:  ## What is running
	$(COMPOSE) ps

shell: guard-docker  ## A shell in the API image
	$(COMPOSE) run --rm --entrypoint sh api

redis: guard-docker  ## Just Redis, for running the API on the host
	$(COMPOSE) up -d redis

api: redis  ## Run the API on the host against the container's Redis
	DDN_REDIS_DSN=redis://localhost:6379/0 \
		$(UV) run uvicorn ddn.api.app:create_app --factory --reload --port $(PORT)

worker: redis  ## Run an Arq worker on the host (§13.1)
	DDN_REDIS_DSN=redis://localhost:6379/0 \
		$(UV) run arq ddn.api.jobs.WorkerSettings

test-docker: guard-docker guard-token  ## Run the suite inside the shipped image
	$(COMPOSE) run --rm tests

rebuild: guard-docker guard-token  ## Rebuild from scratch
	$(COMPOSE) build --no-cache

clean:  ## Stop everything and drop the queue's volume
	$(COMPOSE) down -v --remove-orphans
	rm -rf .pytest_cache .ruff_cache

where:  ## Which Docker daemon these targets will use
	@echo "DOCKER_HOST     = $${DOCKER_HOST:-(unset — using the current context)}"
	@echo "DOCKER_CONTEXT  = $${DOCKER_CONTEXT:-(unset)}"
	@docker context show 2>/dev/null | sed 's/^/context         = /' || true
	@if docker info >/dev/null 2>&1; then \
		docker info --format 'daemon          = {{.Name}} ({{.ServerVersion}})'; \
	else \
		echo "daemon          = unreachable"; \
	fi

# --------------------------------------------------------------------- guards

# The build clones a *second* private repository. Saying so here, by name, is
# the difference between a clear stop and a git authentication error that
# mentions neither repository — the same reason `simulation/on_road.py` asks
# for its two inputs by name rather than guessing them.
guard-token:
	@test -n "$$GH_TOKEN" || { \
		echo "GH_TOKEN is unset."; \
		echo; \
		echo "  The image build installs vrp-platform from"; \
		echo "  github.com/lvalverdeb/osrm-microservice, which is private."; \
		echo "  Without a token 'uv sync' fails inside the build with a git"; \
		echo "  authentication error that names neither repository."; \
		echo; \
		echo "    export GH_TOKEN=\$$(gh auth token)"; \
		echo; \
		echo "  Or build over SSH instead: the Dockerfile falls back to an"; \
		echo "  ssh-agent mount when GH_TOKEN is empty (ssh-add -l to check)."; \
		exit 1; }

# Reaching no daemon is the other failure that arrives as a wall of Go stack
# rather than a sentence. Say which endpoint was tried, and how to point
# somewhere else.
guard-docker:
	@docker info >/dev/null 2>&1 || { \
		echo "No Docker daemon at $${DOCKER_HOST:-the current context}."; \
		echo; \
		echo "  Start one, or point these targets at another:"; \
		echo; \
		echo "    make bootstrap DOCKER_HOST=ssh://user@host"; \
		echo "    make bootstrap DOCKER_CONTEXT=colima"; \
		echo; \
		echo "  'make where' shows which one is currently selected."; \
		echo "  'make test' needs no daemon at all."; \
		exit 1; }
