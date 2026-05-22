.PHONY: install run dev test test-integration lint typecheck migrate fmt check clean

PIP ?= $(shell command -v uv >/dev/null && echo "uv pip" || echo "pip")

install:
	$(PIP) install -e ".[dev]"
	pre-commit install || true

run:
	uvicorn agent_service.server:app --host $${MCP_SERVER_HOST:-0.0.0.0} --port $${MCP_SERVER_PORT:-5000}

dev:
	uvicorn agent_service.server:app --reload --host 127.0.0.1 --port $${MCP_SERVER_PORT:-5000}

# Unit tests only (connects to Helium via $DATABASE_URL, TRUNCATEs mneme tables between tests)
test:
	pytest -q -m "not integration"

# Integration tests — require HELIUM Postgres + live saaz upstream
test-integration:
	MNEME_INTEGRATION=1 pytest -q -m integration -v

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

typecheck:
	mypy agent_service

# Run all checks (lint + typecheck + unit tests)
check: lint typecheck test

# Apply only the new migration (0001 already applied on Helium by Replit Agent)
migrate:
	@psql "$$DATABASE_URL" -f migrations/0002_sessions.sql

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
