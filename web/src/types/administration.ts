export interface AuditLog {
  id: string
  actor_user_id: string | null
  action: string
  resource_type: string
  resource_id: string | null
  success: boolean
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface AuditLogListResponse {
  logs: AuditLog[]
  total: number
  page: number
  page_size: number
}

export interface AuditLogExportResponse {
  exported_at: string
  total: number
  truncated: boolean
  logs: AuditLog[]
}

export interface EncryptedDataInventoryCategory {
  total_records: number
  encrypted_records: number
  unreadable_records: number
  encrypted_fields: number
  unreadable_fields: number
}

export interface EncryptedDataStartupScan {
  completed_at: string | null
  status: 'healthy' | 'warning' | 'critical' | null
  error: string | null
  total_unreadable_records: number | null
  total_unreadable_fields: number | null
}

export interface EncryptedDataInventorySummary {
  total_records: number
  encrypted_records: number
  unreadable_records: number
  encrypted_fields: number
  unreadable_fields: number
}

export interface EncryptedDataInventoryResponse {
  ok: boolean
  status: 'healthy' | 'warning' | 'critical'
  scanned_at: string
  warnings: string[]
  require_explicit_app_data_encryption_key: boolean
  using_derived_app_data_encryption_key: boolean
  startup_scan: EncryptedDataStartupScan
  feeds: EncryptedDataInventoryCategory
  notification_webhooks: EncryptedDataInventoryCategory
  notification_delivery_snapshots: EncryptedDataInventoryCategory
  summary: EncryptedDataInventorySummary
}
