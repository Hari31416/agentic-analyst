import { FC } from 'react'
import {
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  X,
  AlertCircle,
  Check,
} from 'lucide-react'
import { isDocumentFile, isStructuredDataFile } from '../../lib/uploadHelper'

export type UploadingAttachment = {
  id: string
  file: File
  progress: number
  status: 'uploading' | 'ready' | 'error'
  error?: string
  sourceId?: string
}

interface ComposerUploadChipsProps {
  attachments: UploadingAttachment[]
  onRemove: (id: string) => void
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

export const ComposerUploadChips: FC<ComposerUploadChipsProps> = ({
  attachments,
  onRemove,
}) => {
  if (attachments.length === 0) return null

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 px-3 pt-2 pb-1"
      aria-label="Attached files"
    >
      {attachments.map((item) => {
        const isDoc = isDocumentFile(item.file)
        const isData = isStructuredDataFile(item.file)
        return (
          <div
            key={item.id}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs shadow-xs transition-colors ${
              item.status === 'error'
                ? 'border-status-danger/30 bg-status-danger/5'
                : 'border-border bg-card'
            }`}
          >
            <span className="text-muted-foreground">
              {isData ? (
                <FileSpreadsheet size={13} />
              ) : isDoc ? (
                <FileText size={13} />
              ) : (
                <FileSpreadsheet size={13} />
              )}
            </span>
            <span className="max-w-[140px] truncate font-medium text-foreground">
              {item.file.name}
            </span>
            <span className="text-[10px] text-muted-foreground">
              {item.status === 'uploading' ? (
                <span className="inline-flex items-center gap-1 text-primary">
                  <LoaderCircle size={10} className="animate-spin" />
                  {item.progress > 0 ? `${item.progress}%` : 'Uploading'}
                </span>
              ) : item.status === 'error' ? (
                <span
                  className="inline-flex items-center gap-0.5 text-status-danger cursor-help"
                  title={item.error || 'Upload failed'}
                >
                  <AlertCircle size={10} />
                  <span>Failed</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-0.5 text-status-ready">
                  <Check size={10} />
                  {formatSize(item.file.size)}
                </span>
              )}
            </span>
            <button
              type="button"
              className="ml-0.5 inline-flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => onRemove(item.id)}
              aria-label={`Remove ${item.file.name}`}
            >
              <X size={11} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default ComposerUploadChips
