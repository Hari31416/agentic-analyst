import assert from 'node:assert/strict'
import { MarkdownRenderer } from '../src/components/MarkdownRenderer'

const id = '12345678-1234-1234-1234-123456789abc'
// Composer rerenders supply new arrays and callbacks. Mounted images must survive.
const { act, create } = await import('react-test-renderer')
const requests: string[] = []
const originalFetch = globalThis.fetch
const originalRevoke = URL.revokeObjectURL
const revoked: string[] = []
URL.revokeObjectURL = (url) => {
  revoked.push(url)
  originalRevoke(url)
}
globalThis.fetch = async (url) => {
  requests.push(String(url))
  if (String(url).endsWith('/content'))
    return new Response(new Blob(['image'], { type: 'image/png' }))
  return Response.json({
    id,
    display_name: 'plot.png',
    artifact_type: 'image',
    media_type: 'image/png',
    byte_size: 5,
  })
}
let renderer: ReturnType<typeof create>
let clicked = ''
const element = (version: string) => (
  <MarkdownRenderer
    content={`![Plot](artifact:${id})\n\n[evidence:${id}]`}
    artifactIds={[id]}
    evidenceIds={[id]}
    onOpenEvidence={() => {
      clicked = version
    }}
  />
)
try {
  await act(async () => {
    renderer = create(element('first'))
  })
  const imageUrl = renderer!.root.findByType('img').props.src
  assert.equal(requests.length, 2)
  await act(async () => {
    renderer!.update(element('next'))
  })
  assert.equal(requests.length, 2)
  assert.equal(renderer!.root.findByType('img').props.src, imageUrl)
  assert.equal(revoked.length, 0)
  renderer!.root
    .findAllByType('button')
    .find((button) => button.props.title === 'Open cited evidence')!
    .props.onClick()
  assert.equal(clicked, 'next')
  await act(async () => {
    renderer!.unmount()
  })
  assert.deepEqual(revoked, [imageUrl])
} finally {
  globalThis.fetch = originalFetch
  URL.revokeObjectURL = originalRevoke
}
console.log('Artifact lifecycle regression passed')
