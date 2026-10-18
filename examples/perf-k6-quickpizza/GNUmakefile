# GNU make reads this file before Makefile. Makefile is rendered by the template and must
# stay byte-identical to it (.scaffold-manifest), so the local lane lives here and every
# other target is handed to Makefile unchanged.
.DEFAULT_GOAL := help
.PHONY: help test-local local-env

# k6 runs in the Compose network, so the target is the service name; the host checks
# readiness through the published port.
LOCAL_URL := http://quickpizza:3333
READY_URL := http://localhost:8305/ready
LOCAL_ENV := TARGET_URL=$(LOCAL_URL)
COMPOSE_LOCAL := docker compose -f docker-compose.local.yml -p perf-k6-quickpizza-local

# Refuses to run when a base URL in the test environment names a host other than
# localhost, 127.0.0.1 or a single-label (Compose service) name.
ONLY_LOCAL = bad=$$(env | grep -E '^(APP_[A-Z_]*URL|TARGET_URL)=' \
	| grep -vE '=https?://(localhost|127\.0\.0\.1|[a-z][a-z0-9-]*)(:[0-9]+)?(/.*)?$$' || true); \
	if [ -n "$$bad" ]; then echo "test-local: non-local URL: $$bad" >&2; exit 1; fi
WAIT_FOR = for i in $$(seq 60); do curl -fs -o /dev/null $(1) && break; \
	if [ $$i = 60 ]; then echo "test-local: no answer from $(1)" >&2; exit 1; fi; sleep 2; done

help: ## list the targets
	@$(MAKE) --no-print-directory -f Makefile help
	@grep -E '^[a-z-]+:.*## ' GNUmakefile | grep -v '^help:' \
		| awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

local-env: ## the base URLs test-local gives the suite
	@echo "$(LOCAL_ENV)"

# The k6 container runs as the calling user, so it can write to the bind-mounted reports
# directory on any host. Summaries land in reports/local/ and name the target they ran on.
test-local: ## the smoke profile against a local QuickPizza, k6 in a container, then tear down
	@mkdir -p reports/local
	@export $(LOCAL_ENV); $(ONLY_LOCAL); \
	trap '$(COMPOSE_LOCAL) down --volumes --remove-orphans' EXIT; \
	$(COMPOSE_LOCAL) up --detach quickpizza && \
	$(call WAIT_FOR,$(READY_URL)) && \
	$(COMPOSE_LOCAL) run --rm --build --user "$$(id -u):$$(id -g)" k6 run --quiet k6/scenarios/smoke.js

# Every other target is Makefile's.
GNUmakefile Makefile: ;
%::
	@$(MAKE) --no-print-directory -f Makefile $@
