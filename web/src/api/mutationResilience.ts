import { ApiError, ApiTransportError } from './client'

export function isAmbiguousMutationError(error: unknown): boolean {
  return error instanceof ApiTransportError
    || (error instanceof ApiError
      && (error.status >= 500 || error.code === 'invalid_response'))
}
