import { User, UserCreateRequest, UserUpdateRequest } from '../types/api'

export type UserSettingsDraft = {
  role: User['role']
  isActive: boolean
  isApproved: boolean
}

export type UserSettingsDraftConflict = {
  serverDraft: UserSettingsDraft
  reappliedDraft: UserSettingsDraft
  overlappingFields: Array<{
    field: keyof UserSettingsDraft
    label: string
    serverValue: string
    operatorValue: string
  }>
}

const USER_SETTINGS_FIELDS: Array<{
  field: keyof UserSettingsDraft
  label: string
}> = [
  { field: 'role', label: 'Role' },
  { field: 'isActive', label: 'Active status' },
  { field: 'isApproved', label: 'Approval status' },
]

export type UserConfirmationState = {
  title: string
  description: string
  confirmLabel: string
  confirmTone: 'danger' | 'primary'
  details: string[]
  warnings: string[]
  payload: UserUpdateRequest
}

export type CreateUserConfirmationState = {
  title: string
  description: string
  confirmLabel: string
  confirmTone: 'danger' | 'primary'
  details: string[]
  payload: UserCreateRequest
}

type ActingUserContext = Pick<User, 'id' | 'role'> | null | undefined
type VersionedUser = User & { security_version?: number }

export function resolveSelfLockoutWarnings(
  user: Pick<User, 'id' | 'role' | 'is_active' | 'is_approved'>,
  draft: UserSettingsDraft,
  actingUser?: ActingUserContext,
): string[] {
  if (
    !actingUser ||
    actingUser.role !== 'admin' ||
    actingUser.id !== user.id ||
    user.role !== 'admin'
  ) {
    return []
  }

  const warnings: string[] = []

  if (draft.role !== user.role) {
    warnings.push(
      'You are removing your own admin access. Another admin may need to restore your role before you can manage users, audit logs, AI settings, feeds, or tags again.',
    )
  }

  if (draft.isActive !== user.is_active && !draft.isActive) {
    warnings.push(
      'You are disabling your own account. Your current session can stop working on the next authorization check.',
    )
  }

  if (draft.isApproved !== user.is_approved && !draft.isApproved) {
    warnings.push(
      'You are sending your own account back to pending approval. Another admin must approve it before you can sign in again.',
    )
  }

  return warnings
}

export function buildUserSettingsConfirmation(
  user: VersionedUser,
  draft: UserSettingsDraft,
  actingUser?: ActingUserContext,
): UserConfirmationState | null {
  const payload: UserUpdateRequest = {}
  const details: string[] = []

  if (draft.role !== user.role) {
    payload.role = draft.role
    details.push(`Role will change from ${user.role} to ${draft.role}.`)
    if (draft.role === 'admin') {
      details.push(
        'This grants full administrative access across user management, global settings, and operational controls.',
      )
    } else if (user.role === 'admin') {
      details.push(
        'This removes administrative access to user management, audit logs, and global settings.',
      )
    }
  }

  if (draft.isActive !== user.is_active) {
    payload.is_active = draft.isActive
    details.push(
      draft.isActive
        ? 'Sign-in will be re-enabled for this account.'
        : 'Sign-in will be blocked until the account is reactivated.',
    )
  }

  if (draft.isApproved !== user.is_approved) {
    payload.is_approved = draft.isApproved
    details.push(
      draft.isApproved
        ? 'The account will move out of pending approval.'
        : 'The account will return to pending approval.',
    )
  }

  if (!details.length) {
    return null
  }

  details.push(
    'All existing browser sessions and API tokens will be revoked so the updated access policy takes effect.',
  )

  const warnings = resolveSelfLockoutWarnings(user, draft, actingUser)
  attachExpectedSecurityVersion(payload, user)

  return {
    title: warnings.length
      ? 'Apply self-access changes?'
      : 'Apply privileged user changes?',
    description: warnings.length
      ? 'Review these changes carefully. They can remove your own administrative access and may require another admin to recover.'
      : 'Review the account changes below before they are applied.',
    confirmLabel: warnings.length
      ? 'Apply self-access changes'
      : 'Apply user changes',
    confirmTone: warnings.length ? 'danger' : 'primary',
    details,
    warnings,
    payload,
  }
}

export function buildPasswordResetConfirmation(
  user: VersionedUser,
  nextPassword: string,
): UserConfirmationState | null {
  if (nextPassword.length < 8) {
    return null
  }

  return {
    title: 'Reset user password?',
    description: `This immediately replaces the current password for ${user.email} and revokes their existing access credentials.`,
    confirmLabel: 'Reset password',
    confirmTone: 'primary',
    details: [
      `You are updating credentials for ${user.email}.`,
      'The current password will stop working as soon as you confirm.',
      `The new password meets the minimum length requirement with ${nextPassword.length} characters.`,
      'All existing browser sessions and API tokens will be revoked. The user must sign in again.',
      'Share the new password through a secure channel.',
    ],
    warnings: [],
    payload: attachExpectedSecurityVersion({ password: nextPassword }, user),
  }
}

function attachExpectedSecurityVersion(
  payload: UserUpdateRequest,
  user: VersionedUser,
): UserUpdateRequest {
  if (typeof user.security_version === 'number') {
    payload.expected_security_version = user.security_version
  }
  return payload
}

export function createUserSettingsDraft(
  user: Pick<User, 'role' | 'is_active' | 'is_approved'>,
): UserSettingsDraft {
  return {
    role: user.role,
    isActive: user.is_active,
    isApproved: user.is_approved,
  }
}

export function isUserSettingsDraftDirty(
  user: Pick<User, 'role' | 'is_active' | 'is_approved'>,
  draft: UserSettingsDraft,
): boolean {
  return (
    draft.role !== user.role ||
    draft.isActive !== user.is_active ||
    draft.isApproved !== user.is_approved
  )
}

export function isUserSettingsDraftEqual(
  left: UserSettingsDraft,
  right: UserSettingsDraft,
): boolean {
  return (
    left.role === right.role &&
    left.isActive === right.isActive &&
    left.isApproved === right.isApproved
  )
}

export function syncUserSettingsDrafts(
  users: User[],
  drafts: Record<string, UserSettingsDraft>,
  baselines: Record<string, UserSettingsDraft>,
): {
  drafts: Record<string, UserSettingsDraft>
  baselines: Record<string, UserSettingsDraft>
  conflicts: Record<string, UserSettingsDraftConflict>
} {
  const nextDrafts: Record<string, UserSettingsDraft> = {}
  const nextBaselines: Record<string, UserSettingsDraft> = {}
  const conflicts: Record<string, UserSettingsDraftConflict> = {}

  for (const user of users) {
    const nextBaseline = createUserSettingsDraft(user)
    const persistedDraft = drafts[user.id]
    const persistedBaseline = baselines[user.id]
    const hasDirtyDraft =
      persistedDraft && persistedBaseline
        ? !isUserSettingsDraftEqual(persistedDraft, persistedBaseline)
        : false

    if (persistedDraft && persistedBaseline && hasDirtyDraft) {
      const reappliedDraft = { ...nextBaseline }
      const overlappingFields: UserSettingsDraftConflict['overlappingFields'] =
        []
      const writableDraft = reappliedDraft as unknown as Record<string, unknown>
      for (const { field, label } of USER_SETTINGS_FIELDS) {
        const operatorChanged =
          persistedDraft[field] !== persistedBaseline[field]
        const serverChanged = nextBaseline[field] !== persistedBaseline[field]
        if (operatorChanged) writableDraft[field] = persistedDraft[field]
        if (
          operatorChanged &&
          serverChanged &&
          persistedDraft[field] !== nextBaseline[field]
        ) {
          overlappingFields.push({
            field,
            label,
            serverValue: formatUserSetting(field, nextBaseline[field]),
            operatorValue: formatUserSetting(field, persistedDraft[field]),
          })
        }
      }
      nextDrafts[user.id] = reappliedDraft
      if (overlappingFields.length) {
        conflicts[user.id] = {
          serverDraft: nextBaseline,
          reappliedDraft,
          overlappingFields,
        }
      }
    } else {
      nextDrafts[user.id] = nextBaseline
    }
    nextBaselines[user.id] = nextBaseline
  }

  for (const [userId, persistedDraft] of Object.entries(drafts)) {
    if (nextDrafts[userId]) continue
    const persistedBaseline = baselines[userId]
    if (
      persistedBaseline &&
      !isUserSettingsDraftEqual(persistedDraft, persistedBaseline)
    ) {
      nextDrafts[userId] = persistedDraft
      nextBaselines[userId] = persistedBaseline
    }
  }

  return {
    drafts: nextDrafts,
    baselines: nextBaselines,
    conflicts,
  }
}

function formatUserSetting(
  field: keyof UserSettingsDraft,
  value: UserSettingsDraft[keyof UserSettingsDraft],
): string {
  if (field === 'isActive') return value ? 'Active' : 'Inactive'
  if (field === 'isApproved') return value ? 'Approved' : 'Pending approval'
  return String(value)
}

export function buildCreateUserConfirmation(
  createRequest: UserCreateRequest,
): CreateUserConfirmationState | null {
  const email = createRequest.email.trim()
  if (!email || createRequest.password.length < 8) {
    return null
  }

  const details = [
    `Role: ${createRequest.role}.`,
    createRequest.is_active
      ? 'Sign-in will be enabled immediately.'
      : 'Sign-in will stay blocked until an admin enables the account.',
    createRequest.is_approved
      ? 'The account will skip the pending-approval state.'
      : 'The account will remain pending approval after creation.',
  ]

  if (createRequest.role === 'admin') {
    details.splice(
      1,
      0,
      'This account will have full administrative access on first sign-in.',
    )
  }

  return {
    title: 'Create local user account?',
    description: 'Review the local account details before provisioning access.',
    confirmLabel: 'Create local user',
    confirmTone: 'primary',
    details,
    payload: {
      ...createRequest,
      email,
    },
  }
}
