import { FC } from 'react'
import {
  ArrowRight,
  BarChart3,
  FileSearch,
  LayoutDashboard,
  Sparkles,
} from 'lucide-react'

const SUGGESTIONS = [
  {
    icon: BarChart3,
    title: 'Analyze & Profile Data',
    description:
      'Profile distributions, identify missing records, and execute Python analysis on structured tables.',
    prompt:
      'Profile the dataset distributions, count missing values, and summarize outliers',
    cta: 'Profile data',
  },
  {
    icon: FileSearch,
    title: 'Cross-Source Evidence',
    description:
      'Search and synthesize across PDFs and DOCX files with verified page and line citations.',
    prompt:
      'Extract and synthesize findings from uploaded documents with page and line citations',
    cta: 'Synthesize evidence',
  },
  {
    icon: LayoutDashboard,
    title: 'Executive Reports & Charts',
    description:
      'Generate formatted reports, data comparisons, and Plotly visualization specifications.',
    prompt:
      'Generate an executive summary with a chart and structured comparison table',
    cta: 'Generate report',
  },
] as const

interface EmptyChatHeroProps {
  onSuggestion: (prompt: string) => void
}

export const EmptyChatHero: FC<EmptyChatHeroProps> = ({ onSuggestion }) => {
  return (
    <div className="relative mx-auto max-w-2xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="flex justify-center sm:justify-start">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-accent px-3 py-1 text-[11px] font-semibold tracking-wider text-accent-foreground uppercase">
          <Sparkles className="size-3.5 text-primary" />
          Evidence-Backed Analyst
        </span>
      </div>

      <h2 className="mt-4 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
        Research and analyze across documents and data
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        Ask questions, query structured datasets, execute Python code, and
        inspect citations with complete evidence provenance.
      </p>

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        {SUGGESTIONS.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.title}
              type="button"
              onClick={() => onSuggestion(item.prompt)}
              className="group flex flex-col justify-between rounded-xl border border-border bg-card p-4 text-left shadow-xs transition-all duration-150 hover:border-primary/40 hover:bg-card hover:shadow-sm"
            >
              <div>
                <div className="flex size-8 items-center justify-center rounded-lg border border-primary/20 bg-accent text-accent-foreground transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                  <Icon className="size-4" />
                </div>
                <h3 className="mt-3 text-xs font-semibold text-foreground group-hover:text-primary">
                  {item.title}
                </h3>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {item.description}
                </p>
              </div>
              <div className="mt-4 flex items-center gap-1 text-[11px] font-medium text-primary">
                <span>{item.cta}</span>
                <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default EmptyChatHero
