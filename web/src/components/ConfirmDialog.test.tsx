import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ConfirmDialog } from './ConfirmDialog'

describe('ConfirmDialog', () => {
  it('renders alertdialog semantics when open', () => {
    const markup = renderToStaticMarkup(
      <ConfirmDialog
        open
        title="Delete alert?"
        description="This removes the alert."
        confirmLabel="Delete alert"
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(markup).toContain('role="alertdialog"')
    expect(markup).toContain('aria-modal="true"')
    expect(markup).toContain('Delete alert?')
    expect(markup).toContain('Delete alert')
  })

  it('marks the dialog busy and disables dismiss controls while confirming', () => {
    const markup = renderToStaticMarkup(
      <ConfirmDialog
        open
        title="Apply changes?"
        description="Review the changes below."
        confirmLabel="Apply"
        confirmTone="primary"
        isConfirming
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    )

    expect(markup).toContain('aria-busy="true"')
    expect(markup).toContain('Working...')
    expect(markup.match(/disabled=""/g)?.length ?? 0).toBeGreaterThanOrEqual(3)
  })
})
