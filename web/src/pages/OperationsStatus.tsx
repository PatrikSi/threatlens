import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Clock3,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import type {
  OperationsStatus,
  SystemOperationStatus,
} from '../types/operations'
import { formatWireLabel } from './operationsHealthPresentation'

export function OperationsStatusChip({
  status,
  label,
}: {
  status: OperationsStatus
  label?: string
}) {
  const presentation = statusPresentation(status)
  const Icon = presentation.icon
  return (
    <span className={`tl-chip ${presentation.chipClassName}`} data-status={status}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {label ?? formatWireLabel(status)}
    </span>
  )
}

export function OperationRunStatusChip({ status }: { status: SystemOperationStatus }) {
  const presentation = status === 'succeeded'
    ? { icon: CheckCircle2, chipClassName: 'tl-chip-success' }
    : status === 'failed'
      ? { icon: XCircle, chipClassName: 'tl-chip-danger' }
      : { icon: Clock3, chipClassName: 'tl-chip-warning' }
  const Icon = presentation.icon
  return (
    <span className={`tl-chip ${presentation.chipClassName}`} data-operation-status={status}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {formatWireLabel(status)}
    </span>
  )
}

export function OperationsIssueSeverityChip({ severity }: { severity: 'warning' | 'critical' }) {
  const critical = severity === 'critical'
  const Icon = critical ? XCircle : AlertTriangle
  return (
    <span className={`tl-chip ${critical ? 'tl-chip-danger' : 'tl-chip-warning'}`} data-issue-severity={severity}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {formatWireLabel(severity)}
    </span>
  )
}

export function OperationsStatusGlyph({
  status,
  className = '',
}: {
  status: OperationsStatus
  className?: string
}) {
  const presentation = statusPresentation(status)
  const Icon = presentation.icon
  return <Icon className={`${presentation.iconClassName} ${className}`} aria-hidden="true" />
}

function statusPresentation(status: OperationsStatus): {
  icon: LucideIcon
  chipClassName: string
  iconClassName: string
} {
  if (status === 'healthy') {
    return {
      icon: CheckCircle2,
      chipClassName: 'tl-chip-success',
      iconClassName: 'text-emerald-600 dark:text-emerald-400',
    }
  }
  if (status === 'degraded') {
    return {
      icon: AlertTriangle,
      chipClassName: 'tl-chip-warning',
      iconClassName: 'text-amber-600 dark:text-amber-400',
    }
  }
  if (status === 'critical') {
    return {
      icon: XCircle,
      chipClassName: 'tl-chip-danger',
      iconClassName: 'text-red-600 dark:text-red-400',
    }
  }
  if (status === 'unavailable') {
    return {
      icon: CircleHelp,
      chipClassName: 'tl-chip-warning',
      iconClassName: 'text-amber-600 dark:text-amber-400',
    }
  }
  return {
    icon: CircleHelp,
    chipClassName: 'tl-chip-neutral',
    iconClassName: 'text-slate dark:text-slate-400',
  }
}
