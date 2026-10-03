import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Download,
  FileArchive,
  FileText,
  LoaderCircle,
  RefreshCw,
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

type ArtifactBrowserProps = {
  workspaceId: string
  onWorkspaceImported: (result: WorkspaceImportResult) => void
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

function ArtifactDetail({ artifact }: { artifact: ArtifactManifest }) {
  const [preview, setPreview] = useState<{
    text: string | null
    truncated: boolean
  } | null>(null)
  const [rows, setRows] = useState<ArtifactRows | null>(null)
  const [chart, setChart] = useState<PlotlySpec | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const mediaType = artifact.media_type.toLowerCase().split(';')[0].trim()
  const isPdf = mediaType === 'application/pdf'
  const isChart =
    /chart|plot/i.test(artifact.artifact_type) || mediaType.includes('plotly')
  const isData = /csv|spreadsheet|parquet|dataset|table/i.test(
    `${artifact.artifact_type} ${mediaType}`,
  )
  const isText =
    mediaType.startsWith('text/') ||
    /json|markdown|notebook|report/i.test(
      `${artifact.artifact_type} ${mediaType}`,
    )

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

  return (
    <article className="artifact-detail">
      <header className="artifact-detail-header">
        <div>
          <div className="artifact-kind">
            {artifact.artifact_type || artifact.media_type}
          </div>
          <h2>{artifact.display_name}</h2>
        </div>
        <a
          className="outputs-download"
          href={phase06Api.downloadUrl(artifact.id)}
        >
          <Download size={14} /> Download
        </a>
      </header>
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
          <dt>Lineage</dt>
          <dd>
            <pre>{JSON.stringify(artifact.lineage, null, 2)}</pre>
          </dd>
        </div>
      </dl>
      <div className="artifact-viewer">
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
        {!loading && !error && !isData && !isChart && !isPdf && !isText && (
          <div className="artifact-empty">
            No inline preview is available for this file. Download it to inspect
            the full content.
          </div>
        )}
      </div>
    </article>
  )
}

export default function ArtifactBrowser({
  workspaceId,
  onWorkspaceImported,
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
            <ArtifactDetail key={selected.id} artifact={selected} />
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
