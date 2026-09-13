/** UUIDv4 request identifiers also work on HTTP LAN origins, where randomUUID is unavailable. */
export function createSecureRequestId(): string {
  try {
    const crypto = globalThis.crypto
    if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID()
    if (typeof crypto?.getRandomValues === 'function') {
      const bytes = crypto.getRandomValues(new Uint8Array(16))
      bytes[6] = (bytes[6] & 0x0f) | 0x40
      bytes[8] = (bytes[8] & 0x3f) | 0x80
      const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
    }
  } catch {
    // A disabled browser crypto API needs the same guidance as a missing one.
  }
  throw new Error('Secure random generation is unavailable in this browser. Reload the page or use a supported browser with browser security APIs enabled.')
}
