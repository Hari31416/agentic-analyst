import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Check,
  CircleStop,
  Download,
  Eye,
  FileSpreadsheet,
  FileText,
  Globe2,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  AnalysisRun,
  AnswerLanguage,
  ApiError,
  ChatMessage,
  RunArtifact,
  RunEvent,
  chatApi,
} from "./chatApi";
import "./chat.css";

export type ChatSource = {
  id: string;
  display_name: string;
  kind: string;
  state: string;
  version: number;
};

type ChatPanelProps = {
  threadId: string;
  sources: ChatSource[];
  modelAvailable: boolean;
  modelMessage?: string;
};

type RetryInput = {
  text: string;
  selectedSourceIds: string[];
  language: AnswerLanguage;
};

type ProgressItem = { id: string; text: string; kind: string };

const liveStates = new Set(["queued", "running"]);

function isLive(run: AnalysisRun): boolean {
  return liveStates.has(run.state) || run.outcome?.cleanup === "pending";
}

function labelForState(runOrState: AnalysisRun | string): string {
  const state = typeof runOrState === "string" ? runOrState : runOrState.state;
  if (
    typeof runOrState !== "string" &&
    runOrState.outcome?.cleanup === "pending"
  ) {
    return "Stopping sandbox";
  }
  const labels: Record<string, string> = {
    queued: "Waiting to start",
    running: "Working through the sources",
    awaiting_clarification: "Needs a follow-up",
    completed: "Complete",
    failed: "Run failed",
    cancelled: "Stopped",
    budget_exhausted: "Run limit reached",
  };
  return labels[state] ?? state.replaceAll("_", " ");
}

function safeText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, 300)
    : undefined;
}

function retrySelectionKey(threadId: string, runId: string): string {
  return `fieldnote:retry-selection:${threadId}:${runId}`;
}

function eventSummary(event: RunEvent): string {
  const payload = event.payload ?? {};
  switch (event.type) {
    case "status":
      return (
        safeText(payload.message) ??
        safeText(payload.status) ??
        "Updating run status"
      );
    case "tool_started": {
      const name = safeText(payload.tool_name) ?? safeText(payload.name);
      return name ? `Started ${name}` : "Started a source operation";
    }
    case "tool_finished": {
      const name = safeText(payload.tool_name) ?? safeText(payload.name);
      return name ? `Finished ${name}` : "Source operation finished";
    }
    case "answer":
      return "Answer ready";
    case "error":
      return safeText(payload.message) ?? "The run reported an error";
    case "terminal":
      return labelForState(safeText(payload.state) ?? "completed");
    default:
      return "Run updated";
  }
}

function answerFromEvent(event: RunEvent): ChatMessage | null {
  const textValue =
    event.payload.text ?? event.payload.answer ?? event.payload.content;
  const text =
    typeof textValue === "string" && textValue.trim()
      ? textValue.trim().slice(0, 20_000)
      : undefined;
  if (!text) return null;
  const references = event.payload.references;
  const referenceObject =
    references && typeof references === "object"
      ? (references as Record<string, unknown>)
      : {};
  const evidenceValues =
    referenceObject.evidence_ids ?? event.payload.evidence_ids;
  const artifactValues =
    referenceObject.artifact_ids ?? event.payload.artifact_ids;
  const evidenceIds = Array.isArray(evidenceValues)
    ? evidenceValues.filter((item): item is string => typeof item === "string")
    : [];
  const artifactIds = Array.isArray(artifactValues)
    ? artifactValues.filter((item): item is string => typeof item === "string")
    : [];
  return {
    id: `event-${event.id}`,
    role: "assistant",
    content: text,
    run_id: event.run_id,
    references: { evidence_ids: evidenceIds, artifact_ids: artifactIds },
  };
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function SourceGlyph({ kind }: { kind: string }) {
  const structured = /csv|sheet|spreadsheet|table|database/i.test(kind);
  return structured ? <FileSpreadsheet size={15} /> : <FileText size={15} />;
}

function ChatPanel({
  threadId,
  sources,
  modelAvailable,
  modelMessage,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [artifactLists, setArtifactLists] = useState<
    Record<string, RunArtifact[]>
  >({});
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [language, setLanguage] = useState<AnswerLanguage>("en-IN");
  const [draft, setDraft] = useState("");
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null);
  const [progress, setProgress] = useState<ProgressItem[]>([]);
  const [liveAnswer, setLiveAnswer] = useState<ChatMessage | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [retryingRunId, setRetryingRunId] = useState("");
  const [error, setError] = useState("");
  const [optimisticMessage, setOptimisticMessage] =
    useState<ChatMessage | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sequenceRef = useRef(0);
  const activeRunIdRef = useRef("");
  const retryInputsRef = useRef(new Map<string, RetryInput>());
  const selectedSourceIdsRef = useRef(selectedSourceIds);
  selectedSourceIdsRef.current = selectedSourceIds;

  const refreshHistory = useCallback(async () => {
    const [nextMessages, nextRuns] = await Promise.all([
      chatApi.messages(threadId),
      chatApi.runs(threadId),
    ]);
    setMessages(nextMessages);
    setRuns(nextRuns);
    setOptimisticMessage(null);
    const current = nextRuns.find(isLive) ?? null;
    setActiveRun(current);
    setLiveAnswer(null);
    setProgress([]);
    activeRunIdRef.current = current?.id ?? "";
  }, [threadId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setMessages([]);
    setRuns([]);
    setActiveRun(null);
    setLiveAnswer(null);
    setOptimisticMessage(null);
    setProgress([]);
    activeRunIdRef.current = "";
    retryInputsRef.current.clear();
    Promise.all([chatApi.messages(threadId), chatApi.runs(threadId)])
      .then(([nextMessages, nextRuns]) => {
        if (!active) return;
        setMessages(nextMessages);
        setRuns(nextRuns);
        const current = nextRuns.find(isLive) ?? null;
        setActiveRun(current);
        activeRunIdRef.current = current?.id ?? "";
        for (const run of nextRuns) {
          if (run.state !== "failed") continue;
          const userMessage = [...nextMessages]
            .reverse()
            .find((item) => item.run_id === run.id && item.role === "user");
          if (userMessage) {
            let saved: Partial<RetryInput> = {};
            try {
              saved = JSON.parse(
                sessionStorage.getItem(retrySelectionKey(threadId, run.id)) ??
                  "{}",
              );
            } catch {
              // Use the visible message and current source selection if browser storage is unavailable.
            }
            retryInputsRef.current.set(run.id, {
              text: userMessage.content,
              selectedSourceIds:
                run.selected_source_ids ?? saved.selectedSourceIds ?? [],
              language: run.answer_language ?? saved.language ?? "en-IN",
            });
          }
        }
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load this conversation.",
          );
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [threadId]);

  useEffect(() => {
    const allowed = new Set(sources.map((source) => source.id));
    setSelectedSourceIds((current) => current.filter((id) => allowed.has(id)));
  }, [sources]);

  useEffect(() => {
    let active = true;
    const runsWithArtifacts = new Set(
      messages
        .filter(
          (message) =>
            (message.references?.artifact_ids?.length ?? 0) > 0 &&
            message.run_id,
        )
        .map((message) => message.run_id as string),
    );
    const runIds = runs
      .filter(
        (run) =>
          (run.outcome?.artifact_ids?.length ?? 0) > 0 ||
          runsWithArtifacts.has(run.id),
      )
      .slice(0, 12)
      .map((run) => run.id);
    if (!runIds.length) {
      setArtifactLists({});
      return;
    }
    Promise.all(
      runIds.map(async (runId) => {
        try {
          return [runId, await chatApi.artifacts(runId)] as const;
        } catch {
          return [runId, []] as const;
        }
      }),
    ).then((entries) => {
      if (active) setArtifactLists(Object.fromEntries(entries));
    });
    return () => {
      active = false;
    };
  }, [messages, runs]);

  useEffect(() => {
    if (!activeRun || !isLive(activeRun)) return;
    const runId = activeRun.id;
    sequenceRef.current = 0;
    const close = chatApi.subscribe(
      runId,
      (event) => {
        if (
          activeRunIdRef.current !== runId ||
          event.run_id !== runId ||
          event.sequence <= sequenceRef.current
        )
          return;
        sequenceRef.current = event.sequence;
        setProgress((current) =>
          [
            ...current,
            { id: event.id, text: eventSummary(event), kind: event.type },
          ].slice(-5),
        );
        if (event.type === "status") {
          const state = safeText(event.payload.state);
          const eventOutcome = event.payload.outcome;
          const outcome =
            eventOutcome && typeof eventOutcome === "object"
              ? (eventOutcome as Record<string, unknown>)
              : {};
          const cleanup =
            safeText(event.payload.cleanup) ?? safeText(outcome.cleanup);
          if (state || cleanup) {
            setActiveRun((current) => {
              if (!current || current.id !== runId) return current;
              const next: AnalysisRun = {
                ...current,
                state: state ?? current.state,
                outcome: {
                  ...current.outcome,
                  cleanup: cleanup ?? current.outcome?.cleanup,
                },
              };
              return next;
            });
          }
        }
        if (event.type === "answer") {
          const answer = answerFromEvent(event);
          if (answer) setLiveAnswer(answer);
        }
        if (event.type === "terminal") {
          void chatApi
            .run(runId)
            .then((finalRun) => {
              setRuns((current) => [
                finalRun,
                ...current.filter((item) => item.id !== runId),
              ]);
              setActiveRun(null);
              activeRunIdRef.current = "";
              return refreshHistory();
            })
            .catch((reason: unknown) => {
              setError(
                reason instanceof Error
                  ? reason.message
                  : "Could not load the completed run.",
              );
              setActiveRun(null);
            });
        }
      },
      () => {
        if (activeRunIdRef.current === runId) {
          setProgress((current) =>
            current.some((item) => item.id === "reconnect")
              ? current
              : [
                  ...current,
                  {
                    id: "reconnect",
                    text: "Reconnecting to run updates",
                    kind: "status",
                  },
                ].slice(-5),
          );
        }
      },
    );
    return close;
  }, [activeRun?.id, refreshHistory]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, activeRun, liveAnswer, progress]);

  const sortedMessages = useMemo(() => {
    const all = [...messages];
    if (
      optimisticMessage &&
      !all.some((item) => item.id === optimisticMessage.id)
    )
      all.push(optimisticMessage);
    return all;
  }, [messages, optimisticMessage]);

  const toggleSource = (sourceId: string) => {
    setSelectedSourceIds((current) =>
      current.includes(sourceId)
        ? current.filter((id) => id !== sourceId)
        : [...current, sourceId],
    );
  };

  async function sendMessage(
    text: string,
    retryFor?: string,
    explicitInput?: RetryInput,
  ) {
    const cleanText = text.trim();
    if (!cleanText || !modelAvailable || activeRun || sending) return;
    setSending(true);
    setError("");
    const input: RetryInput = explicitInput ?? {
      text: cleanText,
      selectedSourceIds: [...selectedSourceIdsRef.current],
      language,
    };
    try {
      const run = await chatApi.createRun(threadId, {
        text: input.text,
        selected_source_ids: input.selectedSourceIds,
        answer_language: input.language,
        request_id: crypto.randomUUID(),
      });
      if (retryFor) retryInputsRef.current.delete(retryFor);
      retryInputsRef.current.set(run.id, input);
      try {
        sessionStorage.setItem(
          retrySelectionKey(threadId, run.id),
          JSON.stringify({
            selectedSourceIds: input.selectedSourceIds,
            language: input.language,
          }),
        );
      } catch {
        // Retry still works in the current view when browser storage is unavailable.
      }
      const userMessage: ChatMessage = {
        id: `pending-${run.id}`,
        role: "user",
        content: cleanText,
        run_id: run.id,
        references: { evidence_ids: [], artifact_ids: [] },
      };
      setOptimisticMessage(userMessage);
      setDraft("");
      setRuns((current) => [
        run,
        ...current.filter((item) => item.id !== run.id),
      ]);
      setActiveRun(isLive(run) ? run : null);
      activeRunIdRef.current = isLive(run) ? run.id : "";
      if (!isLive(run)) await refreshHistory();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 503) {
        setError(
          reason.message || "Chat is unavailable until a model is configured.",
        );
      } else {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not start this run.",
        );
      }
    } finally {
      setSending(false);
    }
  }

  async function stopRun() {
    if (!activeRun || cancelling) return;
    setCancelling(true);
    setError("");
    try {
      const stopped = await chatApi.cancelRun(activeRun.id);
      setRuns((current) => [
        stopped,
        ...current.filter((item) => item.id !== stopped.id),
      ]);
      setActiveRun(stopped);
      activeRunIdRef.current = stopped.id;
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not stop this run.",
      );
    } finally {
      setCancelling(false);
    }
  }

  async function retryRun(run: AnalysisRun) {
    if (activeRun || sending || retryingRunId) return;
    let input = retryInputsRef.current.get(run.id);
    if (!input) {
      const userMessage = [...messages]
        .reverse()
        .find((item) => item.run_id === run.id && item.role === "user");
      if (!userMessage) return;
      let saved: Partial<RetryInput> = {};
      try {
        saved = JSON.parse(
          sessionStorage.getItem(retrySelectionKey(threadId, run.id)) ?? "{}",
        );
      } catch {
        // Retry from the stored user message with the current selection.
      }
      input = {
        text: userMessage.content,
        selectedSourceIds: run.selected_source_ids ??
          saved.selectedSourceIds ?? [...selectedSourceIdsRef.current],
        language: run.answer_language ?? saved.language ?? language,
      };
    }
    setRetryingRunId(run.id);
    setDraft(input.text);
    setLanguage(input.language);
    setSelectedSourceIds(input.selectedSourceIds);
    await sendMessage(input.text, run.id, input);
    setRetryingRunId("");
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(draft);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(draft);
    }
  }

  const messageByRun = new Map<string, ChatMessage[]>();
  for (const message of sortedMessages) {
    if (!message.run_id) continue;
    messageByRun.set(message.run_id, [
      ...(messageByRun.get(message.run_id) ?? []),
      message,
    ]);
  }
  const latestFailedRun = runs.find((run) => run.state === "failed");

  return (
    <section className="chat-panel" aria-label="Research conversation">
      <header className="chat-header">
        <div>
          <div className="chat-eyebrow">
            <span className="chat-eyebrow-rule" /> THREAD TRANSCRIPT
          </div>
          <h2>Research conversation</h2>
        </div>
        <div className="chat-header-status">
          <span className={activeRun ? "chat-live-pulse" : "chat-ready-dot"} />
          {activeRun ? labelForState(activeRun) : "Saved to this thread"}
        </div>
      </header>

      {!modelAvailable && (
        <div className="chat-model-block" role="status">
          <span className="chat-model-icon">
            <Sparkles size={16} />
          </span>
          <div>
            <strong>Chat is unavailable</strong>
            <p>
              {modelMessage ||
                "Configure a model endpoint and credentials to start a run."}
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="chat-error" role="alert">
          <AlertCircle size={15} />
          <span>{error}</span>
          <button onClick={() => setError("")} aria-label="Dismiss error">
            ×
          </button>
        </div>
      )}

      <div className="chat-transcript" aria-live="polite">
        {loading ? (
          <div className="chat-loading">
            <LoaderCircle size={17} className="spin" /> Loading saved
            conversation
          </div>
        ) : sortedMessages.length === 0 && !activeRun ? (
          <div className="chat-empty">
            <div className="chat-empty-glyph">
              <Globe2 size={17} />
            </div>
            <span className="mini-label">A question to begin</span>
            <p>
              Ask about the sources attached to this workspace. Each answer will
              keep its evidence and files with the thread.
            </p>
          </div>
        ) : (
          <div className="message-list">
            {sortedMessages.map((message) => {
              const run = message.run_id
                ? runs.find((item) => item.id === message.run_id)
                : undefined;
              const related = message.run_id
                ? (messageByRun.get(message.run_id) ?? [])
                : [];
              const artifacts = message.references?.artifact_ids ?? [];
              return (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role === "assistant" && (
                    <span className="assistant-seal">
                      <Sparkles size={13} />
                    </span>
                  )}
                  <article className={`message-card ${message.role}`}>
                    <div className="message-topline">
                      <span>
                        {message.role === "user"
                          ? "YOU"
                          : message.role === "assistant"
                            ? "FIELDNOTE"
                            : "SYSTEM"}
                      </span>
                      {run && (
                        <span className={`message-run-state ${run.state}`}>
                          {labelForState(run)}
                        </span>
                      )}
                    </div>
                    <div className="message-content">{message.content}</div>
                    {!!message.references?.evidence_ids?.length && (
                      <div className="evidence-references">
                        <ShieldCheck size={12} />
                        <span>Evidence</span>
                        {message.references.evidence_ids.map((id) => (
                          <span className="evidence-id" key={id}>
                            {id}
                          </span>
                        ))}
                      </div>
                    )}
                    {artifacts.length > 0 && (
                      <ArtifactLinks
                        artifacts={artifactLists[message.run_id ?? ""] ?? []}
                        ids={artifacts}
                      />
                    )}
                  </article>
                  {message.role === "user" &&
                    run?.state === "failed" &&
                    !related.some((item) => item.role === "assistant") && (
                      <RunFailure
                        run={run}
                        retrying={retryingRunId === run.id}
                        onRetry={() => void retryRun(run)}
                      />
                    )}
                </div>
              );
            })}
            {liveAnswer &&
              !messages.some(
                (item) =>
                  item.id === liveAnswer.id ||
                  (item.run_id === liveAnswer.run_id &&
                    item.role === "assistant"),
              ) && (
                <MessageBubble
                  message={liveAnswer}
                  artifacts={artifactLists[liveAnswer.run_id ?? ""] ?? []}
                />
              )}
          </div>
        )}

        {activeRun && (
          <div className="run-progress" aria-label="Run progress">
            <div className="run-progress-head">
              <LoaderCircle size={14} className="spin" />
              <strong>{labelForState(activeRun)}</strong>
              {activeRun.state !== "cancelled" && (
                <button
                  className="stop-run"
                  onClick={() => void stopRun()}
                  disabled={cancelling}
                >
                  <CircleStop size={14} />
                  {cancelling ? "Stopping" : "Stop"}
                </button>
              )}
            </div>
            {!!progress.length && (
              <ol>
                {progress.map((item) => (
                  <li key={item.id} className={item.kind}>
                    <span />
                    {item.text}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
        {latestFailedRun &&
          !sortedMessages.some(
            (message) =>
              message.run_id === latestFailedRun.id && message.role === "user",
          ) && (
            <RunFailure
              run={latestFailedRun}
              retrying={retryingRunId === latestFailedRun.id}
              onRetry={() => void retryRun(latestFailedRun)}
            />
          )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-compose-area">
        {sources.length > 0 && (
          <details className="source-selector">
            <summary>
              <span className="source-selector-mark">
                <Check size={12} />
              </span>
              <span>Use sources</span>
              <span className="selected-source-count">
                {selectedSourceIds.length} selected
              </span>
              <ArrowDown size={13} className="selector-chevron" />
            </summary>
            <div className="source-check-list">
              {sources.map((source) => (
                <label className="source-check-row" key={source.id}>
                  <input
                    type="checkbox"
                    checked={selectedSourceIds.includes(source.id)}
                    onChange={() => toggleSource(source.id)}
                  />
                  <span className="source-check-icon">
                    <SourceGlyph kind={source.kind} />
                  </span>
                  <span className="source-check-copy">
                    <strong>{source.display_name}</strong>
                    <small>
                      {source.kind} · v{source.version}
                    </small>
                  </span>
                  <span
                    className={`source-check-state ${source.state.toLowerCase()}`}
                  >
                    {source.state}
                  </span>
                </label>
              ))}
            </div>
          </details>
        )}
        <form
          className={`chat-composer ${!modelAvailable ? "disabled" : ""}`}
          onSubmit={submit}
        >
          <textarea
            aria-label="Ask a question about your sources"
            placeholder={
              modelAvailable
                ? "Ask a question about your sources…"
                : "Configure a model to start a conversation"
            }
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            maxLength={12000}
            disabled={!modelAvailable || !!activeRun || loading}
          />
          <div className="composer-bottom">
            <div className="composer-tools">
              <label className="language-choice">
                <Globe2 size={13} />
                <span className="sr-only">Answer language</span>
                <select
                  value={language}
                  onChange={(event) =>
                    setLanguage(event.target.value as AnswerLanguage)
                  }
                  disabled={!modelAvailable}
                >
                  <option value="en-IN">English</option>
                  <option value="hi-IN">हिन्दी</option>
                </select>
              </label>
              <span className="composer-hint">
                {activeRun
                  ? "A run is active"
                  : "Enter to send · Shift + Enter for a new line"}
              </span>
            </div>
            <button
              className="send-button"
              type="submit"
              disabled={
                !modelAvailable ||
                !draft.trim() ||
                sending ||
                !!activeRun ||
                loading
              }
              aria-label="Send message"
            >
              {sending ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <ArrowUp size={16} />
              )}
            </button>
          </div>
        </form>
        <div className="chat-footer-note">
          <span>
            <ShieldCheck size={12} /> Sources remain unchanged
          </span>
          <span>ANSWERS KEEP THEIR REFERENCES</span>
        </div>
      </div>
    </section>
  );
}

function MessageBubble({
  message,
  artifacts,
}: {
  message: ChatMessage;
  artifacts: RunArtifact[];
}) {
  return (
    <div className="message-row assistant">
      <span className="assistant-seal">
        <Sparkles size={13} />
      </span>
      <article className="message-card assistant">
        <div className="message-topline">
          <span>FIELDNOTE</span>
          <span className="message-run-state running">Answer</span>
        </div>
        <div className="message-content">{message.content}</div>
        {!!message.references?.evidence_ids?.length && (
          <div className="evidence-references">
            <ShieldCheck size={12} />
            <span>Evidence</span>
            {message.references.evidence_ids.map((id) => (
              <span className="evidence-id" key={id}>
                {id}
              </span>
            ))}
          </div>
        )}
        {!!message.references?.artifact_ids?.length && (
          <ArtifactLinks
            artifacts={artifacts}
            ids={message.references.artifact_ids}
          />
        )}
      </article>
    </div>
  );
}

function ArtifactLinks({
  artifacts,
  ids,
}: {
  artifacts: RunArtifact[];
  ids: string[];
}) {
  return (
    <div className="artifact-list" aria-label="Run artifacts">
      {ids.map((id) => {
        const artifact = artifacts.find((item) => item.id === id);
        return <ArtifactItem key={id} id={id} artifact={artifact} />;
      })}
    </div>
  );
}

function ArtifactItem({
  id,
  artifact,
}: {
  id: string;
  artifact?: RunArtifact;
}) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [preview, setPreview] = useState<{
    id: string;
    display_name: string;
    media_type: string;
    byte_size: number;
    text: string | null;
    truncated: boolean;
  } | null>(null);

  async function togglePreview() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (preview || loading) return;
    setLoading(true);
    setPreviewError("");
    try {
      setPreview(await chatApi.previewArtifact(id, 4000));
    } catch (reason) {
      setPreviewError(
        reason instanceof Error ? reason.message : "Could not load preview.",
      );
    } finally {
      setLoading(false);
    }
  }

  const mediaType = (preview?.media_type ?? artifact?.media_type ?? "")
    .toLowerCase()
    .split(";")[0]
    .trim();
  const isPreviewImage =
    mediaType === "image/png" || mediaType === "image/jpeg";

  return (
    <div className="artifact-entry">
      <div className="artifact-actions-row">
        <a
          className="artifact-link"
          href={chatApi.artifactUrl(id)}
          download={artifact?.display_name}
        >
          <span className="artifact-icon">
            <FileText size={14} />
          </span>
          <span>
            <strong>
              {artifact?.display_name ?? `Artifact ${id.slice(0, 8)}`}
            </strong>
            <small>
              {artifact
                ? `${artifact.media_type} · ${formatSize(artifact.byte_size)}`
                : "Download output"}
            </small>
          </span>
          <Download size={14} />
        </a>
        <button
          className="artifact-preview-toggle"
          type="button"
          onClick={() => void togglePreview()}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Hide" : "Preview"} ${artifact?.display_name ?? "artifact"}`}
        >
          <Eye size={13} /> {expanded ? "Hide" : "Preview"}
        </button>
      </div>
      {expanded && (
        <div className="artifact-preview" aria-live="polite">
          {loading ? (
            <div className="artifact-preview-message">
              <LoaderCircle size={13} className="spin" /> Loading bounded
              preview
            </div>
          ) : previewError ? (
            <div className="artifact-preview-message error">{previewError}</div>
          ) : preview && isPreviewImage ? (
            <img
              src={chatApi.artifactUrl(id)}
              alt={preview.display_name}
              loading="lazy"
            />
          ) : preview?.text !== null && preview?.text !== undefined ? (
            <>
              <pre>{preview.text}</pre>
              {preview.truncated && (
                <div className="artifact-preview-truncated">
                  Preview limited to 4,000 characters. Download the file for the
                  complete content.
                </div>
              )}
            </>
          ) : preview ? (
            <div className="artifact-preview-message">
              No inline preview is available for this file. Download it to
              inspect the full content.
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function RunFailure({
  run,
  retrying,
  onRetry,
}: {
  run: AnalysisRun;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="run-failure">
      <span className="failure-icon">
        <AlertCircle size={14} />
      </span>
      <div>
        <strong>{labelForState(run)}</strong>
        <p>
          {safeText(run.outcome?.text) ?? "This run did not produce an answer."}
        </p>
      </div>
      {run.state === "failed" && (
        <button
          onClick={onRetry}
          disabled={retrying}
          aria-label="Retry this run"
        >
          <RotateCcw size={13} />
          {retrying ? "Retrying" : "Retry"}
        </button>
      )}
    </div>
  );
}

export default ChatPanel;
