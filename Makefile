.PHONY: setup format format-check lint type unit integration runtime

PYTHON := 3.14

setup:
	uv python install $(PYTHON)
	uv lock --python $(PYTHON)
	uv sync --python $(PYTHON) --frozen --all-packages

format:
	uv run --python $(PYTHON) ruff format .

format-check:
	uv run --python $(PYTHON) ruff format --check .

lint:
	uv run --python $(PYTHON) ruff check .

type:
	uv run --python $(PYTHON) mypy synora_agentic_erp/__init__.py synora_agentic_erp/hooks.py synora_agentic_erp/api.py synora_agentic_erp/gateway synora_agentic_erp/synora_agentic_erp services/agent_runtime/src tests

unit:
	uv run --python $(PYTHON) pytest

integration:
	bash env/dev/scripts/dev/env.sh app-test

runtime:
	uv run --python $(PYTHON) uvicorn agent_runtime.app:app --app-dir services/agent_runtime/src --host 127.0.0.1 --port 8001
