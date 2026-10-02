# Phase 01: single agent, model endpoint, and sandbox

Prerequisite: phase 00. Read the Nexus sandbox references before implementation.
Outcome: a real tool-using conversation that executes Python and retains its output.

## Build in order

1. Implement the OpenAI-compatible model adapter using the supplied base URL,
   API key, and model. Normalize streaming/non-streaming messages, tool calls,
   fragmented tool arguments, finish reasons, usage, timeouts, and safe errors.
   Support tool-result correlation IDs and reject malformed/unknown tool calls.
   Keep optional endpoint features behind capability checks.
2. Implement the thin agent loop from `contracts.md`. The model chooses actions;
   the runtime validates, dispatches, records results, and stops on final answer,
   clarification, cancellation, failure, or exhausted budgets. Bound model calls,
   tool calls, context, time, and outputs. Use a versioned prompt with source
   integrity, evidence, and requested-answer-language instructions.
3. Implement the app-owned client for the pinned Nexus-style sandbox service.
   Cover create/heartbeat/execute, status, stdout/stderr, file transfer/listing,
   artifact export, stop, and cleanup. Inspect actual service semantics; avoid
   assuming execution POST is asynchronous or that artifact URIs are downloadable.
   On ambiguous execution failures, reconcile state rather than blindly rerunning.
4. Stage Python as a file and execute a fixed launcher in the microVM. Use an
   analysis image with its required packages installed before networking is
   disabled. Keep credentials outside the guest. Separate command timeout from
   session lifetime, serialize work per session, and verify execution readiness.
5. Add initial `run_python`, artifact-list, and bounded artifact-inspection tools.
   Collect outputs into application storage, validate paths/MIME/size/hash, and
   give the agent compact results with artifact IDs. Preserve useful outputs
   when execution fails or a run is cancelled.
6. Implement durable chat/run APIs, worker-driven execution, SSE replay, thread
   history, cancellation, and clarification continuation. Persist enough context
   to use earlier artifacts on follow-up turns without relying on a live guest.
7. Build chat UI with real streaming progress, operational tool events, answer
   rendering, output links/previews, stop/retry controls, and saved history.
   Display failed and partial outcomes accurately; avoid exposing private reasoning.
8. Add an explicit live capability probe and evaluation case: ask the model to
   calculate a known result using Python and save a table or plot. Include English
   and Hindi prompts. Model configuration errors should produce actionable status.

## Acceptance and validation

- [ ] The configured endpoint completes a real tool round trip with correct IDs.
- [ ] A real microVM runs Python, returns stdout/stderr, and exports verified bytes.
- [ ] An exported artifact remains accessible after its sandbox session is stopped.
- [ ] Hindi/English requests produce an answer in the requested language.
- [ ] Malformed arguments, unknown tools, model timeouts, and unavailable sandbox
  yield safe typed errors; no path falls back to API/host Python execution.
- [ ] Cancellation stops new dispatch and terminates/cleans up the session using
  supported service operations; do not claim cancellation from HTTP timeout alone.
- [ ] Disconnect/reconnect does not duplicate events, tool execution, or artifacts.
- [ ] Concurrent runs have separate execution state; guest variables contain no secrets.
- [ ] Restarting the API/worker preserves history and handles abandoned run leases.

Test provider parsing with recorded/synthetic protocol fixtures, service failures
with HTTP tests, and actual sandbox behavior separately. Live gates require the
supplied model and existing Mac-compatible sandbox; mark missing checks pending.

## Handoff

Record endpoint capability results, pinned sandbox revision/image, usable limits,
artifact transfer mechanism, and UI/run API contracts. Phase 02 adds source tools
to this same agent loop.
