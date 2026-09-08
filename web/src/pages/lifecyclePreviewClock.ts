import type { LifecyclePreview } from '../types/lifecycle'

const localExpiryByPreview = new WeakMap<LifecyclePreview, number>()

export function observeLifecyclePreview(
  preview: LifecyclePreview,
  requestStartedAt: number,
): LifecyclePreview {
  const observedAt = Date.parse(preview.observed_at)
  const expiresAt = Date.parse(preview.expires_at)
  if (Number.isFinite(observedAt) && Number.isFinite(expiresAt)) {
    // The response can only arrive after requestStartedAt, so anchoring the
    // server-reported remaining lifetime here expires conservatively early.
    localExpiryByPreview.set(
      preview,
      requestStartedAt + Math.max(0, expiresAt - observedAt),
    )
  }
  return preview
}

export function lifecyclePreviewLocalExpiresAt(
  preview: LifecyclePreview,
): number {
  return localExpiryByPreview.get(preview) ?? Date.parse(preview.expires_at)
}
