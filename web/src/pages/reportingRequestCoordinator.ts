import { sha256 } from '@noble/hashes/sha2.js'


const PENDING_REQUEST_TTL_MS = 24 * 60 * 60 * 1000
const STORAGE_KEY_PREFIX = 'threatlens.reporting-request.'
const STORAGE_SALT_KEY = 'threatlens.reporting-scope-salt'

type PendingRequest = {
  key: string
  createdAt: number
  inFlight: number
  retainAfterSettlement: boolean
  storageKey: string
  durable: boolean
}

const pendingRequests = new Map<string, PendingRequest>()
const activeRequests = new Map<string, Promise<unknown>>()
const writeTails = new Map<string, WriteTail>()
const knownStorageKeys = new Set<string>()
let coordinationGeneration = 0
let volatileScopeSalt: string | undefined
let scopeSaltIsDurable = false

type WriteTail = {
  requestKey: string
  request: Promise<unknown>
  settled: Promise<void>
}

export type ReportingRequestOutcome = 'confirmed' | 'ambiguous' | 'rejected'
export type ReportingRequestLease = {
  key: string
  generation: number
  durable: boolean
}


export function reportingRequestScope(
  userId: string | undefined,
  operation: string,
  identity: string,
): string {
  return JSON.stringify([userId ?? 'unknown-user', operation, identity])
}


export async function beginPendingReportingRequest(scope: string): Promise<string> {
  const lease = await beginPendingReportingRequestLease(scope)
  assertReportingRequestLeaseCurrent(lease)
  return lease.key
}


export async function beginPendingReportingRequestLease(
  scope: string,
): Promise<ReportingRequestLease> {
  const now = Date.now()
  const startingGeneration = coordinationGeneration
  let request = pendingRequests.get(scope)
  let storageKey: string | undefined
  if (!request) {
    storageKey = await pendingRequestStorageKey(scope)
    requireCurrentCoordinationGeneration(startingGeneration)
    request = pendingRequests.get(scope)
      ?? loadPendingRequest(scope, storageKey)
      ?? migrateLegacyPendingRequest(scope, storageKey)
  }
  if (
    !request
    || (request.inFlight === 0 && now - request.createdAt >= PENDING_REQUEST_TTL_MS)
  ) {
    if (!storageKey) {
      storageKey = await pendingRequestStorageKey(scope)
      requireCurrentCoordinationGeneration(startingGeneration)
    }
    request = {
      key: createIdempotencyKey(),
      createdAt: now,
      inFlight: 0,
      retainAfterSettlement: false,
      storageKey,
      durable: false,
    }
    pendingRequests.set(scope, request)
    request.durable = persistPendingRequest(request)
  }
  if (request.inFlight === 0) request.retainAfterSettlement = false
  request.inFlight += 1
  requireCurrentCoordinationGeneration(startingGeneration)
  return {
    key: request.key,
    generation: startingGeneration,
    durable: request.durable,
  }
}


export function assertReportingRequestLeaseCurrent(
  lease: ReportingRequestLease,
): void {
  requireCurrentCoordinationGeneration(lease.generation)
}


export function settlePendingReportingRequest(
  scope: string,
  key: string,
  outcome: ReportingRequestOutcome,
): void {
  const request = pendingRequests.get(scope)
  if (!request || request.key !== key) return
  request.inFlight = Math.max(0, request.inFlight - 1)
  if (outcome === 'ambiguous') request.retainAfterSettlement = true
  if (request.inFlight === 0 && !request.retainAfterSettlement) {
    pendingRequests.delete(scope)
    removePersistedRequest(request)
  } else {
    request.durable = persistPendingRequest(request) || request.durable
  }
}


export function resetPendingReportingKeys(): void {
  coordinationGeneration += 1
  pendingRequests.clear()
  activeRequests.clear()
  writeTails.clear()
  for (const key of persistedRequestKeys()) removeBrowserStorageItem(key)
  removeBrowserStorageItem(STORAGE_SALT_KEY)
  knownStorageKeys.clear()
  volatileScopeSalt = undefined
  scopeSaltIsDurable = false
}


export function reportMutationRequestKey(entityKey: string, body: string): string {
  return `${entityKey}\0${body}`
}


export function coalesceReportingRequest<Result>(
  key: string,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const activeRequest = activeRequests.get(key)
  if (activeRequest) return activeRequest as Promise<Result>

  const request = createRequest()
  activeRequests.set(key, request)
  const clear = () => {
    if (activeRequests.get(key) === request) activeRequests.delete(key)
  }
  void request.then(clear, clear)
  return request
}


export function serializeReportingWrite<Result>(
  entityKey: string,
  requestKey: string,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const queuedGeneration = coordinationGeneration
  const predecessor = writeTails.get(entityKey)
  if (predecessor?.requestKey === requestKey) {
    return predecessor.request as Promise<Result>
  }
  const request = (predecessor?.settled ?? Promise.resolve()).then(() => {
    requireCurrentCoordinationGeneration(queuedGeneration)
    return createRequest()
  })

  const settled = request.then(
    () => undefined,
    () => undefined,
  )
  const tail: WriteTail = { requestKey, request, settled }
  writeTails.set(entityKey, tail)
  void settled.then(() => {
    if (writeTails.get(entityKey) === tail) writeTails.delete(entityKey)
  })
  return request
}


function loadPendingRequest(
  scope: string,
  storageKey: string,
): PendingRequest | undefined {
  const raw = getBrowserStorageItem(storageKey)
  if (!raw) return undefined
  try {
    const stored = JSON.parse(raw) as Partial<PendingRequest>
    const now = Date.now()
    if (
      !isValidIdempotencyKey(stored.key)
      || typeof stored.createdAt !== 'number'
      || !Number.isFinite(stored.createdAt)
      || stored.createdAt < 0
    ) {
      removeBrowserStorageItem(storageKey)
      return undefined
    }
    knownStorageKeys.add(storageKey)
    const request = {
      key: stored.key,
      createdAt: Math.min(stored.createdAt, now),
      inFlight: 0,
      retainAfterSettlement: false,
      storageKey,
      durable: true,
    }
    pendingRequests.set(scope, request)
    if (stored.createdAt > now) persistPendingRequest(request)
    return request
  } catch {
    removeBrowserStorageItem(storageKey)
    return undefined
  }
}


function persistPendingRequest(request: PendingRequest): boolean {
  if (request.storageKey.includes(`${STORAGE_KEY_PREFIX}v2-`) && !scopeSaltIsDurable) {
    return false
  }
  const persisted = setBrowserStorageItem(request.storageKey, JSON.stringify({
    key: request.key,
    createdAt: request.createdAt,
  }))
  if (persisted) knownStorageKeys.add(request.storageKey)
  return persisted
}


function removePersistedRequest(request: PendingRequest): void {
  knownStorageKeys.delete(request.storageKey)
  removeBrowserStorageItem(request.storageKey)
}


function persistedRequestKeys(): string[] {
  const keys = new Set(knownStorageKeys)
  collectStorageKeys(() => window.sessionStorage, keys)
  collectStorageKeys(() => window.localStorage, keys)
  return [...keys]
}


async function pendingRequestStorageKey(scope: string): Promise<string> {
  const salt = getOrCreateScopeSalt()
  return `${STORAGE_KEY_PREFIX}v2-${await scopeDigest(`${salt}\0${scope}`)}`
}


async function scopeDigest(scope: string): Promise<string> {
  const bytes = new TextEncoder().encode(scope)
  try {
    if (globalThis.crypto?.subtle) {
      const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
      return bytesToHex(new Uint8Array(digest))
    }
  } catch {
    // The deterministic fallback keeps HTTP-only and restricted browsers usable.
  }
  return bytesToHex(sha256(bytes))
}


function migrateLegacyPendingRequest(
  scope: string,
  storageKey: string,
): PendingRequest | undefined {
  const legacyStorageKey = `${STORAGE_KEY_PREFIX}${legacyScopeDigest(scope)}`
  const request = loadPendingRequest(scope, legacyStorageKey)
  if (!request) return undefined
  const migratedRequest = { ...request, storageKey }
  if (!persistPendingRequest(migratedRequest)) return request
  knownStorageKeys.delete(legacyStorageKey)
  removeBrowserStorageItem(legacyStorageKey)
  request.storageKey = storageKey
  return request
}


function legacyScopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
}


function getOrCreateScopeSalt(): string {
  const stored = getBrowserStorageItem(STORAGE_SALT_KEY)
  if (stored && /^[0-9a-f]{64}$/i.test(stored)) {
    volatileScopeSalt = stored.toLowerCase()
    scopeSaltIsDurable = true
    return volatileScopeSalt
  }
  if (volatileScopeSalt) return volatileScopeSalt
  const salt = bytesToHex(randomBytes(32))
  volatileScopeSalt = salt
  scopeSaltIsDurable = setBrowserStorageItem(STORAGE_SALT_KEY, salt)
  return salt
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


function isValidIdempotencyKey(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= 255
    && /^[A-Za-z0-9._~:-]+$/.test(value)
}


function requireCurrentCoordinationGeneration(startingGeneration: number): void {
  if (startingGeneration !== coordinationGeneration) {
    throw new Error(
      'Authentication changed before the reporting request was prepared. Retry the action after signing in.',
    )
  }
}


function getBrowserStorageItem(key: string): string | null {
  try {
    if (typeof window === 'undefined') return null
    const sessionValue = window.sessionStorage.getItem(key)
    if (sessionValue !== null) return sessionValue
  } catch {
    // Fall through to local storage when session storage is unavailable.
  }
  try {
    return typeof window === 'undefined' ? null : window.localStorage.getItem(key)
  } catch {
    return null
  }
}


function setBrowserStorageItem(key: string, value: string): boolean {
  try {
    if (typeof window === 'undefined') return false
    window.sessionStorage.setItem(key, value)
    if (window.sessionStorage.getItem(key) !== value) return false
    try {
      window.localStorage.removeItem(key)
    } catch {
      // A stale fallback record is harmless while session storage is readable.
    }
    return true
  } catch {
    // Fall through to local storage when session storage is unavailable.
  }
  try {
    if (typeof window === 'undefined') return false
    window.localStorage.setItem(key, value)
    return window.localStorage.getItem(key) === value
  } catch {
    return false
  }
}


function removeBrowserStorageItem(key: string): void {
  try {
    if (typeof window !== 'undefined') window.sessionStorage.removeItem(key)
  } catch {
    // Continue so a local-storage fallback can still be removed.
  }
  try {
    if (typeof window !== 'undefined') window.localStorage.removeItem(key)
  } catch {
    // The in-memory record has already been removed.
  }
}


function collectStorageKeys(
  getStorage: () => Storage,
  keys: Set<string>,
): void {
  try {
    if (typeof window === 'undefined') return
    const storage = getStorage()
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index)
      if (key?.startsWith(STORAGE_KEY_PREFIX)) keys.add(key)
    }
  } catch {
    // Known in-memory keys remain available for cleanup.
  }
}


function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}
