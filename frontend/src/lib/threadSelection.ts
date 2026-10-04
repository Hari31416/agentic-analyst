import type { AnalysisRun } from '../chatApi'

export type ThreadSelection = { sourceIds: string[]; datasetIds: string[] }

export function restoreThreadSelection(
  runs: AnalysisRun[],
  saved: unknown,
): ThreadSelection {
  if (
    saved &&
    typeof saved === 'object' &&
    'sourceIds' in saved &&
    'datasetIds' in saved &&
    Array.isArray(saved.sourceIds) &&
    Array.isArray(saved.datasetIds) &&
    saved.sourceIds.every((id) => typeof id === 'string') &&
    saved.datasetIds.every((id) => typeof id === 'string')
  ) {
    return { sourceIds: saved.sourceIds, datasetIds: saved.datasetIds }
  }
  // Failed legacy follow-ups could have lost selection; resume the last answered turn.
  const previous =
    runs.find(
      (run) => run.state === 'completed' || run.state === 'needs_input',
    ) ?? runs[0]
  return {
    sourceIds: previous?.selected_source_ids ?? [],
    datasetIds: previous?.selected_dataset_ids ?? [],
  }
}
