.PHONY: install run dev test lint typecheck migrate fmt check

# Use uv if available, fall back to pip
PIP ?= $(shell command -v uv >/dev/null && echo "uv pip" || echo "pip")

install:
	$(PIP) install -e ".[dev]"
	pre-commit install || true

run:
	uvicorn agent_service.server:app --host $${MCP_SERVER_HOST:-0.0.0.0} --port $${MCP_SERVER_PORT:-8000}

dev:
	uvicorn agent_service.server:app --reload --host 127.0.0.1 --port $${MCP_SERVER_PORT:-8000}

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

typecheck:
	mypy agent_service

check: lint typecheck test

migrate:
	@psql "$$DATABASE_URL" -f migrations/0001_init.sql

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
