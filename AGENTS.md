# Project working instructions

Read `docs/initial-design.md`, `plans/status.md`, and the active phase plan before
implementing a phase. Follow the existing architecture and patterns.

## Development

- Prefer uv, Black, logging, and Pydantic for Python/backend work.
- Prefer pnpm, TypeScript, Vite, and shadcn for frontend work.
- Add UI controls only when they serve a useful action or explain relevant state.
- Run API, worker, frontend, and sandbox services directly on the host, as in Nexus.
  Docker runs infrastructure only. Building a microVM guest image is permitted.
- Use RustFS as the default blob storage in the local Compose stack.
- Start Docker when needed for infrastructure checks if the user has stopped it.
- The user has configured model credentials in `.env` and authorized live calls.
  Keep credentials private and outside commits, logs, and prompts.
- Luna 6 subagents may implement bounded subtasks when useful.

## Validation

- Use deterministic tests and focused integration checks for routine edits.
- Reuse saved live results. Run model/microVM live tests at phase milestones or
  when a material change or unresolved failure needs them; avoid repeating them
  after every edit.
- Record LLM reasoning failures without repeated tuning for a perfect live run.
  Keep deterministic logic, bounds, provenance, and failure handling as gates.
- Retrieval accuracy may be imperfect in the current baseline. Record recall and
  misses honestly; defer further ranking improvements to the relevant phase.
- Full UI verification is deferred by the user. Run frontend type/build checks
  and verify backend contracts without treating complete browser QA as a gate.

## Commits and current scope

Commit between phases and coherent implementation milestones. Use:

```text
<type>(<scope>): <subject>

- Optional detailed bullet points.

Optional issue references or breaking-change notes.
```

Types: `chore`, `docs`, `feat`, `fix`, `refactor`, `style`, `test`.
Scopes: `backend`, `frontend`, `infra`, `general`. Keep subjects at most 50 characters.

The user has authorized phase 07 after the completed phase 06 commits.
Implement and commit phase 07, then stop. Phase 08 requires a later request.
Language quality is a first-pass baseline: record misses without repeated tuning.
OCR quality measurements are descriptive; prioritize code logic, routing,
bounds, provenance, and failure handling over model-dependent accuracy tuning.
