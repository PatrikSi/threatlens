import { useReducer } from 'react'

import { ApiTokenCreateResponse } from '../types/api'

export const DEFAULT_TOKEN_EXPIRY_DAYS = 90

export type TokenCreateFormState = {
  name: string
  expiresInDays: number
  scopesText: string
  currentPassword: string
  code: string
  createdToken: ApiTokenCreateResponse | null
}

type TokenCreateFormAction =
  | { type: 'setName'; value: string }
  | { type: 'setExpiresInDays'; value: number }
  | { type: 'setScopesText'; value: string }
  | { type: 'setCurrentPassword'; value: string }
  | { type: 'setCode'; value: string }
  | { type: 'clearCode' }
  | { type: 'createStarted' }
  | { type: 'createFailed' }
  | { type: 'createSucceeded'; value: ApiTokenCreateResponse }
  | { type: 'dismissCreatedToken' }

export function createInitialTokenCreateFormState(): TokenCreateFormState {
  return {
    name: '',
    expiresInDays: DEFAULT_TOKEN_EXPIRY_DAYS,
    scopesText: '',
    currentPassword: '',
    code: '',
    createdToken: null,
  }
}

export function reduceTokenCreateFormState(
  state: TokenCreateFormState,
  action: TokenCreateFormAction,
): TokenCreateFormState {
  switch (action.type) {
    case 'setName':
      return { ...state, name: action.value }
    case 'setExpiresInDays':
      return { ...state, expiresInDays: action.value }
    case 'setScopesText':
      return { ...state, scopesText: action.value }
    case 'setCurrentPassword':
      return { ...state, currentPassword: action.value }
    case 'setCode':
      return { ...state, code: action.value }
    case 'clearCode':
      return { ...state, code: '' }
    case 'createStarted':
    case 'createFailed':
      return { ...state, createdToken: null }
    case 'createSucceeded':
      return {
        ...createInitialTokenCreateFormState(),
        createdToken: action.value,
      }
    case 'dismissCreatedToken':
      return { ...state, createdToken: null }
    default:
      return state
  }
}

export function useTokenCreateFormState() {
  return useReducer(
    reduceTokenCreateFormState,
    undefined,
    createInitialTokenCreateFormState,
  )
}
