import { createContext, useContext, useSyncExternalStore } from 'react'

import { isSessionVerificationUnavailable, subscribeSessionVerification } from '../api/sessionLifecycle'

import { DialogSurface } from './ConfirmDialog'

const VerificationBoundaryContext = createContext(false)

export function SessionVerificationBoundary({
  unavailable, onRetry, children,
}: { unavailable: boolean; onRetry: () => void; children: React.ReactNode }) {
  const parentUnavailable = useContext(VerificationBoundaryContext)
  const actionsPaused = useSyncExternalStore(
    subscribeSessionVerification,
    isSessionVerificationUnavailable,
    isSessionVerificationUnavailable,
  )
  // Query error stays null during retries; use the same immediate state as API writes.
  const currentUnavailable = unavailable || actionsPaused
  return (
    <VerificationBoundaryContext.Provider value={parentUnavailable || currentUnavailable}>
      {children}
      <DialogSurface
        open={currentUnavailable && !parentUnavailable}
        title="Session check unavailable"
        description="Your drafts remain open. Protected actions are paused until ThreatLens can verify your session."
        dismissDisabled
        onClose={() => undefined}
        footer={<button type="button" className="rounded bg-cyan px-4 py-2 font-semibold text-white" onClick={onRetry}>Retry session check</button>}
      />
    </VerificationBoundaryContext.Provider>
  )
}
