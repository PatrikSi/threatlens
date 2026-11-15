import { useCallback, useEffect, useRef, useState } from 'react'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { ReportDeliveryMode, ReportPromptConfig, ReportSectionConfig, ReportTemplate } from '../types/api'
import type { ExportFilterDraft } from './exportPageModel'
import { reportBuilderFromTemplate } from './reportingPageModel'

type TemplateSaveDraft = {
  mode: 'create' | 'update'
  name: string
  visibility: 'private' | 'shared'
  initialName: string
  initialVisibility: 'private' | 'shared'
  readGeneration: number
}

function templateSaveDraftDirty(draft: TemplateSaveDraft | null) {
  return Boolean(draft && (draft.name !== draft.initialName || draft.visibility !== draft.initialVisibility))
}

export function useReportBuilderDraft(templates: ReportTemplate[] | undefined, editorialDirty = false, readGeneration = 0) {
  const [initial] = useState(() => reportBuilderFromTemplate(undefined))
  const [selectedTemplateId, setSelectedTemplateId] = useState('')
  const selectedTemplateIdRef = useRef(selectedTemplateId)
  selectedTemplateIdRef.current = selectedTemplateId
  const [loadedTemplate, setLoadedTemplate] = useState<ReportTemplate>()
  const [loadedTemplateGeneration, setLoadedTemplateGeneration] = useState(readGeneration)
  const [filterDraft, setFilterDraft] = useState<ExportFilterDraft>(initial.filterDraft)
  const [prompt, setPrompt] = useState<ReportPromptConfig>(initial.prompt)
  const [sections, setSections] = useState<ReportSectionConfig[]>(initial.sections)
  const [excludedItemIds, setExcludedItemIds] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [deliverWhenReady, setDeliverWhenReady] = useState(false)
  const [deliveryMode, setDeliveryMode] = useState<ReportDeliveryMode>('summary')
  const [templateSaveDraft, setTemplateSaveDraft] = useState<TemplateSaveDraft | null>(null)
  const fingerprint = JSON.stringify({ selectedTemplateId, filterDraft, prompt, sections, excludedItemIds, title, deliverWhenReady, deliveryMode })
  const [baseline, setBaseline] = useState(fingerprint)
  const fingerprintRef = useRef(fingerprint)
  fingerprintRef.current = fingerprint
  const dirty = fingerprint !== baseline || templateSaveDraftDirty(templateSaveDraft)
  const confirmDiscard = useUnsavedChangesWarning(dirty || editorialDirty, 'Discard unsaved report changes or review notes?', { ignoreSearchChanges: true })
  const initializedRef = useRef(false)
  useEffect(() => {
    setTemplateSaveDraft((current) => current && current.readGeneration !== readGeneration
      && !templateSaveDraftDirty(current) ? null : current)
  }, [readGeneration])

  const hydrate = useCallback((template: ReportTemplate, generation = readGeneration) => {
    const state = reportBuilderFromTemplate(template)
    setSelectedTemplateId(template.id)
    setLoadedTemplate(structuredClone(template))
    setLoadedTemplateGeneration(generation)
    setFilterDraft(state.filterDraft)
    setPrompt(state.prompt)
    setSections(state.sections)
    setExcludedItemIds([])
    setTitle('')
    setDeliverWhenReady(false)
    setDeliveryMode('summary')
    setTemplateSaveDraft(null)
    setBaseline(JSON.stringify({ selectedTemplateId: template.id, filterDraft: state.filterDraft, prompt: state.prompt, sections: state.sections, excludedItemIds: [], title: '', deliverWhenReady: false, deliveryMode: 'summary' }))
  }, [readGeneration])

  useEffect(() => {
    if (initializedRef.current || !templates) return
    initializedRef.current = true
    if (!dirty && templates[0]) hydrate(templates[0])
    // Template cache refreshes must never hydrate an existing draft.
  }, [templates, dirty, hydrate])

  const latestTemplate = templates?.find((template) => template.id === selectedTemplateId)
  const templateBaselineRetired = Boolean(loadedTemplate && loadedTemplateGeneration !== readGeneration)
  useEffect(() => {
    if (templateBaselineRetired && !dirty && latestTemplate) hydrate(latestTemplate)
  }, [dirty, hydrate, latestTemplate, templateBaselineRetired])
  const templateRevisionChanged = Boolean(loadedTemplate && latestTemplate &&
    (loadedTemplate.resource_version ?? loadedTemplate.updated_at) !== (latestTemplate.resource_version ?? latestTemplate.updated_at))
  const templateUnavailable = Boolean(loadedTemplate && templates && !latestTemplate)

  return {
    selectedTemplateId, selectedTemplate: loadedTemplate, filterDraft, setFilterDraft, prompt, setPrompt,
    sections, setSections, excludedItemIds, setExcludedItemIds, title, setTitle, deliverWhenReady, setDeliverWhenReady,
    deliveryMode, setDeliveryMode, dirty, fingerprint, discardDialog: confirmDiscard.discardDialog,
    templateRevisionChanged, templateUnavailable, confirmDiscard,
    templateBaselineRetired, loadedTemplateGeneration,
    templateSaveDraft, setTemplateSaveDraft,
    setSelectedTemplateId: (id: string) => {
      const template = templates?.find((entry) => entry.id === id)
      if (template && id !== selectedTemplateId) confirmDiscard(() => hydrate(template))
    },
    reloadTemplate: () => { if (latestTemplate) confirmDiscard(() => hydrate(latestTemplate)) },
    adoptSavedTemplate: (template: ReportTemplate, submittedTemplateId: string | undefined, generation = readGeneration) => {
      if (submittedTemplateId !== selectedTemplateIdRef.current) return
      setSelectedTemplateId(template.id)
      setLoadedTemplate(structuredClone(template))
      setLoadedTemplateGeneration(generation)
    },
    selectClonedTemplate: (template: ReportTemplate, generation = readGeneration) => confirmDiscard(() => hydrate(template, generation)),
    acceptSubmission: (submitted: string | undefined) => {
      if (submitted !== fingerprintRef.current) return false
      setBaseline(submitted)
      return true
    },
  }
}
