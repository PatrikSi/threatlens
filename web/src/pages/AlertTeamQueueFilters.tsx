import { AlertQueueScopePicker } from './AlertTeamSelectors'
import type { AlertOccurrencesController } from './useAlertOccurrencesController'

export function AlertTeamQueueFilters({ controller }: { controller: AlertOccurrencesController }) {
  const { filters, updateFilters } = controller
  const scope = filters.teamId ? `team:${filters.teamId}` : filters.queueScope ?? 'all'
  const userId = controller.currentUserQuery.data?.id
  const assignment = filters.unassigned ? 'unassigned' : filters.assigneeUserId === userId && userId ? 'mine' : filters.assigneeUserId ? 'specific' : 'any'
  return <div className="mt-3 grid gap-3 rounded-lg border border-slate/20 p-3 sm:grid-cols-2 dark:border-white/10">
    <AlertQueueScopePicker value={scope} onChange={(value) => updateFilters({
      teamId: value.startsWith('team:') ? value.slice(5) : '', queueScope: value.startsWith('team:') ? 'team' : value as 'all' | 'personal' | 'team', ruleId: '',
    })} />
    <label className="text-sm font-semibold">Assignment
      <select className="mt-1 min-h-10 w-full rounded border bg-white px-2 dark:bg-[#072019]" value={assignment}
        onChange={(event) => updateFilters({ unassigned: event.target.value === 'unassigned', assigneeUserId: event.target.value === 'mine' ? userId ?? '' : '' })}>
        <option value="any">Any assignee</option><option value="unassigned">Unassigned</option>
        <option value="mine" disabled={!userId}>Assigned to me</option>
        {assignment === 'specific' && <option value="specific">Selected analyst ({filters.assigneeUserId?.slice(0, 8)})</option>}
      </select>
    </label>
    <div className="flex flex-wrap gap-4 text-sm sm:col-span-2">
      <label className="flex min-h-10 items-center gap-2"><input type="checkbox" checked={Boolean(filters.overdue)}
        onChange={(event) => updateFilters({ overdue: event.target.checked })} />Overdue only</label>
      <label className="flex min-h-10 items-center gap-2"><input type="checkbox" checked={Boolean(filters.escalated)}
        onChange={(event) => updateFilters({ escalated: event.target.checked })} />Escalated only</label>
      <span className="self-center text-xs">Assignment and deadlines apply to team queues. Filters are included in triage links.</span>
    </div>
  </div>
}
