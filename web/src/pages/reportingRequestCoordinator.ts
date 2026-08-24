const PENDING_REQUEST_TTL_MS = 24 * 60 * 60 * 1000
const STORAGE_KEY_PREFIX = 'threatlens.reporting-request.'

type PendingRequest = {
  key: string
  createdAt: number
  inFlight: number
  retainAfterSettlement: boolean
}

const pendingRequests = new Map<string, PendingRequest>()
const activeRequests = new Map<string, Promise<unknown>>()
const writeTails = new Map<string, WriteTail>()
const knownStorageKeys = new Set<string>()

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


export function beginPendingReportingRequest(scope: string): string {
  const now = Date.now()
  let request = pendingRequests.get(scope) ?? loadPendingRequest(scope)
  if (
    !request
    || (request.inFlight === 0 && now - request.createdAt >= PENDING_REQUEST_TTL_MS)
  ) {
    request = {
      key: createIdempotencyKey(),
      createdAt: now,
      inFlight: 0,
      retainAfterSettlement: false,
    }
    pendingRequests.set(scope, request)
    persistPendingRequest(scope, request)
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
    removePersistedRequest(scope)
  } else {
    persistPendingRequest(scope, request)
  }
}


export function resetPendingReportingKeys(): void {
  pendingRequests.clear()
  activeRequests.clear()
  writeTails.clear()
  for (const key of persistedRequestKeys()) removeSessionStorageItem(key)
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


function loadPendingRequest(scope: string): PendingRequest | undefined {
  const storageKey = pendingRequestStorageKey(scope)
  const raw = getSessionStorageItem(storageKey)
  if (!raw) return undefined
  try {
    const stored = JSON.parse(raw) as Partial<PendingRequest>
    const now = Date.now()
    if (
      typeof stored.key !== 'string'
      || stored.key.length === 0
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
    }
    pendingRequests.set(scope, request)
    return request
  } catch {
    removeSessionStorageItem(storageKey)
    return undefined
  }
}


function persistPendingRequest(scope: string, request: PendingRequest): void {
  const storageKey = pendingRequestStorageKey(scope)
  knownStorageKeys.add(storageKey)
  setSessionStorageItem(storageKey, JSON.stringify({
    key: request.key,
    createdAt: request.createdAt,
  }))
}


function removePersistedRequest(scope: string): void {
  const storageKey = pendingRequestStorageKey(scope)
  knownStorageKeys.delete(storageKey)
  removeSessionStorageItem(storageKey)
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


function pendingRequestStorageKey(scope: string): string {
  return `${STORAGE_KEY_PREFIX}${scopeDigest(scope)}`
}


function scopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
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
