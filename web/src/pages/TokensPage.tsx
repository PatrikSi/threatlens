import { type FormEvent, type RefObject, useEffect, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  DEFAULT_TOKEN_EXPIRY_DAYS,
  useTokenCreateFormState,
} from '../hooks/useTokenCreateFormState'
import type {
  ApiTokenCreateRequest,
  ApiTokenCreateResponse,
  AuthSessionListResponse,
  MFAStatusResponse,
} from '../types/api'
import { resolveCurrentSession, resolvePrivilegedSessionState } from './authSessionModel'
import {
  resolveOIDCReauthNotice,
  resolveOIDCReauthStartError,
} from './oidcCallbackMessages'
import {
  beginOIDCReauthentication,
  readOIDCReauthNavigationState,
} from './oidcReauthentication'
import { TokenCreatePanel } from './TokenCreatePanel'
import { TokenInventory } from './TokenInventory'
import {
  buildTokenCreatePayload,
  getTokenCreateValidationIssue,
  resolveTokenCreateError,
} from './tokenCreateModel'

const CREATE_TOKEN_MUTATION_KEY = ['tokens', 'create'] as const
const MFA_STATUS_QUERY_KEY = ['auth', 'security', 'mfa'] as const

function forgetSettledTokenMutation(queryClient: QueryClient) {
  window.setTimeout(() => {
    const mutationCache = queryClient.getMutationCache()
    mutationCache
      .findAll({ mutationKey: CREATE_TOKEN_MUTATION_KEY, exact: true })
      .forEach((mutation) => {
        if (mutation.state.status !== 'pending') mutationCache.remove(mutation)
      })
  }, 0)
}

export function TokensPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [tokenFormState, dispatchTokenForm] = useTokenCreateFormState()
  const [tokenFormError, setTokenFormError] = useState('')
  const [createTokenError, setCreateTokenError] = useState('')
  const [secretNotice, setSecretNotice] = useState('')
  const reauthContinuationHandledRef = useRef(false)
  const nameInputRef = useRef<HTMLInputElement | null>(null)
  const expiryInputRef = useRef<HTMLInputElement | null>(null)
  const passwordInputRef = useRef<HTMLInputElement | null>(null)
  const codeInputRef = useRef<HTMLInputElement | null>(null)
  const isAdmin = !meQuery.isError && meQuery.data?.role === 'admin'
  const creationAvailable =
    !meQuery.isError && browserTokenCreationAvailable(meQuery.data)
  const confirmDiscardTokenDraft = useUnsavedChangesWarning(
    tokenDraftIsDirty(tokenFormState),
    tokenFormState.createdToken
      ? 'The one-time API token is still visible. Leave without confirming that it is stored?'
      : 'You have an unfinished API token draft. Leave without creating it?',
  )
  const mfaStatusQuery = useQuery({
    queryKey: MFA_STATUS_QUERY_KEY,
    queryFn: () => apiFetch<MFAStatusResponse>('/auth/security/mfa'),
    enabled: creationAvailable,
  })
  const sessionsQuery = useQuery({
    queryKey: ['auth', 'security', 'sessions'],
    queryFn: () => apiFetch<AuthSessionListResponse>('/auth/security/sessions'),
    enabled: creationAvailable,
  })
  const refetchCurrentUser = meQuery.refetch
  const refetchSessions = sessionsQuery.refetch
  const currentSession = resolveCurrentSession(sessionsQuery.data)
  const privilegedSession = resolvePrivilegedSessionState(
    meQuery.data?.authentication,
    sessionsQuery.data,
  )
  const currentAuthMethod = privilegedSession.authMethod
  const localCredentialsRequired = currentAuthMethod !== 'oidc'
  const oidcVerificationRequired =
    currentAuthMethod === 'oidc' && !privilegedSession.recentAuthenticationValid
  const reauthNavigation = readOIDCReauthNavigationState(
    location.state,
    'api_token_create',
  )
  const oidcReauthentication = useMutation({
    mutationKey: ['auth', 'oidc', 'reauth', 'api-token'],
    mutationFn: () =>
      beginOIDCReauthentication({
        returnPath: '/settings/tokens',
        purpose: 'api_token_create',
        context: {
          tokenName: tokenFormState.name,
          tokenExpiresInDays: tokenFormState.expiresInDays,
          tokenScopes: tokenFormState.scopesText,
        },
      }),
    onError: (error) => setCreateTokenError(resolveOIDCReauthStartError(error)),
  })

  useEffect(() => {
    if (reauthContinuationHandledRef.current || !reauthNavigation) return
    reauthContinuationHandledRef.current = true
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    })
    const context = reauthNavigation.context
    if (context?.tokenName !== undefined) {
      dispatchTokenForm({ type: 'setName', value: context.tokenName })
    }
    if (context?.tokenExpiresInDays !== undefined) {
      dispatchTokenForm({
        type: 'setExpiresInDays',
        value: context.tokenExpiresInDays,
      })
    }
    if (context?.tokenScopes !== undefined) {
      dispatchTokenForm({ type: 'setScopesText', value: context.tokenScopes })
    }
    const callbackNotice = resolveOIDCReauthNotice(reauthNavigation.result)
    if (callbackNotice.error) {
      setCreateTokenError(callbackNotice.message)
      return
    }
    setCreateTokenError('')
    setSecretNotice(
      `${callbackNotice.message} Review the restored token request, then generate it.`,
    )
    void Promise.all([refetchCurrentUser(), refetchSessions()])
  }, [
    dispatchTokenForm,
    location.pathname,
    location.search,
    navigate,
    reauthNavigation,
    refetchCurrentUser,
    refetchSessions,
  ])
  const createToken = useMutation({
    mutationKey: CREATE_TOKEN_MUTATION_KEY,
    gcTime: 0,
    mutationFn: (body: ApiTokenCreateRequest) =>
      apiFetch<ApiTokenCreateResponse>('/tokens', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onMutate: () => setCreateTokenError(''),
    onSuccess: (data) => {
      setSecretNotice('')
      dispatchTokenForm({ type: 'createSucceeded', value: data })
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: (error) => {
      dispatchTokenForm({ type: 'createFailed' })
      setCreateTokenError(resolveTokenCreateError(error))
    },
    onSettled: () => {
      dispatchTokenForm({ type: 'clearCode' })
      forgetSettledTokenMutation(queryClient)
    },
  })

  const onCreateSubmit = (event: FormEvent) => {
    event.preventDefault()
    const mfaEnabled = mfaStatusQuery.data?.enabled === true
    const validationIssue = getTokenCreateValidationIssue(
      tokenFormState,
      mfaEnabled,
      localCredentialsRequired,
    )
    if (validationIssue) {
      setTokenFormError(validationIssue.message)
      focusTokenValidationIssue(validationIssue.field, {
        name: nameInputRef,
        expiry: expiryInputRef,
        password: passwordInputRef,
        code: codeInputRef,
      })
      return
    }
    setTokenFormError('')
    setCreateTokenError('')
    if (oidcVerificationRequired) {
      setTokenFormError(
        'Verify this SSO session before generating the API token.',
      )
      return
    }
    dispatchTokenForm({ type: 'createStarted' })
    createToken.mutate(
      buildTokenCreatePayload(
        tokenFormState,
        mfaEnabled,
        localCredentialsRequired,
      ),
    )
  }

  const updateTokenForm = (action: Parameters<typeof dispatchTokenForm>[0]) => {
    setTokenFormError('')
    setCreateTokenError('')
    dispatchTokenForm(action)
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      {confirmDiscardTokenDraft.discardDialog}
      <TokenCreatePanel
        state={tokenFormState}
        formError={tokenFormError}
        requestError={createTokenError}
        mfaStatus={mfaStatusQuery.data}
        mfaLoading={mfaStatusQuery.isLoading}
        mfaError={mfaStatusQuery.error}
        mfaFetching={mfaStatusQuery.isFetching}
        creationAvailable={creationAvailable}
        currentAuthMethod={currentAuthMethod ?? currentSession?.auth_method}
        oidcRecentlyAuthenticated={!oidcVerificationRequired}
        oidcReauthPending={oidcReauthentication.isPending}
        oidcReauthError={oidcReauthentication.error}
        createPending={createToken.isPending}
        nameInputRef={nameInputRef}
        expiryInputRef={expiryInputRef}
        passwordInputRef={passwordInputRef}
        codeInputRef={codeInputRef}
        onSubmit={onCreateSubmit}
        onNameChange={(value) => updateTokenForm({ type: 'setName', value })}
        onExpiryChange={(value) =>
          updateTokenForm({ type: 'setExpiresInDays', value })
        }
        onScopesChange={(value) =>
          updateTokenForm({ type: 'setScopesText', value })
        }
        onPasswordChange={(value) =>
          updateTokenForm({ type: 'setCurrentPassword', value })
        }
        onCodeChange={(value) => updateTokenForm({ type: 'setCode', value })}
        onRetryMfa={() => void mfaStatusQuery.refetch()}
        onOIDCReauthenticate={() => oidcReauthentication.mutate()}
        onCreatedTokenStored={(method) => {
          dispatchTokenForm({ type: 'dismissCreatedToken' })
          setSecretNotice(
            method === 'copied'
              ? 'API token copied and cleared from this page.'
              : 'API token cleared from this page after storage was acknowledged.',
          )
        }}
      />
      <TokenInventory isAdmin={isAdmin} secretNotice={secretNotice} />
    </div>
  )
}

function browserTokenCreationAvailable(
  user: ReturnType<typeof useCurrentUser>['data'],
): boolean {
  return Boolean(
    user &&
    (user.password_login_enabled !== false ||
      user.authentication?.session_auth_method === 'oidc'),
  )
}

function tokenDraftIsDirty(
  state: ReturnType<typeof useTokenCreateFormState>[0],
): boolean {
  return Boolean(
    state.name.trim() ||
    state.scopesText.trim() ||
    state.currentPassword ||
    state.code ||
    state.expiresInDays !== DEFAULT_TOKEN_EXPIRY_DAYS ||
    state.createdToken,
  )
}

function focusTokenValidationIssue(
  field: 'name' | 'expiry' | 'password' | 'code',
  refs: Record<
    'name' | 'expiry' | 'password' | 'code',
    RefObject<HTMLInputElement | null>
  >,
) {
  window.requestAnimationFrame(() => refs[field].current?.focus())
}
