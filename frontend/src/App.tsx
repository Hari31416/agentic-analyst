import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Layers, Plus, Sparkles } from 'lucide-react'
import ChatPanel from './ChatPanel'
import SourceWorkbench from './SourceWorkbench'
import {
  LeftSidebar,
  ThreadItem,
  WorkspaceItem,
} from './components/LeftSidebar'
import { RightSidebar, SourceRecord } from './components/RightSidebar'
import { TopBar } from './components/layout/TopBar'
import { WorkspaceDialog } from './components/WorkspaceDialog'
import { ThreadDialog } from './components/ThreadDialog'
import { useTheme } from './hooks/useTheme'
import { useSystemStatus } from './hooks/useSystemStatus'
import { DatasetSummary, structuredApi } from './structuredApi'
import ArtifactBrowser from './ArtifactBrowser'
import { WorkspaceImportResult } from './phase06Api'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as Record<string, unknown>
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // Keep HTTP status when response is not JSON
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function App() {
  const [theme, toggleTheme] = useTheme()
  const { health, readiness, refreshStatus } = useSystemStatus()

  const [isLeftSidebarCollapsed, setIsLeftSidebarCollapsed] = useState<boolean>(
    () => {
      return localStorage.getItem('analyst_left_sidebar') === 'true'
    },
  )

  const [isRightSidebarOpen, setIsRightSidebarOpen] = useState<boolean>(() => {
    return localStorage.getItem('analyst_right_sidebar') !== 'false'
  })

  const [activeView, setActiveView] = useState<
    'chat' | 'workbench' | 'outputs'
  >('chat')
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [workspaceId, setWorkspaceId] = useState<string>('')
  const [threads, setThreads] = useState<ThreadItem[]>([])
  const [threadId, setThreadId] = useState<string>('')
  const [sources, setSources] = useState<SourceRecord[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [datasetErrors, setDatasetErrors] = useState<Record<string, string>>({})
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [error, setError] = useState<string>('')

  const [createWsOpen, setCreateWsOpen] = useState<boolean>(false)
  const [createThreadOpen, setCreateThreadOpen] = useState<boolean>(false)

  const toggleLeftSidebar = useCallback(() => {
    setIsLeftSidebarCollapsed((prev) => {
      const next = !prev
      localStorage.setItem('analyst_left_sidebar', String(next))
      return next
    })
  }, [])

  const toggleRightSidebar = useCallback(() => {
    setIsRightSidebarOpen((prev) => {
      const next = !prev
      localStorage.setItem('analyst_right_sidebar', String(next))
      return next
    })
  }, [])

  const activeWorkspace = useMemo(
    () => workspaces.find((w) => w.id === workspaceId) ?? null,
    [workspaces, workspaceId],
  )

  const activeThread = useMemo(
    () => threads.find((t) => t.id === threadId) ?? null,
    [threads, threadId],
  )

  const loadWorkspaces = useCallback(async () => {
    try {
      const result = await api<WorkspaceItem[]>('/api/workspaces')
      setWorkspaces(result)
      setError('')
      setWorkspaceId((current) =>
        result.some((item) => item.id === current)
          ? current
          : (result[0]?.id ?? ''),
      )
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not load workspaces.',
      )
    }
  }, [])

  const refreshSources = useCallback(async () => {
    if (!workspaceId) return
    try {
      const nextSources = await api<SourceRecord[]>(
        `/api/workspaces/${workspaceId}/sources`,
      )
      setSources(nextSources)
    } catch {
      // Ignored if network glitch
    }
  }, [workspaceId])

  useEffect(() => {
    void loadWorkspaces()
  }, [loadWorkspaces])

  useEffect(() => {
    if (!workspaceId) {
      setThreads([])
      setSources([])
      setDatasets([])
      setDatasetErrors({})
      setThreadId('')
      setSelectedSourceId(null)
      return
    }

    let active = true
    setSources([])
    setDatasets([])
    setDatasetErrors({})

    Promise.all([
      api<ThreadItem[]>(`/api/workspaces/${workspaceId}/threads`),
      api<SourceRecord[]>(`/api/workspaces/${workspaceId}/sources`),
    ])
      .then(([nextThreads, nextSources]) => {
        if (!active) return
        setThreads(nextThreads)
        setSources(nextSources)
        setThreadId((curr) =>
          nextThreads.some((item) => item.id === curr)
            ? curr
            : (nextThreads[0]?.id ?? ''),
        )
        if (nextSources.length > 0 && !selectedSourceId) {
          setSelectedSourceId(nextSources[0].id)
        }
        setError('')
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load workspace items.',
          )
        }
      })

    return () => {
      active = false
    }
  }, [workspaceId, selectedSourceId])

  // Load datasets for sources
  useEffect(() => {
    let active = true
    if (!sources.length) {
      setDatasets([])
      return
    }

    Promise.all(
      sources.map(async (source) => {
        try {
          return {
            sourceId: source.id,
            datasets: await structuredApi.datasets(source.id),
          }
        } catch (reason) {
          return {
            sourceId: source.id,
            datasets: [],
            error:
              reason instanceof Error
                ? reason.message
                : 'Could not load datasets.',
          }
        }
      }),
    ).then((allDatasets) => {
      if (active) {
        setDatasets(allDatasets.flatMap((result) => result.datasets))
        setDatasetErrors(
          Object.fromEntries(
            allDatasets.flatMap((result) =>
              result.error ? [[result.sourceId, result.error]] : [],
            ),
          ),
        )
      }
    })

    return () => {
      active = false
    }
  }, [sources])

  const handleCreateWorkspace = async (label: string) => {
    const created = await api<WorkspaceItem>('/api/workspaces', {
      method: 'POST',
      body: JSON.stringify({ label }),
    })
    setWorkspaces((prev) => [created, ...prev])
    setWorkspaceId(created.id)
    setError('')
  }

  const handleCreateThread = async (label: string) => {
    if (!workspaceId) return
    const created = await api<ThreadItem>(
      `/api/workspaces/${workspaceId}/threads`,
      {
        method: 'POST',
        body: JSON.stringify({ label }),
      },
    )
    setThreads((prev) => [created, ...prev])
    setThreadId(created.id)
    setActiveView('chat')
    setError('')
  }

  const handleDeleteThread = (id: string) => {
    setThreads((prev) => prev.filter((t) => t.id !== id))
    if (threadId === id) {
      const remaining = threads.filter((t) => t.id !== id)
      setThreadId(remaining[0]?.id ?? '')
    }
  }

  const handleUploadFile = async (file: File) => {
    if (!workspaceId) return
    const added = await structuredApi.uploadFile(workspaceId, file)
    await refreshSources()
    setSelectedSourceId(added.id)
  }

  const handleOpenInWorkbench = (sourceId: string) => {
    setSelectedSourceId(sourceId)
    setActiveView('workbench')
  }

  const handleWorkspaceImported = (result: WorkspaceImportResult) => {
    const imported: WorkspaceItem = {
      id: result.workspace.id,
      label: result.workspace.label,
      created_at: new Date().toISOString(),
    }
    setWorkspaces((current) => [
      imported,
      ...current.filter((workspace) => workspace.id !== imported.id),
    ])
    setWorkspaceId(imported.id)
    setActiveView('workbench')
    setError('')
  }

  const modelAvailable =
    readiness?.components?.model?.status === 'ready' ||
    readiness?.components?.model?.status === 'configured'
  const modelMessage = readiness?.components?.model?.message

  return (
    <div className="app-shell">
      {/* Column 1: Collapsible Left Sidebar */}
      <LeftSidebar
        workspaces={workspaces}
        activeWorkspaceId={workspaceId}
        onSelectWorkspace={setWorkspaceId}
        onCreateWorkspaceClick={() => setCreateWsOpen(true)}
        threads={threads}
        activeThreadId={threadId}
        onSelectThread={setThreadId}
        onCreateThreadClick={() => setCreateThreadOpen(true)}
        onDeleteThread={handleDeleteThread}
        activeView={activeView}
        onSelectView={setActiveView}
        isCollapsed={isLeftSidebarCollapsed}
        onToggleCollapse={toggleLeftSidebar}
        health={health}
        theme={theme}
        onToggleTheme={toggleTheme}
        sourcesCount={sources.length}
      />

      {/* Column 2: Center Main Panel */}
      <main className="main-col">
        <TopBar
          isLeftSidebarCollapsed={isLeftSidebarCollapsed}
          onToggleLeftSidebar={toggleLeftSidebar}
          isRightSidebarOpen={isRightSidebarOpen}
          onToggleRightSidebar={toggleRightSidebar}
          activeWorkspaceLabel={activeWorkspace?.label ?? 'Select Workspace'}
          activeThreadLabel={activeThread?.label}
          activeView={activeView}
          onSelectView={setActiveView}
          readiness={readiness}
          health={health}
          onRefresh={() => {
            void refreshStatus()
            void refreshSources()
          }}
          sourcesCount={sources.length}
        />

        {error && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 16px',
              backgroundColor: 'var(--status-bg-danger)',
              color: 'var(--status-danger)',
              fontSize: '12px',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <AlertCircle size={15} />
            <span>{error}</span>
          </div>
        )}

        <div className="main-content">
          {activeView === 'outputs' && workspaceId ? (
            <ArtifactBrowser
              workspaceId={workspaceId}
              onWorkspaceImported={handleWorkspaceImported}
              onSourcesChanged={refreshSources}
            />
          ) : activeView === 'chat' ? (
            threadId ? (
              <ChatPanel
                threadId={threadId}
                sources={sources}
                datasets={datasets}
                modelAvailable={modelAvailable}
                modelMessage={modelMessage}
              />
            ) : (
              <div className="center-empty-wrap">
                <div className="empty-card">
                  <div className="empty-card-icon">
                    <Sparkles size={24} />
                  </div>
                  <h2 className="empty-card-title">
                    Evidence-Backed Research Studio
                  </h2>
                  <p className="empty-card-desc">
                    {workspaceId
                      ? 'Launch a new thread or select an existing conversation to analyze documents, execute Python code, and evaluate SQL data.'
                      : 'Create or choose a research workspace to begin analyzing evidence.'}
                  </p>
                  <div className="empty-card-actions">
                    {workspaceId ? (
                      <button
                        type="button"
                        className="primary-action-btn"
                        onClick={() => setCreateThreadOpen(true)}
                      >
                        <Plus size={15} />
                        <span>Start research thread</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary-action-btn"
                        onClick={() => setCreateWsOpen(true)}
                      >
                        <Plus size={15} />
                        <span>Create workspace</span>
                      </button>
                    )}
                    <button
                      type="button"
                      className="secondary-action-btn"
                      onClick={() => setActiveView('workbench')}
                    >
                      <Layers size={15} />
                      <span>Explore sources</span>
                    </button>
                  </div>
                </div>
              </div>
            )
          ) : workspaceId ? (
            <SourceWorkbench
              workspaceId={workspaceId}
              sources={sources}
              datasets={datasets}
              datasetErrors={datasetErrors}
              onSourcesChanged={refreshSources}
            />
          ) : (
            <div className="center-empty-wrap">
              <div className="empty-card">
                <div className="empty-card-icon">
                  <Layers size={24} />
                </div>
                <h2 className="empty-card-title">Source Catalog</h2>
                <p className="empty-card-desc">
                  Select a workspace to view and manage ingested files,
                  structured datasets, and database connections.
                </p>
                <button
                  type="button"
                  className="primary-action-btn"
                  onClick={() => setCreateWsOpen(true)}
                >
                  <Plus size={15} />
                  <span>Create workspace</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Column 3: Collapsible Right Sidebar (Inspector) */}
      <RightSidebar
        isOpen={isRightSidebarOpen}
        onToggleOpen={toggleRightSidebar}
        sources={sources}
        datasets={datasets}
        selectedSourceId={selectedSourceId}
        onSelectSource={setSelectedSourceId}
        onOpenInWorkbench={handleOpenInWorkbench}
        onUploadFile={handleUploadFile}
        readiness={readiness}
        health={health}
        onRefreshStatus={refreshStatus}
      />

      {/* Modals */}
      <WorkspaceDialog
        isOpen={createWsOpen}
        onClose={() => setCreateWsOpen(false)}
        onCreate={handleCreateWorkspace}
      />

      <ThreadDialog
        isOpen={createThreadOpen}
        onClose={() => setCreateThreadOpen(false)}
        onCreate={handleCreateThread}
      />
    </div>
  )
}

export default App
