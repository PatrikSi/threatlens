import { ApiError } from '../api/client'
import type {
  InvestigationAccountRole,
  InvestigationDetail,
  InvestigationDetailTab,
  InvestigationEvidenceType,
  InvestigationListFilters,
  InvestigationMember,
  InvestigationMemberRole,
  InvestigationSeverity,
  InvestigationStatus,
} from '../types/investigations'

export const INVESTIGATION_PAGE_SIZE = 25
export const INVESTIGATION_ACTIVITY_PAGE_SIZE = 25
export const INVESTIGATION_MEMBER_PAGE_SIZE = 20
export const INVESTIGATION_EVIDENCE_PAGE_SIZE = 25
export const INVESTIGATION_NOTE_PAGE_SIZE = 25

export const INVESTIGATION_STATUSES: ReadonlyArray<{ value: InvestigationStatus; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'monitoring', label: 'Monitoring' },
  { value: 'closed', label: 'Closed' },
  { value: 'archived', label: 'Archived' },
]

export const INVESTIGATION_SEVERITIES: ReadonlyArray<{ value: InvestigationSeverity; label: string }> = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

export const INVESTIGATION_EVIDENCE_TYPES: ReadonlyArray<{
  value: InvestigationEvidenceType
  label: string
  idLabel: string
}> = [
  { value: 'item', label: 'Article', idLabel: 'Article ID' },
  { value: 'ioc', label: 'IOC', idLabel: 'IOC ID' },
  { value: 'report', label: 'Intelligence report', idLabel: 'Report ID' },
  { value: 'alert_occurrence', label: 'Alert occurrence', idLabel: 'Alert occurrence ID' },
]

export const INVESTIGATION_TABS: ReadonlyArray<{ value: InvestigationDetailTab; label: string }> = [
  { value: 'overview', label: 'Overview' },
  { value: 'members', label: 'Members' },
  { value: 'evidence', label: 'Evidence' },
  { value: 'notes', label: 'Notes' },
  { value: 'activity', label: 'Activity' },
]

const STATUS_VALUES = new Set<InvestigationStatus>(INVESTIGATION_STATUSES.map(({ value }) => value))
const SEVERITY_VALUES = new Set<InvestigationSeverity>(INVESTIGATION_SEVERITIES.map(({ value }) => value))
const TAB_VALUES = new Set<InvestigationDetailTab>(INVESTIGATION_TABS.map(({ value }) => value))

export interface InvestigationAccess {
  canAuthor: boolean
  canWrite: boolean
  canManageMembers: boolean
  canArchive: boolean
  canReopen: boolean
  readOnlyReason: string | null
}

export function resolveInvestigationAccess(
  investigation: InvestigationDetail,
  accountRole: InvestigationAccountRole | undefined,
): InvestigationAccess {
  const canAuthor = accountRole === 'admin' || accountRole === 'analyst'
  const isWriter = investigation.current_user_role === 'owner' || investigation.current_user_role === 'editor'
  const isOwner = investigation.current_user_role === 'owner'
  const archived = investigation.status === 'archived'
  let readOnlyReason: string | null = null

  if (!canAuthor) {
    readOnlyReason = 'Your ThreatLens account role has read-only access to investigations.'
  } else if (investigation.current_user_role === null) {
    readOnlyReason = 'This team-visible investigation is read-only until an owner adds you as a member.'
  } else if (investigation.current_user_role === 'viewer') {
    readOnlyReason = 'Your investigation membership is read-only.'
  } else if (archived) {
    readOnlyReason = 'Archived investigations are read-only until they are reopened.'
  }

  return {
    canAuthor,
    canWrite: canAuthor && isWriter && !archived,
    canManageMembers: canAuthor && isOwner && !archived,
    canArchive: canAuthor && isOwner && !archived,
    canReopen: canAuthor && isWriter && archived,
    readOnlyReason,
  }
}

export function canEditInvestigationNote(
  noteAuthorUserId: string | null,
  currentUserId: string | undefined,
  memberRole: InvestigationMemberRole | null,
): boolean {
  return memberRole === 'owner' || Boolean(noteAuthorUserId && currentUserId && noteAuthorUserId === currentUserId)
}

export function isFinalInvestigationOwner(members: InvestigationMember[], userId: string): boolean {
  return members.find((member) => member.user_id === userId)?.role === 'owner'
    && members.filter((member) => member.role === 'owner').length === 1
}

export function readInvestigationListFilters(searchParams: URLSearchParams): InvestigationListFilters {
  const statuses = uniqueValues(
    searchParams.getAll('status').filter((value): value is InvestigationStatus => STATUS_VALUES.has(value as InvestigationStatus)),
  )
  const severities = uniqueValues(
    searchParams
      .getAll('severity')
      .filter((value): value is InvestigationSeverity => SEVERITY_VALUES.has(value as InvestigationSeverity)),
  )
  const rawPage = Number(searchParams.get('page'))
  return {
    query: (searchParams.get('q') ?? '').slice(0, 255),
    statuses,
    severities,
    assignedToMe: searchParams.get('mine') === '1',
    includeArchived: searchParams.get('archived') === '1',
    page: Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1,
  }
}

export function writeInvestigationListFilters(filters: InvestigationListFilters): URLSearchParams {
  const params = new URLSearchParams()
  const query = filters.query.trim()
  if (query) params.set('q', query)
  filters.statuses.forEach((status) => params.append('status', status))
  filters.severities.forEach((severity) => params.append('severity', severity))
  if (filters.assignedToMe) params.set('mine', '1')
  if (filters.includeArchived) params.set('archived', '1')
  if (filters.page > 1) params.set('page', String(filters.page))
  return params
}

export function buildInvestigationListPath(filters: InvestigationListFilters): string {
  const params = new URLSearchParams({ page: String(filters.page), page_size: String(INVESTIGATION_PAGE_SIZE) })
  const query = filters.query.trim()
  if (query) params.set('q', query)
  filters.statuses.forEach((status) => params.append('statuses', status))
  filters.severities.forEach((severity) => params.append('severities', severity))
  if (filters.assignedToMe) params.set('assigned_to_me', 'true')
  if (filters.includeArchived || filters.statuses.includes('archived')) params.set('include_archived', 'true')
  return `/investigations?${params.toString()}`
}

export function readInvestigationTab(searchParams: URLSearchParams): InvestigationDetailTab {
  const value = searchParams.get('tab')
  return value && TAB_VALUES.has(value as InvestigationDetailTab) ? (value as InvestigationDetailTab) : 'overview'
}

export function formatInvestigationStatus(status: InvestigationStatus): string {
  return INVESTIGATION_STATUSES.find(({ value }) => value === status)?.label ?? status
}

export function formatInvestigationSeverity(severity: InvestigationSeverity): string {
  return INVESTIGATION_SEVERITIES.find(({ value }) => value === severity)?.label ?? severity
}

export function formatEvidenceType(type: InvestigationEvidenceType): string {
  return INVESTIGATION_EVIDENCE_TYPES.find(({ value }) => value === type)?.label ?? type
}

const ACTIVITY_LABELS: Record<string, string> = {
  'investigation.created': 'Created the investigation',
  'investigation.updated': 'Updated the investigation',
  'investigation.member_added': 'Added an investigation member',
  'investigation.member_updated': 'Changed a member role',
  'investigation.member_removed': 'Removed an investigation member',
  'investigation.evidence_added': 'Added evidence',
  'investigation.evidence_removed': 'Removed evidence',
  'investigation.note_added': 'Added a note',
  'investigation.note_updated': 'Updated a note',
  'investigation.note_removed': 'Removed a note',
}

export function formatInvestigationActivityAction(action: string): string {
  const known = ACTIVITY_LABELS[action]
  if (known) return known
  const readable = action.split('.').at(-1)?.replaceAll('_', ' ').trim() ?? ''
  return readable ? `${readable.charAt(0).toUpperCase()}${readable.slice(1)}` : 'Recorded activity'
}

export function isInvestigationVersionConflict(error: unknown): error is ApiError {
  if (!(error instanceof ApiError) || error.status !== 409) return false
  return error.code === 'investigation_version_conflict'
    || error.code === 'investigation_note_version_conflict'
    || (error.code === null && /changed after you loaded/i.test(error.message))
}

export function isTerminalInvestigationAccessError(error: unknown): error is ApiError {
  return error instanceof ApiError && [401, 403, 404].includes(error.status)
}

export const INVESTIGATION_CONFLICT_MESSAGE =
  'This investigation changed after you loaded it. Refresh and review the latest version before retrying. Your unsaved text has been preserved.'

export function isAlertOccurrenceUnavailable(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const candidate = error as Partial<ApiError>
  return candidate.status === 422 && /alert occurrence evidence is unavailable/i.test(error.message)
}

export function investigationResultRange(total: number, page: number, pageSize: number, count: number): string {
  if (total === 0 || count === 0) return '0 results'
  const first = (page - 1) * pageSize + 1
  const last = Math.min(total, first + count - 1)
  return `${first}-${last} of ${total}`
}

export function investigationCollectionPageCount(total: number, pageSize: number): number {
  if (!Number.isFinite(total) || total <= 0) return 1
  if (!Number.isFinite(pageSize) || pageSize <= 0) return 1
  return Math.max(1, Math.ceil(total / pageSize))
}

export function safeInvestigationExternalUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function uniqueValues<T extends string>(values: T[]): T[] {
  return Array.from(new Set(values))
}
