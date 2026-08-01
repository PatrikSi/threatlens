import { FormEvent, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError, apiFetch, buildApiUrl } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { OIDCPublicSettings, RegistrationSettingsResponse, TokenResponse } from '../types/api'

type AuthMode = 'login' | 'register'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { markAuthenticated } = useAuth()
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [registerSuccessMessage, setRegisterSuccessMessage] = useState<string | null>(null)
  const [registerFormError, setRegisterFormError] = useState<string | null>(null)

  const registrationSettingsQuery = useQuery({
    queryKey: ['auth', 'registration-settings'],
    queryFn: () => apiFetch<RegistrationSettingsResponse>('/auth/registration-settings', {}, false),
    staleTime: 60_000,
  })
  const oidcSettingsQuery = useQuery({
    queryKey: ['auth', 'oidc', 'settings'],
    queryFn: () => apiFetch<OIDCPublicSettings>('/auth/oidc/settings', {}, false),
    staleTime: 60_000,
  })

  const login = useMutation({
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<TokenResponse>(
        '/auth/login',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        false,
      ),
    onSuccess: (data) => {
      void data
      markAuthenticated()
      navigate(resolvePostLoginDestination(location.state), { replace: true })
    },
  })

  const register = useMutation({
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<{ id: string }>(
        '/auth/register',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        false,
      ),
    onSuccess: () => {
      setRegisterSuccessMessage('Account created. Wait for admin approval before signing in.')
      setConfirmPassword('')
      setPassword('')
      setMode('login')
    },
  })

  const selfRegistrationEnabled = registrationSettingsQuery.data?.allow_self_registration ?? false
  const authMessage =
    typeof location.state === 'object' && location.state && 'authMessage' in location.state
      ? String(location.state.authMessage || '')
      : ''
  const oidcError = new URLSearchParams(location.search).get('oidc_error')

  const switchMode = (nextMode: AuthMode) => {
    setMode(nextMode)
    setRegisterFormError(null)
    setRegisterSuccessMessage(null)
    login.reset()
    register.reset()
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setRegisterFormError(null)
    setRegisterSuccessMessage(null)

    if (mode === 'register') {
      if (password !== confirmPassword) {
        setRegisterFormError('Passwords do not match.')
        return
      }
      register.mutate({ email, password })
      return
    }

    login.mutate({ email, password })
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        className="tl-surface w-full max-w-sm rounded-2xl p-6 shadow-sm"
        onSubmit={onSubmit}
      >
        <h2 className="font-display text-3xl">{mode === 'login' ? 'Analyst Login' : 'Create Account'}</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          {mode === 'login'
            ? 'Sign in to manage feeds and triage articles.'
            : 'Self-registered accounts require admin approval before login.'}
        </p>
        <LoginNotices
          mode={mode}
          authMessage={authMessage}
          oidcError={oidcError}
          registrationSettingsError={registrationSettingsQuery.isError}
        />

        {selfRegistrationEnabled && (
          <div className="mt-4 grid grid-cols-2 gap-2 rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
            <button
              type="button"
              className={`rounded px-3 py-1 text-sm ${
                mode === 'login' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-slate-300'
              }`}
              onClick={() => switchMode('login')}
            >
              Sign in
            </button>
            <button
              type="button"
              className={`rounded px-3 py-1 text-sm ${
                mode === 'register' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-slate-300'
              }`}
              onClick={() => switchMode('register')}
            >
              Register
            </button>
          </div>
        )}

        <OIDCLoginOption mode={mode} settings={oidcSettingsQuery.data} />

        <label htmlFor="login-email" className={`${mode === 'login' && oidcSettingsQuery.data?.enabled ? '' : 'mt-5'} block text-sm font-semibold`}>
          Email
        </label>
        <input
          id="login-email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          type="email"
          autoComplete="email"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          required
        />

        <label htmlFor="login-password" className="mt-4 block text-sm font-semibold">
          Password
        </label>
        <input
          id="login-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          required
        />

        {mode === 'register' && (
          <>
            <label htmlFor="login-confirm-password" className="mt-4 block text-sm font-semibold">
              Confirm Password
            </label>
            <input
              id="login-confirm-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              type="password"
              autoComplete="new-password"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              required
            />
          </>
        )}

        {registerSuccessMessage && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-3 text-sm text-green-700 dark:text-green-400">
            {registerSuccessMessage}
          </p>
        )}
        {registerFormError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-3 text-sm text-red-600 dark:text-red-300">
            {registerFormError}
          </p>
        )}
        {mode === 'login' && login.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-3 text-sm text-red-600 dark:text-red-300">
            {resolveLoginError(login.error)}
          </p>
        )}
        {mode === 'register' && register.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-3 text-sm text-red-600 dark:text-red-300">
            {resolveRegisterError(register.error)}
          </p>
        )}

        <button
          type="submit"
          className="mt-5 w-full rounded bg-ink px-3 py-2 font-semibold text-white hover:bg-slate dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90"
          disabled={login.isPending || register.isPending}
        >
          {mode === 'login' ? (login.isPending ? 'Signing in...' : 'Sign in') : register.isPending ? 'Submitting...' : 'Register'}
        </button>

        {!selfRegistrationEnabled && registrationSettingsQuery.isSuccess && (
          <p className="mt-3 text-xs text-slate dark:text-slate-300">Self-registration is disabled by configuration.</p>
        )}
      </form>
    </div>
  )
}

function LoginNotices({
  mode,
  authMessage,
  oidcError,
  registrationSettingsError,
}: {
  mode: AuthMode
  authMessage: string
  oidcError: string | null
  registrationSettingsError: boolean
}) {
  return (
    <>
      {mode === 'login' && authMessage && (
        <p
          role="alert"
          aria-live="polite"
          aria-atomic="true"
          className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          {authMessage}
        </p>
      )}
      {mode === 'login' && oidcError && (
        <p
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
          className="mt-3 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
        >
          {resolveOidcError(oidcError)}
        </p>
      )}
      {registrationSettingsError && (
        <p
          role="alert"
          aria-live="polite"
          aria-atomic="true"
          className="mt-3 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
        >
          Registration availability could not be loaded. Sign-in is still available.
        </p>
      )}
    </>
  )
}

function OIDCLoginOption({ mode, settings }: { mode: AuthMode; settings: OIDCPublicSettings | undefined }) {
  if (mode !== 'login' || !settings?.enabled) {
    return null
  }
  return (
    <>
      <button
        type="button"
        className="mt-5 w-full rounded border border-cyan/40 bg-cyan/10 px-3 py-2 font-semibold text-cyan-900 hover:bg-cyan/15 dark:border-cyan/40 dark:text-cyan-100"
        onClick={() => window.location.assign(buildApiUrl('/auth/oidc/login'))}
      >
        Continue with {settings.provider_name || 'SSO'}
      </button>
      <div className="my-4 flex items-center gap-3 text-xs text-slate dark:text-slate-400" aria-hidden="true">
        <span className="h-px flex-1 bg-slate/20 dark:bg-white/10" />
        <span>or use a local account</span>
        <span className="h-px flex-1 bg-slate/20 dark:bg-white/10" />
      </div>
    </>
  )
}

function resolveLoginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    return 'Too many sign-in attempts. Wait a moment and try again.'
  }
  if (error instanceof ApiError) {
    return 'Sign in failed. Check your credentials or account status and try again. If this account was recently created, it may still need admin approval.'
  }
  return 'Sign in failed. Try again.'
}

function resolveRegisterError(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    return 'Too many registration attempts. Wait a moment and try again.'
  }
  if (error instanceof ApiError) {
    return 'Registration failed. Check the submitted details and try again later.'
  }
  return 'Registration failed. Try again later.'
}

function resolveOidcError(errorCode: string): string {
  const messages: Record<string, string> = {
    approval_required: 'Your SSO account is waiting for administrator approval.',
    account_inactive: 'This ThreatLens account is inactive. Contact an administrator.',
    email_link_required: 'An account already uses this email. Sign in locally, then link SSO from Account settings.',
    not_provisioned: 'No ThreatLens account is linked to this identity. Contact an administrator.',
    verified_email_required: 'The identity provider did not supply a verified email address.',
    provider_rejected: 'The identity provider did not complete sign-in.',
    invalid_state: 'The SSO request expired or could not be verified. Start sign-in again.',
  }
  return messages[errorCode] ?? 'SSO sign-in could not be completed. Try again or contact an administrator.'
}

function resolvePostLoginDestination(state: unknown): string {
  if (!state || typeof state !== 'object' || !('from' in state)) {
    return '/'
  }

  const from = state.from
  if (!from || typeof from !== 'object') {
    return '/'
  }

  const pathname = 'pathname' in from && typeof from.pathname === 'string' ? from.pathname : '/'
  const search = 'search' in from && typeof from.search === 'string' ? from.search : ''
  const hash = 'hash' in from && typeof from.hash === 'string' ? from.hash : ''
  return `${pathname}${search}${hash}` || '/'
}
