import {
  Children,
  cloneElement,
  createContext,
  Fragment,
  isValidElement,
  ReactElement,
  ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import { InlineArtifactPreview } from './InlineArtifactPreview'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Check, Copy } from 'lucide-react'

import { preprocessMarkdownMath } from '../lib/markdown-math'
import { cn } from '../lib/utils'

const PreBlockContext = createContext(false)

function getNodeText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(getNodeText).join('')
  if (isValidElement(node)) {
    const children = (node.props as { children?: ReactNode }).children
    return children ? getNodeText(children) : ''
  }
  return ''
}

/** Wide markdown tables scroll horizontally with an indicator fade on the right edge */
function ScrollableMarkdownTable({ children }: { children: ReactNode }) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [showRightFade, setShowRightFade] = useState(false)

  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller) return

    const update = () => {
      const { scrollLeft, scrollWidth, clientWidth } = scroller
      setShowRightFade(
        scrollWidth > clientWidth + 1 &&
          scrollLeft < scrollWidth - clientWidth - 1,
      )
    }

    update()
    scroller.addEventListener('scroll', update, { passive: true })
    const resizeObserver = new ResizeObserver(update)
    resizeObserver.observe(scroller)
    const table = scroller.querySelector('table')
    if (table) resizeObserver.observe(table)

    return () => {
      scroller.removeEventListener('scroll', update)
      resizeObserver.disconnect()
    }
  }, [])

  return (
    <div className="relative my-4 max-w-full rounded-xl border border-border/80 bg-card shadow-sm">
      <div ref={scrollerRef} className="overflow-x-auto rounded-xl">
        <table className="w-full min-w-[480px] border-collapse bg-card text-left text-sm">
          {children}
        </table>
      </div>
      {showRightFade && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 w-8 rounded-r-xl bg-gradient-to-l from-card to-transparent"
        />
      )}
    </div>
  )
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    void navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="group/code-block relative my-3 overflow-hidden rounded-xl border border-border/80 bg-[#1e1e1e] shadow-md">
      {language && (
        <div className="flex items-center justify-between border-b border-white/10 bg-white/5 px-4 py-1.5 text-xs font-mono text-muted-foreground">
          <span>{language}</span>
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1.5 rounded px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground"
            title="Copy code"
          >
            {copied ? (
              <>
                <Check className="size-3.5 text-status-ready" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="size-3.5" />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      )}
      <SyntaxHighlighter
        style={vscDarkPlus as any}
        language={language || 'text'}
        PreTag="div"
        customStyle={{
          margin: 0,
          padding: '1rem 1.25rem',
          fontSize: '0.825rem',
          lineHeight: '1.6',
          backgroundColor: 'transparent',
        }}
      >
        {code}
      </SyntaxHighlighter>
      {!language && (
        <button
          type="button"
          onClick={handleCopy}
          className="absolute top-2.5 right-2.5 rounded border border-white/10 bg-black/40 p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover/code-block:opacity-100 hover:bg-black/60 hover:text-foreground"
          title="Copy code"
        >
          {copied ? (
            <Check className="size-3.5 text-status-ready" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </button>
      )}
    </div>
  )
}

function renderInlineWithEvidence(
  node: ReactNode,
  allowed: Set<string>,
  evidenceIds: string[],
  onOpen?: (id: string) => void,
  artifactIds: string[] = [],
): ReactNode {
  if (typeof node === 'string') {
    const pattern = /\[(evidence|artifact):([0-9a-f-]{36})\]/gi
    if (!pattern.test(node)) return node
    pattern.lastIndex = 0
    const parts: ReactNode[] = []
    let cursor = 0
    let matchIdx = 0
    for (const match of node.matchAll(pattern)) {
      const start = match.index ?? 0
      const id = match[2]
      if (start > cursor) {
        parts.push(node.slice(cursor, start))
      }
      if (
        match[1].toLowerCase() === 'artifact' &&
        artifactIds.some((item) => item.toLowerCase() === id.toLowerCase())
      ) {
        parts.push(
          <InlineArtifactPreview
            key={`art-${id}-${matchIdx}`}
            id={id.toLowerCase()}
            referenceOnly
          />,
        )
      } else if (
        match[1].toLowerCase() === 'evidence' &&
        allowed.has(id.toLowerCase())
      ) {
        const label = evidenceIds.findIndex(
          (evidenceId) => evidenceId.toLowerCase() === id.toLowerCase(),
        )
        parts.push(
          <button
            key={`ev-${id}-${matchIdx}`}
            type="button"
            className="inline-citation"
            title="Open cited evidence"
            onClick={() => onOpen?.(id)}
          >
            [{label + 1}]
          </button>,
        )
      } else {
        parts.push(match[0])
      }
      cursor = start + match[0].length
      matchIdx += 1
    }
    if (cursor < node.length) {
      parts.push(node.slice(cursor))
    }
    return parts
  }

  if (Array.isArray(node)) {
    return node.map((child, idx) => (
      <Fragment key={idx}>
        {renderInlineWithEvidence(
          child,
          allowed,
          evidenceIds,
          onOpen,
          artifactIds,
        )}
      </Fragment>
    ))
  }

  if (isValidElement(node)) {
    const el = node as ReactElement<{ children?: ReactNode }>
    if (el.props.children == null) return node
    return cloneElement(el, {
      ...el.props,
      children: Children.map(el.props.children, (child) =>
        renderInlineWithEvidence(
          child,
          allowed,
          evidenceIds,
          onOpen,
          artifactIds,
        ),
      ),
    })
  }

  return node
}

export interface MarkdownRendererProps {
  content: string
  className?: string
  evidenceIds?: string[]
  onOpenEvidence?: (id: string) => void
  artifactIds?: string[]
}

const EMPTY_REFERENCE_IDS: string[] = []

export function MarkdownRenderer({
  content,
  className,
  artifactIds: artifactIdsInput = EMPTY_REFERENCE_IDS,
  evidenceIds: evidenceIdsInput = EMPTY_REFERENCE_IDS,
  onOpenEvidence,
}: MarkdownRendererProps) {
  // Reference arrays and chat callbacks may be recreated on every composer edit.
  // Keep Markdown component types stable so artifact viewers retain their state.
  const artifactIdsKey = artifactIdsInput.join(',')
  const evidenceIdsKey = evidenceIdsInput.join(',')
  const artifactIds = useMemo(
    () => (artifactIdsKey ? artifactIdsKey.split(',') : EMPTY_REFERENCE_IDS),
    [artifactIdsKey],
  )
  const evidenceIds = useMemo(
    () => (evidenceIdsKey ? evidenceIdsKey.split(',') : EMPTY_REFERENCE_IDS),
    [evidenceIdsKey],
  )
  const openEvidenceRef = useRef(onOpenEvidence)
  openEvidenceRef.current = onOpenEvidence

  const processedContent = useMemo(
    () => preprocessMarkdownMath(content),
    [content],
  )

  const allowedEvidence = useMemo(
    () => new Set(evidenceIds.map((id) => id.toLowerCase())),
    [evidenceIds],
  )

  const renderEvidence = (node: ReactNode) =>
    renderInlineWithEvidence(
      node,
      allowedEvidence,
      evidenceIds,
      (id) => openEvidenceRef.current?.(id),
      artifactIds,
    )

  const artifactIdForUrl = (url?: string) => {
    const match =
      /^(?:artifact:)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(
        url ?? '',
      )
    return match &&
      artifactIds.some((id) => id.toLowerCase() === match[1].toLowerCase())
      ? match[1].toLowerCase()
      : null
  }

  const components = useMemo(() => {
    return {
      pre: ({ children }: { children?: ReactNode }) => (
        <PreBlockContext.Provider value={true}>
          {children}
        </PreBlockContext.Provider>
      ),
      code({ className: codeClassName, children, ...props }: any) {
        const isInsidePre = useContext(PreBlockContext)
        const match = /language-(\w+)/.exec(codeClassName ?? '')
        const code = getNodeText(children).replace(/\n$/, '')

        if (match || isInsidePre) {
          return <CodeBlock language={match ? match[1] : 'text'} code={code} />
        }

        return (
          <code
            className={cn(
              'rounded-md border border-border/80 bg-muted/60 px-1.5 py-0.5 font-mono text-[12px] font-medium text-foreground break-all',
              codeClassName,
            )}
            {...props}
          >
            {children}
          </code>
        )
      },
      table: ({ children }: any) => (
        <ScrollableMarkdownTable>{children}</ScrollableMarkdownTable>
      ),
      thead: ({ children }: any) => (
        <thead className="border-b border-border bg-muted/50">{children}</thead>
      ),
      th: ({ children }: any) => (
        <th className="border border-border/80 bg-muted/30 px-3.5 py-2.5 text-xs font-semibold tracking-wider text-muted-foreground uppercase text-left">
          {renderEvidence(children)}
        </th>
      ),
      td: ({ children }: any) => (
        <td className="border border-border/80 px-3.5 py-2 text-sm text-foreground/90 align-top">
          {renderEvidence(children)}
        </td>
      ),
      ul: ({ children }: any) => (
        <ul className="my-1.5 ml-5 list-disc space-y-0.5 [&_ul]:list-[circle] [&_ul]:my-0.5 [&_ol]:my-0.5">
          {renderEvidence(children)}
        </ul>
      ),
      ol: ({ children }: any) => (
        <ol className="my-1.5 ml-5 list-decimal space-y-0.5 [&_ol]:list-[lower-alpha] [&_ol]:my-0.5 [&_ul]:my-0.5">
          {renderEvidence(children)}
        </ol>
      ),
      li: ({ children }: any) => (
        <li className="leading-relaxed text-foreground/90 pl-1 [&>p]:inline [&>p]:m-0">
          {renderEvidence(children)}
        </li>
      ),
      p: ({ children }: any) => {
        const hasPreview = Children.toArray(children).some(
          (child) =>
            isValidElement(child) &&
            artifactIdForUrl((child.props as { src?: string }).src),
        )
        const Tag = hasPreview ? 'div' : 'p'
        return (
          <Tag className="mb-2 leading-relaxed last:mb-0">
            {renderEvidence(children)}
          </Tag>
        )
      },
      h1: ({ children }: any) => (
        <h1 className="mt-4 mb-2 text-lg font-bold tracking-tight text-foreground border-b border-border/40 pb-1 first:mt-0">
          {renderEvidence(children)}
        </h1>
      ),
      h2: ({ children }: any) => (
        <h2 className="mt-3.5 mb-1.5 text-base font-bold tracking-tight text-foreground first:mt-0">
          {renderEvidence(children)}
        </h2>
      ),
      h3: ({ children }: any) => (
        <h3 className="mt-3 mb-1 text-sm font-semibold tracking-tight text-foreground first:mt-0">
          {renderEvidence(children)}
        </h3>
      ),
      h4: ({ children }: any) => (
        <h4 className="mt-2.5 mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground first:mt-0">
          {renderEvidence(children)}
        </h4>
      ),
      blockquote: ({ children }: any) => (
        <blockquote className="my-2.5 border-l-3 border-primary/70 pl-3.5 text-muted-foreground italic bg-muted/20 py-1 rounded-r-md">
          {renderEvidence(children)}
        </blockquote>
      ),
      hr: () => <hr className="my-3 border-t border-border" />,
      img: ({ src, alt }: any) => {
        const id = artifactIdForUrl(src)
        return id ? (
          <InlineArtifactPreview id={id} caption={alt} />
        ) : src ? (
          <img src={src} alt={alt ?? ''} loading="lazy" />
        ) : (
          <span>{alt || 'Image reference unavailable'}</span>
        )
      },
      a: ({ href, children, ...props }: any) => {
        const id = artifactIdForUrl(href)
        if (id)
          return (
            <InlineArtifactPreview
              id={id}
              caption={getNodeText(children)}
              referenceOnly
            />
          )
        return (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-primary underline underline-offset-2 hover:text-primary-hover"
            {...props}
          >
            {renderEvidence(children)}
          </a>
        )
      },
    }
  }, [allowedEvidence, evidenceIds, artifactIds])

  return (
    <PreBlockContext.Provider value={false}>
      <div
        className={cn(
          'prose max-w-none text-foreground/90 [&_li>p]:inline [&_li>p]:m-0',
          className,
        )}
      >
        <ReactMarkdown
          remarkPlugins={[remarkMath, remarkGfm]}
          rehypePlugins={[rehypeKatex]}
          components={components}
          urlTransform={(url) => {
            if (artifactIdForUrl(url)) return url
            if (
              /^(?:artifact:)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
                url,
              )
            )
              return ''
            return defaultUrlTransform(url)
          }}
        >
          {processedContent}
        </ReactMarkdown>
      </div>
    </PreBlockContext.Provider>
  )
}
