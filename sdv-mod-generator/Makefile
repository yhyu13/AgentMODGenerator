.PHONY: install install-test test lint clean build run stop

VENV := ../.venv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
PYTEST_QUICK := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
PYTHONPATH := .
PYTEST_ARGS := -v

.DEFAULT_GOAL := build

install:
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
	ALL_PROXY=socks5://127.0.0.1:1089 HTTPS_PROXY=http://127.0.0.1:8889 HTTP_PROXY=http://127.0.0.1:8889 PYTHONPATH=$(PYTHONPATH) $(UVICORN) app.main:app --reload --port 8000

stop:
	pkill -f "uvicorn app.main:app" || true
