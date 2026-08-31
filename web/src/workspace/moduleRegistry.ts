import {
  Activity,
  BellRing,
  Bot,
  ChartNoAxesCombined,
  Download,
  FileText,
  FolderSearch,
  KeyRound,
  LayoutDashboard,
  Mail,
  NotebookTabs,
  PlugZap,
  Rss,
  ScrollText,
  Settings,
  ShieldCheck,
  Tags,
  UserRound,
  Users,
  Webhook,
  type LucideIcon,
} from 'lucide-react'

import type {
  WorkspaceFeatureKey,
  WorkspaceMobileBehavior,
  WorkspaceRole,
  WorkspaceSection,
} from '../types/workspace'

export type TrustedWorkspaceModuleId =
  | 'primary.dashboard'
  | 'primary.alerts'
  | 'primary.investigations'
  | 'primary.feeds'
  | 'primary.stats'
  | 'primary.export'
  | 'primary.reporting'
  | 'primary.settings'
  | 'settings.account'
  | 'settings.tokens'
  | 'settings.workspace'
  | 'settings.ai'
  | 'settings.tagging'
  | 'settings.access'
  | 'settings.identity'
  | 'settings.users'
  | 'settings.audit'
  | 'settings.operations'
  | 'settings.integrations'
  | 'settings.integrations.webhooks'
  | 'settings.integrations.smtp'

export type TrustedDashboardPanelId = 'rss' | 'alerts' | 'notes' | 'daily_brief'

export interface TrustedWorkspaceModule {
  id: TrustedWorkspaceModuleId
  label: string
  route: string
  section: WorkspaceSection
  parentId: TrustedWorkspaceModuleId | null
  icon: LucideIcon
  requiredPermissions: readonly string[]
  featureDependency: WorkspaceFeatureKey | null
  serverFeatureFlag: string | null
  defaultVisibleRoles: readonly WorkspaceRole[]
  defaultOptional: boolean
  defaultOrder: number
  defaultMobilePriority: number
  mobileBehavior: WorkspaceMobileBehavior
  isContainer: boolean
  landingEligible: boolean
  policyManaged: boolean
}

export interface TrustedDashboardPanel {
  id: TrustedDashboardPanelId
  label: string
  icon: LucideIcon
  requiredPermissions: readonly string[]
  featureDependency: WorkspaceFeatureKey | null
  serverFeatureFlag: string | null
}

const ALL_ROLES: readonly WorkspaceRole[] = ['admin', 'analyst', 'viewer']
const ADMIN_ONLY: readonly WorkspaceRole[] = ['admin']

function moduleDefinition(
  definition: Omit<
    TrustedWorkspaceModule,
    'section' | 'parentId' | 'isContainer' | 'landingEligible' | 'policyManaged'
  > & {
    parentId?: TrustedWorkspaceModuleId | null
    isContainer?: boolean
    landingEligible?: boolean
    policyManaged?: boolean
  },
): TrustedWorkspaceModule {
  const section: WorkspaceSection = definition.id.startsWith('settings.') ? 'settings' : 'primary'
  const isContainer = definition.isContainer ?? false
  return {
    ...definition,
    section,
    parentId: definition.parentId ?? (section === 'settings' ? 'primary.settings' : null),
    isContainer,
    landingEligible: definition.landingEligible ?? !isContainer,
    policyManaged: definition.policyManaged ?? true,
  }
}

export const TRUSTED_WORKSPACE_MODULES: readonly TrustedWorkspaceModule[] = [
  moduleDefinition({
    id: 'primary.dashboard', label: 'Dashboard', route: '/', icon: LayoutDashboard,
    requiredPermissions: ['read:items'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: false, defaultOrder: 0,
    defaultMobilePriority: 0, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'primary.alerts', label: 'Alerts', route: '/alerts', icon: BellRing,
    requiredPermissions: ['read:alerts', 'read:items'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 10,
    defaultMobilePriority: 10, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'primary.investigations', label: 'Investigations', route: '/investigations', icon: FolderSearch,
    requiredPermissions: ['read:investigations'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 20,
    defaultMobilePriority: 20, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'primary.feeds', label: 'Feeds', route: '/feeds', icon: Rss,
    requiredPermissions: ['read:feeds'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 30,
    defaultMobilePriority: 30, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'primary.stats', label: 'Stats', route: '/stats', icon: ChartNoAxesCombined,
    requiredPermissions: ['read:stats'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 40,
    defaultMobilePriority: 40, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'primary.export', label: 'Export', route: '/export', icon: Download,
    requiredPermissions: ['read:items'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 50,
    defaultMobilePriority: 50, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'primary.reporting', label: 'Reporting', route: '/reporting', icon: FileText,
    requiredPermissions: ['read:reports'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 60,
    defaultMobilePriority: 60, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'primary.settings', label: 'Settings', route: '/settings', icon: Settings,
    requiredPermissions: [], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: false, defaultOrder: 70,
    defaultMobilePriority: 70, mobileBehavior: 'secondary', isContainer: true,
    policyManaged: false,
  }),
  moduleDefinition({
    id: 'settings.account', label: 'Account', route: '/settings/account', icon: UserRound,
    requiredPermissions: [], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: false, defaultOrder: 0,
    defaultMobilePriority: 0, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'settings.tokens', label: 'API Tokens', route: '/settings/tokens', icon: KeyRound,
    requiredPermissions: ['write:tokens'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 10,
    defaultMobilePriority: 10, mobileBehavior: 'primary',
  }),
  moduleDefinition({
    id: 'settings.workspace', label: 'Workspace', route: '/settings/workspace', icon: NotebookTabs,
    requiredPermissions: ['read:workspace'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: false, defaultOrder: 15,
    defaultMobilePriority: 15, mobileBehavior: 'primary', policyManaged: false,
  }),
  moduleDefinition({
    id: 'settings.ai', label: 'AI', route: '/settings/ai', icon: Bot,
    requiredPermissions: ['read:ai'], featureDependency: 'ai_enabled', serverFeatureFlag: 'ai_enabled',
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 20,
    defaultMobilePriority: 20, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.tagging', label: 'Tagging', route: '/settings/tagging', icon: Tags,
    requiredPermissions: ['read:tagging'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 30,
    defaultMobilePriority: 30, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.identity', label: 'Identity', route: '/settings/identity', icon: ShieldCheck,
    requiredPermissions: ['read:users'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 40,
    defaultMobilePriority: 40, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.access', label: 'Access', route: '/settings/access', icon: ShieldCheck,
    requiredPermissions: ['read:iam'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 45,
    defaultMobilePriority: 45, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.users', label: 'Users', route: '/settings/users', icon: Users,
    requiredPermissions: ['read:users'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 50,
    defaultMobilePriority: 50, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.audit', label: 'Audit Logs', route: '/settings/audit-logs', icon: ScrollText,
    requiredPermissions: ['read:audit'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 60,
    defaultMobilePriority: 60, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.operations', label: 'Operations', route: '/settings/operations', icon: Activity,
    requiredPermissions: ['read:operations'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 70,
    defaultMobilePriority: 70, mobileBehavior: 'secondary',
  }),
  moduleDefinition({
    id: 'settings.integrations', label: 'Integrations', route: '/settings/integrations', icon: PlugZap,
    requiredPermissions: ['read:notifications'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 80,
    defaultMobilePriority: 80, mobileBehavior: 'secondary', isContainer: true,
    policyManaged: false,
  }),
  moduleDefinition({
    id: 'settings.integrations.webhooks', label: 'Webhooks', route: '/settings/integrations/webhooks', icon: Webhook,
    requiredPermissions: ['read:notifications'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ALL_ROLES, defaultOptional: true, defaultOrder: 90,
    defaultMobilePriority: 90, mobileBehavior: 'secondary', parentId: 'settings.integrations',
  }),
  moduleDefinition({
    id: 'settings.integrations.smtp', label: 'SMTP', route: '/settings/integrations/smtp', icon: Mail,
    requiredPermissions: ['read:integrations'], featureDependency: null, serverFeatureFlag: null,
    defaultVisibleRoles: ADMIN_ONLY, defaultOptional: true, defaultOrder: 100,
    defaultMobilePriority: 100, mobileBehavior: 'secondary', parentId: 'settings.integrations',
  }),
]

export const TRUSTED_DASHBOARD_PANELS: readonly TrustedDashboardPanel[] = [
  { id: 'rss', label: 'RSS intelligence', icon: Rss, requiredPermissions: ['read:items'], featureDependency: null, serverFeatureFlag: null },
  { id: 'alerts', label: 'Alerts', icon: BellRing, requiredPermissions: ['read:alerts', 'read:items'], featureDependency: null, serverFeatureFlag: null },
  { id: 'notes', label: 'Notes', icon: NotebookTabs, requiredPermissions: [], featureDependency: null, serverFeatureFlag: null },
  { id: 'daily_brief', label: 'AI daily brief', icon: Bot, requiredPermissions: ['read:items'], featureDependency: 'ai_daily_brief_enabled', serverFeatureFlag: 'ai_daily_brief_enabled' },
]

export const TRUSTED_WORKSPACE_MODULE_BY_ID = new Map(
  TRUSTED_WORKSPACE_MODULES.map((module) => [module.id, module]),
)
export const TRUSTED_DASHBOARD_PANEL_BY_ID = new Map(
  TRUSTED_DASHBOARD_PANELS.map((panel) => [panel.id, panel]),
)

export function isTrustedWorkspaceModuleId(value: string): value is TrustedWorkspaceModuleId {
  return TRUSTED_WORKSPACE_MODULE_BY_ID.has(value as TrustedWorkspaceModuleId)
}

export function isTrustedDashboardPanelId(value: string): value is TrustedDashboardPanelId {
  return TRUSTED_DASHBOARD_PANEL_BY_ID.has(value as TrustedDashboardPanelId)
}
