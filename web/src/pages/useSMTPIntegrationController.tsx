import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { rebaseSavedDraft } from '../hooks/rebaseSavedDraft'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  Feed,
  IntegrationDeliveryReplayResponse,
  NotificationTemplateVariable,
  SMTPAnalyticsResponse,
  SMTPDelivery,
  SMTPDeliveryListResponse,
  SMTPHook,
  SMTPHookTestRequest,
  SMTPHookWriteRequest,
  SMTPTemplateDefault,
  SMTPTestResponse,
  SMTPTestRunListResponse,
} from '../types/api'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { admittedRecordValue } from './notificationReadPresentation'
import {
  applySMTPTemplateDefault,
  createSMTPHookDraft,
  createSMTPHookRequest,
  DEFAULT_SMTP_HOOK_DRAFT,
  getFirstSMTPHookDraftValidationError,
  smtpHookDraftFingerprint,
  SMTPHookDraft,
  validateSMTPHookDraft,
} from './smtpHookDraft'
import {
  aiDailyBriefIsAvailable,
  aiReportingIsAvailable,
  createNewHookDraft,
  EMPTY_TEMPLATE_DEFAULTS,
  NoticeState,
  resolveSMTPEventAvailability,
  resolveTestValidationError,
  SendForValue,
  smtpTemplateForAvailableEvents,
} from './smtpIntegrationPresentation'

const DELIVERY_HISTORY_REFRESH_MS = 30_000

export function useSMTPIntegrationController() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const canManageEmailDelivery = !currentUserQuery.isError && hasRequiredPermissions(
    currentUserQuery.data?.access?.permissions ?? [],
    ['write:integrations'],
  )
  const isReadOnly = !currentUserQuery.isLoading && !canManageEmailDelivery
  const accessNotice = isReadOnly
    ? 'You can review email delivery, but changes require permission to manage integrations.'
    : null
  const [selectedHookId, setSelectedHookId] = useState<string | null>(null)
  const selectedHookIdRef = useRef(selectedHookId)
  selectedHookIdRef.current = selectedHookId
  const collectionAccess = useQueryAccessScope(['integrations', 'smtp', 'hooks'])
  const analyticsAccess = useQueryAccessScope(['integrations', 'smtp', 'analytics'])
  const deliveryAccess = useQueryAccessScope(['integrations', 'smtp', 'hooks', selectedHookId ?? '', 'deliveries'])
  const testRunAccess = useQueryAccessScope(['integrations', 'smtp', 'hooks', selectedHookId ?? '', 'test-runs'])
  const [baselineAdmission, setBaselineAdmission] = useState(() => collectionAccess.captureResult()(true))
  const [baselineDraft, setBaselineDraft] = useState<SMTPHookDraft>(DEFAULT_SMTP_HOOK_DRAFT)
  const draftRef = useRef<SMTPHookDraft>(DEFAULT_SMTP_HOOK_DRAFT)
  const [selectionInitialized, setSelectionInitialized] = useState(false)
  const [draft, setDraftState] = useState<SMTPHookDraft>(DEFAULT_SMTP_HOOK_DRAFT)
  draftRef.current = draft
  const [notice, setNotice] = useState<NoticeState | null>(null)
  const [sendTestEmail, setSendTestEmail] = useState(false)
  const [testRecipient, setTestRecipient] = useState('')
  const [testReceipt, setTestReceipt] = useState<{ value: SMTPTestResponse; admission: typeof baselineAdmission; hookId: string | null } | null>(null)
  const [deleteIntent, setDeleteIntent] = useState<{ value: SMTPHook; admission: typeof baselineAdmission } | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [replayIntent, setReplayIntent] = useState<{ value: SMTPDelivery; admission: typeof baselineAdmission; collection: typeof baselineAdmission; page: number } | null>(null)
  const [deliveryPage, setDeliveryPage] = useState(1)
  const [testRunPage, setTestRunPage] = useState(1)

  const hooksQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks'],
    ...collectionAccess.queryOptions(({ signal }) => apiFetch<SMTPHook[]>('/integrations/smtp/hooks', { signal })),
  })
  const analyticsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'analytics'],
    ...analyticsAccess.queryOptions(({ signal }) => apiFetch<SMTPAnalyticsResponse>('/integrations/smtp/analytics', { signal })),
    refetchInterval: DELIVERY_HISTORY_REFRESH_MS,
  })
  const defaultsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'template-defaults'],
    queryFn: () => apiFetch<SMTPTemplateDefault[]>('/integrations/smtp/template-defaults'),
  })
  const feedAccess = useQueryAccessScope(['feeds'])
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    ...feedAccess.queryOptions(({ signal }) => apiFetch<Feed[]>('/feeds', { signal })),
  })
  const variablesQuery = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: () => apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables'),
  })

  const hooks = hooksQuery.data ?? []
  const feeds = feedsQuery.data ?? []
  const variables = variablesQuery.data ?? []
  const templateDefaults = defaultsQuery.data ?? EMPTY_TEMPLATE_DEFAULTS
  const selectedHook = hooks.find((hook) => hook.id === selectedHookId) ?? null
  const newHookDraft = useMemo(() => createNewHookDraft(templateDefaults), [templateDefaults])
  const validation = useMemo(() => validateSMTPHookDraft(draft), [draft])
  const firstValidationError = getFirstSMTPHookDraftValidationError(validation)
  const draftDirty = smtpHookDraftFingerprint(draft) !== smtpHookDraftFingerprint(baselineDraft)
  const confirmDiscardUnsavedChanges = useUnsavedChangesWarning(
    draftDirty,
    'Discard unsaved email destination changes?',
  )
  const credentialSources = hooks.filter(
    (hook) => hook.id !== selectedHookId && !hook.uses_shared_credentials && Boolean(hook.host),
  )
  const selectedCredentialSource = hooks.find((hook) => hook.id === draft.credential_source_id) ?? null
  const eventAvailability = resolveSMTPEventAvailability(
    aiDailyBriefIsAvailable(currentUserQuery.data),
    draft.event_types,
    aiReportingIsAvailable(currentUserQuery.data),
  )

  const baselineAuthorized = Boolean(collectionAccess.currentData(baselineAdmission))
  const editorReadHidden = Boolean(selectedHookId) && collectionAccess.generation > 0 && !selectedHook
  const editorReadUnavailable = Boolean(selectedHookId) && (hooksQuery.isError || !selectedHook || !baselineAuthorized)
  const visibleDraft = selectedHookId && !baselineAuthorized && !draftDirty ? newHookDraft : draft
  const collectionKey = ['integrations', 'smtp', 'hooks'] as const
  function currentHooks() {
    return queryClient.getQueryState(collectionKey)?.status === 'error' ? undefined
      : collectionAccess.currentData<SMTPHook[]>(queryClient.getQueryData(collectionKey))
  }
  function assertSavedHook(hookId: string | null) {
    if (!canManageEmailDelivery || hookId !== selectedHookIdRef.current) throw new Error('Verify the selected email destination before continuing.')
    if (hookId && (!collectionAccess.currentData(baselineAdmission) || !currentHooks()?.some((row) => row.id === hookId))) {
      throw new Error('Reload the saved email destination before continuing. Your local draft is kept.')
    }
  }
  const setDraft: Dispatch<SetStateAction<SMTPHookDraft>> = (value) => {
    if (!canManageEmailDelivery || editorReadUnavailable) return
    setTestReceipt(null)
    setDraftState(value)
  }
  const adoptHook = useCallback((hook: SMTPHook) => {
    const next = createSMTPHookDraft(hook)
    setSelectedHookId(hook.id)
    setDraftState(next)
    setBaselineDraft(next)
    setBaselineAdmission(collectionAccess.captureResult()(true))
  }, [collectionAccess])
  useEffect(() => {
    if (selectionInitialized || !hooksQuery.data) return
    setSelectionInitialized(true)
    const firstHook = hooksQuery.data[0]
    if (firstHook) adoptHook(firstHook)
    else {
      setDraftState(newHookDraft)
      setBaselineDraft(newHookDraft)
    }
  }, [adoptHook, hooksQuery.data, newHookDraft, selectionInitialized])
  useEffect(() => {
    if (draftDirty) return
    if (selectedHook) {
      const next = createSMTPHookDraft(selectedHook)
      if (baselineAuthorized && smtpHookDraftFingerprint(next) === smtpHookDraftFingerprint(draft)) return
      adoptHook(selectedHook)
    } else if (selectionInitialized && !selectedHookId && smtpHookDraftFingerprint(draft) !== smtpHookDraftFingerprint(newHookDraft)) {
      setDraftState(newHookDraft)
      setBaselineDraft(newHookDraft)
    }
  }, [adoptHook, baselineAuthorized, draft, draftDirty, newHookDraft, selectedHook, selectedHookId, selectionInitialized])
  useEffect(() => { setDeliveryPage(1); setTestRunPage(1) }, [selectedHookId])
  useEffect(() => { setReplayIntent(null) }, [deliveryPage, selectedHookId])
  const reloadSavedHook = () => {
    const row = currentHooks()?.find((entry) => entry.id === selectedHookIdRef.current)
    if (row) confirmDiscardUnsavedChanges(() => adoptHook(row))
    else void hooksQuery.refetch()
  }

  const saveHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'save'],
    onMutate: () => collectionAccess.captureResult(),
    mutationFn: ({ hookId, hook }: { hookId: string | null; hook: SMTPHookWriteRequest; submittedDraft: SMTPHookDraft }) => {
      assertSavedHook(hookId)
      return apiFetch<SMTPHook>(hookId ? `/integrations/smtp/hooks/${hookId}` : '/integrations/smtp/hooks', {
        method: hookId ? 'PATCH' : 'POST', body: JSON.stringify(hook),
      })
    },
    onSuccess: (saved, variables, capture) => {
      if (capture && collectionAccess.currentData(capture(saved))) {
        if (queryClient.getQueryState(collectionKey)?.status !== 'error') {
          queryClient.setQueryData<ReturnType<typeof capture<SMTPHook[]>>>(collectionKey, (current) => {
            const rows = collectionAccess.currentData(current)
            return rows ? capture([...rows.filter((row) => row.id !== saved.id), saved]) : current
          })
        }
        if (selectedHookIdRef.current === variables.hookId) {
          const nextBaseline = createSMTPHookDraft(saved)
          const nextDraft = rebaseSavedDraft(variables.submittedDraft, draftRef.current, nextBaseline)
          setSelectedHookId(saved.id)
          setDraftState(nextDraft)
          setBaselineDraft(nextBaseline)
          setBaselineAdmission(capture(true))
        }
      }
      setTestReceipt(null)
      setNotice({ tone: 'success', message: variables.hookId ? 'Email destination updated.' : 'Email destination created.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  const deleteHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'delete'],
    onMutate: () => collectionAccess.captureResult(),
    mutationFn: (hookId: string) => {
      assertSavedHook(hookId)
      if (!deleteIntent || deleteIntent.value.id !== hookId || !collectionAccess.currentData(deleteIntent.admission)) throw new Error('Review the current destination deletion again.')
      return apiFetch<void>(`/integrations/smtp/hooks/${hookId}`, { method: 'DELETE' })
    },
    onSuccess: (_result, hookId, capture) => {
      setDeleteIntent(null)
      setDeleteError(null)
      if (selectedHookIdRef.current === hookId && capture && collectionAccess.currentData(capture(true))) {
        setSelectedHookId(null)
        setDraftState(newHookDraft)
        setBaselineDraft(newHookDraft)
        setBaselineAdmission(capture(true))
      }
      setTestReceipt(null)
      setNotice({ tone: 'success', message: 'Email destination deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
    onError: (error) => setDeleteError(resolveApiErrorMessage(error, 'Failed to delete email destination.')),
  })

  const testHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'test'],
    onMutate: () => ({ capture: collectionAccess.captureResult(), hookId: selectedHookIdRef.current }),
    mutationFn: (payload: SMTPHookTestRequest) => {
      assertSavedHook(payload.hook_id ?? null)
      return apiFetch<SMTPTestResponse>('/integrations/smtp/hooks/test', { method: 'POST', body: JSON.stringify(payload) })
    },
    onSuccess: (result, variables, context) => {
      if (!context || context.hookId !== selectedHookIdRef.current || !collectionAccess.currentData(context.capture(true))) return
      setTestReceipt({ value: result, admission: context.capture(true), hookId: context.hookId })
      setNotice({
        tone: result.success ? 'success' : 'error',
        message: result.success ? 'Email delivery test succeeded.' : 'Email delivery test failed.',
      })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'hooks'], exact: true })
      if (variables.hook_id) {
        void queryClient.invalidateQueries({
          queryKey: ['integrations', 'smtp', 'hooks', variables.hook_id, 'test-runs'],
        })
      }
    },
  })

  const deliveriesKey = ['integrations', 'smtp', 'hooks', selectedHookId, 'deliveries', deliveryPage, collectionAccess.generation] as const
  const deliveriesQuery = useQuery({
    queryKey: deliveriesKey,
    ...deliveryAccess.queryOptions(({ signal }) => apiFetch<SMTPDeliveryListResponse>(
      `/integrations/smtp/hooks/${selectedHookId}/deliveries?page=${deliveryPage}&page_size=10`, { signal },
    )),
    enabled: Boolean(selectedHookId) && !collectionAccess.denied,
    refetchInterval: selectedHookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const testRunsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks', selectedHookId, 'test-runs', testRunPage, collectionAccess.generation],
    ...testRunAccess.queryOptions(({ signal }) => apiFetch<SMTPTestRunListResponse>(
      `/integrations/smtp/hooks/${selectedHookId}/test-runs?page=${testRunPage}&page_size=10`, { signal },
    )),
    enabled: Boolean(selectedHookId) && !collectionAccess.denied,
    refetchInterval: selectedHookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const replayDelivery = useMutation({
    mutationKey: ['integrations', 'smtp', 'deliveries', 'replay'],
    mutationFn: ({ hookId, deliveryId }: { hookId: string; deliveryId: string }) => {
      assertSavedHook(hookId)
      const current = queryClient.getQueryState(deliveriesKey)?.status === 'error' ? undefined
        : deliveryAccess.currentData<SMTPDeliveryListResponse>(queryClient.getQueryData(deliveriesKey))
      if (!replayIntent || replayIntent.value.id !== deliveryId || replayIntent.page !== deliveryPage
        || !deliveryAccess.currentData(replayIntent.admission) || !collectionAccess.currentData(replayIntent.collection)
        || !current?.deliveries.some((row) => row.id === deliveryId && row.state === 'dead_letter')) throw new Error('Refresh the current delivery and review replay again.')
      return apiFetch<IntegrationDeliveryReplayResponse>(`/integrations/smtp/hooks/${hookId}/deliveries/${deliveryId}/replay`, { method: 'POST' })
    },
    onSuccess: (result, variables) => {
      setReplayIntent(null)
      setNotice({
        tone: 'success',
        message: result.queued
          ? 'Dead-letter delivery queued for replay.'
          : 'Replay created and awaiting queue recovery.',
      })
      void queryClient.invalidateQueries({
        queryKey: ['integrations', 'smtp', 'hooks', variables.hookId, 'deliveries'],
      })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'analytics'] })
    },
    onError: () => setReplayIntent(null),
  })

  const replayReadUnavailable = editorReadUnavailable || deliveriesQuery.isError || !deliveriesQuery.data
  const pendingDelete = admittedRecordValue(deleteIntent,
    (intent) => collectionAccess.currentData(intent.admission) && intent.value.id === selectedHookId)
  const pendingReplay = admittedRecordValue(replayIntent,
    (intent) => deliveryAccess.currentData(intent.admission) && collectionAccess.currentData(intent.collection) && intent.page === deliveryPage && intent.value.hook_id === selectedHookId)
  const testResult = admittedRecordValue(testReceipt,
    (receipt) => receipt.hookId === selectedHookId && collectionAccess.currentData(receipt.admission))
  const setPendingDelete = (value: SMTPHook | null) => setDeleteIntent(value ? { value, admission: collectionAccess.captureResult()(true) } : null)
  const setPendingReplay = (value: SMTPDelivery | null) => {
    if (value && replayReadUnavailable) return
    setReplayIntent(value ? { value, admission: deliveryAccess.captureResult()(true), collection: collectionAccess.captureResult()(true), page: deliveryPage } : null)
  }
  const setTestResult = (value: SMTPTestResponse | null) => setTestReceipt(value ? { value, admission: collectionAccess.captureResult()(true), hookId: selectedHookId } : null)

  const resetTransientState = () => {
    setNotice(null)
    setTestReceipt(null)
    setSendTestEmail(false)
    setTestRecipient('')
    setReplayIntent(null)
    saveHook.reset()
    testHook.reset()
    replayDelivery.reset()
  }

  const onSelectHook = (hook: SMTPHook) => {
    if (hook.id === selectedHookId) return
    confirmDiscardUnsavedChanges(() => {
      const current = currentHooks()?.find((row) => row.id === hook.id)
      if (!current) return
      adoptHook(current)
      resetTransientState()
    })
  }

  const onCreateHook = () => {
    if (!canManageEmailDelivery) return
    confirmDiscardUnsavedChanges(() => {
      setSelectedHookId(null)
      setDraftState(newHookDraft)
      setBaselineDraft(newHookDraft)
      setBaselineAdmission(collectionAccess.captureResult()(true))
      resetTransientState()
    })
  }

  const onSave = () => {
    if (!canManageEmailDelivery || editorReadUnavailable) return
    if (firstValidationError) {
      setNotice({ tone: 'error', message: firstValidationError })
      return
    }
    setNotice(null)
    saveHook.mutate({ hookId: selectedHookId, hook: createSMTPHookRequest(draft), submittedDraft: draft })
  }

  const onTest = () => {
    if (!canManageEmailDelivery || editorReadUnavailable) return
    const testError = resolveTestValidationError(draft, sendTestEmail, testRecipient, firstValidationError)
    if (testError) {
      setNotice({ tone: 'error', message: testError })
      return
    }
    setNotice(null)
    testHook.mutate({
      hook_id: selectedHookId,
      hook: !selectedHookId || draftDirty ? createSMTPHookRequest(draft) : null,
      send_email: sendTestEmail,
      recipient_email: sendTestEmail ? testRecipient.trim() : null,
    })
  }

  const onCredentialSourceChange = (sourceId: string) => {
    if (!canManageEmailDelivery) return
    if (!sourceId) {
      setDraft((current) => ({
        ...current,
        credential_source_id: null,
        host: current.credential_source_id ? '' : current.host,
        port: current.credential_source_id ? '587' : current.port,
        security: current.credential_source_id ? 'starttls' : current.security,
        username: current.credential_source_id ? '' : current.username,
        password: '',
        clear_password: false,
      }))
      return
    }
    const source = hooks.find((hook) => hook.id === sourceId)
    if (!source) {
      setNotice({ tone: 'error', message: 'The selected credential source is no longer available.' })
      return
    }
    setDraft((current) => ({
      ...current,
      credential_source_id: source.id,
      host: source.host ?? '',
      port: String(source.port),
      security: source.security,
      username: source.username ?? '',
      password: '',
      clear_password: false,
    }))
  }

  const onSendForChange = (sendFor: SendForValue) => {
    if (!canManageEmailDelivery) return
    if (sendFor === 'custom') return
    const template = templateDefaults.find((entry) => entry.send_for === sendFor)
    if (!template) {
      setNotice({ tone: 'error', message: 'The default template for this event could not be loaded.' })
      return
    }
    const availableTemplate = smtpTemplateForAvailableEvents(
      template,
      sendFor,
      eventAvailability.availableEventTypes,
    )
    setDraft((current) => applySMTPTemplateDefault(current, availableTemplate))
  }

  const loadError = hooksQuery.error
    ?? analyticsQuery.error
    ?? defaultsQuery.error
    ?? feedsQuery.error
    ?? variablesQuery.error

  return {
    accessNotice,
    analyticsQuery,
    canManageEmailDelivery,
    credentialSources,
    deleteError,
    deleteHook,
    deliveriesQuery,
    deliveryPage,
    draft: visibleDraft,
    editorReadUnavailable,
    editorReadHidden,
    replayReadUnavailable,
    reloadSavedHook,
    eventAvailability,
    feeds,
    hooks,
    hooksQuery,
    loadError,
    notice,
    onCreateHook,
    onCredentialSourceChange,
    onSave,
    onSelectHook,
    onSendForChange,
    onTest,
    pendingDelete,
    pendingReplay,
    replayDelivery,
    saveHook,
    selectedCredentialSource,
    selectedHook,
    selectedHookId,
    sendTestEmail,
    setDeleteError,
    setDeliveryPage,
    setDraft,
    setPendingDelete,
    setPendingReplay,
    setSendTestEmail,
    setTestRecipient,
    setTestResult,
    setTestRunPage,
    testHook,
    testRecipient,
    testResult,
    testRunPage,
    testRunsQuery,
    validation,
    variables,
    discardDialog: confirmDiscardUnsavedChanges.discardDialog,
    confirmDiscardUnsavedChanges,
  }
}

export type SMTPIntegrationController = ReturnType<typeof useSMTPIntegrationController>
