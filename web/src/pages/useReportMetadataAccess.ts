import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type { ReportCapabilities, ReportSchedule, ReportTemplate } from '../types/api'

export function useReportMetadataAccess(isAdmin: boolean, canAuthor: boolean) {
  const client = useQueryClient()
  const capabilitiesAccess = useQueryAccessScope(['reports', 'capabilities'])
  const templatesAccess = useQueryAccessScope(['reports', 'templates'])
  const schedulesAccess = useQueryAccessScope(['reports', 'schedules'])
  const capabilitiesRead = capabilitiesAccess.queryOptions(({ signal }) => apiFetch<ReportCapabilities>('/reports/capabilities', { signal }), 5 * 60_000)
  const templatesRead = templatesAccess.queryOptions(({ signal }) => apiFetch<ReportTemplate[]>('/reports/templates', { signal }), 60_000)
  const schedulesRead = schedulesAccess.queryOptions(({ signal }) => apiFetch<ReportSchedule[]>('/reports/schedules', { signal }), 30_000)
  const capabilitiesQuery = useQuery({ queryKey: ['reports', 'capabilities'], ...capabilitiesRead })
  const templatesQuery = useQuery({ queryKey: ['reports', 'templates'], ...templatesRead })
  const schedulesQuery = useQuery({ queryKey: ['reports', 'schedules'], ...schedulesRead, enabled: isAdmin })

  function currentCapabilities() {
    const state = client.getQueryState<Awaited<ReturnType<typeof capabilitiesRead.queryFn>>>(['reports', 'capabilities'])
    return state?.status === 'success' ? capabilitiesAccess.currentData(state.data) : undefined
  }
  function currentTemplates() {
    const state = client.getQueryState<Awaited<ReturnType<typeof templatesRead.queryFn>>>(['reports', 'templates'])
    return state?.status === 'success' ? templatesAccess.currentData(state.data) : undefined
  }
  function currentSchedules() {
    const state = client.getQueryState<Awaited<ReturnType<typeof schedulesRead.queryFn>>>(['reports', 'schedules'])
    return state?.status === 'success' ? schedulesAccess.currentData(state.data) : undefined
  }
  function templateActionCurrent(generation: number) {
    return canAuthor && Boolean(templatesAccess.currentData({ generation, value: true })) && currentTemplates() !== undefined
  }
  function scheduleActionCurrent(generation: number) {
    return canAuthor && isAdmin && Boolean(schedulesAccess.currentData({ generation, value: true })) && currentSchedules() !== undefined
  }
  function assertTemplate(id?: string) {
    const templates = currentTemplates()
    if (!canAuthor || !templates || id && !templates.some((template) => template.id === id)) throw new Error('Reload current template access before using saved report metadata.')
    return templates.find((template) => template.id === id)
  }
  function assertSchedule(id?: string) {
    const schedules = currentSchedules()
    if (!canAuthor || !isAdmin || !schedules || id && !schedules.some((schedule) => schedule.id === id)) throw new Error('Reload current schedule access before changing saved report schedules.')
    return schedules.find((schedule) => schedule.id === id)
  }
  function storeTemplate(template: ReportTemplate, generation: number) {
    if (!templateActionCurrent(generation)) return false
    client.setQueryData(['reports', 'templates'], {
      generation, value: upsert(currentTemplates()!, template),
    })
    return true
  }
  function storeSchedule(schedule: ReportSchedule, generation: number) {
    if (!scheduleActionCurrent(generation)) return false
    client.setQueryData(['reports', 'schedules'], {
      generation, value: upsert(currentSchedules()!, schedule),
    })
    return true
  }
  return {
    capabilitiesQuery, templatesQuery, schedulesQuery, templatesAccess, schedulesAccess,
    currentCapabilities, currentTemplates, currentSchedules, templateActionCurrent, scheduleActionCurrent,
    assertTemplate, assertSchedule, storeTemplate, storeSchedule,
    captureTemplateGeneration: () => templatesAccess.captureResult()(true).generation,
    captureScheduleGeneration: () => schedulesAccess.captureResult()(true).generation,
    templateGeneration: templatesAccess.generation, scheduleGeneration: schedulesAccess.generation,
    templateActionsAvailable: canAuthor && templatesQuery.isSuccess && !templatesQuery.isFetching && templatesQuery.data !== undefined,
    scheduleActionsAvailable: canAuthor && isAdmin && schedulesQuery.isSuccess && !schedulesQuery.isFetching && schedulesQuery.data !== undefined,
  }
}

function upsert<T extends { id: string }>(current: T[], saved: T) {
  return current.some((value) => value.id === saved.id)
    ? current.map((value) => value.id === saved.id ? saved : value) : [...current, saved]
}
