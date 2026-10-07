import {
  ChangeEvent,
  DragEvent,
  FC,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react'
import {
  ArrowLeft,
  BarChart3,
  Database,
  Download,
  ExternalLink,
  Eye,
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Layers,
  PackageOpen,
  PanelRightClose,
  RefreshCw,
  Search,
  Upload,
} from 'lucide-react'
import { DatasetSummary, sourceKindLabel } from '../structuredApi'
import { ArtifactManifest, phase06Api } from '../phase06Api'
import { ArtifactDetail } from '../ArtifactBrowser'
import { Button } from './ui/button'
import { cn } from '../lib/utils'
import { filterVisibleArtifacts } from '../lib/artifactVisibility'

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
  artifactScopeKey?: string
  artifacts?: ArtifactManifest[]
  onRefreshArtifacts?: () => Promise<void>
  onSourcesChanged?: () => Promise<void>
  onSelectView?: (view: 'chat' | 'workbench' | 'outputs') => void
  workspaceId?: string
  readiness?: SystemReadiness | null
  health?: 'checking' | 'online' | 'offline'
  onRefreshStatus?: () => Promise<void>
}

function identityLabel(identity: DatasetSummary['identity']): string {
  if (typeof identity === 'string') return identity
  const values = Object.values(identity)
  return values.length ? values.map(String).join(' · ') : 'Dataset'
}

function sizeLabel(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
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

function getArtifactIcon(artifact: ArtifactManifest) {
  const mediaType = (artifact.media_type || '').toLowerCase()
  const artifactType = (artifact.artifact_type || '').toLowerCase()
  if (
    mediaType.startsWith('image/') ||
    /png|jpg|jpeg|gif/i.test(artifactType)
  ) {
    return <FileImage size={15} />
  }
  if (artifactType === 'chart' || /chart|plot|plotly/i.test(mediaType)) {
    return <BarChart3 size={15} />
  }
  if (
    artifactType === 'table' ||
    /csv|spreadsheet|excel|parquet/i.test(mediaType)
  ) {
    return <FileSpreadsheet size={15} />
  }
  if (
    /python|code|notebook|json/i.test(mediaType) ||
    /code|notebook/i.test(artifactType)
  ) {
    return <FileCode2 size={15} />
  }
  if (
    /report|markdown|text/i.test(artifactType) ||
    mediaType.startsWith('text/')
  ) {
    return <FileText size={15} />
  }
  return <File size={15} />
}

function getArtifactBadge(artifact: ArtifactManifest): string {
  if (artifact.artifact_type) return artifact.artifact_type.toUpperCase()
  const media = (artifact.media_type || '').split('/')[1] || 'FILE'
  return media.toUpperCase()
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
  artifacts = [],
  artifactScopeKey,
  onRefreshArtifacts,
  onSourcesChanged,
  onSelectView,
}) => {
  const [activeTab, setActiveTab] = useState<'sources' | 'artifacts'>('sources')
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    null,
  )
  const [searchQuery, setSearchQuery] = useState('')
  const [artifactSearchQuery, setArtifactSearchQuery] = useState('')
  const [showIntermediate, setShowIntermediate] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [isRefreshingArtifacts, setIsRefreshingArtifacts] = useState(false)
  const [width, setWidth] = useState(360)
  const [isResizing, setIsResizing] = useState(false)

  useEffect(() => {
    setSelectedArtifactId(null)
    setArtifactSearchQuery('')
    setShowIntermediate(false)
  }, [artifactScopeKey])

  const selectedArtifact = useMemo(
    () => artifacts.find((a) => a.id === selectedArtifactId) ?? null,
    [artifacts, selectedArtifactId],
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

  const filteredArtifacts = useMemo(
    () =>
      filterVisibleArtifacts(artifacts, showIntermediate, artifactSearchQuery),
    [artifacts, showIntermediate, artifactSearchQuery],
  )
  const visibleArtifactCount = useMemo(
    () => filterVisibleArtifacts(artifacts, showIntermediate).length,
    [artifacts, showIntermediate],
  )
  const hiddenArtifactCount =
    artifacts.length - filterVisibleArtifacts(artifacts, false).length

  const startResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsResizing(true)
  }, [])

  const stopResizing = useCallback(() => {
    setIsResizing(false)
  }, [])

  const resize = useCallback(
    (mouseMoveEvent: MouseEvent) => {
      if (isResizing) {
        const newWidth = document.body.clientWidth - mouseMoveEvent.clientX
        if (
          newWidth >= 300 &&
          newWidth <= Math.min(800, document.body.clientWidth * 0.8)
        ) {
          setWidth(newWidth)
        }
      }
    },
    [isResizing],
  )

  useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', resize)
      window.addEventListener('mouseup', stopResizing)
    }
    return () => {
      window.removeEventListener('mousemove', resize)
      window.removeEventListener('mouseup', stopResizing)
    }
  }, [isResizing, resize, stopResizing])

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

  const handleRefreshArtifacts = async () => {
    if (!onRefreshArtifacts) return
    setIsRefreshingArtifacts(true)
    try {
      await onRefreshArtifacts()
    } finally {
      setIsRefreshingArtifacts(false)
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
    if (
      lower.includes('image') ||
      lower.includes('png') ||
      lower.includes('jpg') ||
      lower.includes('jpeg') ||
      lower.includes('webp')
    ) {
      return <FileImage size={16} />
    }
    return <FileText size={16} />
  }

  return (
    <aside
      className={cn(
        'flex flex-col shrink-0 h-screen bg-sidebar border-l border-sidebar-border relative z-20 overflow-hidden',
        isResizing ? 'select-none' : 'transition-[width] duration-200',
        isOpen
          ? 'opacity-100'
          : 'w-0 min-w-0 max-w-0 border-l-transparent opacity-0 pointer-events-none',
      )}
      style={{ width: isOpen ? width : 0 }}
      aria-label="Workspace Inspector"
    >
      {/* Drag resize handle */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 cursor-ew-resize hover:bg-primary/40 active:bg-primary/60 z-50 transition-colors"
        onMouseDown={startResizing}
        title="Drag to resize inspector"
      />

      <div
        className="flex flex-col h-full overflow-hidden"
        style={{ width: `${width}px` }}
      >
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
            onClick={() => {
              setActiveTab('sources')
              setSelectedArtifactId(null)
            }}
          >
            <FileText size={13} />
            <span>Sources ({sources.length})</span>
          </button>
          <button
            type="button"
            className={cn(
              'flex items-center justify-center gap-1.5 flex-1 h-7.5 rounded-sm text-[11px] font-medium transition-colors',
              activeTab === 'artifacts'
                ? 'bg-card text-foreground font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:text-foreground hover:bg-sidebar-accent',
            )}
            onClick={() => setActiveTab('artifacts')}
          >
            <Layers size={13} />
            <span>
              Artifacts ({filterVisibleArtifacts(artifacts, false).length})
            </span>
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
                  accept=".csv,.xlsx,.xls,.json,.parquet,.pdf,.docx,.txt,.md,.markdown,.html,.htm,.pptx,.png,.jpg,.jpeg,.webp,image/*"
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
                  Supports PDF, CSV, Excel, JSON, Parquet, documents, and images
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
                        'flex flex-col gap-2 p-2.5 rounded-md bg-card border border-border cursor-pointer transition-all hover:border-primary hover:shadow-xs',
                        isSelected && 'border-primary ring-1 ring-primary',
                      )}
                      onClick={() =>
                        onSelectSource(isSelected ? '' : source.id)
                      }
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          onSelectSource(isSelected ? '' : source.id)
                        }
                      }}
                    >
                      <div className="flex items-center gap-2.5">
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
                        <button
                          type="button"
                          className="flex items-center justify-center size-7 rounded text-muted-foreground hover:text-foreground hover:bg-muted shrink-0"
                          onClick={(e) => {
                            e.stopPropagation()
                            onOpenInWorkbench(source.id)
                          }}
                          title="Open in Source Workbench"
                        >
                          <Layers size={14} />
                        </button>
                      </div>

                      {isSelected && (
                        <div className="flex flex-col gap-2 pt-2 border-t border-border/60">
                          {sourceDatasets.length > 0 && (
                            <div className="flex flex-col gap-1">
                              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                                Structured Datasets ({sourceDatasets.length})
                              </span>
                              {sourceDatasets.map((ds) => (
                                <div
                                  key={ds.id}
                                  className="p-1.5 bg-muted rounded-sm text-[11px]"
                                >
                                  <strong className="text-foreground">
                                    {identityLabel(ds.identity)}
                                  </strong>
                                  <div className="text-[10px] text-muted-foreground">
                                    {ds.designation}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                          <Button
                            size="sm"
                            className="w-full justify-center gap-1.5 h-7 text-xs"
                            onClick={(e) => {
                              e.stopPropagation()
                              onOpenInWorkbench(source.id)
                            }}
                          >
                            <Layers size={13} />
                            <span>Open in Source Workbench</span>
                          </Button>
                        </div>
                      )}
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

          {activeTab === 'artifacts' && (
            <>
              {selectedArtifact ? (
                <div className="flex flex-col h-full gap-2">
                  <div className="flex items-center justify-between pb-2 border-b border-sidebar-border shrink-0">
                    <button
                      type="button"
                      className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() => setSelectedArtifactId(null)}
                      title="Back to artifacts list"
                    >
                      <ArrowLeft size={14} />
                      <span>Back to list</span>
                    </button>
                    <div className="flex items-center gap-1">
                      <a
                        href={phase06Api.downloadUrl(selectedArtifact.id)}
                        download={selectedArtifact.display_name}
                        className="flex items-center justify-center size-7 rounded-sm text-muted-foreground border border-border bg-background transition-colors hover:text-foreground hover:border-primary"
                        title="Download artifact"
                      >
                        <Download size={13} />
                      </a>
                      {onSelectView && (
                        <button
                          type="button"
                          className="flex items-center justify-center size-7 rounded-sm text-muted-foreground border border-border bg-background transition-colors hover:text-foreground hover:border-primary"
                          onClick={() => onSelectView('outputs')}
                          title="Open full view in Workspace Outputs"
                        >
                          <ExternalLink size={13} />
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="flex-1 min-h-0 overflow-y-auto sidebar-artifact-detail-wrap">
                    <ArtifactDetail
                      key={selectedArtifact.id}
                      artifact={selectedArtifact}
                      onSourcesChanged={onSourcesChanged}
                    />
                  </div>
                </div>
              ) : (
                <>
                  {(visibleArtifactCount > 2 ||
                    artifactSearchQuery.length > 0) && (
                    <div className="relative flex items-center">
                      <Search
                        size={13}
                        className="absolute left-2.5 text-muted-foreground pointer-events-none"
                      />
                      <input
                        type="text"
                        className="w-full h-8 pl-8 pr-3 text-xs bg-background border border-border rounded-md text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                        placeholder="Search artifacts..."
                        value={artifactSearchQuery}
                        onChange={(e) => setArtifactSearchQuery(e.target.value)}
                        aria-label="Search artifacts"
                      />
                    </div>
                  )}

                  <label className="flex items-center gap-2 text-[11px] text-muted-foreground px-0.5">
                    <input
                      type="checkbox"
                      checked={showIntermediate}
                      onChange={(event) =>
                        setShowIntermediate(event.target.checked)
                      }
                    />
                    Show intermediate files
                  </label>
                  {!showIntermediate && hiddenArtifactCount > 0 && (
                    <p className="px-0.5 text-[10px] text-muted-foreground">
                      {hiddenArtifactCount} intermediate{' '}
                      {hiddenArtifactCount === 1 ? 'file is' : 'files are'}{' '}
                      hidden.
                    </p>
                  )}
                  <div className="flex items-center justify-between text-xs text-muted-foreground px-0.5">
                    <span className="font-semibold text-[10px] uppercase tracking-wider text-muted-foreground">
                      {showIntermediate ? 'All retained files' : 'Chat outputs'}{' '}
                      ({filteredArtifacts.length})
                    </span>
                    {onRefreshArtifacts && (
                      <button
                        type="button"
                        onClick={handleRefreshArtifacts}
                        disabled={isRefreshingArtifacts}
                        className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
                        title="Refresh artifacts"
                      >
                        <RefreshCw
                          size={12}
                          className={
                            isRefreshingArtifacts ? 'animate-spin' : ''
                          }
                        />
                      </button>
                    )}
                  </div>

                  <div className="flex flex-col gap-1.5">
                    {filteredArtifacts.map((artifact) => (
                      <div
                        key={artifact.id}
                        className="flex flex-col gap-1.5 p-2.5 rounded-md bg-card border border-border cursor-pointer transition-all hover:border-primary hover:shadow-xs group"
                        onClick={() => setSelectedArtifactId(artifact.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            setSelectedArtifactId(artifact.id)
                          }
                        }}
                      >
                        <div className="flex items-start gap-2.5">
                          <div className="flex items-center justify-center w-8 h-8 rounded-sm bg-muted text-muted-foreground shrink-0 mt-0.5 group-hover:text-primary transition-colors">
                            {getArtifactIcon(artifact)}
                          </div>
                          <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                            <div className="flex items-center justify-between gap-1.5">
                              <span
                                className="text-xs font-semibold text-foreground truncate group-hover:text-primary transition-colors"
                                title={artifact.display_name}
                              >
                                {artifact.display_name}
                              </span>
                              <div className="flex items-center gap-1 opacity-70 group-hover:opacity-100 transition-opacity shrink-0">
                                <a
                                  href={phase06Api.downloadUrl(artifact.id)}
                                  download={artifact.display_name}
                                  onClick={(e) => e.stopPropagation()}
                                  className="flex items-center justify-center size-6 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                                  title="Download artifact"
                                >
                                  <Download size={13} />
                                </a>
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setSelectedArtifactId(artifact.id)
                                  }}
                                  className="flex items-center justify-center size-6 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                                  title="View details"
                                >
                                  <Eye size={13} />
                                </button>
                              </div>
                            </div>
                            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                              <span className="inline-flex items-center px-1.5 py-0.2 rounded bg-muted/80 font-mono text-[9px] font-semibold text-muted-foreground uppercase">
                                {getArtifactBadge(artifact)}
                              </span>
                              <span>{sizeLabel(artifact.byte_size)}</span>
                              {artifact.sha256 && (
                                <span className="font-mono text-[9px]">
                                  • {artifact.sha256.slice(0, 8)}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}

                    {filteredArtifacts.length === 0 && (
                      <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-muted-foreground leading-relaxed">
                        <PackageOpen
                          size={30}
                          className="text-muted-foreground/40 mb-2.5"
                        />
                        <p className="font-semibold text-foreground">
                          {visibleArtifactCount === 0
                            ? 'No outputs yet'
                            : 'No matching outputs'}
                        </p>
                        <p className="text-[11px] mt-1 text-muted-foreground">
                          {visibleArtifactCount === 0
                            ? 'Reports, charts, and tables produced by research runs will appear here.'
                            : 'Try a different search, or show intermediate files.'}
                        </p>
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </aside>
  )
}
