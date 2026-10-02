SHELL := /bin/bash

COMPOSE = docker compose -f infra/compose.yaml
BACKEND = cd backend && uv run

.PHONY: setup up up-all down logs health migrate format check test frontend-check \
	test-integration live-model live-sandbox fixtures

setup:
	@test -f .env || cp .env.example .env
	cd backend && uv sync --locked
	cd frontend && pnpm install --frozen-lockfile

# Start PostgreSQL, RustFS, and initialize the application bucket.
up:
	$(COMPOSE) --profile fullstack up -d db rustfs
	$(COMPOSE) --profile fullstack run --build --rm bucket-init

up-all:
	$(COMPOSE) --profile fullstack up -d --build

down:
	$(COMPOSE) --profile fullstack down

logs:
	$(COMPOSE) --profile fullstack logs -f

health:
	$(COMPOSE) --profile fullstack ps
	@curl --fail --silent --show-error http://127.0.0.1:$${RUSTFS_API_PORT:-19000}/health/ready
	@if $(COMPOSE) --profile fullstack ps --services --status running | grep -qx api; then \
		curl --fail --silent --show-error http://127.0.0.1:$${API_PORT:-8000}/api/health; \
	fi

migrate:
	$(COMPOSE) --profile fullstack run --rm api uv run alembic upgrade head

format:
	$(BACKEND) black app tests
	cd frontend && pnpm format

check:
	cd backend && uv run black --check app tests
	cd backend && uv run mypy
	cd backend && uv run pytest
	$(MAKE) frontend-check

test:
	cd backend && uv run pytest

frontend-check:
	cd frontend && pnpm typecheck && pnpm build

test-integration:
	cd backend && uv run pytest -m integration

live-model:
	cd backend && uv run pytest -m live_model

live-sandbox:
	cd backend && uv run pytest -m live_sandbox

fixtures:
	cd backend && uv run python ../evals/generators/generate_v1.py
