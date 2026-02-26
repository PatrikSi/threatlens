import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { AuthProvider } from './components/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RoleRoute } from './components/RoleRoute'
import { ThemeProvider } from './components/ThemeContext'
import { AccountPage } from './pages/AccountPage'
import { AuditLogsPage } from './pages/AuditLogsPage'
import { DashboardPage } from './pages/DashboardPage'
import { FeedsPage } from './pages/FeedsPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsLayout } from './pages/SettingsLayout'
import { SettingsOverviewPage } from './pages/SettingsOverviewPage'
import { StatsPage } from './pages/StatsPage'
import { TokensPage } from './pages/TokensPage'
import { UsersPage } from './pages/UsersPage'

const queryClient = new QueryClient()

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
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
              <Route index element={<DashboardPage />} />
              <Route path="feeds" element={<FeedsPage />} />
              <Route path="stats" element={<StatsPage />} />
              <Route path="settings" element={<SettingsLayout />}>
                <Route index element={<SettingsOverviewPage />} />
                <Route path="account" element={<AccountPage />} />
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
      </AuthProvider>
    </ThemeProvider>
  )
}
