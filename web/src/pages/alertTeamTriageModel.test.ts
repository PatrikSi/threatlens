import { describe, expect, it } from 'vitest'
import { deadlinePayload, validateAlertDeadlineMinutes } from './alertTeamTriageModel'
import { readAlertUrlState, writeAlertUrlState } from './alertUrlState'
import { buildAlertOccurrencesPath } from './alertOccurrenceModel'

describe('team queue query and deadline contracts', () => {
  it('round-trips shared triage ownership, assignment and urgency context into the API request', () => {
    const params = new URLSearchParams('view=occurrences&team_id=team-1&queue_scope=team&assignee_user_id=analyst-2&overdue=true&escalated=true&occurrence=occurrence-1')
    const state = readAlertUrlState(params)
    const shared = writeAlertUrlState(new URLSearchParams('view=occurrences'), state)
    expect(readAlertUrlState(shared)).toEqual(state)
    const path = buildAlertOccurrencesPath(state.filters, 2, 25)
    expect(path).toContain('team_id=team-1')
    expect(path).toContain('queue_scope=team')
    expect(path).toContain('assignee_user_id=analyst-2')
    expect(path).toContain('overdue=true')
    expect(path).toContain('escalated=true')
  })
  it('preserves zero-minute escalation and sends the draft baseline version', () => {
    expect(deadlinePayload({ expectedVersion: 6, dueAt: '2030-01-01T12:00', escalationMinutes: '0' })).toEqual({ body: {
      expected_version: 6, due_at: new Date('2030-01-01T12:00').toISOString(), escalation_after_minutes: 0,
    } })
    expect(deadlinePayload({ expectedVersion: 6, dueAt: '', escalationMinutes: '' })).toEqual({ body: {
      expected_version: 6, due_at: null, escalation_after_minutes: null,
    } })
  })
  it.each([['0', ''], ['1.5', ''], ['525601', ''], ['', '1'], ['10', '-1'], ['10', '525601']])('rejects invalid rule deadline budgets (%s / %s)', (due, escalation) => {
    expect(validateAlertDeadlineMinutes(due, escalation)).not.toBeNull()
  })
})
