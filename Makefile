# DAYBOOK — `make demo` from a clean clone: venv, schema, synthetic seed, publish, all checks.
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
DEMO_ENV := DAYBOOK_DB=demo.sqlite DAYBOOK_CONFIG=demo/daybook.demo.json

.PHONY: help setup init demo check test test-worker e2e node-deps publish deploy-pages clean
PAGES_PROJECT ?= daybook

help:
	@echo "make demo        clean demo: schema + synthetic seed + publish + all checks (pytest, worker, playwright)"
	@echo "make check       run all checks on the current demo"
	@echo "make test        pytest only        make test-worker   node tests of the Worker"
	@echo "make init        create daybook.sqlite (real data; see README)"
	@echo "make deploy-pages  daybook publish + wrangler pages deploy ./dist (real data)"

setup: $(BIN)/daybook

$(BIN)/daybook: pyproject.toml
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install -q --upgrade pip
	$(BIN)/python -m pip install -q -e '.[test]'
	@touch $@

init: setup
	$(BIN)/daybook init

demo: setup
	rm -f demo.sqlite
	$(DEMO_ENV) $(BIN)/daybook init
	$(DEMO_ENV) $(BIN)/daybook demo
	$(MAKE) check

check: test test-worker e2e

test: setup
	$(BIN)/pytest -q

test-worker:
	cd worker && node --test test/*.test.mjs

# Playwright: use an installed copy if Node can resolve it, else install it locally (needs network once).
node-deps:
	@node -e "require('playwright')" 2>/dev/null || (npm install --no-save --no-audit --no-fund playwright@1 && npx playwright install chromium)

e2e: setup node-deps
	$(DEMO_ENV) node tests/e2e/check.cjs

publish: setup
	$(BIN)/daybook publish

# Real data: publish (stops on a lint finding), then deploy dist/ to Cloudflare Pages (needs `wrangler login`).
deploy-pages: publish
	cd worker && npx wrangler pages deploy ../dist --project-name $(PAGES_PROJECT) --branch main

clean:
	rm -rf dist .dist-staging demo.sqlite
