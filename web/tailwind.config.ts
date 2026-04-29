import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#111827',
        cyan: '#2563eb',
        source: '#0f766e',
        sand: '#f8fafc',
        ember: '#a95318',
        slate: '#475569',
      },
      fontFamily: {
        display: ['Space Grotesk', 'Source Sans 3', 'system-ui', 'sans-serif'],
        body: ['Source Sans 3', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config
