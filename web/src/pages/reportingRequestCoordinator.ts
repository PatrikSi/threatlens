const PENDING_REQUEST_TTL_MS = 24 * 60 * 60 * 1000
const STORAGE_KEY_PREFIX = 'threatlens.reporting-request.'
const STORAGE_SALT_KEY = 'threatlens.reporting-scope-salt'

type PendingRequest = {
  key: string
  createdAt: number
  inFlight: number
  retainAfterSettlement: boolean
  storageKey: string
}

const pendingRequests = new Map<string, PendingRequest>()
const activeRequests = new Map<string, Promise<unknown>>()
const writeTails = new Map<string, WriteTail>()
const knownStorageKeys = new Set<string>()
let coordinationGeneration = 0

type WriteTail = {
  requestKey: string
  request: Promise<unknown>
  settled: Promise<void>
}

export type ReportingRequestOutcome = 'confirmed' | 'ambiguous' | 'rejected'


export function reportingRequestScope(
  userId: string | undefined,
  operation: string,
  identity: string,
): string {
  return JSON.stringify([userId ?? 'unknown-user', operation, identity])
}


export async function beginPendingReportingRequest(scope: string): Promise<string> {
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
    }
    pendingRequests.set(scope, request)
    persistPendingRequest(request)
  }
  if (request.inFlight === 0) request.retainAfterSettlement = false
  request.inFlight += 1
  return request.key
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
    persistPendingRequest(request)
  }
}


export function resetPendingReportingKeys(): void {
  coordinationGeneration += 1
  pendingRequests.clear()
  activeRequests.clear()
  writeTails.clear()
  for (const key of persistedRequestKeys()) removeSessionStorageItem(key)
  removeSessionStorageItem(STORAGE_SALT_KEY)
  knownStorageKeys.clear()
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
  const predecessor = writeTails.get(entityKey)
  if (predecessor?.requestKey === requestKey) {
    return predecessor.request as Promise<Result>
  }
  const request = (predecessor?.settled ?? Promise.resolve()).then(createRequest)

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
  const raw = getSessionStorageItem(storageKey)
  if (!raw) return undefined
  try {
    const stored = JSON.parse(raw) as Partial<PendingRequest>
    const now = Date.now()
    if (
      !isValidIdempotencyKey(stored.key)
      || typeof stored.createdAt !== 'number'
      || !Number.isFinite(stored.createdAt)
      || stored.createdAt < 0
      || stored.createdAt > now
    ) {
      removeSessionStorageItem(storageKey)
      return undefined
    }
    knownStorageKeys.add(storageKey)
    const request = {
      key: stored.key,
      createdAt: stored.createdAt,
      inFlight: 0,
      retainAfterSettlement: false,
      storageKey,
    }
    pendingRequests.set(scope, request)
    return request
  } catch {
    removeSessionStorageItem(storageKey)
    return undefined
  }
}


function persistPendingRequest(request: PendingRequest): void {
  knownStorageKeys.add(request.storageKey)
  setSessionStorageItem(request.storageKey, JSON.stringify({
    key: request.key,
    createdAt: request.createdAt,
  }))
}


function removePersistedRequest(request: PendingRequest): void {
  knownStorageKeys.delete(request.storageKey)
  removeSessionStorageItem(request.storageKey)
}


function persistedRequestKeys(): string[] {
  try {
    if (typeof window === 'undefined') return [...knownStorageKeys]
    const keys: string[] = []
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index)
      if (key?.startsWith(STORAGE_KEY_PREFIX)) keys.push(key)
    }
    return keys
  } catch {
    return [...knownStorageKeys]
  }
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
  return fallbackScopeDigest(bytes)
}


function migrateLegacyPendingRequest(
  scope: string,
  storageKey: string,
): PendingRequest | undefined {
  const legacyStorageKey = `${STORAGE_KEY_PREFIX}${legacyScopeDigest(scope)}`
  const request = loadPendingRequest(scope, legacyStorageKey)
  if (!request) return undefined
  knownStorageKeys.delete(legacyStorageKey)
  removeSessionStorageItem(legacyStorageKey)
  request.storageKey = storageKey
  persistPendingRequest(request)
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


function fallbackScopeDigest(bytes: Uint8Array): string {
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


function getOrCreateScopeSalt(): string {
  const stored = getSessionStorageItem(STORAGE_SALT_KEY)
  if (stored && /^[0-9a-f]{64}$/i.test(stored)) return stored.toLowerCase()
  const salt = bytesToHex(randomBytes(32))
  setSessionStorageItem(STORAGE_SALT_KEY, salt)
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


function getSessionStorageItem(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}


function setSessionStorageItem(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined') window.sessionStorage.setItem(key, value)
  } catch {
    // In-memory coordination remains available when browser storage is denied.
  }
}


function removeSessionStorageItem(key: string): void {
  try {
    if (typeof window !== 'undefined') window.sessionStorage.removeItem(key)
  } catch {
    // The in-memory record has already been removed.
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
