import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, Layers, Plus } from 'lucide-react'
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
import {
  ArtifactManifest,
  WorkspaceImportResult,
  phase06Api,
} from './phase06Api'
import { apiFetch } from './lib/apiFetch'
import { formatPath, parsePath } from './lib/routing'

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
    return localStorage.getItem('analyst_right_sidebar') === 'true'
  })

  const initialRouteRef = useRef(parsePath(window.location.pathname))

  const [activeView, setActiveView] = useState<
    'chat' | 'workbench' | 'outputs'
  >(initialRouteRef.current.view)
  const activeViewRef = useRef(activeView)
  activeViewRef.current = activeView

  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const workspacesRef = useRef(workspaces)
  workspacesRef.current = workspaces
  const [workspaceId, setWorkspaceId] = useState<string>(
    initialRouteRef.current.workspaceId ?? '',
  )
  const workspaceIdRef = useRef(workspaceId)
  workspaceIdRef.current = workspaceId
  const [threads, setThreads] = useState<ThreadItem[]>([])
  const threadsRef = useRef(threads)
  threadsRef.current = threads
  const [threadId, setThreadId] = useState<string>(
    initialRouteRef.current.threadId ?? '',
  )
  const threadIdRef = useRef(threadId)
  threadIdRef.current = threadId
  const [sources, setSources] = useState<SourceRecord[]>([])
  const [artifacts, setArtifacts] = useState<ArtifactManifest[]>([])
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

  const handleSelectSource = useCallback((id: string | null) => {
    setSelectedSourceId(id)
    if (id) {
      setIsRightSidebarOpen(true)
    }
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const isInput =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable)

      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === 'b' &&
        !e.shiftKey
      ) {
        if (!isInput) {
          e.preventDefault()
          toggleLeftSidebar()
        }
      } else if (
        ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j') ||
        ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'b')
      ) {
        e.preventDefault()
        toggleRightSidebar()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [toggleLeftSidebar, toggleRightSidebar])

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
      let result = await api<WorkspaceItem[]>('/api/workspaces')
      if (result.length === 0) {
        try {
          const created = await api<WorkspaceItem>('/api/workspaces', {
            method: 'POST',
            body: JSON.stringify({ label: 'Default Workspace' }),
          })
          result = [created]
        } catch {
          // If creation fails, keep empty array
        }
      }
      setWorkspaces(result)
      setError('')
      const targetWs =
        workspaceIdRef.current || initialRouteRef.current.workspaceId || ''
      const selected = result.some((item) => item.id === targetWs)
        ? targetWs
        : (result[0]?.id ?? '')
      setWorkspaceId(selected)
      workspaceIdRef.current = selected
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not load workspaces.',
      )
    }
  }, [])

  const handleStartThread = useCallback(
    async (firstMessage: string): Promise<string> => {
      let currentWsId = workspaceIdRef.current
      if (!currentWsId) {
        const createdWs = await api<WorkspaceItem>('/api/workspaces', {
          method: 'POST',
          body: JSON.stringify({ label: 'Default Workspace' }),
        })
        setWorkspaces((prev) => [createdWs, ...prev])
        setWorkspaceId(createdWs.id)
        workspaceIdRef.current = createdWs.id
        currentWsId = createdWs.id
      }
      const title =
        firstMessage
          .trim()
          .slice(0, 36)
          .replace(/[\n\r]+/g, ' ') || 'New Conversation'
      const createdThread = await api<ThreadItem>(
        `/api/workspaces/${currentWsId}/threads`,
        {
          method: 'POST',
          body: JSON.stringify({ label: title }),
        },
      )
      setThreads((prev) => [createdThread, ...prev])
      setThreadId(createdThread.id)
      threadIdRef.current = createdThread.id
      const targetPath = formatPath({
        workspaceId: currentWsId,
        threadId: createdThread.id,
        view: 'chat',
      })
      window.history.replaceState(null, '', targetPath)
      return createdThread.id
    },
    [],
  )

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

  const refreshArtifacts = useCallback(async () => {
    if (!workspaceId) {
      setArtifacts([])
      return
    }
    try {
      const nextArtifacts = await phase06Api.artifacts(workspaceId)
      if (workspaceIdRef.current === workspaceId) setArtifacts(nextArtifacts)
    } catch {
      // Ignored if network glitch
    }
  }, [workspaceId])

  const refreshResources = useCallback(async () => {
    await Promise.all([refreshSources(), refreshArtifacts()])
  }, [refreshSources, refreshArtifacts])

  const handleSelectWorkspace = useCallback((id: string) => {
    setWorkspaceId(id)
    workspaceIdRef.current = id
    setThreadId('')
    threadIdRef.current = ''
    const targetPath = formatPath({
      workspaceId: id,
      view: activeViewRef.current,
    })
    window.history.pushState(null, '', targetPath)
  }, [])

  const handleSelectThread = useCallback((id: string) => {
    setThreadId(id)
    threadIdRef.current = id
    setActiveView('chat')
    activeViewRef.current = 'chat'
    const targetPath = formatPath({
      workspaceId: workspaceIdRef.current,
      threadId: id,
      view: 'chat',
    })
    window.history.pushState(null, '', targetPath)
  }, [])

  const handleSelectView = useCallback(
    (view: 'chat' | 'workbench' | 'outputs') => {
      setActiveView(view)
      activeViewRef.current = view
      const targetPath = formatPath({
        workspaceId: workspaceIdRef.current,
        threadId: view === 'chat' ? threadIdRef.current : undefined,
        view,
      })
      window.history.pushState(null, '', targetPath)
    },
    [],
  )

  const handleCreateThreadClick = useCallback(() => {
    setThreadId('')
    threadIdRef.current = ''
    setActiveView('chat')
    activeViewRef.current = 'chat'
    const targetPath = formatPath({
      workspaceId: workspaceIdRef.current,
      view: 'chat',
    })
    window.history.pushState(null, '', targetPath)
  }, [])

  useEffect(() => {
    const handlePopState = () => {
      const parsed = parsePath(window.location.pathname)
      if (parsed.workspaceId && parsed.workspaceId !== workspaceIdRef.current) {
        setWorkspaceId(parsed.workspaceId)
        workspaceIdRef.current = parsed.workspaceId
      }
      if (parsed.threadId !== undefined) {
        setThreadId(parsed.threadId)
        threadIdRef.current = parsed.threadId
      } else if (parsed.view !== 'chat') {
        setThreadId('')
        threadIdRef.current = ''
      }
      setActiveView(parsed.view)
      activeViewRef.current = parsed.view
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    if (!workspaceId) return
    const currentPath = formatPath({
      workspaceId,
      threadId: activeView === 'chat' && threadId ? threadId : undefined,
      view: activeView,
    })
    if (window.location.pathname === '/' || window.location.pathname === '') {
      window.history.replaceState(null, '', currentPath)
    }
  }, [workspaceId, threadId, activeView])

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
      phase06Api.artifacts(workspaceId).catch(() => []),
    ])
      .then(([nextThreads, nextSources, nextArtifacts]) => {
        if (!active) return
        setThreads(nextThreads)
        setSources(nextSources)
        setArtifacts(nextArtifacts)
        const targetThread =
          threadIdRef.current || initialRouteRef.current.threadId || ''
        const chosenThread = nextThreads.some(
          (item) => item.id === targetThread,
        )
          ? targetThread
          : (nextThreads[0]?.id ?? '')
        setThreadId(chosenThread)
        threadIdRef.current = chosenThread
        setSelectedSourceId((current) =>
          nextSources.some((source) => source.id === current)
            ? current
            : (nextSources[0]?.id ?? null),
        )
        setError('')
        initialRouteRef.current.threadId = undefined
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
  }, [workspaceId])

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
    handleSelectWorkspace(created.id)
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
    handleSelectThread(created.id)
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
    await refreshResources()
    setSelectedSourceId(added.id)
  }

  const handleOpenInWorkbench = (sourceId: string) => {
    setSelectedSourceId(sourceId)
    handleSelectView('workbench')
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
    workspaceIdRef.current = imported.id
    void refreshResources()
    handleSelectView('workbench')
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
        onSelectWorkspace={handleSelectWorkspace}
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
        onSelectThread={handleSelectThread}
        onCreateThreadClick={handleCreateThreadClick}
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
        onSelectView={handleSelectView}
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
          onSelectView={handleSelectView}
          readiness={readiness}
          health={health}
          onRefresh={() => {
            void refreshStatus()
            void refreshResources()
          }}
          sourcesCount={sources.length}
          artifactsCount={artifacts.length}
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
              onSourcesChanged={refreshResources}
            />
          ) : activeView === 'chat' ? (
            <ChatPanel
              workspaceId={workspaceId}
              threadId={threadId || null}
              sources={sources}
              datasets={datasets}
              modelAvailable={modelAvailable}
              modelMessage={modelMessage}
              onStartThread={handleStartThread}
              onSourcesChanged={refreshResources}
            />
          ) : workspaceId ? (
            <SourceWorkbench
              key={workspaceId}
              workspaceId={workspaceId}
              sources={sources}
              datasets={datasets}
              datasetErrors={datasetErrors}
              onSourcesChanged={refreshResources}
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
        onSelectSource={handleSelectSource}
        onOpenInWorkbench={handleOpenInWorkbench}
        onUploadFile={handleUploadFile}
        artifacts={artifacts}
        onRefreshArtifacts={refreshArtifacts}
        onSourcesChanged={refreshResources}
        onSelectView={handleSelectView}
        workspaceId={workspaceId}
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
