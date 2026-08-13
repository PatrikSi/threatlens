import type { ItemListEntry } from './items'

export interface AlertInterest {
  id: string
  user_id: string
  name: string
  category: string
  keywords: string[]
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface AlertMatchReference {
  alert_id: string
  alert_name: string
  category: string
  matched_keywords: string[]
}

export interface AlertMatchEntry extends ItemListEntry {
  matches: AlertMatchReference[]
}

export interface AlertMatchListResponse {
  items: AlertMatchEntry[]
  total: number
  page: number
  page_size: number
}
