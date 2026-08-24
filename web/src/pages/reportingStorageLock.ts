const STORAGE_LOCK_NAME = 'threatlens.reporting-request-storage'
const STORAGE_LOCK_PREFIX = 'threatlens.reporting-storage-lock.'
const STORAGE_LOCK_LEASE_MS = 5_000
const STORAGE_LOCK_TIMEOUT_MS = 10_000
const STORAGE_LOCK_POLL_MS = 10

type StorageLockRecord = {
  owner: string
  choosing: boolean
  ticket: number
  expiresAt: number
}


export async function withReportingStorageLock<Result>(
  callback: () => Result | Promise<Result>,
): Promise<Result> {
  if (typeof window === 'undefined') return callback()
  const lockManager = window.navigator.locks
  if (lockManager?.request) {
    return lockManager.request(STORAGE_LOCK_NAME, { mode: 'exclusive' }, callback)
  }
  if (!localStorageIsWritable()) return callback()
  return withLocalStorageBakeryLock(callback)
}


async function withLocalStorageBakeryLock<Result>(
  callback: () => Result | Promise<Result>,
): Promise<Result> {
  const owner = createRandomIdentifier()
  const participantKey = `${STORAGE_LOCK_PREFIX}${owner}`
  const startedAt = monotonicNow()
  let record: StorageLockRecord = {
    owner,
    choosing: true,
    ticket: 0,
    expiresAt: Date.now() + STORAGE_LOCK_LEASE_MS,
  }
  if (!writeStorageLockRecord(participantKey, record)) {
    throw storageUnavailableError('preparing')
  }

  try {
    record = {
      owner,
      choosing: false,
      ticket: nextStorageLockTicket(),
      expiresAt: Date.now() + STORAGE_LOCK_LEASE_MS,
    }
    if (!writeStorageLockRecord(participantKey, record)) {
      throw storageUnavailableError('preparing')
    }
    while (hasEarlierStorageLockParticipant(record)) {
      if (monotonicNow() - startedAt >= STORAGE_LOCK_TIMEOUT_MS) {
        throw new Error(
          'Another browser tab is still preparing a reporting request. Wait a moment and retry.',
        )
      }
      await delay(STORAGE_LOCK_POLL_MS)
      if (record.expiresAt - Date.now() < STORAGE_LOCK_LEASE_MS / 2) {
        record = { ...record, expiresAt: Date.now() + STORAGE_LOCK_LEASE_MS }
        if (!writeStorageLockRecord(participantKey, record)) {
          throw storageUnavailableError('waiting to prepare')
        }
      }
    }
    if (!ownsStorageLock(participantKey, record)) {
      throw storageUnavailableError('acquiring coordination for')
    }
    return await callback()
  } finally {
    removeStorageLockRecord(participantKey, owner)
  }
}


function nextStorageLockTicket(): number {
  const tickets = storageLockRecords().map((record) => record.ticket)
  const maximum = Math.max(0, ...tickets)
  if (maximum >= Number.MAX_SAFE_INTEGER) {
    throw new Error('Browser request coordination is exhausted. Clear site data and retry.')
  }
  return maximum + 1
}


function hasEarlierStorageLockParticipant(current: StorageLockRecord): boolean {
  return storageLockRecords().some((record) => {
    if (record.owner === current.owner) return false
    if (record.choosing) return true
    if (record.ticket === 0) return false
    return record.ticket < current.ticket
      || (record.ticket === current.ticket && record.owner < current.owner)
  })
}


function storageLockRecords(): StorageLockRecord[] {
  const records: StorageLockRecord[] = []
  try {
    const storage = window.localStorage
    const now = Date.now()
    for (let index = storage.length - 1; index >= 0; index -= 1) {
      const key = storage.key(index)
      if (!key?.startsWith(STORAGE_LOCK_PREFIX)) continue
      const record = parseStorageLockRecord(storage.getItem(key))
      if (!record || record.expiresAt <= now) {
        removeLocalStorageValue(key)
        continue
      }
      records.push(record)
    }
  } catch {
    // The ownership check fails closed if local storage becomes unavailable.
  }
  return records
}


function ownsStorageLock(key: string, expected: StorageLockRecord): boolean {
  const current = parseStorageLockRecord(getLocalStorageValue(key))
  return current?.owner === expected.owner
    && current.ticket === expected.ticket
    && !current.choosing
    && current.expiresAt > Date.now()
}


function parseStorageLockRecord(value: string | null): StorageLockRecord | undefined {
  if (!value) return undefined
  try {
    const record = JSON.parse(value) as Partial<StorageLockRecord>
    if (
      typeof record.owner !== 'string'
      || record.owner.length < 1
      || record.owner.length > 255
      || typeof record.choosing !== 'boolean'
      || !Number.isSafeInteger(record.ticket)
      || (record.ticket ?? -1) < 0
      || typeof record.expiresAt !== 'number'
      || !Number.isFinite(record.expiresAt)
    ) return undefined
    return record as StorageLockRecord
  } catch {
    return undefined
  }
}


function writeStorageLockRecord(key: string, record: StorageLockRecord): boolean {
  const serialized = JSON.stringify(record)
  try {
    window.localStorage.setItem(key, serialized)
    return window.localStorage.getItem(key) === serialized
  } catch {
    return false
  }
}


function removeStorageLockRecord(key: string, owner: string): void {
  const record = parseStorageLockRecord(getLocalStorageValue(key))
  if (record?.owner === owner) removeLocalStorageValue(key)
}


function localStorageIsWritable(): boolean {
  const key = `${STORAGE_LOCK_PREFIX}probe-${createRandomIdentifier()}`
  try {
    window.localStorage.setItem(key, key)
    return window.localStorage.getItem(key) === key
  } catch {
    return false
  } finally {
    removeLocalStorageValue(key)
  }
}


function getLocalStorageValue(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}


function removeLocalStorageValue(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Expired and released records are also ignored while storage is unavailable.
  }
}


function createRandomIdentifier(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    globalThis.crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}


function storageUnavailableError(action: string): Error {
  return new Error(
    `Shared browser storage became unavailable while ${action} the report request.`,
  )
}


function monotonicNow(): number {
  return globalThis.performance?.now() ?? Date.now()
}


function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}
