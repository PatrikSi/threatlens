import {
  CheckCircle2,
  CircleEllipsis,
  Clock3,
  OctagonX,
  PauseCircle,
  TriangleAlert,
} from 'lucide-react'

import type { LifecycleRunStatus } from '../types/lifecycle'
import { LIFECYCLE_RUN_STATUS_LABELS } from './lifecycleModel'

export function LifecycleRunStatusChip({ status }: { status: LifecycleRunStatus }) {
  const presentation = statusPresentation(status)
  const Icon = presentation.icon
  return (
    <span className={`tl-chip ${presentation.className}`} data-lifecycle-status={status}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {LIFECYCLE_RUN_STATUS_LABELS[status]}
    </span>
  )
}

export function LifecyclePolicyState({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span className="tl-chip tl-chip-success">
      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
      Enabled
    </span>
  ) : (
    <span className="tl-chip tl-chip-neutral">
      <PauseCircle className="h-3.5 w-3.5" aria-hidden="true" />
      Disabled
    </span>
  )
}

function statusPresentation(status: LifecycleRunStatus) {
  if (status === 'succeeded') {
    return { icon: CheckCircle2, className: 'tl-chip-success' }
  }
  if (status === 'failed') {
    return { icon: OctagonX, className: 'tl-chip-danger' }
  }
  if (status === 'partial') {
    return { icon: TriangleAlert, className: 'tl-chip-warning' }
  }
  if (status === 'cancelled') {
    return { icon: PauseCircle, className: 'tl-chip-neutral' }
  }
  if (status === 'running') {
    return { icon: CircleEllipsis, className: 'tl-chip-warning' }
  }
  return { icon: Clock3, className: 'tl-chip-neutral' }
}
