export function disableWhen(...conditions: boolean[]): boolean {
  return conditions.some(Boolean)
}
