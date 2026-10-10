import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Download,
  FileText,
  Image as ImageIcon,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Trash2,
  X,
} from 'lucide-react'
import { Button } from './components/ui/button'
import { DeleteConfirmDialog } from './components/DeleteConfirmDialog'
import { usePins } from './components/PinsContext'
import { pinKindLabel } from './components/PinButton'
import {
  reportApi,
  reportAssetUrl,
  type Report,
  type ReportBlock,
  type ReportLanguage,
  type ReportProgress,
  type ReportTable,
  type ReportVersion,
} from './reportApi'
import './reports.css'

const dateLabel = (date: string) =>
  new Date(date).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export default function Reports({ workspaceId }: { workspaceId: string }) {
  const pinsState = usePins()
  const pins = pinsState?.pins ?? []
  const [reports, setReports] = useState<Report[]>([])
  const [languages, setLanguages] = useState<ReportLanguage[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<Report | null>(null)
  const [selectedVersionId, setSelectedVersionId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState('en-IN')
  const [instructions, setInstructions] = useState('')
  const [selectedPinIds, setSelectedPinIds] = useState<string[]>([])
  const [pinQuery, setPinQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [streamProgress, setStreamProgress] = useState<ReportProgress | null>(
    null,
  )
  const [streamFailed, setStreamFailed] = useState(false)
  const [mode, setMode] = useState<'wording' | 'restructure'>('wording')
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [deleteBusy, setDeleteBusy] = useState(false)
  const requestId = useRef(0)
  const detailRequestRef = useRef(0)
  const mountedRef = useRef(true)
  const selectedIdRef = useRef(selectedId)
  const pollEpochRef = useRef(0)
  const pollRequestRef = useRef(0)
  const deletedIdsRef = useRef(new Set<string>())
  selectedIdRef.current = selectedId

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestId.current += 1
    }
  }, [])

  const refreshList = useCallback(
    async (signal?: AbortSignal) => {
      const id = ++requestId.current
      setLoading(true)
      setError('')
      try {
        const [nextReports, nextLanguages] = await Promise.all([
          reportApi.list(workspaceId, signal),
          reportApi.languages(signal),
        ])
        if (!mountedRef.current || id !== requestId.current || signal?.aborted)
          return
        const availableReports = nextReports.filter(
          (report) => !deletedIdsRef.current.has(report.id),
        )
        setReports(availableReports)
        setLanguages(nextLanguages)
        setSelectedId((current) =>
          current && availableReports.some((report) => report.id === current)
            ? current
            : (availableReports[0]?.id ?? ''),
        )
        setLanguage((current) =>
          nextLanguages.some((item) => item.language === current)
            ? current
            : (nextLanguages[0]?.language ?? 'en-IN'),
        )
      } catch (reason) {
        if (mountedRef.current && !signal?.aborted && id === requestId.current)
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load reports.',
          )
      } finally {
        if (mountedRef.current && !signal?.aborted && id === requestId.current)
          setLoading(false)
      }
    },
    [workspaceId],
  )

  useEffect(() => {
    const controller = new AbortController()
    setDetail(null)
    setSelectedId('')
    void refreshList(controller.signal)
    return () => {
      controller.abort()
    }
  }, [refreshList])

  const loadDetail = useCallback(
    async (id: string, signal?: AbortSignal, terminalVersionId?: string) => {
      if (!id) {
        detailRequestRef.current += 1
        setDetail(null)
        setSelectedVersionId('')
        return null
      }
      const request = ++detailRequestRef.current
      const epoch = pollEpochRef.current
      try {
        const next = await reportApi.read(id, signal)
        if (
          !mountedRef.current ||
          signal?.aborted ||
          epoch !== pollEpochRef.current ||
          request !== detailRequestRef.current ||
          id !== selectedIdRef.current
        )
          return null
        if (terminalVersionId) {
          const terminalVersion = next.versions?.find(
            (version) => version.id === terminalVersionId,
          )
          if (
            !terminalVersion ||
            (terminalVersion.state !== 'failed' &&
              (terminalVersion.state !== 'ready' || !terminalVersion.document))
          )
            return null
        }
        setDetail(next)
        setSelectedVersionId((current) =>
          current && next.versions?.some((version) => version.id === current)
            ? current
            : (next.versions?.[0]?.id ?? ''),
        )
        setReports((items) =>
          items.map((report) =>
            report.id === id ? { ...report, ...next } : report,
          ),
        )
        setError('')
        return next
      } catch (reason) {
        if (
          mountedRef.current &&
          !signal?.aborted &&
          epoch === pollEpochRef.current &&
          request === detailRequestRef.current &&
          id === selectedIdRef.current
        )
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load this report.',
          )
        return null
      }
    },
    [],
  )

  useEffect(() => {
    const controller = new AbortController()
    detailRequestRef.current += 1
    setDetail((current) => (current?.id === selectedId ? current : null))
    setSelectedVersionId((current) =>
      detail?.id === selectedId ? current : '',
    )
    setBusy(false)
    setRenaming(false)
    setRenameValue('')
    setDeleteOpen(false)
    setDeleteBusy(false)
    setDeleteError('')
    setFeedback('')
    setStreamProgress(null)
    setStreamFailed(false)
    if (selectedId) void loadDetail(selectedId, controller.signal)
    return () => controller.abort()
  }, [selectedId, loadDetail])

  const versions = detail?.versions ?? []
  const selectedVersion =
    versions.find((version) => version.id === selectedVersionId) ?? null
  const pendingVersion = versions.find(
    (version) => version.state === 'queued' || version.state === 'generating',
  )
  const activeProgress =
    streamProgress ??
    pendingVersion?.progress ??
    selectedVersion?.progress ??
    null

  useEffect(() => {
    if (!detail || !pendingVersion) {
      setStreamProgress(null)
      setStreamFailed(false)
      return
    }
    const reportId = detail.id
    const versionId = pendingVersion.id
    let active = true
    setStreamProgress(pendingVersion.progress ?? null)
    setStreamFailed(false)
    try {
      const unsubscribe = reportApi.subscribeProgress(
        reportId,
        versionId,
        (progress) => {
          if (!active || selectedIdRef.current !== reportId) return
          setStreamProgress(progress)
          const isTerminal =
            progress.state === 'ready' || progress.state === 'failed'
          const visibleState = isTerminal ? undefined : progress.state
          setDetail((current) => {
            if (!current || current.id !== reportId) return current
            const versions = current.versions?.map((version) =>
              version.id === versionId
                ? {
                    ...version,
                    ...(visibleState ? { state: visibleState } : {}),
                    progress,
                  }
                : version,
            )
            const latestVersion =
              current.latest_version?.id === versionId
                ? {
                    ...current.latest_version,
                    ...(visibleState ? { state: visibleState } : {}),
                    progress,
                  }
                : current.latest_version
            return { ...current, versions, latest_version: latestVersion }
          })
          setReports((current) =>
            current.map((report) => {
              if (report.id !== reportId) return report
              const versions = report.versions?.map((version) =>
                version.id === versionId
                  ? {
                      ...version,
                      ...(visibleState ? { state: visibleState } : {}),
                      progress,
                    }
                  : version,
              )
              const latestVersion =
                report.latest_version?.id === versionId
                  ? {
                      ...report.latest_version,
                      ...(visibleState ? { state: visibleState } : {}),
                      progress,
                    }
                  : report.latest_version
              return { ...report, versions, latest_version: latestVersion }
            }),
          )
          if (isTerminal)
            void loadDetail(reportId, undefined, versionId).then(
              (canonical) => {
                if (active && selectedIdRef.current === reportId && !canonical)
                  setStreamFailed(true)
              },
            )
        },
        () => {
          if (active && selectedIdRef.current === reportId)
            setStreamFailed(true)
        },
        () => {
          if (active && selectedIdRef.current === reportId)
            setStreamFailed(false)
        },
      )
      return () => {
        active = false
        unsubscribe()
      }
    } catch {
      setStreamFailed(true)
      return
    }
  }, [detail?.id, pendingVersion?.id, loadDetail])

  useEffect(() => {
    const hasPendingElsewhere = reports.some(
      (report) =>
        report.id !== selectedId &&
        (report.latest_version?.state === 'queued' ||
          report.latest_version?.state === 'generating'),
    )
    const hasPending =
      hasPendingElsewhere || (Boolean(pendingVersion) && streamFailed)
    if (!hasPending) return
    const timer = globalThis.setInterval(() => {
      const epoch = pollEpochRef.current
      const request = ++pollRequestRef.current
      void reportApi
        .list(workspaceId)
        .then((next) => {
          if (
            !mountedRef.current ||
            epoch !== pollEpochRef.current ||
            request !== pollRequestRef.current
          )
            return
          const current = next.filter(
            (report) => !deletedIdsRef.current.has(report.id),
          )
          setReports(current)
          const selected = current.find(
            (report) => report.id === selectedIdRef.current,
          )
          if (selected && streamFailed && pendingVersion)
            void loadDetail(selected.id, undefined, pendingVersion.id)
        })
        .catch(() => {})
    }, 2500)
    return () => globalThis.clearInterval(timer)
  }, [
    workspaceId,
    reports,
    pendingVersion?.id,
    pendingVersion?.state,
    streamFailed,
    loadDetail,
  ])

  const visiblePins = useMemo(
    () =>
      pins
        .filter((pin) =>
          [pin.title, pin.thread_label, pin.notes, ...pin.tags, pin.kind]
            .join(' ')
            .toLowerCase()
            .includes(pinQuery.trim().toLowerCase()),
        )
        .sort((a, b) => a.title.localeCompare(b.title)),
    [pins, pinQuery],
  )
  const orderedPins = selectedPinIds
    .map((id) => pins.find((pin) => pin.id === id))
    .filter((pin): pin is NonNullable<typeof pin> => Boolean(pin))

  function togglePin(id: string) {
    setSelectedPinIds((current) => {
      if (current.includes(id)) return current.filter((pinId) => pinId !== id)
      if (current.length >= 30) return current
      return [...current, id]
    })
  }
  function movePin(index: number, delta: number) {
    setSelectedPinIds((current) => {
      const nextIndex = index + delta
      if (nextIndex < 0 || nextIndex >= current.length) return current
      const next = [...current]
      ;[next[index], next[nextIndex]] = [next[nextIndex], next[index]]
      return next
    })
  }
  function startCreate() {
    setError('')
    setCreating(true)
    setTitle('')
    setInstructions('')
    setSelectedPinIds([])
  }
  async function createReport(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const created = await reportApi.create(workspaceId, {
        title: title.trim(),
        language,
        pin_ids: selectedPinIds,
        instructions: instructions.trim(),
      })
      if (!mountedRef.current) return
      pollEpochRef.current += 1
      setCreating(false)
      setReports((current) => [
        created,
        ...current.filter((report) => report.id !== created.id),
      ])
      setSelectedId(created.id)
      selectedIdRef.current = created.id
      detailRequestRef.current += 1
      setDetail(created)
      setSelectedVersionId(
        created.versions?.[0]?.id ?? created.latest_version?.id ?? '',
      )
    } catch (reason) {
      if (!mountedRef.current) return
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not create the report.',
      )
    } finally {
      if (mountedRef.current) setBusy(false)
    }
  }
  async function regenerate(event: React.FormEvent) {
    event.preventDefault()
    if (!detail || !selectedVersion) return
    const targetId = detail.id
    setBusy(true)
    setError('')
    try {
      const next = await reportApi.regenerate(detail.id, {
        version_id: selectedVersion.id,
        feedback: feedback.trim(),
        mode,
      })
      if (!mountedRef.current || selectedIdRef.current !== targetId) return
      pollEpochRef.current += 1
      detailRequestRef.current += 1
      setDetail(next)
      setSelectedVersionId(next.versions?.[0]?.id ?? '')
      setReports((current) =>
        current.map((item) => (item.id === next.id ? next : item)),
      )
      setFeedback('')
    } catch (reason) {
      if (!mountedRef.current || selectedIdRef.current !== targetId) return
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not start a new version.',
      )
    } finally {
      if (mountedRef.current && selectedIdRef.current === targetId)
        setBusy(false)
    }
  }
  async function renameReport(event: React.FormEvent) {
    event.preventDefault()
    if (!detail || !renameValue.trim()) return
    const targetId = detail.id
    setBusy(true)
    setError('')
    try {
      const updated = await reportApi.rename(detail.id, renameValue.trim())
      if (!mountedRef.current || selectedIdRef.current !== targetId) return
      pollEpochRef.current += 1
      detailRequestRef.current += 1
      setDetail((current) => (current ? { ...current, ...updated } : updated))
      setReports((current) =>
        current.map((item) =>
          item.id === updated.id ? { ...item, ...updated } : item,
        ),
      )
      setRenaming(false)
    } catch (reason) {
      if (!mountedRef.current || selectedIdRef.current !== targetId) return
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not rename this report.',
      )
    } finally {
      if (mountedRef.current && selectedIdRef.current === targetId)
        setBusy(false)
    }
  }
  async function deleteReport() {
    if (!detail) return
    const targetId = detail.id
    setDeleteBusy(true)
    setDeleteError('')
    try {
      await reportApi.remove(detail.id)
      if (!mountedRef.current) return
      pollEpochRef.current += 1
      detailRequestRef.current += 1
      deletedIdsRef.current.add(targetId)
      const remaining = reports.filter((item) => item.id !== detail.id)
      setReports(remaining)
      if (selectedIdRef.current === targetId) {
        const nextId = remaining[0]?.id ?? ''
        selectedIdRef.current = nextId
        setSelectedId(nextId)
      }
      setDeleteOpen(false)
    } catch (reason) {
      if (!mountedRef.current || selectedIdRef.current !== targetId) return
      setDeleteError(
        reason instanceof Error
          ? reason.message
          : 'Could not delete this report.',
      )
    } finally {
      if (mountedRef.current) setDeleteBusy(false)
    }
  }
  async function download(kind: 'pdf' | 'json') {
    if (!detail || !selectedVersion) return
    try {
      const blob =
        kind === 'pdf'
          ? await reportApi.download(detail.id, selectedVersion.id)
          : await reportApi.document(detail.id, selectedVersion.id)
      saveBlob(
        blob,
        `${detail.title.replace(/[^a-z0-9-_]+/gi, '-').toLowerCase()}-v${selectedVersion.number}.${kind === 'pdf' ? 'pdf' : 'json'}`,
      )
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not download this version.',
      )
    }
  }

  return (
    <section className="reports-page" aria-label="Research reports">
      <header className="reports-header">
        <div>
          <div className="reports-kicker">Workspace library / reports</div>
          <h1>Research reports</h1>
          <p>Build versioned PDFs from the work you have pinned.</p>
        </div>
        <div className="reports-header-actions">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refreshList()}
            disabled={loading}
          >
            <RefreshCw size={14} /> Refresh
          </Button>
          <Button size="sm" onClick={startCreate} aria-label="New report">
            <Plus size={15} /> New report
          </Button>
        </div>
      </header>

      {error && (
        <div className="reports-alert" role="alert">
          {error}
        </div>
      )}
      {creating ? (
        <form
          className="report-create"
          onSubmit={(event) => void createReport(event)}
        >
          <div className="report-create-heading">
            <div>
              <span className="reports-kicker">New document</span>
              <h2>Choose the material</h2>
            </div>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label="Close report form"
              onClick={() => setCreating(false)}
            >
              <X size={16} />
            </Button>
          </div>
          <div className="report-form-grid">
            <label className="report-field">
              Report title (optional)
              <input
                maxLength={180}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Auto-generated from selected material"
              />
            </label>
            <label className="report-field">
              Report language
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                {languages.map((item) => (
                  <option key={item.language} value={item.language}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="report-pin-picker">
            <div className="report-picker-head">
              <div>
                <h3>
                  Saved material <span>{selectedPinIds.length}/30</span>
                </h3>
                <p>
                  Threads, answers, and artifacts can all be included. Artifact
                  pins work on their own.
                </p>
              </div>
              <input
                aria-label="Search saved pins"
                value={pinQuery}
                onChange={(event) => setPinQuery(event.target.value)}
                placeholder="Filter pins"
              />
            </div>
            <div className="report-picker-columns">
              <div className="report-pin-choices">
                {pinsState?.loading && (
                  <p className="report-muted" role="status">
                    Loading saved pins...
                  </p>
                )}
                {!pinsState?.loading && !visiblePins.length && (
                  <p className="report-muted">
                    No saved pins match this search.
                  </p>
                )}
                {visiblePins.map((pin) => {
                  const selected = selectedPinIds.includes(pin.id)
                  return (
                    <label
                      className={`report-pin-choice ${selected ? 'is-selected' : ''}`}
                      key={pin.id}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={!selected && selectedPinIds.length >= 30}
                        onChange={() => togglePin(pin.id)}
                      />
                      <span>
                        <small>
                          {pinKindLabel[pin.kind]} · {pin.thread_label}
                        </small>
                        <strong>{pin.title}</strong>
                        {pin.notes && <em>{pin.notes}</em>}
                      </span>
                    </label>
                  )
                })}
              </div>
              <div className="report-order-list">
                <h4>Report order</h4>
                {orderedPins.length === 0 ? (
                  <p className="report-muted">
                    Select pins to set their order.
                  </p>
                ) : (
                  orderedPins.map((pin, index) => (
                    <div className="report-order-item" key={pin.id}>
                      <span className="report-order-number">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="report-order-title">
                        {pin.title}
                        <small>{pinKindLabel[pin.kind]}</small>
                      </span>
                      <button
                        type="button"
                        aria-label={`Move ${pin.title} up`}
                        disabled={!index}
                        onClick={() => movePin(index, -1)}
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${pin.title} down`}
                        disabled={index === orderedPins.length - 1}
                        onClick={() => movePin(index, 1)}
                      >
                        <ArrowDown size={14} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Remove ${pin.title}`}
                        onClick={() => togglePin(pin.id)}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
          <label className="report-field">
            Instructions{' '}
            <span className="report-char-count">
              {instructions.length}/4000
            </span>
            <textarea
              maxLength={4000}
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="Describe the question, audience, or structure you want the report to follow."
              rows={4}
            />
          </label>
          {pinsState?.error && (
            <p className="reports-alert" role="alert">
              {pinsState.error}
            </p>
          )}
          <div className="report-form-actions">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setCreating(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={busy || !selectedPinIds.length || pinsState?.loading}
            >
              {busy ? 'Creating...' : 'Create report'}
            </Button>
          </div>
        </form>
      ) : (
        <div className="reports-workspace">
          <aside className="report-library">
            <div className="report-library-title">
              <span>Library</span>
              <span>{reports.length}</span>
            </div>
            {loading && !reports.length && (
              <p className="report-muted">Loading reports...</p>
            )}
            {!loading && !reports.length && (
              <div className="report-library-empty">
                <FileText size={24} />
                <p>Your reports will appear here.</p>
                <Button size="sm" variant="outline" onClick={startCreate}>
                  <Plus size={14} />
                  Create a report
                </Button>
              </div>
            )}
            {reports.map((report) => (
              <button
                key={report.id}
                className={`report-library-item ${selectedId === report.id ? 'is-active' : ''}`}
                onClick={() => setSelectedId(report.id)}
              >
                <FileText size={16} />
                <span>
                  <strong>{report.title}</strong>
                  <small>
                    {report.latest_version
                      ? `Version ${report.latest_version.number} · ${report.latest_version.state}`
                      : 'No version yet'}
                  </small>
                </span>
              </button>
            ))}
          </aside>
          <section className="report-detail-panel">
            {!detail ? (
              <div className="report-placeholder">
                <FileText size={28} />
                <h2>{loading ? 'Loading report' : 'Select a report'}</h2>
                <p>
                  {loading
                    ? 'Opening the latest version…'
                    : 'Choose a report from the library, or start a new one.'}
                </p>
              </div>
            ) : (
              <>
                <header className="report-detail-header">
                  <div className="report-title-wrap">
                    {renaming ? (
                      <form
                        className="report-rename"
                        onSubmit={(event) => void renameReport(event)}
                      >
                        <input
                          autoFocus
                          maxLength={180}
                          value={renameValue}
                          onChange={(event) =>
                            setRenameValue(event.target.value)
                          }
                          aria-label="Report title"
                        />
                        <Button
                          type="submit"
                          size="icon-sm"
                          disabled={busy || !renameValue.trim()}
                          aria-label="Save report title"
                        >
                          <Save size={14} />
                        </Button>
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          onClick={() => setRenaming(false)}
                          aria-label="Cancel rename"
                        >
                          <X size={14} />
                        </Button>
                      </form>
                    ) : (
                      <>
                        <h2>{detail.title}</h2>
                        <button
                          className="report-text-action"
                          onClick={() => {
                            setRenameValue(detail.title)
                            setRenaming(true)
                          }}
                        >
                          Rename
                        </button>
                      </>
                    )}
                    <p>Updated {dateLabel(detail.updated_at)}</p>
                  </div>
                  <div className="report-detail-actions">
                    <label className="report-version-select">
                      Version{' '}
                      <select
                        aria-label="Choose report version"
                        value={selectedVersionId}
                        onChange={(event) =>
                          setSelectedVersionId(event.target.value)
                        }
                      >
                        {versions.map((version) => (
                          <option key={version.id} value={version.id}>
                            v{version.number} · {version.state}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void download('json')}
                      disabled={
                        !selectedVersion || selectedVersion.state !== 'ready'
                      }
                    >
                      Document JSON
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => void download('pdf')}
                      disabled={
                        !selectedVersion || selectedVersion.state !== 'ready'
                      }
                    >
                      <Download size={14} />
                      PDF
                    </Button>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label="Delete report"
                      onClick={() => {
                        setDeleteError('')
                        setDeleteOpen(true)
                      }}
                    >
                      <Trash2 size={15} />
                    </Button>
                  </div>
                </header>
                {selectedVersion && (
                  <div className="report-version-meta">
                    <span
                      className={`report-state state-${selectedVersion.state}`}
                    >
                      <i /> {selectedVersion.state}
                    </span>
                    <span>Created {dateLabel(selectedVersion.created_at)}</span>
                    <span>{selectedVersion.language}</span>
                    {pendingVersion && (
                      <span className="report-pending">
                        <RotateCw size={13} /> Building version{' '}
                        {pendingVersion.number}
                      </span>
                    )}
                    {pendingVersion && activeProgress && (
                      <div className="report-progress-detail">
                        <div
                          className="report-progress-track"
                          role="progressbar"
                          aria-label="Report generation progress"
                          aria-valuemin={0}
                          aria-valuemax={activeProgress.total_steps || 1}
                          aria-valuenow={activeProgress.step}
                        >
                          <i
                            style={{
                              width: `${activeProgress.total_steps > 0 ? Math.max(0, Math.min(100, (activeProgress.step / activeProgress.total_steps) * 100)) : 0}%`,
                            }}
                          />
                        </div>
                        <span>
                          {activeProgress.stage.replaceAll('_', ' ')}
                          {activeProgress.message
                            ? ` · ${activeProgress.message}`
                            : ''}
                        </span>
                        <small>
                          {activeProgress.step}/{activeProgress.total_steps}
                        </small>
                      </div>
                    )}
                  </div>
                )}
                {selectedVersion?.error && (
                  <div className="reports-alert" role="alert">
                    {selectedVersion.error}
                  </div>
                )}
                {selectedVersion?.state === 'ready' &&
                selectedVersion.document ? (
                  <ReportPreview
                    reportId={detail.id}
                    version={selectedVersion}
                  />
                ) : (
                  <div className="report-placeholder report-state-placeholder">
                    <FileText size={25} />
                    <h3>
                      {selectedVersion?.state === 'failed'
                        ? 'This version failed'
                        : selectedVersion
                          ? 'Your PDF is being prepared'
                          : 'No versions yet'}
                    </h3>
                    <p>
                      {selectedVersion?.state === 'failed'
                        ? activeProgress?.message ||
                          'You can start another version below.'
                        : 'The report will appear here when it is ready.'}
                    </p>
                  </div>
                )}
                {selectedVersion && (
                  <form
                    className="report-regenerate"
                    onSubmit={(event) => void regenerate(event)}
                  >
                    <div className="report-regenerate-head">
                      <div>
                        <h3>Start a new version</h3>
                        <p>
                          Each version keeps its original pinned references.
                        </p>
                      </div>
                      <label>
                        Change type
                        <select
                          value={mode}
                          onChange={(event) =>
                            setMode(
                              event.target.value as 'wording' | 'restructure',
                            )
                          }
                        >
                          <option value="wording">Wording</option>
                          <option value="restructure">Restructure</option>
                        </select>
                      </label>
                    </div>
                    <textarea
                      maxLength={4000}
                      value={feedback}
                      onChange={(event) => setFeedback(event.target.value)}
                      placeholder="Describe the edits you want for this version."
                      rows={3}
                    />
                    <div className="report-form-actions">
                      <span>{feedback.length}/4000</span>
                      <Button
                        type="submit"
                        disabled={busy || Boolean(pendingVersion)}
                      >
                        <RotateCw size={14} />
                        {pendingVersion
                          ? 'Version in progress'
                          : busy
                            ? 'Starting...'
                            : 'Regenerate'}
                      </Button>
                    </div>
                  </form>
                )}
              </>
            )}
          </section>
        </div>
      )}
      <DeleteConfirmDialog
        isOpen={deleteOpen}
        title="Delete report"
        message={`Delete “${detail?.title ?? ''}” and its version history? This cannot be undone.`}
        onClose={() => setDeleteOpen(false)}
        onConfirm={deleteReport}
        confirming={deleteBusy}
        error={deleteError}
      />
    </section>
  )
}

function ReportPreview({
  reportId,
  version,
}: {
  reportId: string
  version: ReportVersion
}) {
  const document = version.document
  if (!document) return null
  return (
    <article className="report-preview">
      <div className="report-paper-top">
        <span>Version {version.number}</span>
        <span>{document.language || version.language}</span>
      </div>
      <h1>{document.title}</h1>
      {document.sections.map((section, index) => (
        <section key={section.id} className="report-preview-section">
          <div className="report-section-number">
            {String(index + 1).padStart(2, '0')}
          </div>
          <div className="report-section-body">
            <h2>{section.heading}</h2>
            {section.blocks.map((block) => (
              <PreviewBlock
                key={block.id}
                block={block}
                reportId={reportId}
                version={version}
              />
            ))}
          </div>
        </section>
      ))}
    </article>
  )
}

function PreviewBlock({
  block,
  reportId,
  version,
}: {
  block: ReportBlock
  reportId: string
  version: ReportVersion
}) {
  if (block.type === 'paragraph')
    return <p className="report-paragraph">{block.text}</p>
  if (block.type === 'figure') {
    const asset = version.assets?.[block.artifact_id]
    const mediaType = asset?.media_type.toLowerCase() ?? ''
    const assetUrl = reportAssetUrl(reportId, version.id, block.artifact_id)
    const isChart = mediaType.includes('plotly') || mediaType.includes('json')
    return (
      <figure className="report-figure">
        {mediaType.startsWith('image/') ? (
          <img
            src={assetUrl}
            alt={block.caption || asset?.display_name || 'Report figure'}
            loading="lazy"
          />
        ) : (
          <a className="report-retained-asset" href={assetUrl} download>
            {isChart ? <ImageIcon size={16} /> : <Download size={15} />}
            <span>
              <strong>
                {block.caption ||
                  asset?.display_name ||
                  (isChart ? 'Retained chart' : 'Retained figure')}
              </strong>
              <small>
                {isChart
                  ? 'Chart source · download original'
                  : 'Download retained artifact'}
              </small>
            </span>
            <Download size={14} />
          </a>
        )}
        <figcaption>
          {block.caption || asset?.display_name || 'Figure'}
        </figcaption>
      </figure>
    )
  }
  return (
    <ReportTablePreview block={block} reportId={reportId} version={version} />
  )
}

function ReportTablePreview({
  block,
  reportId,
  version,
}: {
  block: Extract<ReportBlock, { type: 'table' }>
  reportId: string
  version: ReportVersion
}) {
  const [table, setTable] = useState<ReportTable | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setTable(null)
    setError('')
    void reportApi
      .table(
        reportId,
        version.id,
        block.artifact_id,
        block.columns,
        Math.min(30, block.max_rows),
        controller.signal,
      )
      .then((result) => {
        if (active) setTable(result)
      })
      .catch((reason) => {
        if (active && !controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : 'Table preview unavailable.',
          )
      })
    return () => {
      active = false
      controller.abort()
    }
  }, [block.artifact_id, block.columns, block.max_rows, reportId, version.id])
  return (
    <div className="report-table-preview">
      <strong>{block.caption || 'Table'}</strong>
      {error ? (
        <p role="alert">{error}</p>
      ) : !table ? (
        <p role="status">Loading table rows…</p>
      ) : (
        <>
          <div className="report-table-scroll">
            <table>
              <thead>
                <tr>
                  {table.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((value, cellIndex) => (
                      <td key={cellIndex}>
                        {typeof value === 'string'
                          ? value
                          : JSON.stringify(value ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {table.truncated && (
            <small>Showing up to {Math.min(30, block.max_rows)} rows.</small>
          )}
        </>
      )}
    </div>
  )
}
