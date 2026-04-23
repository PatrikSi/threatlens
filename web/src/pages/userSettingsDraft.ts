import { User, UserCreateRequest, UserUpdateRequest } from '../types/api'

export type UserSettingsDraft = {
  role: User['role']
  isActive: boolean
  isApproved: boolean
}

export type UserConfirmationState = {
  title: string
  description: string
  confirmLabel: string
  confirmTone: 'danger' | 'primary'
  details: string[]
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

export function buildUserSettingsConfirmation(user: User, draft: UserSettingsDraft): UserConfirmationState | null {
  const payload: UserUpdateRequest = {}
  const details: string[] = []

  if (draft.role !== user.role) {
    payload.role = draft.role
    details.push(`Role will change from ${user.role} to ${draft.role}.`)
    if (draft.role === 'admin') {
      details.push('This grants full administrative access across user management, global settings, and operational controls.')
    } else if (user.role === 'admin') {
      details.push('This removes administrative access to user management, audit logs, and global settings.')
    }
  }

  if (draft.isActive !== user.is_active) {
    payload.is_active = draft.isActive
    details.push(
      draft.isActive ? 'Sign-in will be re-enabled for this account.' : 'Sign-in will be blocked until the account is reactivated.',
    )
  }

  if (draft.isApproved !== user.is_approved) {
    payload.is_approved = draft.isApproved
    details.push(
      draft.isApproved ? 'The account will move out of pending approval.' : 'The account will return to pending approval.',
    )
  }

  if (!details.length) {
    return null
  }

  return {
    title: 'Apply privileged user changes?',
    description: 'Review the account changes below before they are applied.',
    confirmLabel: 'Apply user changes',
    confirmTone: 'primary',
    details,
    payload,
  }
}

export function buildPasswordResetConfirmation(user: User, nextPassword: string): UserConfirmationState | null {
  const trimmedPassword = nextPassword.trim()
  if (trimmedPassword.length < 8) {
    return null
  }

  return {
    title: 'Reset user password?',
    description: `This immediately replaces the current password for ${user.email}.`,
    confirmLabel: 'Reset password',
    confirmTone: 'primary',
    details: [
      `You are updating credentials for ${user.email}.`,
      'The current password will stop working as soon as you confirm.',
      `The new password meets the minimum length requirement with ${trimmedPassword.length} characters.`,
      'Share the new password through a secure channel.',
    ],
    payload: { password: trimmedPassword },
  }
}

export function createUserSettingsDraft(user: Pick<User, 'role' | 'is_active' | 'is_approved'>): UserSettingsDraft {
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
  return draft.role !== user.role || draft.isActive !== user.is_active || draft.isApproved !== user.is_approved
}

export function isUserSettingsDraftEqual(left: UserSettingsDraft, right: UserSettingsDraft): boolean {
  return left.role === right.role && left.isActive === right.isActive && left.isApproved === right.isApproved
}

export function syncUserSettingsDrafts(
  users: User[],
  drafts: Record<string, UserSettingsDraft>,
  baselines: Record<string, UserSettingsDraft>,
): {
  drafts: Record<string, UserSettingsDraft>
  baselines: Record<string, UserSettingsDraft>
} {
  const nextDrafts: Record<string, UserSettingsDraft> = {}
  const nextBaselines: Record<string, UserSettingsDraft> = {}

  for (const user of users) {
    const nextBaseline = createUserSettingsDraft(user)
    const persistedDraft = drafts[user.id]
    const persistedBaseline = baselines[user.id]
    const hasDirtyDraft =
      persistedDraft && persistedBaseline ? !isUserSettingsDraftEqual(persistedDraft, persistedBaseline) : false

    nextDrafts[user.id] = persistedDraft && hasDirtyDraft ? persistedDraft : nextBaseline
    nextBaselines[user.id] = nextBaseline
  }

  return {
    drafts: nextDrafts,
    baselines: nextBaselines,
  }
}

export function buildCreateUserConfirmation(createRequest: UserCreateRequest): CreateUserConfirmationState | null {
  const email = createRequest.email.trim()
  if (!email || createRequest.password.length < 8) {
    return null
  }

  const details = [
    `Role: ${createRequest.role}.`,
    createRequest.is_active ? 'Sign-in will be enabled immediately.' : 'Sign-in will stay blocked until an admin enables the account.',
    createRequest.is_approved ? 'The account will skip the pending-approval state.' : 'The account will remain pending approval after creation.',
  ]

  if (createRequest.role === 'admin') {
    details.splice(1, 0, 'This account will have full administrative access on first sign-in.')
  }

  return {
    title: 'Create user account?',
    description: 'Review the new account details before provisioning access.',
    confirmLabel: 'Create user',
    confirmTone: 'primary',
    details,
    payload: {
      ...createRequest,
      email,
    },
  }
}
