import { useEffect, useState } from 'react'
import { Button } from './components/ui/button'
import {
  reportApi,
  type Report,
  type ReportDocument,
  type ReportVersion,
} from './reportApi'

function fields(
  document: ReportDocument,
): Map<string, { label: string; value: string; identity?: string }> {
  const values = new Map<
    string,
    { label: string; value: string; identity?: string }
  >()
  values.set('title', { label: 'Title', value: document.title })
  values.set('language', { label: 'Language', value: document.language })
  values.set('sections', {
    label: 'Section order',
    value: document.sections.map((s) => s.heading).join(' → '),
    identity: document.sections.map((s) => s.id).join(', '),
  })
  for (const section of document.sections) {
    values.set(`section:${section.id}`, {
      label: 'Section heading',
      value: section.heading,
    })
    values.set(`order:${section.id}`, {
      label: `Block order · ${section.heading}`,
      value: section.blocks
        .map((b, i) =>
          b.type === 'paragraph'
            ? `Paragraph ${i + 1}: ${b.text.slice(0, 60)}`
            : b.caption || b.type,
        )
        .join(' → '),
      identity: section.blocks.map((b) => b.id).join(', '),
    })
    for (const block of section.blocks) {
      values.set(`block:${block.id}`, {
        label: `${section.heading} · ${block.type === 'paragraph' ? 'Paragraph' : 'Caption'}`,
        value: block.type === 'paragraph' ? block.text : block.caption,
      })
      if (block.type !== 'paragraph')
        values.set(`asset:${block.id}`, {
          label: `${section.heading} · Artifact`,
          value: block.artifact_id,
        })
      if (block.type === 'table')
        values.set(`table:${block.id}`, {
          label: `${section.heading} · Table settings`,
          value: `Columns: ${block.columns.join(', ') || 'All'}; maximum rows: ${block.max_rows}`,
        })
    }
  }
  return values
}

export function ReportComparison({
  before,
  after,
  beforeLabel,
  afterLabel,
}: {
  before: ReportDocument
  after: ReportDocument
  beforeLabel: string
  afterLabel: string
}) {
  const oldFields = fields(before),
    newFields = fields(after)
  const changed = [
    ...new Set([...oldFields.keys(), ...newFields.keys()]),
  ].filter(
    (key) =>
      (oldFields.get(key)?.identity ?? oldFields.get(key)?.value) !==
      (newFields.get(key)?.identity ?? newFields.get(key)?.value),
  )
  return (
    <section className="report-comparison" aria-label="Report changes">
      <h3>
        {changed.length
          ? `${changed.length} changed ${changed.length === 1 ? 'field' : 'fields'}`
          : 'No document changes'}
      </h3>
      <div className="report-diff-head">
        <span>{beforeLabel}</span>
        <span>{afterLabel}</span>
      </div>
      {changed.map((key) => (
        <div className="report-diff" key={key}>
          <h4>{newFields.get(key)?.label ?? oldFields.get(key)?.label}</h4>
          <div className="report-diff-columns">
            <div className="report-diff-before">
              {oldFields.get(key)?.value ?? 'Not present'}
            </div>
            <div className="report-diff-after">
              {newFields.get(key)?.value ?? 'Removed'}
            </div>
          </div>
        </div>
      ))}
    </section>
  )
}

export function ReportEditor({
  reportId,
  version,
  onSaved,
  onCancel,
}: {
  reportId: string
  version: ReportVersion
  onSaved: (report: Report) => void
  onCancel: () => void
}) {
  const base = version.document!
  const [draft, setDraft] = useState<ReportDocument>(() =>
    structuredClone(base),
  )
  const [reviewing, setReviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const dirty = JSON.stringify(draft) !== JSON.stringify(base)
  useEffect(() => {
    if (!dirty) return
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', prevent)
    return () => window.removeEventListener('beforeunload', prevent)
  }, [dirty])
  async function save() {
    setSaving(true)
    setError('')
    try {
      onSaved(await reportApi.edit(reportId, version.id, draft))
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not save changes.',
      )
      setSaving(false)
    }
  }
  return (
    <section className="report-editor" aria-label="Edit report">
      <header className="report-editor-header">
        <div>
          <h3>
            {reviewing
              ? 'Review your changes'
              : `Edit version ${version.number}`}
          </h3>
          <p>
            Save creates a new version. Tables, figures, and source references
            stay attached.
          </p>
        </div>
      </header>
      {error && (
        <p role="alert" className="reports-alert">
          {error}
        </p>
      )}
      {reviewing ? (
        <ReportComparison
          before={base}
          after={draft}
          beforeLabel={`Version ${version.number}`}
          afterLabel="Your draft"
        />
      ) : (
        <div className="report-editor-fields">
          <label>
            Document title
            <input
              value={draft.title}
              maxLength={200}
              required
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          {draft.sections.map((section, si) => (
            <fieldset key={section.id}>
              <legend>Section {si + 1}</legend>
              <label>
                Heading
                <input
                  value={section.heading}
                  maxLength={300}
                  required
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      sections: current.sections.map((s, i) =>
                        i === si ? { ...s, heading: e.target.value } : s,
                      ),
                    }))
                  }
                />
              </label>
              {section.blocks.map((block, bi) => (
                <label key={block.id}>
                  {block.type === 'paragraph'
                    ? `Paragraph ${bi + 1}`
                    : `${block.type === 'table' ? 'Table' : 'Figure'} caption`}
                  <textarea
                    value={
                      block.type === 'paragraph' ? block.text : block.caption
                    }
                    rows={block.type === 'paragraph' ? 5 : 2}
                    maxLength={block.type === 'paragraph' ? 50000 : 2000}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        sections: current.sections.map((s, i) =>
                          i !== si
                            ? s
                            : {
                                ...s,
                                blocks: s.blocks.map((b, j) =>
                                  j !== bi
                                    ? b
                                    : b.type === 'paragraph'
                                      ? { ...b, text: e.target.value }
                                      : { ...b, caption: e.target.value },
                                ),
                              },
                        ),
                      }))
                    }
                  />
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      )}
      <footer className="report-editor-actions">
        <Button variant="ghost" disabled={saving} onClick={onCancel}>
          {dirty ? 'Discard changes' : 'Cancel'}
        </Button>
        {reviewing ? (
          <>
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => setReviewing(false)}
            >
              Back to editing
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save new version'}
            </Button>
          </>
        ) : (
          <Button
            disabled={
              !dirty ||
              !draft.title.trim() ||
              draft.sections.some((s) => !s.heading.trim())
            }
            onClick={() => setReviewing(true)}
          >
            Review changes
          </Button>
        )}
      </footer>
    </section>
  )
}
