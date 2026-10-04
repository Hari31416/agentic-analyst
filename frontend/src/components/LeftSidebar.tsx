import { FC, useState, useMemo } from 'react'
import {
  ChevronDown,
  FolderPlus,
  Layers,
  PackageOpen,
  MessageSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Sun,
  Trash2,
  Pencil,
} from 'lucide-react'
import { cn } from '../lib/utils'

export type WorkspaceItem = {
  id: string
  label: string
  created_at: string
}

export type ThreadItem = {
  id: string
  label: string
  created_at?: string
}

type LeftSidebarProps = {
  workspaces: WorkspaceItem[]
  activeWorkspaceId: string
  onSelectWorkspace: (id: string) => void
  onCreateWorkspaceClick: () => void
  onRenameWorkspace: () => void
  onDeleteWorkspace: () => void
  threads: ThreadItem[]
  activeThreadId: string
  onSelectThread: (id: string) => void
  onCreateThreadClick: () => void
  onRenameThread: (thread: ThreadItem) => void
  onDeleteThread: (thread: ThreadItem) => void
  activeView: 'chat' | 'workbench' | 'outputs'
  onSelectView: (view: 'chat' | 'workbench' | 'outputs') => void
  isCollapsed: boolean
  onToggleCollapse: () => void
  health: 'checking' | 'online' | 'offline'
  theme: 'dark' | 'light'
  onToggleTheme: () => void
  sourcesCount: number
}

export const LeftSidebar: FC<LeftSidebarProps> = ({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onCreateWorkspaceClick,
  onRenameWorkspace,
  onDeleteWorkspace,
  threads,
  activeThreadId,
  onSelectThread,
  onCreateThreadClick,
  onRenameThread,
  onDeleteThread,
  activeView,
  onSelectView,
  isCollapsed,
  onToggleCollapse,
  health: _health,
  theme,
  onToggleTheme,
  sourcesCount,
}) => {
  const [searchQuery, setSearchQuery] = useState('')

  const filteredThreads = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return threads
    return threads.filter((t) => t.label.toLowerCase().includes(q))
  }, [threads, searchQuery])

  if (isCollapsed) {
    return (
      <aside
        className="flex flex-col h-screen bg-sidebar border-r border-sidebar-border transition-all duration-200 relative z-20 overflow-hidden shrink-0 w-[54px] min-w-[54px] max-w-[54px]"
        aria-label="Sidebar navigation"
      >
        <div className="flex flex-col items-center w-[54px] h-full py-2.5 overflow-hidden">
          <div className="flex flex-col items-center gap-2 w-full">
            <button
              type="button"
              className="flex items-center justify-center w-9 h-9 rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
              onClick={onToggleCollapse}
              title="Expand sidebar"
              aria-label="Expand sidebar"
            >
              <PanelLeftOpen size={18} />
            </button>
            <div className="w-6 h-px bg-sidebar-border my-1.5" />
            <button
              type="button"
              className="flex items-center justify-center w-9 h-9 rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground disabled:opacity-50"
              onClick={onCreateThreadClick}
              disabled={!activeWorkspaceId}
              title="New research thread"
              aria-label="New research thread"
            >
              <Plus size={18} />
            </button>
          </div>

          <div className="flex flex-col items-center gap-1.5 flex-1 w-full overflow-y-auto py-1">
            <button
              type="button"
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-md transition-colors',
                activeView === 'chat'
                  ? 'bg-card text-primary border border-border shadow-xs'
                  : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
              )}
              onClick={() => onSelectView('chat')}
              title="Active conversation"
              aria-label="Active conversation"
            >
              <MessageSquare size={18} />
            </button>
            <button
              type="button"
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-md transition-colors',
                activeView === 'workbench'
                  ? 'bg-card text-primary border border-border shadow-xs'
                  : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
              )}
              onClick={() => onSelectView('workbench')}
              title={`Source workbench (${sourcesCount})`}
              aria-label="Source workbench"
            >
              <Layers size={18} />
            </button>
            <button
              type="button"
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-md transition-colors',
                activeView === 'outputs'
                  ? 'bg-card text-primary border border-border shadow-xs'
                  : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
              )}
              onClick={() => onSelectView('outputs')}
              title="Workspace outputs"
              aria-label="Workspace outputs"
            >
              <PackageOpen size={18} />
            </button>
          </div>

          <div className="flex flex-col items-center gap-2 w-full">
            <button
              type="button"
              className="flex items-center justify-center w-9 h-9 rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
              onClick={onToggleTheme}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
          </div>
        </div>
      </aside>
    )
  }

  return (
    <aside
      className="flex flex-col h-screen bg-sidebar border-r border-sidebar-border transition-all duration-200 relative z-20 overflow-hidden shrink-0 w-[280px] min-w-[280px] max-w-[280px]"
      aria-label="Sidebar navigation"
    >
      <div className="flex flex-col w-[280px] h-full overflow-hidden">
        <header className="flex items-center justify-between h-14 px-3.5 border-b border-sidebar-border shrink-0">
          <div className="flex items-center gap-2.5 text-foreground">
            <div
              className="flex items-center justify-center w-8 h-8 rounded-md bg-gradient-to-br from-primary to-indigo-700 text-white font-bold text-[15px] tracking-tight shadow-md shadow-primary/30"
              aria-hidden="true"
            >
              A
            </div>
            <div className="flex flex-col">
              <span className="text-[13px] font-semibold tracking-tight text-foreground">
                Agentic Analyst
              </span>
              <span className="text-[10px] font-medium text-muted-foreground tracking-wider uppercase">
                Research Studio
              </span>
            </div>
          </div>
          <button
            type="button"
            className="flex items-center justify-center w-7.5 h-7.5 rounded-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
            onClick={onToggleCollapse}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
          >
            <PanelLeftClose size={18} />
          </button>
        </header>

        <div className="p-3 px-3.5 border-b border-sidebar-border shrink-0">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-[0.6px]">
              Workspace
            </span>
            <button
              type="button"
              className="flex items-center gap-1 text-[11px] font-medium text-primary rounded-sm px-1.5 py-0.5 transition-colors hover:bg-accent"
              onClick={onCreateWorkspaceClick}
              title="Create new workspace"
            >
              <FolderPlus size={13} />
              <span>New</span>
            </button>
          </div>
          <div className="relative flex items-center">
            <select
              className="w-full h-9.5 pl-3 pr-8 bg-card border border-border rounded-md text-foreground text-xs font-medium appearance-none cursor-pointer transition-colors hover:border-muted-foreground focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20"
              value={activeWorkspaceId}
              onChange={(e) => onSelectWorkspace(e.target.value)}
              aria-label="Select workspace"
            >
              {workspaces.length === 0 && (
                <option value="">No workspaces found</option>
              )}
              {workspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.label}
                </option>
              ))}
            </select>
            <ChevronDown
              size={14}
              className="absolute right-3 pointer-events-none text-muted-foreground"
            />
          </div>
          {activeWorkspaceId && (
            <div className="flex items-center justify-end gap-1.5 mt-2">
              <button
                type="button"
                className="inline-flex items-center gap-1 px-2 py-1 rounded-sm border border-border bg-card text-muted-foreground text-[11px] font-medium transition-colors hover:bg-sidebar-accent hover:text-foreground"
                onClick={onRenameWorkspace}
                aria-label="Rename workspace"
                title="Rename workspace"
              >
                <Pencil size={13} /> Rename
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1 px-2 py-1 rounded-sm border border-border bg-card text-muted-foreground text-[11px] font-medium transition-colors hover:bg-status-danger/10 hover:text-status-danger hover:border-status-danger/40"
                onClick={onDeleteWorkspace}
                aria-label="Delete workspace"
                title="Delete workspace"
              >
                <Trash2 size={13} /> Delete
              </button>
            </div>
          )}
        </div>

        <div className="px-3.5 pt-2.5 pb-1.5 shrink-0">
          <button
            type="button"
            className="flex items-center justify-center gap-2 w-full h-9 bg-card border border-border rounded-md text-foreground text-xs font-medium transition-colors shadow-xs hover:bg-sidebar-accent hover:border-primary hover:text-primary disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={onCreateThreadClick}
            disabled={!activeWorkspaceId}
            title="Start a new research thread"
          >
            <Plus size={15} />
            <span>New thread</span>
          </button>
        </div>

        <section
          className="flex flex-col flex-1 min-h-0 overflow-hidden"
          aria-label="Research threads"
        >
          <div className="flex items-center justify-between px-3.5 pt-2.5 pb-1.5 shrink-0">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-[0.6px]">
              Threads
            </span>
            <span className="text-[10px] font-semibold px-1.5 py-px rounded-full bg-card border border-border text-muted-foreground">
              {threads.length}
            </span>
          </div>

          {threads.length > 2 && (
            <div className="px-3.5 pb-2 shrink-0 relative flex items-center">
              <Search
                size={13}
                className="absolute left-5.5 text-muted-foreground pointer-events-none"
              />
              <input
                type="text"
                className="w-full h-7 pl-7 pr-2.5 bg-card border border-border rounded-sm text-[11px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary"
                placeholder="Filter threads..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Filter threads"
              />
            </div>
          )}

          <div className="flex flex-col gap-0.5 px-2.5 pb-2.5 overflow-y-auto flex-1">
            {filteredThreads.map((thread) => {
              const isActive =
                thread.id === activeThreadId && activeView === 'chat'
              return (
                <div
                  key={thread.id}
                  className={cn(
                    'group flex items-center justify-between h-8.5 pl-2.5 pr-2 rounded-md text-xs font-medium transition-colors cursor-pointer relative',
                    isActive
                      ? 'bg-card text-foreground font-semibold shadow-xs border border-border'
                      : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
                  )}
                  onClick={() => {
                    onSelectThread(thread.id)
                    onSelectView('chat')
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      onSelectThread(thread.id)
                      onSelectView('chat')
                    }
                  }}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <MessageSquare
                      size={14}
                      className={cn(
                        'shrink-0',
                        isActive ? 'text-primary' : 'text-muted-foreground',
                      )}
                    />
                    <span className="truncate" title={thread.label}>
                      {thread.label}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 items-center justify-center w-5.5 h-5.5 rounded-sm text-muted-foreground shrink-0 transition-opacity hover:bg-sidebar-accent hover:text-foreground"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRenameThread(thread)
                    }}
                    title="Rename thread"
                    aria-label={`Rename thread ${thread.label}`}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    type="button"
                    className="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 items-center justify-center w-5.5 h-5.5 rounded-sm text-muted-foreground shrink-0 transition-opacity hover:bg-status-danger/10 hover:text-status-danger"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteThread(thread)
                    }}
                    title="Delete thread"
                    aria-label={`Delete thread ${thread.label}`}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              )
            })}

            {threads.length === 0 && (
              <div className="flex flex-col items-center justify-center py-6 px-4 text-center text-muted-foreground text-[11px] leading-relaxed">
                <p>No research threads yet.</p>
                <p>Create a thread to begin an evidence-backed inquiry.</p>
              </div>
            )}
          </div>
        </section>

        <div className="p-2 px-2.5 flex flex-col gap-0.5 border-t border-sidebar-border shrink-0">
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-[0.6px] px-1.5 py-1">
            Library
          </div>
          <button
            type="button"
            className={cn(
              'flex items-center gap-2.5 w-full h-8 px-2.5 rounded-md text-xs font-medium transition-colors',
              activeView === 'workbench'
                ? 'bg-card text-primary font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
            )}
            onClick={() => onSelectView('workbench')}
            title="Open Source Workbench"
          >
            <Layers size={15} />
            <span>Source Workbench</span>
            {sourcesCount > 0 && (
              <span className="ml-auto text-[10px] font-semibold px-1.5 py-px rounded-full bg-muted text-muted-foreground">
                {sourcesCount}
              </span>
            )}
          </button>
          <button
            type="button"
            className={cn(
              'flex items-center gap-2.5 w-full h-8 px-2.5 rounded-md text-xs font-medium transition-colors',
              activeView === 'outputs'
                ? 'bg-card text-primary font-semibold shadow-xs border border-border'
                : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
            )}
            onClick={() => onSelectView('outputs')}
            title="Open workspace outputs"
          >
            <PackageOpen size={15} />
            <span>Outputs</span>
          </button>
        </div>

        <footer className="flex items-center justify-between h-12 px-3.5 border-t border-sidebar-border shrink-0 bg-sidebar">
          <button
            type="button"
            className="flex items-center justify-center w-7 h-7 rounded-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
            onClick={onToggleTheme}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            aria-label="Toggle theme"
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </footer>
      </div>
    </aside>
  )
}
