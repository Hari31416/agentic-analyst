# Phase 08: guardrail and audit hardening

Prerequisite: phase 07. Baseline source protection, sandbox controls, and audit
already exist; this phase tests and strengthens them across the complete system.
Outcome: execution decisions and failures are inspectable and adversarially tested.

## Build in order

1. Consolidate existing action checks into typed policy decisions with stable
   allow/reject/clarify reason codes. Apply them at tool execution and ingestion
   boundaries. Record policy version and decision in run/audit data. Keep source
   selection and execution policy independent of model-generated instructions.
2. Test and harden SQL across both dialects: nested/write CTEs, multiple statements,
   comments, unsafe or user-defined functions, expensive queries, system/secret
   metadata, file sinks, cancellation, and transactional cleanup. Fix actual gaps
   rather than treating an AST parse or rollback as sufficient protection.
3. Exercise sandbox networking, resources, path boundaries, credential absence,
   timeouts, teardown, and artifact collection. Test malicious or runaway Python,
   concurrent session work, symlinks, path escapes, and guest output flooding.
   Application originals must remain intact even when guest working copies change.
4. Add prompt-injection fixtures in documents, OCR text, cells, DB values, tool
   errors, and imported archives, including Hindi/English instructions. Treat
   content as data and ensure it cannot enable providers, reveal secrets, choose
   arbitrary paths, or mutate sources. Optional detection is evidence, not the
   enforcement boundary or a promised complete injection detector.
5. Harden ingestion/crawling, archive expansion, SSRF controls, HTML/artifact
   rendering, and cache/source version handling. Check redirects and network
   resolution where the app fetches URLs. Limit parser/model resource use.
6. Implement complete audit inspection/export for source processing, tool calls,
   policy outcomes, model/prompt/pipeline versions, SQL/code, artifact lineage,
   errors, cancellation, and partial results. Keep compact metadata separate from
   bounded evidence/result storage, with secrets removed consistently.
7. Add answer checks for nonexistent/mismatched evidence, numeric discrepancies
   against returned results, missing units, and unsupported completion claims.
   Present warnings or explicit uncertainty rather than pretending deterministic
   checks can prove semantic correctness.
8. Build a useful run audit view with filters by run/action/state and links to
   existing source/code/result views. Document how each control works, what it
   catches, tested bypass attempts, and remaining limitations for learning.

## Acceptance and validation

- [x] Source mutations and tested SQL/file/network sinks are rejected at execution
  boundaries with recorded reason codes on real services.
- [x] Injection fixtures cannot expand configured capabilities or expose secrets.
- [x] Guest network/resource policies and original-file protection pass actual
  sandbox tests; unsupported controls are explicitly identified.
- [x] Cancellation, timeout, worker lease loss, and service failure cannot produce
  late successful completion or lose already-exported partial artifacts.
- [x] Audit reconstructs a mixed-source run from question to cited answer and outputs.
- [x] Secret sentinel tests cover frontend, guest, prompts, results, logs, audit,
  report exports, and portable archives.
- [x] Unsafe artifact previews and imports are rejected or isolated as designed.
- [x] Critical negative tests are deterministic release checks; live injection
  trials report outcomes and failures without claiming universal protection.

Department permissions, RBAC, SSO, compliance certification, and tamper-evident
infrastructure remain outside this phase. Do not add them as prerequisites.

## Handoff

Record policy versions, adversarial cases, real-service results, residual limits,
audit APIs/export schema, and which checks phase 09 must run as release gates.

## Recorded validation limits

See [status](status.md) and [guardrails and audit](../docs/guardrails-and-audit.md).
The resource gate records unsupported/unverified memory stress and workspace disk
quotas explicitly. Injection success is a single bounded diagnostic, not universal
protection. Historical audit policy gaps and full browser QA remain documented.
