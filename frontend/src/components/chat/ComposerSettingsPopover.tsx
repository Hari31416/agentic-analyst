import { FC, useRef, useEffect, useState } from 'react'
import { Globe2, Settings2, SlidersHorizontal, X } from 'lucide-react'
import { AnswerLanguage } from '../../chatApi'
import { LanguageCapabilities } from '../../languageApi'
import { UiLanguage, UiTextKey, uiText } from '../../uiText'
import { cn } from '../../lib/utils'

interface ComposerSettingsPopoverProps {
  language: AnswerLanguage
  onLanguageChange: (lang: AnswerLanguage) => void
  languageCapabilities: LanguageCapabilities | null
  retrievalProfile: 'basic' | 'advanced'
  onRetrievalProfileChange: (profile: 'basic' | 'advanced') => void
  uiLanguage: UiLanguage
  disabled?: boolean
}

export const ComposerSettingsPopover: FC<ComposerSettingsPopoverProps> = ({
  language,
  onLanguageChange,
  languageCapabilities,
  retrievalProfile,
  onRetrievalProfileChange,
  uiLanguage,
  disabled,
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const copy = (key: UiTextKey) => uiText(uiLanguage, key)

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

  const languages = languageCapabilities?.languages ?? [
    { tag: 'en-IN', name: 'English' },
    { tag: 'hi-IN', name: 'हिन्दी' },
  ]

  const activeLangName =
    languages.find((l) => l.tag === language)?.name ?? 'English'

  return (
    <div className="relative inline-flex items-center" ref={containerRef}>
      <button
        type="button"
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors border',
          isOpen || retrievalProfile === 'advanced'
            ? 'border-primary/40 bg-accent text-accent-foreground'
            : 'border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground',
          disabled && 'opacity-50 pointer-events-none',
        )}
        onClick={() => setIsOpen((prev) => !prev)}
        disabled={disabled}
        aria-expanded={isOpen}
        aria-label="Query options and settings"
        title="Query options (language and retrieval depth)"
      >
        <SlidersHorizontal size={12} />
        <span>{activeLangName}</span>
        {retrievalProfile === 'advanced' && (
          <span className="rounded bg-primary/20 px-1 text-[9px] font-semibold text-primary">
            Deep
          </span>
        )}
      </button>

      {isOpen && (
        <div
          className="absolute bottom-full left-0 mb-2 w-72 rounded-xl border border-border bg-card p-3 shadow-lg z-30"
          role="dialog"
          aria-label="Query settings dialog"
        >
          <div className="flex items-center justify-between pb-2 mb-3 border-b border-border">
            <div className="flex items-center gap-1.5">
              <Settings2 size={13} className="text-primary" />
              <span className="text-[11px] font-semibold text-foreground uppercase tracking-wider">
                Query Options
              </span>
            </div>
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => setIsOpen(false)}
              aria-label="Close options"
            >
              <X size={13} />
            </button>
          </div>

          <div className="flex flex-col gap-3 text-xs">
            <div>
              <label className="flex items-center gap-1.5 text-muted-foreground font-medium mb-1">
                <Globe2 size={12} />
                <span>{copy('answerLanguage')}</span>
              </label>
              <select
                className="w-full h-8 px-2 bg-background border border-border rounded-md text-foreground text-xs focus:outline-none focus:border-primary"
                value={language}
                onChange={(e) => onLanguageChange(e.target.value as AnswerLanguage)}
              >
                {languages.map((lang) => (
                  <option key={lang.tag} value={lang.tag}>
                    {lang.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-muted-foreground font-medium mb-1">
                Retrieval Depth
              </label>
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  className={cn(
                    'flex flex-col p-2 rounded-lg border text-left transition-colors',
                    retrievalProfile === 'basic'
                      ? 'border-primary bg-accent/60 text-accent-foreground font-medium'
                      : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
                  )}
                  onClick={() => onRetrievalProfileChange('basic')}
                >
                  <span className="text-xs font-semibold">Standard</span>
                  <span className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                    Fast hybrid retrieval
                  </span>
                </button>
                <button
                  type="button"
                  className={cn(
                    'flex flex-col p-2 rounded-lg border text-left transition-colors',
                    retrievalProfile === 'advanced'
                      ? 'border-primary bg-accent/60 text-accent-foreground font-medium'
                      : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
                  )}
                  onClick={() => onRetrievalProfileChange('advanced')}
                >
                  <span className="text-xs font-semibold">Deep Research</span>
                  <span className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                    Multi-hop reranking
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ComposerSettingsPopover
