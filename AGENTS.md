# Project working instructions

Follow the existing architecture and patterns.

## Development

- Prefer uv, Black, logging, and Pydantic for Python/backend work.
- Prefer pnpm, TypeScript, Vite, and shadcn for frontend work.
- Add UI controls only when they serve a useful action or explain relevant state.
- Run API, worker, frontend, and sandbox services directly on the host.
  Docker runs infrastructure only. Building a microVM guest image is permitted.
  Keep credentials private and outside commits, logs, and prompts.
- Luna 6 subagents may implement bounded subtasks when useful.

## Validation

- Use deterministic tests and focused integration checks for routine edits.
- Record LLM reasoning failures without repeated tuning for a perfect live run.
  Keep deterministic logic, bounds, provenance, and failure handling as gates.

## Commits and current scope

Commit between phases and coherent implementation milestones. Use:

```text
<type>(<scope>): <subject>

- Optional detailed bullet points.

Optional issue references or breaking-change notes.
```

Types: `chore`, `docs`, `feat`, `fix`, `refactor`, `style`, `test`.
Scopes: `backend`, `frontend`, `infra`, `general`. Keep subjects at most 50 characters.
