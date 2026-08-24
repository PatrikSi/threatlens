import { sha256 } from '@noble/hashes/sha2.js'


const REQUEST_STORAGE_PREFIX = 'threatlens.reporting-request.'
const SCOPE_SALT_KEY = 'threatlens.reporting-scope-salt'

type StorageLocation = 'session' | 'local'
type StoredRequest = {
  key: string
  createdAt: number
  settled: boolean
}
type LocatedRequest = {
  location: StorageLocation
  storageKey: string
  record: StoredRequest
}
export type ReportingRequestStorageEntry = {
  key: string
  createdAt: number
  storageKey: string
  compatibilityKeys: string[]
  predecessorKeys: string[]
  durable: boolean
}


export function acquireReportingRequestStorage(
  scope: string,
  createKey: () => string,
  assertCurrent: () => void,
): ReportingRequestStorageEntry {
  assertCurrent()
  const storageKey = `${REQUEST_STORAGE_PREFIX}v4-${scopeDigest(scope)}`
  const salts = storedScopeSalts()
  const compatibilityKeys = rollbackStorageKeys(scope, salts)
  const predecessorKeys = predecessorStorageKeys(scope, salts)
  const located = locatedRequests([storageKey, ...predecessorKeys])
  const settledCurrent = located.filter(
    (request) => request.storageKey === storageKey && request.record.settled,
  )

  if (settledCurrent.length > 0) {
    assertSettledRequestMatchesAliases(settledCurrent, located)
    removeStorageKeys([storageKey, ...predecessorKeys])
  } else {
    const stored = resolveStoredRequest(located)
    if (stored) {
      const promoted = writeCompatibleRequest(
        storageKey,
        compatibilityKeys,
        predecessorKeys,
        stored.record,
      )
      return storageEntry(
        storageKey,
        compatibilityKeys,
        predecessorKeys,
        stored.record,
        promoted || requestStillStored(stored),
      )
    }
  }

  const record: StoredRequest = {
    key: createKey(),
    createdAt: Date.now(),
    settled: false,
  }
  const stored = writeCompatibleRequest(
    storageKey,
    compatibilityKeys,
    predecessorKeys,
    record,
  )
  return storageEntry(
    storageKey,
    compatibilityKeys,
    predecessorKeys,
    record,
    stored,
  )
}


export function persistReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  const record = storageRecord(entry)
  if (writeCompatibleRequest(
    entry.storageKey,
    entry.compatibilityKeys,
    entry.predecessorKeys,
    record,
  )) return true
  try {
    return [entry.storageKey, ...entry.compatibilityKeys].every((storageKey) => (
      locatedRequests([storageKey]).some(
        (located) => !located.record.settled && located.record.key === entry.key,
      )
    ))
  } catch (error) {
    if (error instanceof ReportingRequestStorageReadError) return false
    throw error
  }
}


export function settleReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  const settled = writeStoredRequest('session', entry.storageKey, {
    ...storageRecord(entry),
    settled: true,
  })
  if (hasConflictingAlias(entry)) return false
  const aliasesRemoved = removeStorageKeys(entry.predecessorKeys)
  return settled && aliasesRemoved
}


export function removeReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): boolean {
  if (typeof window === 'undefined') return true
  if (hasConflictingAlias(entry)) return false
  const aliasesRemoved = removeStorageKeys(entry.predecessorKeys)
  if (removeStorageKeys([entry.storageKey]) && aliasesRemoved) return true
  const tombstoneStored = writeStoredRequest('session', entry.storageKey, {
    ...storageRecord(entry),
    settled: true,
  })
  return tombstoneStored && aliasesRemoved
}


export function clearReportingRequestStorage(): boolean {
  if (typeof window === 'undefined') return true
  const persisted = persistedRequestKeys()
  const requestsRemoved = removeStorageKeys(persisted.keys)
  const sessionSaltRemoved = removeStorageValue('session', SCOPE_SALT_KEY)
  const localSaltRemoved = removeStorageValue('local', SCOPE_SALT_KEY)
  return persisted.complete
    && requestsRemoved
    && sessionSaltRemoved
    && localSaltRemoved
}


function storageEntry(
  storageKey: string,
  compatibilityKeys: string[],
  predecessorKeys: string[],
  record: StoredRequest,
  durable: boolean,
): ReportingRequestStorageEntry {
  return {
    key: record.key,
    createdAt: record.createdAt,
    storageKey,
    compatibilityKeys,
    predecessorKeys,
    durable,
  }
}


function storageRecord(
  entry: Pick<ReportingRequestStorageEntry, 'key' | 'createdAt'>,
): StoredRequest {
  return { key: entry.key, createdAt: entry.createdAt, settled: false }
}


function assertSettledRequestMatchesAliases(
  settledCurrent: LocatedRequest[],
  located: LocatedRequest[],
): void {
  const settledKeys = new Set(settledCurrent.map(({ record }) => record.key))
  const hasConflict = settledKeys.size > 1 || located.some(({ record }) => (
    !record.settled && !settledKeys.has(record.key)
  ))
  if (hasConflict) throw conflictingRequestKeysError()
}


function resolveStoredRequest(
  located: LocatedRequest[],
): LocatedRequest | undefined {
  const unresolved = located.filter(({ record }) => !record.settled)
  const requestKeys = new Set(unresolved.map(({ record }) => record.key))
  if (requestKeys.size > 1) throw conflictingRequestKeysError()
  return unresolved[0]
}


function locatedRequests(storageKeys: string[]): LocatedRequest[] {
  const located: LocatedRequest[] = []
  for (const storageKey of storageKeys) {
    for (const location of ['session', 'local'] as const) {
      const record = readStoredRequest(location, storageKey)
      if (record) located.push({ location, storageKey, record })
    }
  }
  return located
}


function readStoredRequest(
  location: StorageLocation,
  storageKey: string,
): StoredRequest | undefined {
  const raw = getStorageValue(location, storageKey)
  if (raw === null) return undefined
  try {
    const value = JSON.parse(raw) as Record<string, unknown>
    if (
      !isValidIdempotencyKey(value.key)
      || typeof value.createdAt !== 'number'
      || !Number.isFinite(value.createdAt)
      || value.createdAt < 0
      || (value.settled !== undefined && typeof value.settled !== 'boolean')
    ) {
      removeStorageValue(location, storageKey)
      return undefined
    }
    return {
      key: value.key,
      createdAt: value.createdAt,
      settled: value.settled === true,
    }
  } catch {
    removeStorageValue(location, storageKey)
    return undefined
  }
}


function writeStoredRequest(
  location: StorageLocation,
  storageKey: string,
  record: StoredRequest,
  supersedes: string[] = [],
): boolean {
  return setStorageValue(location, storageKey, JSON.stringify({
    key: record.key,
    createdAt: record.createdAt,
    ...(record.settled ? { settled: true } : {}),
    ...(supersedes.length > 0 ? { supersedes } : {}),
  }))
}


function writeCompatibleRequest(
  storageKey: string,
  compatibilityKeys: string[],
  predecessorKeys: string[],
  record: StoredRequest,
): boolean {
  const writtenKeys = [...new Set([storageKey, ...compatibilityKeys])]
  const cleanupKeys = [...new Set([storageKey, ...predecessorKeys])]
  const v1CompatibilityKey = compatibilityKeys.find(
    (key) => !key.startsWith(`${REQUEST_STORAGE_PREFIX}v3-`),
  )
  let complete = true
  for (const key of writtenKeys) {
    const migratesToV3 = key === v1CompatibilityKey
    const supersedes = cleanupKeys.filter((candidate) => (
      candidate !== key
      && !(migratesToV3 && candidate.startsWith(`${REQUEST_STORAGE_PREFIX}v3-`))
    ))
    complete = writeStoredRequest('session', key, record, supersedes) && complete
  }
  return complete
}


function requestStillStored(request: LocatedRequest): boolean {
  const stored = readStoredRequest(request.location, request.storageKey)
  return stored?.key === request.record.key && !stored.settled
}


function hasConflictingAlias(entry: ReportingRequestStorageEntry): boolean {
  try {
    return locatedRequests(entry.predecessorKeys).some(
      ({ record }) => !record.settled && record.key !== entry.key,
    )
  } catch (error) {
    if (error instanceof ReportingRequestStorageReadError) return true
    throw error
  }
}


function conflictingRequestKeysError(): Error {
  return new Error(
    'ThreatLens found conflicting unresolved report request keys in this tab. Sign out and back in before retrying.',
  )
}


function predecessorStorageKeys(scope: string, salts: Set<string>): string[] {
  const keys = rollbackStorageKeys(scope, salts)
  for (const salt of salts) {
    const bytes = new TextEncoder().encode(`${salt}\0${scope}`)
    keys.push(`${REQUEST_STORAGE_PREFIX}v2-${bytesToHex(sha256(bytes))}`)
    keys.push(`${REQUEST_STORAGE_PREFIX}v2-${legacyFallbackScopeDigest(bytes)}`)
  }
  return [...new Set(keys)]
}


function rollbackStorageKeys(scope: string, salts: Set<string>): string[] {
  const keys = [...salts].map((salt) => (
    `${REQUEST_STORAGE_PREFIX}v3-${scopeDigest(`${salt}\0${scope}`)}`
  ))
  keys.push(`${REQUEST_STORAGE_PREFIX}${legacyScopeDigest(scope)}`)
  return [...new Set(keys)]
}


function storedScopeSalts(): Set<string> {
  return new Set([
    validScopeSalt(getStorageValue('session', SCOPE_SALT_KEY)),
    validScopeSalt(getStorageValue('local', SCOPE_SALT_KEY)),
  ].filter((value): value is string => value !== undefined))
}


function validScopeSalt(value: string | null): string | undefined {
  return value && /^[0-9a-f]{64}$/i.test(value) ? value.toLowerCase() : undefined
}


function getStorageValue(location: StorageLocation, key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : browserStorage(location).getItem(key)
  } catch {
    throw new ReportingRequestStorageReadError()
  }
}


function setStorageValue(
  location: StorageLocation,
  key: string,
  value: string,
): boolean {
  try {
    if (typeof window === 'undefined') return false
    const storage = browserStorage(location)
    storage.setItem(key, value)
    return storage.getItem(key) === value
  } catch {
    return false
  }
}


function removeStorageKeys(keys: string[]): boolean {
  let removed = true
  for (const key of keys) {
    const sessionRemoved = removeStorageValue('session', key)
    const localRemoved = removeStorageValue('local', key)
    removed = sessionRemoved && localRemoved && removed
  }
  return removed
}


function removeStorageValue(location: StorageLocation, key: string): boolean {
  try {
    if (typeof window === 'undefined') return false
    const storage = browserStorage(location)
    storage.removeItem(key)
    return storage.getItem(key) === null
  } catch {
    return false
  }
}


function browserStorage(location: StorageLocation): Storage {
  return location === 'session' ? window.sessionStorage : window.localStorage
}


function persistedRequestKeys(): { keys: string[], complete: boolean } {
  const keys = new Set<string>()
  let complete = true
  for (const location of ['session', 'local'] as const) {
    try {
      if (typeof window === 'undefined') continue
      const storage = browserStorage(location)
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index)
        if (key?.startsWith(REQUEST_STORAGE_PREFIX)) keys.add(key)
      }
    } catch {
      complete = false
    }
  }
  return { keys: [...keys], complete }
}


function scopeDigest(scope: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(scope)))
}


function legacyScopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
}


function legacyFallbackScopeDigest(bytes: Uint8Array): string {
  const seeds = [
    0xcbf29ce484222325n,
    0x84222325cbf29cen,
    0x9e3779b97f4a7c15n,
    0x6a09e667f3bcc909n,
  ]
  return seeds.map((seed, lane) => {
    let hash = seed
    for (let index = 0; index < bytes.length; index += 1) {
      hash ^= BigInt(bytes[index] ^ ((index + lane * 67) & 0xff))
      hash = BigInt.asUintN(64, hash * 0x100000001b3n)
      hash ^= hash >> 32n
    }
    return BigInt.asUintN(64, hash).toString(16).padStart(16, '0')
  }).join('')
}


function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}


function isValidIdempotencyKey(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= 255
    && /^[A-Za-z0-9._~:-]+$/.test(value)
}


class ReportingRequestStorageReadError extends Error {
  constructor() {
    super(
      'ThreatLens cannot inspect unresolved report request keys because browser storage is unreadable. Restore browser storage, reload, and retry; no request was sent.',
    )
    this.name = 'ReportingRequestStorageReadError'
  }
}
