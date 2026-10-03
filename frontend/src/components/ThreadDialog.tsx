import { FC, FormEvent, useEffect, useState } from 'react'
import { MessageSquarePlus } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'

type ThreadDialogProps = {
  isOpen: boolean
  onClose: () => void
  onCreate: (name: string) => Promise<void>
  initialName?: string
  title?: string
  submitLabel?: string
}

export const ThreadDialog: FC<ThreadDialogProps> = ({
  isOpen,
  onClose,
  onCreate,
  initialName = '',
  title = 'New Research Thread',
  submitLabel = 'Start research thread',
}) => {
  const [name, setName] = useState(initialName)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (isOpen) {
      setName(initialName)
      setError('')
    }
  }, [isOpen, initialName])

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setSubmitting(true)
    setError('')
    try {
      await onCreate(trimmed)
      setName('')
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create thread.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MessageSquarePlus className="size-4" />
              {title}
            </DialogTitle>
            <DialogDescription>
              Start a focused research and reasoning thread
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="thread-name-input">Thread Objective / Topic</Label>
            <Input
              id="thread-name-input"
              type="text"
              placeholder="e.g. Evaluate revenue variance between schemes"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={140}
              autoFocus
              disabled={submitting}
            />
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || submitting}>
              {submitting ? 'Saving...' : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
