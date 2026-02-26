const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const tokenStorageKey = 'threatlens.token'

export function getToken(): string | null {
  return localStorage.getItem(tokenStorageKey)
}

export function setToken(token: string | null) {
  if (token) {
    localStorage.setItem(tokenStorageKey, token)
  } else {
    localStorage.removeItem(tokenStorageKey)
  }
}

export async function apiFetch<T>(path: string, options: RequestInit = {}, auth = true): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')

  if (auth) {
    const token = getToken()
    if (token) {
      headers.set('Authorization', `Bearer ${token}`)
    }
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `HTTP ${response.status}`)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
