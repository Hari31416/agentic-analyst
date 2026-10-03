import { FC } from 'react'
import { ArrowLeft, ChevronRight } from 'lucide-react'
import { SystemReadiness } from '../RightSidebar'

type TopBarProps = {
  isLeftSidebarCollapsed?: boolean
  onToggleLeftSidebar?: () => void
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
  isLeftSidebarCollapsed: _isLeftSidebarCollapsed,
  onToggleLeftSidebar: _onToggleLeftSidebar,
  activeWorkspaceLabel,
  activeThreadLabel,
  activeView,
  onSelectView,
}) => {
  return (
    <header className="main-topbar">
      <div className="topbar-left">
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
    </header>
  )
}
