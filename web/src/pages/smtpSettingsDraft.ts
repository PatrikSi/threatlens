import { SMTPSettings, SMTPSettingsUpdateRequest, SMTPSecurityMode } from '../types/api'

export type SMTPSettingsDraft = {
  enabled: boolean
  host: string
  port: string
  security: SMTPSecurityMode
  username: string
  password: string
  clear_password: boolean
  from_email: string
  from_name: string
  timeout_seconds: string
}

export type SMTPSettingsDraftValidation = Partial<Record<keyof SMTPSettingsDraft, string>>

export const DEFAULT_SMTP_DRAFT: SMTPSettingsDraft = {
  enabled: false,
  host: '',
  port: '587',
  security: 'starttls',
  username: '',
  password: '',
  clear_password: false,
  from_email: '',
  from_name: '',
  timeout_seconds: '10',
}

export function createSMTPDraftFromSettings(settings: SMTPSettings): SMTPSettingsDraft {
  return {
    enabled: settings.enabled,
    host: settings.host ?? '',
    port: String(settings.port),
    security: settings.security,
    username: settings.username ?? '',
    password: '',
    clear_password: false,
    from_email: settings.from_email ?? '',
    from_name: settings.from_name ?? '',
    timeout_seconds: String(settings.timeout_seconds),
  }
}

export function createSMTPRequestFromDraft(draft: SMTPSettingsDraft): SMTPSettingsUpdateRequest {
  const request: SMTPSettingsUpdateRequest = {
    enabled: draft.enabled,
    host: normalizeOptionalText(draft.host),
    port: parseBoundedInt(draft.port, 587, 1, 65535),
    security: draft.security,
    username: normalizeOptionalText(draft.username),
    from_email: normalizeOptionalText(draft.from_email),
    from_name: normalizeOptionalText(draft.from_name),
    timeout_seconds: parseBoundedInt(draft.timeout_seconds, 10, 1, 60),
  }

  const password = normalizeOptionalText(draft.password)
  if (password) {
    request.password = password
  }
  if (draft.clear_password) {
    request.clear_password = true
  }
  return request
}

export function validateSMTPSettingsDraft(draft: SMTPSettingsDraft): SMTPSettingsDraftValidation {
  const errors: SMTPSettingsDraftValidation = {}
  const host = draft.host.trim()
  const port = Number(draft.port)
  const timeout = Number(draft.timeout_seconds)
  const fromEmail = draft.from_email.trim()
  const username = draft.username.trim()
  const password = draft.password.trim()

  if (draft.enabled && !host) {
    errors.host = 'Host is required when SMTP is enabled.'
  } else if (host && /\s/.test(host)) {
    errors.host = 'Host must not contain whitespace.'
  }

  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    errors.port = 'Port must be between 1 and 65535.'
  }

  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 60) {
    errors.timeout_seconds = 'Timeout must be between 1 and 60 seconds.'
  }

  if (draft.enabled && !fromEmail) {
    errors.from_email = 'Sender email is required when SMTP is enabled.'
  } else if (fromEmail && !looksLikeEmail(fromEmail)) {
    errors.from_email = 'Enter a valid sender email address.'
  }

  if (password && !username) {
    errors.username = 'Username is required when a password is provided.'
  }
  if (password && draft.clear_password) {
    errors.password = 'Remove the password value or turn off clear saved password.'
  }

  return errors
}

export function getFirstSMTPSettingsDraftValidationError(validation: SMTPSettingsDraftValidation): string | null {
  const fields: Array<keyof SMTPSettingsDraft> = [
    'host',
    'port',
    'timeout_seconds',
    'from_email',
    'username',
    'password',
  ]
  for (const field of fields) {
    const message = validation[field]
    if (message) {
      return message
    }
  }
  return null
}

export function smtpDraftFingerprint(draft: SMTPSettingsDraft): string {
  return JSON.stringify({
    ...draft,
    host: draft.host.trim(),
    port: String(parseBoundedInt(draft.port, 587, 1, 65535)),
    username: draft.username.trim(),
    password: draft.password.trim(),
    from_email: draft.from_email.trim(),
    from_name: draft.from_name.trim(),
    timeout_seconds: String(parseBoundedInt(draft.timeout_seconds, 10, 1, 60)),
  })
}

function normalizeOptionalText(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function parseBoundedInt(value: string, fallback: number, minimum: number, maximum: number) {
  const parsed = Number(value)
  if (!Number.isInteger(parsed)) {
    return fallback
  }
  return Math.min(Math.max(parsed, minimum), maximum)
}

function looksLikeEmail(value: string) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)
}
