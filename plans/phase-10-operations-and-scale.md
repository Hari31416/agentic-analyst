# Phase 10: packaging, recovery, and scale

Prerequisite: phase 09. All functional phases exist; preserve their contracts.
Outcome: a measured application that can be installed and operated on-prem.

## Build in order

1. Finalize Compose development and deployment profiles, pinned images/service
   revisions, migrations, readiness, configuration examples, and startup scripts.
   Verify Mac development through the user's existing sandbox pattern. Verify
   the chosen on-prem host/runtime separately; do not invent a Mac limitation.
2. Provide lightweight/default and richer processing profiles for OCR, embeddings,
   reranking, summaries, and speech. Load heavy models lazily or in designated
   workers/services. Avoid one model copy per API process. Record downloads,
   asset paths, image sizes, warm/cold startup, and feature availability.
3. Implement S3-compatible storage using the established storage contract and a
   selectable local service such as the reference RustFS setup. Keep filesystem
   mode for a single host. Test backend migration/copy by hash, durable artifact
   transfer from sandbox, signed/bounded downloads where applicable, and cleanup.
4. Scale API, ingestion/OCR, agent/analysis, and evaluation workers independently.
   Add per-queue/run concurrency, resource admission, fairness, backpressure,
   connection-pool budgets, and limits so ingestion/evaluation cannot starve chat.
   If using multiple sandbox instances, persist session-to-service routing and
   keep each instance's local state independent; do not share mutable SQLite files.
5. Benchmark PostgreSQL vector/lexical indexes with realistic source filters,
   sizes, concurrent queries, and ingestion. Compare exact search with HNSW,
   tune candidate/iterative scan budgets and indexes where measured. Consider a
   broker or specialized search service only if evidence shows the current design
   cannot meet the documented target; record the rationale before changing it.
6. Add operational metrics for queue age, leases, failures, model/tool latency,
   token usage when available, active sandbox sessions, processing throughput,
   storage, and cache/index health. Logs remain redacted and correlate with runs.
7. Implement backup and restore of application DB/files/artifacts/configuration
   with separately protected credential encryption keys. Exercise migrations,
   upgrades/rollback, interrupted ingestion, worker/sandbox loss, and cleanup.
   Clarify that client source DB backups are not managed by this application.
8. Add documented source deletion/reindex/export behavior and orphan cleanup.
   Clean temporary files, expired sessions, unused generations, and cached data
   without deleting retained reports or making references point to another source.
9. Write installation, local development, capability configuration, troubleshooting,
   backup/restore, and source/output lifecycle guides. Document trusted-network
   exposure and existing client infrastructure boundaries; RBAC/SSO remain deferred.
10. Run the complete workflow and phase 09 release gates against the deployment
    profile. If an offline client profile is requested, add preloaded assets,
    wheels/images, blocked-network tests, and local provider settings as a separate
    verified profile. Internet availability means that profile is not mandatory now.

## Acceptance and validation

- [ ] A clean install starts the documented stack and passes real execution readiness.
- [ ] Mac local development and the selected deployment environment are verified
  independently with recorded runtime versions and commands.
- [ ] Filesystem and S3 modes preserve hashes, artifacts, downloads, and provenance.
- [ ] Concurrent workers cannot duplicate active ownership or finalize stale runs.
- [ ] Resource/load measurements identify tested corpus, model endpoint, concurrency,
  latency, throughput, memory, and limits; do not claim unmeasured scalability.
- [ ] Ingestion/evaluation load respects interactive-run admission and resource budgets.
- [ ] Restart/failure scenarios yield correct run states and durable partial outputs.
- [ ] Backup restoration recovers an actual thread, sources/indexes, encrypted
  connection configuration, citations, and exported artifacts.
- [ ] Cleanup/deletion/reindexing behavior is tested with stale and live references.
- [ ] Full deterministic gates and available live quality checks pass; outstanding
  model/provider/human-review gates are explicitly reported before release claims.

## Final handoff

Summarize working features, exact startup/test commands, profiles, migrations,
service/model/assets revisions, measured quality/resource results, and remaining
limits in project documentation and `status.md`. Do not equate passing unit tests
with successful deployment or verified client quality.
