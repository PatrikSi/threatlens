import type { AIProviderAdmissionLimits } from '../types/ai'

export type ProviderAdmissionDraft = {
  max_concurrent_requests: string
  hourly_token_budget: string
}

export function createAdmissionDraft(settings: AIProviderAdmissionLimits = {}): ProviderAdmissionDraft {
  return {
    max_concurrent_requests: String(settings.max_concurrent_requests ?? 0),
    hourly_token_budget: String(settings.hourly_token_budget ?? 0),
  }
}

export function createAdmissionRequest(draft: ProviderAdmissionDraft): AIProviderAdmissionLimits {
  return {
    max_concurrent_requests: Number(draft.max_concurrent_requests),
    hourly_token_budget: Number(draft.hourly_token_budget),
  }
}

export function validateAdmissionDraft(draft: ProviderAdmissionDraft): Partial<Record<keyof ProviderAdmissionDraft, string>> {
  const errors: Partial<Record<keyof ProviderAdmissionDraft, string>> = {}
  for (const [key, label, maximum] of [
    ['max_concurrent_requests', 'Concurrent request limit', 1000],
    ['hourly_token_budget', 'Rolling-hour token budget', 1000000000000],
  ] as const) {
    const value = draft[key].trim()
    if (!value || !Number.isInteger(Number(value)) || Number(value) < 0 || Number(value) > maximum)
      errors[key] = `${label} must be a whole number between 0 and ${maximum.toLocaleString()}. Use 0 for unlimited.`
  }
  return errors
}
