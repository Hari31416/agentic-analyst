import { FC, useRef, useEffect, useState } from 'react'
import { ChevronDown, Database, FileSpreadsheet, FileText, Layers, X } from 'lucide-react'
import { DatasetSummary, sourceKindLabel, datasetLabel } from '../../structuredApi'
import { ChatSource } from '../../ChatPanel'
import { cn } from '../../lib/utils'

interface ComposerScopePopoverProps {
  sources: ChatSource[]
  datasets: DatasetSummary[]
  selectedSourceIds: string[]
  selectedDatasetIds: string[]
  onToggleSource: (sourceId: string) => void
  onToggleDataset: (datasetId: string) => void
  onSelectAll: () => void
  onClearScope: () => void
}

function SourceIcon({ kind }: { kind: string }) {
  const lower = kind.toLowerCase()
  if (lower.includes('database') || lower.includes('sql') || lower.includes('postgres')) {
    return <Database size={13} />
  }
  if (lower.includes('sheet') || lower.includes('csv') || lower.includes('excel')) {
    return <FileSpreadsheet size={13} />
  }
  return <FileText size={13} />
}

export const ComposerScopePopover: FC<ComposerScopePopoverProps> = ({
  sources,
  datasets,
  selectedSourceIds,
  selectedDatasetIds,
  onToggleSource,
  onToggleDataset,
  onSelectAll,
  onClearScope,
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  if (sources.length === 0) return null

  const isAllSelected =
    selectedSourceIds.length === 0 || selectedSourceIds.length === sources.length

  const label = isAllSelected
    ? 'All sources'
    : selectedSourceIds.length === 1
      ? sources.find((s) => s.id === selectedSourceIds[0])?.display_name || '1 source'
      : `${selectedSourceIds.length} sources`

  return (
    <div className="relative inline-flex items-center" ref={containerRef}>
      <button
        type="button"
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors border',
          isOpen || !isAllSelected
            ? 'border-primary/40 bg-accent text-accent-foreground'
            : 'border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground',
        )}
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-label="Analysis source scope"
        title="Choose which sources this query analyzes"
      >
        <Layers size={13} className="text-primary" />
        <span className="max-w-[150px] truncate">{label}</span>
        <ChevronDown size={11} className={cn('transition-transform', isOpen && 'rotate-180')} />
      </button>

      {isOpen && (
        <div
          className="absolute bottom-full left-0 mb-2 w-80 max-h-96 overflow-y-auto rounded-xl border border-border bg-card p-3 shadow-lg z-30"
          role="dialog"
          aria-label="Source selection menu"
        >
          <div className="flex items-center justify-between pb-2 mb-2 border-b border-border">
            <span className="text-[11px] font-semibold text-foreground uppercase tracking-wider">
              Query Scope
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="text-[11px] text-primary hover:underline font-medium"
                onClick={onSelectAll}
              >
                All
              </button>
              <span className="text-muted-foreground text-[10px]">·</span>
              <button
                type="button"
                className="text-[11px] text-muted-foreground hover:text-foreground"
                onClick={onClearScope}
              >
                Reset
              </button>
              <button
                type="button"
                className="ml-1 text-muted-foreground hover:text-foreground"
                onClick={() => setIsOpen(false)}
                aria-label="Close scope menu"
              >
                <X size={13} />
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-1">
            {sources.map((source) => {
              const checked =
                selectedSourceIds.length === 0 || selectedSourceIds.includes(source.id)
              const sourceDatasets = datasets.filter(
                (dataset) => dataset.source_id === source.id,
              )
              return (
                <div key={source.id} className="rounded-lg border border-border/40 p-2 hover:bg-muted/40 transition-colors">
                  <label className="flex items-start gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      className="mt-0.5 rounded border-border accent-primary"
                      checked={checked}
                      onChange={() => onToggleSource(source.id)}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-muted-foreground shrink-0">
                          <SourceIcon kind={source.kind} />
                        </span>
                        <strong className="text-xs font-medium text-foreground truncate block">
                          {source.display_name}
                        </strong>
                      </div>
                      <span className="text-[10px] text-muted-foreground block mt-0.5">
                        {sourceKindLabel(source.kind)} · v{source.version}
                      </span>
                    </div>
                  </label>

                  {checked && sourceDatasets.length > 0 && (
                    <div className="mt-2 ml-5 flex flex-col gap-1 border-l-2 border-border pl-2.5">
                      <span className="text-[10px] text-muted-foreground font-medium">
                        Tables / Sheets:
                      </span>
                      {sourceDatasets.map((ds) => (
                        <label
                          key={ds.id}
                          className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            className="rounded border-border accent-primary"
                            checked={selectedDatasetIds.includes(ds.id)}
                            onChange={() => onToggleDataset(ds.id)}
                          />
                          <span className="truncate">{datasetLabel(ds)}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default ComposerScopePopover
