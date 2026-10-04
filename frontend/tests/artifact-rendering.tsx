import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'
import { MarkdownRenderer } from '../src/components/MarkdownRenderer'

const id = '12345678-1234-1234-1234-123456789abc'
const render = (content: string, artifactIds = [id]) =>
  renderToStaticMarkup(
    <MarkdownRenderer content={content} artifactIds={artifactIds} />,
  )
assert.match(render(`![Result](artifact:${id})`), /inline-artifact-preview/)
assert.match(render(`[Result](artifact:${id})`), /Open artifact viewer/)
assert.match(render(`[artifact:${id}]`), /Open artifact viewer/)
assert.match(
  render(`![Result](artifact:${id.toUpperCase()})`),
  /inline-artifact-preview/,
)
assert.doesNotMatch(
  render(`![Result](artifact:${id})`, []),
  /inline-artifact-preview/,
)
assert.doesNotMatch(
  render(`\`![Result](artifact:${id})\``),
  /inline-artifact-preview/,
)
assert.doesNotMatch(
  render('![Result](javascript:alert%281%29)'),
  /src="javascript:/,
)
assert.match(
  render('[Docs](https://example.com)'),
  /href="https:\/\/example.com"/,
)
assert.doesNotMatch(render(`![Result](artifact:${id})`), /<p[^>]*><figure/)
assert.match(render('Normal paragraph'), /<p[^>]*>Normal paragraph<\/p>/)
assert.match(render(`![Result](${id})`), /inline-artifact-preview/)
assert.match(render(`[Result](${id})`), /Open artifact viewer/)
assert.doesNotMatch(
  render(`![Result](${id})`, []),
  /<img|inline-artifact-preview/,
)
assert.doesNotMatch(
  render(`![Result](artifact:${id})`, []),
  /<img|inline-artifact-preview/,
)
console.log('14 artifact rendering checks passed')

const { restoreThreadSelection } = await import('../src/lib/threadSelection')
const runs = [
  { state: 'failed', selected_source_ids: [], selected_dataset_ids: [] },
  {
    state: 'completed',
    selected_source_ids: ['iris'],
    selected_dataset_ids: ['sheet'],
  },
] as any
assert.deepEqual(restoreThreadSelection(runs, null), {
  sourceIds: ['iris'],
  datasetIds: ['sheet'],
})
assert.deepEqual(
  restoreThreadSelection(runs, { sourceIds: [], datasetIds: [] }),
  { sourceIds: [], datasetIds: [] },
)
assert.deepEqual(restoreThreadSelection([], null), {
  sourceIds: [],
  datasetIds: [],
})
assert.deepEqual(
  restoreThreadSelection(runs, { sourceIds: [123], datasetIds: [] }),
  { sourceIds: ['iris'], datasetIds: ['sheet'] },
)
console.log('4 thread selection checks passed')
