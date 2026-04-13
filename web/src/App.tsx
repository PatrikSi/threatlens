import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useMemo } from 'react'

import { AppShell } from './components/AppShell'
import { AuthProvider, useAuth } from './components/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RoleRoute } from './components/RoleRoute'
import { ThemeProvider } from './components/ThemeContext'
import { AccountPage } from './pages/AccountPage'
import { AiSettingsPage } from './pages/AiSettingsPage'
import { AlertsPage } from './pages/AlertsPage'
import { AuditLogsPage } from './pages/AuditLogsPage'
import { DashboardPage } from './pages/DashboardPage'
import { FeedsPage } from './pages/FeedsPage'
import { LoginPage } from './pages/LoginPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { SettingsLayout } from './pages/SettingsLayout'
import { SettingsOverviewPage } from './pages/SettingsOverviewPage'
import { StatsPage } from './pages/StatsPage'
import { TaggingSettingsPage } from './pages/TaggingSettingsPage'
import { TokensPage } from './pages/TokensPage'
import { UsersPage } from './pages/UsersPage'

function createQueryClient(sessionVersion: number) {
  void sessionVersion
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

function SessionScopedApp() {
  const { sessionVersion } = useAuth()
  const queryClient = useMemo(() => createQueryClient(sessionVersion), [sessionVersion])

  return (
    <QueryClientProvider client={queryClient} key={sessionVersion}>
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
          <Route index element={<DashboardPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="feeds" element={<FeedsPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="ai" element={<Navigate to="/settings/ai" replace />} />
          <Route path="settings" element={<SettingsLayout />}>
            <Route index element={<SettingsOverviewPage />} />
            <Route path="account" element={<AccountPage />} />
            <Route path="notifications" element={<NotificationsPage />} />
            <Route
              path="ai"
              element={
                <RoleRoute roles={['admin']}>
                  <AiSettingsPage />
                </RoleRoute>
              }
            />
            <Route
              path="tagging"
              element={
                <RoleRoute roles={['admin']}>
                  <TaggingSettingsPage />
                </RoleRoute>
              }
            />
            <Route path="tokens" element={<TokensPage />} />
            <Route
              path="users"
              element={
                <RoleRoute roles={['admin']}>
                  <UsersPage />
                </RoleRoute>
              }
            />
            <Route
              path="audit-logs"
              element={
                <RoleRoute roles={['admin']}>
                  <AuditLogsPage />
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
