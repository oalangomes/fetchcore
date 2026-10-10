import { describe, expect, it } from 'vitest'
import { compareRuns, exportComparison } from './compareRuns'
import { captureRun } from './runHistory'
import type { ExplorerTarget, SearchPayload, SearchResponse } from './types'

const target: ExplorerTarget = {
  id: 'default', repository: 'demo/catalog', backend: 'solr', collection: 'fetchcore-lab',
  revision: 'revision-1', capabilities: { lexical: true }, retrieval: { candidate_budget: 80 },
}
const request: SearchPayload = {
  repository: 'demo/catalog', target_id: 'default',
  mode: 'discovery', limit: 5, knowledge_types: ['code'],
  intent: { schema_version: 2, canonical_query: 'eligibility_validator', anchors: [] },
}
function candidate(id: string, rank: number, ref = id) {
  return {
    id, source: id, source_ref: 'repository://demo/catalog/' + ref + '.py',
    knowledge_type: 'code', score: 5 - rank, content: 'indexed body',
    revision: 'revision-1', checksum: 'sum-' + id,
    query_matches: [{ signal: {}, match_type: 'lexical', channels: ['lexical'],
      matched_values: [id], source_ref: 'repository://demo/catalog/' + ref + '.py' }],
  }
}
function run(id: string, results: ReturnType<typeof candidate>[], patches: {
  request?: Partial<SearchPayload>, target?: Partial<ExplorerTarget>,
  fingerprints?: Record<string, string>, mode?: string, error?: string,
} = {}) {
  const t = { ...target, ...patches.target }
  const req = { ...request, ...patches.request, target_id: t.id, repository: t.repository }
  const response: SearchResponse = {
    backend: t.backend, status: 'ok', duration_ms: 18, metadata: {},
    results, telemetry: {
      mode: patches.mode ?? 'lexical',
      duration_ms: 18, candidates_examined: 12, candidates_returned: results.length,
      returned_chars: 90, time_to_first_candidate_ms: 5,
      stages: [{ name: 'lexical', duration_ms: 14 }],
      fingerprints: patches.fingerprints ?? { projection: 'p1', indexing: 'i1' },
    },
  }
  return captureRun(id, req, t, patches.error ? null : response, patches.error ?? null, '2026-10-10T12:00:00Z')
}

describe('Controlled comparison — observed evidence only', () => {
  it('shows rank movement, shared/exclusive candidates, channel contribution and measured costs', () => {
    const a = run('a', [candidate('one', 1), candidate('two', 2)])
    const b = run('b', [candidate('two', 1), candidate('three', 2), candidate('one', 3)])
    const diff = compareRuns(a, b)
    expect(diff.assessment.status).toBe('snapshot-aligned')
    expect(diff.counts).toEqual({ a: 2, b: 3, shared: 2, only_a: 0, only_b: 1 })
    expect(diff.candidates.find(x => x.a?.id === 'one')?.rank_delta).toBe(2)
    expect(diff.candidates.find(x => x.a?.id === 'two')?.rank_delta).toBe(-1)
    expect(diff.candidates.find(x => x.b?.id === 'three')?.presence).toBe('only-b')
    expect(diff.candidates.find(x => x.a?.id === 'one')?.channels_a).toEqual(['lexical'])
    expect(diff.costs.a?.duration_ms).toBe(18)
    expect(diff.costs.a?.time_to_first_candidate_ms).toBe(5)
    expect(exportComparison(diff)).not.toContain('indexed body')
  })
  it('marks unchanged source refs with different checksums as different evidence', () => {
    const a = run('a', [candidate('old', 1, 'same')])
    const b = run('b', [candidate('new', 1, 'same')])
    const diff = compareRuns(a, b)
    expect(diff.counts.shared).toBe(0)
    expect(diff.counts.only_a).toBe(1)
    expect(diff.counts.only_b).toBe(1)
  })
  it('rejects query drift even when JSON objects have different key order', () => {
    const a = run('a', [candidate('one', 1)])
    const b = run('b', [candidate('one', 1)], { request: {
      intent: { anchors: [], canonical_query: 'eligibility_validator', schema_version: 2 },
    } })
    expect(compareRuns(a, b).assessment.query_equal).toBe(true)
    const altered = run('new', [candidate('one', 1)], { request: {
      intent: { schema_version: 2, canonical_query: 'other' },
    } })
    const diff = compareRuns(a, altered)
    expect(diff.assessment.status).toBe('incompatible')
    expect(diff.assessment.reasons.join(' ')).toContain('SearchIntent')
    expect(diff.candidates[0].rank_delta).toBeNull()
  })
  it('does not certify unbound revision, different K/scope, missing fingerprints, or backend drift', () => {
    const a = run('a', [candidate('one', 1)])
    const noRevision = run('unbound', [candidate('one', 1)], { target: { revision: null } })
    expect(compareRuns(a, noRevision).assessment.same_corpus).toBe(false)
    expect(compareRuns(a, noRevision).assessment.status).toBe('incompatible')
    const k = run('k', [candidate('one', 1)], { request: { limit: 10 } })
    expect(compareRuns(a, k).assessment.reasons.join(' ')).toContain('Top-K')
    const mode = run('scope', [candidate('one', 1)], { request: { mode: 'targeted' } })
    expect(compareRuns(a, mode).assessment.reasons.join(' ')).toContain('scope')
    const noFingerprint = run('unknown', [candidate('one', 1)], { fingerprints: {} })
    expect(compareRuns(a, noFingerprint).assessment.status).toBe('unverified')
    expect(compareRuns(a, noFingerprint).candidates[0].rank_delta).toBeNull()
    const backend = run('os', [candidate('one', 1)], { target: { backend: 'opensearch', collection: 'os' } })
    expect(compareRuns(a, backend).assessment.status).toBe('incompatible')
  })
  it('reports vector model/serializer divergence and telemetry absence without fabricated zeroes', () => {
    const a = run('a', [candidate('one', 1)], { fingerprints: {
      projection: 'p1', indexing: 'i1', embedding_model: 'e1',
    } })
    const b = run('b', [candidate('one', 1)], { fingerprints: {
      projection: 'p1', indexing: 'i1', embedding_model: 'e2',
    } })
    expect(compareRuns(a, b).assessment.reasons.join(' ')).toContain('embedding_model')
    const missing = captureRun('no-telemetry', request, target, {
      backend: 'solr', status: 'ok', duration_ms: 5, metadata: {}, results: [candidate('one', 1)],
    }, null)
    const diff = compareRuns(a, missing)
    expect(diff.costs.b).toBeNull()
    expect(diff.assessment.observations.join(' ')).toContain('Telemetry ausente')
  })
  it('never treats failed run as zero retrieved evidence', () => {
    const good = run('a', [candidate('one', 1)])
    const failed = run('error', [], { error: 'embedding_failed' })
    const diff = compareRuns(good, failed)
    expect(diff.assessment.status).toBe('incompatible')
    expect(diff.assessment.reasons.join(' ')).toContain('falharam')
    expect(failed.outcome.category).toBe('embedding_failed')
    expect(diff.costs.b).toBeNull()
  })
})
