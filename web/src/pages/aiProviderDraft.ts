import { createSecureRequestId } from '../utils/secureRandomId'
import type { AIProvider, AIProviderRouting, AIProviderWriteRequest } from '../types/ai'
import { DEFAULT_DRAFT, validateAISettingsDraft } from './aiSettingsDraft'
import { createAdmissionDraft, createAdmissionRequest, type ProviderAdmissionDraft } from './aiProviderAdmissionDraft'
import { createCapabilitiesDraft, createCapabilitiesRequest, type ProviderCapabilitiesDraft } from './aiProviderCapabilitiesDraft'

export type ProviderDraft = ProviderCapabilitiesDraft & ProviderAdmissionDraft & {
  name: string
  enabled: boolean
  base_url: string
  model: string
  temperature: string
  max_completion_tokens: string
  request_timeout_seconds: string
  request_max_retries: string
  api_key: string
  clear_api_key: boolean
}

export type RoutingField = Exclude<keyof AIProviderRouting, 'version'>

export const ROUTING_FIELDS: { key: RoutingField; label: string }[] = [
  { key: 'default_provider_id', label: 'Default provider' },
  { key: 'item_enrichment_provider_id', label: 'Article summaries and relevance' },
  { key: 'daily_brief_provider_id', label: 'Daily briefs' },
  { key: 'report_provider_id', label: 'Reports' },
]

export function createProviderDraft(provider?: AIProvider): ProviderDraft {
  return {
    ...createCapabilitiesDraft(provider),
    ...createAdmissionDraft(provider),
    name: provider?.name ?? '',
    enabled: provider?.enabled ?? true,
    base_url: provider?.base_url ?? '',
    model: provider?.model ?? '',
    temperature: provider?.temperature === null ? '' : String(provider?.temperature ?? 0.2),
    max_completion_tokens: String(provider?.max_completion_tokens ?? 5000),
    request_timeout_seconds: String(provider?.request_timeout_seconds ?? 300),
    request_max_retries: String(provider?.request_max_retries ?? 3),
    api_key: '',
    clear_api_key: false,
  }
}

export function validateProviderDraft(draft: ProviderDraft): Partial<Record<keyof ProviderDraft, string>> {
  const errors = validateAISettingsDraft({ ...DEFAULT_DRAFT, ...draft }) as Partial<Record<keyof ProviderDraft, string>>
  if (!draft.name.trim()) errors.name = 'Enter a provider name.'
  else if (draft.name.trim().length > 120) errors.name = 'Provider name cannot exceed 120 characters.'
  if (!draft.model.trim()) errors.model = 'Enter a model identifier supported by this endpoint.'
  try {
    const url = new URL(draft.base_url)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      errors.base_url = 'Use an HTTP or HTTPS URL without credentials, query parameters or a fragment.'
    }
  } catch {
    errors.base_url = 'Enter a complete HTTP or HTTPS base URL.'
  }
  if (draft.api_key && draft.clear_api_key)
    errors.api_key = 'Choose either a replacement key or removal of the saved key.'
  else if (draft.api_key.length > 16384) errors.api_key = 'The API key cannot exceed 16384 characters.'
  else if (draft.api_key && (!draft.api_key.trim() || /[^\x20-\x7e]/.test(draft.api_key)))
    errors.api_key = 'Use a nonempty API key containing printable ASCII characters.'
  return errors
}

export const createProviderRequestId = createSecureRequestId

export function createProviderRequest(draft: ProviderDraft): AIProviderWriteRequest {
  return {
    ...createCapabilitiesRequest(draft),
    ...createAdmissionRequest(draft),
    name: draft.name.trim(),
    enabled: draft.enabled,
    provider_type: 'openai_compatible',
    base_url: draft.base_url.trim(),
    model: draft.model.trim(),
    temperature: draft.temperature.trim() ? Number(draft.temperature) : null,
    max_completion_tokens: Number(draft.max_completion_tokens),
    request_timeout_seconds: Number(draft.request_timeout_seconds),
    request_max_retries: Number(draft.request_max_retries),
    ...(draft.api_key ? { api_key: draft.api_key } : {}),
    ...(draft.clear_api_key ? { clear_api_key: true } : {}),
  }
}
