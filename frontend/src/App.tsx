import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  FileText,
  FolderPlus,
  Layers3,
  MessageSquarePlus,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Table2,
} from "lucide-react";
import ChatPanel from "./ChatPanel";

type ComponentState = { status: string; message: string };
type Readiness = {
  status: "ready" | "degraded";
  components: {
    database: ComponentState;
    storage: ComponentState;
    model: ComponentState;
    sandbox: ComponentState;
  };
};
type Workspace = { id: string; label: string; created_at: string };
type Thread = { id: string; label: string; created_at?: string };
type Source = {
  id: string;
  display_name: string;
  kind: string;
  state: string;
  version: number;
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function App() {
  const [health, setHealth] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [threads, setThreads] = useState<Thread[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [threadId, setThreadId] = useState("");
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [error, setError] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [threadName, setThreadName] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [creatingThread, setCreatingThread] = useState(false);

  const workspace = useMemo(
    () => workspaces.find((item) => item.id === workspaceId) ?? null,
    [workspaces, workspaceId],
  );
  const selectedThread = useMemo(
    () => threads.find((item) => item.id === threadId) ?? null,
    [threads, threadId],
  );

  const loadStatus = useCallback(async () => {
    const [healthResult, readinessResult] = await Promise.allSettled([
      api<{ status: "ok" }>("/api/health"),
      api<Readiness>("/api/readiness"),
    ]);
    setHealth(healthResult.status === "fulfilled" ? "online" : "offline");
    setReadiness(
      readinessResult.status === "fulfilled" ? readinessResult.value : null,
    );
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const result = await api<Workspace[]>("/api/workspaces");
      setWorkspaces(result);
      setError("");
      setWorkspaceId((current) =>
        result.some((item) => item.id === current)
          ? current
          : (result[0]?.id ?? ""),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load workspaces.",
      );
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadWorkspaces();
  }, [loadStatus, loadWorkspaces]);

  useEffect(() => {
    if (!workspaceId) {
      setThreads([]);
      setSources([]);
      setThreadId("");
      return;
    }
    let active = true;
    setLoadingWorkspace(true);
    Promise.all([
      api<Thread[]>(`/api/workspaces/${workspaceId}/threads`),
      api<Source[]>(`/api/workspaces/${workspaceId}/sources`),
    ])
      .then(([nextThreads, nextSources]) => {
        if (!active) return;
        setThreads(nextThreads);
        setSources(nextSources);
        setThreadId((current) =>
          nextThreads.some((item) => item.id === current) ? current : "",
        );
        setError("");
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load this workspace.",
          );
      })
      .finally(() => active && setLoadingWorkspace(false));
    return () => {
      active = false;
    };
  }, [workspaceId]);

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const label = workspaceName.trim();
    if (!label) return;
    setCreatingWorkspace(true);
    try {
      const created = await api<Workspace>("/api/workspaces", {
        method: "POST",
        body: JSON.stringify({ label }),
      });
      setWorkspaceName("");
      setWorkspaces((current) => [created, ...current]);
      setWorkspaceId(created.id);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create workspace.",
      );
    } finally {
      setCreatingWorkspace(false);
    }
  }

  async function createThread(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const label = threadName.trim();
    if (!workspaceId || !label) return;
    setCreatingThread(true);
    try {
      const created = await api<Thread>(
        `/api/workspaces/${workspaceId}/threads`,
        {
          method: "POST",
          body: JSON.stringify({ label }),
        },
      );
      setThreads((current) => [created, ...current]);
      setThreadId(created.id);
      setThreadName("");
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not create thread.",
      );
    } finally {
      setCreatingThread(false);
    }
  }

  const components = readiness
    ? ([
        ["Database", readiness.components.database],
        ["File store", readiness.components.storage],
        ["Model", readiness.components.model],
        ["Sandbox", readiness.components.sandbox],
      ] as const)
    : [];

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand-mark" aria-label="Fieldnote home">
          f<span>.</span>
        </div>
        <div className="rail-rule" />
        <div
          className="rail-icon active"
          aria-label="Research workspace"
          title="Research workspace"
        >
          <Layers3 size={18} strokeWidth={1.7} />
        </div>
        <div className="rail-bottom">
          <span>FN</span>
        </div>
      </aside>

      <aside className="sidebar">
        <header className="sidebar-head">
          <div className="eyebrow">FIELDNOTE / 01</div>
          <button
            className="icon-button quiet"
            onClick={() => void loadWorkspaces()}
            title="Refresh workspaces"
            aria-label="Refresh workspaces"
          >
            <RefreshCw size={15} />
          </button>
        </header>

        <div className="workspace-picker-wrap">
          <label className="mini-label" htmlFor="workspace-picker">
            Workspace
          </label>
          <div className="select-frame">
            <select
              id="workspace-picker"
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.target.value)}
            >
              {workspaces.length === 0 && (
                <option value="">No workspaces yet</option>
              )}
              {workspaces.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </div>
          <form className="quick-create" onSubmit={createWorkspace}>
            <input
              aria-label="New workspace name"
              placeholder="Name a workspace"
              value={workspaceName}
              onChange={(event) => setWorkspaceName(event.target.value)}
              maxLength={120}
            />
            <button
              disabled={!workspaceName.trim() || creatingWorkspace}
              aria-label="Create workspace"
              title="Create workspace"
            >
              <FolderPlus size={16} />
            </button>
          </form>
        </div>

        <div className="section-heading">
          <span className="mini-label">Threads</span>
          <span className="count">
            {threads.length.toString().padStart(2, "0")}
          </span>
        </div>
        <form className="thread-create" onSubmit={createThread}>
          <input
            aria-label="New thread name"
            placeholder="Start a research thread"
            value={threadName}
            onChange={(event) => setThreadName(event.target.value)}
            maxLength={120}
            disabled={!workspaceId}
          />
          <button
            disabled={!workspaceId || !threadName.trim() || creatingThread}
            aria-label="Create thread"
            title="Create thread"
          >
            <Plus size={16} />
          </button>
        </form>
        <nav className="thread-list" aria-label="Workspace threads">
          {threads.map((item, index) => (
            <button
              key={item.id}
              className={`thread-item ${threadId === item.id ? "selected" : ""}`}
              onClick={() => setThreadId(item.id)}
            >
              <span className="thread-glyph">
                {index % 2 === 0 ? (
                  <BookOpen size={15} />
                ) : (
                  <MessageSquarePlus size={15} />
                )}
              </span>
              <span className="thread-label">{item.label}</span>
              {threadId === item.id && (
                <ArrowUpRight className="thread-arrow" size={14} />
              )}
            </button>
          ))}
          {workspace && threads.length === 0 && !loadingWorkspace && (
            <p className="sidebar-empty">
              No threads here yet. Give the first one a name above.
            </p>
          )}
          {!workspace && workspaces.length === 0 && (
            <p className="sidebar-empty">
              Create a workspace to keep research and sources together.
            </p>
          )}
        </nav>
        <footer className="sidebar-footer">
          <span className={`connection-dot ${health}`} />
          <span>
            {health === "online"
              ? "API connected"
              : health === "offline"
                ? "API unavailable"
                : "Checking API"}
          </span>
          <span className="footer-spacer" />
          <span className="mono">LOCAL</span>
        </footer>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Workspaces</span>
            <ArrowDownLeft size={13} />
            <strong>{workspace?.label ?? "New workspace"}</strong>
          </div>
          <div className="topbar-right">
            <div className={`system-chip ${readiness?.status ?? health}`}>
              <span className="status-light" />
              {readiness?.status === "ready"
                ? "Systems ready"
                : readiness
                  ? "Needs setup"
                  : health === "offline"
                    ? "Disconnected"
                    : "Connecting"}
            </div>
            <span className="topbar-date">RESEARCH / WORKSPACE</span>
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <AlertCircle size={16} />
            <span>{error}</span>
            <button onClick={() => setError("")} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}

        <div className={`content-grid ${selectedThread ? "chat-active" : ""}`}>
          <div className={`center-column ${selectedThread ? "chat-mode" : ""}`}>
            {selectedThread ? (
              <ChatPanel
                threadId={selectedThread.id}
                sources={sources}
                modelAvailable={
                  readiness?.components.model.status === "configured"
                }
                modelMessage={
                  readiness?.components.model.message ??
                  (health === "offline"
                    ? "The API is unavailable. Reconnect to continue."
                    : "Checking model configuration...")
                }
              />
            ) : (
              <>
                <div className="hero-kicker">
                  <span className="kicker-line" /> ANALYST DESK{" "}
                  <span className="kicker-index">/ 001</span>
                </div>
                <h1>
                  {workspace
                    ? "A clearer view\nof your sources."
                    : "Make room for\na new inquiry."}
                </h1>
                <p className="hero-copy">
                  {workspace
                    ? "Bring documents and data together, then trace each answer back to the material behind it."
                    : "Create a workspace to collect source material and start a research thread."}
                </p>

                {readiness?.components.model.status === "unavailable" && (
                  <div className="model-notice">
                    <span className="notice-icon">
                      <Sparkles size={15} />
                    </span>
                    <div>
                      <strong>Chat is not configured yet</strong>
                      <p>
                        {readiness.components.model.message ||
                          "Add model credentials to enable assistant responses."}
                      </p>
                    </div>
                    <span className="notice-state">OFFLINE</span>
                  </div>
                )}
                {health === "offline" && (
                  <div className="model-notice connection-notice">
                    <span className="notice-icon">
                      <AlertCircle size={15} />
                    </span>
                    <div>
                      <strong>Could not reach the API</strong>
                      <p>
                        Check that the backend is running, then refresh this
                        page.
                      </p>
                    </div>
                    <button
                      className="text-action"
                      onClick={() => {
                        void loadStatus();
                        void loadWorkspaces();
                      }}
                    >
                      Retry
                    </button>
                  </div>
                )}

                <div className="workspace-overview">
                  <div className="overview-head">
                    <div>
                      <span className="mini-label">Workspace contents</span>
                      <span className="overview-caption">
                        {workspace
                          ? "A live view of this workspace"
                          : "Nothing collected yet"}
                      </span>
                    </div>
                    <span className="overview-number">
                      {String(sources.length).padStart(2, "0")}{" "}
                      <small>SOURCES</small>
                    </span>
                  </div>
                  <div className="overview-divider" />
                  {sources.length > 0 ? (
                    <div
                      className="source-table"
                      role="list"
                      aria-label="Selected workspace sources"
                    >
                      {sources.map((source, index) => (
                        <div
                          className="source-row"
                          role="listitem"
                          key={source.id}
                        >
                          <span className="source-index">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="source-kind-icon">
                            {source.kind.toLowerCase().includes("csv") ||
                            source.kind.toLowerCase().includes("sheet") ||
                            source.kind.toLowerCase().includes("database") ? (
                              <Table2 size={17} />
                            ) : (
                              <FileText size={17} />
                            )}
                          </span>
                          <div className="source-meta">
                            <strong>{source.display_name}</strong>
                            <span>
                              {source.kind} <i>·</i> {source.version}
                            </span>
                          </div>
                          <span
                            className={`source-state ${source.state.toLowerCase()}`}
                          >
                            {source.state}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-sources">
                      <div className="empty-illustration">
                        <div className="paper paper-back" />
                        <div className="paper paper-front">
                          <span />
                          <span />
                          <span />
                        </div>
                        <div className="empty-plus">
                          <Plus size={16} />
                        </div>
                      </div>
                      <div className="empty-copy">
                        <strong>
                          {workspace
                            ? "Your source list is empty"
                            : "Start with a workspace"}
                        </strong>
                        <p>
                          {workspace
                            ? "Sources added to this workspace will appear here with their current processing state."
                            : "A workspace gives related sources and research threads one place to live."}
                        </p>
                      </div>
                      {!workspace && (
                        <form
                          className="empty-create"
                          onSubmit={createWorkspace}
                        >
                          <input
                            aria-label="Workspace name"
                            placeholder="e.g. Scheme review, Q3"
                            value={workspaceName}
                            onChange={(event) =>
                              setWorkspaceName(event.target.value)
                            }
                            maxLength={120}
                          />
                          <button
                            disabled={
                              !workspaceName.trim() || creatingWorkspace
                            }
                          >
                            <FolderPlus size={15} /> Create workspace
                          </button>
                        </form>
                      )}
                      {workspace && (
                        <div className="no-upload-yet">
                          <span className="upload-mark">
                            <Search size={15} />
                          </span>
                          <span>Source upload arrives in a later phase</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="below-note">
                  <span className="note-index">01</span>
                  <span>
                    Original files stay unchanged. Analysis outputs will be
                    stored separately.
                  </span>
                  <span className="note-rule" />
                </div>
              </>
            )}
          </div>

          <aside className="right-column">
            <div className="right-head">
              <span className="mini-label">System check</span>
              <span className="live-label">
                <span className="live-dot" />
                LIVE
              </span>
            </div>
            <p className="right-intro">
              Service status from this development environment.
            </p>
            <div className="service-list">
              {components.length ? (
                components.map(([label, detail]) => (
                  <div className="service-row" key={label}>
                    <span className={`service-marker ${detail.status}`} />
                    <div>
                      <strong>{label}</strong>
                      <span>{detail.message || detail.status}</span>
                    </div>
                    <span className="service-status">{detail.status}</span>
                  </div>
                ))
              ) : (
                <div className="service-loading">
                  <span
                    className={`service-marker ${health === "offline" ? "unavailable" : "checking"}`}
                  />
                  <div>
                    <strong>
                      {health === "offline"
                        ? "API unavailable"
                        : "Checking services"}
                    </strong>
                    <span>
                      {health === "offline"
                        ? "Backend did not respond"
                        : "Waiting for readiness response"}
                    </span>
                  </div>
                </div>
              )}
            </div>

            <div className="right-separator" />
            <div className="sources-side-head">
              <span className="mini-label">Selected sources</span>
              <span className="source-count">
                {String(sources.length).padStart(2, "0")}
              </span>
            </div>
            {sources.length ? (
              <div className="compact-sources">
                {sources.map((source) => (
                  <div className="compact-source" key={source.id}>
                    <span className="compact-icon">
                      {source.kind.toLowerCase().includes("csv") ||
                      source.kind.toLowerCase().includes("sheet") ? (
                        <Table2 size={15} />
                      ) : (
                        <FileText size={15} />
                      )}
                    </span>
                    <span>{source.display_name}</span>
                    <i className={`tiny-state ${source.state.toLowerCase()}`} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="selected-empty">
                Sources connected to this workspace will be listed here.
              </p>
            )}

            <div className="workspace-card">
              <div className="card-topline">
                <span>FIELDNOTE NOTE</span>
                <span>01 / 03</span>
              </div>
              <div className="card-mark">“</div>
              <p>
                Every result should lead you back to the source that supports
                it.
              </p>
              <div className="card-footer">
                <span>RESEARCH PRINCIPLE</span>
                <span className="card-line" />
              </div>
            </div>
            <div className="right-footer">
              <span>BUILD 00.1</span>
              <span>LOCAL INSTANCE</span>
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;
