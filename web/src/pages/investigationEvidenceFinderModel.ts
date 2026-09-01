import type {
  InvestigationEvidenceCandidate,
  InvestigationEvidenceCandidateListResponse,
  InvestigationEvidenceCandidateRange,
  InvestigationEvidenceSourceCapability,
  InvestigationEvidenceType,
} from '../types/investigations'
import { hasRequiredPermissions } from '../workspace/workspaceModel'

export const INVESTIGATION_EVIDENCE_CANDIDATE_PAGE_SIZE = 20
export const INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH = 3
export const INVESTIGATION_EVIDENCE_MAX_PAGES = 20

export const INVESTIGATION_EVIDENCE_RANGES: ReadonlyArray<{
  value: InvestigationEvidenceCandidateRange
  label: string
}> = [
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
]

export const INVESTIGATION_EVIDENCE_SOURCE_OPTIONS: ReadonlyArray<{
  value: InvestigationEvidenceType
  label: string
  pluralLabel: string
  requiredPermissions: readonly string[]
}> = [
  {
    value: 'item',
    label: 'Article',
    pluralLabel: 'Articles',
    requiredPermissions: ['read:items'],
  },
  {
    value: 'ioc',
    label: 'Indicator',
    pluralLabel: 'Indicators',
    requiredPermissions: ['read:items'],
  },
  {
    value: 'report',
    label: 'Intelligence report',
    pluralLabel: 'Reports',
    requiredPermissions: ['read:reports'],
  },
  {
    value: 'alert_occurrence',
    label: 'My alert occurrence',
    pluralLabel: 'My alerts',
    requiredPermissions: ['read:alerts', 'read:items'],
  },
]

export type InvestigationEvidenceSourceFilter = 'all' | InvestigationEvidenceType

export interface InvestigationEvidenceSourceAvailability {
  sourceType: InvestigationEvidenceType
  available: boolean
  reason: string | null
}

export function evidenceSourceAvailability(
  grantedPermissions: readonly string[] | undefined,
  serverCapabilities: InvestigationEvidenceSourceCapability[] | undefined,
  alertOccurrenceUnavailable: boolean,
): InvestigationEvidenceSourceAvailability[] {
  const serverByType = new Map(
    (serverCapabilities ?? []).map((capability) => [capability.source_type, capability]),
  )
  return INVESTIGATION_EVIDENCE_SOURCE_OPTIONS.map((option) => {
    const missingPermissions = option.requiredPermissions.filter(
      (permission) => !hasRequiredPermissions(grantedPermissions, [permission]),
    )
    if (missingPermissions.length > 0) {
      return {
        sourceType: option.value,
        available: false,
        reason: `Requires ${formatPermissionList(missingPermissions)}.`,
      }
    }
    if (option.value === 'alert_occurrence' && alertOccurrenceUnavailable) {
      return {
        sourceType: option.value,
        available: false,
        reason: 'Durable Alerting v2 occurrences are unavailable on this deployment.',
      }
    }
    const serverCapability = serverByType.get(option.value)
    if (serverCapability?.available === false) {
      const missingServerPermissions = (serverCapability.required_permissions ?? []).filter(
        (permission) => !hasRequiredPermissions(grantedPermissions, [permission]),
      )
      return {
        sourceType: option.value,
        available: false,
        reason: missingServerPermissions.length > 0
          ? `Requires ${formatPermissionList(missingServerPermissions)}.`
          : 'This evidence source is unavailable.',
      }
    }
    return { sourceType: option.value, available: true, reason: null }
  })
}

export function availableEvidenceSourceTypes(
  availability: InvestigationEvidenceSourceAvailability[],
  filter: InvestigationEvidenceSourceFilter,
): InvestigationEvidenceType[] {
  return availability
    .filter((entry) => entry.available && (filter === 'all' || entry.sourceType === filter))
    .map((entry) => entry.sourceType)
}

export function candidateEvidenceSourceTypes(
  availability: InvestigationEvidenceSourceAvailability[],
  filter: InvestigationEvidenceSourceFilter,
  query: string,
): InvestigationEvidenceType[] {
  const availableTypes = availableEvidenceSourceTypes(availability, filter)
  if (filter !== 'all' || query.trim()) return availableTypes
  return availableTypes.filter((sourceType) => sourceType !== 'ioc')
}

export function buildEvidenceCandidateRequest({
  investigationId,
  query,
  sourceTypes,
  range,
  page,
  asOf,
}: {
  investigationId: string
  query: string
  sourceTypes: readonly InvestigationEvidenceType[]
  range: InvestigationEvidenceCandidateRange
  page: number
  asOf?: string | null
}) {
  const normalizedQuery = query.trim()
  return {
    path: `/investigations/${encodeURIComponent(investigationId)}/evidence-candidates`,
    body: {
      q: normalizedQuery || null,
      source_types: [...sourceTypes],
      range,
      page,
      page_size: INVESTIGATION_EVIDENCE_CANDIDATE_PAGE_SIZE,
      as_of: asOf ?? null,
    },
  }
}

export function candidatePageCount(
  response: InvestigationEvidenceCandidateListResponse | undefined,
): number {
  if (!response || response.page_size <= 0) return 1
  return Math.min(
    INVESTIGATION_EVIDENCE_MAX_PAGES,
    Math.max(1, Math.ceil(response.total / response.page_size)),
  )
}

export function candidateIdentity(candidate: InvestigationEvidenceCandidate): string {
  return `${candidate.source_type}:${candidate.source_id}`
}

export function formatEvidenceCandidateMatchReason(reason: string | null): string | null {
  if (!reason) return null
  const labels: Record<string, string> = {
    exact_id: 'Exact ID match',
    exact_ioc: 'Exact indicator match',
    exact_url: 'Exact URL match',
    prefix: 'Prefix match',
    related_ioc: 'Mentions this indicator',
    recent: 'Recent evidence',
    text: 'Text match',
  }
  if (labels[reason]) return labels[reason]
  const readable = reason.replaceAll('_', ' ')
  return `${readable.charAt(0).toUpperCase()}${readable.slice(1)}`
}

export function evidenceCandidateSourceLabel(type: InvestigationEvidenceType): string {
  return (
    INVESTIGATION_EVIDENCE_SOURCE_OPTIONS.find((option) => option.value === type)?.label ?? type
  )
}

function formatPermissionList(permissions: readonly string[]): string {
  const labels: Record<string, string> = {
    'read:items': 'View intelligence items',
    'read:reports': 'View reports',
    'read:alerts': 'View alerts',
  }
  return permissions.map((permission) => labels[permission] ?? permission).join(' and ')
}
