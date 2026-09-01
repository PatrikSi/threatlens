import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

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
  const canManageEmailDelivery = hasRequiredPermissions(
    currentUserQuery.data?.access?.permissions ?? [],
    ['write:integrations'],
  )
  const isReadOnly = !currentUserQuery.isLoading && !canManageEmailDelivery
  const accessNotice = isReadOnly
    ? 'You can review email delivery, but changes require permission to manage integrations.'
    : null
  const [selectedHookId, setSelectedHookId] = useState<string | null>(null)
  const [selectionInitialized, setSelectionInitialized] = useState(false)
  const [draft, setDraftState] = useState<SMTPHookDraft>(DEFAULT_SMTP_HOOK_DRAFT)
  const [hasUserEdited, setHasUserEdited] = useState(false)
  const [notice, setNotice] = useState<NoticeState | null>(null)
  const [sendTestEmail, setSendTestEmail] = useState(false)
  const [testRecipient, setTestRecipient] = useState('')
  const [testResult, setTestResult] = useState<SMTPTestResponse | null>(null)
  const [pendingDelete, setPendingDelete] = useState<SMTPHook | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [pendingReplay, setPendingReplay] = useState<SMTPDelivery | null>(null)
  const [deliveryPage, setDeliveryPage] = useState(1)
  const [testRunPage, setTestRunPage] = useState(1)

  const hooksQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks'],
    queryFn: () => apiFetch<SMTPHook[]>('/integrations/smtp/hooks'),
  })
  const analyticsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'analytics'],
    queryFn: () => apiFetch<SMTPAnalyticsResponse>('/integrations/smtp/analytics'),
    refetchInterval: DELIVERY_HISTORY_REFRESH_MS,
  })
  const defaultsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'template-defaults'],
    queryFn: () => apiFetch<SMTPTemplateDefault[]>('/integrations/smtp/template-defaults'),
  })
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
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
  const baselineDraft = useMemo(
    () => (selectedHook ? createSMTPHookDraft(selectedHook) : newHookDraft),
    [newHookDraft, selectedHook],
  )
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

  const setDraft: Dispatch<SetStateAction<SMTPHookDraft>> = (value) => {
    if (!canManageEmailDelivery) return
    setHasUserEdited(true)
    setTestResult(null)
    setDraftState(value)
  }

  useEffect(() => {
    if (selectionInitialized || !hooksQuery.data) return
    const firstHook = hooksQuery.data[0]
    setSelectionInitialized(true)
    if (firstHook) {
      setSelectedHookId(firstHook.id)
      setDraftState(createSMTPHookDraft(firstHook))
      return
    }
    setDraftState(createNewHookDraft(templateDefaults))
  }, [hooksQuery.data, selectionInitialized, templateDefaults])

  useEffect(() => {
    if (hasUserEdited) return
    if (selectedHook) {
      setDraftState(createSMTPHookDraft(selectedHook))
    } else if (selectionInitialized) {
      setDraftState(newHookDraft)
    }
  }, [hasUserEdited, newHookDraft, selectedHook, selectionInitialized])

  useEffect(() => {
    setDeliveryPage(1)
    setTestRunPage(1)
  }, [selectedHookId])

  const saveHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'save'],
    mutationFn: ({ hookId, hook }: { hookId: string | null; hook: SMTPHookWriteRequest }) =>
      apiFetch<SMTPHook>(hookId ? `/integrations/smtp/hooks/${hookId}` : '/integrations/smtp/hooks', {
        method: hookId ? 'PATCH' : 'POST',
        body: JSON.stringify(hook),
      }),
    onSuccess: (saved, variables) => {
      setSelectedHookId(saved.id)
      setDraftState(createSMTPHookDraft(saved))
      setHasUserEdited(false)
      setTestResult(null)
      setNotice({ tone: 'success', message: variables.hookId ? 'Email destination updated.' : 'Email destination created.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  const deleteHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'delete'],
    mutationFn: (hookId: string) => apiFetch<void>(`/integrations/smtp/hooks/${hookId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setPendingDelete(null)
      setDeleteError(null)
      setSelectedHookId(null)
      setDraftState(newHookDraft)
      setHasUserEdited(false)
      setTestResult(null)
      setNotice({ tone: 'success', message: 'Email destination deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp'] })
      void queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
    onError: (error) => setDeleteError(resolveApiErrorMessage(error, 'Failed to delete email destination.')),
  })

  const testHook = useMutation({
    mutationKey: ['integrations', 'smtp', 'hooks', 'test'],
    mutationFn: (payload: SMTPHookTestRequest) =>
      apiFetch<SMTPTestResponse>('/integrations/smtp/hooks/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result, variables) => {
      setTestResult(result)
      setNotice({
        tone: result.success ? 'success' : 'error',
        message: result.success ? 'Email delivery test succeeded.' : result.error || 'Email delivery test failed.',
      })
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'smtp', 'hooks'], exact: true })
      if (variables.hook_id) {
        void queryClient.invalidateQueries({
          queryKey: ['integrations', 'smtp', 'hooks', variables.hook_id, 'test-runs'],
        })
      }
    },
  })

  const deliveriesQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks', selectedHookId, 'deliveries', deliveryPage],
    queryFn: () => apiFetch<SMTPDeliveryListResponse>(
      `/integrations/smtp/hooks/${selectedHookId}/deliveries?page=${deliveryPage}&page_size=10`,
    ),
    enabled: Boolean(selectedHookId),
    refetchInterval: selectedHookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const testRunsQuery = useQuery({
    queryKey: ['integrations', 'smtp', 'hooks', selectedHookId, 'test-runs', testRunPage],
    queryFn: () => apiFetch<SMTPTestRunListResponse>(
      `/integrations/smtp/hooks/${selectedHookId}/test-runs?page=${testRunPage}&page_size=10`,
    ),
    enabled: Boolean(selectedHookId),
    refetchInterval: selectedHookId ? DELIVERY_HISTORY_REFRESH_MS : false,
  })

  const replayDelivery = useMutation({
    mutationKey: ['integrations', 'smtp', 'deliveries', 'replay'],
    mutationFn: ({ hookId, deliveryId }: { hookId: string; deliveryId: string }) =>
      apiFetch<IntegrationDeliveryReplayResponse>(
        `/integrations/smtp/hooks/${hookId}/deliveries/${deliveryId}/replay`,
        { method: 'POST' },
      ),
    onSuccess: (result, variables) => {
      setPendingReplay(null)
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
    onError: () => setPendingReplay(null),
  })

  const resetTransientState = () => {
    setNotice(null)
    setTestResult(null)
    setSendTestEmail(false)
    setTestRecipient('')
    setPendingReplay(null)
    saveHook.reset()
    testHook.reset()
    replayDelivery.reset()
  }

  const onSelectHook = (hook: SMTPHook) => {
    if (hook.id === selectedHookId) return
    confirmDiscardUnsavedChanges(() => {
      setSelectedHookId(hook.id)
      setDraftState(createSMTPHookDraft(hook))
      setHasUserEdited(false)
      resetTransientState()
    })
  }

  const onCreateHook = () => {
    if (!canManageEmailDelivery) return
    confirmDiscardUnsavedChanges(() => {
      setSelectedHookId(null)
      setDraftState(newHookDraft)
      setHasUserEdited(false)
      resetTransientState()
    })
  }

  const onSave = () => {
    if (!canManageEmailDelivery) return
    if (firstValidationError) {
      setNotice({ tone: 'error', message: firstValidationError })
      return
    }
    setNotice(null)
    saveHook.mutate({ hookId: selectedHookId, hook: createSMTPHookRequest(draft) })
  }

  const onTest = () => {
    if (!canManageEmailDelivery) return
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
    draft,
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
