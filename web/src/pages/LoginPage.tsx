import { FormEvent, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { ApiError, apiFetch } from '../api/client'
import { useAuth } from '../components/AuthContext'
import { TokenResponse } from '../types/api'

export function LoginPage() {
  const navigate = useNavigate()
  const { setAuthToken } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

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
    onMutate: () => {
      setAuthToken(null)
    },
    onSuccess: (data) => {
      setAuthToken(data.access_token)
      navigate('/', { replace: true })
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    login.mutate({ email, password })
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        className="w-full max-w-sm rounded-2xl border border-slate/20 bg-white/80 p-6 shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
        onSubmit={onSubmit}
      >
        <h2 className="font-display text-3xl">Analyst Login</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">Sign in to manage feeds and triage articles.</p>

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

        {login.isError && <p className="mt-3 text-sm text-red-600">{resolveLoginError(login.error)}</p>}

        <button
          type="submit"
          className="mt-5 w-full rounded bg-ink px-3 py-2 font-semibold text-white hover:bg-slate dark:bg-cyan dark:text-[#053c2e]"
          disabled={login.isPending}
        >
          {login.isPending ? 'Signing in...' : 'Sign in'}
        </button>
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
