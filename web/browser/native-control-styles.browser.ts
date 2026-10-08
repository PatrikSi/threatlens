import { test, expect } from './fixtures'

test('native form backgrounds remain visible while explicit transparent inputs retain their appearance', async ({ page }) => {
  await page.goto('/feeds')
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()

  const opacity = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const context = canvas.getContext('2d')!
    const controls = ['input', 'select', 'textarea', 'input'] as const
    return controls.map((tag, index) => {
      const control = document.createElement(tag)
      control.className = `rounded border p-2 ${index === 3 ? 'bg-transparent' : ''}`
      document.body.append(control)
      context.clearRect(0, 0, 1, 1)
      context.fillStyle = getComputedStyle(control).backgroundColor
      context.fillRect(0, 0, 1, 1)
      const alpha = context.getImageData(0, 0, 1, 1).data[3]
      control.remove()
      return alpha
    })
  })

  expect(opacity).toEqual([255, 255, 255, 0])
})
