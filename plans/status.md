# Implementation status

Phase 00 is implemented. Final Compose/RustFS integration checks are in progress.
Phase 01 implementation has begun against the shared foundation contracts.

Allowed statuses: not started, in progress, implemented / integration pending,
verified. A verified phase has evidence for every required acceptance gate.

| Phase | Status      | Verification evidence | Remaining gate   |
| ----- | ----------- | --------------------- | ---------------- |
| 00    | Implemented / integration pending | 20 deterministic tests, 5 real PostgreSQL tests; backend types and frontend checks pass | Full-stack startup, RustFS and restart checks |
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

