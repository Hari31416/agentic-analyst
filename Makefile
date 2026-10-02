SHELL := /bin/bash

COMPOSE = docker compose $(if $(wildcard .env),--env-file .env) -f infra/compose.yaml
BACKEND = cd backend && uv run

.PHONY: setup up up-all dev api worker frontend sandbox down logs health migrate format check test frontend-check \
	test-integration live-model live-sandbox live-agent fixtures analysis-image eval-infra embedding-model live-documents

setup:
	@test -f .env || cp .env.example .env
	cd backend && uv sync --locked
	cd frontend && pnpm install --frozen-lockfile

# Docker runs infrastructure only; bucket initialization runs on the host.
up:
	$(COMPOSE) up -d --wait
	$(BACKEND) python -m app.storage.initialize

up-all: up migrate dev

dev:
	backend/.venv/bin/python scripts/dev.py

api:
	$(BACKEND) uvicorn app.main:app --host 127.0.0.1 --port $${API_PORT:-8000} --reload

worker:
	$(BACKEND) python -m app.workers.main

frontend:
	cd frontend && pnpm dev --host 127.0.0.1 --port $${FRONTEND_PORT:-5173}

sandbox:
	.sandbox/venv/bin/python scripts/run-sandbox.py

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

health:
	$(COMPOSE) ps
	@curl --fail --silent --show-error http://127.0.0.1:$${RUSTFS_API_PORT:-19000}/health/ready
	@curl --fail --silent --show-error http://127.0.0.1:$${API_PORT:-8000}/api/health

migrate:
	$(BACKEND) alembic upgrade head

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
	cd backend && LIVE_MODEL_TESTS=1 uv run pytest -m live_model

live-sandbox:
	cd backend && LIVE_SANDBOX_ENABLED=1 uv run pytest -m live_sandbox

live-agent:
	cd backend && uv run python -m app.probes

fixtures:
	cd backend && uv run python ../evals/generators/generate_v1.py

# Image construction prepares the microVM guest, not an application container.
analysis-image:
	backend/.venv/bin/python scripts/build-analysis.py

eval-infra:
	$(COMPOSE) --profile eval up -d --wait

embedding-model:
	backend/.venv/bin/python scripts/setup-embeddings.py

live-documents:
	$(BACKEND) python -m app.document_probes
