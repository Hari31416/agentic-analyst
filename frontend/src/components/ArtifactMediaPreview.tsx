import { useEffect, useState } from 'react'
import { apiFetch } from '../lib/apiFetch'
import { ArtifactManifest, phase06Api } from '../phase06Api'

export function mediaViewerKind(
  artifact: ArtifactManifest,
): 'image' | 'html' | null {
  const media = artifact.media_type.toLowerCase().split(';')[0].trim()
  if (/^image\/(png|jpeg|gif|webp|avif|bmp|svg\+xml)$/.test(media))
    return 'image'
  if (media === 'text/html' || /\.html?$/i.test(artifact.display_name))
    return 'html'
  return null
}

/** A srcdoc has an opaque origin. Its policy permits only self-contained assets. */
export function prepareArtifactHtml(html: string): string {
  const document = new DOMParser().parseFromString(html, 'text/html')
  document
    .querySelectorAll('base, meta[http-equiv]')
    .forEach((node) => node.remove())
  const policy = document.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content =
    "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
  document.head.prepend(policy)
  return '<!doctype html>\n' + document.documentElement.outerHTML
}

export function ArtifactMediaPreview({
  artifact,
}: {
  artifact: ArtifactManifest
}) {
  const kind = mediaViewerKind(artifact)
  const [content, setContent] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    setContent('')
    setError('')
    async function load() {
      try {
        const maxBytes = 5_000_000
        if (artifact.byte_size > maxBytes)
          throw new Error(
            'Preview exceeds 5 MB. Download the complete artifact.',
          )
        const response = await apiFetch(phase06Api.previewUrl(artifact.id), {
          signal: controller.signal,
        })
        if (!response.ok)
          throw new Error(`Could not load preview (${response.status}).`)
        const blob = await response.blob()
        if (controller.signal.aborted) return
        if (blob.size > maxBytes)
          throw new Error(
            'Preview exceeds 5 MB. Download the complete artifact.',
          )
        const result =
          kind === 'html'
            ? prepareArtifactHtml(await blob.text())
            : (objectUrl = URL.createObjectURL(
                new Blob([blob], { type: artifact.media_type }),
              ))
        if (!controller.signal.aborted) setContent(result)
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not load preview.',
          )
      }
    }
    void load()
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [artifact.id, artifact.byte_size, artifact.media_type, kind])
  if (error)
    return (
      <div className="artifact-error" role="alert">
        {error}
      </div>
    )
  if (!content)
    return (
      <div className="artifact-empty" role="status">
        Loading preview…
      </div>
    )
  if (kind === 'html')
    return (
      <iframe
        className="artifact-html-frame"
        srcDoc={content}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        title={`${artifact.display_name} preview`}
      />
    )
  return (
    <img
      className="artifact-image-preview"
      src={content}
      alt={artifact.display_name}
      onError={() =>
        setError(
          'Could not decode this image. Download the artifact to inspect it.',
        )
      }
    />
  )
}
