import { ArtifactManifest } from '../phase06Api'

export function filterVisibleArtifacts(
  artifacts: ArtifactManifest[],
  showIntermediate: boolean,
  query = '',
): ArtifactManifest[] {
  const visible = showIntermediate
    ? artifacts
    : artifacts.filter((artifact) => artifact.role === 'output')
  const normalized = query.trim().toLowerCase()
  if (!normalized) return visible
  return visible.filter((artifact) =>
    [artifact.display_name, artifact.artifact_type, artifact.media_type].some(
      (value) => value.toLowerCase().includes(normalized),
    ),
  )
}

export function outputArtifactCount(artifacts: ArtifactManifest[]): number {
  return filterVisibleArtifacts(artifacts, false).length
}
