import type { AIProviderCapabilities } from '../types/ai'

export type ProviderCapabilitiesDraft = {
  request_dialect: NonNullable<AIProviderCapabilities['request_dialect']>
  reasoning_effort: NonNullable<AIProviderCapabilities['reasoning_effort']> | ''
  structured_output_mode: NonNullable<AIProviderCapabilities['structured_output_mode']>
  model_context_window_tokens: string
  model_max_output_tokens: string
}

export const REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const

export function createCapabilitiesDraft(settings: AIProviderCapabilities = {}): ProviderCapabilitiesDraft {
  return {
    request_dialect: settings.request_dialect ?? 'chat_completions',
    reasoning_effort: settings.reasoning_effort ?? '',
    structured_output_mode: settings.structured_output_mode ?? 'off',
    model_context_window_tokens: settings.model_context_window_tokens == null ? '' : String(settings.model_context_window_tokens),
    model_max_output_tokens: settings.model_max_output_tokens == null ? '' : String(settings.model_max_output_tokens),
  }
}

export function createCapabilitiesRequest(draft: ProviderCapabilitiesDraft): AIProviderCapabilities {
  return {
    request_dialect: draft.request_dialect,
    reasoning_effort: draft.reasoning_effort || null,
    structured_output_mode: draft.structured_output_mode,
    model_context_window_tokens: draft.model_context_window_tokens.trim() ? Number(draft.model_context_window_tokens) : null,
    model_max_output_tokens: draft.model_max_output_tokens.trim() ? Number(draft.model_max_output_tokens) : null,
  }
}

export function validateCapabilitiesDraft(draft: ProviderCapabilitiesDraft): Partial<Record<keyof ProviderCapabilitiesDraft, string>> {
  const errors: Partial<Record<keyof ProviderCapabilitiesDraft, string>> = {}
  if (!['chat_completions', 'chat_completions_modern'].includes(draft.request_dialect))
    errors.request_dialect = 'Choose a supported chat-completions request format.'
  if (draft.reasoning_effort && !REASONING_EFFORTS.includes(draft.reasoning_effort))
    errors.reasoning_effort = 'Choose a supported reasoning setting or omit it.'
  if (!['off', 'json_object'].includes(draft.structured_output_mode))
    errors.structured_output_mode = 'Choose a supported JSON response mode.'
  for (const [key, label, min, max] of [
    ['model_context_window_tokens', 'Model context limit', 2048, 2097152],
    ['model_max_output_tokens', 'Model output limit', 128, 131072],
  ] as const) {
    const value = draft[key].trim()
    if (value && (!Number.isInteger(Number(value)) || Number(value) < min || Number(value) > max))
      errors[key] = `${label} must be a whole number between ${min.toLocaleString()} and ${max.toLocaleString()}, or blank if unverified.`
  }
  return errors
}
