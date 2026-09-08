import { createContext, useContext } from 'react'

import { DialogSurface } from './ConfirmDialog'

const VerificationBoundaryContext = createContext(false)

export function SessionVerificationBoundary({
  unavailable, onRetry, children,
}: { unavailable: boolean; onRetry: () => void; children: React.ReactNode }) {
  const parentUnavailable = useContext(VerificationBoundaryContext)
  return (
    <VerificationBoundaryContext.Provider value={parentUnavailable || unavailable}>
      {children}
      <DialogSurface
        open={unavailable && !parentUnavailable}
        title="Session check unavailable"
        description="Your drafts remain open. Protected actions are paused until ThreatLens can verify your session."
        dismissDisabled
        onClose={() => undefined}
        footer={<button type="button" className="rounded bg-cyan px-4 py-2 font-semibold text-white" onClick={onRetry}>Retry session check</button>}
      />
    </VerificationBoundaryContext.Provider>
  )
}
