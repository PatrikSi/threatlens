import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.indexOf('node_modules') === -1) {
            return undefined
          }
          if (id.indexOf('@tanstack/react-query') !== -1) {
            return 'react-query'
          }
          if (id.indexOf('react-router') !== -1) {
            return 'react-router'
          }
          if (id.indexOf('/react/') !== -1 || id.indexOf('/react-dom/') !== -1) {
            return 'react-vendor'
          }
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
  },
})
