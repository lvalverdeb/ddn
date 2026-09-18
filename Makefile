# DDN — the front door.
#
# `make` on its own lists what there is. `make bootstrap` gets the §13 service
# running; `make test` needs nothing but Python.
#
# Two flows, deliberately separate. The suite runs on the host with no
# services at all — no Redis, no gateway, no routing data — because that
# promise is what makes this repository cheap to pick up. Docker is for
# running the *service*, which does need a queue.

# Configuration is environment, not arguments. `make` imports the environment,
# so every variable below can be set in your shell, in `.env` (which compose
# reads by itself), or inline -- and the same name reaches compose, the
# container and the host command. `make config` prints what is in force.
#
#   export DDN_API_PORT=9000
#   export DOCKER_HOST=ssh://ops@buildbox
#   DDN_API_PORT=9000 make up
#
# `DDN_API_PORT` used to be `PORT` here and `DDN_API_PORT` in compose, so
# setting one moved the URL this file printed and not the port compose
# published. One fact, one name.
DDN_API_PORT   ?= 8000
DDN_IMAGE      ?= ddn-api:latest
DDN_REDIS_DSN  ?= redis://localhost:6379/0
COMPOSE        ?= docker compose
UV             ?= uv


# Exported only when *you* set them. Exporting make's own default would put
# `DDN_API_PORT=8000` in compose's environment, where it outranks `.env` --
# so writing the port in `.env` would have had no effect when invoked through
# `make`. `origin` is "file" when the `?=` above supplied the value and
# "environment" or "command line" when you did.
ifneq ($(origin DDN_API_PORT),file)
export DDN_API_PORT
endif
ifneq ($(origin DDN_IMAGE),file)
export DDN_IMAGE
endif

# Which daemon to build and run on. Both are Docker's own variables; naming
# them here means `make` passes them on and `make config` can say which one it
# will use, rather than leaving you to infer it from a connection error.
#
#   export DOCKER_HOST=ssh://user@host      # or an ~/.ssh/config alias
#   export DOCKER_CONTEXT=colima
#
# Prefer `ssh://` over `tcp://`: a daemon reached over ssh needs no open
# 2375, which is an unauthenticated root-equivalent socket. This example was
# a literal `tcp://` address until the host behind it moved and the comment
# went on pointing at nothing.
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
.PHONY: help install test lint fmt check notebooks nb-clean bootstrap up down logs ps \
	shell api worker redis rebuild clean config daemon-host \
	guard-docker

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

nb-clean: install  ## Strip stored outputs from the notebooks before committing
	$(UV) run python notebooks/clear_outputs.py

notebooks:  ## Open the notebooks in `notebooks/`
	@command -v $(UV) >/dev/null || { echo "uv is not installed"; exit 1; }
	$(UV) sync --extra dev --extra api --extra notebooks
	$(UV) run jupyter lab notebooks/

# ------------------------------------------------------------------- services

bootstrap: guard-docker  ## Build the images and start the stack
	$(COMPOSE) build
	$(COMPOSE) up -d --wait
	@port=$$($(COMPOSE) port api 8000 2>/dev/null | sed 's/.*://'); \
	port=$${port:-$(DDN_API_PORT)}; \
	host=$$($(MAKE) --no-print-directory daemon-host); \
	echo; \
	echo "  API      http://$$host:$$port"; \
	echo "  OpenAPI  http://$$host:$$port/docs   (§13.2's twenty-three endpoints)"; \
	echo "  logs     make logs"; \
	echo

up: guard-docker  ## Start the stack without rebuilding
	$(COMPOSE) up -d

down:  ## Stop the stack, keeping the queue's data
	$(COMPOSE) down

logs:  ## Follow the logs
	$(COMPOSE) logs -f

ps:  ## What is running
	$(COMPOSE) ps

shell: guard-docker  ## A shell in the API image
	$(COMPOSE) run --rm --entrypoint sh api

redis: guard-docker  ## Just Redis, for running the API on the host
	$(COMPOSE) up -d redis

api: redis  ## Run the API on the host against the container's Redis
	DDN_REDIS_DSN=$(DDN_REDIS_DSN) \
		$(UV) run uvicorn ddn.api.app:create_app --factory --reload --port $(DDN_API_PORT)

worker: redis  ## Run an Arq worker on the host (§13.1)
	DDN_REDIS_DSN=$(DDN_REDIS_DSN) \
		$(UV) run arq ddn.api.jobs.WorkerSettings

rebuild: guard-docker  ## Rebuild from scratch
	$(COMPOSE) build --no-cache

clean:  ## Stop everything and drop the queue's volume
	$(COMPOSE) down -v --remove-orphans
	rm -rf .pytest_cache .ruff_cache

config:  ## Print every variable in force, and which daemon they point at
	@echo "DDN_API_PORT    = $(DDN_API_PORT)   (make's value; compose's is below)"
	@echo "DDN_IMAGE       = $(DDN_IMAGE)"
	@echo "DDN_REDIS_DSN   = $(DDN_REDIS_DSN)   (host targets; compose sets its own)"
	@echo "DOCKER_HOST     = $${DOCKER_HOST:-(unset — using the current context)}"
	@echo "DOCKER_CONTEXT  = $${DOCKER_CONTEXT:-(unset)}"
	@docker context show 2>/dev/null | sed 's/^/context         = /' || true
	@if docker info >/dev/null 2>&1; then \
		docker info --format 'daemon          = {{.Name}} ({{.ServerVersion}})'; \
	else \
		echo "daemon          = unreachable"; \
	fi
	@test -f .env && echo "env file        = .env (compose reads it; make does not)" \
		|| echo "env file        = none (see .env.example)"
	@$(COMPOSE) config 2>/dev/null \
		| awk '/published:/ {gsub(/"/, "", $$2); print "api port        = " $$2 "  (compose, after .env)"; exit}' \
		|| true

# --------------------------------------------------------------------- guards

# Where the stack is actually listening. A remote daemon publishes its ports on
# *its* host, not on yours -- printing `localhost` for an ssh:// or tcp://
# daemon sends you to a port nothing is on, which is exactly what happened the
# first time this ran against a remote engine. An ssh alias is resolved through
# `ssh -G`, because the alias is a name only your ssh config knows.
daemon-host:
	@case "$$DOCKER_HOST" in \
		ssh://*) alias=$${DOCKER_HOST#ssh://}; alias=$${alias#*@}; alias=$${alias%%[:/]*}; \
			ssh -G "$$alias" 2>/dev/null | awk '/^hostname /{print $$2; found=1} END{if(!found) print "'"$$alias"'"}' | head -1 ;; \
		tcp://*) h=$${DOCKER_HOST#tcp://}; echo "$${h%%:*}" ;; \
		*) echo localhost ;; \
	esac

# Reaching no daemon is the other failure that arrives as a wall of Go stack
# rather than a sentence. Say which endpoint was tried, and how to point
# somewhere else.
guard-docker:
	@docker info >/dev/null 2>&1 || { \
		echo "No Docker daemon at $${DOCKER_HOST:-the current context}."; \
		echo; \
		echo "  Start one, or point these targets at another:"; \
		echo; \
		echo "    export DOCKER_HOST=ssh://user@host"; \
		echo "    export DOCKER_CONTEXT=colima"; \
		echo; \
		echo "  'make config' shows which one is currently selected."; \
		echo "  'make test' needs no daemon at all."; \
		exit 1; }
