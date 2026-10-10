import { FC } from 'react'
import { ArrowLeft, ChevronRight, PanelRightOpen } from 'lucide-react'
import { SystemReadiness } from '../RightSidebar'
type TopBarProps = {
  isLeftSidebarCollapsed?: boolean
  onToggleLeftSidebar?: () => void
  isRightSidebarOpen?: boolean
  onToggleRightSidebar?: () => void
  activeWorkspaceLabel: string
  activeThreadLabel?: string
  activeView: 'chat' | 'workbench' | 'outputs' | 'pins'
  onSelectView: (view: 'chat' | 'workbench' | 'outputs' | 'pins') => void
  readiness?: SystemReadiness | null
  health?: 'checking' | 'online' | 'offline'
  onRefresh?: () => void
  sourcesCount?: number
  artifactsCount?: number
}

export const TopBar: FC<TopBarProps> = ({
  isRightSidebarOpen = false,
  onToggleRightSidebar,
  activeWorkspaceLabel,
  activeThreadLabel,
  activeView,
  onSelectView,
  sourcesCount = 0,
  artifactsCount = 0,
}) => {
  const totalCount = sourcesCount + artifactsCount

  return (
    <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-border bg-card px-4 z-10">
      <div className="flex items-center gap-3 min-w-0">
        {activeView !== 'chat' && (
          <button
            type="button"
            className="flex items-center justify-center size-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted border border-border transition-colors"
            onClick={() => onSelectView('chat')}
            title="Return to chat"
            aria-label="Return to chat"
          >
            <ArrowLeft size={16} />
          </button>
        )}

        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground whitespace-nowrap overflow-hidden">
          <span>{activeWorkspaceLabel}</span>
          <ChevronRight size={13} className="text-muted-foreground/40" />
          <strong className="text-foreground font-semibold truncate max-w-[280px]">
            {activeView === 'chat'
              ? (activeThreadLabel ?? 'Research Conversation')
              : activeView === 'workbench'
                ? 'Source Workbench'
                : activeView === 'pins'
                  ? 'Saved pins'
                  : 'Workspace Outputs'}
          </strong>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {!isRightSidebarOpen && (
          <button
            type="button"
            className="relative flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-primary/40 hover:bg-muted hover:text-foreground"
            onClick={onToggleRightSidebar}
            title="Open inspector (Cmd+J / Ctrl+J)"
            aria-label="Open inspector"
          >
            <PanelRightOpen size={15} />
            {totalCount > 0 && (
              <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-bold text-primary-foreground shadow-sm">
                {totalCount}
              </span>
            )}
          </button>
        )}
      </div>
    </header>
  )
}
