# OSS 120B diagnostic reproduction

One user-authorized fresh execution of retail-sales-chart-csv with GPT OSS 120B and bounded response/validation diagnostics. Original benchmark files remain unchanged. The live execution occurred after diagnostic instrumentation, before the error-message bound repair, with the original question, fixtures and prompt.

Run `7043637f-932c-476c-89d1-48ab213214aa` completed in 47.24 seconds with 16 model requests and 15 tool attempts. It produced declared CSV/PNG artifacts. Automatic status is needs_review, which is not an application failure and does not establish complete artifact/content correctness. This diagnostic task did not repeat the full quality review.

The live run did not reproduce the fatal exception. Its rejected tool-input diagnostics were 200, 165 and 200 characters; none exceeded the 500-character SafeError.message limit. Call six was a valid search_documents call. Therefore the exact cause of the historical run remains unconfirmed.

A scripted deterministic replay through the actual pre-fix agent loop supplied ten unsupported tool-input fields. The original validation generated a 646-character diagnostic. Constructing SafeError then raised ValidationError, string_too_long at message, instead of returning the rejection. This confirms the code defect independently of the historical missing response.

The repair bounds the short SafeError message to 500 characters while retaining the bounded structured validation error list and tool summary. A regression test verifies that this same oversized rejection reaches the model and the loop can recover. Another test checks that submitted argument values are absent from diagnostics.

Diagnostic events now retain model-call number, finish reason, response content length, tool names/argument lengths, sanitized validation fields/types/messages, and generated diagnostic length. Fatal ValueError/ValidationError diagnostics are persisted as events and logged with run ID. Model reasoning, prompts, raw argument values and credentials are not included.

[Live database events](live-events.json), [pre-fix deterministic replay](deterministic-replay-before-fix.json), [live runner report](../../runs/oss-120b-chart-diagnostic-2026-10-05/report.json). Only one live trial was run; no repeat was made to force a failure or improve quality.
