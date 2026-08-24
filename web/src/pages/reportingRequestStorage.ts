import { sha256 } from '@noble/hashes/sha2.js'


const REQUEST_STORAGE_PREFIX = 'threatlens.reporting-request.'

type StoredRequest = {
  key: string
  createdAt: number
  settled: boolean
}
export type ReportingRequestStorageEntry = {
  key: string
  createdAt: number
  storageKey: string
  durable: boolean
}


export function acquireReportingRequestStorage(
  scope: string,
  createKey: () => string,
  assertCurrent: () => void,
): ReportingRequestStorageEntry {
  assertCurrent()
  const storageKey = `${REQUEST_STORAGE_PREFIX}v4-${scopeDigest(scope)}`
  const stored = readStoredRequest(storageKey)

  if (stored && !stored.settled) {
    return storageEntry(storageKey, stored, true)
  }
  if (stored?.settled && !removeStorageValue(storageKey)) {
    throw new Error(
      'ThreatLens could not clear the previous report request state. Restore browser storage, reload, and retry; no request was sent.',
    )
  }

  const record: StoredRequest = {
    key: createKey(),
    createdAt: Date.now(),
    settled: false,
  }
  return storageEntry(storageKey, record, writeStoredRequest(storageKey, record))
}


export function persistReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  const record = storageRecord(entry)
  if (writeStoredRequest(entry.storageKey, record)) return true
  try {
    const stored = readStoredRequest(entry.storageKey)
    return stored?.key === entry.key && !stored.settled
  } catch (error) {
    if (error instanceof ReportingRequestStorageReadError) return false
    throw error
  }
}


export function settleReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  return writeStoredRequest(entry.storageKey, {
    ...storageRecord(entry),
    settled: true,
  })
}


export function removeReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  if (typeof window === 'undefined') return true
  if (removeStorageValue(entry.storageKey)) return true
  return writeStoredRequest(entry.storageKey, {
    ...storageRecord(entry),
    settled: true,
  })
}


export function clearReportingRequestStorage(): boolean {
  if (typeof window === 'undefined') return true
  const persisted = persistedRequestKeys()
  return persisted !== undefined && removeStorageKeys(persisted)
}


function storageEntry(
  storageKey: string,
  record: StoredRequest,
  durable: boolean,
): ReportingRequestStorageEntry {
  return {
    key: record.key,
    createdAt: record.createdAt,
    storageKey,
    durable,
  }
}


function storageRecord(
  entry: Pick<ReportingRequestStorageEntry, 'key' | 'createdAt'>,
): StoredRequest {
  return { key: entry.key, createdAt: entry.createdAt, settled: false }
}


function readStoredRequest(storageKey: string): StoredRequest | undefined {
  const raw = getStorageValue(storageKey)
  if (raw === null) return undefined
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return discardMalformedRequest(storageKey)
  }
  if (!isStoredRequest(value)) return discardMalformedRequest(storageKey)
  return {
    key: value.key,
    createdAt: value.createdAt,
    settled: value.settled === true,
  }
}


function discardMalformedRequest(storageKey: string): undefined {
  if (!removeStorageValue(storageKey)) {
    throw new Error(
      'ThreatLens found invalid report request state but could not clear it. Restore browser storage, reload, and retry; no request was sent.',
    )
  }
  return undefined
}


function writeStoredRequest(
  storageKey: string,
  record: StoredRequest,
): boolean {
  return setStorageValue(storageKey, JSON.stringify({
    key: record.key,
    createdAt: record.createdAt,
    ...(record.settled ? { settled: true } : {}),
  }))
}


function getStorageValue(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage.getItem(key)
  } catch {
    throw new ReportingRequestStorageReadError()
  }
}


function setStorageValue(key: string, value: string): boolean {
  try {
    if (typeof window === 'undefined') return false
    window.sessionStorage.setItem(key, value)
    return window.sessionStorage.getItem(key) === value
  } catch {
    return false
  }
}


function removeStorageKeys(keys: string[]): boolean {
  let removed = true
  for (const key of keys) {
    removed = removeStorageValue(key) && removed
  }
  return removed
}


function removeStorageValue(key: string): boolean {
  try {
    if (typeof window === 'undefined') return false
    window.sessionStorage.removeItem(key)
    return window.sessionStorage.getItem(key) === null
  } catch {
    return false
  }
}


function persistedRequestKeys(): string[] | undefined {
  const keys = new Set<string>()
  try {
    const storage = window.sessionStorage
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index)
      if (key?.startsWith(REQUEST_STORAGE_PREFIX)) keys.add(key)
    }
  } catch {
    return undefined
  }
  return [...keys]
}


function scopeDigest(scope: string): string {
  return Array.from(
    sha256(new TextEncoder().encode(scope)),
    (value) => value.toString(16).padStart(2, '0'),
  ).join('')
}


function isValidIdempotencyKey(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= 255
    && /^[A-Za-z0-9._~:-]+$/.test(value)
}


function isStoredRequest(value: unknown): value is {
  key: string
  createdAt: number
  settled?: boolean
} {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return isValidIdempotencyKey(record.key)
    && typeof record.createdAt === 'number'
    && Number.isFinite(record.createdAt)
    && record.createdAt >= 0
    && (record.settled === undefined || typeof record.settled === 'boolean')
}


class ReportingRequestStorageReadError extends Error {
  constructor() {
    super(
      'ThreatLens cannot inspect unresolved report request keys because browser storage is unreadable. Restore browser storage, reload, and retry; no request was sent.',
    )
    this.name = 'ReportingRequestStorageReadError'
  }
}
