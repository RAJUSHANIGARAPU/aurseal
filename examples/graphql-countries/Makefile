.DEFAULT_GOAL := help
.PHONY: help install lint fmt typecheck test-offline test smoke report check clean docker-test

PYTEST := uv run pytest

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## create .venv from uv.lock
	uv sync

lint: ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy, strict on src/
	uv run mypy

test-offline: ## tests that need no network access to the target
	$(PYTEST) -m offline

test: ## the whole suite, in parallel
	$(PYTEST) -n auto

smoke: ## the short live check
	$(PYTEST) -m smoke

report: ## render the report and print where it is
	@echo "HTML report: reports/report.html"

check: lint typecheck test-offline ## everything CI checks before the live run

docker-test: ## run the suite in the container
	docker compose run --rm tests

clean: ## remove reports and caches
	rm -rf reports test-results .pytest_cache .mypy_cache .ruff_cache
