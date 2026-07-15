import {
  SMTPHook,
  SMTPHookWriteRequest,
  SMTPTemplateDefault,
} from '../types/api'
import {
  createSMTPDraftFromSettings,
  createSMTPRequestFromDraft,
  DEFAULT_SMTP_DRAFT,
  getFirstSMTPSettingsDraftValidationError,
  smtpDraftFingerprint,
  SMTPSettingsDraft,
  SMTPSettingsDraftValidation,
  validateSMTPSettingsDraft,
} from './smtpSettingsDraft'

export type SMTPHookDraft = SMTPSettingsDraft & {
  name: string
  credential_source_id: string | null
}

export type SMTPHookDraftValidation = SMTPSettingsDraftValidation & {
  name?: string
}

export const DEFAULT_SMTP_HOOK_DRAFT: SMTPHookDraft = {
  ...DEFAULT_SMTP_DRAFT,
  name: '',
  credential_source_id: null,
}

export function createSMTPHookDraft(hook: SMTPHook): SMTPHookDraft {
  return {
    ...createSMTPDraftFromSettings(hook),
    name: hook.name,
    credential_source_id: hook.credential_source_id,
  }
}

export function createSMTPHookRequest(draft: SMTPHookDraft): SMTPHookWriteRequest {
  const settings = createSMTPRequestFromDraft(draft)
  if (draft.credential_source_id) {
    settings.host = null
    settings.username = null
    delete settings.password
    delete settings.clear_password
  }
  return {
    name: draft.name.trim(),
    credential_source_id: draft.credential_source_id,
    settings,
  }
}

export function validateSMTPHookDraft(draft: SMTPHookDraft): SMTPHookDraftValidation {
  const validation: SMTPHookDraftValidation = validateSMTPSettingsDraft(draft)
  if (!draft.name.trim()) {
    validation.name = 'Name is required.'
  }
  if (draft.credential_source_id) {
    delete validation.host
    delete validation.username
    delete validation.password
  }
  return validation
}

export function getFirstSMTPHookDraftValidationError(validation: SMTPHookDraftValidation): string | null {
  return validation.name ?? getFirstSMTPSettingsDraftValidationError(validation)
}

export function smtpHookDraftFingerprint(draft: SMTPHookDraft): string {
  return JSON.stringify({
    name: draft.name.trim(),
    credential_source_id: draft.credential_source_id,
    settings: smtpDraftFingerprint(draft),
  })
}

export function applySMTPTemplateDefault(
  draft: SMTPHookDraft,
  template: SMTPTemplateDefault,
): SMTPHookDraft {
  return {
    ...draft,
    event_types: [...template.event_types],
    subject_template: template.subject_template,
    html_template: template.html_template,
  }
}
