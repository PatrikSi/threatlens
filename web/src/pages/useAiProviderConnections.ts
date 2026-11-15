import { useAiQuotaGroups } from './useAiQuotaGroups'
import { useDeferredValue, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { captureSessionLease } from '../api/sessionLifecycle'
import type { AIProvider, AIProviderPage, AIProviderRouting, AITestConnectionResponse } from '../types/ai'
import {
  createProviderDraft,
  createProviderRequest,
  createProviderRequestId,
  ROUTING_FIELDS,
  validateProviderDraft,
  type ProviderDraft,
  type RoutingField,
} from './aiProviderDraft'

const PAGE_SIZE = 25
type Editor = { baseline: AIProvider | null; draft: ProviderDraft; creationId: string }
type Notice = { error: boolean; message: string; current?: () => boolean }
type DeleteIntent = { provider: AIProvider; providerGeneration: number; routingGeneration: number }
type Selection = AIProvider | 'new' | 'reload'
type Action =
  | { kind: 'save'; editor: Editor }
  | { kind: 'delete'; provider: AIProvider }
  | { kind: 'test'; provider: AIProvider }
  | { kind: 'routing'; routing: AIProviderRouting }

async function performAction(action: Action) {
  const session = captureSessionLease()
  if (action.kind === 'save') {
    const { baseline, draft, creationId } = action.editor
    const provider = await apiFetch<AIProvider>(baseline ? `/ai/providers/${baseline.id}` : '/ai/providers', {
      method: baseline ? 'PUT' : 'POST',
      body: JSON.stringify({
        ...createProviderRequest(draft),
        ...(baseline ? { version: baseline.version } : { id: creationId }),
      }),
    })
    session.assertCurrent()
    return { kind: 'save' as const, provider }
  }
  if (action.kind === 'delete') {
    await apiFetch<void>(`/ai/providers/${action.provider.id}?version=${action.provider.version}`, { method: 'DELETE' })
    session.assertCurrent()
    return { kind: 'delete' as const }
  }
  if (action.kind === 'test') {
    const result = await apiFetch<AITestConnectionResponse>(`/ai/providers/${action.provider.id}/test-connection`, {
      method: 'POST',
      body: JSON.stringify({ version: action.provider.version }),
      timeoutMs: 45000,
    })
    session.assertCurrent()
    return { kind: 'test' as const, result }
  }
  const routing = await apiFetch<AIProviderRouting>('/ai/provider-routing', {
    method: 'PUT',
    body: JSON.stringify(action.routing),
  })
  session.assertCurrent()
  return { kind: 'routing' as const, routing }
}

function providerEditorHidden(editor: Editor | null, denied: boolean, generation: number, current: AIProvider | undefined) {
  return Boolean(editor?.baseline && (denied || generation > 0 && !current))
}

export function useAiProviderConnections(enabled: boolean) {
  const queryClient = useQueryClient()
  const quotas = useAiQuotaGroups(enabled)
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [page, setPage] = useState(0)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [awaitingProvider, setAwaitingProvider] = useState<string | null>(null)
  const providerId = awaitingProvider ?? editor?.baseline?.id
  const providersAccess = useQueryAccessScope(['ai', 'providers', 'list'])
  const providerAccess = useQueryAccessScope(['ai', 'providers', 'detail', providerId ?? 'new'])
  const routingAccess = useQueryAccessScope(['ai', 'provider-routing'])
  const namesAccess = useQueryAccessScope(['ai', 'provider-routing', 'names'])
  const [pendingSelection, setPendingSelection] = useState<Selection | null>(null)
  const [deleteIntent, setDeleteIntent] = useState<DeleteIntent | null>(null)
  const [routingDraft, setRoutingDraft] = useState<AIProviderRouting | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [testResult, setTestResult] = useState<{ value: AITestConnectionResponse; current: () => boolean } | null>(null)
  const [completedDeletes, setCompletedDeletes] = useState(0)
  const actionPending = useRef(false)

  const providers = useQuery({
    queryKey: ['ai', 'providers', deferredSearch, page],
    ...providersAccess.queryOptions(({ signal }) =>
      apiFetch<AIProviderPage>(
        `/ai/providers?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}&search=${encodeURIComponent(deferredSearch)}`,
        { signal },
      )),
    enabled,
  })
  const routing = useQuery({
    queryKey: ['ai', 'provider-routing'],
    ...routingAccess.queryOptions(({ signal }) => apiFetch<AIProviderRouting>('/ai/provider-routing', { signal })),
    enabled,
  })
  const selectedProvider = useQuery({
    queryKey: ['ai', 'providers', 'detail', providerId],
    ...providerAccess.queryOptions(({ signal }) => apiFetch<AIProvider>(`/ai/providers/${providerId}`, { signal })),
    enabled: enabled && Boolean(providerId),
  })
  const routingHidden = routingAccess.denied || routingAccess.generation > 0 && !routing.data
  const visibleRouting = routingHidden ? undefined : routingDraft ?? routing.data
  const editorHidden = providerEditorHidden(editor, providerAccess.denied, providerAccess.generation, selectedProvider.data)
  const visibleEditor = editorHidden ? null : editor
  const assignedIds = [
    ...new Set(ROUTING_FIELDS.flatMap(({ key }) => (visibleRouting?.[key] ? [visibleRouting[key]] : []))),
  ].sort()
  const assignedProviders = useQuery({
    queryKey: ['ai', 'provider-routing', 'names', assignedIds, routingAccess.generation],
    ...namesAccess.queryOptions(({ signal }) =>
      Promise.all(assignedIds.map((id) => apiFetch<AIProvider>(`/ai/providers/${id}`, { signal })))),
    enabled: enabled && assignedIds.length > 0,
  })
  const editorDirty = Boolean(
    editor && JSON.stringify(editor.draft) !== JSON.stringify(createProviderDraft(editor.baseline ?? undefined)),
  )
  const validation = editor ? validateProviderDraft(editor.draft) : {}
  const dirty = editorDirty || routingDraft !== null || quotas.dirty

  useEffect(() => {
    if (providers.data && providers.data.total > 0 && page * PAGE_SIZE >= providers.data.total) {
      setPage(Math.max(0, Math.ceil(providers.data.total / PAGE_SIZE) - 1))
    }
  }, [page, providers.data])

  useEffect(() => {
    if (!awaitingProvider || selectedProvider.data?.id !== awaitingProvider) return
    const latest = selectedProvider.data
    setEditor({ baseline: latest, draft: createProviderDraft(latest), creationId: latest.id })
    setAwaitingProvider(null)
  }, [awaitingProvider, selectedProvider.data])

  function providerReadUnavailable() {
    if (!providerId) return false
    const key = ['ai', 'providers', 'detail', providerId]
    return queryClient.getQueryState(key)?.status === 'error'
      || !providerAccess.currentData(providerAccess.captureResult()(true))
      || providerAccess.generation > 0 && !providerAccess.currentData<AIProvider>(queryClient.getQueryData(key))
  }
  function actionUnavailable(action: Action) {
    if (!enabled) return true
    if (action.kind === 'routing') return queryClient.getQueryState(['ai', 'provider-routing'])?.status === 'error'
      || !routingAccess.currentData<AIProviderRouting>(queryClient.getQueryData(['ai', 'provider-routing']))
    if (action.kind === 'save' && (providers.isError || providersAccess.denied)) return true
    if ((action.kind !== 'save' || action.editor.baseline) && providerReadUnavailable()) return true
    if ((action.kind === 'delete' || action.kind === 'test') && action.provider.id !== providerId) return true
    const savedRouting = routing.data
    return action.kind === 'delete' && (routing.isError || routingAccess.denied || !savedRouting
      || ROUTING_FIELDS.some(({ key }) => savedRouting[key] === action.provider.id))
  }
  function currentDeleteTarget() {
    if (!deleteIntent || deleteIntent.provider.id !== providerId) return null
    return providerAccess.currentData({ generation: deleteIntent.providerGeneration, value: true })
      && routingAccess.currentData({ generation: deleteIntent.routingGeneration, value: true })
      ? deleteIntent.provider : null
  }
  function setDeleteTarget(provider: AIProvider | null) {
    if (!provider) { setDeleteIntent(null); return }
    if (actionUnavailable({ kind: 'delete', provider })) return
    setDeleteIntent({
      provider,
      providerGeneration: providerAccess.captureResult()(true).generation,
      routingGeneration: routingAccess.captureResult()(true).generation,
    })
  }
  function assertDeleteIntent(provider: AIProvider) {
    const target = currentDeleteTarget()
    if (!target || target.id !== provider.id || target.version !== provider.version) throw new Error('Review the current provider deletion again.')
  }

  const mutation = useMutation({
    mutationKey: ['ai', 'providers', 'action'],
    mutationFn: async (action: Action) => {
      if (actionUnavailable(action)) throw new Error('Refresh the current provider configuration before continuing. Your draft is preserved.')
      if (action.kind === 'delete') assertDeleteIntent(action.provider)
      const scope = action.kind === 'routing' ? routingAccess : action.kind === 'save' && !action.editor.baseline ? providersAccess : providerAccess
      const admission = scope.captureResult()(true)
      const routingCapture = routingAccess.captureResult()
      const result = await performAction(action)
      return { result, current: () => Boolean(scope.currentData(admission)), routingCapture }
    },
    onSuccess: ({ result, current, routingCapture }, action) => {
      void queryClient.invalidateQueries({ queryKey: ['ai'] })
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
      if (!current()) return
      if (result.kind === 'save') {
        if (action.kind === 'save' && !action.editor.baseline) setAwaitingProvider(result.provider.id)
        else setEditor({
          baseline: result.provider,
          draft: createProviderDraft(result.provider),
          creationId: result.provider.id,
        })
        setTestResult(null)
        setNotice({
          error: false,
          message: action.kind === 'save' && !action.editor.baseline ? 'Provider saved. Loading its current configuration; feature assignments are saved separately.' : `Provider “${result.provider.name}” saved. Feature assignments are saved separately.`,
          current,
        })
      } else if (result.kind === 'delete') {
        setEditor(null)
        setDeleteTarget(null)
        setTestResult(null)
        setNotice({ error: false, message: 'Provider deleted.' })
        setCompletedDeletes((count) => count + 1)
      } else if (result.kind === 'routing') {
        if (queryClient.getQueryState(['ai', 'provider-routing'])?.status !== 'error') queryClient.setQueryData(['ai', 'provider-routing'], routingCapture(result.routing))
        setRoutingDraft(null)
        setNotice({ error: false, message: 'AI feature assignments saved.' })
      } else {
        setTestResult({ value: result.result, current })
        setNotice({
          error: !result.result.success,
          message: result.result.success
            ? 'Saved provider connection succeeded.'
            : (result.result.error ?? 'Saved provider connection test failed.'),
          current,
        })
      }
    },
    onError: (error, action) => {
      setNotice({
        error: true,
        message: resolveApiErrorMessage(
          error,
          `Could not ${action.kind === 'routing' ? 'save feature assignments' : action.kind === 'test' ? 'test the saved connection' : `${action.kind} the provider`}. Your draft has been kept.`,
        ),
      })
      // A lost response may follow a successful write. Refresh visible server state,
      // retaining the draft's original version until the operator deliberately reloads it.
      void queryClient.invalidateQueries({ queryKey: ['ai', 'providers'] })
      void queryClient.invalidateQueries({ queryKey: ['ai', 'provider-routing'] })
    },
    onSettled: () => {
      actionPending.current = false
    },
  })

  function run(action: Action) {
    if (actionPending.current || actionUnavailable(action)) return
    actionPending.current = true
    setNotice(null)
    if (action.kind === 'test') setTestResult(null)
    mutation.mutate(action)
  }

  function applySelection(selection: Selection) {
    setPendingSelection(null)
    setAwaitingProvider(null)
    setNotice(null)
    setTestResult(null)
    setDeleteIntent(null)
    if (selection === 'reload') {
      if (!editor?.baseline) return
      const latest =
        selectedProvider.data ??
        providers.data?.items.find(({ id }) => id === editor.baseline?.id) ??
        assignedProviders.data?.find(({ id }) => id === editor.baseline?.id)
      if (!latest) {
        setNotice({
          error: true,
          message: 'The latest provider is not in this page. Find and select it to reload its saved configuration.',
        })
        return
      }
      setEditor({ baseline: latest, draft: createProviderDraft(latest), creationId: latest.id })
    } else if (selection === 'new') {
      try {
        setEditor({ baseline: null, draft: createProviderDraft(), creationId: createProviderRequestId() })
      } catch (error) {
        setNotice({ error: true, message: resolveApiErrorMessage(error, 'The new provider could not be prepared. No request was sent.') })
      }
    } else {
      setEditor({ baseline: selection, draft: createProviderDraft(selection), creationId: selection.id })
    }
  }

  function select(selection: Selection) {
    if (actionPending.current) return
    if (editorDirty) setPendingSelection(selection)
    else applySelection(selection)
  }

  function updateDraft<K extends keyof ProviderDraft>(key: K, value: ProviderDraft[K]) {
    if (actionPending.current) return
    setEditor((current) => (current ? { ...current, draft: { ...current.draft, [key]: value } } : current))
    setTestResult(null)
  }

  function assign(key: RoutingField, id: string | null) {
    if (actionPending.current || !visibleRouting || routing.isError || routingAccess.denied) return
    // Keep the version that accompanied the first edit, including after background refresh.
    setRoutingDraft({ ...visibleRouting, [key]: id })
  }

  const knownProviders = [
    ...(providers.data?.items ?? []),
    ...(assignedProviders.data ?? []),
    ...(visibleEditor?.baseline ? [visibleEditor.baseline] : []),
  ]
  function providerName(id: string | null | undefined) {
    if (!id) return 'Legacy provider settings'
    return knownProviders.find((provider) => provider.id === id)?.name ?? `Provider ${id}`
  }

  return {
    quotas,
    providers,
    routing,
    assignedProviders: routingHidden ? { ...assignedProviders, data: undefined } : assignedProviders,
    selectedProvider,
    editor: visibleEditor,
    editorReadUnavailable: providerReadUnavailable(),
    editorDirty,
    validation,
    dirty,
    notice: notice && (!notice.current || notice.current()) ? notice : null,
    testResult: testResult?.current() && !editorHidden ? testResult.value : null,
    completedDeletes,
    pendingSelection,
    deleteTarget: editorHidden || routingHidden ? null : currentDeleteTarget(),
    visibleRouting,
    routingDirty: routingDraft !== null,
    busy: mutation.isPending || quotas.busy,
    search,
    page,
    providerName,
    select,
    updateDraft,
    assign,
    setSearch: (value: string) => {
      setSearch(value)
      setPage(0)
    },
    setPage,
    setDeleteTarget,
    confirmSelection: () => {
      if (pendingSelection && !actionPending.current) applySelection(pendingSelection)
    },
    cancelSelection: () => setPendingSelection(null),
    reloadRouting: () => {
      if (!actionPending.current) setRoutingDraft(null)
    },
    save: () => {
      if (editor && !Object.keys(validation).length && !providers.isError) run({ kind: 'save', editor })
    },
    remove: () => {
      const target = currentDeleteTarget()
      if (target) run({ kind: 'delete', provider: target })
    },
    test: () => {
      if (editor?.baseline && !editorDirty) run({ kind: 'test', provider: editor.baseline })
    },
    saveRouting: () => {
      if (routingDraft && !routing.isError) run({ kind: 'routing', routing: routingDraft })
    },
  }
}

export type AiProviderConnectionsController = ReturnType<typeof useAiProviderConnections>
