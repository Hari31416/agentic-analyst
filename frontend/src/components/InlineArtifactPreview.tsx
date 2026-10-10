import { useEffect, useState } from 'react'
import { Download, ExternalLink, Eye } from 'lucide-react'
import { PinButton } from './PinButton'
import { ArtifactDetail } from '../ArtifactBrowser'
import { ArtifactManifest, ArtifactRows, phase06Api } from '../phase06Api'
import { chatApi } from '../chatApi'
import { ArtifactMediaPreview, mediaViewerKind } from './ArtifactMediaPreview'
import { Dialog, DialogContent, DialogTitle } from './ui/dialog'
import '../artifact-browser.css'

export function InlineArtifactPreview({
  id,
  caption,
  referenceOnly = false,
  initiallyExpanded = true,
}: {
  id: string
  caption?: string
  referenceOnly?: boolean
  initiallyExpanded?: boolean
}) {
  const [artifact, setArtifact] = useState<ArtifactManifest | null>(null)
  const [rows, setRows] = useState<ArtifactRows | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(initiallyExpanded && !referenceOnly)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!expanded && !open) return
    let active = true
    setError('')
    setArtifact(null)
    setRows(null)
    setText(null)
    async function load() {
      try {
        const item = await phase06Api.artifact(id)
        if (!active) return
        setArtifact(item)
        if (item.artifact_type === 'table') {
          const result = await phase06Api.rows(id, 0, 10)
          if (active) setRows(result)
        } else if (!mediaViewerKind(item)) {
          const result = await chatApi.previewArtifact(id, 4000)
          if (active) setText(result.text)
        }
      } catch (reason) {
        if (active)
          setError(
            reason instanceof Error ? reason.message : 'Artifact unavailable.',
          )
      }
    }
    void load()
    return () => {
      active = false
    }
  }, [id, expanded, open])
  const label =
    caption || artifact?.display_name || `Artifact ${id.slice(0, 8)}`
  const dialog = (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        className="artifact-preview-dialog"
        aria-describedby={undefined}
      >
        <DialogTitle>{artifact?.display_name ?? label}</DialogTitle>
        {error ? (
          <div className="artifact-error" role="alert">
            {error}
          </div>
        ) : artifact ? (
          <ArtifactDetail key={artifact.id} artifact={artifact} />
        ) : (
          <div role="status">Loading artifact…</div>
        )}
      </DialogContent>
    </Dialog>
  )
  if (referenceOnly)
    return (
      <>
        <button
          type="button"
          className="inline-citation"
          onClick={() => setOpen(true)}
          title="Open artifact viewer"
        >
          {label}
        </button>
        {dialog}
      </>
    )
  return (
    <figure className="inline-artifact-preview">
      <div className="inline-artifact-header">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          <Eye size={14} />
          {expanded ? 'Hide preview' : label}
        </button>
        <span className="inline-artifact-actions">
          <PinButton kind="artifact" targetId={id} title={label} />
          <button
            type="button"
            onClick={() => setOpen(true)}
            title="Open artifact viewer"
          >
            <ExternalLink size={14} /> Open
          </button>
          <a
            href={phase06Api.downloadUrl(id)}
            download
            title="Download artifact"
          >
            <Download size={14} />
            <span className="sr-only">Download {label}</span>
          </a>
        </span>
      </div>
      {expanded && (
        <div className="inline-artifact-body">
          {error ? (
            <div className="artifact-error" role="alert">
              {error}
            </div>
          ) : !artifact ? (
            <div className="artifact-empty" role="status">
              Loading artifact…
            </div>
          ) : mediaViewerKind(artifact) ? (
            <ArtifactMediaPreview artifact={artifact} />
          ) : artifact.artifact_type === 'table' ? (
            rows ? (
              <>
                <div className="artifact-table-scroll">
                  <table className="artifact-data-table">
                    <thead>
                      <tr>
                        {rows.columns.map((column) => (
                          <th key={column.name}>{column.name}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.rows.map((row, index) => (
                        <tr key={index}>
                          {rows.columns.map((column) => (
                            <td key={column.name}>
                              {String(row[column.name] ?? '')}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="inline-artifact-note">
                  {rows.rows.length} of {rows.total_rows} rows
                  {rows.truncated ? ' · result truncated' : ''}. Open to browse
                  all available rows.
                </p>
              </>
            ) : (
              <div className="artifact-empty" role="status">
                Loading table…
              </div>
            )
          ) : text !== null ? (
            <pre className="inline-artifact-text">{text}</pre>
          ) : (
            <div className="artifact-empty">
              Open or download this artifact to inspect it.
            </div>
          )}
        </div>
      )}
      {expanded && caption && (
        <figcaption className="inline-artifact-caption">{caption}</figcaption>
      )}
      {dialog}
    </figure>
  )
}
