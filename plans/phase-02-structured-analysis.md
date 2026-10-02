# Phase 02: files, databases, and structured analysis

Prerequisite: phase 01. Read codeagent workspace/DB tools and Nexus SQL adapters.
Outcome: the single agent analyzes CSV, Excel, MySQL, and PostgreSQL safely.

## Build in order

1. Implement upload and source selection for CSV, XLSX, and legacy XLS through
   an appropriate optional reader. Validate content/type/size, store immutable
   originals, and identify sheets and headers. Handle encodings, delimiters,
   empty sheets, formula cached values, dates, nullable types, decimals, and
   leading-zero identifiers. Report uncertain typing and missing formula caches.
2. Persist dataset profiles: columns, types, row count or explicitly estimated
   count, missingness, bounded samples, units/hints, source version, and sheet.
   Support lazy/chunked processing; large files cannot require every row in RAM.
3. Add MySQL/PostgreSQL connection forms and APIs: test, save encrypted credentials,
   inspect schema/relationships, refresh schema metadata, and select a connection
   for a run. Use safe identifier quoting, bounded pools and queries, TLS options,
   redacted errors, and a separate credential key. Keep provisioning/topology out.
4. Implement dialect-aware SQL policy from the first query. Parse exactly one
   allowed read query; reject writes inside CTEs, unsafe functions, file/network
   sinks, arbitrary commands, and forbidden schema references. Use database
   read-only transactions, supported query deadlines, and bounded result fetching.
   Do not depend on rollback to protect against MySQL DDL or function side effects.
5. Add source-list, dataset-profile, schema-inspection, bounded-row-sample, and
   `run_sql` tools. File SQL uses DuckDB in the sandbox with registered selected
   datasets and restrictions on path readers, extensions, and network functions.
   Database SQL stays in the connector service. Preserve aggregate correctness
   when limiting returned rows; limits must not truncate input before aggregation.
6. Stage query results and selected file working copies into the sandbox for
   pandas/numpy analysis. Let the agent run and repair Python, produce a chart
   or table, and register derived datasets with code/query lineage.
7. Render source/sheet selection, connection status, profiles, SQL/code/output
   inspection, paginated tables, and charts in the workspace. Add optional source
   descriptions and metric hints; schema discovery works without manual catalogs.
8. Seed both development databases and run the known-answer fixture questions
   against files and both dialects. Add nulls, duplicated keys, mixed dates,
   reserved/non-ASCII identifiers, and monetary precision cases.

## Acceptance and validation

- [ ] Every initial source type can be selected and analyzed through the same agent.
- [ ] Count 2 and INR 25,000 total match all corresponding golden sources.
- [ ] SQL and Python result artifacts retain input versions, code/query, and units.
- [ ] Upload hashes and source DB rows remain unchanged after cleaning/analysis.
- [ ] Write queries, write CTEs, multi-statements, unsafe functions, and file sinks
  are rejected in actual MySQL and PostgreSQL tests, including permissive test credentials.
- [ ] Query timeout, row limits, cancellation, and decimal/date serialization work.
- [ ] XLSX formulas and XLS limitations are visible; no macro execution occurs.
- [ ] Oversized inputs/results are handled within configured limits with explicit
  partial/truncated status and inspection options.
- [ ] Model/sandbox/DB credentials are absent from frontend, guest, and tool results.
- [ ] At least one live agent case per source type has a recorded result.

Keep dialect policy tests and real-database behavior tests separate. Unit tests
that only mock rollback or a connection do not establish read-only behavior.

## Handoff

Document supported file details, connector configuration, SQL function policy,
timeouts, result limits, profile semantics, and derived dataset registration.
Phase 03 supplies document definitions to these analytical tools.
