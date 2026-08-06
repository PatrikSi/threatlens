import { describe, expect, it } from 'vitest'

import { User } from '../types/api'
import {
  buildCreateUserConfirmation,
  buildPasswordResetConfirmation,
  buildUserSettingsConfirmation,
  resolveSelfLockoutWarnings,
  syncUserSettingsDrafts,
} from './userSettingsDraft'

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
      warnings: [],
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

  it('elevates self-lockout changes when an admin is editing their own access posture', () => {
    const adminUser: User = {
      ...BASE_USER,
      email: 'admin@example.com',
      role: 'admin',
    }

    const confirmation = buildUserSettingsConfirmation(
      adminUser,
      {
        role: 'viewer',
        isActive: false,
        isApproved: false,
      },
      {
        id: 'user-1',
        role: 'admin',
      },
    )

    expect(confirmation).toMatchObject({
      title: 'Apply self-access changes?',
      confirmLabel: 'Apply self-access changes',
      confirmTone: 'danger',
      payload: {
        role: 'viewer',
        is_active: false,
        is_approved: false,
      },
    })
    expect(confirmation?.warnings).toEqual([
      'You are removing your own admin access. Another admin may need to restore your role before you can manage users, audit logs, AI settings, feeds, or tags again.',
      'You are disabling your own account. Your current session can stop working on the next authorization check.',
      'You are sending your own account back to pending approval. Another admin must approve it before you can sign in again.',
    ])
  })
})

describe('resolveSelfLockoutWarnings', () => {
  it('stays quiet when the acting user is changing another account', () => {
    expect(
      resolveSelfLockoutWarnings(
        {
          id: 'user-1',
          role: 'admin',
          is_active: true,
          is_approved: true,
        },
        {
          role: 'viewer',
          isActive: false,
          isApproved: false,
        },
        {
          id: 'admin-2',
          role: 'admin',
        },
      ),
    ).toEqual([])
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

describe('buildCreateUserConfirmation', () => {
  it('trims the email and summarizes the initial access posture before creation', () => {
    const confirmation = buildCreateUserConfirmation({
      email: ' admin@example.com ',
      password: 'temporary-password',
      role: 'admin',
      is_active: false,
      is_approved: false,
    })

    expect(confirmation).toMatchObject({
      title: 'Create local user account?',
      confirmLabel: 'Create local user',
      confirmTone: 'primary',
      payload: {
        email: 'admin@example.com',
        password: 'temporary-password',
        role: 'admin',
        is_active: false,
        is_approved: false,
      },
    })
    expect(confirmation?.details).toEqual([
      'Role: admin.',
      'This account will have full administrative access on first sign-in.',
      'Sign-in will stay blocked until an admin enables the account.',
      'The account will remain pending approval after creation.',
    ])
  })
})

describe('syncUserSettingsDrafts', () => {
  it('keeps dirty row drafts while syncing untouched rows to the latest server copy', () => {
    const nextUsers: User[] = [
      BASE_USER,
      {
        ...BASE_USER,
        id: 'user-2',
        email: 'viewer@example.com',
        role: 'viewer',
        is_active: false,
      },
    ]

    expect(
      syncUserSettingsDrafts(nextUsers, {
        'user-1': {
          role: 'admin',
          isActive: false,
          isApproved: true,
        },
        'user-2': {
          role: 'viewer',
          isActive: true,
          isApproved: true,
        },
      }, {
        'user-1': {
          role: 'analyst',
          isActive: true,
          isApproved: true,
        },
        'user-2': {
          role: 'viewer',
          isActive: true,
          isApproved: true,
        },
      }),
    ).toEqual({
      drafts: {
        'user-1': {
          role: 'admin',
          isActive: false,
          isApproved: true,
        },
        'user-2': {
          role: 'viewer',
          isActive: false,
          isApproved: true,
        },
      },
      baselines: {
        'user-1': {
          role: 'analyst',
          isActive: true,
          isApproved: true,
        },
        'user-2': {
          role: 'viewer',
          isActive: false,
          isApproved: true,
        },
      },
    })
  })
})
