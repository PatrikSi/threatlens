import { describe, expect, it } from 'vitest'

import type { LifecyclePreview } from '../types/lifecycle'
import { lifecyclePreviewIsFreshForDraft } from './lifecycleModel'
import {
  lifecyclePreviewLocalExpiresAt,
  observeLifecyclePreview,
} from './lifecyclePreviewClock'

describe('lifecycle preview clock', () => {
  it('uses the server-observed remaining lifetime on a skewed browser clock', () => {
    const requestStartedAt = Date.parse('2040-01-01T00:00:00Z')
    const preview = previewFixture({
      observed_at: '2026-09-02T10:10:00Z',
      expires_at: '2026-09-02T10:15:00Z',
    })

    observeLifecyclePreview(preview, requestStartedAt)

    expect(lifecyclePreviewLocalExpiresAt(preview)).toBe(
      requestStartedAt + 5 * 60_000,
    )
    expect(lifecyclePreviewIsFreshForDraft({
      preview,
      previewMatchesDraft: true,
      policyRevision: preview.policy_revision,
      now: requestStartedAt + 4 * 60_000,
    })).toBe(true)
    expect(lifecyclePreviewIsFreshForDraft({
      preview,
      previewMatchesDraft: true,
      policyRevision: preview.policy_revision,
      now: requestStartedAt + 5 * 60_000,
    })).toBe(false)
  })

  it('does not extend a reused preview from its original generation time', () => {
    const requestStartedAt = Date.parse('2020-01-01T00:00:00Z')
    const preview = previewFixture({
      generated_at: '2026-09-02T09:00:00Z',
      observed_at: '2026-09-02T10:14:00Z',
      expires_at: '2026-09-02T10:15:00Z',
    })

    observeLifecyclePreview(preview, requestStartedAt)

    expect(lifecyclePreviewLocalExpiresAt(preview)).toBe(
      requestStartedAt + 60_000,
    )
  })
})

function previewFixture(
  overrides: Partial<LifecyclePreview> = {},
): LifecyclePreview {
  return {
    id: 'preview-clock-test',
    target_key: 'article_content',
    policy_revision: 3,
    generated_at: '2026-09-02T10:00:00Z',
    cutoff_at: '2025-09-02T10:00:00Z',
    expires_at: '2026-09-02T10:15:00Z',
    observed_at: '2026-09-02T10:00:00Z',
    eligible_count: 24,
    protected_count: 3,
    protected_counts: { starred: 3 },
    eligible_bytes: 4_096,
    oldest_candidate_at: '2024-01-01T00:00:00Z',
    count_is_lower_bound: false,
    is_partial: false,
    ...overrides,
  }
}
