export interface Team {
  id: string
  key: string
  name: string
  description: string
  membership_group_id: string
  manager_group_id: string | null
  active: boolean
  revision: number
  can_manage: boolean
  created_at: string
  updated_at: string
}

export interface TeamPage {
  items: Team[]
  total: number
  page: number
  page_size: number
}

export interface TeamMember {
  id: string
  email: string
  account_role: string
  is_manager: boolean
}

export interface TeamMemberPage {
  items: TeamMember[]
  total: number
  page: number
  page_size: number
}
