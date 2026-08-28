import { FormEvent, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError, apiFetch, buildApiUrl } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useAuth } from '../components/AuthContext'
import {
  OIDCPublicSettings,
  RegistrationSettingsResponse,
  TokenResponse,
} from '../types/api'
import { isExpiredMfaChallenge, resolveMfaLoginError } from './loginMfaModel'
import { resolveOIDCLoginError } from './oidcCallbackMessages'

type AuthMode = 'login' | 'register'
type LoginStep = 'credentials' | 'mfa'

const LOGIN_MUTATION_KEY = ['auth', 'login'] as const
const REGISTER_MUTATION_KEY = ['auth', 'register'] as const
const MFA_VERIFY_MUTATION_KEY = ['auth', 'mfa', 'verify'] as const

function forgetCredentialMutation(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
  reset: () => void,
) {
  reset()
  const mutationCache = queryClient.getMutationCache()
  mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
    if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
  })
}

function forgetCredentialMutationAfterSettlement(
  queryClient: QueryClient,
  mutationKey: readonly unknown[],
) {
  window.setTimeout(() => {
    const mutationCache = queryClient.getMutationCache()
    mutationCache.findAll({ mutationKey, exact: true }).forEach((mutation) => {
      if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
    })
  }, 0)
}

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { markAuthenticated } = useAuth()
  const [mode, setMode] = useState<AuthMode>('login')
  const [loginStep, setLoginStep] = useState<LoginStep>('credentials')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [registerSuccessMessage, setRegisterSuccessMessage] = useState<
    string | null
  >(null)
  const [registerFormError, setRegisterFormError] = useState<string | null>(
    null,
  )
  const [loginErrorMessage, setLoginErrorMessage] = useState<string | null>(
    null,
  )
  const [registerErrorMessage, setRegisterErrorMessage] = useState<
    string | null
  >(null)
  const [mfaCode, setMfaCode] = useState('')
  const [mfaErrorState, setMfaErrorState] = useState<{
    message: string
    expired: boolean
  } | null>(null)

  const registrationSettingsQuery = useQuery({
    queryKey: ['auth', 'registration-settings'],
    queryFn: () =>
      apiFetch<RegistrationSettingsResponse>(
        '/auth/registration-settings',
        {},
        false,
      ),
    staleTime: 60_000,
  })
  const oidcSettingsQuery = useQuery({
    queryKey: ['auth', 'oidc', 'settings'],
    queryFn: () =>
      apiFetch<OIDCPublicSettings>('/auth/oidc/settings', {}, false),
    staleTime: 60_000,
  })

  const login = useMutation({
    mutationKey: LOGIN_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<TokenResponse>(
        '/auth/login',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        false,
      ),
    onMutate: () => setLoginErrorMessage(null),
    onSuccess: (data) => {
      if (data.mfa_required) {
        setPassword('')
        setMfaCode('')
        setLoginStep('mfa')
        return
      }
      setPassword('')
      markAuthenticated()
      navigate(resolvePostLoginDestination(location.state), { replace: true })
    },
    onError: (error) => setLoginErrorMessage(resolveLoginError(error)),
    onSettled: () =>
      forgetCredentialMutationAfterSettlement(queryClient, LOGIN_MUTATION_KEY),
  })

  const verifyMfa = useMutation({
    mutationKey: MFA_VERIFY_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (code: string) =>
      apiFetch<TokenResponse>(
        '/auth/mfa/verify',
        {
          method: 'POST',
          body: JSON.stringify({ code }),
        },
        false,
      ),
    onMutate: () => setMfaErrorState(null),
    onSuccess: () => {
      setMfaCode('')
      markAuthenticated()
      navigate(resolvePostLoginDestination(location.state), { replace: true })
    },
    onError: (error) =>
      setMfaErrorState({
        message: resolveMfaLoginError(error),
        expired: isExpiredMfaChallenge(error),
      }),
    onSettled: () =>
      forgetCredentialMutationAfterSettlement(
        queryClient,
        MFA_VERIFY_MUTATION_KEY,
      ),
  })

  const register = useMutation({
    mutationKey: REGISTER_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<{ id: string }>(
        '/auth/register',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        false,
      ),
    onMutate: () => setRegisterErrorMessage(null),
    onSuccess: () => {
      setRegisterSuccessMessage(
        'Account created. Wait for admin approval before signing in.',
      )
      setConfirmPassword('')
      setPassword('')
      setMode('login')
    },
    onError: (error) => setRegisterErrorMessage(resolveRegisterError(error)),
    onSettled: () =>
      forgetCredentialMutationAfterSettlement(
        queryClient,
        REGISTER_MUTATION_KEY,
      ),
  })

  const selfRegistrationEnabled =
    registrationSettingsQuery.data?.allow_self_registration ?? false
  const authMessage =
    typeof location.state === 'object' &&
    location.state &&
    'authMessage' in location.state
      ? String(location.state.authMessage || '')
      : ''
  const oidcError = new URLSearchParams(location.search).get('oidc_error')

  const switchMode = (nextMode: AuthMode) => {
    setMode(nextMode)
    setRegisterFormError(null)
    setRegisterSuccessMessage(null)
    setLoginErrorMessage(null)
    setRegisterErrorMessage(null)
    setMfaErrorState(null)
    forgetCredentialMutation(queryClient, LOGIN_MUTATION_KEY, login.reset)
    forgetCredentialMutation(
      queryClient,
      MFA_VERIFY_MUTATION_KEY,
      verifyMfa.reset,
    )
    setLoginStep('credentials')
    setMfaCode('')
    setPassword('')
    setConfirmPassword('')
    forgetCredentialMutation(queryClient, REGISTER_MUTATION_KEY, register.reset)
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    setRegisterFormError(null)
    setRegisterSuccessMessage(null)

    if (mode === 'login' && loginStep === 'mfa') {
      if (mfaCode.trim()) {
        verifyMfa.mutate(mfaCode.trim())
      }
      return
    }

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

  const returnToPassword = () => {
    forgetCredentialMutation(
      queryClient,
      MFA_VERIFY_MUTATION_KEY,
      verifyMfa.reset,
    )
    forgetCredentialMutation(queryClient, LOGIN_MUTATION_KEY, login.reset)
    setMfaCode('')
    setMfaErrorState(null)
    setLoginStep('credentials')
  }
  const presentation = resolveLoginPresentation(
    mode,
    loginStep,
    login.isPending,
    register.isPending,
    verifyMfa.isPending,
  )

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form
        className="tl-surface w-full max-w-sm rounded-2xl p-6 shadow-sm"
        onSubmit={onSubmit}
      >
        <h2 className="font-display text-3xl">{presentation.title}</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          {presentation.description}
        </p>
        <LoginNotices
          mode={mode}
          authMessage={authMessage}
          oidcError={oidcError}
          registrationSettingsError={registrationSettingsQuery.error}
        />

        {selfRegistrationEnabled && loginStep === 'credentials' && (
          <div
            className="mt-4 grid grid-cols-2 gap-2 rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40"
            role="group"
            aria-label="Authentication mode"
          >
            <button
              type="button"
              className={`rounded px-3 py-1 text-sm ${
                mode === 'login'
                  ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                  : 'text-slate dark:text-slate-300'
              }`}
              aria-pressed={mode === 'login'}
              onClick={() => switchMode('login')}
            >
              Sign in
            </button>
            <button
              type="button"
              className={`rounded px-3 py-1 text-sm ${
                mode === 'register'
                  ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                  : 'text-slate dark:text-slate-300'
              }`}
              aria-pressed={mode === 'register'}
              onClick={() => switchMode('register')}
            >
              Register
            </button>
          </div>
        )}

        <LoginIdentityOptions
          step={loginStep}
          mode={mode}
          settings={oidcSettingsQuery.data}
          isLoading={oidcSettingsQuery.isLoading}
          isFetching={oidcSettingsQuery.isFetching}
          error={oidcSettingsQuery.error}
          onRetry={() => void oidcSettingsQuery.refetch()}
        />

        {loginStep === 'credentials' ? (
          <CredentialFields
            mode={mode}
            oidcEnabled={oidcSettingsQuery.data?.enabled === true}
            email={email}
            password={password}
            onEmailChange={(value) => {
              setEmail(value)
              setLoginErrorMessage(null)
              setRegisterErrorMessage(null)
            }}
            onPasswordChange={(value) => {
              setPassword(value)
              setLoginErrorMessage(null)
              setRegisterErrorMessage(null)
            }}
          />
        ) : (
          <MfaCodeField
            value={mfaCode}
            onChange={(value) => {
              setMfaCode(value)
              setMfaErrorState(null)
            }}
          />
        )}

        {mode === 'register' && (
          <>
            <label
              htmlFor="login-confirm-password"
              className="mt-4 block text-sm font-semibold"
            >
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
          <p
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="mt-3 text-sm text-green-700 dark:text-green-400"
          >
            {registerSuccessMessage}
          </p>
        )}
        {registerFormError && (
          <p
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className="mt-3 text-sm text-red-600 dark:text-red-300"
          >
            {registerFormError}
          </p>
        )}
        {mode === 'login' &&
          loginStep === 'credentials' &&
          loginErrorMessage && (
            <p
              role="alert"
              aria-live="assertive"
              aria-atomic="true"
              className="mt-3 text-sm text-red-600 dark:text-red-300"
            >
              {loginErrorMessage}
            </p>
          )}
        {mode === 'login' && loginStep === 'mfa' && mfaErrorState && (
          <p
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className="mt-3 text-sm text-red-600 dark:text-red-300"
          >
            {mfaErrorState.message}
          </p>
        )}
        {mode === 'register' && registerErrorMessage && (
          <p
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className="mt-3 text-sm text-red-600 dark:text-red-300"
          >
            {registerErrorMessage}
          </p>
        )}

        <button
          type="submit"
          className="mt-5 w-full rounded bg-ink px-3 py-2 font-semibold text-white hover:bg-slate dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90"
          disabled={
            login.isPending ||
            register.isPending ||
            verifyMfa.isPending ||
            (loginStep === 'mfa' && !mfaCode.trim())
          }
        >
          {presentation.submitLabel}
        </button>

        {loginStep === 'mfa' && (
          <button
            type="button"
            className="mt-2 min-h-11 w-full rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            onClick={returnToPassword}
            disabled={verifyMfa.isPending}
          >
            Back to password sign-in
          </button>
        )}

        {loginStep === 'mfa' && mfaErrorState?.expired && (
          <p
            role="status"
            className="mt-2 text-xs text-slate dark:text-slate-300"
          >
            Your password was not retained. Start again to create a fresh
            verification session.
          </p>
        )}

        {!selfRegistrationEnabled && registrationSettingsQuery.isSuccess && (
          <p className="mt-3 text-xs text-slate dark:text-slate-300">
            Self-registration is disabled by configuration.
          </p>
        )}
      </form>
    </div>
  )
}

function LoginIdentityOptions({
  step,
  mode,
  settings,
  isLoading,
  isFetching,
  error,
  onRetry,
}: {
  step: LoginStep
  mode: AuthMode
  settings?: OIDCPublicSettings
  isLoading: boolean
  isFetching: boolean
  error: unknown
  onRetry: () => void
}) {
  return step === 'credentials' ? (
    <OIDCLoginOption
      mode={mode}
      settings={settings}
      isLoading={isLoading}
      isFetching={isFetching}
      error={error}
      onRetry={onRetry}
    />
  ) : null
}

function CredentialFields({
  mode,
  oidcEnabled,
  email,
  password,
  onEmailChange,
  onPasswordChange,
}: {
  mode: AuthMode
  oidcEnabled: boolean
  email: string
  password: string
  onEmailChange: (value: string) => void
  onPasswordChange: (value: string) => void
}) {
  return (
    <>
      <label
        htmlFor="login-email"
        className={`${mode === 'login' && oidcEnabled ? '' : 'mt-5'} block text-sm font-semibold`}
      >
        Email
      </label>
      <input
        id="login-email"
        value={email}
        onChange={(event) => onEmailChange(event.target.value)}
        type="email"
        autoComplete="email"
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
        required
      />
      <label
        htmlFor="login-password"
        className="mt-4 block text-sm font-semibold"
      >
        Password
      </label>
      <input
        id="login-password"
        value={password}
        onChange={(event) => onPasswordChange(event.target.value)}
        type="password"
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
        required
      />
    </>
  )
}

function MfaCodeField({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  return (
    <>
      <label
        htmlFor="login-mfa-code"
        className="mt-5 block text-sm font-semibold"
      >
        Authenticator or recovery code
      </label>
      <input
        id="login-mfa-code"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        type="text"
        inputMode="text"
        autoComplete="one-time-code"
        autoCapitalize="none"
        spellCheck={false}
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
        aria-describedby="login-mfa-help"
        autoFocus
        required
      />
      <p
        id="login-mfa-help"
        className="mt-2 text-xs text-slate dark:text-slate-300"
      >
        Recovery codes are single use. Spaces and hyphens are accepted when
        present in the code.
      </p>
    </>
  )
}

function resolveLoginPresentation(
  mode: AuthMode,
  step: LoginStep,
  loginPending: boolean,
  registerPending: boolean,
  mfaPending: boolean,
) {
  if (mode === 'register') {
    return {
      title: 'Create Account',
      description:
        'Self-registered accounts require admin approval before login.',
      submitLabel: registerPending ? 'Submitting...' : 'Register',
    }
  }
  if (step === 'mfa') {
    return {
      title: 'Verify Sign-In',
      description:
        'Enter a current authenticator code or one of your unused recovery codes.',
      submitLabel: mfaPending ? 'Verifying...' : 'Verify and sign in',
    }
  }
  return {
    title: 'Analyst Login',
    description: 'Sign in to manage feeds and triage articles.',
    submitLabel: loginPending ? 'Signing in...' : 'Sign in',
  }
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
  registrationSettingsError: unknown
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
      {Boolean(registrationSettingsError) && (
        <p
          role="alert"
          aria-live="polite"
          aria-atomic="true"
          className="mt-3 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
        >
          {resolveApiErrorMessage(
            registrationSettingsError,
            'Registration availability could not be loaded. Sign-in is still available',
          )}
        </p>
      )}
    </>
  )
}

function OIDCLoginOption({
  mode,
  settings,
  isLoading,
  isFetching,
  error,
  onRetry,
}: {
  mode: AuthMode
  settings: OIDCPublicSettings | undefined
  isLoading: boolean
  isFetching: boolean
  error: unknown
  onRetry: () => void
}) {
  if (mode !== 'login') {
    return null
  }
  if (isLoading) {
    return (
      <p
        role="status"
        aria-live="polite"
        className="mt-5 text-sm text-slate dark:text-slate-300"
      >
        Checking single sign-on availability...
      </p>
    )
  }
  if (error) {
    return (
      <div
        role="alert"
        className="mt-5 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
      >
        <p>
          {resolveApiErrorMessage(
            error,
            'Single sign-on availability could not be loaded. Local sign-in remains available',
          )}
        </p>
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onRetry}
          disabled={isFetching}
        >
          {isFetching ? 'Retrying...' : 'Retry SSO check'}
        </button>
      </div>
    )
  }
  if (!settings?.enabled) {
    return (
      <p className="mt-5 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-slate-300">
        Single sign-on is disabled. Use a local account.
      </p>
    )
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
      <div
        className="my-4 flex items-center gap-3 text-xs text-slate dark:text-slate-400"
        aria-hidden="true"
      >
        <span className="h-px flex-1 bg-slate/20 dark:bg-white/10" />
        <span>or use a local account</span>
        <span className="h-px flex-1 bg-slate/20 dark:bg-white/10" />
      </div>
    </>
  )
}

function resolveLoginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    return resolveApiErrorMessage(error, 'Too many sign-in attempts')
  }
  if (error instanceof ApiError && error.status === 401) {
    return resolveApiErrorMessage(error, 'Sign in failed', {
      includeApiDetail: false,
      retryGuidance: 'Check your email and password, then try again.',
    })
  }
  return resolveApiErrorMessage(error, 'Sign in failed')
}

function resolveRegisterError(error: unknown): string {
  return resolveApiErrorMessage(error, 'Registration could not be completed')
}

function resolveOidcError(errorCode: string): string {
  return resolveOIDCLoginError(errorCode)
}

function resolvePostLoginDestination(state: unknown): string {
  if (!state || typeof state !== 'object' || !('from' in state)) {
    return '/'
  }

  const from = state.from
  if (!from || typeof from !== 'object') {
    return '/'
  }

  const pathname =
    'pathname' in from && typeof from.pathname === 'string'
      ? from.pathname
      : '/'
  const search =
    'search' in from && typeof from.search === 'string' ? from.search : ''
  const hash = 'hash' in from && typeof from.hash === 'string' ? from.hash : ''
  return `${pathname}${search}${hash}` || '/'
}
