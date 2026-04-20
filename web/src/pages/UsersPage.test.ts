import { describe, expect, it } from 'vitest'

import { User } from '../types/api'
import { buildPasswordResetConfirmation, buildUserSettingsConfirmation } from './UsersPage'

const BASE_USER: User = {
  id: 'user-1',
  email: 'analyst@example.com',
  role: 'analyst',
  is_active: true,
  is_approved: true,
  approved_at: '2026-04-18T09:00:00Z',
  created_at: '2026-04-17T09:00:00Z',
}

describe('buildUserSettingsConfirmation', () => {
  it('returns null when no privileged account changes are requested', () => {
    expect(
      buildUserSettingsConfirmation(BASE_USER, {
        role: 'analyst',
        isActive: true,
        isApproved: true,
      }),
    ).toBeNull()
  })

  it('summarizes role, sign-in, and approval consequences before saving', () => {
    const confirmation = buildUserSettingsConfirmation(BASE_USER, {
      role: 'admin',
      isActive: false,
      isApproved: false,
    })

    expect(confirmation).toMatchObject({
      title: 'Apply privileged user changes?',
      confirmLabel: 'Apply user changes',
      confirmTone: 'primary',
      payload: {
        role: 'admin',
        is_active: false,
        is_approved: false,
      },
    })
    expect(confirmation?.details).toEqual([
      'Role will change from analyst to admin.',
      'This grants full administrative access across user management, global settings, and operational controls.',
      'Sign-in will be blocked until the account is reactivated.',
      'The account will return to pending approval.',
    ])
  })
})

describe('buildPasswordResetConfirmation', () => {
  it('requires a minimum-length password before enabling the confirmation flow', () => {
    expect(buildPasswordResetConfirmation(BASE_USER, ' short ')).toBeNull()
  })

  it('describes the operational impact of resetting a password', () => {
    const confirmation = buildPasswordResetConfirmation(BASE_USER, ' replaced-password ')

    expect(confirmation).toMatchObject({
      title: 'Reset user password?',
      confirmLabel: 'Reset password',
      confirmTone: 'primary',
      payload: { password: 'replaced-password' },
    })
    expect(confirmation?.details).toEqual([
      'You are updating credentials for analyst@example.com.',
      'The current password will stop working as soon as you confirm.',
      'The new password meets the minimum length requirement with 17 characters.',
      'Share the new password through a secure channel.',
    ])
  })
})
