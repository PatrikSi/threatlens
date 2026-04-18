import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Suspense, lazy, useEffect, useMemo } from 'react'

import { AppShell } from './components/AppShell'
import { AuthProvider, useAuth } from './components/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RoleRoute } from './components/RoleRoute'
import { ThemeProvider } from './components/ThemeContext'
import { LoginPage } from './pages/LoginPage'

const AccountPage = lazy(() => import('./pages/AccountPage').then((module) => ({ default: module.AccountPage })))
const AiSettingsPage = lazy(() => import('./pages/AiSettingsPage').then((module) => ({ default: module.AiSettingsPage })))
const AlertsPage = lazy(() => import('./pages/AlertsPage').then((module) => ({ default: module.AlertsPage })))
const AuditLogsPage = lazy(() => import('./pages/AuditLogsPage').then((module) => ({ default: module.AuditLogsPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const FeedsPage = lazy(() => import('./pages/FeedsPage').then((module) => ({ default: module.FeedsPage })))
const NotificationsPage = lazy(() =>
  import('./pages/NotificationsPage').then((module) => ({ default: module.NotificationsPage })),
)
const SettingsLayout = lazy(() =>
  import('./pages/SettingsLayout').then((module) => ({ default: module.SettingsLayout })),
)
const StatsPage = lazy(() => import('./pages/StatsPage').then((module) => ({ default: module.StatsPage })))
const TaggingSettingsPage = lazy(() =>
  import('./pages/TaggingSettingsPage').then((module) => ({ default: module.TaggingSettingsPage })),
)
const TokensPage = lazy(() => import('./pages/TokensPage').then((module) => ({ default: module.TokensPage })))
const UsersPage = lazy(() => import('./pages/UsersPage').then((module) => ({ default: module.UsersPage })))

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  })
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SessionScopedApp />
      </AuthProvider>
    </ThemeProvider>
  )
}

function RouteLoadingFallback({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-slate/20 bg-white/80 p-5 text-sm text-slate shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/90 dark:text-slate-200">
      {label}
    </div>
  )
}

function suspenseRoute(element: React.ReactNode, label: string) {
  return <Suspense fallback={<RouteLoadingFallback label={label} />}>{element}</Suspense>
}

function SessionScopedApp() {
  const { sessionVersion } = useAuth()
  const queryClient = useMemo(() => createQueryClient(), [])

  useEffect(() => {
    if (sessionVersion === 0) {
      return
    }
    queryClient.clear()
  }, [queryClient, sessionVersion])

  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        >
          <Route index element={suspenseRoute(<DashboardPage />, 'Loading dashboard...')} />
          <Route path="alerts" element={suspenseRoute(<AlertsPage />, 'Loading alerts...')} />
          <Route path="feeds" element={suspenseRoute(<FeedsPage />, 'Loading feeds...')} />
          <Route path="stats" element={suspenseRoute(<StatsPage />, 'Loading statistics...')} />
          <Route path="ai" element={<Navigate to="/settings/ai" replace />} />
          <Route path="settings" element={suspenseRoute(<SettingsLayout />, 'Loading settings...')}>
            <Route index element={<Navigate to="account" replace />} />
            <Route path="account" element={suspenseRoute(<AccountPage />, 'Loading account settings...')} />
            <Route path="notifications" element={suspenseRoute(<NotificationsPage />, 'Loading notification settings...')} />
            <Route
              path="ai"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<AiSettingsPage />, 'Loading AI settings...')}
                </RoleRoute>
              }
            />
            <Route
              path="tagging"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<TaggingSettingsPage />, 'Loading tagging settings...')}
                </RoleRoute>
              }
            />
            <Route path="tokens" element={suspenseRoute(<TokensPage />, 'Loading token inventory...')} />
            <Route
              path="users"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<UsersPage />, 'Loading user administration...')}
                </RoleRoute>
              }
            />
            <Route
              path="audit-logs"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<AuditLogsPage />, 'Loading audit logs...')}
                </RoleRoute>
              }
            />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </QueryClientProvider>
  )
}
