import type { AlertOccurrence } from '../types/alerts'

export type AlertDeadlineDraft = { expectedVersion: number; dueAt: string; escalationMinutes: string }

export function deadlineDraft(occurrence: AlertOccurrence): AlertDeadlineDraft {
  const date = occurrence.due_at ? new Date(occurrence.due_at) : null
  const local = date && Number.isFinite(date.getTime())
    ? new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : ''
  return { expectedVersion: occurrence.version, dueAt: local, escalationMinutes: occurrence.escalation_after_minutes?.toString() ?? '' }
}

export function deadlinePayload(draft: AlertDeadlineDraft): { body?: Record<string, unknown>; error?: string } {
  const due = draft.dueAt ? new Date(draft.dueAt) : null
  if (due && !Number.isFinite(due.getTime())) return { error: 'Choose a valid due date and time.' }
  const delayError = validateAlertDeadlineMinutes(due ? '1' : '', draft.escalationMinutes)
  if (delayError) return { error: delayError }
  return { body: {
    expected_version: draft.expectedVersion,
    due_at: due?.toISOString() ?? null,
    escalation_after_minutes: draft.escalationMinutes === '' ? null : Number(draft.escalationMinutes),
  } }
}

export function validateAlertDeadlineMinutes(due: string, escalation: string): string | null {
  if (due && (!/^\d+$/.test(due) || Number(due) < 1 || Number(due) > 525600)) return 'Due delay must be a whole number from 1 to 525600 minutes.'
  if (escalation && !due) return 'Set a due time before configuring escalation.'
  if (escalation && (!/^\d+$/.test(escalation) || Number(escalation) > 525600)) return 'Escalation delay must be a whole number from 0 to 525600 minutes.'
  return null
}
