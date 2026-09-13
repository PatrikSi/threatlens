let controller = new AbortController()
let verificationUnavailable = false
const verificationListeners = new Set<() => void>()

export class SessionChangedError extends Error {
  readonly retryable = false
  constructor() {
    super('The session changed. This operation belongs to the previous session.')
    this.name = 'SessionChangedError'
  }
}

export class SessionVerificationError extends Error {
  readonly retryable = false
  constructor() {
    super('Protected actions are paused until the session can be verified.')
    this.name = 'SessionVerificationError'
  }
}

/** Capture once for a whole multi-request operation, including any awaited local work. */
export function captureSessionLease() {
  const captured = controller
  return {
    signal: captured.signal,
    assertCurrent() {
      if (captured !== controller || captured.signal.aborted) throw new SessionChangedError()
    },
  }
}

/** Invalidate synchronously before rendering the next identity's providers. */
export function invalidateSession() {
  const previous = controller
  controller = new AbortController()
  setSessionVerificationUnavailable(false)
  previous.abort(new SessionChangedError())
}

export function setSessionVerificationUnavailable(unavailable: boolean) {
  if (verificationUnavailable === unavailable) return
  verificationUnavailable = unavailable
  verificationListeners.forEach((listener) => listener())
}

export function isSessionVerificationUnavailable() {
  return verificationUnavailable
}

export function subscribeSessionVerification(listener: () => void) {
  verificationListeners.add(listener)
  return () => { verificationListeners.delete(listener) }
}

export function assertSessionActionsAvailable() {
  if (verificationUnavailable) throw new SessionVerificationError()
}
