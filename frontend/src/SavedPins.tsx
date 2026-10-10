import { useEffect, useMemo, useState } from 'react'
import {
  ExternalLink,
  Pencil,
  Pin,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react'
import { pinsApi, type PinDetail, type PinKind, type SavedPin } from './pinsApi'
import { usePins } from './components/PinsContext'
import { PinEditor, pinKindLabel } from './components/PinButton'
import { MarkdownRenderer } from './components/MarkdownRenderer'
import { ArtifactDetail } from './ArtifactBrowser'
import { DeleteConfirmDialog } from './components/DeleteConfirmDialog'
import { Button } from './components/ui/button'
import './pins.css'

export default function SavedPins({
  onOpenThread,
}: {
  onOpenThread: (threadId: string) => void
}) {
  const state = usePins()!
  const [kind, setKind] = useState<PinKind | ''>('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<PinDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [edit, setEdit] = useState<SavedPin | null>(null)
  const [deleting, setDeleting] = useState<SavedPin | null>(null)
  const [busy, setBusy] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [page, setPage] = useState(0)
  const matches = useMemo(
    () =>
      state.pins.filter(
        (pin) =>
          (!kind || kind === pin.kind) &&
          [pin.title, pin.notes, pin.thread_label, ...pin.tags]
            .join(' ')
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
      ),
    [state.pins, kind, query],
  )
  const selectedPin = state.pins.find((pin) => pin.id === selected)
  useEffect(() => {
    let active = true
    setDetail(null)
    setDetailError('')
    if (selectedPin)
      void pinsApi
        .read(selectedPin.id)
        .then((result) => {
          if (active) setDetail(result)
        })
        .catch((reason) => {
          if (active)
            setDetailError(
              reason instanceof Error ? reason.message : 'Could not read pin.',
            )
        })
    return () => {
      active = false
    }
  }, [selectedPin])
  const lastPage = Math.max(0, Math.ceil(matches.length / 30) - 1)
  const currentPage = Math.min(page, lastPage)
  async function remove() {
    if (!deleting) return
    setBusy(true)
    setDeleteError('')
    try {
      await state.remove(deleting.id)
      setDeleting(null)
    } catch (reason) {
      setDeleteError(
        reason instanceof Error ? reason.message : 'Could not remove pin.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="saved-pins" aria-label="Saved pins">
      <header className="pins-heading">
        <div>
          <h1>
            <Pin size={20} /> Saved pins
          </h1>
          <p>
            Your threads, answers, and generated artifacts, saved in this
            workspace.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => void state.refresh()}
          disabled={state.loading}
        >
          <RefreshCw size={14} /> Refresh
        </Button>
      </header>
      <div className="pins-filters">
        <label className="pins-search">
          <Search size={15} />
          <input
            aria-label="Search pins"
            placeholder="Search titles, notes, or tags"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setPage(0)
            }}
          />
        </label>
        <select
          aria-label="Filter pins by type"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as PinKind | '')
            setPage(0)
          }}
        >
          <option value="">All types</option>
          <option value="thread">Threads</option>
          <option value="message">Questions & answers</option>
          <option value="artifact">Artifacts only</option>
        </select>
      </div>
      {state.error && (
        <p className="pins-error" role="alert">
          {state.error}
        </p>
      )}
      <div className="pins-columns">
        <div className="pins-list">
          {state.loading && <p role="status">Loading pins...</p>}
          {!state.loading && !matches.length && (
            <div className="pins-empty">
              <Pin size={28} />
              <h2>
                {state.pins.length
                  ? 'No matching pins'
                  : 'Keep useful work here'}
              </h2>
              <p>
                Use Pin on a thread, a message, or an artifact. Each pin can
                have its own title, notes, and tags.
              </p>
            </div>
          )}
          {matches
            .slice(currentPage * 30, (currentPage + 1) * 30)
            .map((pin) => (
              <article
                className={`pin-entry ${selected === pin.id ? 'selected' : ''}`}
                key={pin.id}
              >
                <button
                  className="pin-entry-open"
                  onClick={() => setSelected(pin.id)}
                  aria-pressed={selected === pin.id}
                >
                  <span className="pin-type">{pinKindLabel[pin.kind]}</span>
                  <strong>{pin.title}</strong>
                  <span className="pin-origin">
                    {pin.thread_label} ·{' '}
                    {new Date(pin.created_at).toLocaleDateString()}
                  </span>
                  {pin.notes && <span className="pin-note">{pin.notes}</span>}
                  {!!pin.tags.length && (
                    <span className="pin-tags">{pin.tags.join(' · ')}</span>
                  )}
                </button>
                <div className="pin-entry-actions">
                  <button
                    aria-label={`Edit pin ${pin.title}`}
                    title="Edit pin"
                    onClick={() => setEdit(pin)}
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    aria-label={`Remove pin ${pin.title}`}
                    title="Remove pin"
                    onClick={() => {
                      setDeleteError('')
                      setDeleting(pin)
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            ))}
          {matches.length > 30 && (
            <div className="pins-pagination">
              <Button
                variant="outline"
                disabled={!currentPage}
                onClick={() => setPage(currentPage - 1)}
              >
                Previous
              </Button>
              <span>
                {currentPage + 1} / {lastPage + 1}
              </span>
              <Button
                variant="outline"
                disabled={currentPage === lastPage}
                onClick={() => setPage(currentPage + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </div>
        <div className="pin-detail">
          {!selectedPin ? (
            <div className="pins-empty">
              <h2>Select a pin to inspect it</h2>
              <p>Artifact pins open the generated file directly.</p>
            </div>
          ) : (
            <>
              <header className="pin-detail-heading">
                <h2>{selectedPin.title}</h2>
                <Button
                  variant="outline"
                  onClick={() => onOpenThread(selectedPin.thread_id)}
                >
                  <ExternalLink size={14} /> Open thread
                </Button>
              </header>
              {selectedPin.notes && (
                <p className="pin-detail-notes">{selectedPin.notes}</p>
              )}
              {detailError ? (
                <p role="alert" className="pins-error">
                  {detailError}
                </p>
              ) : !detail ? (
                <p role="status">Loading saved item...</p>
              ) : detail.artifact ? (
                <ArtifactDetail
                  key={detail.artifact.id}
                  artifact={detail.artifact}
                />
              ) : detail.messages ? (
                detail.messages.map((message) => (
                  <article className="pin-message" key={message.id}>
                    <span className="pin-type">{message.role}</span>
                    <MarkdownRenderer
                      content={message.content}
                      artifactIds={message.references.artifact_ids ?? []}
                      evidenceIds={message.references.evidence_ids ?? []}
                    />
                  </article>
                ))
              ) : (
                <p>
                  This pin links to the full conversation. Open the thread to
                  continue.
                </p>
              )}
            </>
          )}
        </div>
      </div>
      {edit && (
        <PinEditor
          kind={edit.kind}
          targetId={edit.target_id}
          title={edit.title}
          pin={edit}
          onClose={() => setEdit(null)}
        />
      )}
      <DeleteConfirmDialog
        isOpen={!!deleting}
        title="Remove pin?"
        message={`Remove "${deleting?.title ?? ''}" from your pins? The original content will stay available.`}
        onClose={() => {
          if (!busy) setDeleting(null)
        }}
        onConfirm={remove}
        confirming={busy}
        error={deleteError}
      />
    </section>
  )
}
