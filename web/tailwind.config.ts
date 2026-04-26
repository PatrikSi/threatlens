import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#071B16',
        cyan: '#2CD4A7',
        sand: '#F5EFE2',
        ember: '#D66B29',
        slate: '#375A53',
      },
      fontFamily: {
        display: ['Space Grotesk', 'Source Sans 3', 'system-ui', 'sans-serif'],
        body: ['Source Sans 3', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config
