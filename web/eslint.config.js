import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  {
    ignores: ['dist', 'coverage'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}', 'vite.config.ts'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': [
        'warn',
        {
          allowConstantExport: true,
          allowExportNames: ['useAuth', 'useTheme', 'getDashboardStorageKeys', 'migrateLegacyDashboardStorage'],
        },
      ],
      complexity: ['error', 35],
      'max-lines': ['error', { max: 2000, skipBlankLines: true, skipComments: true }],
    },
  },
  {
    files: ['src/pages/AiSettingsActivityTab.tsx'],
    rules: { complexity: ['error', 79] },
  },
  {
    files: ['src/pages/AiSettingsConfigurationTab.tsx'],
    rules: { complexity: ['error', 39] },
  },
  {
    files: ['src/pages/AiSettingsPage.tsx'],
    rules: { complexity: ['error', 96] },
  },
  {
    files: ['src/pages/AlertsPage.tsx'],
    rules: { complexity: ['error', 42] },
  },
  {
    files: ['src/pages/DashboardPageView.tsx'],
    rules: { complexity: ['error', 117] },
  },
  {
    files: ['src/pages/FeedsPage.tsx'],
    rules: { complexity: ['error', 140] },
  },
  {
    files: ['src/pages/IntegrationsSettingsPage.tsx'],
    rules: { complexity: ['error', 77] },
  },
  {
    files: ['src/pages/NotificationsPage.tsx'],
    rules: { complexity: ['error', 106] },
  },
  {
    files: ['src/pages/TaggingSettingsPage.tsx'],
    rules: { complexity: ['error', 56] },
  },
  {
    files: ['src/pages/dashboardSavedViews.ts'],
    rules: { complexity: ['error', 43] },
  },
)
