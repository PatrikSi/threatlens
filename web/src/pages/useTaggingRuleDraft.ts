import { useRef, useState, type SetStateAction } from 'react'
import { rebaseSavedDraft } from '../hooks/rebaseSavedDraft'
import type { TaggingRule } from '../types/api'
import { createDefaultRuleDraft, createDraftFromRule, type TaggingRuleDraft } from './taggingSettingsModel'

export type TaggingRuleSubmission = {
  ruleId: string | null
  revision: number
  draft: TaggingRuleDraft
}

type RuleEditorState = {
  baseline: TaggingRule | null
  revision: number
  draft: TaggingRuleDraft
}

/** Selection, baseline and draft travel together across asynchronous completions. */
export function useTaggingRuleDraft() {
  const [state, setState] = useState<RuleEditorState>(() => ({
    baseline: null,
    revision: 0,
    draft: createDefaultRuleDraft(),
  }))
  const stateRef = useRef(state)
  stateRef.current = state

  const isSelectedSubmission = (submission: TaggingRuleSubmission) =>
    stateRef.current.revision === submission.revision
  const isCurrentSubmission = (submission: TaggingRuleSubmission) =>
    isSelectedSubmission(submission) && JSON.stringify(stateRef.current.draft) === JSON.stringify(submission.draft)

  return {
    selectedRule: state.baseline,
    selectedRuleId: state.baseline?.id ?? null,
    ruleDraft: state.draft,
    baselineRuleDraft: state.baseline ? createDraftFromRule(state.baseline) : createDefaultRuleDraft(),
    setRuleDraft: (update: SetStateAction<TaggingRuleDraft>) => setState((current) => ({
      ...current,
      draft: typeof update === 'function' ? update(current.draft) : update,
    })),
    replaceRuleDraft: (rule: TaggingRule | null) => setState((current) => ({
      baseline: rule,
      revision: current.revision + 1,
      draft: rule ? createDraftFromRule(rule) : createDefaultRuleDraft(),
    })),
    captureSubmission: (): TaggingRuleSubmission => ({
      ruleId: state.baseline?.id ?? null,
      revision: state.revision,
      draft: state.draft,
    }),
    isSelectedSubmission,
    isCurrentSubmission,
    acceptSavedRule: (saved: TaggingRule, submission: TaggingRuleSubmission) => {
      setState((current) => current.revision === submission.revision ? {
        ...current,
        baseline: saved,
        draft: rebaseSavedDraft(submission.draft, current.draft, createDraftFromRule(saved)),
      } : current)
    },
  }
}
