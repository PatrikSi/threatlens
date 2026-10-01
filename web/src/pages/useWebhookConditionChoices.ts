import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import type { AlertInterest, Feed, Tag } from '../types/api'
import type { TeamPage } from '../types/teams'
import type { WebhookConditionField } from '../types/webhookAutomation'

export interface WebhookConditionChoice { value: string; label: string }
const labels = (values: string[]): WebhookConditionChoice[] => values.map((value) => ({ value, label: value.replaceAll('_', ' ') }))
const ENUM_CHOICES: Partial<Record<WebhookConditionField, WebhookConditionChoice[]>> = {
  ioc_type: labels(['domain', 'url', 'email', 'ipv4', 'ipv6', 'hash_md5', 'hash_sha1', 'hash_sha256', 'cve']),
  ioc_role: labels(['malicious_infrastructure', 'benign', 'reference', 'unknown']),
  analyst_verdict: labels(['malicious', 'benign', 'reference', 'example', 'retracted', 'unreviewed']),
  hunt_review_status: labels(['accepted', 'rejected', 'suggested']),
  ai_relevance_label: labels(['high', 'medium', 'low']),
}
const REMOTE_FIELDS = new Set(['feed_id', 'tag', 'tag_id', 'alert_rule_id', 'team_id'])

async function fetchChoices(field: WebhookConditionField, page: number, signal: AbortSignal): Promise<{ choices: WebhookConditionChoice[]; total?: number }> {
  if (field === 'team_id') {
    const result = await apiFetch<TeamPage>(`/teams?page=${page}&page_size=100`, { signal })
    return { choices: (result.items ?? []).map((team) => ({ value: team.id, label: `${team.name}${team.active ? '' : ' (inactive)'}` })), total: result.total }
  }
  if (field === 'alert_rule_id') {
    const rules = await apiFetch<AlertInterest[]>('/alerts?include_disabled=true', { signal })
    return { choices: rules.map((rule) => ({ value: rule.id, label: `${rule.name}${rule.enabled ? '' : ' (disabled)'}` })) }
  }
  if (field === 'feed_id') {
    const feeds = await apiFetch<Feed[]>('/feeds', { signal })
    return { choices: feeds.map((feed) => ({ value: feed.id, label: feed.name })) }
  }
  const tags = await apiFetch<Tag[]>('/tags', { signal })
  return { choices: tags.map((tag) => ({ value: field === 'tag' ? tag.name : tag.id, label: tag.name })) }
}

export function useWebhookConditionChoices(field: WebhookConditionField) {
  const [page, setPage] = useState(1)
  const remote = REMOTE_FIELDS.has(field)
  const query = useQuery({
    queryKey: ['notifications', 'condition-choices', field, page],
    queryFn: ({ signal }) => fetchChoices(field, page, signal),
    enabled: remote,
    retry: false,
    staleTime: 60_000,
  })
  // A failed permission check must not expose cached names while the draft keeps its identifiers.
  const data = query.isError ? undefined : query.data
  return {
    choices: remote ? data?.choices ?? [] : ENUM_CHOICES[field] ?? [],
    isLoading: remote && query.isLoading,
    error: remote ? query.error : null,
    refetch: query.refetch,
    page, setPage,
    total: data?.total,
    remote,
  }
}
