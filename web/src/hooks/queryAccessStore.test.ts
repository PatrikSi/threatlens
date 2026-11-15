import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createQueryAccessStore } from './queryAccessStore'

class CollectedReference<T extends object> {
  static instances: CollectedReference<object>[] = []
  value: T | undefined
  constructor(value: T) { this.value = value; CollectedReference.instances.push(this) }
  deref() { return this.value }
  collect() { this.value = undefined }
}

let finalizations: Array<() => void>
beforeEach(() => {
  CollectedReference.instances = []
  finalizations = []
  vi.stubGlobal('WeakRef', CollectedReference)
  vi.stubGlobal('FinalizationRegistry', class {
    constructor(private cleanup: (held: unknown) => void) {}
    register(_target: object, held: unknown) { finalizations.push(() => this.cleanup(held)) }
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('query read authority when idle scopes are collected', () => {
  it('preserves live denial, sibling notifications and fresh-read recovery', () => {
    const store = createQueryAccessStore()
    const scope = store.get('shared-family')
    expect(store.get('shared-family')).toBe(scope)
    const notify = vi.fn()
    const unsubscribe = scope.subscribe(notify)
    scope.deny(0)
    const retired = scope.snapshot().generation
    expect(scope.snapshot().denied).toBe(true)
    scope.authorize(0)
    expect(store.denied('shared-family')).toBe(true)
    scope.authorize(retired)
    expect(store.denied('shared-family')).toBe(false)
    expect(notify).toHaveBeenCalledTimes(2)
    unsubscribe()
    scope.deny(retired)
    expect(notify).toHaveBeenCalledTimes(2)
  })

  it('reclaims an unowned never-denied resource without creating a withdrawal', () => {
    const store = createQueryAccessStore()
    const original = store.get('never-denied')
    CollectedReference.instances[0].collect()
    finalizations[0]()
    expect(store.denied('never-denied')).toBe(false)
    const replacement = store.get('never-denied')
    expect(replacement).not.toBe(original)
    expect(replacement.snapshot()).toEqual({ generation: 0, denied: false })
  })

  it('keeps a first detail 404 denied to a list-only getter after collection with no successful envelope', () => {
    const store = createQueryAccessStore()
    const detail = store.get('dashboard-detail-404')
    detail.deny(0)
    CollectedReference.instances[0].collect()
    finalizations[0]()
    const referencesBefore = CollectedReference.instances.length
    expect(store.denied('dashboard-detail-404')).toBe(true)
    expect(store.denied('dashboard-detail-404')).toBe(true)
    expect(CollectedReference.instances).toHaveLength(referencesBefore)
    const fresh = store.get('dashboard-detail-404')
    expect(fresh.snapshot().denied).toBe(true)
    expect(fresh.snapshot().generation).not.toBe(detail.snapshot().generation)
    fresh.authorize(fresh.snapshot().generation)
    expect(store.denied('dashboard-detail-404')).toBe(false)
  })

  it('rejects manually cloned old envelopes after a previously recovered scope is collected', () => {
    const store = createQueryAccessStore()
    const original = store.get('editor')
    original.deny(0)
    const oldEpoch = original.snapshot().generation
    original.authorize(oldEpoch)
    const clonedCache = { generation: oldEpoch, value: { privateName: 'Prior saved value' } }
    CollectedReference.instances[0].collect()
    finalizations[0]()
    expect(store.denied('editor')).toBe(true)
    const replacement = store.get('editor')
    const freshEpoch = replacement.snapshot().generation
    expect(freshEpoch).not.toBe(clonedCache.generation)
    replacement.authorize(clonedCache.generation)
    expect(replacement.snapshot().denied).toBe(true)
    replacement.authorize(freshEpoch)
    expect(store.denied('editor')).toBe(false)
    expect(clonedCache.generation).not.toBe(replacement.snapshot().generation)
  })

  it('cannot remove a replacement when an old finalizer arrives late', () => {
    const store = createQueryAccessStore()
    store.get('replaced')
    CollectedReference.instances[0].collect()
    const replacement = store.get('replaced')
    finalizations[0]()
    expect(store.get('replaced')).toBe(replacement)
  })

  it('allocates distinct withdrawal epochs across resources in one session', () => {
    const store = createQueryAccessStore()
    const first = store.get('first')
    const second = store.get('second')
    first.deny(0)
    second.deny(0)
    expect(first.snapshot().generation).not.toBe(second.snapshot().generation)
    expect(createQueryAccessStore().denied('first')).toBe(false)
  })

  it('fails closed on filter collisions and lets an actual fresh read recover without clearing other history', () => {
    const TypedArray = Uint32Array
    // Simulate saturation deterministically; this is not a capacity measurement.
    vi.stubGlobal('Uint32Array', class extends TypedArray {
      constructor(length: number) { super(length); this.fill(0xffffffff) }
    })
    const store = createQueryAccessStore()
    store.get('withdrawn').deny(0)
    expect(store.denied('otherwise-new')).toBe(true)
    const unrelated = store.get('otherwise-new')
    expect(unrelated.snapshot().denied).toBe(true)
    unrelated.authorize(unrelated.snapshot().generation)
    expect(store.denied('otherwise-new')).toBe(false)
    expect(store.denied('withdrawn')).toBe(true)
    expect(store.denied('another-new')).toBe(true)
  })
})
