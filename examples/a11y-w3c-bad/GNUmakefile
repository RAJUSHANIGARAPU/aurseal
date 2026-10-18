# GNU make reads this file before Makefile. Makefile is rendered by the template and must
# stay byte-identical to it (.scaffold-manifest), so the local lane lives here and every
# other target is handed to Makefile unchanged.
.DEFAULT_GOAL := help
.PHONY: help test-local local-env

PYTEST := uv run pytest
LOCAL_URL := http://localhost:8304
# Every base URL the suite reads, pointed at the local target.
LOCAL_ENV := APP_BASE_URL=$(LOCAL_URL)
# These tests assert on the W3C demo itself (its title and the violations planted in it),
# so they cannot pass against the fixtures and run in the nightly live lane only.
NOT_LOCAL := --ignore=tests/a11y/test_before_after.py
COMPOSE_LOCAL := docker compose -f docker-compose.local.yml -p a11y-w3c-bad-local
LOCAL_REPORT := --html=reports/local/report.html --output=reports/local/test-results --metadata Lane local \
	--metadata Target "the example's own fixtures served by nginx (docker-compose.local.yml), not the W3C demo"

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

test-local: ## scanner and baseline checks against local fixtures (not the demo), then tear down
	@$(MAKE) --no-print-directory -f Makefile browsers
	@mkdir -p reports/local
	@export $(LOCAL_ENV); $(ONLY_LOCAL); \
	trap '$(COMPOSE_LOCAL) down --volumes --remove-orphans' EXIT; \
	$(COMPOSE_LOCAL) up --detach && \
	$(call WAIT_FOR,$(LOCAL_URL)/after/home.html) && \
	$(PYTEST) -n auto $(LOCAL_REPORT) $(NOT_LOCAL)

# Every other target is Makefile's.
GNUmakefile Makefile: ;
%::
	@$(MAKE) --no-print-directory -f Makefile $@
