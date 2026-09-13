import { useDeferredValue, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
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
type Notice = { error: boolean; message: string }
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

export function useAiProviderConnections(enabled: boolean) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [page, setPage] = useState(0)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [pendingSelection, setPendingSelection] = useState<Selection | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AIProvider | null>(null)
  const [routingDraft, setRoutingDraft] = useState<AIProviderRouting | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [testResult, setTestResult] = useState<AITestConnectionResponse | null>(null)
  const [completedDeletes, setCompletedDeletes] = useState(0)
  const actionPending = useRef(false)

  const providers = useQuery({
    queryKey: ['ai', 'providers', deferredSearch, page],
    queryFn: ({ signal }) =>
      apiFetch<AIProviderPage>(
        `/ai/providers?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}&search=${encodeURIComponent(deferredSearch)}`,
        { signal },
      ),
    enabled,
  })
  const routing = useQuery({
    queryKey: ['ai', 'provider-routing'],
    queryFn: ({ signal }) => apiFetch<AIProviderRouting>('/ai/provider-routing', { signal }),
    enabled,
  })
  const selectedProvider = useQuery({
    queryKey: ['ai', 'providers', 'detail', editor?.baseline?.id],
    queryFn: ({ signal }) => apiFetch<AIProvider>(`/ai/providers/${editor!.baseline!.id}`, { signal }),
    enabled: enabled && Boolean(editor?.baseline),
  })
  const visibleRouting = routingDraft ?? routing.data
  const assignedIds = [
    ...new Set(ROUTING_FIELDS.flatMap(({ key }) => (visibleRouting?.[key] ? [visibleRouting[key]] : []))),
  ].sort()
  const assignedProviders = useQuery({
    queryKey: ['ai', 'provider-routing', 'names', assignedIds],
    queryFn: ({ signal }) =>
      Promise.all(assignedIds.map((id) => apiFetch<AIProvider>(`/ai/providers/${id}`, { signal }))),
    enabled: enabled && assignedIds.length > 0,
  })
  const editorDirty = Boolean(
    editor && JSON.stringify(editor.draft) !== JSON.stringify(createProviderDraft(editor.baseline ?? undefined)),
  )
  const validation = editor ? validateProviderDraft(editor.draft) : {}
  const dirty = editorDirty || routingDraft !== null

  useEffect(() => {
    if (providers.data && providers.data.total > 0 && page * PAGE_SIZE >= providers.data.total) {
      setPage(Math.max(0, Math.ceil(providers.data.total / PAGE_SIZE) - 1))
    }
  }, [page, providers.data])

  const mutation = useMutation({
    mutationKey: ['ai', 'providers', 'action'],
    mutationFn: performAction,
    onSuccess: (result) => {
      if (result.kind === 'save') {
        setEditor({
          baseline: result.provider,
          draft: createProviderDraft(result.provider),
          creationId: result.provider.id,
        })
        setTestResult(null)
        setNotice({
          error: false,
          message: `Provider “${result.provider.name}” saved. Feature assignments are saved separately.`,
        })
      } else if (result.kind === 'delete') {
        setEditor(null)
        setDeleteTarget(null)
        setTestResult(null)
        setNotice({ error: false, message: 'Provider deleted.' })
        setCompletedDeletes((count) => count + 1)
      } else if (result.kind === 'routing') {
        queryClient.setQueryData(['ai', 'provider-routing'], result.routing)
        setRoutingDraft(null)
        setNotice({ error: false, message: 'AI feature assignments saved.' })
      } else {
        setTestResult(result.result)
        setNotice({
          error: !result.result.success,
          message: result.result.success
            ? 'Saved provider connection succeeded.'
            : (result.result.error ?? 'Saved provider connection test failed.'),
        })
      }
      void queryClient.invalidateQueries({ queryKey: ['ai'] })
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
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
    if (actionPending.current) return
    actionPending.current = true
    setNotice(null)
    if (action.kind === 'test') setTestResult(null)
    mutation.mutate(action)
  }

  function applySelection(selection: Selection) {
    setPendingSelection(null)
    setNotice(null)
    setTestResult(null)
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
    if (actionPending.current || !visibleRouting || routing.isError) return
    // Keep the version that accompanied the first edit, including after background refresh.
    setRoutingDraft({ ...visibleRouting, [key]: id })
  }

  const knownProviders = [
    ...(providers.data?.items ?? []),
    ...(assignedProviders.data ?? []),
    ...(editor?.baseline ? [editor.baseline] : []),
  ]
  function providerName(id: string | null) {
    if (!id) return 'Legacy provider settings'
    return knownProviders.find((provider) => provider.id === id)?.name ?? `Provider ${id}`
  }

  return {
    providers,
    routing,
    assignedProviders,
    selectedProvider,
    editor,
    editorDirty,
    validation,
    dirty,
    notice,
    testResult,
    completedDeletes,
    pendingSelection,
    deleteTarget,
    visibleRouting,
    routingDirty: routingDraft !== null,
    busy: mutation.isPending,
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
      if (deleteTarget) run({ kind: 'delete', provider: deleteTarget })
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
