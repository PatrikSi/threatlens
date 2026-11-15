/** Keep private returned values bound to the read/selection that admitted them. */
export function admittedRecordValue<T extends { value: unknown }>(
  record: T | null,
  isAdmitted: (record: T) => unknown,
): T['value'] | null {
  return record && isAdmitted(record) ? record.value : null
}
