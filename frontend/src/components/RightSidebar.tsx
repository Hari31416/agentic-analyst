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
import { Button } from './ui/button'
import { cn } from '../lib/utils'

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

function StateBadge({ state }: { state: string }) {
  const lower = state.toLowerCase()
  let colorClasses = 'bg-status-danger/10 text-status-danger'
  if (
    lower.includes('ready') ||
    lower.includes('processed') ||
    lower.includes('success')
  ) {
    colorClasses = 'bg-status-ready/10 text-status-ready'
  } else if (
    lower.includes('ingesting') ||
    lower.includes('processing') ||
    lower.includes('pending')
  ) {
    colorClasses = 'bg-status-warning/10 text-status-warning'
  }

  return (
    <span
      className={cn(
        'inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider',
        colorClasses,
      )}
    >
      {state}
    </span>
  )
}

function StatusDot({ status }: { status: string }) {
  const isOnline = status === 'ready' || status === 'online'
  const isWarning =
    status === 'configured' || status === 'degraded' || status === 'warning'
  const isChecking = status === 'checking'

  const colorClass = isOnline
    ? 'bg-status-ready ring-2 ring-status-ready/20'
    : isWarning
      ? 'bg-status-warning ring-2 ring-status-warning/20'
      : isChecking
        ? 'bg-status-checking'
        : 'bg-status-danger ring-2 ring-status-danger/20'

  return <span className={cn('w-2 h-2 rounded-full shrink-0', colorClass)} />
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
      className={cn(
        'flex flex-col shrink-0 h-screen bg-sidebar border-l border-sidebar-border transition-all duration-200 relative z-20 overflow-hidden',
        isOpen
          ? 'w-[320px] lg:w-[340px] min-w-[320px] lg:min-w-[340px] max-w-[340px] opacity-100'
          : 'w-0 min-w-0 max-w-0 border-l-transparent opacity-0 pointer-events-none',
      )}
      aria-label="Workspace Inspector"
    >
      <div className="flex flex-col w-[320px] lg:w-[340px] h-full overflow-hidden">
        <header className="flex items-center justify-between h-14 px-4 border-b border-sidebar-border shrink-0">
          <div className="flex items-center gap-2">
            <FolderOpen size={16} className="text-foreground" />
            <span className="text-[11px] font-bold tracking-[0.6px] uppercase text-foreground">
              Workspace Inspector
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="flex items-center justify-center w-8 h-8 rounded-sm text-muted-foreground border border-border bg-background transition-colors hover:text-foreground hover:border-primary"
              onClick={onToggleOpen}
              title="Close inspector"
              aria-label="Close inspector"
            >
              <PanelRightClose size={15} />
            </button>
          </div>
        </header>

        <nav
          className="flex items-center p-2 gap-1 border-b border-sidebar-border bg-sidebar shrink-0"
          aria-label="Inspector tabs"
        >
          <button
            type="button"
            className={cn(
              'flex items-center justify-center gap-1.5 flex-1 h-7.5 rounded-sm text-[11px] font-medium transition-colors',
              activeTab === 'sources'
                ? 'bg-card text-foreground font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent',
            )}
            onClick={() => setActiveTab('sources')}
          >
            <FileText size={13} />
            <span>Sources ({sources.length})</span>
          </button>
          <button
            type="button"
            className={cn(
              'flex items-center justify-center gap-1.5 flex-1 h-7.5 rounded-sm text-[11px] font-medium transition-colors',
              activeTab === 'inspect'
                ? 'bg-card text-foreground font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent',
            )}
            onClick={() => setActiveTab('inspect')}
          >
            <Eye size={13} />
            <span>Inspect</span>
          </button>
          <button
            type="button"
            className={cn(
              'flex items-center justify-center gap-1.5 flex-1 h-7.5 rounded-sm text-[11px] font-medium transition-colors',
              activeTab === 'health'
                ? 'bg-card text-foreground font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent',
            )}
            onClick={() => setActiveTab('health')}
          >
            <Activity size={13} />
            <span>Health</span>
          </button>
        </nav>

        <div className="flex-1 min-h-0 flex flex-col overflow-y-auto p-3.5 gap-3">
          {activeTab === 'sources' && (
            <>
              <div
                className={cn(
                  'border border-dashed border-border rounded-md p-3.5 bg-card text-center cursor-pointer transition-colors hover:border-primary hover:bg-accent/40',
                  isDragOver && 'border-primary bg-accent/40',
                )}
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
                  className="hidden"
                  onChange={handleFileInput}
                  disabled={isUploading}
                />
                <Upload size={20} className="text-primary mx-auto mb-1.5" />
                <div className="text-xs font-semibold text-foreground mb-0.5">
                  {isUploading
                    ? 'Uploading file...'
                    : 'Drop source document here'}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Supports PDF, CSV, Excel, JSON, Parquet, and documents
                </div>
              </div>

              {uploadError && (
                <div className="text-status-danger text-[11px] p-1.5">
                  {uploadError}
                </div>
              )}

              {sources.length > 2 && (
                <div className="relative flex items-center">
                  <Search
                    size={13}
                    className="absolute left-2.5 text-muted-foreground pointer-events-none"
                  />
                  <input
                    type="text"
                    className="w-full h-8 pl-8 pr-3 text-xs bg-background border border-border rounded-md text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                    placeholder="Search sources..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    aria-label="Search sources"
                  />
                </div>
              )}

              <div className="flex flex-col gap-1.5">
                {filteredSources.map((source) => {
                  const isSelected = source.id === selectedSourceId
                  return (
                    <div
                      key={source.id}
                      className={cn(
                        'flex items-center gap-2.5 p-2.5 rounded-md bg-card border border-border cursor-pointer transition-all hover:border-primary hover:shadow-xs',
                        isSelected && 'border-primary ring-1 ring-primary',
                      )}
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
                      <div className="flex items-center justify-center w-8 h-8 rounded-sm bg-muted text-muted-foreground shrink-0">
                        {getSourceIcon(source.kind)}
                      </div>
                      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                        <span
                          className="text-xs font-semibold text-foreground truncate"
                          title={source.display_name}
                        >
                          {source.display_name}
                        </span>
                        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                          <span>{sourceKindLabel(source.kind)}</span>
                          <span>v{source.version}</span>
                          <StateBadge state={source.state} />
                        </div>
                      </div>
                    </div>
                  )
                })}

                {sources.length === 0 && (
                  <div className="flex flex-col items-center justify-center p-6 text-center text-xs text-muted-foreground leading-relaxed">
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
                <div className="flex flex-col gap-3.5">
                  <div className="flex flex-col gap-1.5 p-3 rounded-md bg-card border border-border">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 text-xs font-semibold text-foreground truncate">
                        {getSourceIcon(selectedSource.kind)}
                        {selectedSource.display_name}
                      </span>
                      <StateBadge state={selectedSource.state} />
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-1">
                      <span>Kind: {sourceKindLabel(selectedSource.kind)}</span>
                      <span>Version: {selectedSource.version}</span>
                    </div>
                  </div>

                  {sourceDatasets.length > 0 && (
                    <div className="flex flex-col gap-1.5 p-3 rounded-md bg-card border border-border">
                      <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">
                        Structured Datasets ({sourceDatasets.length})
                      </span>
                      {sourceDatasets.map((ds) => (
                        <div
                          key={ds.id}
                          className="p-2 bg-muted rounded-sm mb-1.5 text-xs"
                        >
                          <strong className="text-foreground">
                            {identityLabel(ds.identity)}
                          </strong>
                          <div className="text-[11px] text-muted-foreground">
                            {ds.designation}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <Button
                    className="w-full justify-center gap-2"
                    onClick={() => onOpenInWorkbench(selectedSource.id)}
                  >
                    <Layers size={14} />
                    <span>Open in Source Workbench</span>
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center p-6 text-center text-xs text-muted-foreground leading-relaxed">
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
            <div className="flex flex-col gap-2">
              {systemComponents.length > 0 ? (
                systemComponents.map((comp) => {
                  const Icon = comp.icon
                  return (
                    <div
                      key={comp.name}
                      className="flex flex-col gap-1.5 p-3 rounded-md bg-card border border-border"
                    >
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-2 text-xs font-semibold text-foreground">
                          <Icon size={15} />
                          {comp.name}
                        </span>
                        <StatusDot status={comp.details.status} />
                      </div>
                      <span className="text-[11px] text-muted-foreground leading-snug">
                        {comp.details.message || comp.details.status}
                      </span>
                    </div>
                  )
                })
              ) : (
                <div className="flex flex-col gap-1.5 p-3 rounded-md bg-card border border-border">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-2 text-xs font-semibold text-foreground">
                      <Activity size={15} />
                      API Service
                    </span>
                    <StatusDot status={health} />
                  </div>
                  <span className="text-[11px] text-muted-foreground leading-snug">
                    {health === 'online'
                      ? 'Backend connected and responsive'
                      : 'Backend offline or unreachable'}
                  </span>
                </div>
              )}

              <Button
                variant="outline"
                className="w-full justify-center gap-2 mt-2"
                onClick={handleRefresh}
                disabled={isRefreshing}
              >
                <RefreshCw
                  size={13}
                  className={isRefreshing ? 'animate-spin' : ''}
                />
                <span>Test connections now</span>
              </Button>
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
