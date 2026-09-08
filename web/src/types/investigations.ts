export type InvestigationStatus = 'open' | 'monitoring' | 'closed' | 'archived'
export type InvestigationSeverity = 'low' | 'medium' | 'high' | 'critical'
export type InvestigationVisibility = 'private' | 'team'
export type InvestigationMemberRole = 'owner' | 'editor' | 'viewer'
export type InvestigationEvidenceType = 'item' | 'ioc' | 'report' | 'alert_occurrence'
export type InvestigationEvidenceCandidateRange = '24h' | '7d' | '30d' | '90d'
export type InvestigationEvidenceQueryKind = 'empty' | 'text' | 'url' | 'ioc' | 'uuid'
export type InvestigationAccountRole = 'admin' | 'analyst' | 'viewer'

export interface InvestigationMember {
  user_id: string
  email: string
  role: InvestigationMemberRole
  created_at: string
}

export interface InvestigationMemberCandidate {
  id: string
  email: string
  account_role: InvestigationAccountRole
}

export interface InvestigationMemberCandidateListResponse {
  users: InvestigationMemberCandidate[]
  total: number
  page: number
  page_size: number
}

export interface InvestigationEvidence {
  id: string
  source_type: InvestigationEvidenceType
  source_id: string
  title_snapshot: string
  description_snapshot: string | null
  url_snapshot: string | null
  metadata_snapshot: Record<string, unknown>
  note: string | null
  added_by_user_id: string | null
  created_at: string
}

export interface InvestigationEvidenceCandidate {
  source_type: InvestigationEvidenceType
  source_id: string
  title: string
  description: string | null
  url: string | null
  observed_at: string | null
  source_label: string | null
  metadata: InvestigationEvidenceCandidateMetadata
  already_attached: boolean
  match_reason: string | null
}

export interface InvestigationEvidenceRelatedItem {
  item_id: string
  title: string
  feed_name: string | null
  first_seen_at: string | null
}

export interface InvestigationEvidenceCandidateMetadata extends Record<string, unknown> {
  related_items?: InvestigationEvidenceRelatedItem[]
}

export interface InvestigationEvidenceQueryAnalysis {
  kind: InvestigationEvidenceQueryKind
  normalized_value: string | null
  detected_ioc_type: string | null
}

export interface InvestigationEvidenceSourceCapability {
  source_type: InvestigationEvidenceType
  available: boolean
  unavailable_reason: string | null
  required_permissions: string[]
}

export interface InvestigationEvidenceCandidateListResponse {
  candidates: InvestigationEvidenceCandidate[]
  total: number
  total_truncated: boolean
  page: number
  page_size: number
  query_analysis: InvestigationEvidenceQueryAnalysis
  source_capabilities: InvestigationEvidenceSourceCapability[]
  effective_range: InvestigationEvidenceCandidateRange
  effective_since: string
  effective_until: string
}

export interface InvestigationNote {
  id: string
  author_user_id: string | null
  author_email: string | null
  body: string
  version: number
  created_at: string
  updated_at: string
}

export interface InvestigationActivity {
  id: string
  actor_user_id: string | null
  actor_email: string | null
  action: string
  entity_type: string | null
  entity_id: string | null
  details: Record<string, unknown>
  created_at: string
}

export interface InvestigationSummary {
  id: string
  title: string
  description: string
  status: InvestigationStatus
  severity: InvestigationSeverity
  visibility: InvestigationVisibility
  disposition: string | null
  assignee_user_id: string | null
  assignee_email: string | null
  current_user_role: InvestigationMemberRole | null
  evidence_count: number
  member_count: number
  note_count: number
  version: number
  created_at: string
  updated_at: string
  closed_at: string | null
  archived_at: string | null
}

export interface InvestigationDetail extends InvestigationSummary {
  members: InvestigationMember[]
  evidence: InvestigationEvidence[]
  evidence_truncated: boolean
  notes: InvestigationNote[]
  notes_truncated: boolean
}

export interface InvestigationListResponse {
  investigations: InvestigationSummary[]
  total: number
  page: number
  page_size: number
}

export interface InvestigationActivityListResponse {
  activities: InvestigationActivity[]
  total: number
  page: number
  page_size: number
}

export interface InvestigationEvidenceListResponse {
  evidence: InvestigationEvidence[]
  total: number
  page: number
  page_size: number
}

export interface InvestigationNoteListResponse {
  notes: InvestigationNote[]
  total: number
  page: number
  page_size: number
}

export interface InvestigationCreateRequest {
  title: string
  description: string
  severity: InvestigationSeverity
  visibility: InvestigationVisibility
  assignee_user_id: string | null
}

export interface InvestigationUpdateRequest {
  expected_version: number
  title?: string
  description?: string
  status?: InvestigationStatus
  severity?: InvestigationSeverity
  visibility?: InvestigationVisibility
  disposition?: string | null
  assignee_user_id?: string | null
}

export interface InvestigationMemberAddRequest {
  user_id: string
  role: InvestigationMemberRole
  expected_version: number
}

export interface InvestigationMemberUpdateRequest {
  role: InvestigationMemberRole
  expected_version: number
}

export interface InvestigationEvidenceAddRequest {
  source_type: InvestigationEvidenceType
  source_id: string
  note?: string | null
  expected_version: number
}

export interface InvestigationNoteCreateRequest {
  body: string
  expected_version: number
}

export interface InvestigationNoteUpdateRequest {
  body: string
  expected_note_version: number
  expected_investigation_version: number
}

export interface InvestigationListFilters {
  query: string
  statuses: InvestigationStatus[]
  severities: InvestigationSeverity[]
  assignedToMe: boolean
  includeArchived: boolean
  page: number
}

export type InvestigationDetailTab = 'overview' | 'members' | 'evidence' | 'notes' | 'activity'
