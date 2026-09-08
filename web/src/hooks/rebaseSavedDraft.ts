/** Apply accepted server values only to fields unchanged since this submission. */
export function rebaseSavedDraft<T extends object>(submitted: T, current: T, saved: T): T {
  const next = { ...saved }
  for (const key of Object.keys(current) as (keyof T)[]) {
    if (JSON.stringify(current[key]) !== JSON.stringify(submitted[key])) next[key] = current[key]
  }
  return next
}
