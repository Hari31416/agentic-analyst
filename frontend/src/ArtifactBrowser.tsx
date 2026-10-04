import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Code2,
  Copy,
  Database,
  Download,
  Eye,
  FileArchive,
  FileText,
  Layers,
  LoaderCircle,
  RefreshCw,
  Terminal,
  Upload,
} from 'lucide-react'
import { chatApi } from './chatApi'
import {
  ArtifactManifest,
  ArtifactRows,
  PlotlySpec,
  WorkspaceImportResult,
  phase06Api,
} from './phase06Api'
import './artifact-browser.css'
import {
  ArtifactMediaPreview,
  mediaViewerKind,
} from './components/ArtifactMediaPreview'

type ArtifactBrowserProps = {
  workspaceId: string
  onWorkspaceImported: (result: WorkspaceImportResult) => void
  onSourcesChanged: () => Promise<void>
}

function readable(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function sizeLabel(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function safeSvgColor(value: unknown, fallback: string): string {
  if (typeof value !== 'string') return fallback
  const color = value.trim()
  return /^(#[\da-f]{3,8}|[a-z]{3,20}|rgba?\([\d\s.,%]+\))$/i.test(color)
    ? color
    : fallback
}

function ChartPreview({ spec }: { spec: PlotlySpec }) {
  const width = 720
  const height = 280
  const margin = { left: 52, right: 18, top: 30, bottom: 48 }
  const plotWidth = width - margin.left - margin.right
  const plotHeight = height - margin.top - margin.bottom
  const traces = spec.data
    .filter((trace) =>
      ['bar', 'scatter', 'line'].includes(String(trace.type ?? 'scatter')),
    )
    .slice(0, 8)
  const points = traces.flatMap((trace) => {
    const xs = Array.isArray(trace.x) ? trace.x : []
    const ys = Array.isArray(trace.y) ? trace.y : []
    return ys.slice(0, 120).map((y, index) => ({
      x: xs[index] ?? index + 1,
      y: typeof y === 'number' && Number.isFinite(y) ? y : null,
      index,
      trace,
    }))
  })
  const numeric = points.flatMap((point) => (point.y === null ? [] : [point.y]))
  if (!numeric.length) {
    return (
      <div className="artifact-empty">
        This chart has no finite values to preview.
      </div>
    )
  }
  const minY = Math.min(0, ...numeric)
  const maxY = Math.max(0, ...numeric)
  const spanY = maxY - minY || 1
  const itemCount = Math.max(
    1,
    ...traces.map((trace) => (Array.isArray(trace.y) ? trace.y.length : 0)),
  )
  const xAt = (index: number) =>
    margin.left + ((index + 0.5) / itemCount) * plotWidth
  const yAt = (value: number) =>
    margin.top + ((maxY - value) / spanY) * plotHeight
  const title = spec.layout?.title
  const titleText =
    typeof title === 'string'
      ? title
      : title && typeof title === 'object' && 'text' in title
        ? String(title.text)
        : ''
  const colors = [
    '#3b82f6',
    '#f97316',
    '#16a34a',
    '#a855f7',
    '#0891b2',
    '#e11d48',
  ]
  const firstBarWidth = Math.max(
    2,
    Math.min(36, ((plotWidth / itemCount) * 0.68) / traces.length),
  )
  return (
    <div className="chart-preview-wrap">
      <svg
        className="chart-preview"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={titleText || 'Chart preview'}
      >
        {titleText && (
          <text
            className="chart-title"
            x={width / 2}
            y="18"
            textAnchor="middle"
          >
            {titleText}
          </text>
        )}
        <line
          className="chart-axis"
          x1={margin.left}
          y1={margin.top}
          x2={margin.left}
          y2={margin.top + plotHeight}
        />
        <line
          className="chart-axis"
          x1={margin.left}
          y1={margin.top + plotHeight}
          x2={margin.left + plotWidth}
          y2={margin.top + plotHeight}
        />
        {[0, 1, 2, 3, 4].map((tick) => {
          const value = minY + (spanY * tick) / 4
          const y = yAt(value)
          return (
            <g key={tick}>
              <line
                className="chart-gridline"
                x1={margin.left}
                y1={y}
                x2={margin.left + plotWidth}
                y2={y}
              />
              <text
                className="chart-tick"
                x={margin.left - 7}
                y={y + 4}
                textAnchor="end"
              >
                {Number(value.toPrecision(4))}
              </text>
            </g>
          )
        })}
        {traces.map((trace, traceIndex) => {
          const ys = Array.isArray(trace.y) ? trace.y : []
          const type = String(trace.type ?? 'scatter')
          const mode = String(trace.mode ?? 'lines')
          const requestedColor =
            typeof trace.marker === 'object' &&
            trace.marker &&
            'color' in trace.marker
              ? trace.marker.color
              : typeof trace.line === 'object' &&
                  trace.line &&
                  'color' in trace.line
                ? trace.line.color
                : undefined
          const color = safeSvgColor(
            requestedColor,
            colors[traceIndex % colors.length],
          )
          if (type === 'bar') {
            return ys.slice(0, 120).map((raw, index) => {
              if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
              const zero = yAt(0)
              const y = yAt(raw)
              const x =
                xAt(index) +
                (traceIndex - (traces.length - 1) / 2) * firstBarWidth
              return (
                <rect
                  key={`${traceIndex}-${index}`}
                  x={x - firstBarWidth / 2}
                  y={Math.min(zero, y)}
                  width={firstBarWidth}
                  height={Math.max(1, Math.abs(zero - y))}
                  fill={color}
                />
              )
            })
          }
          const coords = ys
            .slice(0, 120)
            .flatMap((raw, index) =>
              typeof raw === 'number' && Number.isFinite(raw)
                ? [{ x: xAt(index), y: yAt(raw), index }]
                : [],
            )
          return (
            <g key={traceIndex}>
              {(mode.includes('lines') || type === 'line') &&
                coords.length > 1 && (
                  <polyline
                    points={coords
                      .map((point) => `${point.x},${point.y}`)
                      .join(' ')}
                    fill="none"
                    stroke={color}
                    strokeWidth="2"
                  />
                )}
              {(mode.includes('markers') ||
                !mode.includes('lines') ||
                type === 'line') &&
                coords.map((point) => (
                  <circle
                    key={point.index}
                    cx={point.x}
                    cy={point.y}
                    r="3.5"
                    fill={color}
                  />
                ))}
            </g>
          )
        })}
        {traces[0] &&
          Array.isArray(traces[0].x) &&
          traces[0].x.slice(0, 7).map((value, index) => (
            <text
              key={index}
              className="chart-tick"
              x={xAt(index)}
              y={height - 17}
              textAnchor="middle"
            >
              {String(value).slice(0, 12)}
            </text>
          ))}
      </svg>
      <div className="chart-preview-note">
        Preview shows at most 120 points per series. Download the full artifact
        for its complete data.
      </div>
    </div>
  )
}

type LineageItem = {
  category: string
  label: string
  raw: string
}

function parseLineageItem(entry: string): LineageItem {
  if (entry.startsWith('source:')) {
    return { category: 'Source', label: entry.slice(7), raw: entry }
  }
  if (entry.startsWith('dataset:')) {
    return { category: 'Dataset', label: entry.slice(8), raw: entry }
  }
  if (entry.startsWith('tool:')) {
    return {
      category: 'Tool Used',
      label: entry.slice(5).replace('_', ' '),
      raw: entry,
    }
  }
  if (entry.startsWith('sandbox:')) {
    return { category: 'Sandbox Session', label: entry.slice(8), raw: entry }
  }
  if (entry.startsWith('artifact:')) {
    return { category: 'Upstream Output', label: entry.slice(9), raw: entry }
  }
  if (entry.startsWith('sha256:')) {
    return {
      category: 'Content Hash',
      label: `${entry.slice(7, 19)}...`,
      raw: entry,
    }
  }
  return { category: 'Lineage', label: entry, raw: entry }
}

export function ArtifactDetail({
  artifact,
  onSourcesChanged,
}: {
  artifact: ArtifactManifest
  onSourcesChanged?: () => Promise<void>
}) {
  const [activeTab, setActiveTab] = useState<'preview' | 'sources' | 'technical'>(
    'preview',
  )
  const [copiedJson, setCopiedJson] = useState(false)
  const [preview, setPreview] = useState<{
    text: string | null
    truncated: boolean
  } | null>(null)
  const [rows, setRows] = useState<ArtifactRows | null>(null)
  const [chart, setChart] = useState<PlotlySpec | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reuseMessage, setReuseMessage] = useState('')
  const [registering, setRegistering] = useState(false)
  const [offset, setOffset] = useState(0)

  const handleCopyJson = async () => {
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(artifact.lineage, null, 2),
      )
      setCopiedJson(true)
      setTimeout(() => setCopiedJson(false), 2000)
    } catch {
      // ignore
    }
  }

  const rawLineageList: string[] = useMemo(() => {
    if (Array.isArray(artifact.lineage)) {
      return artifact.lineage.map(String)
    }
    if (artifact.lineage && typeof artifact.lineage === 'object') {
      return Object.entries(artifact.lineage).map(
        ([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`,
      )
    }
    return []
  }, [artifact.lineage])

  const parsedLineage = useMemo(
    () => rawLineageList.map(parseLineageItem),
    [rawLineageList],
  )
  const mediaType = artifact.media_type.toLowerCase().split(';')[0].trim()
  const mediaKind = mediaViewerKind(artifact)
  const isPdf = mediaType === 'application/pdf'
  const isChart =
    !mediaKind &&
    (/chart|plot/i.test(artifact.artifact_type) || mediaType.includes('plotly'))
  const isData =
    !mediaKind &&
    /csv|spreadsheet|parquet|dataset|table/i.test(
      `${artifact.artifact_type} ${mediaType}`,
    )
  const isText =
    !mediaKind &&
    !isData &&
    !isChart &&
    (mediaType.startsWith('text/') ||
      /json|markdown|notebook|report/i.test(
        `${artifact.artifact_type} ${mediaType}`,
      ))
  const isReusableCsv =
    mediaType === 'text/csv' && artifact.artifact_type === 'table'

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setRows(null)
    setChart(null)
    setPreview(null)
    setOffset(0)
    const jobs: Promise<void>[] = []
    if (isData)
      jobs.push(
        phase06Api.rows(artifact.id, 0).then((result) => {
          if (active) setRows(result)
        }),
      )
    else if (isChart)
      jobs.push(
        phase06Api.chart(artifact.id).then((result) => {
          if (active) setChart(result)
        }),
      )
    else if (isText)
      jobs.push(
        chatApi.previewArtifact(artifact.id, 16000).then((result) => {
          if (active) setPreview(result)
        }),
      )
    Promise.all(jobs)
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load this artifact.',
          )
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [artifact.id, isData, isChart, isText])

  async function loadPage(nextOffset: number) {
    setLoading(true)
    setError('')
    try {
      setRows(await phase06Api.rows(artifact.id, nextOffset))
      setOffset(nextOffset)
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not load rows.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function registerDataset() {
    setRegistering(true)
    setError('')
    setReuseMessage('')
    try {
      const result = await phase06Api.registerDataset(artifact.id)
      await onSourcesChanged?.()
      setReuseMessage(
        result.reused
          ? `Already available as ${result.display_name}.`
          : `Added ${result.display_name} as a dataset for later runs.`,
      )
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not register this output as a dataset.',
      )
    } finally {
      setRegistering(false)
    }
  }

  return (
    <article className="artifact-detail">
      <header className="artifact-detail-header">
        <div>
          <div className="artifact-kind">
            {artifact.artifact_type || artifact.media_type}
          </div>
          <h2>{artifact.display_name}</h2>
        </div>
        <div className="artifact-header-actions">
          {isReusableCsv && onSourcesChanged && (
            <button
              className="outputs-download"
              type="button"
              onClick={() => void registerDataset()}
              disabled={registering}
            >
              {registering ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <FileText size={14} />
              )}
              {registering ? 'Adding dataset' : 'Use as dataset'}
            </button>
          )}
          <a
            className="outputs-download"
            href={phase06Api.downloadUrl(artifact.id)}
          >
            <Download size={14} /> Download
          </a>
        </div>
      </header>
      {reuseMessage && <div className="artifact-success">{reuseMessage}</div>}
      <div className="artifact-nav-tabs">
        <button
          type="button"
          className={`artifact-nav-tab ${activeTab === 'preview' ? 'active' : ''}`}
          onClick={() => setActiveTab('preview')}
        >
          <Eye size={13} />
          <span>Preview</span>
        </button>
        <button
          type="button"
          className={`artifact-nav-tab ${activeTab === 'sources' ? 'active' : ''}`}
          onClick={() => setActiveTab('sources')}
        >
          <Layers size={13} />
          <span>Sources & Lineage</span>
          {parsedLineage.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted font-mono font-normal">
              {parsedLineage.length}
            </span>
          )}
        </button>
        <button
          type="button"
          className={`artifact-nav-tab ${activeTab === 'technical' ? 'active' : ''}`}
          onClick={() => setActiveTab('technical')}
        >
          <Code2 size={13} />
          <span>Technical Details</span>
        </button>
      </div>

      {activeTab === 'preview' && (
        <>
          <div className="artifact-viewer">
            {mediaKind && <ArtifactMediaPreview artifact={artifact} />}
            {isData && rows && (
              <div className="artifact-download-formats">
                <span>Download data as</span>
                {['csv', 'xlsx', 'parquet'].map((format) => (
                  <a
                    key={format}
                    href={phase06Api.downloadUrl(artifact.id, format)}
                  >
                    {format.toUpperCase()}
                  </a>
                ))}
              </div>
            )}
            {loading && (
              <div className="artifact-empty">
                <LoaderCircle className="spin" size={15} /> Loading artifact preview
              </div>
            )}
            {error && (
              <div className="artifact-error">
                <AlertCircle size={15} />
                {error}
              </div>
            )}
            {!loading && !error && isData && rows && (
              <>
                <div className="artifact-table-scroll">
                  <table className="artifact-data-table">
                    <thead>
                      <tr>
                        {rows.columns.map((column) => (
                          <th key={column.name}>
                            <span>{column.name}</span>
                            <small>{column.type}</small>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.rows.map((row, index) => (
                        <tr key={index}>
                          {rows.columns.map((column) => (
                            <td key={column.name}>{readable(row[column.name])}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="artifact-page-controls">
                  <span>
                    Rows {rows.total_rows ? offset + 1 : 0}–
                    {Math.min(offset + rows.rows.length, rows.total_rows)} of{' '}
                    {rows.total_rows}
                    {rows.truncated ? ' · result truncated' : ''}
                  </span>
                  <div>
                    <button
                      type="button"
                      onClick={() =>
                        void loadPage(Math.max(0, offset - rows.limit))
                      }
                      disabled={offset === 0 || loading}
                    >
                      <ArrowLeft size={14} /> Previous
                    </button>
                    <button
                      type="button"
                      onClick={() => void loadPage(offset + rows.limit)}
                      disabled={
                        offset + rows.rows.length >= rows.total_rows || loading
                      }
                    >
                      Next <ArrowRight size={14} />
                    </button>
                  </div>
                </div>
              </>
            )}
            {!loading && !error && isChart && chart && (
              <ChartPreview spec={chart} />
            )}
            {!loading && !error && isPdf && (
              <iframe
                className="artifact-pdf-frame"
                title={`${artifact.display_name} preview`}
                src={phase06Api.previewUrl(artifact.id)}
                sandbox=""
              />
            )}
            {!loading && !error && isText && preview && (
              <div className="artifact-report-preview">
                <pre>{preview.text ?? 'No text preview is available.'}</pre>
                {preview.truncated && (
                  <p>
                    Preview is limited to 16,000 characters. Download the full
                    report for the complete content.
                  </p>
                )}
              </div>
            )}
            {!loading &&
              !error &&
              !isData &&
              !isChart &&
              !isPdf &&
              !isText &&
              !mediaKind && (
                <div className="artifact-empty">
                  No inline preview is available for this file. Download it to
                  inspect the full content.
                </div>
              )}
          </div>
          <div className="artifact-viewer-footer">
            <span>
              Generated by run{' '}
              <code className="font-mono text-foreground font-semibold">
                {artifact.run_id.slice(0, 8)}
              </code>
            </span>
            <button
              type="button"
              className="text-xs text-primary hover:underline font-medium inline-flex items-center gap-1"
              onClick={() => setActiveTab('sources')}
            >
              <span>Inspect source lineage</span>
              <ArrowRight size={12} />
            </button>
          </div>
        </>
      )}

      {activeTab === 'sources' && (
        <div className="artifact-provenance-view">
          <div className="provenance-card">
            <h4>Lineage & Source Attribution</h4>
            <p>
              This artifact was synthesized by analysis run{' '}
              <code className="px-1.5 py-0.5 rounded bg-muted font-mono text-foreground font-semibold">
                {artifact.run_id}
              </code>
              . All computational transformations maintain strict cryptographic provenance.
            </p>
            <div className="provenance-item-list">
              {parsedLineage.length > 0 ? (
                parsedLineage.map((item, idx) => (
                  <div key={idx} className="provenance-item">
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground">
                        {item.category === 'Source' ? (
                          <FileText size={13} />
                        ) : item.category === 'Dataset' ? (
                          <Database size={13} />
                        ) : item.category === 'Tool Used' ? (
                          <Terminal size={13} />
                        ) : (
                          <Layers size={13} />
                        )}
                      </span>
                      <strong className="text-xs font-semibold text-foreground">
                        {item.category}
                      </strong>
                    </div>
                    <span className="font-mono text-xs text-muted-foreground">
                      {item.label}
                    </span>
                  </div>
                ))
              ) : (
                <div className="text-xs text-muted-foreground py-2">
                  Direct synthesized output from analysis run {artifact.run_id.slice(0, 8)}.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'technical' && (
        <dl className="artifact-metadata">
          <div>
            <dt>Format</dt>
            <dd>{artifact.media_type}</dd>
          </div>
          <div>
            <dt>Size</dt>
            <dd>{sizeLabel(artifact.byte_size)}</dd>
          </div>
          <div>
            <dt>Run</dt>
            <dd title={artifact.run_id}>{artifact.run_id.slice(0, 12)}</dd>
          </div>
          <div>
            <dt>SHA-256</dt>
            <dd className="hash-value" title={artifact.sha256}>
              {artifact.sha256}
            </dd>
          </div>
          <div className="lineage-cell">
            <div className="flex items-center justify-between mb-2">
              <dt className="m-0">Raw Lineage Payload</dt>
              <button
                type="button"
                className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded border border-border bg-card hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                onClick={handleCopyJson}
              >
                {copiedJson ? (
                  <Check size={11} className="text-status-ready" />
                ) : (
                  <Copy size={11} />
                )}
                <span>{copiedJson ? 'Copied' : 'Copy JSON'}</span>
              </button>
            </div>
            <dd>
              <pre>{JSON.stringify(artifact.lineage, null, 2)}</pre>
            </dd>
          </div>
        </dl>
      )}
    </article>
  )
}

export default function ArtifactBrowser({
  workspaceId,
  onWorkspaceImported,
  onSourcesChanged,
}: ArtifactBrowserProps) {
  const [artifacts, setArtifacts] = useState<ArtifactManifest[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const importRef = useRef<HTMLInputElement>(null)
  const selected = useMemo(
    () => artifacts.find((item) => item.id === selectedId),
    [artifacts, selectedId],
  )

  async function refresh() {
    if (!workspaceId) return
    setLoading(true)
    setError('')
    try {
      const result = await phase06Api.artifacts(workspaceId)
      setArtifacts(result)
      setSelectedId((current) =>
        result.some((item) => item.id === current)
          ? current
          : (result[0]?.id ?? ''),
      )
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not load workspace outputs.',
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setArtifacts([])
    setSelectedId('')
    void refresh()
  }, [workspaceId])

  async function handleImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await phase06Api.importWorkspace(file)
      const reconnections = result.reconnection_required.length
      const reindex = result.reindex_required.length
      setNotice(
        `Imported “${result.workspace.label}”.${reconnections ? ` Reconnect ${reconnections} database source${reconnections === 1 ? '' : 's'}.` : ''}${reindex ? ` ${reindex} document${reindex === 1 ? '' : 's'} need reindexing.` : ''}`,
      )
      onWorkspaceImported(result)
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not import this workspace.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="artifact-browser">
      <header className="outputs-header">
        <div>
          <h1>Workspace outputs</h1>
          <p>Reports, charts, and calculated data with their source lineage.</p>
        </div>
        <div className="workspace-portability">
          <a href={phase06Api.exportWorkspace(workspaceId)}>
            <FileArchive size={15} /> Export workspace
          </a>
          <button
            type="button"
            onClick={() => importRef.current?.click()}
            disabled={busy}
          >
            <Upload size={15} /> Import workspace
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => void handleImport(event)}
            hidden
          />
        </div>
      </header>
      {(error || notice) && (
        <div className={error ? 'outputs-alert error' : 'outputs-alert'}>
          {error ? <AlertCircle size={15} /> : <FileText size={15} />}
          {error || notice}
        </div>
      )}
      <div className="outputs-layout">
        <aside className="outputs-list-pane">
          <div className="outputs-list-heading">
            <strong>Artifacts</strong>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              aria-label="Refresh artifacts"
              title="Refresh artifacts"
            >
              <RefreshCw size={14} />
            </button>
          </div>
          {loading && artifacts.length === 0 ? (
            <div className="artifact-empty">
              <LoaderCircle className="spin" size={14} /> Loading artifacts
            </div>
          ) : artifacts.length === 0 ? (
            <div className="artifact-empty">
              No artifacts in this workspace yet.
            </div>
          ) : (
            artifacts.map((artifact) => (
              <button
                type="button"
                key={artifact.id}
                className={`outputs-list-item ${artifact.id === selectedId ? 'selected' : ''}`}
                onClick={() => setSelectedId(artifact.id)}
              >
                <FileText size={15} />
                <span>
                  <strong>{artifact.display_name}</strong>
                  <small>
                    {artifact.artifact_type || artifact.media_type} ·{' '}
                    {sizeLabel(artifact.byte_size)}
                  </small>
                </span>
              </button>
            ))
          )}
        </aside>
        <div className="outputs-detail-pane">
          {selected ? (
            <ArtifactDetail
              key={selected.id}
              artifact={selected}
              onSourcesChanged={onSourcesChanged}
            />
          ) : (
            <div className="artifact-empty outputs-select-empty">
              Select an artifact to inspect its preview, data, and lineage.
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
