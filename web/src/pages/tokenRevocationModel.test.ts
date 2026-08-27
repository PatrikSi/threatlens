import { describe, expect, it } from 'vitest'

import {
  formatTokenRevocationImpact,
  parseTokenRevocationImpact,
} from './tokenRevocationModel'

describe('token revocation response headers', () => {
  it('reports root and recursive descendant impact from the 204 response', () => {
    const headers = new Headers({
      'X-ThreatLens-Revoked-Token-Count': '3',
      'X-ThreatLens-Revoked-Descendant-Count': '2',
      'X-ThreatLens-Root-Token-Revoked': 'true',
    })

    expect(
      formatTokenRevocationImpact(parseTokenRevocationImpact(headers)),
    ).toBe(
      'API token revoked. 2 delegated child tokens recursively revoked. 3 tokens revoked in total.',
    )
  })

  it('distinguishes an already inactive lineage', () => {
    const headers = new Headers({
      'X-ThreatLens-Revoked-Token-Count': '0',
      'X-ThreatLens-Revoked-Descendant-Count': '0',
      'X-ThreatLens-Root-Token-Revoked': 'false',
    })

    expect(
      formatTokenRevocationImpact(parseTokenRevocationImpact(headers)),
    ).toBe('The API token and its delegated descendants were already inactive.')
  })

  it('uses truthful recursive language when custom headers are unavailable', () => {
    expect(
      formatTokenRevocationImpact(parseTokenRevocationImpact(new Headers())),
    ).toBe(
      'API token revoked. Any active delegated child tokens were recursively revoked.',
    )
  })

  it('rejects malformed and unsafe count headers', () => {
    const headers = new Headers({
      'X-ThreatLens-Revoked-Token-Count': '-1',
      'X-ThreatLens-Revoked-Descendant-Count': '9007199254740992',
      'X-ThreatLens-Root-Token-Revoked': 'perhaps',
    })

    expect(parseTokenRevocationImpact(headers)).toEqual({
      revokedTokenCount: null,
      revokedDescendantCount: null,
      rootTokenRevoked: null,
    })
  })
})
