import { AlertQueueScopePicker } from './AlertTeamSelectors'
import type { AlertsPageController } from './useAlertsPageController'

export function AlertRuleTeamFields({ controller }: { controller: AlertsPageController }) {
  const disabled = controller.saveAlert.isPending
  return <fieldset className="space-y-3 rounded border border-slate/20 p-3 dark:border-white/10" disabled={disabled}>
    <legend className="px-1 text-sm font-semibold">Watchlist ownership and deadlines</legend>
    <AlertQueueScopePicker label="Watchlist owner" allowAll={false}
      value={controller.draftTeamId ? `team:${controller.draftTeamId}` : 'personal'}
      disabled={disabled || Boolean(controller.editingAlertId)}
      onChange={(value) => controller.setDraftTeamId(value.startsWith('team:') ? value.slice(5) : '')} />
    {controller.editingAlertId && <p className="text-xs">Ownership stays with the original person or team. Create a new watchlist to use another owner.</p>}
    {controller.draftTeamId && <>
      <label className="block text-sm">Due minutes after a new occurrence (optional)
        <input type="number" min={1} max={525600} value={controller.dueAfterMinutes} disabled={disabled}
          className="mt-1 block min-h-10 w-full rounded border bg-transparent px-2"
          onChange={(event) => controller.setDueAfterMinutes(event.target.value)} />
      </label>
      <label className="block text-sm">Escalate minutes after due time (optional)
        <input type="number" min={0} max={525600} value={controller.escalationAfterMinutes} disabled={disabled}
          className="mt-1 block min-h-10 w-full rounded border bg-transparent px-2"
          onChange={(event) => controller.setEscalationAfterMinutes(event.target.value)} />
      </label>
      <p className="text-xs">Applies to new occurrences. Existing deadlines remain unchanged. Escalations appear in the team queue and activity history.</p>
    </>}
  </fieldset>
}
