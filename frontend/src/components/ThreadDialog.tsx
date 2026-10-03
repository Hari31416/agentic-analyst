import { FC, FormEvent, useEffect, useState } from 'react'
import { MessageSquarePlus } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'
import { Button } from './ui/button'
import { Input } from './ui/input'

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

  const handleOpenChange = (open: boolean) => {
    if (open) setName(initialName)
    else onClose()
  }

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <MessageSquarePlus className="size-5 text-primary" />
            <DialogTitle>{title}</DialogTitle>
          </div>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          <div className="space-y-2">
            <label
              htmlFor="thread-name-input"
              className="text-xs font-medium text-foreground"
            >
              Thread Objective / Topic
            </label>
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

          {error && <div className="text-xs text-destructive">{error}</div>}

          <DialogFooter className="pt-2">
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
