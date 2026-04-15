import { FormEvent, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { RegistrationSettingsResponse, TokenResponse } from '../types/api'

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
      navigate('/', { replace: true })
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
        className="w-full max-w-sm rounded-2xl border border-slate/20 bg-white/80 p-6 shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
        onSubmit={onSubmit}
      >
        <h2 className="font-display text-3xl">{mode === 'login' ? 'Analyst Login' : 'Create Account'}</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          {mode === 'login'
            ? 'Sign in to manage feeds and triage articles.'
            : 'Self-registered accounts require admin approval before login.'}
        </p>
        {mode === 'login' && authMessage && (
          <p className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {authMessage}
          </p>
        )}

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

        <label className="mt-5 block text-sm font-semibold">Email</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          type="email"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          required
        />

        <label className="mt-4 block text-sm font-semibold">Password</label>
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          required
        />

        {mode === 'register' && (
          <>
            <label className="mt-4 block text-sm font-semibold">Confirm Password</label>
            <input
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              type="password"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              required
            />
          </>
        )}

        {registerSuccessMessage && <p className="mt-3 text-sm text-green-700 dark:text-green-400">{registerSuccessMessage}</p>}
        {registerFormError && <p className="mt-3 text-sm text-red-600">{registerFormError}</p>}
        {mode === 'login' && login.isError && <p className="mt-3 text-sm text-red-600">{resolveLoginError(login.error)}</p>}
        {mode === 'register' && register.isError && <p className="mt-3 text-sm text-red-600">{resolveRegisterError(register.error)}</p>}

        <button
          type="submit"
          className="mt-5 w-full rounded bg-ink px-3 py-2 font-semibold text-white hover:bg-slate dark:bg-cyan dark:text-[#053c2e]"
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

function resolveLoginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return 'Invalid email or password.'
  }
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  return 'Login failed. Check credentials and try again.'
}

function resolveRegisterError(error: unknown): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  return 'Registration failed. Try again later.'
}
