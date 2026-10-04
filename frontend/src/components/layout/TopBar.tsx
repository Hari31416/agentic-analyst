import { FC } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react'
import { SystemReadiness } from '../RightSidebar'
import { cn } from '../../lib/utils'
import { AuthBar } from '../../auth/AuthGate'
import { UiLanguage } from '../../uiText'
import { useUiLanguage } from '../../hooks/useUiLanguage'

type TopBarProps = {
  isLeftSidebarCollapsed?: boolean
  onToggleLeftSidebar?: () => void
  isRightSidebarOpen?: boolean
  onToggleRightSidebar?: () => void
  activeWorkspaceLabel: string
  activeThreadLabel?: string
  activeView: 'chat' | 'workbench' | 'outputs'
  onSelectView: (view: 'chat' | 'workbench' | 'outputs') => void
  readiness?: SystemReadiness | null
  health?: 'checking' | 'online' | 'offline'
  onRefresh?: () => void
  sourcesCount?: number
}

export const TopBar: FC<TopBarProps> = ({
  isRightSidebarOpen = false,
  onToggleRightSidebar,
  activeWorkspaceLabel,
  activeThreadLabel,
  activeView,
  onSelectView,
  sourcesCount = 0,
}) => {
  const [uiLanguage, setUiLanguage] = useUiLanguage()

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
                : 'Workspace Outputs'}
          </strong>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <label className="flex items-center text-xs text-muted-foreground">
          <span className="sr-only">UI Language</span>
          <select
            value={uiLanguage}
            aria-label="UI Language"
            onChange={(e) => setUiLanguage(e.target.value as UiLanguage)}
            className="h-7 px-1.5 text-[11px] font-semibold rounded-md border border-border bg-card text-muted-foreground hover:text-foreground cursor-pointer focus:outline-none"
          >
            <option value="en">EN</option>
            <option value="hi">हिन्दी</option>
          </select>
        </label>
        <AuthBar />
        <button
          type="button"
          className={cn(
            'relative flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-primary/40 hover:bg-muted hover:text-foreground',
            isRightSidebarOpen &&
              'bg-accent text-accent-foreground border-primary/30',
          )}
          onClick={onToggleRightSidebar}
          title={
            isRightSidebarOpen
              ? 'Hide inspector (Cmd+J / Ctrl+J)'
              : 'Open inspector (Cmd+J / Ctrl+J)'
          }
          aria-label={isRightSidebarOpen ? 'Hide inspector' : 'Open inspector'}
        >
          {isRightSidebarOpen ? (
            <PanelRightClose size={15} />
          ) : (
            <PanelRightOpen size={15} />
          )}
          {sourcesCount > 0 && !isRightSidebarOpen && (
            <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-bold text-primary-foreground shadow-sm">
              {sourcesCount}
            </span>
          )}
        </button>
      </div>
    </header>
  )
}
