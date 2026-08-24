import {
  acquireReportingRequestStorage,
  clearReportingRequestStorage,
  persistReportingRequestStorage,
  removeReportingRequestStorage,
  type ReportingRequestStorageEntry,
} from './reportingRequestStorage'


type PendingRequest = ReportingRequestStorageEntry & {
  inFlight: number
  retainAfterSettlement: boolean
}
type WriteTail = {
  requestKey: string
  request: Promise<unknown>
  settled: Promise<void>
}

const pendingRequests = new Map<string, PendingRequest>()
const activeRequests = new Map<string, Promise<unknown>>()
const writeTails = new Map<string, WriteTail>()
let coordinationGeneration = 0

export type ReportingRequestOutcome = 'confirmed' | 'ambiguous' | 'blocked' | 'rejected'
export type ReportingRequestLease = {
  key: string
  generation: number
  durable: boolean
  shared: boolean
}
export type ReportingRequestSettlement = {
  durable: boolean
  shared: boolean
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
  const startingGeneration = coordinationGeneration
  let request = pendingRequests.get(scope)
  if (!request) {
    const stored = await acquireReportingRequestStorage(
      scope,
      createIdempotencyKey,
      () => requireCurrentCoordinationGeneration(startingGeneration),
    )
    requireCurrentCoordinationGeneration(startingGeneration)
    request = pendingRequests.get(scope)
    if (!request) {
      request = {
        ...stored,
        inFlight: 0,
        retainAfterSettlement: false,
      }
      pendingRequests.set(scope, request)
    }
  }
  if (request.inFlight === 0) request.retainAfterSettlement = false
  request.inFlight += 1
  requireCurrentCoordinationGeneration(startingGeneration)
  return {
    key: request.key,
    generation: startingGeneration,
    durable: request.durable,
    shared: request.shared,
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
): ReportingRequestSettlement {
  const request = pendingRequests.get(scope)
  if (!request || request.key !== key) {
    return { durable: false, shared: false }
  }
  request.inFlight = Math.max(0, request.inFlight - 1)
  if (outcome === 'ambiguous' || outcome === 'blocked') {
    request.retainAfterSettlement = true
  }
  if (request.inFlight === 0 && !request.retainAfterSettlement) {
    pendingRequests.delete(scope)
    removeReportingRequestStorage(request)
    return { durable: true, shared: true }
  }
  const status = persistReportingRequestStorage(request)
  request.durable = status.durable
  request.shared = status.shared
  return status
}


export function resetPendingReportingKeys(): void {
  coordinationGeneration += 1
  pendingRequests.clear()
  activeRequests.clear()
  writeTails.clear()
  clearReportingRequestStorage()
}


export function reportMutationRequestKey(entityKey: string, body: string): string {
  return `${entityKey}\0${body}`
}


export function coalesceReportingRequest<Result>(
  key: string,
  createRequest: () => Promise<Result>,
): Promise<Result> {
  const requestGeneration = coordinationGeneration
  const activeRequest = activeRequests.get(key)
  if (activeRequest) return activeRequest as Promise<Result>

  let createdRequest: Promise<Result>
  try {
    createdRequest = createRequest()
  } catch (error) {
    createdRequest = Promise.reject(error)
  }
  const request = createdRequest.then((result) => {
    requireCurrentCoordinationGeneration(requestGeneration)
    return result
  })
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
    return createRequest().then((result) => {
      requireCurrentCoordinationGeneration(queuedGeneration)
      return result
    })
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


function requireCurrentCoordinationGeneration(startingGeneration: number): void {
  if (startingGeneration !== coordinationGeneration) {
    throw new Error(
      'Authentication changed before the reporting request was prepared. Retry the action after signing in.',
    )
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
