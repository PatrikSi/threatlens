import { ApiError, apiFetch } from '../api/client'
import type { ApiToken, ApiTokenListResponse } from '../types/api'

type TokenInventoryFetcher = <T>(path: string) => Promise<T>

export async function loadTokenInventory(
  params: URLSearchParams,
  page: number,
  pageSize: number,
  userId: string,
  fetcher: TokenInventoryFetcher = apiFetch,
): Promise<ApiTokenListResponse> {
  try {
    const response = await fetcher<ApiTokenListResponse | ApiToken[]>(
      `/tokens/inventory?${params.toString()}`,
    )
    return normalizeTokenInventory(response, page, pageSize)
  } catch (error) {
    if (!(error instanceof ApiError) || ![404, 422].includes(error.status)) {
      throw error
    }
  }

  const legacyParams = new URLSearchParams()
  if (userId) legacyParams.set('user_id', userId)
  const suffix = legacyParams.size ? `?${legacyParams.toString()}` : ''
  const legacyTokens = await fetcher<ApiToken[]>(`/tokens${suffix}`)
  return normalizeTokenInventory(legacyTokens, page, pageSize)
}

export function normalizeTokenInventory(
  value: ApiTokenListResponse | ApiToken[],
  page: number,
  pageSize: number,
): ApiTokenListResponse {
  if (Array.isArray(value)) {
    const start = (page - 1) * pageSize
    return {
      tokens: value.slice(start, start + pageSize),
      total: value.length,
      page,
      page_size: pageSize,
    }
  }
  if (
    !Array.isArray(value.tokens) ||
    !Number.isSafeInteger(value.total) ||
    value.total < 0 ||
    !Number.isSafeInteger(value.page) ||
    value.page < 1 ||
    !Number.isSafeInteger(value.page_size) ||
    value.page_size < 1
  ) {
    throw new Error('The API returned an invalid token inventory response.')
  }
  return value
}
