import type { Indicator, IndicatorPage } from '../../src/types/indicators'

export const indicatorFixture: Indicator = {
  id: 'ioc-1',
  type: 'domain',
  value: 'suspicious.test',
  raw: 'suspicious[.]test',
  extraction_confidence: 0.95,
  evidence: [
    {
      source: 'article',
      raw: 'suspicious[.]test',
      quote: 'Contact suspicious[.]test for control.',
      start: 8,
      end: 25,
      transformations: ['refang_dot'],
    },
  ],
  ai: {
    role: 'malicious_infrastructure',
    assertion: 'reported',
    evidence: [],
    maliciousness_confidence: null,
  },
  ai_current: true,
  assessment: null,
  excluded: false,
  exclusion_reasons: [],
  suppressed: false,
}
export const indicatorPageFixture: IndicatorPage = {
  items: [indicatorFixture],
  total: 1,
  page: 1,
  page_size: 20,
  source_revision: 7,
  extraction_revision: 4,
  can_review: true,
  can_manage_suppressions: true,
  extraction_current: true,
}
