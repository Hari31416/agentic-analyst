import { FC, useState, useMemo } from 'react'
import {
  ChevronDown,
  FolderPlus,
  Layers,
  MessageSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Sun,
  Trash2,
} from 'lucide-react'

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
  threads: ThreadItem[]
  activeThreadId: string
  onSelectThread: (id: string) => void
  onCreateThreadClick: () => void
  onDeleteThread: (id: string) => void
  activeView: 'chat' | 'workbench'
  onSelectView: (view: 'chat' | 'workbench') => void
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
  threads,
  activeThreadId,
  onSelectThread,
  onCreateThreadClick,
  onDeleteThread,
  activeView,
  onSelectView,
  isCollapsed,
  onToggleCollapse,
  health,
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
      <aside className="sidebar-col collapsed" aria-label="Sidebar navigation">
        <div className="sidebar-rail">
          <div className="rail-top">
            <button
              type="button"
              className="rail-icon-btn"
              onClick={onToggleCollapse}
              title="Expand sidebar"
              aria-label="Expand sidebar"
            >
              <PanelLeftOpen size={18} />
            </button>
            <div className="rail-divider" />
            <button
              type="button"
              className="rail-icon-btn"
              onClick={onCreateThreadClick}
              disabled={!activeWorkspaceId}
              title="New research thread"
              aria-label="New research thread"
            >
              <Plus size={18} />
            </button>
          </div>

          <div className="rail-middle">
            <button
              type="button"
              className={`rail-icon-btn ${activeView === 'chat' ? 'active' : ''}`}
              onClick={() => onSelectView('chat')}
              title="Active conversation"
              aria-label="Active conversation"
            >
              <MessageSquare size={18} />
            </button>
            <button
              type="button"
              className={`rail-icon-btn ${activeView === 'workbench' ? 'active' : ''}`}
              onClick={() => onSelectView('workbench')}
              title={`Source workbench (${sourcesCount})`}
              aria-label="Source workbench"
            >
              <Layers size={18} />
            </button>
          </div>

          <div className="rail-bottom">
            <div
              className={`status-dot ${health}`}
              title={`API status: ${health}`}
            />
            <button
              type="button"
              className="rail-icon-btn"
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
    <aside className="sidebar-col expanded" aria-label="Sidebar navigation">
      <div className="sidebar-drawer">
        <header className="sidebar-header">
          <div className="brand-section">
            <div className="brand-badge" aria-hidden="true">
              A
            </div>
            <div className="brand-text">
              <span className="brand-title">Agentic Analyst</span>
              <span className="brand-subtitle">Research Studio</span>
            </div>
          </div>
          <button
            type="button"
            className="collapse-btn"
            onClick={onToggleCollapse}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
          >
            <PanelLeftClose size={18} />
          </button>
        </header>

        <div className="workspace-selector-box">
          <div className="workspace-label-row">
            <span className="section-caption">Workspace</span>
            <button
              type="button"
              className="create-ws-inline-btn"
              onClick={onCreateWorkspaceClick}
              title="Create new workspace"
            >
              <FolderPlus size={13} />
              <span>New</span>
            </button>
          </div>
          <div className="workspace-dropdown-wrap">
            <select
              className="workspace-select"
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
            <ChevronDown size={14} className="select-chevron" />
          </div>
        </div>

        <div className="new-thread-box">
          <button
            type="button"
            className="new-thread-button"
            onClick={onCreateThreadClick}
            disabled={!activeWorkspaceId}
            title="Start a new research thread"
          >
            <Plus size={15} />
            <span>New thread</span>
          </button>
        </div>

        <section className="threads-section" aria-label="Research threads">
          <div className="threads-header">
            <span className="section-caption">Threads</span>
            <span className="threads-count-badge">{threads.length}</span>
          </div>

          {threads.length > 2 && (
            <div className="threads-search-wrap">
              <Search size={13} className="threads-search-icon" />
              <input
                type="text"
                className="threads-search-input"
                placeholder="Filter threads..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Filter threads"
              />
            </div>
          )}

          <div className="threads-list">
            {filteredThreads.map((thread) => {
              const isActive =
                thread.id === activeThreadId && activeView === 'chat'
              return (
                <div
                  key={thread.id}
                  className={`thread-item-row ${isActive ? 'active' : ''}`}
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
                  <div className="thread-item-title-group">
                    <MessageSquare size={14} className="thread-item-icon" />
                    <span className="thread-item-title" title={thread.label}>
                      {thread.label}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="thread-delete-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteThread(thread.id)
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
              <div className="threads-empty-state">
                <p>No research threads yet.</p>
                <p>Create a thread to begin an evidence-backed inquiry.</p>
              </div>
            )}
          </div>
        </section>

        <div
          className="sidebar-nav-group"
          style={{
            borderTop: '1px solid var(--sidebar-border)',
            borderBottom: 'none',
          }}
        >
          <div className="section-caption" style={{ padding: '4px 6px 6px' }}>
            Library
          </div>
          <button
            type="button"
            className={`sidebar-nav-btn ${activeView === 'workbench' ? 'active' : ''}`}
            onClick={() => onSelectView('workbench')}
            title="Open Source Workbench"
          >
            <Layers size={15} />
            <span>Source Workbench</span>
            {sourcesCount > 0 && (
              <span className="sidebar-nav-badge">{sourcesCount}</span>
            )}
          </button>
        </div>

        <footer className="sidebar-footer">
          <div className="sidebar-status-pill">
            <span className={`status-dot ${health}`} />
            <span>{health === 'online' ? 'Connected' : 'Offline'}</span>
          </div>
          <button
            type="button"
            className="theme-toggle-btn"
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
