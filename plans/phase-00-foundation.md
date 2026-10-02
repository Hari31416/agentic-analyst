# Phase 00: development foundation and contracts

Prerequisite: read `README.md`, `contracts.md`, and `references.md`.
Outcome: a reproducible development stack and contracts used by every later phase.

## Build in order

1. Scaffold the Python backend and TypeScript frontend using the defaults in
   `README.md`. Add lockfiles, formatting/type-check scripts, pytest, frontend
   checks, `.gitignore`, a secret-free `.env.example`, and developer documentation.
   The frontend initially needs a useful workspace shell and connectivity state.
2. Add Compose infrastructure-only and full-stack development entry points.
   Include PostgreSQL/pgvector, API, worker, frontend, and durable file volumes.
   Add reload support, health checks, predictable configurable ports, and Makefile
   targets. Support the existing Mac sandbox startup or endpoint as documented
   in the references. Keep heavy guest execution out of the API container.
3. Implement configuration validation. Include the supplied model variables,
   sandbox URL/token/image, file storage, database encryption key, language/model
   settings, job/runtime budgets, and explicit external-provider policy. Missing
   model credentials can disable live chat with a clear status while the rest
   of the development stack remains usable.
4. Add SQLAlchemy/Alembic migrations for the phase's actual records: workspaces,
   threads/messages, source identities, runs/jobs, events, evidence/artifacts,
   tool calls, and audit. Implement a filesystem storage adapter with content
   hashing, atomic writes, safe storage keys, and original/derived separation.
   Introduce later document/vector tables when their behavior is implemented.
5. Implement the shared Pydantic contracts, error codes, run states, event
   sequencing, redacted logging, and foundational audit writes. Add typed storage,
   model, and sandbox interfaces without fake production implementations.
6. Implement PostgreSQL job claiming, leases, heartbeat, retry limits, ownership
   checks, and worker dispatch for a real small maintenance/test task. Expose
   readiness independently for database, storage, model configuration, and sandbox.
7. Create a versioned synthetic benchmark pack with CSV, XLSX, matching MySQL/
   PostgreSQL seed scripts, English/Hindi PDF and DOCX, and expected results.
   Use reviewed fixtures: active scheme S1 applications with annual income at
   most INR 200,000 qualify. Two qualifying fixture applications have grants
   INR 10,000 and INR 15,000. Include higher-income, inactive, and other-scheme
   exclusions. The expected count is 2 and grant total INR 25,000. Document all
   rows, units, and assumptions. Seed scripts write only developer-owned fixtures.

Use `indic-language-utils` canonical language metadata from this phase. Keep
the sample text and documents original-language; establish safe unknown/mixed
language handling. Additional malformed, OCR, and adversarial fixtures arrive
with the phases that exercise them.

## Acceptance and validation

- [ ] A clean environment can run documented setup and Compose startup commands.
- [ ] Compose configuration validates without printing secrets; API/frontend
  startup and readiness show component availability accurately.
- [ ] Empty-database migration and restart preserve stored records and files.
- [ ] Two workers cannot own the same job; an expired owner cannot finalize it.
- [ ] Storage traversal is rejected and failed writes do not publish partial files.
- [ ] Backend contracts/types/tests and frontend typecheck/build pass.
- [ ] Fixture generation is reproducible with recorded hashes and expected values.
- [ ] Source originals, derived outputs, audit, and events have stable references.

Record actual commands and results in `status.md`. Validate live sandbox startup
when available, but phase 01 owns the full execution probe. Phase 00 completion
does not imply that model, retrieval, or sandbox execution has been verified.

## Handoff

Document service URLs and host/container addressing, migration command, fixture
generation/seeding commands, chosen dependency versions, and unresolved live
configuration. Phase 01 consumes the run/event/tool/storage contracts.
