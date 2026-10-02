# Implementation status

Phase 00 is verified, including Compose startup, RustFS, and restart persistence.
Phase 01 implementation has begun against the shared foundation contracts.

Allowed statuses: not started, in progress, implemented / integration pending,
verified. A verified phase has evidence for every required acceptance gate.

| Phase | Status      | Verification evidence | Remaining gate   |
| ----- | ----------- | --------------------- | ---------------- |
| 00    | Verified | 20 deterministic tests, 6 PostgreSQL/RustFS integration tests, 5 Linux fixture tests, healthy Compose, migration/schema and restart checks | None |
| 01    | In progress | Not run | Runtime, UI, sandbox/model integration |
| 02    | Not started | Not run                     | All phase checks |
| 03    | Not started | Not run                     | All phase checks |
| 04    | Not started | Not run                     | All phase checks |
| 05    | Not started | Not run                     | All phase checks |
| 06    | Not started | Not run                     | All phase checks |
| 07    | Not started | Not run                     | All phase checks |
| 08    | Not started | Not run                     | All phase checks |
| 09    | Not started | Not run                     | All phase checks |
| 10    | Not started | Not run                     | All phase checks |

## Inputs for live verification

- Main model: user-provided `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`.
- Sandbox: existing Mac-compatible Nexus-style service, pinned revision/image,
  configured URL/token, and a successful execution readiness probe.
- Optional voice/translation providers: explicit model assets or approved
  provider configuration. Record per-capability availability.

These inputs do not prevent building adapters, fixtures, UI, or deterministic
tests. Record missing live checks precisely rather than marking them passed.

## Phase handoff entries

When implementing a phase, append a dated entry containing:

1. Completed behaviors and relevant files.
2. Commands executed, pass/fail results, and report/artifact references.
3. Live services/model/revisions used, or reasons checks were skipped.
4. Migrations and configuration added.
5. Remaining failures or decisions and the next safe implementation step.

Keep secrets and raw client data out of this file.

## 2 October 2026: phase 00 foundation

- Added FastAPI/Pydantic contracts, SQLAlchemy records, migration
  `f5078b82e3e3`, immutable filesystem and S3 adapters, redacted audit writes,
  atomic per-run event sequencing, and PostgreSQL leased jobs with a real
  artifact verification task. No Python executes in the API process.
- Added the React/Vite workspace shell, workspace/thread APIs, component
  readiness, uv/pnpm lockfiles, Compose services and development commands.
- User selected RustFS as default blob storage. The Compose stack uses a pinned
  RustFS image, an initialized S3 bucket, and separate original/derived prefixes.
  This brings the S3 adapter forward from phase 10. Filesystem remains an
  explicitly selected development option. PostgreSQL holds metadata only.
- Added deterministic CSV/XLSX, MySQL/PostgreSQL seeds, Hindi/English PDF/DOCX,
  expected answers, fixture hashes, and bundled Noto fonts with OFL licenses.
  Repeated fixture generation matches the recorded bytes on macOS.
- Verification so far: Black and mypy pass; pytest deterministic tests 20 passed;
  real PostgreSQL queue tests 5 passed. Those tests cover simultaneous claims,
  expired-owner rejection, retries, concurrent events, and real stored-byte
  maintenance dispatch. Frontend typecheck, build, and formatting pass.
- PostgreSQL uses host port 55432 because an existing host server occupies 5432.
  Compose initial startup exposed an invalid quoted language environment value;
  corrected that configuration. Fresh migration applies successfully.
- Remaining phase 00 gates: final RustFS write protection/read/hash test, full
  stack health and restart persistence, portable Linux fixture regeneration.
  Model credentials are absent and sandbox execution has not yet been probed.


## 2 October 2026: phase 00 verification complete

- Real PostgreSQL/RustFS integration suite: 6 passed. Conditional S3 writes
  reject overwriting original keys; identical writes remain idempotent. Bounded
  reads, byte sizes, and SHA-256 checks pass against the running RustFS service.
- Full Compose build/start succeeds. Database, RustFS, API, and Vite frontend
  health checks pass, and the worker executes a real `verify_storage` job.
  Its retained result reports `verified: true`.
- Applied migration inside the API container; `alembic check` reports no pending
  upgrade operations. Restarted db, RustFS, API, and worker. The UI-created
  workspace, artifact record, completed job, and exact RustFS object hash remain.
- Ran the fixture suite in the Linux API container with read-only mounted evals:
  5 passed, including byte-for-byte regeneration against the macOS-produced pack.
- User supplied the model configuration and authorized live calls. English/Hindi
  tool round trips passed for `gpt-oss-120b` at `cloud.olakrutrim.com`, using the
  configured Chat Completions endpoint and correlated tool IDs. This is a model
  adapter capability check; the complete live agent/sandbox gate remains phase 01.
- Resolved the sandbox analysis image to digest
  `sha256:73520043dc5aa0a30475b8b8a11bf8cc9fea1236540c54b83055d9e876d32fec`.
  The standalone service revision remains
  `b3f032b6a0ce1fab7cebc75250073f58b5b6c63c`.
- Foundation committed as `c694a57`. Phase 01 adds worker-driven agent runs,
  persisted history, SSE replay, microVM execution, and output inspection.
