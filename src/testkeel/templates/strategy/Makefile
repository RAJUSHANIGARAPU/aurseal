.DEFAULT_GOAL := help
.PHONY: help install lint test report check clean

# Pinned so the lint result does not change under you.
PYMARKDOWN := uvx --from pymarkdownlnt==0.9.41 pymarkdown --config .pymarkdown.json
DOCS := README.md TEST_PLAN.md TEST_STRATEGY.md
PLAN_SECTIONS := "Context of testing" "Assumptions and constraints" "Stakeholders and communication" \
	"Risk register" "Test strategy" "Testing activities and estimates" "Staffing" "Schedule"
STRATEGY_SECTIONS := "Quality goals" "Test levels and ownership" "Gates and metrics"

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## nothing to install: the linter runs through uvx
	@uv --version

lint: ## markdown lint
	$(PYMARKDOWN) scan $(DOCS)

test: ## every required section is present
	@for section in $(PLAN_SECTIONS); do \
		grep -Eq "^## [0-9]+\. $$section" TEST_PLAN.md || { echo "TEST_PLAN.md: missing '$$section'"; exit 1; }; \
	done
	@for section in $(STRATEGY_SECTIONS); do \
		grep -Eq "^## $$section" TEST_STRATEGY.md || { echo "TEST_STRATEGY.md: missing '$$section'"; exit 1; }; \
	done
	@echo "all required sections present"

report: ## where the published documents are
	@echo "Local: TEST_PLAN.md, TEST_STRATEGY.md; CI publishes both to GitHub Pages"

check: lint test ## everything CI checks

clean: ## remove the local site build
	rm -rf _site
