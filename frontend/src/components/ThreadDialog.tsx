import { FC, FormEvent, useState } from 'react'
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
}

export const ThreadDialog: FC<ThreadDialogProps> = ({
  isOpen,
  onClose,
  onCreate,
}) => {
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

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
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <MessageSquarePlus className="size-5 text-primary" />
            <DialogTitle>New Research Thread</DialogTitle>
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
              {submitting ? 'Starting...' : 'Start research thread'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
