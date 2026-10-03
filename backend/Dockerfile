FROM ghcr.io/astral-sh/uv:0.12.1 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates fonts-noto-core && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app/backend
COPY pyproject.toml uv.lock ./
RUN uv sync --locked
COPY . .
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
