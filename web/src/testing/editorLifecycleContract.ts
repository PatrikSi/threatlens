import { describe, expect, it } from 'vitest'

/** A test adapter drives an actual editor controller, router, and session cache.
 * Keep domain validation, normalization and resource-version assertions in the
 * editor's own suite; this contract covers the lifecycle shared by editors.
 */
export interface EditorLifecycleDriver {
  value: () => string
  edit: (value: string) => void
  submit: () => Promise<void>
  complete: () => Promise<void>
  conflict: () => Promise<void>
  refresh: () => Promise<void>
  reselect: () => Promise<void>
  leave: () => Promise<void>
  cancelLeave: () => void
  discardAndLeave: () => Promise<void>
  onEditorRoute: () => boolean
  discardVisible: () => boolean
  retireSession: () => Promise<void>
  hasAcceptedCache: () => boolean
}

export function defineEditorLifecycleContract(name: string, mount: () => Promise<EditorLifecycleDriver>) {
  describe(`${name}: shared editor lifecycle contract`, () => {
    it('preserves edits made during a delayed save and keeps navigation protected', async () => {
      const editor = await mount()
      editor.edit('Submitted')
      await editor.submit()
      editor.edit('Later draft')
      await editor.complete()
      expect(editor.value()).toBe('Later draft')
      await editor.leave()
      expect(editor.discardVisible()).toBe(true)
      editor.cancelLeave()
      expect(editor.onEditorRoute()).toBe(true)
      expect(editor.value()).toBe('Later draft')
    })

    it('does not replace a subsequently reselected draft with a late save', async () => {
      const editor = await mount()
      editor.edit('Submitted')
      await editor.submit()
      await editor.reselect()
      editor.edit('Reselected draft')
      await editor.complete()
      expect(editor.value()).toBe('Reselected draft')
    })

    it('preserves a dirty draft when refreshed inventory changes', async () => {
      const editor = await mount()
      editor.edit('Local changes')
      await editor.refresh()
      expect(editor.value()).toBe('Local changes')
      await editor.leave()
      expect(editor.discardVisible()).toBe(true)
      editor.cancelLeave()
    })

    it('preserves edits after a rejected save and subsequent inventory refresh', async () => {
      const editor = await mount()
      editor.edit('Submitted')
      await editor.submit()
      editor.edit('Unsaved after request')
      await editor.conflict()
      await editor.refresh()
      expect(editor.value()).toBe('Unsaved after request')
      await editor.leave()
      expect(editor.discardVisible()).toBe(true)
      editor.cancelLeave()
    })

    it('renders cancel and discard navigation through the real router', async () => {
      const editor = await mount()
      editor.edit('Local changes')
      await editor.leave()
      expect(editor.discardVisible()).toBe(true)
      expect(editor.onEditorRoute()).toBe(true)
      editor.cancelLeave()
      expect(editor.value()).toBe('Local changes')
      await editor.discardAndLeave()
      expect(editor.onEditorRoute()).toBe(false)
      expect(editor.discardVisible()).toBe(false)
    })

    it('retires drafts and rejects late cache writes across a session change', async () => {
      const editor = await mount()
      editor.edit('Submitted')
      await editor.submit()
      editor.edit('Private draft')
      await editor.retireSession()
      await editor.complete()
      expect(editor.value()).not.toBe('Private draft')
      expect(editor.value()).not.toBe('Submitted')
      expect(editor.hasAcceptedCache()).toBe(false)
    })
  })
}
