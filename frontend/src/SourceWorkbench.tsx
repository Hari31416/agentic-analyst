import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Database,
  FileText,
  FileSpreadsheet,
  LoaderCircle,
  Plus,
  RefreshCw,
  Server,
  Upload,
} from "lucide-react";
import {
  ConnectionDraft,
  ConnectionTestResult,
  DatasetProfile,
  DatasetRows,
  DatasetSummary,
  SourceSchema,
  SourceView,
  structuredApi,
} from "./structuredApi";
import { DocumentBlock, DocumentView, documentApi } from "./documentApi";
import "./structured.css";

type SourceWorkbenchProps = {
  workspaceId: string;
  sources: SourceView[];
  datasets: DatasetSummary[];
  datasetErrors: Record<string, string>;
  onSourcesChanged: () => Promise<void>;
};

const emptyConnection: ConnectionDraft = {
  dialect: "postgresql",
  host: "localhost",
  port: 5432,
  database_name: "",
  username: "",
  password: "",
  options: { ssl_mode: "verify-full" },
};

function identityLabel(identity: DatasetSummary["identity"]): string {
  if (typeof identity === "string") return identity;
  const values = Object.values(identity);
  return values.length ? values.map(String).join(" · ") : "Dataset";
}

function displayCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isDatabase(source: SourceView): boolean {
  return /mysql|postgres|database|connection/i.test(source.kind);
}

function isDocument(source: SourceView): boolean {
  return /pdf|docx|document/i.test(source.kind);
}

function SourceWorkbench({
  workspaceId,
  sources,
  datasets,
  datasetErrors,
  onSourcesChanged,
}: SourceWorkbenchProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [selectedDocumentFile, setSelectedDocumentFile] = useState<File | null>(
    null,
  );
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [documentUploadError, setDocumentUploadError] = useState("");
  const [documents, setDocuments] = useState<DocumentView[]>([]);
  const [documentsError, setDocumentsError] = useState("");
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [documentBlocks, setDocumentBlocks] = useState<DocumentBlock[]>([]);
  const [documentBlockTotal, setDocumentBlockTotal] = useState(0);
  const [documentBlockOffset, setDocumentBlockOffset] = useState(0);
  const [loadingDocumentBlocks, setLoadingDocumentBlocks] = useState(false);
  const [documentBlocksError, setDocumentBlocksError] = useState("");
  const [connection, setConnection] =
    useState<ConnectionDraft>(emptyConnection);
  const [connectionName, setConnectionName] = useState("");
  const [testingConnection, setTestingConnection] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    null,
  );
  const [connectionError, setConnectionError] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [profile, setProfile] = useState<DatasetProfile | null>(null);
  const [rows, setRows] = useState<DatasetRows | null>(null);
  const [offset, setOffset] = useState(0);
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [loadingRows, setLoadingRows] = useState(false);
  const [inspectorError, setInspectorError] = useState("");
  const [schema, setSchema] = useState<SourceSchema | null>(null);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [schemaError, setSchemaError] = useState("");
  const [sourceDescription, setSourceDescription] = useState("");
  const [metricHints, setMetricHints] = useState<
    { key: string; value: string }[]
  >([]);
  const [savingMetadata, setSavingMetadata] = useState(false);
  const [metadataMessage, setMetadataMessage] = useState("");
  const [metadataError, setMetadataError] = useState("");

  const selectedSource = sources.find(
    (source) => source.id === selectedSourceId,
  );
  const sourceDatasets = useMemo(
    () => datasets.filter((dataset) => dataset.source_id === selectedSourceId),
    [datasets, selectedSourceId],
  );
  const selectedDataset = sourceDatasets.find(
    (dataset) => dataset.id === selectedDatasetId,
  );
  const sourceDocuments = useMemo(
    () =>
      documents.filter((document) => document.source_id === selectedSourceId),
    [documents, selectedSourceId],
  );
  const selectedDocument = sourceDocuments.find(
    (document) => document.id === selectedDocumentId,
  );
  const hasDocument = Boolean(selectedSource && isDocument(selectedSource));

  async function loadDocuments() {
    if (!workspaceId) return;
    try {
      setDocuments(await documentApi.list(workspaceId));
      setDocumentsError("");
    } catch (reason) {
      setDocumentsError(
        reason instanceof Error ? reason.message : "Could not load documents.",
      );
    }
  }

  useEffect(() => {
    setDocuments([]);
    setSelectedDocumentId("");
    setDocumentBlocks([]);
    setDocumentsError("");
    void loadDocuments();
  }, [workspaceId]);

  useEffect(() => {
    const processing = documents.some((document) =>
      ["queued", "pending", "running", "processing", "indexing"].includes(
        document.state.toLowerCase(),
      ),
    );
    if (!processing) return;
    const timer = window.setInterval(() => void loadDocuments(), 2500);
    return () => window.clearInterval(timer);
  }, [documents, workspaceId]);

  useEffect(() => {
    if (
      !sourceDocuments.some((document) => document.id === selectedDocumentId)
    ) {
      setSelectedDocumentId(sourceDocuments[0]?.id ?? "");
    }
  }, [sourceDocuments, selectedDocumentId]);

  useEffect(() => {
    if (!selectedDocumentId) {
      setDocumentBlocks([]);
      setDocumentBlockTotal(0);
      return;
    }
    let active = true;
    setLoadingDocumentBlocks(true);
    setDocumentBlocksError("");
    documentApi
      .blocks(selectedDocumentId, documentBlockOffset, 50)
      .then((result) => {
        if (!active) return;
        setDocumentBlocks(result.blocks);
        setDocumentBlockTotal(result.total);
      })
      .catch((reason: unknown) => {
        if (active)
          setDocumentBlocksError(
            reason instanceof Error
              ? reason.message
              : "Could not inspect extracted text.",
          );
      })
      .finally(() => active && setLoadingDocumentBlocks(false));
    return () => {
      active = false;
    };
  }, [selectedDocumentId, documentBlockOffset]);

  useEffect(() => {
    setSourceDescription(selectedSource?.description ?? "");
    setMetricHints(
      Object.entries(selectedSource?.metric_hints ?? {}).map(
        ([key, value]) => ({
          key,
          value,
        }),
      ),
    );
    setMetadataMessage("");
    setMetadataError("");
  }, [
    selectedSource?.id,
    selectedSource?.description,
    selectedSource?.metric_hints,
  ]);

  useEffect(() => {
    if (!sources.some((source) => source.id === selectedSourceId)) {
      setSelectedSourceId(sources[0]?.id ?? "");
      setSelectedDatasetId("");
      setProfile(null);
      setRows(null);
      setSchema(null);
    }
  }, [selectedSourceId, sources]);

  useEffect(() => {
    if (!sourceDatasets.some((dataset) => dataset.id === selectedDatasetId)) {
      setSelectedDatasetId(sourceDatasets[0]?.id ?? "");
    }
  }, [selectedDatasetId, sourceDatasets]);

  useEffect(() => {
    if (!selectedDatasetId) {
      setProfile(null);
      setRows(null);
      return;
    }
    let active = true;
    setLoadingProfile(true);
    setInspectorError("");
    Promise.all([
      structuredApi.profile(selectedDatasetId),
      structuredApi.rows(selectedDatasetId, 0, 50),
    ])
      .then(([nextProfile, nextRows]) => {
        if (!active) return;
        setProfile(nextProfile);
        setRows(nextRows);
        setOffset(0);
      })
      .catch((reason: unknown) => {
        if (active)
          setInspectorError(
            reason instanceof Error
              ? reason.message
              : "Could not inspect this dataset.",
          );
      })
      .finally(() => active && setLoadingProfile(false));
    return () => {
      active = false;
    };
  }, [selectedDataset, selectedDatasetId]);

  async function loadRows(nextOffset: number) {
    if (!selectedDatasetId) return;
    setLoadingRows(true);
    setInspectorError("");
    try {
      setRows(await structuredApi.rows(selectedDatasetId, nextOffset, 50));
      setOffset(nextOffset);
    } catch (reason) {
      setInspectorError(
        reason instanceof Error ? reason.message : "Could not load these rows.",
      );
    } finally {
      setLoadingRows(false);
    }
  }

  async function uploadFile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) return;
    const input =
      event.currentTarget.querySelector<HTMLInputElement>('input[type="file"]');
    setUploading(true);
    setUploadError("");
    try {
      const added = await structuredApi.uploadFile(workspaceId, selectedFile);
      setSelectedFile(null);
      if (input) input.value = "";
      await onSourcesChanged();
      setSelectedSourceId(added.id);
      setSelectedDatasetId("");
    } catch (reason) {
      setUploadError(
        reason instanceof Error
          ? reason.message
          : "Could not upload this file.",
      );
    } finally {
      setUploading(false);
    }
  }

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedDocumentFile) return;
    const input =
      event.currentTarget.querySelector<HTMLInputElement>('input[type="file"]');
    setUploadingDocument(true);
    setDocumentUploadError("");
    try {
      const added = await documentApi.upload(workspaceId, selectedDocumentFile);
      setSelectedDocumentFile(null);
      if (input) input.value = "";
      await onSourcesChanged();
      await loadDocuments();
      setSelectedSourceId(added.source.id);
      setSelectedDocumentId(added.document.id);
      setSelectedDatasetId("");
    } catch (reason) {
      setDocumentUploadError(
        reason instanceof Error
          ? reason.message
          : "Could not upload this document.",
      );
    } finally {
      setUploadingDocument(false);
    }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setUploadError("");
  }

  function changeDialect(dialect: ConnectionDraft["dialect"]) {
    setConnection((current) => ({
      ...current,
      dialect,
      port: dialect === "mysql" ? 3306 : 5432,
    }));
    setTestResult(null);
    setConnectionError("");
  }

  async function testConnection() {
    setTestingConnection(true);
    setTestResult(null);
    setConnectionError("");
    try {
      setTestResult(
        await structuredApi.testConnection(workspaceId, connection),
      );
    } catch (reason) {
      setConnectionError(
        reason instanceof Error ? reason.message : "Connection test failed.",
      );
    } finally {
      setTestingConnection(false);
    }
  }

  async function saveConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingConnection(true);
    setConnectionError("");
    try {
      const created = await structuredApi.saveConnection(workspaceId, {
        ...connection,
        display_name:
          connectionName.trim() ||
          `${connection.dialect === "mysql" ? "MySQL" : "PostgreSQL"} · ${connection.database_name}`,
      });
      setConnection({
        ...emptyConnection,
        dialect: connection.dialect,
        port: connection.dialect === "mysql" ? 3306 : 5432,
      });
      setConnectionName("");
      setTestResult(null);
      await onSourcesChanged();
      setSelectedSourceId(created.source.id);
      setSelectedDatasetId("");
    } catch (reason) {
      setConnectionError(
        reason instanceof Error
          ? reason.message
          : "Could not save this connection.",
      );
    } finally {
      setSavingConnection(false);
    }
  }

  async function openSchema() {
    if (!selectedSource) return;
    setLoadingSchema(true);
    setSchemaError("");
    try {
      setSchema(await structuredApi.schema(selectedSource.id));
    } catch (reason) {
      setSchemaError(
        reason instanceof Error ? reason.message : "Could not load the schema.",
      );
    } finally {
      setLoadingSchema(false);
    }
  }

  async function refreshSchema() {
    if (!selectedSource) return;
    setLoadingSchema(true);
    setSchemaError("");
    try {
      const refreshed = await structuredApi.refreshSchema(selectedSource.id);
      setSchema({
        source_id: refreshed.source.id,
        source_version: refreshed.source.version,
        schema_version: refreshed.source.schema_version ?? "updated",
        datasets: refreshed.datasets,
      });
      await onSourcesChanged();
    } catch (reason) {
      setSchemaError(
        reason instanceof Error
          ? reason.message
          : "Could not refresh the schema.",
      );
    } finally {
      setLoadingSchema(false);
    }
  }

  async function saveSourceMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSource) return;
    setSavingMetadata(true);
    setMetadataError("");
    setMetadataMessage("");
    const normalizedHints: Record<string, string> = {};
    for (const hint of metricHints) {
      const key = hint.key.trim();
      const value = hint.value.trim();
      if (!key && !value) continue;
      if (!key || !value) {
        setMetadataError("Add both a column or metric name and its meaning.");
        setSavingMetadata(false);
        return;
      }
      if (key.length > 128 || value.length > 500) {
        setMetadataError(
          "Names must be 128 characters or fewer; meanings 500 or fewer.",
        );
        setSavingMetadata(false);
        return;
      }
      if (normalizedHints[key]) {
        setMetadataError(`“${key}” appears more than once.`);
        setSavingMetadata(false);
        return;
      }
      normalizedHints[key] = value;
    }
    if (Object.keys(normalizedHints).length > 32) {
      setMetadataError("A source can have up to 32 metric hints.");
      setSavingMetadata(false);
      return;
    }
    try {
      await structuredApi.updateSourceMetadata(workspaceId, selectedSource.id, {
        description: sourceDescription.trim() || null,
        metric_hints: normalizedHints,
      });
      await onSourcesChanged();
      setMetadataMessage(
        "Source context saved. It will be used in future analyses.",
      );
    } catch (reason) {
      setMetadataError(
        reason instanceof Error
          ? reason.message
          : "Could not save source context.",
      );
    } finally {
      setSavingMetadata(false);
    }
  }

  const columns = profile?.details.columns ?? [];

  return (
    <section className="source-workbench" aria-label="Sources and datasets">
      <div className="source-actions">
        <form className="file-upload-form" onSubmit={uploadFile}>
          <label className="upload-pick">
            <Upload size={15} />
            <span>{selectedFile?.name ?? "Choose a data file"}</span>
            <input
              type="file"
              accept=".csv,.xlsx,.xls,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
              onChange={chooseFile}
            />
          </label>
          <button
            className="source-primary-button"
            type="submit"
            disabled={!selectedFile || uploading}
          >
            {uploading ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <Plus size={14} />
            )}
            {uploading ? "Uploading" : "Add file"}
          </button>
          <span className="supported-formats">CSV · XLSX · XLS</span>
        </form>
        <form className="document-upload-form" onSubmit={uploadDocument}>
          <label className="upload-pick document-upload-pick">
            <FileText size={15} />
            <span>
              {selectedDocumentFile?.name ?? "Choose a research document"}
            </span>
            <input
              type="file"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(event) => {
                setSelectedDocumentFile(event.target.files?.[0] ?? null);
                setDocumentUploadError("");
              }}
            />
          </label>
          <button
            className="source-primary-button"
            type="submit"
            disabled={!selectedDocumentFile || uploadingDocument}
          >
            {uploadingDocument ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <Plus size={14} />
            )}
            {uploadingDocument ? "Adding" : "Add document"}
          </button>
          <span className="supported-formats">PDF · DOCX</span>
        </form>
        {uploadError && (
          <p className="source-form-error" role="alert">
            <AlertCircle size={13} />
            {uploadError}
          </p>
        )}
        {documentUploadError && (
          <p className="source-form-error" role="alert">
            <AlertCircle size={13} />
            {documentUploadError}
          </p>
        )}
        {documentsError && (
          <p className="source-form-error" role="alert">
            <AlertCircle size={13} />
            {documentsError}
          </p>
        )}

        <details className="connection-details">
          <summary>
            <Database size={15} />
            <span>Connect a database</span>
            <ChevronDown size={14} className="connection-chevron" />
          </summary>
          <form className="connection-form" onSubmit={saveConnection}>
            <div className="connection-form-heading">
              <strong>Read-only source connection</strong>
              <span>Credentials stay on the server.</span>
            </div>
            <div className="connection-grid">
              <label>
                <span>Database</span>
                <select
                  value={connection.dialect}
                  onChange={(event) =>
                    changeDialect(
                      event.target.value as ConnectionDraft["dialect"],
                    )
                  }
                >
                  <option value="postgresql">PostgreSQL</option>
                  <option value="mysql">MySQL</option>
                </select>
              </label>
              <label>
                <span>
                  Display name <i>optional</i>
                </span>
                <input
                  value={connectionName}
                  onChange={(event) => setConnectionName(event.target.value)}
                  placeholder="e.g. Finance reporting"
                  maxLength={120}
                />
              </label>
              <label>
                <span>Host</span>
                <input
                  required
                  value={connection.host}
                  onChange={(event) =>
                    setConnection({ ...connection, host: event.target.value })
                  }
                  placeholder="db.example.internal"
                  autoComplete="off"
                />
              </label>
              <label>
                <span>Port</span>
                <input
                  required
                  type="number"
                  min={1}
                  max={65535}
                  value={connection.port}
                  onChange={(event) =>
                    setConnection({
                      ...connection,
                      port: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                <span>Database name</span>
                <input
                  required
                  value={connection.database_name}
                  onChange={(event) =>
                    setConnection({
                      ...connection,
                      database_name: event.target.value,
                    })
                  }
                  autoComplete="off"
                />
              </label>
              <label>
                <span>Username</span>
                <input
                  required
                  value={connection.username}
                  onChange={(event) =>
                    setConnection({
                      ...connection,
                      username: event.target.value,
                    })
                  }
                  autoComplete="username"
                />
              </label>
              <label className="connection-password">
                <span>Password</span>
                <input
                  required
                  type="password"
                  value={connection.password}
                  onChange={(event) =>
                    setConnection({
                      ...connection,
                      password: event.target.value,
                    })
                  }
                  autoComplete="new-password"
                />
              </label>
            </div>
            <label className="tls-option">
              <span>TLS mode</span>
              <select
                value={connection.options.ssl_mode}
                onChange={(event) =>
                  setConnection({
                    ...connection,
                    options: {
                      ...connection.options,
                      ssl_mode: event.target
                        .value as ConnectionDraft["options"]["ssl_mode"],
                    },
                  })
                }
              >
                <option value="verify-full">Verify certificate and host</option>
                <option value="verify-ca">Verify certificate</option>
                <option value="require">Require encrypted connection</option>
                <option value="prefer">Prefer TLS</option>
                <option value="disable">Disable TLS</option>
              </select>
            </label>
            {connectionError && (
              <p className="source-form-error" role="alert">
                <AlertCircle size={13} />
                {connectionError}
              </p>
            )}
            {testResult && (
              <p
                className={`connection-test-result ${testResult.ok ? "success" : "failure"}`}
                role="status"
              >
                {testResult.ok ? (
                  <CheckCircle2 size={14} />
                ) : (
                  <AlertCircle size={14} />
                )}
                {testResult.message}
                {typeof testResult.latency_ms === "number" && (
                  <span>{testResult.latency_ms} ms</span>
                )}
              </p>
            )}
            <div className="connection-actions">
              <button
                className="secondary-source-button"
                type="button"
                onClick={() => void testConnection()}
                disabled={
                  testingConnection ||
                  savingConnection ||
                  !connection.host ||
                  !connection.database_name ||
                  !connection.username ||
                  !connection.password
                }
              >
                {testingConnection ? (
                  <LoaderCircle size={13} className="spin" />
                ) : (
                  <Server size={13} />
                )}
                {testingConnection ? "Testing" : "Test connection"}
              </button>
              <button
                className="source-primary-button"
                type="submit"
                disabled={
                  savingConnection ||
                  testingConnection ||
                  !connection.host ||
                  !connection.database_name ||
                  !connection.username ||
                  !connection.password
                }
              >
                {savingConnection ? (
                  <LoaderCircle size={13} className="spin" />
                ) : (
                  <Plus size={13} />
                )}
                {savingConnection ? "Saving" : "Save connection"}
              </button>
            </div>
          </form>
        </details>
      </div>

      {sources.length === 0 ? (
        <div className="catalog-empty">
          <span className="catalog-empty-mark">
            <FileSpreadsheet size={17} />
          </span>
          <div>
            <strong>No sources in this workspace</strong>
            <p>
              Add a PDF or DOCX, upload a spreadsheet, or connect a read-only
              database.
            </p>
          </div>
        </div>
      ) : (
        <div className="catalog-layout">
          <nav className="catalog-source-list" aria-label="Workspace sources">
            <div className="catalog-label">
              Sources <span>{String(sources.length).padStart(2, "0")}</span>
            </div>
            {sources.map((source) => (
              <button
                className={`catalog-source ${source.id === selectedSourceId ? "selected" : ""}`}
                key={source.id}
                onClick={() => {
                  setSelectedSourceId(source.id);
                  setSelectedDatasetId("");
                  setSchema(null);
                }}
              >
                <span className="catalog-source-icon">
                  {isDatabase(source) ? (
                    <Database size={15} />
                  ) : isDocument(source) ? (
                    <FileText size={15} />
                  ) : (
                    <FileSpreadsheet size={15} />
                  )}
                </span>
                <span className="catalog-source-copy">
                  <strong>{source.display_name}</strong>
                  <small>
                    {source.kind} · v{source.version}
                  </small>
                </span>
                <span
                  className={`catalog-state ${source.state.toLowerCase()}`}
                />
              </button>
            ))}
          </nav>

          <div className="catalog-detail">
            {selectedSource ? (
              <>
                <header className="catalog-detail-head">
                  <div>
                    <span className="mini-label">
                      {selectedSource.kind} / SOURCE {selectedSource.version}
                    </span>
                    <h3>{selectedSource.display_name}</h3>
                  </div>
                  {isDatabase(selectedSource) && (
                    <div className="schema-actions">
                      <button
                        className="icon-button"
                        onClick={() => void openSchema()}
                        disabled={loadingSchema}
                        title="Inspect database schema"
                        aria-label="Inspect database schema"
                      >
                        <Database size={14} />
                      </button>
                      <button
                        className="icon-button"
                        onClick={() => void refreshSchema()}
                        disabled={loadingSchema}
                        title="Refresh schema"
                        aria-label="Refresh schema"
                      >
                        {loadingSchema ? (
                          <LoaderCircle size={14} className="spin" />
                        ) : (
                          <RefreshCw size={14} />
                        )}
                      </button>
                    </div>
                  )}
                </header>
                {hasDocument ? (
                  <DocumentInspector
                    documents={sourceDocuments}
                    selectedDocument={selectedDocument}
                    selectedDocumentId={selectedDocumentId}
                    blocks={documentBlocks}
                    blockTotal={documentBlockTotal}
                    blockOffset={documentBlockOffset}
                    loadingBlocks={loadingDocumentBlocks}
                    blocksError={documentBlocksError}
                    onSelect={(id) => {
                      setSelectedDocumentId(id);
                      setDocumentBlockOffset(0);
                    }}
                    onPage={(offset) => setDocumentBlockOffset(offset)}
                  />
                ) : (
                  <>
                    <form
                      className="source-context"
                      onSubmit={saveSourceMetadata}
                    >
                      <label className="source-context-description">
                        <span>Context for analysis</span>
                        <textarea
                          value={sourceDescription}
                          onChange={(event) =>
                            setSourceDescription(event.target.value)
                          }
                          placeholder="Describe what this source covers, its time period, or important caveats."
                          rows={2}
                        />
                      </label>
                      <div className="source-context-hints">
                        <div className="source-context-hints-head">
                          <div>
                            <strong>Column and metric meanings</strong>
                            <span>
                              For example, “ARR” means annual recurring revenue
                              in INR.
                            </span>
                          </div>
                          <button
                            className="secondary-source-button"
                            type="button"
                            onClick={() =>
                              setMetricHints((current) => [
                                ...current,
                                { key: "", value: "" },
                              ])
                            }
                            disabled={
                              metricHints.length >= 32 || savingMetadata
                            }
                          >
                            <Plus size={12} /> Add meaning
                          </button>
                        </div>
                        {metricHints.map((hint, index) => (
                          <div className="source-context-hint" key={index}>
                            <input
                              aria-label="Column or metric name"
                              value={hint.key}
                              maxLength={128}
                              placeholder="Column or metric"
                              onChange={(event) =>
                                setMetricHints((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? { ...item, key: event.target.value }
                                      : item,
                                  ),
                                )
                              }
                            />
                            <input
                              aria-label="Meaning and units"
                              value={hint.value}
                              maxLength={500}
                              placeholder="Meaning, units, or caveat"
                              onChange={(event) =>
                                setMetricHints((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? { ...item, value: event.target.value }
                                      : item,
                                  ),
                                )
                              }
                            />
                            <button
                              className="remove-hint-button"
                              type="button"
                              aria-label={`Remove meaning ${index + 1}`}
                              onClick={() =>
                                setMetricHints((current) =>
                                  current.filter(
                                    (_, itemIndex) => itemIndex !== index,
                                  ),
                                )
                              }
                              disabled={savingMetadata}
                            >
                              ×
                            </button>
                          </div>
                        ))}
                      </div>
                      {(metadataError || metadataMessage) && (
                        <p
                          className={
                            metadataError
                              ? "source-form-error"
                              : "source-context-saved"
                          }
                          role={metadataError ? "alert" : "status"}
                        >
                          {metadataError ? (
                            <AlertCircle size={13} />
                          ) : (
                            <CheckCircle2 size={13} />
                          )}
                          {metadataError || metadataMessage}
                        </p>
                      )}
                      <div className="source-context-actions">
                        <span>Up to 32 hints · used as analysis context</span>
                        <button
                          className="source-primary-button"
                          type="submit"
                          disabled={savingMetadata}
                        >
                          {savingMetadata ? (
                            <LoaderCircle size={13} className="spin" />
                          ) : null}
                          {savingMetadata ? "Saving context" : "Save context"}
                        </button>
                      </div>
                    </form>
                    {schemaError && (
                      <p className="source-form-error" role="alert">
                        <AlertCircle size={13} />
                        {schemaError}
                      </p>
                    )}
                    {schema && <SchemaSummary schema={schema} />}
                    <div className="dataset-strip-head">
                      <span className="mini-label">Datasets / sheets</span>
                      <span>
                        {String(sourceDatasets.length).padStart(2, "0")}
                      </span>
                    </div>
                    {sourceDatasets.length ? (
                      <div
                        className="dataset-pills"
                        role="list"
                        aria-label="Source datasets"
                      >
                        {sourceDatasets.map((dataset) => (
                          <button
                            role="listitem"
                            className={`dataset-choice ${selectedDatasetId === dataset.id ? "selected" : ""}`}
                            key={dataset.id}
                            onClick={() => setSelectedDatasetId(dataset.id)}
                          >
                            <span>{identityLabel(dataset.identity)}</span>
                            <small>{dataset.designation}</small>
                          </button>
                        ))}
                      </div>
                    ) : datasetErrors[selectedSource.id] ? (
                      <p className="source-form-error" role="alert">
                        <AlertCircle size={13} />
                        {datasetErrors[selectedSource.id]}
                      </p>
                    ) : (
                      <p className="no-datasets">
                        {selectedSource.state !== "ready"
                          ? `This source is ${selectedSource.state.toLowerCase()}. Datasets are unavailable until it is ready.`
                          : isDatabase(selectedSource)
                            ? "Inspect or refresh the schema to discover tables."
                            : "No sheets were found in this source."}
                      </p>
                    )}

                    {inspectorError && (
                      <p className="source-form-error" role="alert">
                        <AlertCircle size={13} />
                        {inspectorError}
                      </p>
                    )}
                    {loadingProfile ? (
                      <div className="profile-loading">
                        <LoaderCircle size={15} className="spin" /> Loading
                        dataset profile
                      </div>
                    ) : profile ? (
                      <>
                        <div className="profile-stats">
                          <div>
                            <span>ROWS</span>
                            <strong>
                              {profile.details.row_count === null ||
                              profile.details.row_count === undefined
                                ? "Unknown"
                                : profile.details.row_count.toLocaleString()}
                              {profile.details.row_count_exact === false && (
                                <i> est.</i>
                              )}
                            </strong>
                          </div>
                          <div>
                            <span>COLUMNS</span>
                            <strong>{columns.length}</strong>
                          </div>
                          {profile.details.encoding && (
                            <div>
                              <span>ENCODING</span>
                              <strong>{profile.details.encoding}</strong>
                            </div>
                          )}
                        </div>
                        {!!profile.details.warnings?.length && (
                          <div className="profile-warnings">
                            {profile.details.warnings.map((warning, index) => (
                              <p key={`${index}-${warning}`}>
                                <AlertCircle size={12} />
                                {warning}
                              </p>
                            ))}
                          </div>
                        )}
                        <div
                          className="column-profile"
                          role="table"
                          aria-label="Dataset column profile"
                        >
                          <div className="column-head" role="row">
                            <span>Column</span>
                            <span>Type</span>
                            <span>Missing</span>
                            <span>Hints</span>
                          </div>
                          {columns.map((column) => (
                            <div
                              className="column-row"
                              role="row"
                              key={column.name}
                            >
                              <strong>{column.name}</strong>
                              <span>
                                {column.type || column.duckdb_type || "Unknown"}
                              </span>
                              <span>{column.missing_count ?? "—"}</span>
                              <span>{column.hints?.join(", ") || "—"}</span>
                            </div>
                          ))}
                        </div>
                        <div className="sample-head">
                          <span className="mini-label">Sample rows</span>
                          <span>
                            {rows
                              ? `${rows.total_rows.toLocaleString()} total`
                              : ""}
                          </span>
                        </div>
                        {rows && columns.length > 0 && (
                          <div className="sample-table-wrap">
                            <table className="sample-table">
                              <thead>
                                <tr>
                                  {columns.map((column) => (
                                    <th key={column.name}>{column.name}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {rows.rows.map((row, index) => (
                                  <tr key={`${rows.offset}-${index}`}>
                                    {columns.map((column) => (
                                      <td
                                        key={column.name}
                                        title={displayCell(row[column.name])}
                                      >
                                        {displayCell(row[column.name])}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                        {loadingRows && (
                          <div className="rows-loading">
                            <LoaderCircle size={12} className="spin" /> Loading
                            rows
                          </div>
                        )}
                        {rows && (
                          <div className="rows-pagination">
                            <span>
                              {rows.rows.length
                                ? `${offset + 1}–${Math.min(offset + rows.rows.length, rows.total_rows)} of ${rows.total_rows.toLocaleString()}`
                                : "No rows"}
                              {rows.truncated && " · result truncated"}
                            </span>
                            <div>
                              <button
                                onClick={() =>
                                  void loadRows(
                                    Math.max(0, offset - rows.limit),
                                  )
                                }
                                disabled={offset === 0 || loadingRows}
                                aria-label="Previous rows"
                              >
                                <ArrowLeft size={13} />
                              </button>
                              <button
                                onClick={() =>
                                  void loadRows(offset + rows.limit)
                                }
                                disabled={
                                  offset + rows.limit >= rows.total_rows ||
                                  loadingRows
                                }
                                aria-label="Next rows"
                              >
                                <ArrowRight size={13} />
                              </button>
                            </div>
                          </div>
                        )}
                      </>
                    ) : selectedDatasetId ? (
                      <div className="profile-loading">
                        Choose a dataset to inspect its profile and sample.
                      </div>
                    ) : null}
                  </>
                )}
              </>
            ) : (
              <div className="catalog-prompt">
                Select a source to inspect its contents.
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function documentLocation(location: Record<string, unknown>): string {
  const parts = Object.entries(location)
    .filter(
      ([, value]) => value !== null && value !== undefined && value !== "",
    )
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${String(value)}`);
  return parts.join(" · ") || "Location not provided";
}

function documentWarnings(document: DocumentView): string[] {
  const warnings = document.details.warnings;
  return Array.isArray(warnings)
    ? warnings.filter(
        (warning): warning is string => typeof warning === "string",
      )
    : [];
}

function DocumentInspector({
  documents,
  selectedDocument,
  selectedDocumentId,
  blocks,
  blockTotal,
  blockOffset,
  loadingBlocks,
  blocksError,
  onSelect,
  onPage,
}: {
  documents: DocumentView[];
  selectedDocument: DocumentView | undefined;
  selectedDocumentId: string;
  blocks: DocumentBlock[];
  blockTotal: number;
  blockOffset: number;
  loadingBlocks: boolean;
  blocksError: string;
  onSelect: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  if (!selectedDocument) {
    return (
      <div className="document-inspector empty">
        <BookOpen size={18} />
        <div>
          <strong>No document record is available</strong>
          <p>
            The uploaded file may still be registering. Refresh the workspace to
            try again.
          </p>
        </div>
      </div>
    );
  }
  const warnings = documentWarnings(selectedDocument);
  const progress =
    typeof selectedDocument.progress === "number"
      ? Math.max(0, Math.min(100, selectedDocument.progress))
      : null;
  return (
    <div className="document-inspector">
      <div className="document-toolbar">
        <div className="document-heading">
          <span className="mini-label">DOCUMENT / EXTRACTED TEXT</span>
          <strong>{selectedDocument.display_name}</strong>
          <small>
            Version {selectedDocument.source_version}
            {selectedDocument.extractor_version
              ? ` · extractor ${selectedDocument.extractor_version}`
              : ""}
          </small>
        </div>
        {documents.length > 1 && (
          <label className="document-version-select">
            <span>Document version</span>
            <select
              value={selectedDocumentId}
              onChange={(event) => onSelect(event.target.value)}
            >
              {documents.map((document) => (
                <option key={document.id} value={document.id}>
                  v{document.source_version} · {document.state}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="document-state-line">
        <span
          className={`document-state ${selectedDocument.state.toLowerCase()}`}
        >
          {selectedDocument.state.replaceAll("_", " ")}
        </span>
        <span>
          {selectedDocument.stage?.replaceAll("_", " ") || "Extraction"}
        </span>
        {progress !== null && <span>{Math.round(progress)}%</span>}
      </div>
      {progress !== null && (
        <div
          className="document-progress"
          role="progressbar"
          aria-label="Document processing progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
      )}
      {warnings.length > 0 && (
        <div className="document-warnings">
          {warnings.map((warning, index) => (
            <p key={`${index}-${warning}`}>
              <AlertCircle size={12} /> {warning}
            </p>
          ))}
        </div>
      )}
      {blocksError && (
        <p className="source-form-error" role="alert">
          <AlertCircle size={13} /> {blocksError}
        </p>
      )}
      <div className="document-block-heading">
        <span className="mini-label">EXTRACTED BLOCKS</span>
        <span>{blockTotal.toLocaleString()} total</span>
      </div>
      {loadingBlocks ? (
        <div className="profile-loading">
          <LoaderCircle size={15} className="spin" /> Loading extracted text
        </div>
      ) : blocks.length ? (
        <div className="document-block-list">
          {blocks.map((block) => (
            <article className="document-block" key={block.id}>
              <div className="document-block-meta">
                <span>{block.kind.replaceAll("_", " ")}</span>
                <span>{documentLocation(block.location)}</span>
              </div>
              {block.heading && <h4>{block.heading}</h4>}
              <p>{block.text}</p>
              {(block.language || block.scripts?.length) && (
                <small>
                  {[block.language, ...(block.scripts ?? [])]
                    .filter(Boolean)
                    .join(" · ")}
                </small>
              )}
            </article>
          ))}
        </div>
      ) : (
        <p className="no-datasets">
          {selectedDocument.state === "failed"
            ? "Extraction failed. See the warning above for details."
            : selectedDocument.state === "needs_ocr"
              ? "This document needs OCR before its text can be inspected."
              : "Text blocks will appear here when extraction is complete."}
        </p>
      )}
      {blockTotal > 50 && (
        <div className="rows-pagination">
          <span>
            {blockOffset + 1}–
            {Math.min(blockOffset + blocks.length, blockTotal)} of{" "}
            {blockTotal.toLocaleString()}
          </span>
          <div>
            <button
              onClick={() => onPage(Math.max(0, blockOffset - 50))}
              disabled={blockOffset === 0 || loadingBlocks}
              aria-label="Previous text blocks"
            >
              <ArrowLeft size={13} />
            </button>
            <button
              onClick={() => onPage(blockOffset + 50)}
              disabled={blockOffset + 50 >= blockTotal || loadingBlocks}
              aria-label="Next text blocks"
            >
              <ArrowRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SchemaSummary({ schema }: { schema: SourceSchema }) {
  return (
    <details className="schema-summary">
      <summary>
        <span>Schema version {schema.schema_version}</span>
        <ChevronDown size={13} />
      </summary>
      <div className="schema-datasets">
        {schema.datasets.map((dataset) => {
          const columns = Array.isArray(dataset.details.columns)
            ? (dataset.details.columns as {
                name?: string;
                type?: string;
                duckdb_type?: string;
              }[])
            : [];
          return (
            <div className="schema-dataset" key={dataset.id}>
              <strong>{identityLabel(dataset.identity)}</strong>
              <span>
                {columns
                  .map(
                    (column) =>
                      `${column.name ?? "Column"} (${column.type ?? column.duckdb_type ?? "?"})`,
                  )
                  .join(" · ") || "No column details"}
              </span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

export default SourceWorkbench;
