import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { DeleteConfirmDialog } from './components/DeleteConfirmDialog'
import { Button } from './components/ui/button'
import { useTheme } from './hooks/useTheme'
import { useSystemStatus } from './hooks/useSystemStatus'
import { DatasetSummary, structuredApi } from './structuredApi'
import ArtifactBrowser from './ArtifactBrowser'
import { WorkspaceImportResult } from './phase06Api'
import { apiFetch } from './lib/apiFetch'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
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
  if (response.status === 204) return undefined as T
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
  const workspacesRef = useRef(workspaces)
  workspacesRef.current = workspaces
  const [workspaceId, setWorkspaceId] = useState<string>('')
  const workspaceIdRef = useRef(workspaceId)
  workspaceIdRef.current = workspaceId
  const [threads, setThreads] = useState<ThreadItem[]>([])
  const threadsRef = useRef(threads)
  threadsRef.current = threads
  const [threadId, setThreadId] = useState<string>('')
  const threadIdRef = useRef(threadId)
  threadIdRef.current = threadId
  const [sources, setSources] = useState<SourceRecord[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [datasetErrors, setDatasetErrors] = useState<Record<string, string>>({})
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [error, setError] = useState<string>('')

  const [createWsOpen, setCreateWsOpen] = useState<boolean>(false)
  const [createThreadOpen, setCreateThreadOpen] = useState<boolean>(false)
  const [renameTarget, setRenameTarget] = useState<{
    kind: 'workspace' | 'thread'
    id: string
    label: string
  } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{
    kind: 'workspace' | 'thread'
    id: string
    label: string
    workspaceId?: string
  } | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [deleting, setDeleting] = useState(false)

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
        `/api/workspaces/${encodeURIComponent(workspaceId)}/sources`,
      )
      if (workspaceIdRef.current === workspaceId) setSources(nextSources)
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

  const handleRename = async (label: string) => {
    if (!renameTarget) return
    const path =
      renameTarget.kind === 'workspace'
        ? `/api/workspaces/${encodeURIComponent(renameTarget.id)}`
        : `/api/threads/${encodeURIComponent(renameTarget.id)}`
    const updated = await api<WorkspaceItem | ThreadItem | void>(path, {
      method: 'PATCH',
      body: JSON.stringify({ label }),
    })
    const normalizedLabel =
      updated &&
      typeof updated === 'object' &&
      'label' in updated &&
      typeof updated.label === 'string'
        ? updated.label
        : label
    if (renameTarget.kind === 'workspace') {
      setWorkspaces((items) =>
        items.map((item) =>
          item.id === renameTarget.id
            ? { ...item, label: normalizedLabel }
            : item,
        ),
      )
    } else {
      setThreads((items) =>
        items.map((item) =>
          item.id === renameTarget.id
            ? { ...item, label: normalizedLabel }
            : item,
        ),
      )
    }
    setRenameTarget(null)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    const target = deleteTarget
    setDeleting(true)
    setDeleteError('')
    try {
      const path =
        target.kind === 'workspace'
          ? `/api/workspaces/${encodeURIComponent(target.id)}`
          : `/api/threads/${encodeURIComponent(target.id)}`
      await api<void>(path, { method: 'DELETE' })
      if (target.kind === 'workspace') {
        const remaining = workspacesRef.current.filter(
          (item) => item.id !== target.id,
        )
        workspacesRef.current = remaining
        setWorkspaces(remaining)
        if (workspaceIdRef.current === target.id) {
          const nextWorkspaceId = remaining[0]?.id ?? ''
          setWorkspaceId(nextWorkspaceId)
          workspaceIdRef.current = nextWorkspaceId
          setSelectedSourceId(null)
        }
      } else {
        if (workspaceIdRef.current === target.workspaceId) {
          const remaining = threadsRef.current.filter(
            (item) => item.id !== target.id,
          )
          threadsRef.current = remaining
          setThreads(remaining)
          if (threadIdRef.current === target.id) {
            const nextThreadId = remaining[0]?.id ?? ''
            setThreadId(nextThreadId)
            threadIdRef.current = nextThreadId
          }
        }
      }
      setDeleteTarget(null)
    } catch (reason) {
      setDeleteError(
        reason instanceof Error
          ? reason.message
          : 'Could not delete this item.',
      )
    } finally {
      setDeleting(false)
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
    <div className="flex w-screen h-screen overflow-hidden bg-background text-foreground">
      {/* Column 1: Collapsible Left Sidebar */}
      <LeftSidebar
        workspaces={workspaces}
        activeWorkspaceId={workspaceId}
        onSelectWorkspace={setWorkspaceId}
        onCreateWorkspaceClick={() => setCreateWsOpen(true)}
        onRenameWorkspace={() =>
          activeWorkspace &&
          setRenameTarget({
            kind: 'workspace',
            id: activeWorkspace.id,
            label: activeWorkspace.label,
          })
        }
        onDeleteWorkspace={() =>
          activeWorkspace &&
          (setDeleteError(''),
          setDeleteTarget({
            kind: 'workspace',
            id: activeWorkspace.id,
            label: activeWorkspace.label,
          }))
        }
        threads={threads}
        activeThreadId={threadId}
        onSelectThread={setThreadId}
        onCreateThreadClick={() => setCreateThreadOpen(true)}
        onRenameThread={(thread) =>
          setRenameTarget({
            kind: 'thread',
            id: thread.id,
            label: thread.label,
          })
        }
        onDeleteThread={(thread) => {
          setDeleteError('')
          setDeleteTarget({
            kind: 'thread',
            id: thread.id,
            label: thread.label,
            workspaceId,
          })
        }}
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
      <main className="flex-1 min-w-0 flex flex-col h-screen overflow-hidden relative bg-background">
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
          <div className="flex items-center gap-2 px-4 py-2.5 bg-status-danger/10 text-status-danger text-xs border-b border-border">
            <AlertCircle size={15} />
            <span>{error}</span>
          </div>
        )}

        <div className="flex-1 min-h-0 flex flex-col overflow-hidden relative">
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
              <div className="flex h-full flex-col items-center justify-center p-8 text-center">
                <div className="flex max-w-md flex-col items-center rounded-2xl border border-border bg-card p-8 shadow-sm">
                  <div className="mb-4 flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                    <Sparkles size={24} />
                  </div>
                  <h2 className="mb-2 text-lg font-semibold text-foreground">
                    Evidence-Backed Research Studio
                  </h2>
                  <p className="mb-6 text-sm leading-relaxed text-muted-foreground">
                    {workspaceId
                      ? 'Launch a new thread or select an existing conversation to analyze documents, execute Python code, and evaluate SQL data.'
                      : 'Create or choose a research workspace to begin analyzing evidence.'}
                  </p>
                  <div className="flex items-center gap-3">
                    {workspaceId ? (
                      <Button
                        type="button"
                        onClick={() => setCreateThreadOpen(true)}
                        className="gap-1.5"
                      >
                        <Plus size={15} />
                        <span>Start research thread</span>
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        onClick={() => setCreateWsOpen(true)}
                        className="gap-1.5"
                      >
                        <Plus size={15} />
                        <span>Create workspace</span>
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setActiveView('workbench')}
                      className="gap-1.5"
                    >
                      <Layers size={15} />
                      <span>Explore sources</span>
                    </Button>
                  </div>
                </div>
              </div>
            )
          ) : workspaceId ? (
            <SourceWorkbench
              key={workspaceId}
              workspaceId={workspaceId}
              sources={sources}
              datasets={datasets}
              datasetErrors={datasetErrors}
              onSourcesChanged={refreshSources}
              onSourceDeleted={(sourceId) => {
                setSelectedSourceId((current) =>
                  current === sourceId ? null : current,
                )
              }}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center p-8 text-center">
              <div className="flex max-w-md flex-col items-center rounded-2xl border border-border bg-card p-8 shadow-sm">
                <div className="mb-4 flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                  <Layers size={24} />
                </div>
                <h2 className="mb-2 text-lg font-semibold text-foreground">
                  Source Catalog
                </h2>
                <p className="mb-6 text-sm leading-relaxed text-muted-foreground">
                  Select a workspace to view and manage ingested files,
                  structured datasets, and database connections.
                </p>
                <Button
                  type="button"
                  onClick={() => setCreateWsOpen(true)}
                  className="gap-1.5"
                >
                  <Plus size={15} />
                  <span>Create workspace</span>
                </Button>
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

      <WorkspaceDialog
        isOpen={renameTarget?.kind === 'workspace'}
        onClose={() => setRenameTarget(null)}
        onCreate={handleRename}
        title="Rename Workspace"
        submitLabel="Save name"
        initialName={
          renameTarget?.kind === 'workspace' ? renameTarget.label : ''
        }
      />
      <ThreadDialog
        isOpen={renameTarget?.kind === 'thread'}
        onClose={() => setRenameTarget(null)}
        onCreate={handleRename}
        title="Rename Thread"
        submitLabel="Save name"
        initialName={renameTarget?.kind === 'thread' ? renameTarget.label : ''}
      />
      <DeleteConfirmDialog
        isOpen={deleteTarget !== null}
        title={`Delete ${deleteTarget?.kind ?? 'item'}?`}
        message={`“${deleteTarget?.label ?? ''}” will be permanently deleted. This action cannot be undone.`}
        onClose={() => {
          setDeleteTarget(null)
          setDeleteError('')
        }}
        onConfirm={handleDelete}
        confirming={deleting}
        error={deleteError}
      />
    </div>
  )
}

export default App
