import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#172033',
        cyan: '#0f766e',
        sand: '#f6f8f4',
        ember: '#a95318',
        slate: '#5f6f82',
      },
      fontFamily: {
        display: ['Space Grotesk', 'Source Sans 3', 'system-ui', 'sans-serif'],
        body: ['Source Sans 3', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config
