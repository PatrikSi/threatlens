import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#0E1726',
        cyan: '#00A3B4',
        sand: '#F5EFE2',
        ember: '#D66B29',
        slate: '#31445D',
      },
      fontFamily: {
        display: ['Space Grotesk', 'sans-serif'],
        body: ['Source Sans 3', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config
