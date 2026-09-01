import { ConfirmDialog } from '../components/ConfirmDialog'
import { TaggingSettingsController } from './useTaggingSettingsController'

type TaggingDialogsProps = {
  controller: TaggingSettingsController
}

export function TaggingSettingsDialogs({ controller }: TaggingDialogsProps) {
  return (
    <>
      <ReapplyTaggingDialog controller={controller} />
      <DeleteRuleDialog controller={controller} />
      {controller.confirmDiscardUnsavedTaggingChanges.discardDialog}
    </>
  )
}

function ReapplyTaggingDialog({ controller }: TaggingDialogsProps) {
  const {
    onConfirmReapplyTagging,
    pendingReapplyRequest,
    reapplyTagging,
    setPendingReapplyRequest,
  } = controller

  return (
    <ConfirmDialog
      open={Boolean(pendingReapplyRequest)}
      title={pendingReapplyRequest?.limit === 0 ? 'Queue full retagging pass?' : 'Queue retagging pass?'}
      description="Review the scope before scheduling a bulk retagging job."
      confirmLabel={pendingReapplyRequest?.limit === 0 ? 'Queue full retagging' : 'Queue retagging'}
      confirmTone="primary"
      onCancel={() => setPendingReapplyRequest(null)}
      onConfirm={onConfirmReapplyTagging}
      confirmDisabled={!pendingReapplyRequest || reapplyTagging.isPending || !controller.canManageTagging}
      isConfirming={reapplyTagging.isPending}
    >
      {pendingReapplyRequest && (
        <div className="space-y-2 text-sm">
          <p>
            Time window:{' '}
            <span className="font-semibold text-ink dark:text-white">
              last {pendingReapplyRequest.days} day{pendingReapplyRequest.days === 1 ? '' : 's'}
            </span>
          </p>
          <p>
            Scope:{' '}
            <span className="font-semibold text-ink dark:text-white">
              {pendingReapplyRequest.limit === 0
                ? 'all items in the selected time window'
                : `up to ${pendingReapplyRequest.limit} recent item${pendingReapplyRequest.limit === 1 ? '' : 's'}`}
            </span>
          </p>
          {pendingReapplyRequest.limit === 0 && (
            <p className="text-amber-700 dark:text-amber-300">
              Limit 0 reprocesses every eligible item in the selected time window.
            </p>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}

function DeleteRuleDialog({ controller }: TaggingDialogsProps) {
  const { deleteRule, onConfirmDeleteRule, pendingRuleDelete, setPendingRuleDelete } = controller

  return (
    <ConfirmDialog
      open={Boolean(pendingRuleDelete)}
      title="Delete tagging rule?"
      description="This permanently removes the rule from auto-tagging."
      confirmLabel="Delete rule"
      onCancel={() => setPendingRuleDelete(null)}
      onConfirm={onConfirmDeleteRule}
      confirmDisabled={deleteRule.isPending || !controller.canManageTagging}
      isConfirming={deleteRule.isPending}
    >
      {pendingRuleDelete && (
        <div className="space-y-3">
          <p className="font-semibold text-ink dark:text-white">{pendingRuleDelete.name}</p>
          <p className="text-xs text-slate dark:text-white/70">Tag: {pendingRuleDelete.tag_name}</p>
          <p className="text-xs text-slate dark:text-white/70">
            Match: {pendingRuleDelete.match_type} on {pendingRuleDelete.applies_to.join(', ')}
          </p>
        </div>
      )}
    </ConfirmDialog>
  )
}
