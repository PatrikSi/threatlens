import { type Dispatch, type SetStateAction } from 'react'

import { AIAuditEntryResponse, AISettings, AITestConnectionResponse } from '../types/api'
import { AISettingsDraft, AISettingsDraftValidation } from './aiSettingsDraft'

export type AiConfigurationDraftProps = {
  draft: AISettingsDraft
  setDraft: Dispatch<SetStateAction<AISettingsDraft>>
  validation: AISettingsDraftValidation
}

export type AiSettingsConfigurationTabProps = AiConfigurationDraftProps & {
  draftDirty: boolean
  settings: AISettings | undefined
  readiness: string | null
  isLoading: boolean
  isError: boolean
  errorMessage: string
  savePending: boolean
  saveDisabled: boolean
  saveDisabledReason: string | null
  onSave: () => void
  onTestConnection: () => void
  testPending: boolean
  testDisabledReason: string | null
  testResult: AITestConnectionResponse | null
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
}
