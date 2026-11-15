export type AccessSnapshot = { generation: number; denied: boolean }

function createScope(initial: AccessSnapshot, withdraw: () => number) {
  let snapshot = initial
  const listeners = new Set<() => void>()
  function update(next: AccessSnapshot) {
    snapshot = next
    for (const notify of listeners) notify()
  }
  return {
    snapshot: () => snapshot,
    subscribe: (notify: () => void) => {
      listeners.add(notify)
      return () => { listeners.delete(notify) }
    },
    deny: (generation: number) => {
      if (generation === snapshot.generation) update({ generation: withdraw(), denied: true })
    },
    authorize: (generation: number) => {
      if (generation === snapshot.generation && snapshot.denied) update({ generation, denied: false })
    },
  }
}

// A conservative summary preserves withdrawals after an idle exact scope is
// collected, including detail withdrawals consulted by another list scope.
// Bits are never cleared. Collisions require a fresh read; they cannot admit one.
function createDenialHistory() {
  let bits: Uint32Array | undefined
  function positions(key: string) {
    let first = 2166136261
    let second = 0x9e3779b9
    for (let index = 0; index < key.length; index++) {
      const unit = key.charCodeAt(index)
      first = Math.imul(first ^ unit, 16777619)
      second = Math.imul(second ^ unit, 0x85ebca6b)
    }
    return Array.from({ length: 4 }, (_, index) =>
      ((first + Math.imul(index, second | 1)) >>> 0) & ((64 * 1024 * 8) - 1))
  }
  return {
    add(key: string) {
      bits ??= new Uint32Array(64 * 1024 / 4)
      for (const position of positions(key)) bits[position >>> 5] |= 1 << (position & 31)
    },
    has(key: string) {
      return bits !== undefined && positions(key).every((position) =>
        (bits![position >>> 5] & (1 << (position & 31))) !== 0)
    },
  }
}

export function createQueryAccessStore() {
  type Scope = ReturnType<typeof createScope>
  const resources = new Map<string, WeakRef<Scope>>()
  const history = createDenialHistory()
  let sequence = 0
  // NaN is permanently unreadable (including to itself) if epochs ever exhaust.
  const nextGeneration = () => sequence < Number.MAX_SAFE_INTEGER ? ++sequence : Number.NaN
  const finalizer = new FinalizationRegistry<{ key: string; reference: WeakRef<Scope> }>(({ key, reference }) => {
    if (resources.get(key) === reference && !reference.deref()) resources.delete(key)
  })
  let cursor = resources.entries()
  function sweep() {
    // Bound work per lookup even if the browser postpones finalizer callbacks.
    for (let count = 0; count < 4; count++) {
      const entry = cursor.next()
      if (entry.done) { cursor = resources.entries(); break }
      const [key, reference] = entry.value
      if (!reference.deref() && resources.get(key) === reference) resources.delete(key)
    }
  }
  return {
    get(key: string) {
      sweep()
      let scope = resources.get(key)?.deref()
      if (!scope) {
        const retired = history.has(key)
        scope = createScope({ generation: retired ? nextGeneration() : 0, denied: retired }, () => {
          history.add(key)
          return nextGeneration()
        })
        const reference = new WeakRef(scope)
        resources.set(key, reference)
        finalizer.register(scope, { key, reference })
      }
      return scope
    },
    denied: (key: string) => resources.get(key)?.deref()?.snapshot().denied ?? history.has(key),
  }
}
