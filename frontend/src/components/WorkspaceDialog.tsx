import { FC, FormEvent, useState } from 'react'
import { FolderPlus } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'
import { Button } from './ui/button'
import { Input } from './ui/input'

type WorkspaceDialogProps = {
  isOpen: boolean
  onClose: () => void
  onCreate: (name: string) => Promise<void>
}

export const WorkspaceDialog: FC<WorkspaceDialogProps> = ({
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
      setError(
        err instanceof Error ? err.message : 'Failed to create workspace.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <FolderPlus className="size-5 text-primary" />
            <DialogTitle>New Workspace</DialogTitle>
          </div>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          <div className="space-y-2">
            <label
              htmlFor="ws-name-input"
              className="text-xs font-medium text-foreground"
            >
              Workspace Name
            </label>
            <Input
              id="ws-name-input"
              type="text"
              placeholder="e.g. Q3 Financial Risk Review"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
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
              {submitting ? 'Creating...' : 'Create workspace'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
