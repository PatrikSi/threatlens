const PENDING_REQUEST_TTL_MS = 24 * 60 * 60 * 1000

type PendingRequest = {
  key: string
  createdAt: number
}

const pendingRequests = new Map<string, PendingRequest>()


export function reportingRequestScope(
  userId: string | undefined,
  operation: string,
  identity: string,
): string {
  return JSON.stringify([userId ?? 'unknown-user', operation, identity])
}


export function getOrCreatePendingReportingKey(scope: string): string {
  const now = Date.now()
  const existing = pendingRequests.get(scope)
  if (existing && now - existing.createdAt < PENDING_REQUEST_TTL_MS) {
    return existing.key
  }
  const key = createIdempotencyKey()
  pendingRequests.set(scope, { key, createdAt: now })
  return key
}


export function clearPendingReportingKey(scope: string, key: string): void {
  if (pendingRequests.get(scope)?.key === key) pendingRequests.delete(scope)
}


export function resetPendingReportingKeys(): void {
  pendingRequests.clear()
}


export function reportMutationRequestKey(entityKey: string, body: string): string {
  return `${entityKey}\0${body}`
}


export function coalesceRequest<Key, Result>(
  requests: Map<Key, Promise<Result>>,
  key: Key,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const activeRequest = requests.get(key)
  if (activeRequest) return activeRequest

  const request = createRequest()
  requests.set(key, request)
  const clear = () => {
    if (requests.get(key) === request) requests.delete(key)
  }
  void request.then(clear, clear)
  return request
}


export function serializeCoalescedRequest<EntityKey, RequestKey, Result>(
  requests: Map<RequestKey, Promise<Result>>,
  tails: Map<EntityKey, Promise<void>>,
  entityKey: EntityKey,
  requestKey: RequestKey,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const activeRequest = requests.get(requestKey)
  if (activeRequest) return activeRequest

  const predecessor = tails.get(entityKey)
  const request = (predecessor ?? Promise.resolve()).then(createRequest)
  requests.set(requestKey, request)

  const tail = request.then(
    () => undefined,
    () => undefined,
  )
  tails.set(entityKey, tail)
  void tail.then(() => {
    if (requests.get(requestKey) === request) requests.delete(requestKey)
    if (tails.get(entityKey) === tail) tails.delete(entityKey)
  })
  return request
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
