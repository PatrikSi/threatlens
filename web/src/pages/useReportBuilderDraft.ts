import { useCallback, useEffect, useRef, useState } from 'react'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { ReportDeliveryMode, ReportPromptConfig, ReportSectionConfig, ReportTemplate } from '../types/api'
import type { ExportFilterDraft } from './exportPageModel'
import { reportBuilderFromTemplate } from './reportingPageModel'

export function useReportBuilderDraft(templates: ReportTemplate[] | undefined, canAuthor: boolean, editorialDirty = false) {
  const [initial] = useState(() => reportBuilderFromTemplate(undefined))
  const [selectedTemplateId, setSelectedTemplateId] = useState('')
  const selectedTemplateIdRef = useRef(selectedTemplateId)
  selectedTemplateIdRef.current = selectedTemplateId
  const [loadedTemplate, setLoadedTemplate] = useState<ReportTemplate>()
  const [filterDraft, setFilterDraft] = useState<ExportFilterDraft>(initial.filterDraft)
  const [prompt, setPrompt] = useState<ReportPromptConfig>(initial.prompt)
  const [sections, setSections] = useState<ReportSectionConfig[]>(initial.sections)
  const [excludedItemIds, setExcludedItemIds] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [deliverWhenReady, setDeliverWhenReady] = useState(false)
  const [deliveryMode, setDeliveryMode] = useState<ReportDeliveryMode>('summary')
  const fingerprint = JSON.stringify({ selectedTemplateId, filterDraft, prompt, sections, excludedItemIds, title, deliverWhenReady, deliveryMode })
  const [baseline, setBaseline] = useState(fingerprint)
  const fingerprintRef = useRef(fingerprint)
  fingerprintRef.current = fingerprint
  const dirty = fingerprint !== baseline
  const confirmDiscard = useUnsavedChangesWarning((canAuthor && dirty) || editorialDirty, 'Discard unsaved report changes or review notes?', { ignoreSearchChanges: true })
  const initializedRef = useRef(false)

  const hydrate = useCallback((template: ReportTemplate) => {
    const state = reportBuilderFromTemplate(template)
    setSelectedTemplateId(template.id)
    setLoadedTemplate(structuredClone(template))
    setFilterDraft(state.filterDraft)
    setPrompt(state.prompt)
    setSections(state.sections)
    setExcludedItemIds([])
    setTitle('')
    setDeliverWhenReady(false)
    setDeliveryMode('summary')
    setBaseline(JSON.stringify({ selectedTemplateId: template.id, filterDraft: state.filterDraft, prompt: state.prompt, sections: state.sections, excludedItemIds: [], title: '', deliverWhenReady: false, deliveryMode: 'summary' }))
  }, [])

  useEffect(() => {
    if (initializedRef.current || !templates) return
    initializedRef.current = true
    if (!dirty && templates[0]) hydrate(templates[0])
    // Template cache refreshes must never hydrate an existing draft.
  }, [templates, dirty, hydrate])

  const latestTemplate = templates?.find((template) => template.id === selectedTemplateId)
  const templateRevisionChanged = Boolean(loadedTemplate && latestTemplate &&
    (loadedTemplate.resource_version ?? loadedTemplate.updated_at) !== (latestTemplate.resource_version ?? latestTemplate.updated_at))
  const templateUnavailable = Boolean(loadedTemplate && templates && !latestTemplate)

  return {
    selectedTemplateId, selectedTemplate: loadedTemplate, filterDraft, setFilterDraft, prompt, setPrompt,
    sections, setSections, excludedItemIds, setExcludedItemIds, title, setTitle, deliverWhenReady, setDeliverWhenReady,
    deliveryMode, setDeliveryMode, dirty, fingerprint, discardDialog: confirmDiscard.discardDialog,
    templateRevisionChanged, templateUnavailable, confirmDiscard,
    setSelectedTemplateId: (id: string) => {
      const template = templates?.find((entry) => entry.id === id)
      if (template && id !== selectedTemplateId) confirmDiscard(() => hydrate(template))
    },
    reloadTemplate: () => { if (latestTemplate) confirmDiscard(() => hydrate(latestTemplate)) },
    adoptSavedTemplate: (template: ReportTemplate, submittedTemplateId: string | undefined) => {
      if (submittedTemplateId !== selectedTemplateIdRef.current) return
      setSelectedTemplateId(template.id)
      setLoadedTemplate(structuredClone(template))
    },
    selectClonedTemplate: (template: ReportTemplate) => confirmDiscard(() => hydrate(template)),
    acceptSubmission: (submitted: string | undefined) => {
      if (submitted !== fingerprintRef.current) return false
      setBaseline(submitted)
      return true
    },
  }
}
