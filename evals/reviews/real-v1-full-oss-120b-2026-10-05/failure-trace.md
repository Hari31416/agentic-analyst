# OSS chart/export failure database trace

Case `retail-sales-chart-csv`, run `df6c0123-06ca-409d-9441-c84b87320c3f`. Read directly from local PostgreSQL in a read-only transaction. Run duration 13.09s on 5 October 2026, 11:03:42–11:03:55 UTC.

| Model call | Recorded result |
| --- | --- |
| 1 | list_sources succeeded, two sources selected. |
| 2 | inspect_schema succeeded, one dataset available. |
| 3 | inspect_artifact rejected because source_2 is a source alias, not an artifact reference. |
| 4 | search_documents rejected because source_id is an extra field absent from its schema. |
| 5 | search_documents succeeded and returned the complete qualifying-sale rules. |
| 6 | Fatal invalid_model_response before another tool or answer event. Worker log identifies ValidationError. |

The run never reached SQL, Python, CSV export or chart execution. The worker job completed its single attempt; the application run was failed and cleanup completed. The earlier argument mistakes were handled and the model continued, so they are not the fatal exception themselves.

No sixth response, exception field list or traceback is retained in the run, events or audit records. The trace narrows the location but cannot establish the precise failing input. It does not show an infrastructure error or a chart-generation failure.

One plausible code path deserves attention: the loop builds a detailed invalid-argument message from all validation errors, then places it in SafeError.message, capped at 500 characters. An oversized diagnostic raises another uncaught ValidationError instead of returning the tool rejection. A deterministic local construction reproduces that failure mode. This is a code-level hypothesis for this run, not proof that its missing sixth response hit that path.

[Database event snapshot](failure-trace.json). Relevant code: backend/app/agent/loop.py invalid_arguments error construction and backend/app/contracts.py SafeError.message bound. Source worker log line 131 records the fatal ValidationError. No database changes or new model calls were made.
