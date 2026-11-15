import { test, expect } from './fixtures'

// Guard browser-toolchain updates against the Firefox cross-world crash:
// https://github.com/microsoft/playwright/issues/43967
for (const order of ['main-world-first', 'role-selector-first'] as const) {
  test(`resolves ARIA label references across execution worlds (${order})`, async ({ page }) => {
    await page.setContent('<!doctype html><html lang="en"><title>ARIA references</title><main><span id="label">Save</span><button aria-labelledby="label">Save</button></main></html>')
    const readReferences = async () => {
      const references = await page.evaluate(() =>
        document.querySelector('button')?.ariaLabelledByElements?.map((element) => element.id),
      )
      expect(references).toEqual(['label'])
    }
    const resolveRole = () => page.getByRole('button', { name: 'Save', exact: true }).click({ trial: true })
    if (order === 'main-world-first') {
      await readReferences()
      await resolveRole()
    } else {
      await resolveRole()
      await readReferences()
    }
  })
}
