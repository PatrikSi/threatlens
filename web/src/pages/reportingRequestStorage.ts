import { sha256 } from '@noble/hashes/sha2.js'

import { withReportingStorageLock } from './reportingStorageLock'


const REQUEST_STORAGE_PREFIX = 'threatlens.reporting-request.'
const SCOPE_SALT_KEY = 'threatlens.reporting-scope-salt'

type StorageLocation = 'local' | 'session'
type StorageStatus = {
  durable: boolean
  shared: boolean
}
type StoredRequest = {
  key: string
  createdAt: number
  supersedes: string[]
}
type LocatedRequest = {
  record: StoredRequest
  location: StorageLocation
}
type ScopeSalt = StorageStatus & {
  value: string
}
export type ReportingRequestStorageEntry = StorageStatus & StoredRequest & {
  storageKey: string
}


export async function acquireReportingRequestStorage(
  scope: string,
  createKey: () => string,
  assertCurrent: () => void,
): Promise<ReportingRequestStorageEntry> {
  return withReportingStorageLock(() => {
    assertCurrent()
    const historicalSalts = storedScopeSalts()
    const salt = getOrCreateScopeSalt()
    historicalSalts.add(salt.value)
    const digest = scopeDigest(`${salt.value}\0${scope}`)
    const storageKey = `${REQUEST_STORAGE_PREFIX}v3-${digest}`
    const migrationMarkerKey = `${REQUEST_STORAGE_PREFIX}migration-${digest}`
    const current = loadStoredRequest(storageKey)
    if (current) {
      const status = promoteStoredRequest(storageKey, current)
      const migrationIsSafe = finishInterruptedMigration(
        current.record,
        migrationMarkerKey,
      )
      return storageEntry(
        storageKey,
        current.record,
        status,
        salt.shared && migrationIsSafe,
      )
    }

    if (getStorageValueAt('local', migrationMarkerKey) === null) {
      const migrated = migratePredecessor(
        scope,
        historicalSalts,
        storageKey,
        migrationMarkerKey,
        salt.shared,
      )
      if (migrated) return migrated
    }

    const record: StoredRequest = {
      key: createKey(),
      createdAt: Date.now(),
      supersedes: [],
    }
    const status = persistStoredRequest(storageKey, record)
    return storageEntry(storageKey, record, status, salt.shared)
  })
}


export function persistReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): StorageStatus {
  const status = persistStoredRequest(entry.storageKey, entry)
  return {
    durable: status.durable,
    shared: status.shared && entry.shared,
  }
}


export function removeReportingRequestStorage(
  entry: ReportingRequestStorageEntry,
): void {
  removeStorageValue(entry.storageKey)
  for (const predecessor of entry.supersedes) removeStorageValue(predecessor)
}


export function clearReportingRequestStorage(): void {
  for (const key of persistedRequestKeys()) removeStorageValue(key)
  removeStorageValue(SCOPE_SALT_KEY)
}


function migratePredecessor(
  scope: string,
  historicalSalts: Set<string>,
  storageKey: string,
  migrationMarkerKey: string,
  saltIsShared: boolean,
): ReportingRequestStorageEntry | undefined {
  for (const predecessorKey of predecessorStorageKeys(scope, historicalSalts)) {
    const predecessor = loadStoredRequest(predecessorKey)
    if (!predecessor) continue
    const record: StoredRequest = {
      ...predecessor.record,
      supersedes: uniqueStorageKeys([
        ...predecessor.record.supersedes,
        predecessorKey,
      ]),
    }
    const replacement = persistStoredRequest(storageKey, record)
    if (!replacement.shared || !saltIsShared) {
      removeStorageValue(storageKey)
      return storageEntry(
        predecessorKey,
        predecessor.record,
        locationStatus(predecessor.location),
        false,
      )
    }
    const marker = setStorageValue(migrationMarkerKey, '1')
    if (!marker.shared) {
      removeStorageValue(storageKey)
      return storageEntry(
        predecessorKey,
        predecessor.record,
        locationStatus(predecessor.location),
        false,
      )
    }
    removeStorageValue(predecessorKey)
    return storageEntry(storageKey, record, replacement, true)
  }
  return undefined
}


function finishInterruptedMigration(
  record: StoredRequest,
  migrationMarkerKey: string,
): boolean {
  if (record.supersedes.length === 0) return true
  if (getStorageValueAt('local', migrationMarkerKey) !== null) {
    for (const predecessor of record.supersedes) removeStorageValue(predecessor)
    return true
  }
  const marker = setStorageValue(migrationMarkerKey, '1')
  if (!marker.shared) return false
  for (const predecessor of record.supersedes) removeStorageValue(predecessor)
  return true
}


function predecessorStorageKeys(scope: string, salts: Set<string>): string[] {
  const keys: string[] = []
  for (const salt of salts) {
    const bytes = new TextEncoder().encode(`${salt}\0${scope}`)
    keys.push(`${REQUEST_STORAGE_PREFIX}v2-${bytesToHex(sha256(bytes))}`)
    keys.push(`${REQUEST_STORAGE_PREFIX}v2-${legacyFallbackScopeDigest(bytes)}`)
  }
  keys.push(`${REQUEST_STORAGE_PREFIX}${legacyScopeDigest(scope)}`)
  return uniqueStorageKeys(keys)
}


function storageEntry(
  storageKey: string,
  record: StoredRequest,
  status: StorageStatus,
  saltIsShared: boolean,
): ReportingRequestStorageEntry {
  return {
    ...record,
    storageKey,
    durable: status.durable,
    shared: status.shared && saltIsShared,
  }
}


function promoteStoredRequest(
  storageKey: string,
  current: LocatedRequest,
): StorageStatus {
  if (current.location === 'local') return { durable: true, shared: true }
  const promoted = persistStoredRequest(storageKey, current.record)
  return promoted.durable ? promoted : { durable: true, shared: false }
}


function persistStoredRequest(
  storageKey: string,
  record: Pick<StoredRequest, 'key' | 'createdAt' | 'supersedes'>,
): StorageStatus {
  return setStorageValue(storageKey, JSON.stringify({
    key: record.key,
    createdAt: record.createdAt,
    ...(record.supersedes.length > 0 ? { supersedes: record.supersedes } : {}),
  }))
}


function loadStoredRequest(storageKey: string): LocatedRequest | undefined {
  for (const location of ['local', 'session'] as const) {
    const raw = getStorageValueAt(location, storageKey)
    if (raw === null) continue
    try {
      const value = JSON.parse(raw) as Record<string, unknown>
      if (
        !isValidIdempotencyKey(value.key)
        || typeof value.createdAt !== 'number'
        || !Number.isFinite(value.createdAt)
        || value.createdAt < 0
      ) {
        removeStorageValueAt(location, storageKey)
        continue
      }
      return {
        location,
        record: {
          key: value.key,
          createdAt: value.createdAt,
          supersedes: validSupersededStorageKeys(value.supersedes),
        },
      }
    } catch {
      removeStorageValueAt(location, storageKey)
    }
  }
  return undefined
}


function validSupersededStorageKeys(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return uniqueStorageKeys(value.filter((item): item is string => (
    typeof item === 'string'
    && item.startsWith(REQUEST_STORAGE_PREFIX)
    && !item.includes('.migration-')
  ))).slice(0, 16)
}


function getOrCreateScopeSalt(): ScopeSalt {
  const localSalt = validScopeSalt(getStorageValueAt('local', SCOPE_SALT_KEY))
  if (localSalt) return { value: localSalt, durable: true, shared: true }

  const sessionSalt = validScopeSalt(getStorageValueAt('session', SCOPE_SALT_KEY))
  const value = sessionSalt ?? bytesToHex(randomBytes(32))
  const status = setStorageValue(SCOPE_SALT_KEY, value)
  return { value, ...status }
}


function storedScopeSalts(): Set<string> {
  return new Set([
    validScopeSalt(getStorageValueAt('local', SCOPE_SALT_KEY)),
    validScopeSalt(getStorageValueAt('session', SCOPE_SALT_KEY)),
  ].filter((value): value is string => value !== undefined))
}


function validScopeSalt(value: string | null): string | undefined {
  return value && /^[0-9a-f]{64}$/i.test(value) ? value.toLowerCase() : undefined
}


function getStorageValueAt(location: StorageLocation, key: string): string | null {
  try {
    if (typeof window === 'undefined') return null
    return browserStorage(location).getItem(key)
  } catch {
    return null
  }
}


function setStorageValue(key: string, value: string): StorageStatus {
  if (setStorageValueAt('local', key, value)) {
    removeStorageValueAt('session', key)
    return { durable: true, shared: true }
  }
  if (setStorageValueAt('session', key, value)) {
    return { durable: true, shared: false }
  }
  return { durable: false, shared: false }
}


function setStorageValueAt(
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


function removeStorageValue(key: string): void {
  removeStorageValueAt('local', key)
  removeStorageValueAt('session', key)
}


function removeStorageValueAt(location: StorageLocation, key: string): void {
  try {
    if (typeof window !== 'undefined') browserStorage(location).removeItem(key)
  } catch {
    // The caller has already removed its in-memory reference.
  }
}


function browserStorage(location: StorageLocation): Storage {
  return location === 'local' ? window.localStorage : window.sessionStorage
}


function persistedRequestKeys(): string[] {
  const keys = new Set<string>()
  collectStorageKeys('local', keys)
  collectStorageKeys('session', keys)
  return [...keys]
}


function collectStorageKeys(location: StorageLocation, keys: Set<string>): void {
  try {
    if (typeof window === 'undefined') return
    const storage = browserStorage(location)
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index)
      if (key?.startsWith(REQUEST_STORAGE_PREFIX)) keys.add(key)
    }
  } catch {
    // Storage cleanup remains best effort when the browser revokes access.
  }
}


function locationStatus(location: StorageLocation): StorageStatus {
  return { durable: true, shared: location === 'local' }
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


function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length)
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    return globalThis.crypto.getRandomValues(bytes)
  }
  for (let index = 0; index < length; index += 1) {
    bytes[index] = Math.floor(Math.random() * 256)
  }
  return bytes
}


function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}


function uniqueStorageKeys(keys: string[]): string[] {
  return [...new Set(keys)]
}


function isValidIdempotencyKey(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= 255
    && /^[A-Za-z0-9._~:-]+$/.test(value)
}

