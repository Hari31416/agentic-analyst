# Agentic RAG Analyst

An on-prem analyst workspace for documents, SQL, spreadsheets, and generated
Python. The app keeps source files immutable, stores objects in S3-compatible
storage, and sends code execution to the configured sandbox service.

## Start locally

Install Docker Compose, `uv`, Node.js with Corepack, and `pnpm`. Then run:

```bash
make setup
make up
```

`make up` starts PostgreSQL 17 with pgvector and RustFS, then builds the backend
image and creates the application bucket. `make up-all` also starts the API,
worker, and Vite development server. The frontend installs its lockfile
dependencies in the container and reloads as you edit files.

Open <http://127.0.0.1:5173>. The API listens on port 8000 and reports health at
<http://127.0.0.1:8000/api/health>. The S3 endpoint is
<http://127.0.0.1:19000> and the RustFS console is
<http://127.0.0.1:19001>. Both storage ports bind to loopback. The local
development credentials are `analyst-dev` and `analyst-dev-secret`; change them
in `.env` if you share this Docker host. Override `POSTGRES_PORT`, `API_PORT`,
`FRONTEND_PORT`, `RUSTFS_API_PORT`, or `RUSTFS_CONSOLE_PORT` if those host ports
are occupied. PostgreSQL and the S3 bucket persist in Docker volumes. RustFS
runs as UID 10001, so a small startup service sets ownership on its named data
volume before RustFS opens it. PostgreSQL uses host port 55432 to avoid
collisions with an existing local PostgreSQL server.

Run `make migrate` after the stack is up to apply Alembic migrations. Use
`make logs` to follow service logs and `make down` to stop the Compose stack.
`make down` keeps data volumes; `docker compose -f infra/compose.yaml --profile
fullstack down --volumes` removes them. Run `make up` again to recreate the
storage bucket after removing volumes.

## Sandbox service on macOS

The sandbox is a separate service. The local Mac setup was checked against
`/Users/hari/Desktop/Miscs/nexus/nexus/sandbox` at revision
`b3f032b6a0ce1fab7cebc75250073f58b5b6c63c`. Its defaults use the `microsandbox`
backend, listen on port 8787, and select
`hari31416/data-science-heavy-runtime:py312-v2`. On the Mac, start it in a
separate terminal:

```bash
cd /Users/hari/Desktop/Miscs/nexus/nexus/sandbox
just setup
just start
just health
```

The service README lists `msb` as an optional prerequisite for the microVM
backend. Keep `SANDBOX_DEFAULT_BACKEND=microsandbox`; the local subprocess
backend does not provide the required isolation. For the host-run backend,
`SANDBOX_BASE_URL` defaults to `http://127.0.0.1:8787`. Compose uses
`SANDBOX_DOCKER_BASE_URL=http://host.docker.internal:8787` so containers can
reach the Mac service. Set `SANDBOX_AUTH_TOKEN` if sandbox authentication is
enabled, and keep the sandbox image aligned with the one supported by that
service.

The inspected parent Nexus checkout is
`/Users/hari/Desktop/Miscs/nexus/nexus` at revision
`1ff59b665cb978383ddfb17c64b301d4bc3d59d6`. Those repositories are references;
this project calls the sandbox over HTTP and does not include its runtime.

## Configuration and commands

Copy `.env.example` to `.env` if `make setup` has not done so. Compose sets
`STORAGE_BACKEND=s3` and uses RustFS at `http://rustfs:9000`. Running the backend
on the host uses `STORAGE_BACKEND=filesystem` and `STORAGE_ROOT=../data`.
`make up` creates bucket `analyst` through the backend's storage initializer
before the API or worker starts. Compose defaults for the local S3 credentials
are for development and the ports listen on loopback.

Set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` for the explicitly
configured OpenAI-compatible endpoint. Leaving them blank keeps the local
stack available without live model calls. Generate and set
`DATABASE_ENCRYPTION_KEY` before storing external database credentials.

Useful commands:

```bash
make health             # Compose status and RustFS/API health
make migrate            # Apply database migrations
make format             # Format backend and frontend sources
make check              # Backend tests and frontend typecheck/build
make test-integration   # Integration-marked backend tests
make live-model         # Explicit live model checks
make live-sandbox       # Explicit sandbox checks
```

The API and worker share one backend package. The worker runs
`python -m app.workers.main`; the frontend proxies API requests to `api:8000`.
For direct host development, install packages with `make setup` and run the
backend from `backend/` with `uv run uvicorn app.main:app --reload` and the
frontend from `frontend/` with `pnpm dev --host 0.0.0.0`. In that setup, use a
host-resolvable `DATABASE_URL` and `SANDBOX_BASE_URL` in `.env`. The
filesystem adapter remains the host-development default.

## RustFS image

Compose uses the pinned `ghcr.io/rustfs/rustfs:1.0.0-glibc` image digest
`sha256:bffcab0c9d647aab0055d1c69d340b202d0909966b385932d4ead1aeb7602858`.
RustFS 1.0.0 is the latest stable release; current preview builds are not used.
The published image supports both `linux/amd64` and `linux/arm64`, including
Apple Silicon Docker Desktop. RustFS runs as UID 10001 and requires its data
directory to be writable by that user. See the [RustFS release list](https://github.com/rustfs/rustfs/releases),
[official image package](https://github.com/rustfs/rustfs/pkgs/container/rustfs),
and [container setup docs](https://docs.rustfs.com/en/installation/container).
