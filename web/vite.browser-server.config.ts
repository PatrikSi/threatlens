import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiOrigin = process.env.THREATLENS_BROWSER_API_ORIGIN
const webOrigin = process.env.THREATLENS_BROWSER_BASE_URL
if (!apiOrigin || !webOrigin || !process.env.THREATLENS_BROWSER_ENV_DIR) {
  throw new Error('Use web/browser/server/run.py to start isolated browser services')
}

export default defineConfig({
  plugins: [react()],
  envDir: process.env.THREATLENS_BROWSER_ENV_DIR,
  cacheDir: `${process.env.THREATLENS_BROWSER_ENV_DIR}/vite-cache`,
  server: {
    host: '127.0.0.1', port: Number(new URL(webOrigin).port), strictPort: true,
    proxy: { '/api': { target: apiOrigin, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, '') } },
  },
})
