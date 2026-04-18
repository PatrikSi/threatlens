export function resolveVisibleRunSelection(
  runs: Array<{ id: string }> | undefined,
  selectedRunId: string | null,
) {
  if (!runs?.length) {
    return null
  }
  if (selectedRunId) {
    return selectedRunId
  }
  return runs[0].id
}
