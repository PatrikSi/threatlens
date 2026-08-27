import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  Route,
  RouterProvider,
  createBrowserRouter,
  createRoutesFromElements,
  isRouteErrorResponse,
  useRouteError,
} from 'react-router-dom'
import { Component, Suspense, lazy, useEffect, useMemo, useState } from 'react'

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
const ExportPage = lazy(() => import('./pages/ExportPage').then((module) => ({ default: module.ExportPage })))
const FeedsPage = lazy(() => import('./pages/FeedsPage').then((module) => ({ default: module.FeedsPage })))
const IntegrationsSettingsPage = lazy(() =>
  import('./pages/IntegrationsSettingsPage').then((module) => ({ default: module.SMTPIntegrationSettingsPage })),
)
const ReportingPage = lazy(() => import('./pages/ReportingPage').then((module) => ({ default: module.ReportingPage })))
const IdentitySettingsPage = lazy(() =>
  import('./pages/IdentitySettingsPage').then((module) => ({ default: module.IdentitySettingsPage })),
)
const NotificationWebhooksSettingsPage = lazy(() =>
  import('./pages/NotificationsPage').then((module) => ({ default: module.NotificationWebhooksSettings })),
)
const OperationsPage = lazy(() => import('./pages/OperationsPage').then((module) => ({ default: module.OperationsPage })))
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
  const [router] = useState(() => createAppRouter())

  return (
    <AppRenderErrorBoundary>
      <RouterProvider router={router} />
    </AppRenderErrorBoundary>
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

function createAppRouter() {
  return createBrowserRouter(
    createRoutesFromElements(
      <Route element={<AppProviders />} errorElement={<RouteErrorBoundary />}>
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
          <Route path="export" element={suspenseRoute(<ExportPage />, 'Loading export workspace...')} />
          <Route path="reporting" element={suspenseRoute(<ReportingPage />, 'Loading intelligence reporting...')} />
          <Route path="reporting/:reportId" element={suspenseRoute(<ReportingPage />, 'Loading intelligence report...')} />
          <Route path="ai" element={<Navigate to="/settings/ai" replace />} />
          <Route path="settings" element={suspenseRoute(<SettingsLayout />, 'Loading settings...')}>
            <Route index element={<Navigate to="account" replace />} />
            <Route path="account" element={suspenseRoute(<AccountPage />, 'Loading account settings...')} />
            <Route path="notifications" element={<Navigate to="/settings/integrations/webhooks" replace />} />
            <Route path="integrations">
              <Route index element={<Navigate to="webhooks" replace />} />
              <Route path="webhooks" element={suspenseRoute(<NotificationWebhooksSettingsPage />, 'Loading webhook integration settings...')} />
              <Route
                path="smtp"
                element={
                  <RoleRoute roles={['admin']}>
                    {suspenseRoute(<IntegrationsSettingsPage />, 'Loading SMTP integration settings...')}
                  </RoleRoute>
                }
              />
            </Route>
            <Route
              path="identity"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<IdentitySettingsPage />, 'Loading identity provider settings...')}
                </RoleRoute>
              }
            />
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
              path="operations"
              element={
                <RoleRoute roles={['admin']}>
                  {suspenseRoute(<OperationsPage />, 'Loading operations status...')}
                </RoleRoute>
              }
            />
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
      </Route>,
    ),
  )
}

interface AppRenderErrorBoundaryState {
  error: Error | null
}

class AppRenderErrorBoundary extends Component<{ children: React.ReactNode }, AppRenderErrorBoundaryState> {
  state: AppRenderErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <AppErrorState
          title="ThreatLens could not render"
          description="Refresh the page and try again. If the problem continues, contact an administrator."
          errorMessage={this.state.error.message}
        />
      )
    }

    return this.props.children
  }
}

function RouteErrorBoundary() {
  const error = useRouteError()
  const title = isRouteErrorResponse(error) ? `Page failed (${error.status})` : 'Page failed to load'
  const errorMessage = error instanceof Error ? error.message : isRouteErrorResponse(error) ? error.statusText : undefined

  return (
    <AppErrorState
      title={title}
      description="ThreatLens could not render this page. Return to the dashboard or refresh the browser."
      errorMessage={errorMessage}
      homeLink
    />
  )
}

function AppErrorState({
  title,
  description,
  errorMessage,
  homeLink = false,
}: {
  title: string
  description: string
  errorMessage?: string
  homeLink?: boolean
}) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 text-ink dark:text-slate-100">
      <div role="alert" className="w-full max-w-xl rounded-xl border border-red-300/60 bg-white/90 p-6 shadow-sm dark:border-red-500/30 dark:bg-[#041612]/95">
        <h1 className="font-display text-3xl">{title}</h1>
        <p className="mt-2 text-sm text-slate dark:text-slate-300">{description}</p>
        {errorMessage && (
          <p className="mt-3 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-200">
            {errorMessage}
          </p>
        )}
        <button
          type="button"
          className="mt-4 rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate-700 dark:border-white/10 dark:text-slate-100"
          onClick={() => window.location.reload()}
        >
          Reload app
        </button>
        {homeLink && (
          <Link
            to="/"
            className="ml-2 mt-4 inline-flex rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          >
            Go to dashboard
          </Link>
        )}
      </div>
    </div>
  )
}

function AppProviders() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SessionScopedProviders />
      </AuthProvider>
    </ThemeProvider>
  )
}

function SessionScopedProviders() {
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
      <Outlet />
    </QueryClientProvider>
  )
}
