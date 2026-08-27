export type TokenRevocationImpact = {
  revokedTokenCount: number | null
  revokedDescendantCount: number | null
  rootTokenRevoked: boolean | null
}

export function parseTokenRevocationImpact(
  headers: Pick<Headers, 'get'>,
): TokenRevocationImpact {
  return {
    revokedTokenCount: parseNonNegativeInteger(
      headers.get('X-ThreatLens-Revoked-Token-Count'),
    ),
    revokedDescendantCount: parseNonNegativeInteger(
      headers.get('X-ThreatLens-Revoked-Descendant-Count'),
    ),
    rootTokenRevoked: parseBoolean(
      headers.get('X-ThreatLens-Root-Token-Revoked'),
    ),
  }
}

export function formatTokenRevocationImpact(
  impact: TokenRevocationImpact,
): string {
  const { revokedTokenCount, revokedDescendantCount, rootTokenRevoked } = impact
  if (rootTokenRevoked === false && revokedTokenCount === 0) {
    return 'The API token and its delegated descendants were already inactive.'
  }
  if (rootTokenRevoked === false && revokedDescendantCount !== null) {
    return `The root API token was already inactive. ${formatCount(revokedDescendantCount, 'delegated child token')} recursively revoked.`
  }
  if (revokedDescendantCount !== null) {
    const total =
      revokedTokenCount !== null
        ? ` ${formatCount(revokedTokenCount, 'token')} revoked in total.`
        : ''
    return `API token revoked. ${formatCount(revokedDescendantCount, 'delegated child token')} recursively revoked.${total}`
  }
  return 'API token revoked. Any active delegated child tokens were recursively revoked.'
}

function parseNonNegativeInteger(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value.trim())) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) ? parsed : null
}

function parseBoolean(value: string | null): boolean | null {
  if (value?.trim().toLowerCase() === 'true') return true
  if (value?.trim().toLowerCase() === 'false') return false
  return null
}

function formatCount(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}
