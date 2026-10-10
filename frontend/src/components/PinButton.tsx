import { useState } from 'react'
import { Pin, PinOff } from 'lucide-react'
import { type PinKind, type SavedPin } from '../pinsApi'
import { usePins } from './PinsContext'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from './ui/dialog'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'

export const pinKindLabel = {
  thread: 'Thread',
  message: 'Question & answer',
  artifact: 'Artifact',
}

export function PinEditor({
  kind,
  targetId,
  title: defaultTitle,
  pin,
  onClose,
}: {
  kind: PinKind
  targetId: string
  title: string
  pin?: SavedPin
  onClose: () => void
}) {
  const state = usePins()
  const [title, setTitle] = useState(pin?.title ?? defaultTitle.slice(0, 200))
  const [notes, setNotes] = useState(pin?.notes ?? '')
  const [tags, setTags] = useState(pin?.tags.join(', ') ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(remove = false) {
    if (!state) return
    setBusy(true)
    setError('')
    try {
      if (remove && pin) await state.remove(pin.id)
      else
        await state.save(
          kind,
          targetId,
          {
            title: title.trim(),
            notes,
            tags: tags
              .split(',')
              .map((tag) => tag.trim())
              .filter(Boolean),
          },
          pin?.id,
        )
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save pin.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <DialogContent onClick={(event) => event.stopPropagation()}>
        <DialogTitle>
          {pin ? 'Edit pin' : `Pin ${pinKindLabel[kind].toLowerCase()}`}
        </DialogTitle>
        <DialogDescription>
          Saved to your personal pins in this workspace.
        </DialogDescription>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="pin-title">Title</Label>
            <Input
              id="pin-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              required
              disabled={busy}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="pin-notes">Notes</Label>
            <textarea
              id="pin-notes"
              className="min-h-24 rounded-md border border-border bg-background p-3 text-sm"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={4000}
              disabled={busy}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="pin-tags">Tags, separated by commas</Label>
            <Input
              id="pin-tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              disabled={busy}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            {pin && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => void submit(true)}
              >
                <PinOff size={14} /> Remove pin
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy ? 'Saving...' : pin ? 'Save changes' : 'Save pin'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function PinButton({
  kind,
  targetId,
  title,
}: {
  kind: PinKind
  targetId: string
  title: string
}) {
  const state = usePins()
  const [open, setOpen] = useState(false)
  if (!state) return null
  const pin = state.pins.find(
    (item) => item.kind === kind && item.target_id === targetId,
  )
  return (
    <>
      <button
        type="button"
        className="pin-button"
        aria-label={
          pin
            ? `Edit pinned ${pinKindLabel[kind].toLowerCase()}`
            : `Pin ${pinKindLabel[kind].toLowerCase()}`
        }
        aria-pressed={!!pin}
        disabled={state.loading}
        onClick={(event) => {
          event.stopPropagation()
          setOpen(true)
        }}
        title={pin ? 'Edit or remove pin' : 'Pin this item'}
      >
        <Pin size={13} fill={pin ? 'currentColor' : 'none'} />
        <span>{pin ? 'Pinned' : 'Pin'}</span>
      </button>
      {open && (
        <PinEditor
          kind={kind}
          targetId={targetId}
          title={title}
          pin={pin}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}
