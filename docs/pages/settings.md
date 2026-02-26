# Settings Section

## Purpose

Centralized account, token, and admin operations.

## Navigation Items

Always visible:

- Overview
- Account
- API Tokens

Admin-only:

- Users
- Audit Logs

## Overview

Role capabilities reference cards:

- Admin
- Analyst
- Viewer

## Account Page

- User profile summary (`email`, `role`, `status`, `created`)
- Change password form
- API calls:
  - `GET /auth/me`
  - `POST /auth/change-password`

## API Tokens Page

- Create token form: name, expiry days, scopes CSV
- One-time display of created token secret
- Token inventory and revoke action
- Admin optional filter by `user_id`
- API calls:
  - `GET /tokens`
  - `POST /tokens`
  - `DELETE /tokens/{id}`

## Users Page (Admin)

- Create user form
- Search and edit user directory
- Editable fields per user:
  - role
  - active flag
  - optional password reset
- API calls:
  - `GET /users`
  - `POST /users`
  - `PATCH /users/{id}`

## Audit Logs Page (Admin)

- Filter by `action`
- Filter by `actor_user_id`
- Paginated log table
- Export filtered logs to JSON (`Export JSON`)
- API call:
  - `GET /audit-logs`
  - `GET /audit-logs/export`

## Access Rules

- Protected by authenticated route guard.
- Admin-only pages additionally protected with `RoleRoute` (`roles=['admin']`).
