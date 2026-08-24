const PENDING_REQUEST_TTL_MS = 24 * 60 * 60 * 1000

type PendingRequest = {
  key: string
  createdAt: number
  inFlight: number
  retainAfterSettlement: boolean
}

const pendingRequests = new Map<string, PendingRequest>()
const activeRequests = new Map<string, Promise<unknown>>()
const writeTails = new Map<string, Promise<void>>()

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
  let request = pendingRequests.get(scope)
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
  }
}


export function resetPendingReportingKeys(): void {
  pendingRequests.clear()
  activeRequests.clear()
  writeTails.clear()
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
  const activeRequest = activeRequests.get(requestKey)
  if (activeRequest) return activeRequest as Promise<Result>

  const predecessor = writeTails.get(entityKey)
  const request = (predecessor ?? Promise.resolve()).then(createRequest)
  activeRequests.set(requestKey, request)

  const tail = request.then(
    () => undefined,
    () => undefined,
  )
  writeTails.set(entityKey, tail)
  void tail.then(() => {
    if (activeRequests.get(requestKey) === request) activeRequests.delete(requestKey)
    if (writeTails.get(entityKey) === tail) writeTails.delete(entityKey)
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
