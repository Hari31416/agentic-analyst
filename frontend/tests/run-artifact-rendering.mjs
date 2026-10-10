import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const output = await mkdtemp(join(tmpdir(), 'artifact-rendering-'))
try {
  for (const entry of [
    'artifact-rendering',
    'artifact-lifecycle',
    'artifact-visibility',
    'pins-lifecycle',
    'reports-lifecycle',
  ]) {
    const directory = join(output, entry)
    await build({
      logLevel: 'error',
      define: { 'process.env.NODE_ENV': JSON.stringify('development') },
      ssr: { noExternal: true },
      build: {
        ssr: `tests/${entry}.tsx`,
        outDir: directory,
        rolldownOptions: { output: { entryFileNames: 'check.mjs' } },
      },
    })
    await import(pathToFileURL(join(directory, 'check.mjs')).href)
  }
} finally {
  await rm(output, { recursive: true, force: true })
}

// React's development scheduler retains MessageChannel handles after tests finish.
process.exit(0)
