# Resource lifecycle

Workspace and thread deletion is permanent. Source deletion retains data only
when saved run history or derived dataset provenance needs it. Use the sidebar
for workspace/thread actions and the source workbench for source deletion.

| Resource | Update | Delete | Retention |
| --- | --- | --- | --- |
| Workspace | `PATCH /api/workspaces/{id}` with `{"label":"Name"}` | `DELETE /api/workspaces/{id}`, returns 204 | Removes its sources, threads, runs, messages, evidence, artifacts, jobs, audits and summary caches |
| Thread | `PATCH /api/threads/{id}` with `{"label":"Name"}` | `DELETE /api/threads/{id}`, returns 204 | Removes its run history and outputs; workspace sources remain |
| Source | Existing workspace source metadata PATCH | `DELETE /api/workspaces/{workspace_id}/sources/{id}` or `DELETE /api/sources/{id}` | Unreferenced sources are purged; referenced sources are archived |
| Document | Existing retry/reindex actions | `DELETE /api/documents/{id}` | Uses the same source deletion policy, including all versions of that source |

Source/document DELETE returns 200 with `state: "deleted"` and either
`retention: "purged"` or `retention: "archived_for_citations"`. A purged ID
subsequently returns 404. An archived source stays hidden from source lists,
new run selections and retrieval, while its original data, document locations
and historical evidence remain available for saved citations. Connection
credentials are deleted in both cases. Repeating DELETE on an archived source
can purge it after its retained references have been removed.

A saved run selection, source/version configuration, evidence reference,
artifact lineage, or another source/dataset's lineage can require retention.
Deleting a thread returns 409 if workspace datasets need its output provenance,
including an archived dataset. Delete those datasets' sources first if they are
unused, or delete the workspace to remove the complete history together.
There is no force option that silently breaks retained references.

## Active work

Workspace/thread deletion returns 409 while related runs or jobs are queued or
running. Cancel an active run through its existing cancel endpoint and wait for
its worker and sandbox cleanup to finish. Source/document deletion cancels
queued document jobs, but returns 409 for running document jobs or runs using
that source. Reindex, retry, uploads, run creation and dataset registration use
the workspace lock to serialize them with deletion.

Pending sandbox cleanup, or a recorded sandbox session without confirmed
complete cleanup, blocks workspace/thread deletion. Failed cleanup requires
operator recovery and confirmation before deletion; this API does not infer
that a guest is gone merely because its TTL may have expired.

## Database and storage

Migration `c2d14e6a901f` adds `ON DELETE CASCADE` to ownership foreign keys.
It also gives ingestion/crawl jobs and summary caches explicit database owners,
backfills existing jobs/caches, and discards unattributable disposable caches.
It does not delete user workspaces during migration. Run `make migrate` before
starting API and worker processes that use this schema.

Blob deletion uses an outbox in the same transaction as metadata deletion.
Independent `delete_storage` jobs survive the cascades. The normal worker checks
that no source, dataset or artifact still references each key, then removes it
from filesystem storage or RustFS/S3. Removal is idempotent and retries transient
failures, up to ten attempts. A successful DELETE confirms metadata removal and
queued cleanup; the worker must be running for physical blob removal.

Inspect failed cleanup with:

```sql
SELECT id, state, attempts, max_attempts, result
FROM jobs
WHERE kind = 'delete_storage' AND state = 'failed';
```

After resolving the storage failure, an operator can requeue the retained job
by setting `state = 'queued'`, `attempts = 0`, `available_at = now()` and clearing
its lease owner, token and expiry. Do not recreate its resource metadata.

Use the application DELETE endpoints for lifecycle operations. Direct SQL
cascades remove database rows but do not enqueue blob deletion or check JSON
provenance. General orphan discovery, retention schedules and resource quotas
remain separate operations work.
