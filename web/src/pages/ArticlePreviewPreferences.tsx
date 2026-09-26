import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { useArticlePreviewPreferences } from '../hooks/useArticlePreviewPreferences'
import type { WorkspaceUserPreferenceResponse } from '../types/workspace'
import { workspaceQueryKeys } from '../workspace/workspaceApi'

type PreferenceState = ReturnType<typeof useArticlePreviewPreferences>

export function ArticlePreviewPreferences({
  onDirtyChange,
}: {
  onDirtyChange: (dirty: boolean) => void
}) {
  const state = useArticlePreviewPreferences()
  return (
    <PreviewPreferenceEditor
      key={state.userId}
      state={state}
      onDirtyChange={onDirtyChange}
    />
  )
}

function PreviewPreferenceEditor({
  state,
  onDirtyChange,
}: {
  state: PreferenceState
  onDirtyChange: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<{
    enabled: boolean
    revision: number
  } | null>(null)
  const [notice, setNotice] = useState('')
  const stored = state.preferences?.article_preview_external_resources === true
  const enabled = draft?.enabled ?? stored
  const dirty = draft !== null && draft.enabled !== stored
  const save = useMutation({
    mutationFn: (submitted: { enabled: boolean; revision: number }) =>
      apiFetch<WorkspaceUserPreferenceResponse>('/workspace/preferences', {
        method: 'PUT',
        body: JSON.stringify({
          expected_revision: submitted.revision,
          article_preview_external_resources: submitted.enabled,
        }),
      }),
    onSuccess: async (preferences) => {
      if (preferences.user_id !== state.userId) return
      // A background read started before this save must not restore older
      // consent after the user has disabled external requests.
      await queryClient.cancelQueries({
        queryKey: workspaceQueryKeys.preferences(state.userId),
        exact: true,
      })
      queryClient.setQueryData(
        workspaceQueryKeys.preferences(state.userId),
        preferences,
      )
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKeys.effective(state.userId),
      })
      setDraft(null)
      setNotice(
        preferences.article_preview_external_resources
          ? 'Original previews will load external resources by default.'
          : 'Original previews will block external resources by default.',
      )
    },
  })
  useEffect(() => {
    onDirtyChange(dirty)
    return () => onDirtyChange(false)
  }, [dirty, onDirtyChange])

  async function reload() {
    const result = await state.query.refetch()
    if (!result.error) {
      setDraft(null)
      setNotice('Article preview preference reloaded.')
      save.reset()
    }
  }

  return (
    <section
      className="tl-surface rounded-xl p-3.5"
      aria-labelledby="article-preview-preferences-heading"
    >
      <h2
        id="article-preview-preferences-heading"
        className="font-display text-lg"
      >
        Article previews
      </h2>
      <p
        id="article-preview-privacy-description"
        className="mt-2 text-sm text-slate dark:text-slate-300"
      >
        Images, styles, fonts, and media can contact publishers and third
        parties directly from your browser, sharing your IP address and viewing
        activity. This personal preference is off by default. Scripts remain
        blocked. You can change resource loading for each open preview.
      </p>
      {!state.canRead ? (
        <p className="mt-3 text-sm">
          Your permissions do not allow reading workspace preferences.
        </p>
      ) : (
        <>
          <fieldset
            disabled={!state.canWrite || !state.preferences || save.isPending}
            className="mt-3 space-y-3"
          >
            <label className="flex items-start gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                role="switch"
                className="mt-1"
                checked={enabled}
                aria-describedby="article-preview-privacy-description"
                onChange={(event) => {
                  const value = event.target.checked
                  setDraft({
                    enabled: value,
                    revision: draft?.revision ?? state.preferences!.revision,
                  })
                  setNotice('')
                  save.reset()
                }}
              />
              Always load external resources in original article previews
            </label>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50"
              disabled={!dirty}
              onClick={() => draft && save.mutate(draft)}
            >
              {save.isPending
                ? 'Saving preview preference...'
                : 'Save preview preference'}
            </button>
          </fieldset>
          {!state.preferences && !state.query.isError && (
            <p className="mt-2 text-sm">Loading preview preference...</p>
          )}
          {state.preferences && !state.canWrite && !state.query.isError && (
            <p className="mt-2 text-sm">
              Your permissions do not allow changing personal preferences.
            </p>
          )}
          {(state.query.error || save.error) && (
            <div
              role="alert"
              className="mt-3 text-sm text-red-700 dark:text-red-200"
            >
              <p>
                {resolveApiErrorMessage(
                  save.error ?? state.query.error,
                  'Article preview preference could not be saved or loaded.',
                )}
              </p>
              <button
                type="button"
                className="mt-2 rounded border border-current px-3 py-2 font-semibold disabled:opacity-50"
                disabled={save.isPending || state.query.isFetching}
                onClick={() => void reload()}
              >
                {draft
                  ? 'Discard change and reload preference'
                  : 'Retry loading preference'}
              </button>
            </div>
          )}
          {notice && (
            <p role="status" className="mt-3 text-sm">
              {notice}
            </p>
          )}
        </>
      )}
    </section>
  )
}
