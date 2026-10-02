SHELL := /bin/bash

COMPOSE = docker compose $(if $(wildcard .env),--env-file .env) -f infra/compose.yaml
BACKEND = cd backend && uv run

.PHONY: help setup up up-all dev api worker frontend sandbox down logs health migrate format check test frontend-check \
	test-integration live-model live-sandbox live-agent fixtures analysis-image eval-infra embedding-model live-documents ocr-fixtures live-ingestion \
	start stop restart app-start app-stop app-restart start-backend stop-backend start-worker stop-worker start-frontend stop-frontend start-sandbox stop-sandbox \
	status logs-backend logs-worker logs-frontend logs-sandbox logs-save clean-logs

help:
	@echo "Agentic RAG Analyst - Available Commands"
	@echo "========================================"
	@echo ""
	@echo "Full Stack:"
	@echo "  make start          - Start infrastructure, migrate, and start all apps in background"
	@echo "  make stop           - Stop all background apps and infrastructure"
	@echo "  make restart        - Restart all background apps and infrastructure"
	@echo "  make up-all         - Start infrastructure, migrate, and run dev in foreground"
	@echo ""
	@echo "Infrastructure (Docker):"
	@echo "  make up             - Start infrastructure (PostgreSQL + RustFS)"
	@echo "  make down           - Stop infrastructure"
	@echo "  make migrate        - Run database migrations"
	@echo ""
	@echo "Application (Background with Logs):"
	@echo "  make app-start      - Start backend, worker, and frontend in background"
	@echo "  make app-stop       - Stop backend, worker, frontend, and sandbox"
	@echo "  make app-restart    - Restart backend, worker, and frontend"
	@echo "  make start-backend  - Start backend API in background"
	@echo "  make stop-backend   - Stop backend API"
	@echo "  make start-worker   - Start background worker in background"
	@echo "  make stop-worker    - Stop background worker"
	@echo "  make start-frontend - Start Vite frontend in background"
	@echo "  make stop-frontend  - Stop Vite frontend"
	@echo "  make start-sandbox  - Start sandbox service in background"
	@echo "  make stop-sandbox   - Stop sandbox service"
	@echo ""
	@echo "Interactive Development (Foreground):"
	@echo "  make dev            - Run API, worker, and frontend together in foreground"
	@echo "  make api            - Run API in foreground"
	@echo "  make worker         - Run worker in foreground"
	@echo "  make frontend       - Run frontend in foreground"
	@echo "  make sandbox        - Run sandbox service in foreground"
	@echo ""
	@echo "Logs & Status:"
	@echo "  make status         - Show infrastructure and local process status"
	@echo "  make health         - Health check for infrastructure and API"
	@echo "  make logs           - View Docker infrastructure logs (follow)"
	@echo "  make logs-backend   - View local backend logs (follow)"
	@echo "  make logs-worker    - View local worker logs (follow)"
	@echo "  make logs-frontend  - View local frontend logs (follow)"
	@echo "  make logs-sandbox   - View local sandbox logs (follow)"
	@echo "  make logs-save      - Save infrastructure logs to timestamped file"
	@echo "  make clean-logs     - Remove local log files"
	@echo ""
	@echo "Quality & Testing:"
	@echo "  make check          - Run all backend checks and frontend typecheck/build"
	@echo "  make test           - Run backend unit tests"
	@echo "  make format         - Format backend and frontend"

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

# Local extraction/OCR metrics. Does not call the chat model or sandbox.
ocr-fixtures:
	backend/.venv/bin/python evals/generators/generate_v2.py

live-ingestion:
	$(BACKEND) python -m app.ingestion_probes

# Local background lifecycle targets with logs
start-backend:
	@mkdir -p logs
	@if [ ! -d "backend/.venv" ]; then \
		echo "Virtual environment not found. Setting up..."; \
		$(MAKE) setup; \
	fi
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	PORT=$${API_PORT:-8000}; \
	if lsof -ti:$$PORT >/dev/null 2>&1; then \
		echo "Backend already running on port $$PORT"; \
	else \
		echo "Starting backend locally..."; \
		(cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port $$PORT --reload > ../logs/backend.log 2>&1 &); \
		echo "Backend started on port $$PORT"; \
		echo "Backend logs: logs/backend.log"; \
	fi

stop-backend:
	@echo "Stopping backend..."
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	PORT=$${API_PORT:-8000}; \
	lsof -ti:$$PORT | xargs kill -9 2>/dev/null || true; \
	pkill -f 'uvicorn app.main:app' 2>/dev/null || true; \
	echo "Backend stopped"

start-worker:
	@mkdir -p logs
	@if [ ! -d "backend/.venv" ]; then \
		echo "Virtual environment not found. Setting up..."; \
		$(MAKE) setup; \
	fi
	@if pgrep -f 'python -m app.workers.main' >/dev/null 2>&1; then \
		echo "Worker already running"; \
	else \
		echo "Starting worker..."; \
		(cd backend && uv run python -m app.workers.main > ../logs/worker.log 2>&1 &); \
		echo "Worker started"; \
		echo "Worker logs: logs/worker.log"; \
	fi

stop-worker:
	@echo "Stopping worker..."
	@pkill -f 'python -m app.workers.main' 2>/dev/null || pkill -f 'app.workers.main' 2>/dev/null || true; \
	echo "Worker stopped"

start-frontend:
	@mkdir -p logs
	@if [ ! -d "frontend/node_modules" ]; then \
		echo "Frontend dependencies not found. Installing..."; \
		cd frontend && pnpm install --frozen-lockfile; \
	fi
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	API_PORT=$${API_PORT:-8000}; \
	PORT=$${FRONTEND_PORT:-5173}; \
	if lsof -ti:$$PORT >/dev/null 2>&1; then \
		echo "Frontend already running on port $$PORT"; \
	else \
		echo "Starting frontend locally..."; \
		(cd frontend && API_PROXY_URL="http://127.0.0.1:$$API_PORT" pnpm dev --host 127.0.0.1 --port $$PORT > ../logs/frontend.log 2>&1 &); \
		echo "Frontend started on port $$PORT"; \
		echo "Frontend logs: logs/frontend.log"; \
	fi

stop-frontend:
	@echo "Stopping frontend..."
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	PORT=$${FRONTEND_PORT:-5173}; \
	lsof -ti:$$PORT | xargs kill -9 2>/dev/null || true; \
	pkill -f 'vite' 2>/dev/null || true; \
	echo "Frontend stopped"

start-sandbox:
	@mkdir -p logs
	@if lsof -ti:8787 >/dev/null 2>&1; then \
		echo "Sandbox service already running on port 8787"; \
	elif [ -f ".sandbox/venv/bin/python" ]; then \
		echo "Starting sandbox service..."; \
		(.sandbox/venv/bin/python scripts/run-sandbox.py > logs/sandbox.log 2>&1 &); \
		echo "Sandbox service started on port 8787"; \
		echo "Sandbox logs: logs/sandbox.log"; \
	else \
		echo "Sandbox virtualenv not found (.sandbox/venv). Run 'bash scripts/setup-sandbox.sh' first."; \
	fi

stop-sandbox:
	@echo "Stopping sandbox service..."
	@lsof -ti:8787 | xargs kill -9 2>/dev/null || true; \
	pkill -f 'sandbox_service.main:app' 2>/dev/null || true; \
	echo "Sandbox service stopped"

app-start:
	@echo "Starting application services..."
	@$(MAKE) start-backend
	@$(MAKE) start-worker
	@$(MAKE) start-frontend
	@echo "Application services started"

app-stop:
	@echo "Stopping application services..."
	@$(MAKE) stop-backend
	@$(MAKE) stop-worker
	@$(MAKE) stop-frontend
	@$(MAKE) stop-sandbox
	@echo "Application services stopped"

app-restart:
	@$(MAKE) app-stop
	@sleep 1
	@$(MAKE) app-start

start:
	@echo "Starting full stack..."
	@$(MAKE) up
	@$(MAKE) migrate
	@$(MAKE) app-start
	@echo ""
	@echo "All services started."
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	echo "  Frontend:    http://127.0.0.1:$${FRONTEND_PORT:-5173}"; \
	echo "  Backend API: http://127.0.0.1:$${API_PORT:-8000}"; \
	echo "  RustFS:      http://127.0.0.1:$${RUSTFS_CONSOLE_PORT:-19001}"

stop:
	@echo "Stopping full stack..."
	@$(MAKE) app-stop
	@$(MAKE) down
	@echo "Full stack stopped"

restart:
	@$(MAKE) stop
	@sleep 1
	@$(MAKE) start

status:
	@echo "Infrastructure Services:"
	@$(COMPOSE) ps
	@echo ""
	@echo "Local Processes:"
	@pgrep -fl 'uvicorn app.main:app|app.workers.main|vite|sandbox_service' || echo "No local backend/worker/frontend processes running"
	@echo ""
	@echo "Health Checks:"
	@set -a; [ -f ./.env ] && . ./.env; set +a; \
	curl --fail --silent --show-error http://127.0.0.1:$${RUSTFS_API_PORT:-19000}/health/ready >/dev/null 2>&1 && echo "  RustFS: healthy" || echo "  RustFS: unavailable"; \
	curl --fail --silent --show-error http://127.0.0.1:$${API_PORT:-8000}/api/health >/dev/null 2>&1 && echo "  Backend API: healthy" || echo "  Backend API: unavailable"

logs-backend:
	@if [ -f logs/backend.log ]; then \
		echo "Viewing backend logs (Ctrl-C to exit)..."; \
		tail -f logs/backend.log; \
	else \
		echo "logs/backend.log not found"; \
	fi

logs-worker:
	@if [ -f logs/worker.log ]; then \
		echo "Viewing worker logs (Ctrl-C to exit)..."; \
		tail -f logs/worker.log; \
	else \
		echo "logs/worker.log not found"; \
	fi

logs-frontend:
	@if [ -f logs/frontend.log ]; then \
		echo "Viewing frontend logs (Ctrl-C to exit)..."; \
		tail -f logs/frontend.log; \
	else \
		echo "logs/frontend.log not found"; \
	fi

logs-sandbox:
	@if [ -f logs/sandbox.log ]; then \
		echo "Viewing sandbox logs (Ctrl-C to exit)..."; \
		tail -f logs/sandbox.log; \
	else \
		echo "logs/sandbox.log not found"; \
	fi

logs-save:
	@mkdir -p logs
	@$(COMPOSE) logs --no-color > logs/infra-$$(date +%Y%m%d-%H%M%S).log 2>/dev/null || true
	@echo "Saved infrastructure logs to logs/"

clean-logs:
	@rm -f logs/*.log
	@echo "Cleaned log files in logs/"
