.PHONY: dev lint typecheck test build run-paper validate-handoff secret-scan check

dev:
	uv sync --extra dev

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src/trader

test:
	uv run pytest

build:
	uv build

run-paper:
	TRADING_MODE=paper BROKER_ENV=demo LIVE_TRADING_ENABLED=false uv run python -m trader.main

validate-handoff:
	uv run python agent_state/validate_handoff.py

secret-scan:
	uv run python scripts/secret_scan.py

check: lint typecheck test validate-handoff secret-scan

