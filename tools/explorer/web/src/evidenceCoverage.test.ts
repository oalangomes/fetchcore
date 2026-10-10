import { describe, expect, it } from 'vitest'
import { buildCoverageMap } from './evidenceCoverage'
import type { SearchHit, SearchPayload, SearchResponse, QueryMatch } from './types'

const request: SearchPayload = {
  repository: 'demo/catalog', target_id: 'default', mode: 'discovery', limit: 10,
  knowledge_types: ['code'], intent: {
    schema_version: 2, canonical_query: 'validate eligibility',
    anchors: [{ type: 'anchor', kind: 'path', value: 'src/eligibility.py', origin: 'caller' }],
    expression: { type: 'all_of', children: [
      { type: 'term', value: 'eligibility' },
      { type: 'any_of', children: [
        { type: 'anchor', kind: 'identifier', value: 'eligibility_validator', origin: 'caller' },
        { type: 'term', value: 'auth' },
      ] },
    ] },
    exact_terms: ['eligibility'], semantic_terms: ['authorization'],
    hints: ['nice to have'], expansions: [{ value: 'optional expansion', origin: 'caller' }],
    signals: [{
      value: 'eligibility', type: 'exact', classification: 'exact_identifier', required: true,
      priority: 1, weight: 1, origin: 'caller', source: 'user', section: 'test', signals: [],
    }],
  },
}
const makeMatch = (name: string, classification = 'semantic_phrase', type = 'semantic'): QueryMatch => ({
  signal: { value: name, type, classification, required: true, origin: 'caller' },
  match_type: 'lexical', channels: ['lexical'], matched_values: [name],
  source_ref: 'repository://demo/catalog/src/eligibility.py',
  provenance: 'backend',
})
function hit(id: string, opts: Partial<SearchHit> = {}): SearchHit {
  return {
    id, source: 'src/eligibility.py',
    source_ref: 'repository://demo/catalog/src/eligibility.py',
    knowledge_type: 'code', score: 0.92, content: 'source must not be used for inferred coverage',
    revision: 'r1', checksum: 'one', line_start: 8, line_end: 22,
    query_matches: [], observations: [], ...opts,
  }
}
const response = (results: SearchHit[], meta: Record<string, unknown> = {}): SearchResponse => ({
  backend: 'solr', status: 'ok', metadata: meta, duration_ms: 12, results,
  telemetry: { mode: 'lexical', duration_ms: 12, candidates_examined: 7,
    candidates_returned: results.length, returned_chars: 100,
    stages: [{ name: 'lexical', duration_ms: 9 }], fingerprints: { projection: 'p1' },
  },
})
const row = (map: ReturnType<typeof buildCoverageMap>, role: string, value: string) => {
  const item = map.rows.find(x => x.role === role && x.value === value)
  expect(item).toBeDefined()
  return item!
}

describe('Evidence Coverage Map contract — retrieval is not satisfaction', () => {
  it('observes exact typed signal on one candidate, never a structural anchor from a semantic match', () => {
    const m = buildCoverageMap(request, response([
      hit('one', { query_matches: [
        makeMatch('eligibility'),
        makeMatch('src/eligibility.py', 'semantic_phrase', 'semantic'),
        makeMatch('eligibility_validator', 'semantic_phrase', 'semantic'),
      ] }),
      hit('two', { source_ref: 'repository://demo/catalog/src/other.py', checksum: 'two', query_matches: [makeMatch('something else')] }),
    ]))
    expect(row(m, 'expression-term', 'eligibility').summary).toBe('observed')
    expect(row(m, 'exact-term', 'eligibility').observedCandidates).toBe(1)
    expect(row(m, 'signal-required', 'eligibility').summary).toBe('not-observed') // typed classification mismatch
    expect(row(m, 'anchor', 'src/eligibility.py').summary).toBe('not-observed')
    expect(row(m, 'expression-anchor', 'eligibility_validator').summary).toBe('not-observed')
    expect(row(m, 'canonical', 'validate eligibility').summary).toBe('not-evaluated')
    expect(row(m, 'hint', 'nice to have').summary).toBe('not-evaluated')
    expect(row(m, 'expansion', 'optional expansion').summary).toBe('not-evaluated')
    expect(m.branches.find(b => b.operator === 'all_of')?.totalChildren).toBe(3)
    expect(m.diagnostics.effectiveMode).toBe('lexical')
    expect(m.diagnostics.fingerprints.projection).toBe('p1')
    expect(m.diagnostics.totalReturned).toBe(2)
  })

  it('reports a typed path / identifier anchor only from matching typed QuerySignal', () => {
    const m = buildCoverageMap(request, response([hit('one', { query_matches: [
      makeMatch('src/eligibility.py', 'path', 'exact'),
      makeMatch('eligibility_validator', 'exact_identifier', 'reference'),
    ] })]))
    expect(row(m, 'anchor', 'src/eligibility.py').summary).toBe('observed')
    expect(row(m, 'expression-anchor', 'eligibility_validator').summary).toBe('observed')
    expect(row(m, 'expression-anchor', 'eligibility_validator').cells[0].channels).toEqual(['lexical'])
    expect(m.branches.find(b => b.operator === 'any_of')?.observedChildren).toBe(1)
  })

  it('distinguishes no match observed from not evaluated and does not infer from observations/scores', () => {
    const empty = buildCoverageMap(request, response([hit('one', { observations: [
      { category: 'identifier', subtype: 'symbol', value: 'eligibility', source_ref: 'repository://demo/catalog/src/eligibility.py' },
    ] })]))
    expect(empty.diagnostics.noQueryMatchDiagnostics).toBe(true)
    expect(row(empty, 'exact-term', 'eligibility').summary).toBe('not-evaluated')
    expect(row(empty, 'anchor', 'src/eligibility.py').summary).toBe('not-evaluated')
    expect(row(empty, 'exact-term', 'eligibility').cells[0].observations).toBe(1)
    const withOther = buildCoverageMap(request, response([hit('two', { query_matches: [makeMatch('unrelated')] })]))
    expect(row(withOther, 'anchor', 'src/eligibility.py').summary).toBe('not-observed')
  })

  it('differentiates explicit unsupported translation and capped candidate windows', () => {
    const unsupported = buildCoverageMap(request, response([
      hit('one', { query_matches: [makeMatch('unrelated')] }),
    ], { query_translation: { unsupported: [
      { path: 'anchors.0', reason: 'unsupported' },
    ] } }))
    expect(row(unsupported, 'anchor', 'src/eligibility.py').summary).toBe('unsupported')
    expect(unsupported.diagnostics.unsupportedTranslationReported).toBe(true)
    const capped = buildCoverageMap({ ...request, limit: 1 }, response([hit('one', { query_matches: [makeMatch('unrelated')] })]))
    expect(capped.diagnostics.resultWindowBounded).toBe(true)
    expect(row(capped, 'anchor', 'src/eligibility.py').summary).toBe('truncated')
  })

  it('identifies exact duplicate source identities while preserving multi-hit evidence', () => {
    const m = buildCoverageMap(request, response([
      hit('one', { query_matches: [makeMatch('eligibility')] }),
      hit('two', { query_matches: [makeMatch('src/eligibility.py', 'path', 'exact')] }),
      hit('three', { checksum: 'other', query_matches: [makeMatch('unrelated')] }),
    ]))
    expect(m.columns).toHaveLength(2)
    expect(m.columns[0].hitIds).toEqual(['one', 'two'])
    expect(m.columns[0].duplicateCount).toBe(1)
    expect(m.diagnostics.duplicateCandidates).toBe(1)
    expect(row(m, 'anchor', 'src/eligibility.py').cells[0].state).toBe('observed')
    expect(row(m, 'exact-term', 'eligibility').cells[0].state).toBe('observed')
  })

  it('bounds a large response to 24 source columns and 48 signal rows without unknown → negative promotion', () => {
    const denseRequest: SearchPayload = {
      ...request, limit: 100, intent: { schema_version: 2, canonical_query: 'A',
        exact_terms: Array.from({ length: 80 }, (_, i) => 'term' + i) },
    }
    const results = Array.from({ length: 80 }, (_, i) => hit('hit' + i, {
      source_ref: 'repository://demo/catalog/src/' + i + '.py',
      query_matches: [makeMatch('other')],
    }))
    const m = buildCoverageMap(denseRequest, response(results))
    expect(m.diagnostics.totalSignals).toBe(81)
    expect(m.diagnostics.visibleSignals).toBe(48)
    expect(m.columns).toHaveLength(24)
    expect(m.diagnostics.matrixTruncated).toBe(true)
    expect(row(m, 'exact-term', 'term0').summary).toBe('truncated')
  })

  it('does not invent channel attribution in a hybrid response without QueryMatchEvidence', () => {
    const hybrid = response([hit('one')])
    hybrid.telemetry!.mode = 'hybrid'
    const m = buildCoverageMap(request, hybrid)
    expect(m.diagnostics.effectiveMode).toBe('hybrid')
    expect(m.rows.every(item => item.cells.every(cell => cell.channels.length === 0))).toBe(true)
    expect(m.diagnostics.noQueryMatchDiagnostics).toBe(true)
  })
})
