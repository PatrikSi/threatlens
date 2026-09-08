import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch: vi.fn(),
}))

import { apiFetch } from '../api/client'
import type {
  LifecycleOverviewResponse,
  LifecyclePreview,
  LifecyclePreviewRequest,
} from '../types/lifecycle'
import { loadLifecycleOverview, previewLifecyclePolicy } from './lifecycleApi'
import { lifecyclePreviewLocalExpiresAt } from './lifecyclePreviewClock'

afterEach(() => {
  vi.mocked(apiFetch).mockReset()
  vi.restoreAllMocks()
})

describe('lifecycle API preview clock', () => {
  it('anchors direct preview expiry to the request start', async () => {
    const requestStartedAt = Date.parse('2040-01-01T00:00:00Z')
    const preview = previewFixture()
    vi.spyOn(Date, 'now').mockReturnValue(requestStartedAt)
    vi.mocked(apiFetch).mockResolvedValue(preview)

    const result = await previewLifecyclePolicy({
      target_key: 'article_content',
      expected_revision: 3,
      draft: {
        enabled: true,
        retention_days: 365,
        schedule_cadence: 'daily',
        schedule_hour_utc: 2,
        schedule_weekday: null,
        max_records_per_run: 10_000,
        options: { protect_starred: true },
      },
    } satisfies LifecyclePreviewRequest)

    expect(result).toBe(preview)
    expect(lifecyclePreviewLocalExpiresAt(result)).toBe(
      requestStartedAt + 5 * 60_000,
    )
  })

  it('anchors cached overview previews to the overview request start', async () => {
    const requestStartedAt = Date.parse('2020-01-01T00:00:00Z')
    const preview = previewFixture()
    vi.spyOn(Date, 'now').mockReturnValue(requestStartedAt)
    vi.mocked(apiFetch).mockResolvedValue({
      generated_at: '2026-09-02T10:10:00Z',
      targets: [{ latest_preview: preview }],
    } as LifecycleOverviewResponse)

    const result = await loadLifecycleOverview()

    expect(lifecyclePreviewLocalExpiresAt(
      result.targets[0].latest_preview as LifecyclePreview,
    )).toBe(requestStartedAt + 5 * 60_000)
  })
})

function previewFixture(): LifecyclePreview {
  return {
    id: 'preview-api-test',
    target_key: 'article_content',
    policy_revision: 3,
    generated_at: '2026-09-02T09:00:00Z',
    cutoff_at: '2025-09-02T10:00:00Z',
    expires_at: '2026-09-02T10:15:00Z',
    observed_at: '2026-09-02T10:10:00Z',
    eligible_count: 24,
    protected_count: 3,
    protected_counts: { starred: 3 },
    eligible_bytes: 4_096,
    oldest_candidate_at: '2024-01-01T00:00:00Z',
    count_is_lower_bound: false,
    is_partial: false,
  }
}
