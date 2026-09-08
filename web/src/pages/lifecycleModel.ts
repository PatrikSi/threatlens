import type {
  LifecycleCategory,
  LifecyclePolicy,
  LifecyclePolicyDraft,
  LifecyclePreview,
  LifecycleRunStatus,
  LifecycleTarget,
} from '../types/lifecycle'
import { lifecyclePreviewLocalExpiresAt } from './lifecyclePreviewClock'

export const LIFECYCLE_CATEGORY_ORDER: readonly LifecycleCategory[] = [
  'intelligence',
  'detection',
  'integrations',
  'security',
  'governance',
  'system',
]

export const LIFECYCLE_CATEGORY_LABELS: Record<LifecycleCategory, string> = {
  intelligence: 'Intelligence',
  detection: 'Detection',
  integrations: 'Integrations',
  security: 'Security',
  governance: 'Governance',
  system: 'System',
}

export const LIFECYCLE_WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const

export const LIFECYCLE_RUN_STATUS_LABELS: Record<LifecycleRunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  partial: 'Partially completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function lifecycleDraftFromPolicy(
  policy: LifecyclePolicy,
): LifecyclePolicyDraft {
  return {
    enabled: policy.enabled,
    retention_days: policy.retention_days,
    schedule_cadence: policy.schedule_cadence,
    schedule_hour_utc: policy.schedule_hour_utc,
    schedule_weekday: policy.schedule_weekday,
    max_records_per_run: policy.max_records_per_run,
    options: { ...policy.options },
  }
}

export function lifecycleDraftIsEqual(
  left: LifecyclePolicyDraft,
  right: LifecyclePolicyDraft,
): boolean {
  return left.enabled === right.enabled
    && left.retention_days === right.retention_days
    && left.schedule_cadence === right.schedule_cadence
    && left.schedule_hour_utc === right.schedule_hour_utc
    && left.schedule_weekday === right.schedule_weekday
    && left.max_records_per_run === right.max_records_per_run
    && sortedOptions(left.options) === sortedOptions(right.options)
}

export function lifecycleChangeIsDestructive(
  baseline: LifecyclePolicyDraft,
  draft: LifecyclePolicyDraft,
): boolean {
  if (draft.enabled && !baseline.enabled) return true
  if (draft.retention_days < baseline.retention_days) return true
  if (baseline.enabled
    && draft.enabled
    && draft.max_records_per_run > baseline.max_records_per_run) return true
  return Object.entries(baseline.options).some(
    ([key, enabled]) => enabled && draft.options[key] === false,
  )
}

export function lifecycleDraftError(
  target: LifecycleTarget,
  draft: LifecyclePolicyDraft,
): string | null {
  if (!Number.isInteger(draft.retention_days)
    || draft.retention_days < target.min_retention_days
    || draft.retention_days > target.max_retention_days) {
    return `Retention must be a whole number from ${target.min_retention_days.toLocaleString()} to ${target.max_retention_days.toLocaleString()} days.`
  }
  if (!Number.isInteger(draft.schedule_hour_utc)
    || draft.schedule_hour_utc < 0
    || draft.schedule_hour_utc > 23) {
    return 'Schedule hour must be a whole number from 0 to 23 UTC.'
  }
  if (draft.schedule_cadence === 'weekly'
    && (draft.schedule_weekday == null
      || !Number.isInteger(draft.schedule_weekday)
      || draft.schedule_weekday < 0
      || draft.schedule_weekday > 6)) {
    return 'Choose a weekday for the weekly schedule.'
  }
  if (!Number.isInteger(draft.max_records_per_run)
    || draft.max_records_per_run < 100
    || draft.max_records_per_run > 100_000) {
    return 'Run limit must be a whole number from 100 to 100,000 records.'
  }
  return null
}

export function lifecycleScheduleLabel(policy: LifecyclePolicy): string {
  return lifecycleDraftScheduleLabel(policy)
}

export function lifecycleDraftScheduleLabel(
  draft: Pick<
    LifecyclePolicyDraft,
    'schedule_cadence' | 'schedule_hour_utc' | 'schedule_weekday'
  >,
): string {
  const time = `${String(draft.schedule_hour_utc).padStart(2, '0')}:00 UTC`
  if (draft.schedule_cadence === 'weekly') {
    return `${LIFECYCLE_WEEKDAYS[draft.schedule_weekday ?? 0]} at ${time}`
  }
  return `Daily at ${time}`
}

export function lifecycleNextScheduleAt(
  draft: Pick<
    LifecyclePolicyDraft,
    'schedule_cadence' | 'schedule_hour_utc' | 'schedule_weekday'
  >,
  after = new Date(),
): Date {
  const next = new Date(after.getTime())
  next.setUTCMinutes(0, 0, 0)
  next.setUTCHours(draft.schedule_hour_utc)
  if (draft.schedule_cadence === 'daily') {
    if (next.getTime() <= after.getTime()) next.setUTCDate(next.getUTCDate() + 1)
    return next
  }

  const backendWeekday = draft.schedule_weekday ?? 0
  const jsWeekday = (backendWeekday + 1) % 7
  next.setUTCDate(next.getUTCDate() + ((jsWeekday - next.getUTCDay() + 7) % 7))
  if (next.getTime() <= after.getTime()) next.setUTCDate(next.getUTCDate() + 7)
  return next
}

export function lifecyclePreviewCountLabel(
  count: number,
  preview: Pick<LifecyclePreview, 'count_is_lower_bound' | 'is_partial'>,
): string {
  const prefix = preview.count_is_lower_bound || preview.is_partial ? 'At least ' : ''
  return `${prefix}${count.toLocaleString()}`
}

export function lifecyclePreviewMatchesDraft(
  preview: LifecyclePreview | null,
  previewedDraft: LifecyclePolicyDraft | null,
  draft: LifecyclePolicyDraft,
): boolean {
  if (!preview || !previewedDraft) return false
  return lifecycleDraftIsEqual(draft, previewedDraft)
}

export function lifecyclePreviewIsFreshForDraft({
  preview,
  previewMatchesDraft,
  policyRevision,
  now = Date.now(),
}: {
  preview: LifecyclePreview | null
  previewMatchesDraft: boolean
  policyRevision: number
  now?: number
}): boolean {
  if (!preview || !previewMatchesDraft) return false
  return preview.policy_revision === policyRevision
    && lifecyclePreviewLocalExpiresAt(preview) > now
}

export function groupLifecycleTargets(targets: LifecycleTarget[]) {
  return LIFECYCLE_CATEGORY_ORDER.map((category) => ({
    category,
    label: LIFECYCLE_CATEGORY_LABELS[category],
    targets: targets.filter((target) => target.category === category),
  })).filter((group) => group.targets.length > 0)
}

function sortedOptions(options: Record<string, boolean>): string {
  return JSON.stringify(
    Object.entries(options).sort(([left], [right]) => left.localeCompare(right)),
  )
}
