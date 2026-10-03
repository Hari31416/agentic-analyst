import { FC, useState, useMemo, ChangeEvent, DragEvent } from 'react'
import {
  Activity,
  Database,
  Eye,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Layers,
  PanelRightClose,
  RefreshCw,
  Search,
  Server,
  Upload,
} from 'lucide-react'
import { DatasetSummary, sourceKindLabel } from '../structuredApi'

export type SourceRecord = {
  id: string
  display_name: string
  kind: string
  state: string
  version: number
}

type ComponentStatus = {
  status: string
  message: string
}

export type SystemReadiness = {
  status: 'ready' | 'degraded'
  components: {
    database: ComponentStatus
    storage: ComponentStatus
    model: ComponentStatus
    sandbox: ComponentStatus
  }
}

type RightSidebarProps = {
  isOpen: boolean
  onToggleOpen: () => void
  sources: SourceRecord[]
  datasets: DatasetSummary[]
  selectedSourceId: string | null
  onSelectSource: (id: string) => void
  onOpenInWorkbench: (sourceId: string) => void
  onUploadFile: (file: File) => Promise<void>
  readiness: SystemReadiness | null
  health: 'checking' | 'online' | 'offline'
  onRefreshStatus: () => Promise<void>
}

function identityLabel(identity: DatasetSummary['identity']): string {
  if (typeof identity === 'string') return identity
  const values = Object.values(identity)
  return values.length ? values.map(String).join(' · ') : 'Dataset'
}

export const RightSidebar: FC<RightSidebarProps> = ({
  isOpen,
  onToggleOpen,
  sources,
  datasets,
  selectedSourceId,
  onSelectSource,
  onOpenInWorkbench,
  onUploadFile,
  readiness,
  health,
  onRefreshStatus,
}) => {
  const [activeTab, setActiveTab] = useState<'sources' | 'inspect' | 'health'>(
    'sources',
  )
  const [searchQuery, setSearchQuery] = useState('')
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)

  const selectedSource = useMemo(
    () => sources.find((s) => s.id === selectedSourceId) ?? null,
    [sources, selectedSourceId],
  )

  const sourceDatasets = useMemo(() => {
    if (!selectedSourceId) return []
    return datasets.filter((d) => d.source_id === selectedSourceId)
  }, [datasets, selectedSourceId])

  const filteredSources = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return sources
    return sources.filter((s) => s.display_name.toLowerCase().includes(q))
  }, [sources, searchQuery])

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragOver(true)
  }

  const handleDragLeave = () => {
    setIsDragOver(false)
  }

  const handleDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    await processUpload(file)
  }

  const handleFileInput = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    await processUpload(file)
    e.target.value = ''
  }

  const processUpload = async (file: File) => {
    setIsUploading(true)
    setUploadError('')
    try {
      await onUploadFile(file)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setIsUploading(false)
    }
  }

  const handleRefresh = async () => {
    setIsRefreshing(true)
    try {
      await onRefreshStatus()
    } finally {
      setIsRefreshing(false)
    }
  }

  const getSourceIcon = (kind: string) => {
    const lower = kind.toLowerCase()
    if (
      lower.includes('csv') ||
      lower.includes('sheet') ||
      lower.includes('table') ||
      lower.includes('json') ||
      lower.includes('parquet')
    ) {
      return <FileSpreadsheet size={16} />
    }
    if (
      lower.includes('database') ||
      lower.includes('sql') ||
      lower.includes('postgres')
    ) {
      return <Database size={16} />
    }
    return <FileText size={16} />
  }

  const getStatusBadgeClass = (state: string) => {
    const lower = state.toLowerCase()
    if (
      lower.includes('ready') ||
      lower.includes('processed') ||
      lower.includes('success')
    ) {
      return 'ready'
    }
    if (
      lower.includes('ingesting') ||
      lower.includes('processing') ||
      lower.includes('pending')
    ) {
      return 'processing'
    }
    return 'error'
  }

  const systemComponents = readiness
    ? [
        {
          name: 'Database',
          details: readiness.components.database,
          icon: Database,
        },
        {
          name: 'File Storage',
          details: readiness.components.storage,
          icon: Server,
        },
        {
          name: 'Language Model',
          details: readiness.components.model,
          icon: Activity,
        },
        {
          name: 'Code Sandbox',
          details: readiness.components.sandbox,
          icon: Layers,
        },
      ]
    : []

  return (
    <aside
      className={`inspector-col ${isOpen ? 'expanded' : 'collapsed'}`}
      aria-label="Workspace Inspector"
    >
      <div className="inspector-drawer">
        <header className="inspector-header">
          <div className="inspector-title-row">
            <FolderOpen size={16} />
            <span className="inspector-title">Workspace Inspector</span>
          </div>
          <div className="inspector-header-actions">
            <button
              type="button"
              className="icon-action-btn"
              onClick={handleRefresh}
              title="Refresh inspector"
              aria-label="Refresh inspector"
            >
              <RefreshCw
                size={14}
                className={isRefreshing ? 'animate-spin' : ''}
              />
            </button>
            <button
              type="button"
              className="icon-action-btn"
              onClick={onToggleOpen}
              title="Close inspector"
              aria-label="Close inspector"
            >
              <PanelRightClose size={15} />
            </button>
          </div>
        </header>

        <nav className="inspector-tabs-nav" aria-label="Inspector tabs">
          <button
            type="button"
            className={`inspector-tab-btn ${activeTab === 'sources' ? 'active' : ''}`}
            onClick={() => setActiveTab('sources')}
          >
            <FileText size={13} />
            <span>Sources ({sources.length})</span>
          </button>
          <button
            type="button"
            className={`inspector-tab-btn ${activeTab === 'inspect' ? 'active' : ''}`}
            onClick={() => setActiveTab('inspect')}
          >
            <Eye size={13} />
            <span>Inspect</span>
          </button>
          <button
            type="button"
            className={`inspector-tab-btn ${activeTab === 'health' ? 'active' : ''}`}
            onClick={() => setActiveTab('health')}
          >
            <Activity size={13} />
            <span>Health</span>
          </button>
        </nav>

        <div className="inspector-body">
          {activeTab === 'sources' && (
            <>
              <div
                className={`quick-upload-box ${isDragOver ? 'drag-over' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => {
                  const input = document.getElementById('quick-upload-input')
                  input?.click()
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    document.getElementById('quick-upload-input')?.click()
                  }
                }}
              >
                <input
                  id="quick-upload-input"
                  type="file"
                  accept=".csv,.xlsx,.xls,.json,.parquet,.pdf,.docx,.txt,.md,.markdown,.html,.htm,.pptx"
                  style={{ display: 'none' }}
                  onChange={handleFileInput}
                  disabled={isUploading}
                />
                <Upload size={20} className="quick-upload-icon" />
                <div className="quick-upload-title">
                  {isUploading
                    ? 'Uploading file...'
                    : 'Drop source document here'}
                </div>
                <div className="quick-upload-desc">
                  Supports PDF, CSV, Excel, JSON, Parquet, and documents
                </div>
              </div>

              {uploadError && (
                <div
                  style={{
                    color: 'var(--status-danger)',
                    fontSize: '11px',
                    padding: '6px',
                  }}
                >
                  {uploadError}
                </div>
              )}

              {sources.length > 2 && (
                <div className="threads-search-wrap" style={{ padding: 0 }}>
                  <Search
                    size={13}
                    className="threads-search-icon"
                    style={{ left: '10px' }}
                  />
                  <input
                    type="text"
                    className="threads-search-input"
                    placeholder="Search sources..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    aria-label="Search sources"
                  />
                </div>
              )}

              <div className="inspector-sources-list">
                {filteredSources.map((source) => {
                  const isSelected = source.id === selectedSourceId
                  const badgeClass = getStatusBadgeClass(source.state)
                  return (
                    <div
                      key={source.id}
                      className={`source-item-card ${isSelected ? 'selected' : ''}`}
                      onClick={() => {
                        onSelectSource(source.id)
                        setActiveTab('inspect')
                      }}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          onSelectSource(source.id)
                          setActiveTab('inspect')
                        }
                      }}
                    >
                      <div className="source-item-icon-wrap">
                        {getSourceIcon(source.kind)}
                      </div>
                      <div className="source-item-info">
                        <span
                          className="source-item-name"
                          title={source.display_name}
                        >
                          {source.display_name}
                        </span>
                        <div className="source-item-meta">
                          <span>{sourceKindLabel(source.kind)}</span>
                          <span>v{source.version}</span>
                          <span className={`state-badge ${badgeClass}`}>
                            {source.state}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })}

                {sources.length === 0 && (
                  <div className="threads-empty-state">
                    <p>No sources attached to this workspace.</p>
                    <p>Upload a document or dataset above to get started.</p>
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === 'inspect' && (
            <>
              {selectedSource ? (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '14px',
                  }}
                >
                  <div className="health-card">
                    <div className="health-card-header">
                      <span className="health-card-title">
                        {getSourceIcon(selectedSource.kind)}
                        {selectedSource.display_name}
                      </span>
                      <span
                        className={`state-badge ${getStatusBadgeClass(selectedSource.state)}`}
                      >
                        {selectedSource.state}
                      </span>
                    </div>
                    <div
                      className="source-item-meta"
                      style={{ marginTop: '4px' }}
                    >
                      <span>Kind: {sourceKindLabel(selectedSource.kind)}</span>
                      <span>Version: {selectedSource.version}</span>
                    </div>
                  </div>

                  {sourceDatasets.length > 0 && (
                    <div className="health-card">
                      <span
                        className="section-caption"
                        style={{ marginBottom: '6px' }}
                      >
                        Structured Datasets ({sourceDatasets.length})
                      </span>
                      {sourceDatasets.map((ds) => (
                        <div
                          key={ds.id}
                          style={{
                            padding: '8px',
                            background: 'var(--muted)',
                            borderRadius: 'var(--radius-sm)',
                            marginBottom: '6px',
                            fontSize: '12px',
                          }}
                        >
                          <strong>{identityLabel(ds.identity)}</strong>
                          <div
                            style={{
                              fontSize: '11px',
                              color: 'var(--muted-foreground)',
                            }}
                          >
                            {ds.designation}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <button
                    type="button"
                    className="primary-action-btn"
                    style={{ width: '100%', justifyContent: 'center' }}
                    onClick={() => onOpenInWorkbench(selectedSource.id)}
                  >
                    <Layers size={14} />
                    <span>Open in Source Workbench</span>
                  </button>
                </div>
              ) : (
                <div className="threads-empty-state">
                  <p>No source currently selected.</p>
                  <p>
                    Choose a source from the Sources tab to view its schema and
                    chunk details.
                  </p>
                </div>
              )}
            </>
          )}

          {activeTab === 'health' && (
            <div className="health-cards-list">
              {systemComponents.length > 0 ? (
                systemComponents.map((comp) => {
                  const Icon = comp.icon
                  const status = comp.details.status
                  const dotClass =
                    status === 'ready' || status === 'online'
                      ? 'ready'
                      : status === 'configured'
                        ? 'warning'
                        : 'danger'
                  return (
                    <div key={comp.name} className="health-card">
                      <div className="health-card-header">
                        <span className="health-card-title">
                          <Icon size={15} />
                          {comp.name}
                        </span>
                        <span className={`status-dot ${dotClass}`} />
                      </div>
                      <span className="health-card-msg">
                        {comp.details.message || comp.details.status}
                      </span>
                    </div>
                  )
                })
              ) : (
                <div className="health-card">
                  <div className="health-card-header">
                    <span className="health-card-title">
                      <Activity size={15} />
                      API Service
                    </span>
                    <span className={`status-dot ${health}`} />
                  </div>
                  <span className="health-card-msg">
                    {health === 'online'
                      ? 'Backend connected and responsive'
                      : 'Backend offline or unreachable'}
                  </span>
                </div>
              )}

              <button
                type="button"
                className="secondary-action-btn"
                style={{
                  width: '100%',
                  justifyContent: 'center',
                  marginTop: '8px',
                }}
                onClick={handleRefresh}
                disabled={isRefreshing}
              >
                <RefreshCw
                  size={13}
                  className={isRefreshing ? 'animate-spin' : ''}
                />
                <span>Test connections now</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
