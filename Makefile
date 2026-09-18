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

# The build needs one of two credentials, and the Dockerfile accepts either.
# Defined once and used inline: as a prerequisite target it printed its own
# make error on top of the message, and the actionable line scrolled away.
CREDS_OK = [ -n "$$GH_TOKEN" ] || ssh-add -l >/dev/null 2>&1
NEED_CREDS = $(CREDS_OK) || { $(MAKE) --no-print-directory creds-help; exit 1; }
HAVE_IMAGE = docker image inspect $(DDN_IMAGE) >/dev/null 2>&1

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
	shell api worker redis test-docker rebuild clean where config \
	creds-help guard-docker

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

bootstrap: guard-docker  ## Build the images and start the stack
	@$(NEED_CREDS)
	$(COMPOSE) build
	$(COMPOSE) up -d
	@port=$$($(COMPOSE) port api 8000 2>/dev/null | sed 's/.*://'); \
	port=$${port:-$(DDN_API_PORT)}; \
	echo; \
	echo "  API      http://localhost:$$port"; \
	echo "  OpenAPI  http://localhost:$$port/docs   (§13.2's twenty-three endpoints)"; \
	echo "  logs     make logs"; \
	echo

up: guard-docker  ## Start the stack without rebuilding
	@$(HAVE_IMAGE) || $(NEED_CREDS)
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
	DDN_REDIS_DSN=$(DDN_REDIS_DSN) \
		$(UV) run uvicorn ddn.api.app:create_app --factory --reload --port $(DDN_API_PORT)

worker: redis  ## Run an Arq worker on the host (§13.1)
	DDN_REDIS_DSN=$(DDN_REDIS_DSN) \
		$(UV) run arq ddn.api.jobs.WorkerSettings

test-docker: guard-docker  ## Run the suite inside the shipped image
	@$(HAVE_IMAGE) || $(NEED_CREDS)
	$(COMPOSE) run --rm tests

rebuild: guard-docker  ## Rebuild from scratch
	@$(NEED_CREDS)
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

where: config  ## Alias for `config`

# --------------------------------------------------------------------- guards

# The build clones a *second* private repository, and the Dockerfile accepts
# either credential for it: a token, or an ssh-agent with a key loaded. This
# guard has to accept both, or it refuses a path the build supports -- which it
# did, until `make up` turned somebody away who had keys and no token.
#
# Only reached when something is actually going to be built. `make up` on an
# image that already exists needs no credential at all, which is what "without
# rebuilding" means.
creds-help:
	@if [ -n "$$GH_TOKEN" ]; then exit 0; fi; \
	if ssh-add -l >/dev/null 2>&1; then exit 0; fi; \
	echo "No credential for the private dependency, and no image to start."; \
	echo; \
	echo "  The image build installs vrp-platform from"; \
	echo "  github.com/lvalverdeb/osrm-microservice, which is private."; \
	echo "  Without one of these, 'uv sync' fails inside the build with a git"; \
	echo "  authentication error that names neither repository."; \
	echo; \
	echo "  Either:"; \
	echo "    export GH_TOKEN=\$$(gh auth token)"; \
	echo "  or load a key the agent can offer:"; \
	echo "    ssh-add ~/.ssh/id_ed25519      # 'ssh-add -l' to check"; \
	echo; \
	echo "  'make up' builds when there is no image yet, which is why it asks."; \
	echo "  Once one exists, starting it needs neither. Nor does 'make test',"; \
	echo "  which needs no daemon at all."; \
	true


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
