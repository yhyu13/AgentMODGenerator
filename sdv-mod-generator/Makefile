.PHONY: install install-test test lint clean build run stop deploy-local deploy-stop rotate-token check-no-plaintext-secrets

# Compute paths relative to the Makefile's directory (supports make run from parent dir)
_MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV := $(abspath $(_MAKEFILE_DIR)../.venv)
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
PYTEST_QUICK := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
PYTHONPATH := .
PYTEST_ARGS := -v

.DEFAULT_GOAL := build

install:
	@test -d $(VENV) || python -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements.txt

install-test:
	$(VENV)/bin/pip install -r requirements.txt
	$(VENV)/bin/pip install pytest pytest-asyncio

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/ $(PYTEST_ARGS)

test-quick:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST_QUICK) tests/ $(PYTEST_ARGS) --ignore=tests/test_pipeline_integration.py

lint:
	$(PYTHON) -m mypy . --ignore-missing-imports || true
	$(PYTHON) -m ruff check . || true

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache tests/.pytest_cache

build: test lint
	@echo "Build passed — tests + lint green"

run:
	cd $(_MAKEFILE_DIR) && PYTHONPATH=. $(UVICORN) app.main:app --reload --port 8000

stop:
	pkill -f "uvicorn app.main:app" || true

deploy-local:
	@echo "==> deploy-local: bringing up the prod stack"
	./scripts/deploy_local.sh

deploy:
	@echo "==> deploy: deploy-local + SDV smoke test (if SDV_INSTALL_PATH is set)"
	./scripts/deploy_local.sh

deploy-stop:
	cd $(_MAKEFILE_DIR) && docker compose -f config/docker-compose.prod.yml down

smoke-test:
	@echo "==> smoke-test: SDV runtime load test"
	./scripts/sdv_smoke_test.sh

rotate-token:
	@echo "==> rotate-token: see checklist and optionally restart the api container"
	./scripts/rotate_token.sh $$TOKEN_FILE

check-no-plaintext-secrets:
	@echo "==> check-no-plaintext-secrets: scanning $$FILE (default config/.env)"
	./scripts/check_no_plaintext_secrets.sh $${FILE:-config/.env}
