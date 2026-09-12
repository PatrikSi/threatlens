import { createContext } from 'react'

/** The Alerts page owns one navigation blocker for rule and triage drafts together. */
export const AlertTriageDraftContext = createContext<((dirty: boolean) => void) | null>(null)
