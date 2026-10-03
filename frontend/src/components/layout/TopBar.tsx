import { FC } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
} from 'lucide-react'
import { SystemReadiness } from '../RightSidebar'

type TopBarProps = {
  isLeftSidebarCollapsed: boolean
  onToggleLeftSidebar: () => void
  isRightSidebarOpen: boolean
  onToggleRightSidebar: () => void
  activeWorkspaceLabel: string
  activeThreadLabel?: string
  activeView: 'chat' | 'workbench' | 'outputs'
  onSelectView: (view: 'chat' | 'workbench' | 'outputs') => void
  readiness: SystemReadiness | null
  health: 'checking' | 'online' | 'offline'
  onRefresh: () => void
  sourcesCount: number
}

export const TopBar: FC<TopBarProps> = ({
  isLeftSidebarCollapsed,
  onToggleLeftSidebar,
  isRightSidebarOpen,
  onToggleRightSidebar,
  activeWorkspaceLabel,
  activeThreadLabel,
  activeView,
  onSelectView,
  readiness,
  health,
  onRefresh,
  sourcesCount,
}) => {
  return (
    <header className="main-topbar">
      <div className="topbar-left">
        {isLeftSidebarCollapsed && (
          <button
            type="button"
            className="topbar-toggle-btn"
            onClick={onToggleLeftSidebar}
            title="Expand navigation sidebar"
            aria-label="Expand navigation sidebar"
          >
            <PanelLeftOpen size={16} />
          </button>
        )}

        {activeView !== 'chat' && (
          <button
            type="button"
            className="topbar-toggle-btn"
            onClick={() => onSelectView('chat')}
            title="Return to chat"
            aria-label="Return to chat"
          >
            <ArrowLeft size={16} />
          </button>
        )}

        <div className="breadcrumbs">
          <span className="breadcrumb-root">{activeWorkspaceLabel}</span>
          <ChevronRight size={13} className="breadcrumb-separator" />
          <strong className="breadcrumb-current">
            {activeView === 'chat'
              ? (activeThreadLabel ?? 'Research Conversation')
              : activeView === 'workbench'
                ? 'Source Workbench'
                : 'Workspace Outputs'}
          </strong>
        </div>
      </div>

      <div className="topbar-right">
        <div
          className={`readiness-chip ${readiness?.status ?? health}`}
          title={
            readiness?.status === 'ready'
              ? 'All backend services operational'
              : 'Backend configuration check'
          }
        >
          <span className={`status-dot ${readiness?.status ?? health}`} />
          <span>
            {readiness?.status === 'ready'
              ? 'Ready'
              : health === 'offline'
                ? 'Offline'
                : readiness?.status === 'degraded'
                  ? 'Configured'
                  : 'Degraded'}
          </span>
        </div>

        <button
          type="button"
          className="icon-action-btn"
          onClick={onRefresh}
          title="Refresh workspace state"
          aria-label="Refresh workspace state"
        >
          <RefreshCw size={14} />
        </button>

        <button
          type="button"
          className={`icon-action-btn ${isRightSidebarOpen ? 'active' : ''}`}
          onClick={onToggleRightSidebar}
          title={isRightSidebarOpen ? 'Hide inspector' : 'Open inspector'}
          aria-label={isRightSidebarOpen ? 'Hide inspector' : 'Open inspector'}
        >
          {isRightSidebarOpen ? (
            <PanelRightClose size={15} />
          ) : (
            <PanelRightOpen size={15} />
          )}
          {sourcesCount > 0 && !isRightSidebarOpen && (
            <span className="btn-counter-badge">{sourcesCount}</span>
          )}
        </button>
      </div>
    </header>
  )
}
